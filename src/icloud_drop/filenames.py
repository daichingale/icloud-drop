"""Windowsでも使える原本ファイル名。"""

from __future__ import annotations

import re
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def live_photo_mov_name(filename: str) -> str:
    """Live Photoの動画側。拡張子だけを .MOV に替える（原本の名前）。"""
    stem = Path(filename).stem or "LIVE"
    return f"{stem}.MOV"


def sanitize_filename(filename: str, *, fallback: str = "photo") -> str:
    """エクスプローラーに渡せない文字を除く。中身の意味は変えない。"""
    name = (filename or "").strip().replace("\u3000", " ")
    name = _ILLEGAL.sub("_", name)
    name = name.rstrip(" .")
    if not name or name in {".", ".."}:
        name = fallback
    stem = Path(name).stem
    if stem.upper() in _RESERVED:
        name = f"_{name}"
    # パスが長くなりすぎないよう、ファイル名は240文字まで。
    if len(name) > 240:
        suffix = Path(name).suffix
        keep = 240 - len(suffix)
        name = name[: max(keep, 1)] + suffix
        name = name.rstrip(" .")
    return name or fallback


def unique_filename(filename: str, used: set[str]) -> str:
    """同じフォルダに同名があるとき、末尾に (2), (3)… を付ける。"""
    candidate = filename
    if candidate not in used:
        return candidate
    path = Path(filename)
    stem, suffix = path.stem, path.suffix
    number = 2
    while True:
        candidate = f"{stem} ({number}){suffix}"
        if candidate not in used:
            return candidate
        number += 1


def safe_id(asset_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._=-]", "_", asset_id).strip("._")
    return cleaned[:80] or "asset"
