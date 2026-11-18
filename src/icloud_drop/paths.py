"""設定やクッキーの置き場所。レジストリは使わない。

ポータブル版（exe）は、exeの隣の data フォルダに置く。
そこが書けないときだけ、ユーザーのローカルフォルダへ退避する。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _probe(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_ok"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def portable_data_dir(root: Path) -> Path | None:
    candidate = root / "data"
    if _probe(candidate):
        return candidate
    return None


def resolve_data_dir(
    *,
    frozen: bool,
    executable: Path,
    cwd: Path,
    local_appdata: str | None,
    home: Path,
    env_override: str | None = None,
    force_portable_marker: bool | None = None,
) -> tuple[Path, str]:
    """データフォルダと、選んだ理由（portable / localappdata / home）を返す。"""
    if env_override:
        path = Path(env_override)
        path.mkdir(parents=True, exist_ok=True)
        return path, "env"

    if frozen:
        found = portable_data_dir(executable.parent)
        if found is not None:
            return found, "portable"

    marker = force_portable_marker
    if marker is None:
        marker = (cwd / "portable.txt").exists()
    if marker:
        found = portable_data_dir(cwd)
        if found is not None:
            return found, "portable"

    if local_appdata:
        path = Path(local_appdata) / "IcloudDrop"
        if _probe(path):
            return path, "localappdata"

    path = home / "IcloudDrop"
    path.mkdir(parents=True, exist_ok=True)
    return path, "home"


def default_data_dir() -> Path:
    frozen = bool(getattr(sys, "frozen", False))
    executable = Path(sys.executable).resolve() if frozen else Path(sys.argv[0]).resolve()
    path, _reason = resolve_data_dir(
        frozen=frozen,
        executable=executable,
        cwd=Path.cwd(),
        local_appdata=os.environ.get("LOCALAPPDATA"),
        home=Path.home(),
        env_override=os.environ.get("ICLOUD_DROP_DATA"),
    )
    return path


def win_long_path(path: Path) -> str:
    """長いパスでもコピーできるよう、Windowsでは \\\\?\\ を付ける。"""
    text = str(path)
    if os.name != "nt":
        return text
    resolved = str(path.resolve()) if path.is_absolute() else text
    if resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved
