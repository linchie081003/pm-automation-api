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


def test_root_phase_uat_fs_after_development_rollup():
    """Development (phase + long task) → UAT phase FS: UAT must start after rolled-up Development end."""
    project_start = date(2026, 1, 5)  # Monday
    rows = [
        {
            "row_key": "ph_dev",
            "name": "Development",
            "item_type": "phase",
            "parent_ref": None,
            "sort_order": 0,
            "duration_days": 5,
        },
        {
            "row_key": "t_dev",
            "name": "Build",
            "item_type": "task",
            "parent_ref": "ph_dev",
            "sort_order": 1,
            "duration_days": 10,
        },
        {
            "row_key": "ph_uat",
            "name": "UAT",
            "item_type": "phase",
            "parent_ref": None,
            "sort_order": 2,
            "duration_days": 5,
            "predecessors": [
                {"predecessor_ref": "ph_dev", "link_type": "FS", "lag_days": 0},
            ],
        },
    ]
    out = recalc_timeline_editor_rows(None, rows, project_start)
    by_key = {r["row_key"]: r for r in out}
    dev_end = by_key["ph_dev"]["target_date"]
    uat_start = by_key["ph_uat"]["start_date"]
    assert dev_end >= by_key["t_dev"]["target_date"]
    assert uat_start > dev_end


def test_milestone_gate_uses_sibling_end_not_stale_target():
    """Milestone gate mengikuti akhir task sibling, bukan target_date sisa saat masih task."""
    project_start = date(2026, 11, 17)  # Monday
    rows = [
        {
            "row_key": "ph_doc",
            "name": "Documentation",
            "item_type": "phase",
            "parent_ref": None,
            "sort_order": 0,
            "duration_days": 10,
        },
        {
            "row_key": "t_line",
            "name": "Documentation Line",
            "item_type": "task",
            "parent_ref": "ph_doc",
            "sort_order": 1,
            "duration_days": 10,
        },
        {
            "row_key": "ms_doc",
            "name": "Documentation Milestone",
            "item_type": "milestone",
            "parent_ref": "ph_doc",
            "sort_order": 2,
            "target_date": "2026-11-19",
            "start_date": "2026-11-19",
        },
    ]
    out = recalc_timeline_editor_rows(None, rows, project_start)
    by_key = {r["row_key"]: r for r in out}
    task_end = by_key["t_line"]["target_date"]
    assert by_key["ms_doc"]["target_date"] == task_end
    assert by_key["ms_doc"]["start_date"] == task_end


def test_phase_duration_editable_when_only_milestone_child():
    """Phase dengan anak milestone saja: durasi phase tidak dikunci tanggal gate."""
    project_start = date(2026, 11, 1)
    rows = [
        {
            "row_key": "ph",
            "name": "Documentation",
            "item_type": "phase",
            "parent_ref": None,
            "sort_order": 0,
            "duration_days": 15,
            "schedule_driver": "duration",
        },
        {
            "row_key": "ms",
            "name": "Doc gate",
            "item_type": "milestone",
            "parent_ref": "ph",
            "sort_order": 1,
            "target_date": "2026-11-19",
            "start_date": "2026-11-19",
        },
    ]
    out = recalc_timeline_editor_rows(None, rows, project_start)
    by_key = {r["row_key"]: r for r in out}
    assert by_key["ph"]["target_date"] > "2026-11-19"
    assert by_key["ms"]["target_date"] == by_key["ph"]["target_date"]
