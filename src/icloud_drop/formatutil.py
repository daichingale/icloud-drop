"""画面に出す日付とサイズ。"""

from __future__ import annotations

from datetime import date, datetime, timezone

_WEEKDAYS = "月火水木金土日"


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def local_date(value: datetime, tz: timezone | None = None) -> date:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if tz is None:
        return aware.astimezone().date()
    return aware.astimezone(tz).date()


def format_ja_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日（{_WEEKDAYS[value.weekday()]}）"


def format_ja_datetime(value: datetime, tz: timezone | None = None) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    local = aware.astimezone(tz) if tz is not None else aware.astimezone()
    return f"{format_ja_date(local.date())} {local.hour:02d}:{local.minute:02d}"


def human_size(size: int) -> str:
    if size < 0:
        size = 0
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"
