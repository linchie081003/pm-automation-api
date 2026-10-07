"""Draft timeline recalc via timeline_recalc_adapter (v2 engine path)."""

from datetime import date

import pytest

from app.services.timeline_recalc_adapter import recalc_draft_dict_rows
from app.services.timeline_schedule import infer_milestone_schedule_driver


def test_recalc_draft_multi_predecessor_fan_in(monkeypatch):
    monkeypatch.setattr(
        "app.services.business_calendar.business_day_after",
        lambda d, db=None: date(2026, 10, 7),
    )
    monkeypatch.setattr(
        "app.services.business_calendar.add_business_days",
        lambda start, days, db=None: date(2026, 10, 10) if days > 1 else start,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.count_business_days_inclusive",
        lambda s, e, db=None: 2,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.subtract_business_days",
        lambda end, days, db=None: date(2026, 10, 8),
    )

    rows = [
        {
            "row_key": "a",
            "name": "A",
            "item_type": "task",
            "sort_order": 0,
            "duration_days": 2,
        },
        {
            "row_key": "b",
            "name": "B",
            "item_type": "task",
            "sort_order": 1,
            "duration_days": 2,
        },
        {
            "row_key": "c",
            "name": "C",
            "item_type": "task",
            "sort_order": 2,
            "duration_days": 2,
            "predecessors": [
                {"predecessor_ref": "a", "link_type": "FS", "lag_days": 0},
                {"predecessor_ref": "b", "link_type": "FS", "lag_days": 0},
            ],
        },
    ]
    out = recalc_draft_dict_rows(None, rows, date(2026, 10, 1))
    by_key = {r["row_key"]: r for r in out}
    assert by_key["c"]["start_date"]
    assert by_key["c"]["target_date"]


def test_recalc_draft_predecessor_cycle_raises():
    rows = [
        {
            "row_key": "x",
            "item_type": "task",
            "sort_order": 0,
            "duration_days": 1,
            "predecessors": [{"predecessor_ref": "y", "link_type": "FS", "lag_days": 0}],
        },
        {
            "row_key": "y",
            "item_type": "task",
            "sort_order": 1,
            "duration_days": 1,
            "predecessors": [{"predecessor_ref": "x", "link_type": "FS", "lag_days": 0}],
        },
    ]
    with pytest.raises(ValueError, match="siklus|cycle|predecessor|Predecessor"):
        recalc_draft_dict_rows(None, rows, date(2026, 10, 1))


def test_milestone_manual_gate_preserved_on_recalc(monkeypatch):
    monkeypatch.setattr(
        "app.services.business_calendar.business_day_after",
        lambda d, db=None: date(2026, 10, 6),
    )
    monkeypatch.setattr(
        "app.services.business_calendar.add_business_days",
        lambda start, days, db=None: start,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.count_business_days_inclusive",
        lambda s, e, db=None: 1,
    )

    rows = [
        {
            "row_key": "p1",
            "name": "Phase",
            "item_type": "phase",
            "sort_order": 0,
            "duration_days": 5,
            "weight_pct": 100,
        },
        {
            "row_key": "gate",
            "name": "Gate",
            "item_type": "milestone",
            "parent_ref": "p1",
            "sort_order": 1,
            "duration_days": 0,
            "target_date": "2026-12-15",
            "start_date": "2026-12-15",
        },
    ]
    infer_milestone_schedule_driver(rows[1])
    assert rows[1].get("schedule_driver") == "milestone"
    out = recalc_draft_dict_rows(None, rows, date(2026, 10, 1))
    gate = next(r for r in out if r["row_key"] == "gate")
    assert gate["target_date"] == "2026-12-15"
