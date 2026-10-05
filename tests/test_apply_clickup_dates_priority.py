"""Prioritas tanggal: anak (rollup) sebelum raw ClickUp pada container."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models import TimelineItemType
from app.services.clickup import apply_clickup_dates_to_timeline


def test_apply_clickup_dates_phase_rollup_before_linked_cache():
    phase = SimpleNamespace(
        id=1,
        project_id=1,
        parent_id=None,
        item_type=TimelineItemType.phase,
        clickup_task_id="cu-phase",
        start_date=date(2026, 10, 1),
        target_date=date(2026, 10, 31),
        duration_days=20,
        sort_order=1,
    )
    gate = SimpleNamespace(
        id=2,
        project_id=1,
        parent_id=1,
        item_type=TimelineItemType.milestone,
        clickup_task_id="cu-gate",
        start_date=date(2026, 11, 20),
        target_date=date(2026, 11, 25),
        duration_days=0,
        sort_order=2,
    )
    cache_phase = SimpleNamespace(
        clickup_task_id="cu-phase",
        parent_task_id=None,
        due_date=date(2026, 9, 30),
        raw_json={"start_date": "1727654400000"},
    )

    db = MagicMock()

    def scalar_side(q):
        return None

    db.scalar.side_effect = scalar_side
    db.get.return_value = SimpleNamespace(kickoff_timeline_confirmed_at=None)
    scalars_results = [[phase, gate], [cache_phase], [phase, gate]]

    def all_side_effect():
        return scalars_results.pop(0) if scalars_results else []

    db.scalars.return_value.all.side_effect = all_side_effect

    with patch("app.services.progress.build_clickup_lookups", return_value=({}, {})):
        with patch(
            "app.services.milestone_schedule_rollup.rollup_live_milestone_dates",
            return_value=0,
        ):
            with patch("app.services.clickup._apply_date_span_to_milestone") as apply_span:
                apply_clickup_dates_to_timeline(db, 1)
                phase_calls = [c for c in apply_span.call_args_list if c[0][0] is phase]
                assert phase_calls, "phase should be updated from child rollup"
                start, end = phase_calls[-1][0][1], phase_calls[-1][0][2]
                assert start == date(2026, 11, 20)
                assert end == date(2026, 11, 25)
