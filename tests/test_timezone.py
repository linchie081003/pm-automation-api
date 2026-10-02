from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.timezone import (
    APP_TZ,
    jakarta_date_to_timestamp_ms,
    parse_epoch_to_jakarta_date,
    timestamp_ms_to_jakarta_date,
)


def test_jakarta_date_roundtrip_midnight_wib():
    d = date(2026, 3, 15)
    ms = jakarta_date_to_timestamp_ms(d)
    back = timestamp_ms_to_jakarta_date(ms)
    assert back == d
    dt = datetime.fromtimestamp(ms / 1000.0, tz=APP_TZ)
    assert dt.hour == 0


def test_parse_epoch_uses_jakarta_not_utc():
    # 2026-01-01 00:30 UTC = 2026-01-01 07:30 WIB → same calendar day
    ms = int(datetime(2026, 1, 1, 0, 30, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    assert parse_epoch_to_jakarta_date(ms) == date(2026, 1, 1)
    # 2026-01-01 17:00 UTC = 2026-01-02 00:00 WIB → next day in Jakarta
    ms_late = int(datetime(2026, 1, 1, 17, 0, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    assert parse_epoch_to_jakarta_date(ms_late) == date(2026, 1, 2)
