from datetime import datetime, timezone

from icloud_drop.download import CancelToken, build_drag_plan, save_assets
from icloud_drop.dragdrop import parse_file_group_descriptor, parse_hdrop, pack_file_group_descriptor, pack_hdrop
from icloud_drop.errors import TransientError
from icloud_drop.filenames import live_photo_mov_name
from icloud_drop.heic_convert import HeicConvertError, convert_heic_to_jpeg
from icloud_drop.models import Asset
from icloud_drop.streams import ByteStream

UTC = timezone.utc


def make_asset(asset_id: str, filename: str, payload: bytes, *, live: bytes | None = None) -> Asset:
    return Asset(
        id=asset_id,
        filename=filename,
        kind="photo" if not filename.lower().endswith(".mov") else "video",
        created=datetime(2024, 1, 2, 8, 0, tzinfo=UTC),
        size=len(payload),
        is_live=live is not None,
        live_filename=live_photo_mov_name(filename) if live is not None else None,
        live_size=len(live) if live else 0,
    )


def opener_for(blobs):
    calls = []

    def opener(asset_id, which, start):
        calls.append((asset_id, which, start))
        data = blobs[(asset_id, which)]
        if start and start < len(data):
            return ByteStream(data, start)
        return ByteStream(data, start)

    opener.calls = calls
    return opener


def test_save_skip_resume_live_and_collision(tmp_path):
    photo = b"hello-photo"
    movie = b"hello-movie"
    other = b"another!!!"
    blobs = {
        ("a", "original"): photo,
        ("a", "live"): movie,
        ("b", "original"): other,
    }
    first = make_asset("a", "IMG_0001.HEIC", photo, live=movie)
    second = make_asset("b", "IMG_0001.HEIC", other)
    opener = opener_for(blobs)
    summary = save_assets(
        [first, second],
        dest_dir=tmp_path,
        cache_root=tmp_path / "cache",
        opener=opener,
        include_live=True,
    )
    assert summary.failed == 0
    assert summary.downloaded == 3
    assert (tmp_path / "IMG_0001.HEIC").read_bytes() == photo
    assert (tmp_path / "IMG_0001.MOV").read_bytes() == movie
    assert (tmp_path / "IMG_0001 (2).HEIC").read_bytes() == other
    again = opener_for(blobs)
    skipped = save_assets(
        [first, second],
        dest_dir=tmp_path,
        cache_root=tmp_path / "cache",
        opener=again,
        include_live=True,
    )
    assert skipped.downloaded == 0
    assert skipped.skipped == 3
    assert again.calls == []


def test_resume_partial_file(tmp_path):
    payload = b"abcdefghij"
    asset = make_asset("a", "note.jpg", payload)
    part = tmp_path / "note.jpg.part"
    part.write_bytes(b"abcd")
    opener = opener_for({("a", "original"): payload})
    summary = save_assets([asset], dest_dir=tmp_path, cache_root=tmp_path / "cache", opener=opener)
    assert summary.downloaded == 1
    assert (tmp_path / "note.jpg").read_bytes() == payload
    assert opener.calls == [("a", "original", 4)]
    assert not part.exists()


def test_range_ignored_rewrites(tmp_path):
    payload = b"abcdefghij"
    asset = make_asset("a", "note.jpg", payload)
    part = tmp_path / "note.jpg.part"
    part.write_bytes(b"ab")

    def opener(asset_id, which, start):
        return ByteStream(payload, start, status=200)

    summary = save_assets([asset], dest_dir=tmp_path, cache_root=tmp_path / "cache", opener=opener)
    assert summary.downloaded == 1
    assert (tmp_path / "note.jpg").read_bytes() == payload


def test_cancel_and_retry(tmp_path):
    payload = b"xyz"
    asset = make_asset("a", "a.jpg", payload)
    token = CancelToken()
    token.cancel()
    summary = save_assets(
        [asset],
        dest_dir=tmp_path,
        cache_root=tmp_path / "cache",
        opener=opener_for({("a", "original"): payload}),
        cancel=token,
    )
    assert summary.cancelled
    assert summary.downloaded == 0

    calls = {"n": 0}

    def flaky(asset_id, which, start):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("混んでいます")
        return ByteStream(payload)

    summary = save_assets(
        [asset],
        dest_dir=tmp_path / "retry",
        cache_root=tmp_path / "cache2",
        opener=flaky,
    )
    assert summary.downloaded == 1
    assert calls["n"] == 3


def test_heic_convert_and_drag_plan(tmp_path):
    payload = b"\xff\xd8fake-jpeg"
    live = b"mov-bytes"
    asset = make_asset("a", "IMG_9.HEIC", payload, live=live)
    blobs = {("a", "original"): payload, ("a", "live"): live}
    summary = save_assets(
        [asset],
        dest_dir=tmp_path,
        cache_root=tmp_path / "cache",
        opener=opener_for(blobs),
        convert_heic=True,
        include_live=True,
    )
    assert summary.failed == 0
    assert (tmp_path / "IMG_9.jpg").read_bytes() == payload
    assert (tmp_path / "IMG_9.MOV").read_bytes() == live
    assert not (tmp_path / "IMG_9.HEIC").exists()
    plan = build_drag_plan([asset], include_live=True)
    assert [item.filename for item in plan] == ["IMG_9.HEIC", "IMG_9.MOV"]
    packed = pack_file_group_descriptor([(item.filename, item.size) for item in plan])
    assert parse_file_group_descriptor(packed) == [("IMG_9.HEIC", len(payload)), ("IMG_9.MOV", len(live))]


def test_descriptor_handles_thousands_of_names():
    entries = [(f"IMG_{index:05d}.JPG", index + 10) for index in range(1000)]
    parsed = parse_file_group_descriptor(pack_file_group_descriptor(entries))
    assert len(parsed) == 1000
    assert parsed[0] == ("IMG_00000.JPG", 10)
    assert parsed[-1] == ("IMG_00999.JPG", 1009)
    long_name = "あ" * 300 + ".JPG"
    parsed_long = parse_file_group_descriptor(pack_file_group_descriptor([(long_name, 3)]))
    assert parsed_long[0][0].endswith(".JPG")
    assert len(parsed_long[0][0]) < len(long_name)


def test_hdrop_roundtrip():
    paths = [r"C:\Photos\IMG_0001.HEIC", r"C:\Photos\IMG_0001.MOV"]
    assert parse_hdrop(pack_hdrop(paths)) == paths


def test_heic_converter_paths(tmp_path):
    source = tmp_path / "a.heic"
    source.write_bytes(b"\xff\xd8rest")
    dest = tmp_path / "a.jpg"
    convert_heic_to_jpeg(source, dest)
    assert dest.read_bytes().startswith(b"\xff\xd8")

    def custom(src, out):
        out.write_bytes(b"converted")

    other = tmp_path / "b.jpg"
    convert_heic_to_jpeg(source, other, converter=custom)
    assert other.read_bytes() == b"converted"
    bad = tmp_path / "bad.heic"
    bad.write_bytes(b"not-a-real-heic-file")
    try:
        convert_heic_to_jpeg(bad, tmp_path / "bad.jpg")
    except HeicConvertError:
        return
    raise AssertionError("壊れたHEICは失敗するはずです")
