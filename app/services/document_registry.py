from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, DocumentType, Project, ProjectPhase


def register_file_as_document(
    db: Session,
    project: Project,
    user_id: int | None,
    absolute_path: Path,
    doc_type: DocumentType,
    display_filename: str | None = None,
    phase: ProjectPhase | None = None,
) -> Document | None:
    upload_root = Path(settings.upload_dir).resolve()
    path = absolute_path.resolve()
    if not path.is_file():
        return None
    try:
        rel = path.relative_to(upload_root).as_posix()
    except ValueError:
        return None
    doc = Document(
        project_id=project.id,
        doc_type=doc_type,
        filename=display_filename or path.name,
        storage_path=rel,
        uploaded_by_id=user_id,
        phase=phase or project.current_phase,
    )
    db.add(doc)
    db.flush()
    return doc
