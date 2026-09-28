from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Milestone,
    Project,
    ProjectSph,
    ScheduleBaseline,
    ScheduleBaselineMilestone,
    TimelineItemType,
    TimelineTemplate,
)
from app.services.draft_timeline import list_draft_rows, recalc_draft_dates
from app.services.schedule import (
    copy_milestones_to_baseline,
    get_or_revive_sph_draft_baseline,
    next_baseline_version,
)


def _line_items(items: list | None) -> list[str]:
    if not items:
        return []
    lines: list[str] = []
    for it in items:
        if isinstance(it, dict) and str(it.get("text", "")).strip():
            lines.append(str(it["text"]).strip())
        elif isinstance(it, str) and it.strip():
            lines.append(it.strip())
    return lines


def sync_sph_text_from_items(sph: ProjectSph) -> None:
    scope_lines: list[str] = []
    for it in sph.scope_items or []:
        if not isinstance(it, dict) or not str(it.get("text", "")).strip():
            continue
        mod = str(it.get("module") or "").strip()
        txt = str(it["text"]).strip()
        scope_lines.append(f"[{mod}] {txt}" if mod else txt)
    non_scope_lines = _line_items(sph.non_scope_items)
    if scope_lines:
        sph.scope_text = "\n".join(f"- {x}" for x in scope_lines)
    if non_scope_lines:
        sph.non_scope_text = "\n".join(f"- {x}" for x in non_scope_lines)


def delivery_total_rupiah(sph: ProjectSph) -> float:
    total = 0.0
    for it in sph.delivery_items or []:
        if isinstance(it, dict) and it.get("amount_rupiah") is not None:
            total += float(it["amount_rupiah"])
    return round(total, 2)


def apply_sph_total_from_delivery(sph: ProjectSph) -> None:
    total = delivery_total_rupiah(sph)
    if total > 0:
        sph.sph_total_rupiah = total


def compute_payment_term_amounts(terms: list, sph_total: float | None) -> list:
    total = float(sph_total or 0)
    out: list = []
    for raw in terms:
        if not isinstance(raw, dict):
            continue
        term = dict(raw)
        pct = term.get("percent_pct")
        if pct is not None and total > 0:
            term["amount"] = round(total * float(pct) / 100.0, 2)
        out.append(term)
    return out


def sph_is_complete(sph: ProjectSph | None) -> bool:
    if not sph:
        return False
    sync_sph_text_from_items(sph)
    scope_ok = bool(_line_items(sph.scope_items)) or bool(sph.scope_text and sph.scope_text.strip())
    non_scope_ok = bool(_line_items(sph.non_scope_items)) or bool(
        sph.non_scope_text and sph.non_scope_text.strip()
    )
    delivery_ok = any(
        isinstance(d, dict) and str(d.get("name", "")).strip()
        for d in (sph.delivery_items or [])
    )
    return bool(
        sph.sph_no
        and str(sph.sph_no).strip()
        and sph.sph_name
        and str(sph.sph_name).strip()
        and sph.sales_pic
        and str(sph.sales_pic).strip()
        and sph.estimated_start_date
        and sph.target_delivery_days
        and scope_ok
        and non_scope_ok
        and delivery_ok
        and (sph.sph_total_rupiah or 0) > 0
        and sph.delivery_method
        and sph.pic_user_name
    )


def _item_parent_key(raw: dict) -> str | None:
    pk = raw.get("parent_key")
    if pk is None or pk == "":
        pk = raw.get("parent_ref")
    if pk is None or pk == "":
        return None
    return str(pk)


def _template_root_business_days(items: list) -> int:
    """Perkiraan total hari kerja = jumlah durasi phase root (sequential)."""
    total = 0
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if _item_parent_key(raw):
            continue
        it = str(raw.get("item_type") or "phase").lower()
        if it == "milestone":
            continue
        total += max(int(raw.get("duration_days") or 1), 1)
    return total


def _scale_template_items_to_target(items: list, target_business_days: int) -> list[dict]:
    """Skala durasi template (kecuali milestone) agar total phase root ≈ durasi target SPH."""
    if target_business_days <= 0:
        return [dict(x) for x in items if isinstance(x, dict)]
    baseline = _template_root_business_days(items)
    if baseline <= 0:
        return [dict(x) for x in items if isinstance(x, dict)]

    factor = target_business_days / baseline
    scaled: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        it = str(row.get("item_type") or "phase").lower()
        if it == "milestone":
            row["duration_days"] = 0
        else:
            old = max(int(row.get("duration_days") or 1), 1)
            row["duration_days"] = max(1, int(round(old * factor)))
        scaled.append(row)

    roots = [
        r
        for r in scaled
        if not _item_parent_key(r)
        and str(r.get("item_type") or "phase").lower() != "milestone"
    ]
    if roots:
        current = sum(max(int(r.get("duration_days") or 1), 1) for r in roots)
        diff = target_business_days - current
        if diff != 0:
            last = roots[-1]
            last["duration_days"] = max(1, int(last.get("duration_days") or 1) + diff)

    return scaled


def rescale_existing_draft_to_target(
    db: Session, project: Project, target_business_days: int
) -> bool:
    """Sesuaikan durasi baris draft yang ada ke target (tanpa generate ulang dari template)."""
    from app.services.draft_timeline import list_draft_rows, save_draft_rows

    if target_business_days <= 0:
        return False
    rows = list_draft_rows(db, project.id)
    if not rows:
        return False

    scale_input = [
        {
            "row_key": r.get("row_key"),
            "parent_key": r.get("parent_ref"),
            "item_type": r.get("item_type"),
            "duration_days": r.get("duration_days"),
        }
        for r in rows
    ]
    scaled = _scale_template_items_to_target(scale_input, target_business_days)
    dur_by_key = {
        str(x["row_key"]): int(x["duration_days"])
        for x in scaled
        if x.get("row_key") is not None
    }

    new_rows: list[dict] = []
    for r in rows:
        nr = dict(r)
        rk = str(r.get("row_key") or "")
        it = str(r.get("item_type") or "phase").lower()
        if it == "milestone":
            nr["duration_days"] = 0
        elif rk and rk in dur_by_key:
            nr["duration_days"] = dur_by_key[rk]
        new_rows.append(nr)

    sph = get_or_create_sph(db, project.id)
    start = sph.estimated_start_date
    save_draft_rows(db, project, new_rows, start)

    return True


def get_or_create_sph(db: Session, project_id: int) -> ProjectSph:
    sph = db.get(ProjectSph, project_id)
    if not sph:
        sph = ProjectSph(project_id=project_id)
        db.add(sph)
        db.flush()
    return sph


def sync_payment_terms_with_draft_timeline(db: Session, sph: ProjectSph) -> bool:
    """Refresh due_date + draft_milestone_id for terms mapped to draft timeline rows."""
    terms = sph.payment_terms or []
    if not terms:
        return False
    draft_rows = list_draft_rows(db, sph.project_id)
    if not draft_rows:
        return False
    by_id = {int(r["id"]): r for r in draft_rows if r.get("id") is not None}
    by_key = {str(r["row_key"]): r for r in draft_rows if r.get("row_key")}
    by_name: dict[str, dict] = {}
    for r in draft_rows:
        name = str(r.get("name") or "").strip().lower()
        if name:
            by_name[name] = r

    changed = False
    updated: list = []
    for raw in terms:
        if not isinstance(raw, dict):
            updated.append(raw)
            continue
        row = dict(raw)
        hit: dict | None = None
        rk = str(row.get("draft_milestone_row_key") or "").strip()
        if rk and rk in by_key:
            hit = by_key[rk]
        mid = row.get("draft_milestone_id")
        if hit is None and mid is not None:
            try:
                hit = by_id.get(int(mid))
            except (TypeError, ValueError):
                hit = None
        if hit is None:
            label = str(row.get("label") or "").strip().lower()
            if label:
                hit = by_name.get(label)
        if hit:
            new_id = hit.get("id")
            if new_id is not None and row.get("draft_milestone_id") != new_id:
                row["draft_milestone_id"] = new_id
                changed = True
            hit_key = hit.get("row_key")
            if hit_key and row.get("draft_milestone_row_key") != hit_key:
                row["draft_milestone_row_key"] = hit_key
                changed = True
            due = hit.get("target_date")
            if due:
                iso = str(due)[:10]
                if row.get("due_date") != iso:
                    row["due_date"] = iso
                    changed = True
            if not str(row.get("label") or "").strip() and hit.get("name"):
                row["label"] = hit["name"]
                changed = True
        updated.append(row)
    if changed:
        sph.payment_terms = updated
    return changed


def payment_terms_ready(sph: ProjectSph) -> bool:
    terms = sph.payment_terms or []
    if not terms:
        return False
    for t in terms:
        if not isinstance(t, dict):
            continue
        if str(t.get("label") or "").strip():
            return True
    return False


def _build_draft_baseline_from_template(
    db: Session,
    project: Project,
    sph: ProjectSph,
    user_id: int | None,
    start_date: date | None,
    timeline_template_id: int | None,
) -> ScheduleBaseline:
    from app.services.project_lifecycle import sph_timeline_editable
    from app.services.timeline_validation import validate_timeline_items

    if not sph_timeline_editable(project, sph=sph):
        raise ValueError("Timeline SPH read-only pada fase Kick Off ke atas")

    start = start_date or sph.estimated_start_date
    if not start:
        raise ValueError(
            "Isi estimasi mulai proyek di bagian Timeline (template & draft) sebelum generate draft"
        )
    sph.estimated_start_date = start

    if not project.kickoff_timeline_confirmed_at:
        db.execute(delete(Milestone).where(Milestone.project_id == project.id))

    tpl_id = timeline_template_id or sph.timeline_template_id
    template: TimelineTemplate | None = db.get(TimelineTemplate, tpl_id) if tpl_id else None
    if not template:
        template = db.scalar(
            select(TimelineTemplate).where(
                TimelineTemplate.methodology == project.methodology,
                TimelineTemplate.is_active.is_(True),
            )
        )
    if not template:
        raise ValueError("Template timeline belum dikonfigurasi untuk metodologi proyek")

    sph.timeline_template_id = template.id
    template_items = list(template.items or [])
    target_days = int(sph.target_delivery_days) if sph.target_delivery_days else 0
    if target_days > 0:
        template_items = _scale_template_items_to_target(template_items, target_days)

    tpl_validate: list[dict] = []
    for raw in template_items:
        if not isinstance(raw, dict):
            continue
        it = str(raw.get("item_type") or "phase")
        dur_raw = raw.get("duration_days")
        if it == "milestone":
            dur = 0
        elif dur_raw is not None:
            dur = int(dur_raw)
        else:
            dur = 1
        tpl_validate.append(
            {
                "row_key": str(raw.get("row_key") or raw.get("name") or ""),
                "name": raw.get("name"),
                "item_type": it,
                "weight_pct": raw.get("weight_pct") or 0,
                "parent_key": raw.get("parent_key"),
                "duration_days": dur,
            }
        )
    validate_timeline_items(tpl_validate)

    draft = get_or_revive_sph_draft_baseline(db, project.id)
    if draft:
        db.execute(
            delete(ScheduleBaselineMilestone).where(
                ScheduleBaselineMilestone.baseline_id == draft.id
            )
        )
        db.flush()
        draft.effective_from = start
        draft.reason = "Draft timeline from SPH (kick off)"
        draft.is_draft = True
        draft.is_current = False
        baseline = draft
    else:
        next_ver = next_baseline_version(db, project.id)
        baseline = ScheduleBaseline(
            project_id=project.id,
            version=next_ver,
            effective_from=start,
            reason="Draft timeline from SPH (kick off)",
            is_current=False,
            is_draft=True,
            created_by_id=user_id,
        )
        db.add(baseline)
        db.flush()

    key_to_id: dict[str, int] = {}
    for raw in sorted(template_items, key=lambda x: int(x.get("sort_order") or 0)):
        if not isinstance(raw, dict):
            continue
        parent_key = raw.get("parent_key")
        parent_id = key_to_id.get(str(parent_key)) if parent_key else None
        from app.services.timeline_item_type import parse_timeline_item_type

        it = parse_timeline_item_type(str(raw.get("item_type") or "phase"))
        if not parent_key and it == TimelineItemType.milestone:
            it = TimelineItemType.phase
        row_key = str(raw.get("row_key") or raw.get("name") or "")
        dur = int(raw.get("duration_days") or 1)
        if it == TimelineItemType.milestone:
            dur = 0
        row = ScheduleBaselineMilestone(
            baseline_id=baseline.id,
            milestone_id=None,
            row_key=row_key or None,
            name=str(raw.get("name") or "Item"),
            duration_days=dur,
            weight_pct=float(raw.get("weight_pct") or 0),
            item_type=it,
            parent_id=parent_id,
            sort_order=int(raw.get("sort_order") or 0),
        )
        db.add(row)
        db.flush()
        if row_key:
            key_to_id[row_key] = row.id

    recalc_draft_dates(db, project.id, start)

    last_end = start
    rows = db.scalars(
        select(ScheduleBaselineMilestone).where(
            ScheduleBaselineMilestone.baseline_id == baseline.id
        )
    ).all()
    for r in rows:
        if r.target_date and r.target_date > last_end:
            last_end = r.target_date
    project.planned_end_date = last_end

    return baseline


def generate_draft_timeline_from_template(
    db: Session,
    project: Project,
    user_id: int | None,
    start_date: date | None = None,
    timeline_template_id: int | None = None,
) -> ScheduleBaseline:
    """Isi draft timeline dari metodologi + template (tanpa wajib SPH lengkap)."""
    sph = get_or_create_sph(db, project.id)
    return _build_draft_baseline_from_template(
        db, project, sph, user_id, start_date, timeline_template_id
    )


def generate_draft_milestones_from_sph(
    db: Session,
    project: Project,
    user_id: int | None,
    start_date: date | None = None,
    timeline_template_id: int | None = None,
) -> ScheduleBaseline:
    return generate_draft_timeline_from_template(
        db, project, user_id, start_date, timeline_template_id
    )


def finalize_sph_timeline_for_kickoff(db: Session, project: Project) -> None:
    """Setelah SPH + draft + termin lengkap — siap untuk tab Kick Off."""
    from app.services.draft_timeline import list_draft_rows
    from app.services.pre_kickoff import sync_pack_from_sph

    sph = get_or_create_sph(db, project.id)
    if not sph_is_complete(sph):
        raise ValueError("Lengkapi semua informasi SPH sebelum generate timeline")
    if not payment_terms_ready(sph):
        raise ValueError("Simpan termin pembayaran (Term of payment) terlebih dahulu")
    if not list_draft_rows(db, project.id):
        raise ValueError("Generate draft timeline dari template terlebih dahulu")
    sph.draft_baseline_generated_at = datetime.utcnow()
    sync_pack_from_sph(db, project)
