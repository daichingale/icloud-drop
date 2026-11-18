"""HEICをJPEGにする。ライブラリが無いときは、中身がJPEGのものだけ変換する。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from icloud_drop.errors import ICloudError


class HeicConverter(Protocol):
    def __call__(self, source: Path, dest: Path) -> None: ...


class HeicConvertError(ICloudError):
    pass


def convert_heic_to_jpeg(source: Path, dest: Path, *, converter: HeicConverter | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if converter is not None:
        converter(source, dest)
        return
    data = source.read_bytes()
    if data[:2] == b"\xff\xd8":
        # デモデータや、すでにJPEGとして届いたファイル。
        dest.write_bytes(data)
        return
    try:
        from pillow_heif import open_heif  # type: ignore
        from PIL import Image
    except Exception as exc:
        raise HeicConvertError(
            "HEICをJPEGに変換できません。Windows用のパッケージには変換機能を同梱しています。"
            "自分で動かしている場合は pillow-heif を入れてください。"
        ) from exc
    try:
        handle = open_heif(source)
        image = Image.frombytes(handle.mode, handle.size, handle.data, "raw")
        image.save(dest, "JPEG", quality=92)
    except Exception as exc:  # noqa: BLE001
        raise HeicConvertError(f"HEICをJPEGに変換できませんでした。{exc}") from exc
