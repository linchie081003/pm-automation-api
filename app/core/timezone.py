"""Application timezone: Asia/Jakarta (WIB).

ORM ``DateTime`` columns store naive timestamps interpreted as WIB.
Calendar ``date`` helpers (today, cut-off, report week) use the Jakarta date.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("Asia/Jakarta")
APP_TZ_NAME = "Asia/Jakarta"


def now_jakarta() -> datetime:
    """Current time as naive datetime in Asia/Jakarta (for SQLAlchemy DateTime)."""
    return datetime.now(APP_TZ).replace(tzinfo=None)


def today_jakarta() -> date:
    """Current calendar date in Asia/Jakarta."""
    return datetime.now(APP_TZ).date()


def timestamp_ms_to_jakarta_date(ms: float) -> date:
    """Unix epoch milliseconds → calendar date in Jakarta."""
    return datetime.fromtimestamp(ms / 1000.0, tz=APP_TZ).date()


def jakarta_date_to_timestamp_ms(value: date) -> int:
    """Calendar date (Jakarta) → ClickUp-style ms at start of that day in WIB."""
    dt = datetime.combine(value, datetime.min.time(), tzinfo=APP_TZ)
    return int(dt.timestamp() * 1000)


def parse_epoch_to_jakarta_date(value: int | float | str | None) -> date | None:
    """Parse ClickUp-style epoch (s or ms) or ISO date string → Jakarta date."""
    if value is None or value == "":
        return None
    ms: float | None = None
    if isinstance(value, (int, float)):
        ms = float(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            ms = float(raw)
        except ValueError:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                return None
    else:
        return None
    if ms <= 0:
        return None
    if ms < 1e11:
        ms *= 1000.0
    return timestamp_ms_to_jakarta_date(ms)
