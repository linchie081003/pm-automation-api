from datetime import date

from app.services.timeline_schedule import compute_project_timeline_summary


def test_compute_project_timeline_summary(monkeypatch):
    monkeypatch.setattr(
        "app.services.business_calendar.count_business_days_inclusive",
        lambda s, e, db=None: 10,
    )
    rows = [
        {
            "item_type": "phase",
            "start_date": "2026-10-01",
            "target_date": "2026-10-15",
        },
        {
            "item_type": "task",
            "start_date": "2026-10-02",
            "target_date": "2026-10-20",
        },
    ]
    out = compute_project_timeline_summary(
        None,
        rows,
        date(2026, 10, 1),
    )
    assert out["project_end_date"] == "2026-10-20"
    assert out["project_duration_business_days"] == 10
