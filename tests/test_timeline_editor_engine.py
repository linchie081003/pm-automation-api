from datetime import date

from app.services.timeline_editor_engine import recalc_timeline_editor_rows


def test_fan_out_one_predecessor_many_successors(monkeypatch):
    def fake_after(d, db=None):
        return date(2026, 10, 6)

    def fake_add(start, days, db=None):
        if days == 1:
            return start
        return date(2026, 10, 8)

    monkeypatch.setattr(
        "app.services.business_calendar.business_day_after",
        fake_after,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.add_business_days",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.count_business_days_inclusive",
        lambda s, e, db=None: 2,
    )
    monkeypatch.setattr(
        "app.services.business_calendar.subtract_business_days",
        lambda end, days, db=None: date(2026, 10, 6),
    )

    rows = [
        {
            "row_key": "p1",
            "name": "Phase",
            "item_type": "phase",
            "parent_ref": None,
            "sort_order": 0,
            "duration_days": 5,
        },
        {
            "row_key": "t1",
            "name": "Task 1",
            "item_type": "task",
            "parent_ref": "p1",
            "sort_order": 1,
            "duration_days": 2,
        },
        {
            "row_key": "t2",
            "name": "Task 2",
            "item_type": "task",
            "parent_ref": "p1",
            "sort_order": 2,
            "duration_days": 2,
            "predecessors": [{"predecessor_ref": "t1", "link_type": "FS", "lag_days": 0}],
        },
        {
            "row_key": "t3",
            "name": "Task 3",
            "item_type": "task",
            "parent_ref": "p1",
            "sort_order": 3,
            "duration_days": 2,
            "predecessors": [{"predecessor_ref": "t1", "link_type": "FS", "lag_days": 0}],
        },
    ]
    out = recalc_timeline_editor_rows(None, rows, date(2026, 10, 1))
    by_key = {r["row_key"]: r for r in out}
    assert by_key["t1"]["start_date"]
    assert by_key["t2"]["start_date"]
    assert by_key["t3"]["start_date"]


def test_fan_in_multi_predecessor(monkeypatch):
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
            "item_type": "task",
            "sort_order": 0,
            "duration_days": 2,
            "start_date": "2026-10-01",
            "target_date": "2026-10-02",
        },
        {
            "row_key": "b",
            "item_type": "task",
            "sort_order": 1,
            "duration_days": 2,
            "start_date": "2026-10-05",
            "target_date": "2026-10-06",
        },
        {
            "row_key": "c",
            "item_type": "task",
            "sort_order": 2,
            "duration_days": 2,
            "predecessors": [
                {"predecessor_ref": "a", "link_type": "FS", "lag_days": 0},
                {"predecessor_ref": "b", "link_type": "FS", "lag_days": 0},
            ],
        },
    ]
    out = recalc_timeline_editor_rows(None, rows, date(2026, 10, 1))
    c = next(r for r in out if r["row_key"] == "c")
    assert c["start_date"] >= "2026-10-06"
