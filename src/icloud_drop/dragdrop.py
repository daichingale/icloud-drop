"""エクスプローラーへ渡すドラッグデータのバイト列。

Windowsでは FileGroupDescriptorW と FileContents を使う。
ここにある組み立て処理はOSに依存しないので、テストできる。
"""

from __future__ import annotations

import struct

FD_FILESIZE = 0x00000040
FD_ATTRIBUTES = 0x00000004
FD_PROGRESSUI = 0x00004000
FILE_ATTRIBUTE_NORMAL = 0x00000080
DESCRIPTOR_BYTES = 592
NAME_OFFSET = 72
NAME_CHARS = 259


def clip_filename(name: str, limit: int = NAME_CHARS) -> str:
    """FILEDESCRIPTOR のファイル名は 259 文字まで。拡張子は残す。"""

    def units(text: str) -> int:
        return len(text.encode("utf-16-le")) // 2

    if units(name) <= limit:
        return name
    dot = name.rfind(".")
    suffix = name[dot:] if dot > 0 else ""
    stem = name[:dot] if dot > 0 else name
    while suffix and units(suffix) > limit - 1:
        suffix = suffix[1:]
    room = max(1, limit - units(suffix))
    while stem and units(stem) > room:
        stem = stem[:-1]
    return stem + suffix

CF_HDROP = 15


def pack_file_group_descriptor(entries: list[tuple[str, int]]) -> bytes:
    """(ファイル名, バイト数) の一覧を FILEGROUPDESCRIPTORW にする。"""
    chunks = [struct.pack("<I", len(entries))]
    flags = FD_FILESIZE | FD_ATTRIBUTES | FD_PROGRESSUI
    for name, size in entries:
        blob = bytearray(DESCRIPTOR_BYTES)
        struct.pack_into("<I", blob, 0, flags)
        struct.pack_into("<I", blob, 36, FILE_ATTRIBUTE_NORMAL)
        high = (size >> 32) & 0xFFFFFFFF
        low = size & 0xFFFFFFFF
        struct.pack_into("<II", blob, 64, high, low)
        encoded = clip_filename(name).encode("utf-16-le")[: NAME_CHARS * 2]
        blob[NAME_OFFSET : NAME_OFFSET + len(encoded)] = encoded
        chunks.append(bytes(blob))
    return b"".join(chunks)


def parse_file_group_descriptor(payload: bytes) -> list[tuple[str, int]]:
    if len(payload) < 4:
        raise ValueError("記述子が短すぎます。")
    (count,) = struct.unpack_from("<I", payload, 0)
    expected = 4 + count * DESCRIPTOR_BYTES
    if len(payload) != expected:
        raise ValueError(f"記述子の長さが合いません: {len(payload)} != {expected}")
    entries: list[tuple[str, int]] = []
    for index in range(count):
        start = 4 + index * DESCRIPTOR_BYTES
        high, low = struct.unpack_from("<II", payload, start + 64)
        raw = payload[start + NAME_OFFSET : start + DESCRIPTOR_BYTES]
        name = raw.decode("utf-16-le").split("\x00", 1)[0]
        size = (high << 32) | low
        entries.append((name, size))
    return entries


def pack_hdrop(paths: list[str]) -> bytes:
    """CF_HDROP。先頭20バイトの DROPFILES と、UTF-16のパス一覧。"""
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    body = bytearray()
    for path in paths:
        body += path.encode("utf-16-le") + b"\x00\x00"
    body += b"\x00\x00"
    return header + bytes(body)


def parse_hdrop(payload: bytes) -> list[str]:
    if len(payload) < 20:
        raise ValueError("HDROPが短すぎます。")
    offset, _x, _y, _nc, wide = struct.unpack_from("<IiiII", payload, 0)
    if not wide:
        raise ValueError("ワイド文字のHDROPだけを扱います。")
    raw = payload[offset:]
    text = raw.decode("utf-16-le")
    parts = [part for part in text.split("\x00") if part]
    return parts
