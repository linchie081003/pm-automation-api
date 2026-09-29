from datetime import date
from unittest.mock import MagicMock

from app.models import Project, WeeklyReport
from app.services import weekly_report_pptx as mod


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
    monkeypatch.setattr(mod, "timeline_display_rows_for_project", lambda _db, _pid: [])
    monkeypatch.setattr(mod, "scurve_points", lambda _db, _pid, **kw: [])
    monkeypatch.setattr(
        mod,
        "project_report_start_date",
        lambda _db, _p: date(2026, 5, 1),
    )

    dest = tmp_path / "weekly.pptx"
    mod.build_weekly_report_pptx(dest, db=db, project=project, report=report)
    assert dest.is_file()
    assert dest.stat().st_size > 5000
