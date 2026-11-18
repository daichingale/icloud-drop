"""画面と保存処理が共有するデータ。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Album:
    id: str
    title: str


@dataclass(frozen=True)
class Asset:
    """ライブラリの1枚。原本のファイル名とサイズを持つ。"""

    id: str
    filename: str
    kind: str  # "photo" または "video"
    created: datetime
    size: int
    width: int = 0
    height: int = 0
    is_live: bool = False
    live_filename: str | None = None
    live_size: int = 0
    album_ids: tuple[str, ...] = ()

    @property
    def is_heic(self) -> bool:
        lower = self.filename.lower()
        return lower.endswith(".heic") or lower.endswith(".heif")


@dataclass(frozen=True)
class TrustedPhone:
    id: str
    label: str


@dataclass
class AuthResult:
    """サインインの途中結果。status は ok / need_2fa / need_2sa / need_device_approval / failed。"""

    status: str
    message: str = ""
    hint: str = ""
    phones: tuple[TrustedPhone, ...] = ()
    two_sa_devices: tuple[tuple[str, str], ...] = ()
    security_key: bool = False


@dataclass
class Group:
    title: str
    assets: list[Asset] = field(default_factory=list)


@dataclass(frozen=True)
class DragFile:
    asset_id: str
    which: str  # "original" または "live"
    filename: str
    size: int
