from datetime import datetime, timezone

from icloud_drop.filenames import live_photo_mov_name, sanitize_filename, unique_filename
from icloud_drop.library_index import filter_sort, group_assets, ids_between
from icloud_drop.models import Asset
from icloud_drop.selection import Selection

UTC = timezone.utc


def asset(asset_id: str, name: str, day: int, *, kind: str = "photo", live: bool = False) -> Asset:
    return Asset(
        id=asset_id,
        filename=name,
        kind=kind,
        created=datetime(2024, 3, day, 12, 0, tzinfo=UTC),
        size=10,
        is_live=live,
        live_filename=live_photo_mov_name(name) if live else None,
        live_size=4 if live else 0,
    )


def test_sanitize_windows_names():
    assert sanitize_filename('a<b>:c?.jpg') == "a_b__c_.jpg"
    assert sanitize_filename("CON.txt") == "_CON.txt"
    assert sanitize_filename("  photo.jpg.  ") == "photo.jpg"
    assert live_photo_mov_name("IMG_0001.HEIC") == "IMG_0001.MOV"


def test_unique_filename():
    used = {"IMG.JPG"}
    assert unique_filename("IMG.JPG", used) == "IMG (2).JPG"
    used.add("IMG (2).JPG")
    assert unique_filename("IMG.JPG", used) == "IMG (3).JPG"


def test_selection_click_shift_ctrl():
    ordered = ["a", "b", "c", "d"]
    selection = Selection()
    selection.click(ordered, "b", shift=False, ctrl=False)
    assert selection.chosen == {"b"}
    selection.click(ordered, "d", shift=False, ctrl=True)
    assert selection.chosen == {"b", "d"}
    selection.click(ordered, "a", shift=False, ctrl=False)
    assert selection.chosen == {"a"}
    selection.click(ordered, "c", shift=True, ctrl=False)
    assert selection.chosen == {"a", "b", "c"}
    selection.click(ordered, "b", shift=False, ctrl=True)
    assert selection.chosen == {"a", "c"}
    selection.click(ordered, "d", shift=True, ctrl=True)
    assert selection.chosen == {"a", "b", "c", "d"}
    selection.set_ids(ordered)
    assert len(selection) == 4
    selection.clear()
    assert len(selection) == 0


def test_filter_sort_and_groups():
    assets = [
        asset("1", "b.jpg", 2),
        asset("2", "a.jpg", 1, kind="video"),
        asset("3", "c.HEIC", 2, live=True),
        asset("4", "d.jpg", 3),
    ]
    photos = filter_sort(assets, kind="photo", sort="oldest", tz=UTC)
    assert [item.id for item in photos] == ["1", "3", "4"]
    videos = filter_sort(assets, kind="video", tz=UTC)
    assert [item.id for item in videos] == ["2"]
    newest = filter_sort(assets, sort="newest", tz=UTC)
    assert newest[0].id == "4"
    named = filter_sort(assets, sort="name", tz=UTC)
    assert [item.filename for item in named] == ["a.jpg", "b.jpg", "c.HEIC", "d.jpg"]
    ranged = filter_sort(assets, date_from=datetime(2024, 3, 2).date(), date_to=datetime(2024, 3, 2).date(), tz=UTC)
    assert {item.id for item in ranged} == {"1", "3"}
    groups = group_assets(ranged, sort="newest", tz=UTC)
    assert len(groups) == 1
    assert "2024年3月2日" in groups[0].title
    assert group_assets(named, sort="name")[0].title == ""
    ids = ids_between(assets, datetime(2024, 3, 3).date(), datetime(2024, 3, 1).date(), tz=UTC)
    assert ids == ["1", "2", "3", "4"]
