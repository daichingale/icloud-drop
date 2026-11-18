"""表示の絞り込み、並び、日付ごとのまとまり。"""

from __future__ import annotations

from datetime import date, timezone

from icloud_drop.formatutil import format_ja_date, local_date
from icloud_drop.models import Asset, Group


def filter_sort(
    assets: list[Asset],
    *,
    kind: str = "all",
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "newest",
    tz: timezone | None = None,
) -> list[Asset]:
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    picked: list[Asset] = []
    for asset in assets:
        if kind == "photo" and asset.kind != "photo":
            continue
        if kind == "video" and asset.kind != "video":
            continue
        if date_from or date_to:
            seen = local_date(asset.created, tz)
            if date_from and seen < date_from:
                continue
            if date_to and seen > date_to:
                continue
        picked.append(asset)

    if sort == "name":
        picked.sort(key=lambda asset: (asset.filename.casefold(), asset.id))
    elif sort == "oldest":
        picked.sort(key=lambda asset: (asset.created, asset.id))
    else:
        picked.sort(key=lambda asset: (asset.created, asset.id), reverse=True)
    return picked


def group_assets(assets: list[Asset], *, sort: str = "newest", tz: timezone | None = None) -> list[Group]:
    if sort == "name":
        return [Group(title="", assets=list(assets))] if assets else []
    groups: list[Group] = []
    current: Group | None = None
    current_date: date | None = None
    for asset in assets:
        seen = local_date(asset.created, tz)
        if current is None or seen != current_date:
            current = Group(title=format_ja_date(seen), assets=[])
            groups.append(current)
            current_date = seen
        current.assets.append(asset)
    return groups


def ids_between(
    assets: list[Asset],
    start: date,
    end: date,
    *,
    tz: timezone | None = None,
) -> list[str]:
    if start > end:
        start, end = end, start
    found: list[str] = []
    for asset in assets:
        seen = local_date(asset.created, tz)
        if start <= seen <= end:
            found.append(asset.id)
    return found
