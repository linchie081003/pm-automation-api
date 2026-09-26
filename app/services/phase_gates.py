"""Read-only phase gate checklist for UI."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PreKickoffPack,
    ProgressSnapshot,
    ProgressSnapshotSource,
    Project,
    ProjectPhase,
    ProjectSph,
)
from app.services.pre_kickoff import check_pack_complete
from app.services.progress import resolve_actual_progress
from app.services.workflow import next_phase, validate_phase_transition


def phase_gate_status(db: Session, project: Project) -> dict:
    target = next_phase(project.current_phase)
    items: list[dict] = []
    can_advance = True
    if not target:
        return {
            "current_phase": project.current_phase.value,
            "next_phase": None,
            "can_advance": False,
            "items": [{"id": "done", "label": "Proyek sudah fase akhir", "ok": True}],
        }

    if target == ProjectPhase.kickoff:
        from app.services.draft_timeline import list_draft_rows

        sph = db.get(ProjectSph, project.id)
        ok_done = bool(sph and sph.draft_baseline_generated_at)
        items.append(
            {
                "id": "sph_complete",
                "label": "SPH selesai (Lanjut ke Kick Off dari tab SPH)",
                "ok": ok_done,
            }
        )
        ok_draft = bool(list_draft_rows(db, project.id))
        items.append({"id": "sph_draft", "label": "Draft timeline SPH", "ok": ok_draft})
        if not ok_done or not ok_draft:
            can_advance = False

    if target == ProjectPhase.in_delivery:
        sph = db.get(ProjectSph, project.id)
        ok_gen = bool(sph and sph.draft_baseline_generated_at)
        items.append(
            {"id": "sph_timeline", "label": "Generate timeline SPH (lengkap + termin)", "ok": ok_gen}
        )
        pack = db.get(PreKickoffPack, project.id)
        ok_pack = check_pack_complete(pack)
        items.append({"id": "kickoff_pack", "label": "Materi kick off lengkap", "ok": ok_pack})
        ok = bool(project.kickoff_timeline_confirmed_at)
        items.append({"id": "timeline_ko", "label": "Timeline kick off dikonfirmasi", "ok": ok})
        if not ok_gen or not ok_pack:
            can_advance = False
        if project.clickup_enabled:
            ok_cu = project.clickup_provision_status == "provisioned"
            items.append(
                {
                    "id": "clickup",
                    "label": "ClickUp aktif (struktur otomatis saat Project Start)",
                    "ok": True,
                }
            )
        if not ok:
            can_advance = False

    if target == ProjectPhase.closed:
        from app.services.po_validation import po_complete_for_closing

        ok_po = po_complete_for_closing(db, project.id)
        items.append({"id": "po_closing", "label": "Data PO lengkap (BAST / Closing)", "ok": ok_po})
        if not ok_po:
            can_advance = False
        progress = resolve_actual_progress(db, project, datetime.utcnow().date())
        ok_prog = progress >= 100
        items.append({"id": "progress_100_closing", "label": "Progress Proyek 100%", "ok": ok_prog})
        if not ok_prog:
            can_advance = False

    if target == ProjectPhase.bast:
        from app.services.po_validation import po_complete_for_closing

        ok_po = po_complete_for_closing(db, project.id)
        items.append({"id": "po_bast", "label": "Data PO lengkap (BAST)", "ok": ok_po})
        if not ok_po:
            can_advance = False
        progress = resolve_actual_progress(db, project, datetime.utcnow().date())
        ok_prog = progress >= 100
        items.append({"id": "progress_100_bast", "label": "Progress Proyek 100%", "ok": ok_prog})
        if not ok_prog:
            can_advance = False
        snap = db.scalar(
            select(ProgressSnapshot)
            .where(
                ProgressSnapshot.project_id == project.id,
                ProgressSnapshot.source == ProgressSnapshotSource.weekly_report,
            )
            .limit(1)
        )
        ok = snap is not None
        items.append({"id": "weekly", "label": "Minimal 1 weekly report", "ok": ok})
        if not ok:
            can_advance = False

    try:
        validate_phase_transition(db, project, target)
    except ValueError as e:
        can_advance = False
        if not items:
            items.append({"id": "rule", "label": str(e), "ok": False})

    return {
        "current_phase": project.current_phase.value,
        "next_phase": target.value,
        "can_advance": can_advance,
        "items": items,
    }
