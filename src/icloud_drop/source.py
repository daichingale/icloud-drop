"""写真の出どころ。本物のiCloudと、練習用のモックが同じ形。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator

from icloud_drop.models import Album, Asset, AuthResult
from icloud_drop.streams import BinaryStream

StatusCallback = Callable[[str], None]


class PhotoSource(ABC):
    """サインインと、写真の一覧・原本の読み出し。"""

    practice: bool = False

    @abstractmethod
    def login(
        self,
        apple_id: str,
        password: str | None,
        *,
        adp_web_access: bool,
        domain: str,
        on_status: StatusCallback | None = None,
    ) -> AuthResult: ...

    @abstractmethod
    def submit_code(self, code: str, *, on_status: StatusCallback | None = None) -> AuthResult: ...

    @abstractmethod
    def send_sms(self, device_id: str) -> AuthResult: ...

    @abstractmethod
    def submit_sms_code(self, device_id: str, code: str, *, on_status: StatusCallback | None = None) -> AuthResult: ...

    @abstractmethod
    def send_2sa(self, device_index: int) -> AuthResult: ...

    @abstractmethod
    def submit_2sa_code(self, device_index: int, code: str, *, on_status: StatusCallback | None = None) -> AuthResult: ...

    @abstractmethod
    def approve_on_device(self, *, on_status: StatusCallback | None = None) -> AuthResult: ...

    @abstractmethod
    def approve_security_key(self, *, on_status: StatusCallback | None = None) -> AuthResult: ...

    @abstractmethod
    def list_albums(self) -> list[Album]: ...

    @abstractmethod
    def iter_batches(self, album_id: str, batch_size: int = 60) -> Iterator[list[Asset]]: ...

    @abstractmethod
    def fetch_thumbnail(self, asset_id: str) -> bytes: ...

    @abstractmethod
    def open_original(self, asset_id: str, which: str = "original", start: int = 0) -> BinaryStream: ...

    @abstractmethod
    def forget(self) -> None: ...

    def close(self) -> None:
        return None
