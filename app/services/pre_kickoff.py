from datetime import datetime

from sqlalchemy.orm import Session

from app.models import PreKickoffPack, Project, ProjectSph
from app.services.sph import sync_sph_text_from_items
from app.core.timezone import now_jakarta


def sync_pack_from_sph(db: Session, project: Project) -> PreKickoffPack:
    sph = db.get(ProjectSph, project.id)
    pack = db.get(PreKickoffPack, project.id)
    if not pack:
        pack = PreKickoffPack(project_id=project.id)
        db.add(pack)
    if sph:
        sync_sph_text_from_items(sph)
        if sph.scope_text:
            pack.scope = sph.scope_text
        if sph.non_scope_text:
            pack.non_scope = sph.non_scope_text
        if not pack.background:
            sph_label = sph.sph_name or sph.sph_no or project.code
            pack.background = (
                f"Proyek {project.name} ({sph_label}) untuk klien {project.client_name}. "
                f"Metode delivery: {sph.delivery_method or '-'}. "
                f"Sales PIC: {sph.sales_pic or '-'}."
            )
        if not pack.timeline_summary and sph.target_delivery_days:
            start = sph.estimated_start_date or project.po_date
            pack.timeline_summary = (
                f"Target delivery: {sph.target_delivery_days} hari kalender. "
                f"Estimasi mulai: {start or '-'}."
            )
        if not pack.deliverables_items and sph.delivery_items:
            pack.deliverables_items = [
                {"id": d.get("id", ""), "text": d.get("name", "")}
                for d in sph.delivery_items
                if isinstance(d, dict) and d.get("name")
            ]
            lines = [x["text"] for x in pack.deliverables_items if x.get("text")]
            if lines:
                pack.deliverables = "\n".join(f"- {x}" for x in lines)
    pack.updated_at = now_jakarta()
    db.flush()
    return pack


def pack_from_sph(db: Session, project_id: int) -> PreKickoffPack | None:
    return db.get(PreKickoffPack, project_id)


def check_pack_complete(pack: PreKickoffPack | None) -> bool:
    if not pack:
        return False
    deliverables_ok = bool(pack.deliverables_items) or bool(
        pack.deliverables and str(pack.deliverables).strip()
    )
    org_ok = bool(pack.org_vendor and str(pack.org_vendor).strip()) and bool(
        pack.org_client and str(pack.org_client).strip()
    )
    required = [
        pack.background,
        pack.scope,
        pack.non_scope,
        pack.timeline_summary,
        org_ok,
        deliverables_ok,
        pack.next_activities,
    ]
    return all(bool(x and str(x).strip()) if isinstance(x, str) else bool(x) for x in required)
