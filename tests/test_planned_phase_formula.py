from datetime import date
from types import SimpleNamespace

from app.models import TimelineItemType
from app.services.schedule import planned_cumulative_from_phases, planned_pct_as_of


def _phase(weight, start, end):
    return SimpleNamespace(
        item_type=TimelineItemType.phase,
        weight_pct=weight,
        start_date=start,
        target_date=end,
        duration_days=None,
    )


def test_planned_cumulative_two_phases_midpoint():
    # Mon–Fri x2 weeks each phase (simplified: use calendar via db=None weekdays only)
    rows = [
        _phase(50, date(2026, 3, 2), date(2026, 3, 6)),  # 5 bd
        _phase(50, date(2026, 3, 9), date(2026, 3, 13)),
    ]
    # Mid first phase: Mar 4 Wed -> partial phase1, phase2 not started
    pct = planned_cumulative_from_phases(rows, date(2026, 3, 4), db=None)
    assert 0 < pct < 50
    # After both phases complete
    assert planned_pct_as_of(rows, date(2026, 3, 20), db=None) == 100.0


def test_phase_progress_clamped():
    rows = [_phase(100, date(2026, 5, 4), date(2026, 5, 8))]
    before = planned_cumulative_from_phases(rows, date(2026, 5, 1), db=None)
    assert before == 0.0
    after = planned_cumulative_from_phases(rows, date(2026, 6, 1), db=None)
    assert after == 100.0


def test_planned_normalized_when_weights_sum_below_100():
    rows = [
        _phase(46.25, date(2026, 1, 5), date(2026, 1, 9)),
        _phase(46.25, date(2026, 1, 12), date(2026, 1, 16)),
    ]
    assert planned_cumulative_from_phases(rows, date(2026, 1, 20), db=None) == 100.0
