from datetime import date

from app.services.rebaseline_diff import (
    PhaseSnapshot,
    build_presentation,
    compute_phase_diff,
    validate_rebaseline_proposal,
)


def test_compute_phase_diff_added_removed_modified():
    baseline = [
        PhaseSnapshot(
            name="A",
            target_date=date(2026, 1, 1),
            weight_pct=50,
            milestone_id=1,
        ),
        PhaseSnapshot(
            name="B",
            target_date=date(2026, 2, 1),
            weight_pct=50,
            milestone_id=2,
        ),
    ]
    proposed = [
        PhaseSnapshot(
            name="A",
            target_date=date(2026, 1, 15),
            weight_pct=50,
            milestone_id=1,
        ),
        PhaseSnapshot(
            name="C",
            target_date=date(2026, 3, 1),
            weight_pct=50,
            client_key="new-1",
        ),
    ]
    diff = compute_phase_diff(baseline, proposed)
    assert len(diff["removed"]) == 1
    assert diff["removed"][0]["milestone_id"] == 2
    assert len(diff["added"]) == 1
    assert diff["added"][0]["name"] == "C"
    assert len(diff["modified"]) == 1
    assert diff["modified"][0]["milestone_id"] == 1
    assert "target_date" in diff["modified"][0]


def test_presentation_delay_warns_on_scope_signals():
    diff = {
        "added": [{"name": "X"}],
        "removed": [],
        "modified": [{"milestone_id": 1, "weight_pct": {"from": 50, "to": 40}}],
    }
    pres = build_presentation("delay", diff)
    assert pres["primary_sections"] == ["dates"]
    assert any("scope" in w.lower() for w in pres["cross_category_warnings"])


def test_presentation_scope_notes_date_shifts():
    diff = {
        "added": [],
        "removed": [],
        "modified": [{"milestone_id": 1, "target_date": {"from": "2026-01-01", "to": "2026-02-01"}}],
    }
    pres = build_presentation("scope_change", diff)
    assert "structure_weights" in pres["primary_sections"]
    assert pres["cross_category_warnings"]


def test_validate_total_weight_and_done_lock(monkeypatch):
    baseline = [
        PhaseSnapshot(name="Done", weight_pct=40, milestone_id=10),
        PhaseSnapshot(name="Open", weight_pct=60, milestone_id=11),
    ]
    proposed = [
        PhaseSnapshot(name="Done", weight_pct=35, milestone_id=10),
        PhaseSnapshot(name="Open", weight_pct=65, milestone_id=11),
    ]
    diff = compute_phase_diff(baseline, proposed)

    class FakeScalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class FakeSession:
        def scalars(self, *_a, **_k):
            from app.models import MilestoneStatus

            return FakeScalars(
                [
                    type("M", (), {"id": 10, "name": "Done", "status": MilestoneStatus.done})(),
                    type("M", (), {"id": 11, "name": "Open", "status": MilestoneStatus.open})(),
                ]
            )

    monkeypatch.setattr(
        "app.services.rebaseline_diff.live_phase_lifecycle_by_id",
        lambda *_a, **_k: {10: "closed", 11: "open"},
    )
    v = validate_rebaseline_proposal(FakeSession(), 1, baseline, proposed, diff)
    assert any("terkunci" in e for e in v["blocking_errors"])

    proposed_ok = [
        PhaseSnapshot(name="Done", weight_pct=40, milestone_id=10),
        PhaseSnapshot(name="Open", weight_pct=60, milestone_id=11),
    ]
    diff_ok = compute_phase_diff(baseline, proposed_ok)
    v2 = validate_rebaseline_proposal(FakeSession(), 1, baseline, proposed_ok, diff_ok)
    assert not v2["blocking_errors"]


def test_scope_change_requires_new_phase(monkeypatch):
    baseline = [
        PhaseSnapshot(name="A", weight_pct=100, milestone_id=1),
    ]
    proposed = [
        PhaseSnapshot(name="A", weight_pct=100, milestone_id=1),
    ]
    diff = compute_phase_diff(baseline, proposed)

    monkeypatch.setattr(
        "app.services.rebaseline_diff.live_phase_lifecycle_by_id",
        lambda *_a, **_k: {1: "open"},
    )
    v = validate_rebaseline_proposal(
        __import__("unittest.mock").mock.MagicMock(),
        1,
        baseline,
        proposed,
        diff,
        category="scope_change",
    )
    assert any("fase baru" in e.lower() for e in v["blocking_errors"])


def test_cannot_remove_in_progress_phase(monkeypatch):
    baseline = [
        PhaseSnapshot(name="Run", weight_pct=100, milestone_id=5),
    ]
    proposed: list[PhaseSnapshot] = []
    diff = compute_phase_diff(baseline, proposed)

    class FakeSession:
        def scalars(self, *_a, **_k):
            from app.models import MilestoneStatus

            class R:
                def all(self):
                    return [
                        type(
                            "M",
                            (),
                            {"id": 5, "name": "Run", "status": MilestoneStatus.open},
                        )()
                    ]

            return R()

    monkeypatch.setattr(
        "app.services.rebaseline_diff.live_phase_lifecycle_by_id",
        lambda *_a, **_k: {5: "in_progress"},
    )
    v = validate_rebaseline_proposal(FakeSession(), 1, baseline, proposed, diff)
    assert any("tidak boleh dihapus" in e for e in v["blocking_errors"])
