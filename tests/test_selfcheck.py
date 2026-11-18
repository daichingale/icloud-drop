import sys

from icloud_drop.errors import ICloudError
from icloud_drop.ole_drag import ole_available
from icloud_drop.pyicloud_backend import PyiCloudSource, cookie_stem
from icloud_drop.selfcheck import run_self_check


def test_self_check_passes():
    assert run_self_check() == 0


def test_ole_drag_is_windows_only():
    assert ole_available() is (sys.platform == "win32")


def test_cookie_stem_matches_pyicloud_style():
    assert cookie_stem("demo@icloud.com") == "demoicloudcom"


def test_real_backend_explains_missing_library(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "pyicloud_ipd" or name.startswith("pyicloud_ipd."):
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    source = PyiCloudSource(tmp_path)
    try:
        source.login("me@icloud.com", "secret", adp_web_access=True, domain="com")
    except ICloudError as exc:
        assert "部品" in str(exc)
        assert exc.hint
        return
    raise AssertionError("ライブラリが無いときは失敗するはずです")
