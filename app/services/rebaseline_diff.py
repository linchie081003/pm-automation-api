"""Rebaseline: diff baseline vs proposed phases, validation, apply on approve."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Milestone,
    MilestoneStatus,
    ScheduleBaselineMilestone,
    TimelineItemType,
)
from app.services.schedule import get_current_baseline

RebaselineCategory = Literal["delay", "scope_change"]

WEIGHT_TOTAL_TOLERANCE = 0.01


class ProposedPhaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    start_date: date | None = None
    target_date: date | None = None
    weight_pct: float = 0.0
    milestone_id: int | None = None
    client_key: str | None = None
    sort_order: int = 0
    notes: str | None = None
    predecessor_ref: str | None = None
    predecessor_link_type: str | None = None
    duration_days: int | None = None


class PhaseSnapshot(BaseModel):
    name: str
    start_date: date | None = None
    target_date: date | None = None
    weight_pct: float = 0.0
    milestone_id: int | None = None
    client_key: str | None = None
    sort_order: int = 0
    notes: str | None = None
    predecessor_ref: str | None = None
    predecessor_link_type: str | None = None
    duration_days: int | None = None

    def model_dump_jsonable(self) -> dict[str, Any]:
        d = self.model_dump()
        for k in ("start_date", "target_date"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _date_pair_changed(a: date | None, b: date | None) -> bool:
    return a != b


def _float_changed(a: float, b: float) -> bool:
    return round(float(a or 0), 4) != round(float(b or 0), 4)


def load_baseline_phases(db: Session, project_id: int) -> tuple[int | None, list[PhaseSnapshot]]:
    baseline = get_current_baseline(db, project_id)
    if not baseline:
        return None, []
    rows = list(
        db.scalars(
            select(ScheduleBaselineMilestone)
            .where(
                ScheduleBaselineMilestone.baseline_id == baseline.id,
                ScheduleBaselineMilestone.item_type == TimelineItemType.phase,
            )
            .order_by(ScheduleBaselineMilestone.sort_order, ScheduleBaselineMilestone.id)
        ).all()
    )
    phases = [
        PhaseSnapshot(
            name=r.name,
            start_date=r.start_date,
            target_date=r.target_date,
            weight_pct=float(r.weight_pct or 0),
            milestone_id=r.milestone_id,
            sort_order=r.sort_order or 0,
        )
        for r in rows
    ]
    return baseline.version, phases


def load_live_phase_seed(db: Session, project_id: int) -> list[PhaseSnapshot]:
    rows = list(
        db.scalars(
            select(Milestone)
            .where(
                Milestone.project_id == project_id,
                Milestone.item_type == TimelineItemType.phase,
            )
            .order_by(Milestone.sort_order, Milestone.id)
        ).all()
    )
    out: list[PhaseSnapshot] = []
    for r in rows:
        dur = r.duration_days
        if dur is None and r.start_date and r.target_date:
            dur = (r.target_date - r.start_date).days + 1
            dur = max(int(dur), 1)
        out.append(
            PhaseSnapshot(
                name=r.name,
                start_date=r.start_date,
                target_date=r.target_date,
                weight_pct=float(r.weight_pct or 0),
                milestone_id=r.id,
                sort_order=r.sort_order or 0,
                duration_days=dur,
            )
        )
    return out


def _phase_lifecycle_bucket(phase: Milestone, clickup_workflow: str) -> str:
    if phase.status == MilestoneStatus.done or clickup_workflow == "COMPLETED":
        return "closed"
    if clickup_workflow == "IN PROGRESS":
        return "in_progress"
    return "open"


def load_rebaseline_phase_guide(db: Session, project_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Live phase status for rebaseline UI (PDC + ClickUp workflow)."""
    from app.models import ClickUpTaskCache
    from app.services.progress import (
        build_clickup_lookups,
        clickup_status_mapping_context,
        phase_workflow_status,
    )

    milestones = list(
        db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    )
    caches = list(
        db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
        ).all()
    )
    phases = [m for m in milestones if m.item_type == TimelineItemType.phase]
    rows: list[dict[str, Any]] = []
    with clickup_status_mapping_context(db):
        tasks_by_id, milestone_cache = build_clickup_lookups(milestones, caches)
        for p in sorted(phases, key=lambda x: (x.sort_order or 0, x.id or 0)):
            wf = phase_workflow_status(
                p, milestones, tasks_by_id, milestone_cache, caches=caches
            )
            lifecycle = _phase_lifecycle_bucket(p, wf)
            can_delete = lifecycle == "open"
            can_edit_weight = p.status != MilestoneStatus.done
            rows.append(
                {
                    "milestone_id": p.id,
                    "name": p.name,
                    "weight_pct": float(p.weight_pct or 0),
                    "pdc_status": p.status.value,
                    "clickup_workflow": wf,
                    "lifecycle": lifecycle,
                    "can_delete": can_delete,
                    "can_edit_weight": can_edit_weight,
                }
            )

    adjustable = [
        {
            "milestone_id": r["milestone_id"],
            "name": r["name"],
            "weight_pct": r["weight_pct"],
            "lifecycle": r["lifecycle"],
        }
        for r in rows
        if r["can_edit_weight"]
    ]
    summary = {
        "open_phase_ids": [r["milestone_id"] for r in rows if r["lifecycle"] == "open"],
        "in_progress_phase_ids": [
            r["milestone_id"] for r in rows if r["lifecycle"] == "in_progress"
        ],
        "closed_phase_ids": [r["milestone_id"] for r in rows if r["lifecycle"] == "closed"],
        "adjustable_weights": adjustable,
    }
    return rows, summary


def live_phase_lifecycle_by_id(db: Session, project_id: int) -> dict[int, str]:
    guide, _ = load_rebaseline_phase_guide(db, project_id)
    return {int(r["milestone_id"]): str(r["lifecycle"]) for r in guide if r.get("milestone_id")}


def normalize_proposed_phases(raw: list[ProposedPhaseIn]) -> list[PhaseSnapshot]:
    out: list[PhaseSnapshot] = []
    for i, p in enumerate(raw):
        out.append(
            PhaseSnapshot(
                name=p.name.strip(),
                start_date=p.start_date,
                target_date=p.target_date,
                weight_pct=float(p.weight_pct or 0),
                milestone_id=p.milestone_id,
                client_key=p.client_key,
                sort_order=p.sort_order if p.sort_order else i,
                notes=(p.notes or "").strip() or None,
                predecessor_ref=(p.predecessor_ref or "").strip() or None,
                predecessor_link_type=(p.predecessor_link_type or "FS").strip().upper()
                if (p.predecessor_ref or "").strip()
                else None,
                duration_days=p.duration_days,
            )
        )
    return out


def _baseline_by_milestone_id(baseline: list[PhaseSnapshot]) -> dict[int, PhaseSnapshot]:
    return {p.milestone_id: p for p in baseline if p.milestone_id is not None}


def _proposed_by_milestone_id(proposed: list[PhaseSnapshot]) -> dict[int, PhaseSnapshot]:
    return {p.milestone_id: p for p in proposed if p.milestone_id is not None}


def compute_phase_diff(
    baseline: list[PhaseSnapshot], proposed: list[PhaseSnapshot]
) -> dict[str, Any]:
    base_ids = {p.milestone_id for p in baseline if p.milestone_id is not None}
    prop_ids = {p.milestone_id for p in proposed if p.milestone_id is not None}
    base_map = _baseline_by_milestone_id(baseline)
    prop_map = _proposed_by_milestone_id(proposed)

    added: list[dict[str, Any]] = []
    for p in proposed:
        if p.milestone_id is None or p.milestone_id not in base_ids:
            added.append(p.model_dump_jsonable())

    removed: list[dict[str, Any]] = []
    for p in baseline:
        if p.milestone_id is not None and p.milestone_id not in prop_ids:
            removed.append(p.model_dump_jsonable())

    modified: list[dict[str, Any]] = []
    for mid in sorted(base_ids & prop_ids):
        b, pr = base_map[mid], prop_map[mid]
        entry: dict[str, Any] = {"milestone_id": mid, "name": pr.name}
        changed = False
        if _date_pair_changed(b.start_date, pr.start_date):
            entry["start_date"] = {"from": _iso(b.start_date), "to": _iso(pr.start_date)}
            changed = True
        if _date_pair_changed(b.target_date, pr.target_date):
            entry["target_date"] = {"from": _iso(b.target_date), "to": _iso(pr.target_date)}
            changed = True
        if _float_changed(b.weight_pct, pr.weight_pct):
            entry["weight_pct"] = {"from": b.weight_pct, "to": pr.weight_pct}
            changed = True
        if b.name.strip() != pr.name.strip():
            entry["name_change"] = {"from": b.name, "to": pr.name}
            changed = True
        if changed:
            modified.append(entry)

    return {"added": added, "removed": removed, "modified": modified}


def build_presentation(category: RebaselineCategory, diff: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    has_structure = bool(diff.get("added") or diff.get("removed"))
    has_weight = any("weight_pct" in m for m in diff.get("modified", []))
    has_dates = any(
        "target_date" in m or "start_date" in m for m in diff.get("modified", [])
    )

    if category == "delay":
        primary = ["dates"]
        if has_structure or has_weight:
            warnings.append(
                "Perubahan ini terlihat seperti perubahan scope, bukan keterlambatan murni."
            )
    else:
        primary = ["structure_weights"]
        if has_dates and diff.get("modified"):
            warnings.append(
                "Pergeseran tanggal fase lain dapat menjadi efek wajar redistribusi scope."
            )

    return {"primary_sections": primary, "cross_category_warnings": warnings}


def validate_rebaseline_proposal(
    db: Session,
    project_id: int,
    baseline: list[PhaseSnapshot],
    proposed: list[PhaseSnapshot],
    diff: dict[str, Any],
    *,
    category: RebaselineCategory | None = None,
) -> dict[str, list[str]]:
    blocking: list[str] = []
    warnings: list[str] = []

    total = sum(p.weight_pct for p in proposed)
    if abs(total - 100.0) > WEIGHT_TOTAL_TOLERANCE:
        blocking.append(f"Total bobot fase harus 100% (saat ini {total:.2f}%).")

    live_phases = {
        m.id: m
        for m in db.scalars(
            select(Milestone).where(
                Milestone.project_id == project_id,
                Milestone.item_type == TimelineItemType.phase,
            )
        ).all()
    }
    lifecycle_by_id = live_phase_lifecycle_by_id(db, project_id)
    base_map = _baseline_by_milestone_id(baseline)
    prop_map = _proposed_by_milestone_id(proposed)

    if category == "scope_change" and not diff.get("added"):
        blocking.append(
            "Perubahan scope wajib memuat minimal satu fase baru dalam usulan timeline."
        )

    for mid, live in live_phases.items():
        lifecycle = lifecycle_by_id.get(mid, "open")
        if mid in prop_map and mid in base_map:
            if live.status == MilestoneStatus.done and _float_changed(
                base_map[mid].weight_pct, prop_map[mid].weight_pct
            ):
                blocking.append(
                    f"Bobot fase selesai terkunci (EVM): «{live.name}» tidak boleh diubah."
                )
            if lifecycle == "in_progress" and _float_changed(
                base_map[mid].weight_pct, prop_map[mid].weight_pct
            ):
                warnings.append(
                    f"Fase «{live.name}» sedang in progress di ClickUp — pertimbangkan sync setelah rebaseline."
                )
        for rem in diff.get("removed", []):
            if rem.get("milestone_id") != mid:
                continue
            if live.status == MilestoneStatus.done or lifecycle in ("closed", "in_progress"):
                label = "selesai" if lifecycle == "closed" else "in progress / selesai"
                blocking.append(
                    f"Fase «{live.name}» ({label}) tidak boleh dihapus dari usulan."
                )

    # Weight increases must be offset by decreases on open phases (live status)
    increases = 0.0
    decreases = 0.0
    for mid in base_map.keys() & prop_map.keys():
        live = live_phases.get(mid)
        if live and live.status == MilestoneStatus.done:
            continue
        b_w = base_map[mid].weight_pct
        p_w = prop_map[mid].weight_pct
        delta = p_w - b_w
        if delta > WEIGHT_TOTAL_TOLERANCE:
            increases += delta
        elif delta < -WEIGHT_TOTAL_TOLERANCE:
            decreases += -delta
    for add in diff.get("added", []):
        increases += float(add.get("weight_pct") or 0)

    if increases > WEIGHT_TOTAL_TOLERANCE and decreases + WEIGHT_TOTAL_TOLERANCE < increases:
        blocking.append(
            "Redistribusi bobot: penambahan bobot harus diambil dari penurunan fase yang masih open."
        )

    if not proposed and baseline:
        blocking.append("Usulan fase tidak boleh kosong jika baseline memiliki fase.")

    return {"blocking_errors": blocking, "warnings": warnings}


def build_proposed_changes_payload(
    db: Session,
    project_id: int,
    category: RebaselineCategory,
    effective_from: date,
    proposed: list[PhaseSnapshot],
) -> dict[str, Any]:
    baseline_version, baseline = load_baseline_phases(db, project_id)
    diff = compute_phase_diff(baseline, proposed)
    presentation = build_presentation(category, diff)
    validation = validate_rebaseline_proposal(
        db, project_id, baseline, proposed, diff, category=category
    )
    _, phase_summary = load_rebaseline_phase_guide(db, project_id)
    return {
        "category": category,
        "effective_from": effective_from.isoformat(),
        "baseline_version": baseline_version,
        "proposed_phases": [p.model_dump_jsonable() for p in proposed],
        "diff": diff,
        "presentation": presentation,
        "validation": validation,
        "phase_summary": phase_summary,
        "weight_total_pct": round(sum(p.weight_pct for p in proposed), 4),
    }


def _phase_ref(p: PhaseSnapshot, index: int) -> str:
    if p.milestone_id is not None:
        return f"m:{p.milestone_id}"
    if p.client_key:
        return f"c:{p.client_key}"
    return f"i:{index}"


def recalc_proposed_phases_dates(
    db: Session,
    proposed: list[PhaseSnapshot],
    *,
    project_start: date | None = None,
    lifecycle_by_id: dict[int, str] | None = None,
) -> list[PhaseSnapshot]:
    """Predecessor links FS/SS/FF/SF using hari kerja + libur (draft timeline engine)."""
    from app.services.business_calendar import (
        add_business_days,
        business_day_after,
        count_business_days_inclusive,
    )
    from app.services.schedule_dependency import (
        link_type_requires_pred_end,
        link_type_requires_pred_start,
        parse_predecessor_link_type,
        resolve_successor_span,
    )

    lifecycle_by_id = lifecycle_by_id or {}
    sorted_p = sorted(proposed, key=lambda x: (x.sort_order, x.milestone_id or 0))
    phase_end_by_ref: dict[str, date] = {}
    phase_start_by_ref: dict[str, date] = {}
    computed_by_ref: dict[str, PhaseSnapshot] = {}
    cursor: date | None = project_start

    for i, p in enumerate(sorted_p):
        ref = _phase_ref(p, i)
        if p.milestone_id and lifecycle_by_id.get(p.milestone_id) == "closed":
            computed_by_ref[ref] = p
            if p.start_date:
                phase_start_by_ref[ref] = p.start_date
            if p.target_date:
                phase_end_by_ref[ref] = p.target_date
                cursor = business_day_after(p.target_date, db)

    pending: list[tuple[int, PhaseSnapshot]] = []
    for i, p in enumerate(sorted_p):
        ref = _phase_ref(p, i)
        if ref in computed_by_ref:
            continue
        pending.append((i, p))

    guard = 0
    while pending and guard < len(pending) * 3 + 5:
        guard += 1
        i, p = pending[0]
        ref = _phase_ref(p, i)
        pred = (p.predecessor_ref or "").strip()
        link = parse_predecessor_link_type(p.predecessor_link_type)
        if pred:
            if link_type_requires_pred_end(link) and pred not in phase_end_by_ref:
                pending.append(pending.pop(0))
                continue
            if link_type_requires_pred_start(link) and pred not in phase_start_by_ref:
                pending.append(pending.pop(0))
                continue

        dur = p.duration_days
        if (not dur or dur <= 0) and p.start_date and p.target_date:
            dur = count_business_days_inclusive(p.start_date, p.target_date, db)
        dur = max(int(dur or 1), 1)

        start: date | None = None
        target: date | None = None
        if pred:
            span = resolve_successor_span(
                link,
                phase_start_by_ref.get(pred),
                phase_end_by_ref.get(pred),
                dur,
                db,
            )
            if span[0] and span[1]:
                start, target = span
        if start is None:
            if cursor is not None:
                start = cursor
            elif project_start:
                start = project_start
            else:
                start = p.start_date or date.today()
            target = add_business_days(start, dur, db)

        snap = PhaseSnapshot(
            name=p.name,
            start_date=start,
            target_date=target,
            weight_pct=p.weight_pct,
            milestone_id=p.milestone_id,
            client_key=p.client_key,
            sort_order=p.sort_order,
            notes=p.notes,
            predecessor_ref=p.predecessor_ref,
            predecessor_link_type=p.predecessor_link_type,
            duration_days=dur,
        )
        computed_by_ref[ref] = snap
        if start:
            phase_start_by_ref[ref] = start
        if target:
            phase_end_by_ref[ref] = target
            cursor = business_day_after(target, db)
        pending.pop(0)

    for i, p in pending:
        ref = _phase_ref(p, i)
        if ref in computed_by_ref:
            continue
        dur = max(int(p.duration_days or 1), 1)
        start = p.start_date or cursor or project_start or date.today()
        target = add_business_days(start, dur, db)
        computed_by_ref[ref] = PhaseSnapshot(
            name=p.name,
            start_date=start,
            target_date=target,
            weight_pct=p.weight_pct,
            milestone_id=p.milestone_id,
            client_key=p.client_key,
            sort_order=p.sort_order,
            notes=p.notes,
            predecessor_ref=p.predecessor_ref,
            predecessor_link_type=p.predecessor_link_type,
            duration_days=dur,
        )

    by_mid = {
        p.milestone_id: p
        for p in computed_by_ref.values()
        if p.milestone_id is not None
    }
    by_ck = {p.client_key: p for p in computed_by_ref.values() if p.client_key}
    out: list[PhaseSnapshot] = []
    for p in proposed:
        hit = None
        if p.milestone_id is not None:
            hit = by_mid.get(p.milestone_id)
        if not hit and p.client_key:
            hit = by_ck.get(p.client_key)
        out.append(hit if hit else p)
    return out


def apply_proposed_phases_to_live(
    db: Session, project_id: int, proposed_phases: list[dict[str, Any]]
) -> None:
    """Apply phase-level proposal to live milestones (children unchanged in v1)."""
    existing = list(
        db.scalars(
            select(Milestone).where(
                Milestone.project_id == project_id,
                Milestone.item_type == TimelineItemType.phase,
            )
        ).all()
    )
    by_id = {m.id: m for m in existing}
    proposed_ids = {
        p["milestone_id"] for p in proposed_phases if p.get("milestone_id") is not None
    }

    for p in proposed_phases:
        mid = p.get("milestone_id")
        start_s = p.get("start_date")
        target_s = p.get("target_date")
        start_d = date.fromisoformat(start_s[:10]) if start_s else None
        target_d = date.fromisoformat(target_s[:10]) if target_s else None
        if mid and mid in by_id:
            m = by_id[mid]
            m.name = p["name"]
            m.start_date = start_d
            m.target_date = target_d
            m.weight_pct = float(p.get("weight_pct") or 0)
            m.sort_order = int(p.get("sort_order") or 0)
        else:
            m = Milestone(
                project_id=project_id,
                name=p["name"],
                start_date=start_d,
                target_date=target_d,
                weight_pct=float(p.get("weight_pct") or 0),
                item_type=TimelineItemType.phase,
                sort_order=int(p.get("sort_order") or 0),
                status=MilestoneStatus.open,
            )
            db.add(m)
            db.flush()

    for m in existing:
        if m.id not in proposed_ids:
            db.delete(m)
