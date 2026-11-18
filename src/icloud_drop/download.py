"""原本の保存。途中から再開し、同じファイルは取り直さない。"""

from __future__ import annotations

import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from icloud_drop.errors import CancelledError, TransientError
from icloud_drop.filenames import safe_id, sanitize_filename, unique_filename
from icloud_drop.heic_convert import HeicConverter, convert_heic_to_jpeg
from icloud_drop.models import Asset, DragFile
from icloud_drop.paths import win_long_path
from icloud_drop.streams import BinaryStream

MANIFEST_NAME = "icloud-drop-manifest.json"
CHUNK = 256 * 1024


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError()


@dataclass
class ProgressEvent:
    index: int
    total: int
    filename: str
    state: str
    bytes_done: int = 0
    bytes_total: int = 0
    message: str = ""


@dataclass
class SaveSummary:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class _FileJob:
    asset: Asset
    which: str
    filename: str
    expected: int
    convert: bool


Opener = Callable[[str, str, int], BinaryStream]


def cache_file(cache_root: Path, asset_id: str, filename: str) -> Path:
    directory = cache_root / safe_id(asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / sanitize_filename(filename, fallback=safe_id(asset_id))


def build_drag_plan(assets: list[Asset], *, include_live: bool = True) -> list[DragFile]:
    plan: list[DragFile] = []
    for asset in assets:
        plan.append(
            DragFile(
                asset_id=asset.id,
                which="original",
                filename=sanitize_filename(asset.filename, fallback=asset.id),
                size=max(asset.size, 0),
            )
        )
        if include_live and asset.is_live and asset.live_filename:
            plan.append(
                DragFile(
                    asset_id=asset.id,
                    which="live",
                    filename=sanitize_filename(asset.live_filename, fallback=f"{asset.id}.MOV"),
                    size=max(asset.live_size, 0),
                )
            )
    return plan


def _load_manifest(directory: Path) -> dict:
    path = directory / MANIFEST_NAME
    if not path.exists():
        return {"version": 1, "items": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "items": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), dict):
        return {"version": 1, "items": {}}
    return raw


def _save_manifest(directory: Path, manifest: dict) -> None:
    path = directory / MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _record(manifest: dict, asset_id: str, which: str, filename: str, size: int) -> None:
    item = manifest["items"].setdefault(asset_id, {})
    key = "original" if which == "original" else "live"
    item[key] = {"filename": filename, "size": size}


def _known_names(manifest: dict) -> set[str]:
    names: set[str] = set()
    for item in manifest["items"].values():
        if not isinstance(item, dict):
            continue
        for part in ("original", "live"):
            info = item.get(part)
            if isinstance(info, dict) and info.get("filename"):
                names.add(str(info["filename"]))
    return names


def _owned_filename(manifest: dict, asset_id: str, which: str) -> str | None:
    item = manifest["items"].get(asset_id) or {}
    key = "original" if which == "original" else "live"
    info = item.get(key)
    if isinstance(info, dict) and info.get("filename"):
        return str(info["filename"])
    return None


def _stream_to_file(
    opener: Opener,
    *,
    asset_id: str,
    which: str,
    dest: Path,
    expected: int,
    cancel: CancelToken | None,
    on_bytes: Callable[[int, int], None] | None,
    attempts: int = 4,
) -> str:
    """dest へ書き、downloaded または resumed を返す。"""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    delay_notes: list[str] = []

    def attempt_once() -> str:
        start = 0
        if part.exists():
            start = part.stat().st_size
            if expected and start > expected:
                part.unlink()
                start = 0
            elif expected and start == expected:
                _replace(part, dest)
                return "resumed"
        stream = opener(asset_id, which, start)
        try:
            if start and getattr(stream, "status", 200) == 200:
                # サーバが範囲指定を無視したので、最初から書き直す。
                start = 0
                if part.exists():
                    part.unlink()
            mode = "ab" if start else "wb"
            written = start
            with open(win_long_path(part), mode) as handle:
                while True:
                    if cancel:
                        cancel.raise_if_cancelled()
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    if on_bytes:
                        on_bytes(written, expected)
        finally:
            stream.close()
        _replace(part, dest)
        return "resumed" if start else "downloaded"

    last_error: Exception | None = None
    for try_index in range(attempts):
        if cancel:
            cancel.raise_if_cancelled()
        try:
            return attempt_once()
        except CancelledError:
            raise
        except TransientError as exc:
            last_error = exc
            delay_notes.append(str(exc))
            if try_index == attempts - 1:
                break
            _sleep_backoff(try_index, cancel)
        except OSError as exc:
            last_error = exc
            if try_index == attempts - 1:
                break
            _sleep_backoff(try_index, cancel)
    raise TransientError(delay_notes[-1] if delay_notes else str(last_error or "ダウンロードに失敗しました"))


def _sleep_backoff(try_index: int, cancel: CancelToken | None) -> None:
    import time

    wait = 0.2 * (2**try_index)
    if cancel:
        if cancel._event.wait(wait):
            raise CancelledError()
    else:
        time.sleep(wait)


def _replace(part: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    part.replace(dest)


def _same_size(path: Path, expected: int) -> bool:
    if not path.exists() or not path.is_file():
        return False
    size = path.stat().st_size
    if expected > 0:
        return size == expected
    return size > 0


def ensure_cached(
    asset: Asset,
    which: str,
    *,
    cache_root: Path,
    opener: Opener,
    cancel: CancelToken | None = None,
) -> Path:
    filename = asset.filename if which == "original" else (asset.live_filename or "")
    expected = asset.size if which == "original" else asset.live_size
    dest = cache_file(cache_root, asset.id, filename or asset.id)
    if _same_size(dest, expected):
        return dest
    _stream_to_file(
        opener,
        asset_id=asset.id,
        which=which,
        dest=dest,
        expected=expected,
        cancel=cancel,
        on_bytes=None,
    )
    return dest


def save_assets(
    assets: list[Asset],
    *,
    dest_dir: Path,
    cache_root: Path,
    opener: Opener,
    include_live: bool = True,
    convert_heic: bool = False,
    skip_existing: bool = True,
    converter: HeicConverter | None = None,
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> SaveSummary:
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(dest_dir)
    jobs = _jobs(assets, include_live=include_live, convert_heic=convert_heic, manifest=manifest, dest_dir=dest_dir)
    summary = SaveSummary()
    total = len(jobs)

    for index, job in enumerate(jobs, start=1):
        if cancel:
            try:
                cancel.raise_if_cancelled()
            except CancelledError:
                summary.cancelled = True
                break
        _emit(on_progress, index, total, job.filename, "start", 0, job.expected)

        try:
            outcome = _save_one(
                job,
                dest_dir=dest_dir,
                cache_root=cache_root,
                opener=opener,
                manifest=manifest,
                skip_existing=skip_existing,
                converter=converter,
                cancel=cancel,
                on_bytes=lambda done, total_bytes, job=job, index=index: _emit(
                    on_progress, index, total, job.filename, "download", done, total_bytes or job.expected
                ),
            )
        except CancelledError:
            summary.cancelled = True
            break
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない
            summary.failed += 1
            if len(summary.errors) < 20:
                summary.errors.append(f"{job.filename}: {exc}")
            _emit(on_progress, index, total, job.filename, "failed", 0, job.expected, str(exc))
            continue

        if outcome == "skipped":
            summary.skipped += 1
            _emit(on_progress, index, total, job.filename, "skipped", job.expected, job.expected)
        else:
            summary.downloaded += 1
            _emit(on_progress, index, total, job.filename, "done", job.expected, job.expected)
        _save_manifest(dest_dir, manifest)

    return summary


def _jobs(
    assets: list[Asset],
    *,
    include_live: bool,
    convert_heic: bool,
    manifest: dict,
    dest_dir: Path,
) -> list[_FileJob]:
    assigned = _known_names(manifest)
    jobs: list[_FileJob] = []
    for asset in assets:
        convert = bool(convert_heic and asset.is_heic)
        filename = _choose_name(
            manifest,
            dest_dir,
            asset_id=asset.id,
            which="original",
            preferred=_output_name(asset, convert=convert),
            expected=0 if convert else asset.size,
            convert=convert,
            assigned=assigned,
        )
        assigned.add(filename)
        jobs.append(
            _FileJob(
                asset=asset,
                which="original",
                filename=filename,
                expected=0 if convert else asset.size,
                convert=convert,
            )
        )
        if include_live and asset.is_live and asset.live_filename:
            live_name = _choose_name(
                manifest,
                dest_dir,
                asset_id=asset.id,
                which="live",
                preferred=sanitize_filename(asset.live_filename, fallback=f"{asset.id}.MOV"),
                expected=asset.live_size,
                convert=False,
                assigned=assigned,
            )
            assigned.add(live_name)
            jobs.append(
                _FileJob(
                    asset=asset,
                    which="live",
                    filename=live_name,
                    expected=asset.live_size,
                    convert=False,
                )
            )
    return jobs


def _output_name(asset: Asset, *, convert: bool) -> str:
    preferred = sanitize_filename(asset.filename, fallback=asset.id)
    if convert:
        return str(Path(preferred).with_suffix(".jpg"))
    return preferred


def _choose_name(
    manifest: dict,
    dest_dir: Path,
    *,
    asset_id: str,
    which: str,
    preferred: str,
    expected: int,
    convert: bool,
    assigned: set[str],
) -> str:
    owned = _owned_filename(manifest, asset_id, which)
    if owned:
        return owned
    path = dest_dir / preferred
    taken = preferred in assigned
    if path.exists() and not taken:
        if convert:
            # 変換後のサイズは事前に分からない。記録が無いJPEGは上書きしない。
            taken = True
        elif not _same_size(path, expected):
            taken = True
    if taken:
        return unique_filename(preferred, assigned | _directory_names(dest_dir))
    return preferred


def _directory_names(dest_dir: Path) -> set[str]:
    if not dest_dir.exists():
        return set()
    return {path.name for path in dest_dir.iterdir() if path.is_file()}


def _save_one(
    job: _FileJob,
    *,
    dest_dir: Path,
    cache_root: Path,
    opener: Opener,
    manifest: dict,
    skip_existing: bool,
    converter: HeicConverter | None,
    cancel: CancelToken | None,
    on_bytes: Callable[[int, int], None] | None,
) -> str:
    dest = dest_dir / job.filename
    if skip_existing and not job.convert and _same_size(dest, job.expected):
        _record(manifest, job.asset.id, job.which, job.filename, dest.stat().st_size)
        return "skipped"
    if skip_existing and job.convert and dest.exists() and dest.stat().st_size > 0:
        recorded = _owned_filename(manifest, job.asset.id, job.which)
        if recorded == job.filename:
            return "skipped"

    if job.convert:
        raw_name = sanitize_filename(job.asset.filename, fallback=job.asset.id)
        raw_path = ensure_cached(job.asset, "original", cache_root=cache_root, opener=opener, cancel=cancel)
        # ensure_cached は原本名で保存する。変換先とは別。
        _ = raw_name
        convert_heic_to_jpeg(raw_path, dest, converter=converter)
        _record(manifest, job.asset.id, job.which, job.filename, dest.stat().st_size)
        return "downloaded"

    cached = cache_file(cache_root, job.asset.id, job.asset.filename if job.which == "original" else (job.asset.live_filename or job.filename))
    if _same_size(cached, job.expected):
        if cancel:
            cancel.raise_if_cancelled()
        shutil.copyfile(cached, dest)
        _record(manifest, job.asset.id, job.which, job.filename, dest.stat().st_size)
        return "downloaded"

    outcome = _stream_to_file(
        opener,
        asset_id=job.asset.id,
        which=job.which,
        dest=dest,
        expected=job.expected,
        cancel=cancel,
        on_bytes=on_bytes,
    )
    _record(manifest, job.asset.id, job.which, job.filename, dest.stat().st_size)
    # 次のドラッグが速くなるよう、同じボリュームならキャッシュへ複製する。失敗しても保存は成功。
    try:
        if not _same_size(cached, dest.stat().st_size):
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, cached)
    except OSError:
        pass
    return outcome


def _emit(
    callback: Callable[[ProgressEvent], None] | None,
    index: int,
    total: int,
    filename: str,
    state: str,
    done: int,
    total_bytes: int,
    message: str = "",
) -> None:
    if callback is None:
        return
    callback(
        ProgressEvent(
            index=index,
            total=total,
            filename=filename,
            state=state,
            bytes_done=done,
            bytes_total=total_bytes,
            message=message,
        )
    )
