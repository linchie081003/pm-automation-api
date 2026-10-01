from datetime import date

from app.services.rebaseline_diff import PhaseSnapshot, recalc_proposed_phases_dates


def test_recalc_proposed_phases_uses_business_chain(monkeypatch):
    calls: list[tuple] = []

    def fake_after(d, db=None):
        calls.append(("after", d))
        return date(2026, 10, 2)

    def fake_add(start, days, db=None):
        calls.append(("add", start, days))
        return date(2026, 10, 10)

    monkeypatch.setattr(
        "app.services.business_calendar.business_day_after",
        fake_after,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.add_business_days",
        fake_add,
    )

    phases = [
        PhaseSnapshot(
            name="A",
            start_date=date(2026, 9, 29),
            target_date=date(2026, 10, 1),
            weight_pct=50,
            milestone_id=1,
            sort_order=0,
            duration_days=3,
        ),
        PhaseSnapshot(
            name="B",
            start_date=date(2026, 10, 5),
            target_date=date(2026, 10, 8),
            weight_pct=50,
            milestone_id=2,
            sort_order=1,
            duration_days=3,
            predecessor_ref="m:1",
        ),
    ]
    out = recalc_proposed_phases_dates(None, phases, lifecycle_by_id={1: "open", 2: "open"})
    assert out[1].start_date == date(2026, 10, 2)
    assert out[1].target_date == date(2026, 10, 10)


def test_recalc_proposed_phases_predecessor_before_pred_in_sort_order(monkeypatch):
    """Predecessor may appear later in sort_order; start still follows pred selesai."""

    def fake_after(d, db=None):
        return date(2026, 10, 2)

    def fake_add(start, days, db=None):
        return date(2026, 10, 6)

    monkeypatch.setattr(
        "app.services.business_calendar.business_day_after",
        fake_after,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.add_business_days",
        fake_add,
    )

    phases = [
        PhaseSnapshot(
            name="B",
            start_date=date(2026, 10, 5),
            target_date=date(2026, 10, 8),
            weight_pct=50,
            milestone_id=2,
            sort_order=0,
            duration_days=3,
            predecessor_ref="m:1",
        ),
        PhaseSnapshot(
            name="A",
            start_date=date(2026, 9, 29),
            target_date=date(2026, 10, 1),
            weight_pct=50,
            milestone_id=1,
            sort_order=1,
            duration_days=3,
        ),
    ]
    out = recalc_proposed_phases_dates(None, phases, lifecycle_by_id={1: "open", 2: "open"})
    by_id = {p.milestone_id: p for p in out}
    assert by_id[2].start_date == date(2026, 10, 2)
