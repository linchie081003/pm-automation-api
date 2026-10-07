"""Timeline Editor (beta): isolated workspace tables + optional seed from SPH draft."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import Project
from app.services.project_lifecycle import draft_timeline_editable
from app.services.timeline_schedule import compute_project_timeline_summary
from app.services.sph import get_or_create_sph
from app.services.timeline_editor_engine import (
    recalc_timeline_editor_payload,
    recalc_timeline_editor_rows,
)
from app.services.timeline_editor_store import (
    editor_has_rows,
    get_editor_start_date,
    list_editor_rows,
    save_editor_rows,
    seed_rows_from_draft,
)


def _parse_start(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def _effective_start(db: Session, project_id: int, sph) -> date | None:
    return get_editor_start_date(db, project_id) or _parse_start(sph.estimated_start_date)


def timeline_editor_snapshot(db: Session, project_id: int) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    sph = get_or_create_sph(db, project_id)

    if editor_has_rows(db, project_id):
        rows = list_editor_rows(db, project_id)
        start = get_editor_start_date(db, project_id) or _parse_start(sph.estimated_start_date)
        storage_source = "timeline_editor"
        note = (
            "Workspace beta tersimpan di tabel timeline_editor_* — terpisah dari draft SPH "
            "dan milestone live."
        )
    else:
        rows = seed_rows_from_draft(db, project_id)
        start = _parse_start(sph.estimated_start_date)
        storage_source = "draft_seed" if rows else "empty"
        note = (
            "Belum ada simpanan editor — tampilan dari salinan draft SPH (jika ada). "
            "Gunakan Simpan untuk menulis ke workspace beta."
        )

    return {
        "project_id": project_id,
        "start_date": start.isoformat() if start else None,
        "rows": rows,
        "draft_timeline_writable": True,
        "save_block_reason": None,
        "storage_source": storage_source,
        "read_only_source": storage_source,
        "sph_draft_writable": draft_timeline_editable(project, sph=sph),
        "note": note,
    }


def timeline_editor_save(
    db: Session,
    project_id: int,
    rows: list[dict],
    start_date: date | None,
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    sph = get_or_create_sph(db, project_id)
    eff = start_date or _effective_start(db, project_id, sph)
    if not eff:
        raise ValueError("Isi tanggal mulai proyek sebelum simpan.")

    recalced = recalc_timeline_editor_rows(db, rows, eff)
    for i, raw in enumerate(recalced):
        raw["sort_order"] = i

    save_editor_rows(db, project, recalced, eff)
    snap = timeline_editor_snapshot(db, project_id)
    snap["project_timeline"] = compute_project_timeline_summary(db, snap["rows"], eff)
    return snap


def timeline_editor_recalc(
    db: Session,
    project_id: int,
    rows: list[dict],
    start_date: date | None,
) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("Project not found")
    sph = get_or_create_sph(db, project_id)
    eff = start_date or _effective_start(db, project_id, sph)
    if not eff:
        raise ValueError("Isi tanggal mulai proyek (estimasi start) terlebih dahulu")
    payload = recalc_timeline_editor_payload(db, rows, eff)
    payload["project_id"] = project_id
    payload["start_date"] = eff.isoformat()
    return payload
