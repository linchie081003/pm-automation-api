from datetime import date
from unittest.mock import MagicMock

from app.models import Project, WeeklyReport
from app.services import weekly_report_pptx as mod
from app.services.weekly_report_pptx import _actual_pct_for_report_export, _scurve_snapshot_rows


def test_actual_pct_masked_after_report_cut_off():
    cut = date(2026, 7, 9)
    pt_before = {"date": "2026-07-02", "actual_pct": 40.0}
    pt_after = {"date": "2026-07-16", "actual_pct": 55.0}
    assert _actual_pct_for_report_export(pt_before, cut) == 40.0
    assert _actual_pct_for_report_export(pt_after, cut) is None


def test_scurve_snapshot_skips_weeks_after_cut_off():
    cut = date(2026, 7, 9)
    points = [
        {"date": "2026-06-25", "planned_pct": 10, "actual_pct": 8},
        {"date": "2026-07-02", "planned_pct": 20, "actual_pct": 18},
        {"date": "2026-07-16", "planned_pct": 35, "actual_pct": 30},
    ]
    rows = _scurve_snapshot_rows(points, date(2026, 7, 9), cut)
    assert len(rows) == 2
    assert rows[-1]["_date"] == date(2026, 7, 2)


def test_build_weekly_report_pptx_smoke(tmp_path, monkeypatch):
    project = Project(
        id=1,
        code="TST-PPTX",
        name="Auto Bandwidth On Demand",
        client_name="Client",
        project_manager="PM Test",
        project_brief="Feature rollout for bandwidth automation.",
        owner_id=1,
        weekly_report_anchor_weekday=4,
        weekly_report_cutoff_offset_days=6,
    )
    report = WeeklyReport(
        project_id=1,
        week_start=date(2026, 7, 9),
        week_end=date(2026, 7, 9),
        baseline_version=1,
        summary={"highlights": "1. Requirement Done\n2. Development In Progress"},
        frozen_metrics={"planned_pct": 75.0, "actual_pct": 74.0, "spi": 0.98},
        generated_by_id=1,
    )
    db = MagicMock()
    db.get.return_value = None
    monkeypatch.setattr(
        mod,
        "timeline_display_rows_for_project",
        lambda _db, _pid, **_: [],
    )
    monkeypatch.setattr(mod, "scurve_points", lambda _db, _pid, **kw: [])
    monkeypatch.setattr(
        mod,
        "project_report_start_date",
        lambda _db, _p: date(2026, 5, 1),
    )
    monkeypatch.setattr(
        mod,
        "_compute_auto_highlights",
        lambda *a, **k: "GAP timeline phase:\n• Phase A\n\nTask selesai minggu ini:\n• T1",
    )

    dest = tmp_path / "weekly.pptx"
    mod.build_weekly_report_pptx(dest, db=db, project=project, report=report)
    assert dest.is_file()
    assert dest.stat().st_size > 5000
