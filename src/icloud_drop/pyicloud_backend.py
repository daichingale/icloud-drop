"""icloudpd 1.33 と同じ pyicloud_ipd で、iCloud写真に入る。

公式のAPIはない。クッキーは渡されたフォルダにだけ置く。
高度なデータ保護は PyiCloudService.request_pcs_consent（--adp-web-access）と同じ手順。
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from icloud_drop.errors import AccessDeniedError, ICloudError, TransientError, explain_exception
from icloud_drop.filenames import live_photo_mov_name
from icloud_drop.models import Album, Asset, AuthResult, TrustedPhone
from icloud_drop.source import PhotoSource, StatusCallback
from icloud_drop.streams import ResponseStream

LOGGER = logging.getLogger(__name__)

_SKIP_ALBUMS = {"Hidden", "Recently Deleted"}
_ALBUM_TITLES = {
    "Videos": "ビデオ",
    "Favorites": "お気に入り",
    "Screenshots": "スクリーンショット",
    "Live": "Live Photos",
    "Panoramas": "パノラマ",
    "Time-lapse": "タイムラプス",
    "Slo-mo": "スロー",
    "Bursts": "バースト",
}


def cookie_stem(apple_id: str) -> str:
    return "".join(char for char in apple_id if re.match(r"\w", char))


@dataclass
class _Remote:
    photo: object
    original_url: str
    thumb_url: str
    live_url: str | None


class PyiCloudSource(PhotoSource):
    practice = False

    def __init__(self, cookie_dir: Path) -> None:
        self.cookie_dir = cookie_dir
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        self.api = None
        self.apple_id = ""
        self.adp = False
        self._password: str | None = None
        self._lock = threading.RLock()
        self._remote: dict[str, _Remote] = {}
        self._phones: tuple[TrustedPhone, ...] = ()
        self._two_sa: list[dict] = []
        self._challenge: dict | None = None
        self._album_map: dict | None = None

    def login(
        self,
        apple_id: str,
        password: str | None,
        *,
        adp_web_access: bool,
        domain: str,
        on_status: StatusCallback | None = None,
    ) -> AuthResult:
        if domain not in {"com", "cn"}:
            domain = "com"
        apple_id = apple_id.strip()
        if not apple_id:
            return AuthResult("failed", "Apple IDを入力してください。")
        self.apple_id = apple_id
        self.adp = adp_web_access
        self._password = password or None
        self._album_map = None
        self._remote.clear()
        if on_status:
            on_status("iCloudに接続しています…")
        try:
            from pyicloud_ipd import PyiCloudService
        except ImportError as exc:
            raise ICloudError(
                "iCloudに接続する部品が見つかりません。",
                hint="配布されたフォルダの中の IcloudDrop.exe を起動してください。ソースから動かす場合は、icloudpd 1.33 以上を入れます。",
            ) from exc

        def provider() -> str | None:
            return self._password

        try:
            self.api = PyiCloudService(
                domain,
                apple_id,
                provider,
                cookie_directory=str(self.cookie_dir),
            )
        except Exception as exc:
            LOGGER.exception("iCloudのサインインに失敗しました")
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint)
        return self._after_password(on_status)

    def _after_password(self, on_status: StatusCallback | None) -> AuthResult:
        api = self._api()
        if api.requires_2fa:
            try:
                api.trigger_push_notification()
            except Exception:
                LOGGER.info("確認コードの通知を出せませんでした。コードが届いていれば、そのまま入力できます。")
            phones: list[TrustedPhone] = []
            try:
                for phone in api.get_trusted_phone_numbers():
                    phones.append(TrustedPhone(id=str(phone.id), label=f"SMS {phone.obfuscated_number}"))
            except Exception:
                LOGGER.info("SMSの送り先を取得できませんでした。")
            self._phones = tuple(phones)
            if not phones:
                try:
                    self._challenge = api.get_security_key_challenge()
                except Exception:
                    self._challenge = None
                if self._challenge:
                    return AuthResult(
                        "need_2fa",
                        "このアカウントはセキュリティキーで確認します。キーを挿して「セキュリティキーで承認」を押してください。",
                        security_key=True,
                    )
            return AuthResult(
                "need_2fa",
                "信頼できるデバイスに届いた6桁のコードを入力してください。",
                phones=self._phones,
            )
        if api.requires_2sa:
            devices = []
            raw = list(api.trusted_devices)
            self._two_sa = raw
            for index, device in enumerate(raw):
                label = device.get("deviceName") or f"SMS {device.get('phoneNumber', '')}"
                devices.append((str(index), str(label)))
            return AuthResult("need_2sa", "確認コードの送り先を選んでください。", two_sa_devices=tuple(devices))
        return self._finish(on_status)

    def submit_code(self, code: str, *, on_status: StatusCallback | None = None) -> AuthResult:
        api = self._api()
        code = code.strip()
        if len(code) != 6 or not code.isdigit():
            return AuthResult("failed", "6桁の数字を入力してください。")
        try:
            ok = api.validate_2fa_code(code)
        except Exception as exc:
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint)
        if not ok:
            return AuthResult("failed", "コードが違います。もう一度入力してください。")
        return self._finish(on_status)

    def send_sms(self, device_id: str) -> AuthResult:
        api = self._api()
        try:
            ok = api.send_2fa_code_sms(int(device_id))
        except Exception as exc:
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint)
        if not ok:
            return AuthResult("failed", "SMSを送れませんでした。")
        return AuthResult("need_2fa", "SMSでコードを送りました。", phones=self._phones)

    def submit_sms_code(self, device_id: str, code: str, *, on_status: StatusCallback | None = None) -> AuthResult:
        api = self._api()
        code = code.strip()
        if len(code) != 6 or not code.isdigit():
            return AuthResult("failed", "6桁の数字を入力してください。")
        try:
            ok = api.validate_2fa_code_sms(int(device_id), code)
        except Exception as exc:
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint)
        if not ok:
            return AuthResult("failed", "コードが違います。もう一度入力してください。")
        return self._finish(on_status)

    def send_2sa(self, device_index: int) -> AuthResult:
        api = self._api()
        if device_index < 0 or device_index >= len(self._two_sa):
            return AuthResult("failed", "送り先を選び直してください。")
        device = self._two_sa[device_index]
        try:
            ok = api.send_verification_code(device)
        except Exception as exc:
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint)
        if not ok:
            return AuthResult("failed", "確認コードを送れませんでした。")
        return AuthResult("need_2sa", "コードを送りました。届いた数字を入力してください。")

    def submit_2sa_code(self, device_index: int, code: str, *, on_status: StatusCallback | None = None) -> AuthResult:
        api = self._api()
        if device_index < 0 or device_index >= len(self._two_sa):
            return AuthResult("failed", "送り先を選び直してください。")
        try:
            ok = api.validate_verification_code(self._two_sa[device_index], code.strip())
        except Exception as exc:
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint)
        if not ok:
            return AuthResult("failed", "コードが違います。もう一度入力してください。")
        return self._finish(on_status)

    def approve_on_device(self, *, on_status: StatusCallback | None = None) -> AuthResult:
        api = self._api()
        if api.requires_2fa:
            try:
                api.trigger_push_notification()
            except Exception:
                LOGGER.info("承認通知を再送できませんでした。")
            return AuthResult(
                "need_2fa",
                "デバイスに表示された6桁のコードを入力してください。",
                phones=self._phones,
            )
        return self._finish(on_status)

    def approve_security_key(self, *, on_status: StatusCallback | None = None) -> AuthResult:
        api = self._api()
        challenge = self._challenge
        if not challenge:
            try:
                challenge = api.get_security_key_challenge()
            except Exception:
                challenge = None
        if not challenge:
            return AuthResult("failed", "セキュリティキーの確認を開始できませんでした。")
        if on_status:
            on_status("セキュリティキーに触れてください。")
        try:
            ok = api.confirm_security_key(challenge)
        except Exception as exc:
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint)
        if not ok:
            return AuthResult("failed", "セキュリティキーでの確認に失敗しました。キーを挿して、もう一度触れてください。")
        return self._finish(on_status)

    def _finish(self, on_status: StatusCallback | None) -> AuthResult:
        if not self.adp:
            return AuthResult("ok", "サインインしました。")
        if on_status:
            on_status("iPhoneに届いた「許可」をタップしてください。最大2分待ちます。")
        try:
            self._api().request_pcs_consent("photos")
        except Exception as exc:
            LOGGER.exception("Webアクセスの許可に失敗しました")
            message, hint = explain_exception(exc)
            return AuthResult("failed", message, hint or message)
        return AuthResult("ok", "サインインしました。")

    def list_albums(self) -> list[Album]:
        try:
            with self._lock:
                remote = self._albums()
        except Exception as exc:
            self._raise(exc)
        albums = [Album("all", "すべての写真")]
        for name in remote:
            if name in _SKIP_ALBUMS:
                continue
            albums.append(Album(str(name), _ALBUM_TITLES.get(str(name), str(name))))
        return albums

    def iter_batches(self, album_id: str, batch_size: int = 60) -> Iterator[list[Asset]]:
        if batch_size <= 0:
            batch_size = 60
        try:
            with self._lock:
                if album_id == "all":
                    album = self._api().photos.all
                else:
                    album = self._albums()[album_id]
                iterator = iter(album)
        except Exception as exc:
            self._raise(exc)
        batch: list[Asset] = []
        while True:
            try:
                with self._lock:
                    photo = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                self._raise(exc)
            batch.append(self._map(photo))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def fetch_thumbnail(self, asset_id: str) -> bytes:
        remote = self._remote.get(asset_id)
        if remote is None or not remote.thumb_url:
            raise ICloudError("サムネイルがありません。")
        with self._lock:
            response = self._api().session.get(remote.thumb_url, timeout=60)
        try:
            if response.status_code in {421, 450, 500, 503}:
                raise TransientError("iCloudが混み合っています。少し待ってからもう一度開いてください。")
            response.raise_for_status()
            return response.content
        finally:
            response.close()

    def open_original(self, asset_id: str, which: str = "original", start: int = 0) -> ResponseStream:
        remote = self._remote.get(asset_id)
        if remote is None:
            raise ICloudError("この写真の情報が見つかりません。一覧を更新してください。")
        url = remote.live_url if which == "live" else remote.original_url
        if not url:
            raise ICloudError("原本の場所が分かりません。")
        with self._lock:
            response = remote.photo.download(self._api().session, url, start)  # type: ignore[attr-defined]
        status = getattr(response, "status_code", 200)
        if status in {421, 450, 500, 503}:
            response.close()
            raise TransientError("iCloudが混み合っています。少し待って再試行します。")
        if status in {401, 403}:
            response.close()
            raise AccessDeniedError()
        return ResponseStream(response)

    def forget(self) -> None:
        stem = cookie_stem(self.apple_id)
        if stem and self.cookie_dir.exists():
            for path in self.cookie_dir.iterdir():
                if path.name.startswith(stem):
                    try:
                        path.unlink()
                    except OSError:
                        LOGGER.info("セッションファイルを消せませんでした: %s", path.name)
        self.api = None
        self._remote.clear()
        self._album_map = None

    def _api(self):
        if self.api is None:
            raise ICloudError("先にサインインしてください。")
        return self.api

    def _albums(self) -> dict:
        if self._album_map is None:
            self._album_map = self._api().photos.albums
        return self._album_map

    def _map(self, photo: object) -> Asset:
        from pyicloud_ipd.item_type import AssetItemType
        from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize

        versions = photo.versions  # type: ignore[attr-defined]
        original = versions.get(AssetVersionSize.ORIGINAL)
        if original is None:
            raise ICloudError("原本の情報を取得できませんでした。")
        thumb = versions.get(AssetVersionSize.THUMB)
        live = versions.get(LivePhotoVersionSize.ORIGINAL)
        is_video = photo.item_type == AssetItemType.MOVIE  # type: ignore[attr-defined]
        filename = str(photo.filename)  # type: ignore[attr-defined]
        record = getattr(photo, "_asset_record", {}) or {}
        asset_id = str(record.get("recordName") or photo.id)  # type: ignore[attr-defined]
        is_live = (not is_video) and live is not None
        width, height = 0, 0
        try:
            width, height = photo.dimensions  # type: ignore[attr-defined]
        except Exception:
            width, height = 0, 0
        created = photo.asset_date  # type: ignore[attr-defined]
        asset = Asset(
            id=asset_id,
            filename=filename,
            kind="video" if is_video else "photo",
            created=created,
            size=int(original.size or 0),
            width=int(width or 0),
            height=int(height or 0),
            is_live=is_live,
            live_filename=live_photo_mov_name(filename) if is_live else None,
            live_size=int(live.size or 0) if live is not None else 0,
        )
        self._remote[asset_id] = _Remote(
            photo=photo,
            original_url=original.url,
            thumb_url=thumb.url if thumb is not None else "",
            live_url=live.url if live is not None else None,
        )
        return asset

    def _raise(self, exc: Exception) -> None:
        LOGGER.exception("iCloudの写真を読めませんでした")
        message, hint = explain_exception(exc)
        code = str(getattr(exc, "code", "") or "")
        if hint or "access_denied" in f"{code} {exc}".lower():
            raise AccessDeniedError(message, hint=hint or AccessDeniedError().hint) from exc
        raise ICloudError(message, hint=hint) from exc
