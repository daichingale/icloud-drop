"""ネットワークに出ない練習用のiCloud。

パスワードは保存しない。確認コードは 123456。
パスワードに wrong と入れると失敗する。
Apple ID に adp が含まれると、高度なデータ保護の流れになる。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from icloud_drop.errors import AccessDeniedError, ICloudError
from icloud_drop.filenames import live_photo_mov_name
from icloud_drop.models import Album, Asset, AuthResult, TrustedPhone
from icloud_drop.source import PhotoSource, StatusCallback
from icloud_drop.streams import ByteStream

DEMO_CODE = "123456"
WRONG_PASSWORD = "wrong"


@dataclass
class DemoLibrary:
    albums: list[Album]
    by_album: dict[str, list[Asset]]
    blobs: dict[tuple[str, str], bytes]
    by_id: dict[str, Asset] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.by_id:
            found: dict[str, Asset] = {}
            for assets in self.by_album.values():
                for asset in assets:
                    found[asset.id] = asset
            self.by_id = found


def _jpeg(color: tuple[int, int, int], width: int, height: int, label: str) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(255, 255, 255))
    draw.text((4, 4), label, fill=(255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def _png(color: tuple[int, int, int], width: int, height: int, label: str) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(image)
    draw.text((4, 4), label, fill=(255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _mov(size: int) -> bytes:
    header = b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  "
    filler = bytes((index * 17) % 256 for index in range(max(size, 32)))
    return header + filler


def build_demo_library(count: int = 180, seed: int = 42) -> DemoLibrary:
    """何枚あるかを固定した、見た目の違うサンプル。"""
    del seed  # 色と日付は番号だけで決まる。引数は呼び出し側の印。
    base = datetime(2023, 6, 1, 3, 0, tzinfo=timezone.utc)
    albums = [
        Album("all", "すべての写真"),
        Album("family", "家族"),
        Album("travel", "旅行"),
        Album("shots", "スクリーンショット"),
    ]
    by_album: dict[str, list[Asset]] = {album.id: [] for album in albums}
    blobs: dict[tuple[str, str], bytes] = {}
    colors = [
        (186, 114, 72),
        (72, 122, 138),
        (86, 132, 96),
        (168, 92, 92),
        (116, 104, 158),
        (196, 164, 84),
        (92, 108, 84),
        (150, 118, 96),
    ]
    for index in range(count):
        slot = index % 10
        is_live = False
        if slot in (8, 9):
            kind, ext = "video", "MOV"
        elif slot == 7:
            kind, ext = "photo", "PNG"
        elif slot in (5, 6):
            kind, ext = "photo", "HEIC"
            is_live = slot == 6
        else:
            kind, ext = "photo", "JPG"
        asset_id = f"asset-{index + 1:04d}"
        filename = f"IMG_{index + 1:04d}.{ext}"
        # 同じ日に数枚ずつ並べる。日付の見出しの下に横並びで見える。
        created = base + timedelta(days=index // 6, hours=9, minutes=(index % 6) * 20)
        color = colors[index % len(colors)]
        if ext == "PNG":
            original = _png(color, 64, 48, str(index + 1))
        elif ext == "MOV":
            original = _mov(180 + index)
        else:
            original = _jpeg(color, 64, 48, str(index + 1))
        live_name = None
        live_size = 0
        if is_live:
            live_name = live_photo_mov_name(filename)
            live = _mov(90 + index)
            live_size = len(live)
            blobs[(asset_id, "live")] = live
        blobs[(asset_id, "original")] = original
        blobs[(asset_id, "thumb")] = _jpeg(color, 32, 24, str(index + 1))
        album_ids = ["all"]
        if index % 5 == 0:
            album_ids.append("family")
        if index % 5 == 1:
            album_ids.append("travel")
        if ext == "PNG":
            album_ids.append("shots")
        asset = Asset(
            id=asset_id,
            filename=filename,
            kind=kind,
            created=created,
            size=len(original),
            width=64,
            height=48,
            is_live=is_live,
            live_filename=live_name,
            live_size=live_size,
            album_ids=tuple(album_ids),
        )
        for album_id in album_ids:
            by_album[album_id].append(asset)
    return DemoLibrary(albums=albums, by_album=by_album, blobs=blobs)


class MockSource(PhotoSource):
    practice = True

    def __init__(self, session_dir: Path, *, demo: bool = False, library: DemoLibrary | None = None) -> None:
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.library = library or build_demo_library()
        self.demo = demo
        self.apple_id = "demo@icloud.com" if demo else ""
        self.phase = "ready" if demo else "signed_out"
        self.adp = False
        self.consent = False
        self._phones = (TrustedPhone("sms-1", "SMS ••• ••12"),)
        self._2sa = (("0", "iPhone"), ("1", "SMS ••• ••12"))
        self._2sa_sent: int | None = None
        self._lock = threading.Lock()

    def _path(self) -> Path:
        return self.session_dir / "mock-accounts.json"

    def _read(self) -> dict:
        path = self._path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, data: dict) -> None:
        self._path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record(self) -> dict:
        return self._read().get(self.apple_id) or {}

    def _trust(self) -> None:
        data = self._read()
        data[self.apple_id] = {"trusted": True, "pcs_consent": self.consent}
        self._write(data)
        self.phase = "ready"

    def login(
        self,
        apple_id: str,
        password: str | None,
        *,
        adp_web_access: bool,
        domain: str,
        on_status: StatusCallback | None = None,
    ) -> AuthResult:
        del domain
        apple_id = apple_id.strip()
        if not apple_id:
            return AuthResult("failed", "Apple IDを入力してください。")
        if password == WRONG_PASSWORD:
            return AuthResult("failed", "Apple IDまたはパスワードが違います。")
        self.apple_id = apple_id
        self.adp = adp_web_access
        record = self._record()
        self.consent = bool(record.get("pcs_consent"))
        if record.get("trusted") and password != WRONG_PASSWORD:
            if on_status:
                on_status("保存してあるサインインを使います。")
            return self._after_factors(on_status)
        if password is None or password == "":
            return AuthResult("failed", "パスワードを入力してください。")
        if "2sa" in apple_id.lower():
            self.phase = "need_2sa"
            self._2sa_sent = None
            return AuthResult(
                "need_2sa",
                "確認コードの送り先を選んでください。",
                two_sa_devices=self._2sa,
            )
        self.phase = "need_2fa"
        security_key = "fido" in apple_id.lower()
        return AuthResult(
            "need_2fa",
            "信頼できるデバイスに届いた6桁のコードを入力してください。"
            if not security_key
            else "セキュリティキーを挿して承認するか、練習用コード 123456 を入力してください。",
            phones=() if security_key else self._phones,
            security_key=security_key,
        )

    def submit_code(self, code: str, *, on_status: StatusCallback | None = None) -> AuthResult:
        if self.phase != "need_2fa":
            return AuthResult("failed", "いま確認コードを受け付ける状態ではありません。")
        if code.strip() != DEMO_CODE:
            return AuthResult("failed", "コードが違います。もう一度入力してください。")
        return self._after_factors(on_status)

    def send_sms(self, device_id: str) -> AuthResult:
        if self.phase != "need_2fa":
            return AuthResult("failed", "いまSMSを送る段階ではありません。")
        if device_id != self._phones[0].id:
            return AuthResult("failed", "その電話番号には送れません。")
        return AuthResult("need_2fa", "SMSでコードを送りました。", phones=self._phones)

    def submit_sms_code(self, device_id: str, code: str, *, on_status: StatusCallback | None = None) -> AuthResult:
        del device_id
        return self.submit_code(code, on_status=on_status)

    def send_2sa(self, device_index: int) -> AuthResult:
        if self.phase != "need_2sa":
            return AuthResult("failed", "いまコードを送る段階ではありません。")
        if device_index < 0 or device_index >= len(self._2sa):
            return AuthResult("failed", "送り先を選び直してください。")
        self._2sa_sent = device_index
        return AuthResult("need_2sa", "コードを送りました。6桁を入力してください。", two_sa_devices=self._2sa)

    def submit_2sa_code(self, device_index: int, code: str, *, on_status: StatusCallback | None = None) -> AuthResult:
        if self.phase != "need_2sa" or self._2sa_sent != device_index:
            return AuthResult("failed", "先に送り先を選んで、コードを送ってください。")
        if code.strip() != DEMO_CODE:
            return AuthResult("failed", "コードが違います。もう一度入力してください。")
        return self._after_factors(on_status)

    def approve_on_device(self, *, on_status: StatusCallback | None = None) -> AuthResult:
        if self.phase == "need_adp":
            if on_status:
                on_status("iPhoneの許可を確認しています。")
            self.consent = True
            self._trust()
            return AuthResult("ok", "許可されました。")
        if self.phase == "need_2fa":
            if "approve" in self.apple_id.lower():
                if on_status:
                    on_status("デバイスでの承認を確認しました。")
                return self._after_factors(on_status)
            return AuthResult(
                "need_2fa",
                "デバイスに表示された6桁のコードを入力してください。",
                phones=self._phones,
            )
        if self.phase == "ready":
            return AuthResult("ok", "すでにサインインしています。")
        return AuthResult("failed", "いま承認を待っていません。")

    def approve_security_key(self, *, on_status: StatusCallback | None = None) -> AuthResult:
        if self.phase != "need_2fa" or "fido" not in self.apple_id.lower():
            return AuthResult("failed", "セキュリティキーの確認は、いまは必要ありません。")
        if on_status:
            on_status("セキュリティキーを確認しています。")
        return self._after_factors(on_status)

    def _after_factors(self, on_status: StatusCallback | None) -> AuthResult:
        if "adp" in self.apple_id.lower() and self.adp and not self.consent:
            self.phase = "need_adp"
            if on_status:
                on_status("iPhoneで「許可」をタップしてください。")
            return AuthResult(
                "need_device_approval",
                "iPhoneで「許可」をタップしてから、「許可を確認」を押してください。",
                hint="高度なデータ保護では、写真を開く前にiPhoneの許可が必要です。",
            )
        self._trust()
        return AuthResult("ok", "サインインしました。")

    def _ensure(self) -> None:
        if self.phase != "ready":
            raise ICloudError("サインインが終わっていません。")
        if "adp" in self.apple_id.lower() and not self.adp:
            raise AccessDeniedError()

    def list_albums(self) -> list[Album]:
        with self._lock:
            self._ensure()
            return list(self.library.albums)

    def iter_batches(self, album_id: str, batch_size: int = 60) -> Iterator[list[Asset]]:
        with self._lock:
            self._ensure()
            assets = self.library.by_album.get(album_id)
            if assets is None:
                raise ICloudError("そのアルバムは見当たりません。")
            items = list(assets)
        if batch_size <= 0:
            batch_size = 60
        for start in range(0, len(items), batch_size):
            yield items[start : start + batch_size]

    def fetch_thumbnail(self, asset_id: str) -> bytes:
        with self._lock:
            self._ensure()
            try:
                return self.library.blobs[(asset_id, "thumb")]
            except KeyError as exc:
                raise ICloudError("サムネイルがありません。") from exc

    def open_original(self, asset_id: str, which: str = "original", start: int = 0) -> ByteStream:
        with self._lock:
            self._ensure()
            key = (asset_id, "live" if which == "live" else "original")
            try:
                data = self.library.blobs[key]
            except KeyError as exc:
                raise ICloudError("原本が見つかりません。") from exc
        return ByteStream(data, start)

    def forget(self) -> None:
        data = self._read()
        if self.apple_id:
            data.pop(self.apple_id, None)
            self._write(data)
        if not self.demo:
            self.phase = "signed_out"
            self.consent = False
