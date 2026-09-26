from datetime import date

from app.services.weekly_report_insights import build_highlights_draft


def test_build_highlights_includes_deviation_and_gap():
    text = build_highlights_draft(
        planned_pct=14.0,
        actual_pct=10.83,
        deviation_pct=-3.17,
        gap_pp=3.17,
        rag_gap="yellow",
        phase_gaps=[
            {
                "name": "Phase A",
                "planned_pct": 50.0,
                "actual_pct": 20.0,
                "gap_pp": 30.0,
            }
        ],
        tasks_done=[{"name": "Task 1", "status": "done", "due_date": "2026-09-25"}],
        tasks_next=[{"name": "Task 2", "status": "open", "due_date": "2026-10-02"}],
        phases_current=[],
        phases_next=[],
        next_period_label="minggu depan",
    )
    assert "Deviasi -3.17%" in text
    assert "YELLOW" in text
    assert "Phase A" in text
    assert "Task 1" in text
    assert "Task 2" in text
