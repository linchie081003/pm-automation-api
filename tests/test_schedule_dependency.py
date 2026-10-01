from datetime import date

from app.services.schedule_dependency import (
    PredecessorLinkType,
    resolve_successor_span,
)


def test_resolve_ff_aligns_finish(monkeypatch):
    def fake_add(start, days, db=None):
        if days == 1:
            return start
        return date(2026, 10, 8)

    def fake_sub(end, days, db=None):
        return date(2026, 10, 6)

    monkeypatch.setattr(
        "app.services.business_calendar.add_business_days",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.subtract_business_days",
        fake_sub,
    )

    start, target = resolve_successor_span(
        PredecessorLinkType.FF,
        pred_start=date(2026, 10, 1),
        pred_end=date(2026, 10, 8),
        duration_days=3,
        db=None,
    )
    assert target == date(2026, 10, 8)
    assert start == date(2026, 10, 6)
