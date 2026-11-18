import json

import pytest

from icloud_drop.credentials import PasswordStoreError, PasswordVault
from icloud_drop.errors import ADP_HINT, explain_exception
from icloud_drop.paths import resolve_data_dir
from icloud_drop.settings import Settings


class XorProtector:
    def protect(self, data: bytes) -> bytes:
        return bytes(byte ^ 0x3C for byte in data)

    def unprotect(self, data: bytes) -> bytes:
        return bytes(byte ^ 0x3C for byte in data)


class Boom:
    def protect(self, data: bytes) -> bytes:
        raise OSError("資格情報は使えません")

    def unprotect(self, data: bytes) -> bytes:
        raise OSError("読めません")


class MemoryKeyring:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.store = {}

    def set_password(self, service, account, password):
        if self.fail:
            raise OSError("keyring disabled")
        self.store[(service, account)] = password

    def get_password(self, service, account):
        if self.fail:
            raise OSError("keyring disabled")
        return self.store.get((service, account))

    def delete_password(self, service, account):
        self.store.pop((service, account), None)


def test_password_saved_only_with_protector(tmp_path):
    vault = PasswordVault(tmp_path, dpapi=XorProtector(), keyring_module=None)
    assert vault.save("me@icloud.com", "secret-value") == "dpapi"
    assert vault.load("me@icloud.com") == "secret-value"
    assert not vault.contains_plaintext("secret-value")
    vault.delete("me@icloud.com")
    assert vault.load("me@icloud.com") is None


def test_keyring_fallback_and_total_failure(tmp_path):
    keyring = MemoryKeyring()
    vault = PasswordVault(tmp_path, dpapi=Boom(), keyring_module=keyring)
    assert vault.save("me@icloud.com", "secret-value") == "keyring"
    assert vault.load("me@icloud.com") == "secret-value"
    assert not vault.contains_plaintext("secret-value")

    broken = PasswordVault(tmp_path / "none", dpapi=Boom(), keyring_module=MemoryKeyring(fail=True))
    with pytest.raises(PasswordStoreError):
        broken.save("me@icloud.com", "secret-value")
    assert not broken.contains_plaintext("secret-value")


def test_settings_never_keep_password(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"apple_id": "me@icloud.com", "password": "secret-value", "domain": "nope"}), encoding="utf-8")
    settings = Settings.load(path)
    assert settings.apple_id == "me@icloud.com"
    assert settings.domain == "com"
    assert not hasattr(settings, "password") or "password" not in settings.__dict__
    settings.remember_password = True
    settings.save(path)
    text = path.read_text(encoding="utf-8")
    assert "secret-value" not in text
    assert '"password"' not in text


def test_data_dir_prefers_portable(tmp_path, monkeypatch):
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    path, reason = resolve_data_dir(
        frozen=True,
        executable=exe_dir / "IcloudDrop.exe",
        cwd=tmp_path / "cwd",
        local_appdata=str(tmp_path / "local"),
        home=tmp_path / "home",
    )
    assert reason == "portable"
    assert path == exe_dir / "data"

    monkeypatch.setattr("icloud_drop.paths.portable_data_dir", lambda root: None)
    fallback, why = resolve_data_dir(
        frozen=True,
        executable=exe_dir / "IcloudDrop.exe",
        cwd=tmp_path / "cwd",
        local_appdata=str(tmp_path / "local"),
        home=tmp_path / "home",
    )
    assert why == "localappdata"
    assert fallback == tmp_path / "local" / "IcloudDrop"

    forced, why = resolve_data_dir(
        frozen=True,
        executable=exe_dir / "IcloudDrop.exe",
        cwd=tmp_path,
        local_appdata=None,
        home=tmp_path / "home",
        env_override=str(tmp_path / "override"),
    )
    assert why == "env"
    assert forced == tmp_path / "override"


def test_access_denied_hint():
    class ResponseError(Exception):
        def __init__(self):
            super().__init__("private db access disabled for this account (ACCESS_DENIED)")
            self.code = "ACCESS_DENIED"

    message, hint = explain_exception(ResponseError())
    assert "アクセスできません" in message
    assert hint == ADP_HINT
    assert "Web上のiCloudデータにアクセス" in hint
