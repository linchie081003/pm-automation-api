import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, DocumentType, Milestone, PreKickoffPack, Project
from app.services.pre_kickoff import pack_from_sph, sync_pack_from_sph
from app.services.templates.loader import copy_template
from app.services.templates.placeholders import build_mapping, replace_in_pptx


def build_pre_kickoff_deck_path(db: Session, project: Project, preview: bool = False) -> Path:
    pack = db.get(PreKickoffPack, project.id)
    if not pack:
        sync_pack_from_sph(db, project)
        pack = db.get(PreKickoffPack, project.id)
    milestones = db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
    ms_lines = "\n".join(
        f"- {m.name}: {m.start_date or '?'} → {m.target_date or '?'}" for m in milestones
    )
    mapping = build_mapping(
        PROJECT_NAME=project.name,
        PROJECT_CODE=project.code,
        CLIENT_NAME=project.client_name,
        SCOPE=pack.scope if pack else "",
        NON_SCOPE=pack.non_scope if pack else "",
        MILESTONE_TABLE=ms_lines,
    )
    sub = "previews" if preview else "projects"
    out_dir = Path(settings.upload_dir) / sub / str(project.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"pre_kickoff_{uuid.uuid4().hex}.pptx"
    copy_template("pre_kickoff_deck.pptx", dest)
    replace_in_pptx(dest, mapping)
    return dest


def generate_pre_kickoff_deck(db: Session, project: Project, user_id: int) -> Document:
    from app.services.document_registry import register_file_as_document

    dest = build_pre_kickoff_deck_path(db, project, preview=False)
    pack = db.get(PreKickoffPack, project.id)
    doc = register_file_as_document(
        db,
        project,
        user_id,
        dest,
        DocumentType.pre_kickoff_deck,
        display_filename=f"Pre_Kickoff_{project.code}.pptx",
    )
    if not doc:
        raise FileNotFoundError("Failed to register pre kickoff deck")
    if pack:
        pack.deck_generated_at = datetime.utcnow()
    db.flush()
    return doc
