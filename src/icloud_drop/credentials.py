"""パスワードの保存。

希望したときだけ保存する。平文では書かない。
Windowsでは、ユーザー専用のDPAPIで data フォルダ内のファイルを守る。
資格情報マネージャー（keyring）は、DPAPIが使えないときの予備。
どちらも使えなければ保存せず、その旨を返す。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from icloud_drop.errors import PasswordStoreError
from icloud_drop.filenames import sanitize_filename

SERVICE_NAME = "IcloudDrop"


class Protector(Protocol):
    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, data: bytes) -> bytes: ...


class KeyringLike(Protocol):
    def set_password(self, service: str, account: str, password: str) -> None: ...

    def get_password(self, service: str, account: str) -> str | None: ...

    def delete_password(self, service: str, account: str) -> None: ...


class WindowsDpapi:
    """CryptProtectData。レジストリはアプリ側では書かない。"""

    def protect(self, data: bytes) -> bytes:
        return _crypt(data, encrypt=True)

    def unprotect(self, data: bytes) -> bytes:
        return _crypt(data, encrypt=False)


def _crypt(data: bytes, *, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(data)
    blob_in = DATA_BLOB(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if encrypt:
        ok = fn(
            ctypes.byref(blob_in),
            "IcloudDrop",
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
    else:
        ok = fn(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
    if not ok:
        raise OSError("DPAPIでパスワードを処理できませんでした。")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class PasswordVault:
    def __init__(self, directory: Path, *, dpapi: Protector | None, keyring_module: KeyringLike | None) -> None:
        self.directory = directory
        self.dpapi = dpapi
        self.keyring = keyring_module

    def _file(self, account: str) -> Path:
        safe = sanitize_filename(account, fallback="account")
        return self.directory / f"{safe}.bin"

    def save(self, account: str, password: str) -> str:
        if not account or password is None:
            raise PasswordStoreError("保存するパスワードがありません。")
        errors: list[str] = []
        if self.dpapi is not None:
            try:
                blob = self.dpapi.protect(password.encode("utf-8"))
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self._file(account)
                path.write_bytes(blob)
                return "dpapi"
            except Exception as exc:  # noqa: BLE001 - 予備の保存方法へ進む
                errors.append(str(exc))
        if self.keyring is not None:
            try:
                self.keyring.set_password(SERVICE_NAME, account, password)
                return "keyring"
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        detail = " / ".join(errors) if errors else "保存先がありません"
        raise PasswordStoreError(f"パスワードは保存できませんでした。次回も入力してください。（{detail}）")

    def load(self, account: str) -> str | None:
        path = self._file(account)
        if self.dpapi is not None and path.exists():
            try:
                return self.dpapi.unprotect(path.read_bytes()).decode("utf-8")
            except Exception:
                pass
        if self.keyring is not None:
            try:
                return self.keyring.get_password(SERVICE_NAME, account)
            except Exception:
                return None
        return None

    def delete(self, account: str) -> None:
        path = self._file(account)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if self.keyring is not None:
            try:
                self.keyring.delete_password(SERVICE_NAME, account)
            except Exception:
                pass

    def contains_plaintext(self, password: str) -> bool:
        """テスト用。フォルダ内のファイルにパスワードそのものが含まれていないか。"""
        if not self.directory.exists():
            return False
        raw = password.encode("utf-8")
        for file in self.directory.iterdir():
            try:
                if raw in file.read_bytes():
                    return True
            except OSError:
                continue
        return False


def open_vault(directory: Path) -> PasswordVault:
    dpapi: Protector | None = WindowsDpapi() if sys.platform == "win32" else None
    keyring_module: KeyringLike | None = None
    try:
        import keyring
    except Exception:
        keyring = None  # type: ignore[assignment]
    if keyring is not None:
        keyring_module = keyring
    return PasswordVault(directory, dpapi=dpapi, keyring_module=keyring_module)
