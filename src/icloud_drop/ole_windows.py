"""Windowsのエクスプローラーへ、原本ファイルをドラッグする。

ドロップされるまで中身を渡さない（FileGroupDescriptorW と FileContents）。
すでにキャッシュにあるときは CF_HDROP も出し、そのままコピーできるようにする。
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
from collections.abc import Callable
from ctypes import wintypes

from icloud_drop.dragdrop import CF_HDROP, pack_file_group_descriptor, pack_hdrop

LOGGER = logging.getLogger(__name__)

S_OK = 0
E_NOTIMPL = ctypes.c_long(0x80004001).value
E_NOINTERFACE = ctypes.c_long(0x80004002).value
E_FAIL = ctypes.c_long(0x80004005).value
DV_E_FORMATETC = ctypes.c_long(0x80040064).value
DV_E_LINDEX = ctypes.c_long(0x80040068).value
OLE_E_ADVISENOTSUPPORTED = ctypes.c_long(0x80040003).value
DRAGDROP_S_DROP = 0x00040102
DRAGDROP_S_CANCEL = 0x00040101
DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102
TYMED_HGLOBAL = 1
TYMED_ISTREAM = 4
DVASPECT_CONTENT = 1
DATADIR_GET = 1
STGM_READ = 0
STGM_SHARE_DENY_NONE = 0x40
GMEM_MOVEABLE = 0x0002
MK_LBUTTON = 0x0001
DROPEFFECT_COPY = 1

EnsurePath = Callable[[int], str]


def ole_available() -> bool:
    return sys.platform == "win32"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class FORMATETC(ctypes.Structure):
    _fields_ = [
        ("cfFormat", ctypes.c_uint16),
        ("ptd", ctypes.c_void_p),
        ("dwAspect", ctypes.c_uint32),
        ("lindex", ctypes.c_int32),
        ("tymed", ctypes.c_uint32),
    ]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [
        ("tymed", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
        ("pUnkForRelease", ctypes.c_void_p),
    ]


class ComObject(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.c_void_p)]


HRESULT = ctypes.c_long
QI_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
ADD_FN = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
REL_FN = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
GETDATA_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(FORMATETC), ctypes.POINTER(STGMEDIUM))
QUERY_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(FORMATETC))
ENUM_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
SET_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(FORMATETC), ctypes.POINTER(STGMEDIUM), ctypes.c_int)
CANON_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(FORMATETC), ctypes.POINTER(FORMATETC))
ADVISE_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(FORMATETC), ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32))
UNADVISE_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_uint32)
ENUMADVISE_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
NEXT_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(FORMATETC), ctypes.POINTER(ctypes.c_ulong))
SKIP_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_ulong)
RESET_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p)
CLONE_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
CONTINUE_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32)
FEEDBACK_FN = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_uint32)


class DataVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", QI_FN),
        ("AddRef", ADD_FN),
        ("Release", REL_FN),
        ("GetData", GETDATA_FN),
        ("GetDataHere", GETDATA_FN),
        ("QueryGetData", QUERY_FN),
        ("GetCanonicalFormatEtc", CANON_FN),
        ("SetData", SET_FN),
        ("EnumFormatEtc", ENUM_FN),
        ("DAdvise", ADVISE_FN),
        ("DUnadvise", UNADVISE_FN),
        ("EnumDAdvise", ENUMADVISE_FN),
    ]


class EnumVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", QI_FN),
        ("AddRef", ADD_FN),
        ("Release", REL_FN),
        ("Next", NEXT_FN),
        ("Skip", SKIP_FN),
        ("Reset", RESET_FN),
        ("Clone", CLONE_FN),
    ]


class DropVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", QI_FN),
        ("AddRef", ADD_FN),
        ("Release", REL_FN),
        ("QueryContinueDrag", CONTINUE_FN),
        ("GiveFeedback", FEEDBACK_FN),
    ]


# アドレスごとに、参照カウントと中身を置く。COMから見えるのは先頭の関数テーブルだけ。
_STATE: dict[int, dict] = {}
_KEEP: list[object] = []


def _guid(text: str) -> GUID:
    value = GUID()
    if ctypes.windll.ole32.CLSIDFromString(text, ctypes.byref(value)) != 0:
        raise OSError(f"GUIDを解釈できません: {text}")
    return value


def _same_guid(left: GUID, right: GUID) -> bool:
    return ctypes.memcmp(ctypes.byref(left), ctypes.byref(right), ctypes.sizeof(GUID)) == 0


def _iids() -> dict[str, GUID]:
    return {
        "unknown": _guid("{00000000-0000-0000-C000-000000000046}"),
        "data": _guid("{0000010E-0000-0000-C000-000000000046}"),
        "enum": _guid("{00000103-0000-0000-C000-000000000046}"),
        "drop": _guid("{00000121-0000-0000-C000-000000000046}"),
    }


def _new(vtbl: ctypes.Structure, state: dict) -> ComObject:
    obj = ComObject()
    obj.lpVtbl = ctypes.cast(ctypes.pointer(vtbl), ctypes.c_void_p)
    state["refs"] = 1
    _STATE[ctypes.addressof(obj)] = state
    _KEEP.append(obj)
    _KEEP.append(vtbl)
    return obj


def _qi(this, riid, ppv, names: list[str], iids: dict[str, GUID]):
    try:
        state = _STATE.get(this)
        if state is None or not ppv:
            if ppv:
                ppv[0] = None
            return E_FAIL
        wanted = riid.contents
        for name in names:
            if _same_guid(wanted, iids[name]):
                state["refs"] += 1
                ppv[0] = this
                return S_OK
        ppv[0] = None
        return E_NOINTERFACE
    except Exception:
        LOGGER.exception("QueryInterfaceに失敗しました")
        return E_FAIL


def _add(this):
    state = _STATE.get(this)
    if state is None:
        return 0
    state["refs"] += 1
    return state["refs"]


def _release(this):
    state = _STATE.get(this)
    if state is None:
        return 0
    state["refs"] -= 1
    if state["refs"] <= 0:
        _STATE.pop(this, None)
        return 0
    return state["refs"]


def _hglobal(data: bytes) -> int:
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("メモリを確保できませんでした。")
    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise OSError("メモリをロックできませんでした。")
    ctypes.memmove(locked, data, len(data))
    kernel32.GlobalUnlock(handle)
    return int(handle)


def _stream_for(path: str) -> int:
    shell32 = ctypes.windll.shell32
    shell32.SHCreateStreamOnFileEx.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHCreateStreamOnFileEx.restype = HRESULT
    stream = ctypes.c_void_p()
    result = shell32.SHCreateStreamOnFileEx(path, STGM_READ | STGM_SHARE_DENY_NONE, 0, False, None, ctypes.byref(stream))
    if result != S_OK or not stream.value:
        raise OSError("ファイルを開けませんでした。")
    return int(stream.value)


class _StatusWindow:
    def __init__(self) -> None:
        self.hwnd = 0
        user32 = ctypes.windll.user32
        self.hwnd = user32.CreateWindowExW(
            0x00000008 | 0x00000080,
            "STATIC",
            "フォルダへコピーする準備をしています",
            0x80000000 | 0x10000000 | 0x00800000,
            48,
            48,
            480,
            52,
            None,
            None,
            None,
            None,
        )

    def update(self, text: str) -> None:
        if not self.hwnd:
            return
        user32 = ctypes.windll.user32
        user32.SetWindowTextW(self.hwnd, text)
        user32.InvalidateRect(self.hwnd, None, True)
        user32.UpdateWindow(self.hwnd)

    def close(self) -> None:
        if self.hwnd:
            ctypes.windll.user32.DestroyWindow(self.hwnd)
            self.hwnd = 0


def _make_format(fmt: int, tymed: int, index: int = -1) -> FORMATETC:
    item = FORMATETC()
    item.cfFormat = fmt
    item.ptd = None
    item.dwAspect = DVASPECT_CONTENT
    item.lindex = index
    item.tymed = tymed
    return item


def drag_virtual_files(
    entries: list[tuple[str, int]],
    ensure_path: EnsurePath,
    existing_paths: list[str | None] | None = None,
) -> None:
    """マウスボタンを押したまま呼ぶ。ドロップ先が中身を読むときに ensure_path が動く。"""
    if not ole_available():
        raise OSError("エクスプローラーへのドラッグはWindowsだけで使えます。")
    if not entries:
        return
    ole32 = ctypes.windll.ole32
    started = ole32.OleInitialize(None) == S_OK
    status = _StatusWindow()
    try:
        data, drop_source = _create_pair(entries, ensure_path, existing_paths or [], status)
        effect = ctypes.c_uint32(0)
        ole32.DoDragDrop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        ole32.DoDragDrop.restype = HRESULT
        ole32.DoDragDrop(ctypes.addressof(data), ctypes.addressof(drop_source), DROPEFFECT_COPY, ctypes.byref(effect))
    finally:
        status.close()
        if started:
            ole32.OleUninitialize()


def _clipboard_format(name: str) -> int:
    user32 = ctypes.windll.user32
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = ctypes.c_uint
    value = user32.RegisterClipboardFormatW(name)
    if not value:
        raise OSError(f"形式を登録できませんでした: {name}")
    return int(value)


def _create_pair(entries, ensure_path, existing_paths, status: _StatusWindow):
    iids = _iids()
    descriptor = _clipboard_format("FileGroupDescriptorW")
    contents = _clipboard_format("FileContents")
    prefer = _clipboard_format("Preferred DropEffect")
    offer_hdrop = len(existing_paths) == len(entries) and all(existing_paths)
    formats = [
        _make_format(prefer, TYMED_HGLOBAL),
        _make_format(descriptor, TYMED_HGLOBAL),
        _make_format(contents, TYMED_ISTREAM, -1),
    ]
    if offer_hdrop:
        formats.append(_make_format(CF_HDROP, TYMED_HGLOBAL))

    def data_qi(this, riid, ppv):
        return _qi(this, riid, ppv, ["unknown", "data"], iids)

    def enum_qi(this, riid, ppv):
        return _qi(this, riid, ppv, ["unknown", "enum"], iids)

    def drop_qi(this, riid, ppv):
        return _qi(this, riid, ppv, ["unknown", "drop"], iids)

    def get_data(this, fmt, medium):
        state = _STATE.get(this)
        if state is None or not medium:
            return E_FAIL
        try:
            info = fmt.contents
            if info.cfFormat == prefer:
                medium.contents.tymed = TYMED_HGLOBAL
                medium.contents.data = _hglobal(struct.pack("<I", DROPEFFECT_COPY))
                medium.contents.pUnkForRelease = None
                return S_OK
            if info.cfFormat == descriptor:
                medium.contents.tymed = TYMED_HGLOBAL
                medium.contents.data = _hglobal(pack_file_group_descriptor(list(state["entries"])))
                medium.contents.pUnkForRelease = None
                return S_OK
            if info.cfFormat == contents:
                index = int(info.lindex)
                if index < 0 or index >= len(state["entries"]):
                    return DV_E_LINDEX
                name, _size = state["entries"][index]
                state["status"].update(f"コピーの準備 {index + 1}/{len(state['entries'])}  {name}")
                path = state["ensure_path"](index)
                medium.contents.tymed = TYMED_ISTREAM
                medium.contents.data = _stream_for(path)
                medium.contents.pUnkForRelease = None
                return S_OK
            if offer_hdrop and info.cfFormat == CF_HDROP:
                paths = [path or state["ensure_path"](index) for index, path in enumerate(state["existing"])]
                medium.contents.tymed = TYMED_HGLOBAL
                medium.contents.data = _hglobal(pack_hdrop(paths))
                medium.contents.pUnkForRelease = None
                return S_OK
            return DV_E_FORMATETC
        except Exception:
            LOGGER.exception("ドラッグ中のファイル準備に失敗しました")
            return E_FAIL

    def query_get(this, fmt):
        state = _STATE.get(this)
        if state is None:
            return E_FAIL
        info = fmt.contents
        if info.cfFormat in {prefer, descriptor} and info.tymed & TYMED_HGLOBAL:
            return S_OK
        if info.cfFormat == contents and info.tymed & TYMED_ISTREAM:
            if info.lindex == -1 or 0 <= info.lindex < len(state["entries"]):
                return S_OK
            return DV_E_LINDEX
        if offer_hdrop and info.cfFormat == CF_HDROP and info.tymed & TYMED_HGLOBAL:
            return S_OK
        return DV_E_FORMATETC

    def enum_format(this, direction, ppenum):
        state = _STATE.get(this)
        if state is None or not ppenum:
            return E_FAIL
        if direction != DATADIR_GET:
            return E_NOTIMPL
        enum = _make_enum(state["formats"], enum_vtbl)
        ppenum[0] = ctypes.addressof(enum)
        state["enum"] = enum
        return S_OK

    def not_impl(*_args):
        return E_NOTIMPL

    def no_advise(*_args):
        return OLE_E_ADVISENOTSUPPORTED

    def next_format(this, count, array, fetched):
        state = _STATE.get(this)
        if state is None:
            return E_FAIL
        made = 0
        while made < count and state["index"] < len(state["formats"]):
            array[made] = state["formats"][state["index"]]
            state["index"] += 1
            made += 1
        if fetched:
            fetched[0] = made
        return S_OK if made == count else 1

    def skip_format(this, count):
        state = _STATE.get(this)
        if state is None:
            return E_FAIL
        state["index"] = min(len(state["formats"]), state["index"] + int(count))
        return S_OK

    def reset_format(this):
        state = _STATE.get(this)
        if state is None:
            return E_FAIL
        state["index"] = 0
        return S_OK

    def continue_drag(_this, escape_pressed, key_state):
        if escape_pressed:
            return DRAGDROP_S_CANCEL
        if not (key_state & MK_LBUTTON):
            return DRAGDROP_S_DROP
        return S_OK

    def feedback(_this, _effect):
        return DRAGDROP_S_USEDEFAULTCURSORS

    data_vtbl = DataVtbl(
        QI_FN(data_qi),
        ADD_FN(_add),
        REL_FN(_release),
        GETDATA_FN(get_data),
        GETDATA_FN(lambda *args: E_NOTIMPL),
        QUERY_FN(query_get),
        CANON_FN(lambda *args: E_NOTIMPL),
        SET_FN(lambda *args: E_NOTIMPL),
        ENUM_FN(enum_format),
        ADVISE_FN(lambda *args: OLE_E_ADVISENOTSUPPORTED),
        UNADVISE_FN(lambda *args: OLE_E_ADVISENOTSUPPORTED),
        ENUMADVISE_FN(lambda *args: OLE_E_ADVISENOTSUPPORTED),
    )
    enum_vtbl = EnumVtbl(
        QI_FN(enum_qi),
        ADD_FN(_add),
        REL_FN(_release),
        NEXT_FN(next_format),
        SKIP_FN(skip_format),
        RESET_FN(reset_format),
        CLONE_FN(lambda *args: E_NOTIMPL),
    )
    drop_vtbl = DropVtbl(
        QI_FN(drop_qi),
        ADD_FN(_add),
        REL_FN(_release),
        CONTINUE_FN(continue_drag),
        FEEDBACK_FN(feedback),
    )
    _KEEP.extend([data_vtbl, enum_vtbl, drop_vtbl])
    data = _new(
        data_vtbl,
        {
            "entries": list(entries),
            "ensure_path": ensure_path,
            "existing": list(existing_paths),
            "formats": formats,
            "status": status,
        },
    )
    drop = _new(drop_vtbl, {})
    return data, drop


def _make_enum(formats: list[FORMATETC], vtbl: EnumVtbl) -> ComObject:
    return _new(vtbl, {"formats": formats, "index": 0})
