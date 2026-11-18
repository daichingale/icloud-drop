import json

from icloud_drop.errors import AccessDeniedError
from icloud_drop.mock_backend import DEMO_CODE, MockSource, build_demo_library


def library():
    return build_demo_library(count=12)


def test_demo_library_shape():
    demo = build_demo_library(count=30)
    assert {album.id for album in demo.albums} == {"all", "family", "travel", "shots"}
    assert len(demo.by_album["all"]) == 30
    assert demo.by_album["shots"]
    assert any(asset.is_live for asset in demo.by_album["all"])
    assert any(asset.kind == "video" for asset in demo.by_album["all"])
    live = next(asset for asset in demo.by_album["all"] if asset.is_live)
    assert live.live_filename.endswith(".MOV")
    assert demo.blobs[(live.id, "live")]
    original = demo.blobs[(live.id, "original")]
    assert len(original) == live.size


def test_mock_requires_code_then_reuses_session(tmp_path):
    source = MockSource(tmp_path, library=library())
    failed = source.login("me@icloud.com", "wrong", adp_web_access=False, domain="com")
    assert failed.status == "failed"
    need = source.login("me@icloud.com", "secret", adp_web_access=False, domain="com")
    assert need.status == "need_2fa"
    assert need.phones
    bad = source.submit_code("000000")
    assert bad.status == "failed"
    ok = source.submit_code(DEMO_CODE)
    assert ok.status == "ok"
    text = (tmp_path / "mock-accounts.json").read_text(encoding="utf-8")
    assert "secret" not in text
    saved = json.loads(text)
    assert saved["me@icloud.com"]["trusted"] is True

    again = MockSource(tmp_path, library=library())
    resumed = again.login("me@icloud.com", "", adp_web_access=False, domain="com")
    assert resumed.status == "ok"
    albums = again.list_albums()
    assert albums[0].title == "すべての写真"
    batches = list(again.iter_batches("all", batch_size=5))
    assert sum(len(batch) for batch in batches) == 12
    asset_id = batches[0][0].id
    assert again.fetch_thumbnail(asset_id).startswith(b"\xff\xd8")
    assert again.open_original(asset_id).read() == again.library.blobs[(asset_id, "original")]
    again.forget()
    forgotten = MockSource(tmp_path, library=library())
    assert forgotten.login("me@icloud.com", "", adp_web_access=False, domain="com").status == "failed"


def test_mock_reads_original_bytes(tmp_path):
    demo = library()
    source = MockSource(tmp_path, demo=True, library=demo)
    asset = demo.by_album["all"][0]
    stream = source.open_original(asset.id, start=2)
    assert stream.status == 206
    assert stream.read() == demo.blobs[(asset.id, "original")][2:]


def test_adp_and_device_approval(tmp_path):
    source = MockSource(tmp_path, library=library())
    signed = source.login("adp@icloud.com", "secret", adp_web_access=False, domain="com")
    assert source.submit_code(DEMO_CODE).status == "ok"
    try:
        source.list_albums()
        raise AssertionError("ADPなのに一覧が見えてしまいました")
    except AccessDeniedError as exc:
        assert "Web上のiCloudデータにアクセス" in exc.hint

    waiting = MockSource(tmp_path / "b", library=library())
    assert waiting.login("adp@icloud.com", "secret", adp_web_access=True, domain="com").status == "need_2fa"
    approval = waiting.submit_code(DEMO_CODE)
    assert approval.status == "need_device_approval"
    assert waiting.approve_on_device().status == "ok"
    assert waiting.list_albums()

    push = MockSource(tmp_path / "c", library=library())
    assert push.login("approve@icloud.com", "secret", adp_web_access=False, domain="com").status == "need_2fa"
    assert push.approve_on_device().status == "ok"


def test_two_sa_and_security_key(tmp_path):
    source = MockSource(tmp_path, library=library())
    result = source.login("2sa@icloud.com", "secret", adp_web_access=False, domain="com")
    assert result.status == "need_2sa"
    assert source.submit_2sa_code(0, DEMO_CODE).status == "failed"
    assert source.send_2sa(0).status == "need_2sa"
    assert source.submit_2sa_code(0, DEMO_CODE).status == "ok"

    key = MockSource(tmp_path / "key", library=library())
    challenge = key.login("fido@icloud.com", "secret", adp_web_access=False, domain="com")
    assert challenge.security_key
    assert key.approve_security_key().status == "ok"
