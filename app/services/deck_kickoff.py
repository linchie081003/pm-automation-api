import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, DocumentType, Milestone, PreKickoffPack, Project
from app.services.kickoff_timeline import list_draft_timeline
from app.services.pre_kickoff import sync_pack_from_sph
from app.services.templates.loader import copy_template
from app.services.templates.placeholders import build_mapping, replace_in_pptx


def build_kickoff_deck_path(db: Session, project: Project, preview: bool = False) -> Path:
    pack = db.get(PreKickoffPack, project.id)
    if not pack:
        sync_pack_from_sph(db, project)
        pack = db.get(PreKickoffPack, project.id)

    milestones = db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
    draft = list_draft_timeline(db, project.id)
    if milestones:
        ms_lines = "\n".join(
            f"- {m.name}: start {m.start_date or project.po_date} · due {m.target_date or '-'}"
            for m in milestones
        )
    elif draft:
        ms_lines = "\n".join(
            f"- {r.get('name')}: {r.get('start_date') or '?'} → {r.get('target_date') or '?'}"
            for r in draft
        )
    else:
        ms_lines = ""

    deliverables = ""
    if pack and pack.deliverables_items:
        deliverables = "\n".join(
            f"- {str(i.get('text', '')).strip()}"
            for i in pack.deliverables_items
            if isinstance(i, dict) and str(i.get("text", "")).strip()
        )

    mapping = build_mapping(
        PROJECT_NAME=project.name,
        PROJECT_CODE=project.code,
        CLIENT_NAME=project.client_name or "",
        PO_START=str(project.po_date or ""),
        PO_DUE=str(project.po_due_date or project.planned_end_date or ""),
        SCOPE=pack.scope if pack else "",
        NON_SCOPE=pack.non_scope if pack else "",
        BACKGROUND=pack.background if pack else "",
        ORG_VENDOR=pack.org_vendor if pack else "",
        ORG_CLIENT=pack.org_client if pack else "",
        ORG_STRUCTURE=pack.org_structure if pack else "",
        DELIVERABLES=deliverables or (pack.deliverables if pack else ""),
        NEXT_ACTIVITIES=pack.next_activities if pack else "",
        TIMELINE_SUMMARY=pack.timeline_summary if pack else "",
        MILESTONE_TABLE=ms_lines,
        DRAFT_TIMELINE=ms_lines,
    )
    sub = "previews" if preview else "projects"
    out_dir = Path(settings.upload_dir) / sub / str(project.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"kickoff_{uuid.uuid4().hex}.pptx"
    copy_template("kickoff_deck.pptx", dest)
    replace_in_pptx(dest, mapping)
    return dest


def generate_kickoff_deck(db: Session, project: Project, user_id: int) -> Document:
    from app.services.document_registry import register_file_as_document

    dest = build_kickoff_deck_path(db, project, preview=False)
    doc = register_file_as_document(
        db,
        project,
        user_id,
        dest,
        DocumentType.kickoff_deck,
        display_filename=f"Kickoff_{project.code}.pptx",
    )
    if not doc:
        raise FileNotFoundError("Failed to register kickoff deck")
    db.flush()
    return doc
