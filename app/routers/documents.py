import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import PermissionChecker, get_current_user, get_permission_codes
from app.core.project_access import ensure_permission, ensure_project_read, ensure_project_write
from app.database import get_db
from app.models import Document, DocumentType, Project, ProjectPhase, User
from app.services.activity import log_activity
from app.services.google_drive import parse_drive_folder_id, upload_bytes_to_folder

router = APIRouter(tags=["documents"])

ALLOWED_EXT = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}

PREVIEW_INLINE_EXT = {".pdf", ".png", ".jpg", ".jpeg"}


@router.get("/projects/{project_id}/documents")
def list_documents(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "documents.download")
    ensure_project_read(project_id, user, codes, db)
    docs = db.scalars(
        select(Document).where(Document.project_id == project_id).order_by(Document.created_at.desc())
    ).all()
    return [
        {
            "id": d.id,
            "doc_type": d.doc_type.value,
            "filename": d.filename,
            "external_url": d.external_url,
            "phase": d.phase.value if d.phase else None,
            "created_at": d.created_at.isoformat(),
        }
        for d in docs
    ]


@router.post("/projects/{project_id}/documents")
async def upload_document(
    project_id: int,
    doc_type: str,
    phase: str | None = None,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "documents.upload")
    ensure_project_write(project_id, user, codes, db)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="File type not allowed")
    try:
        dtype = DocumentType(doc_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid doc_type") from exc
    ph: ProjectPhase | None = None
    if phase and str(phase).strip():
        try:
            ph = ProjectPhase(str(phase).strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid phase") from exc

    safe = re.sub(r"[^\w.\-]", "_", file.filename or "file")
    rel = Path("projects") / str(project_id) / f"{uuid.uuid4().hex}_{safe}"
    dest = Path(settings.upload_dir) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)

    project = db.get(Project, project_id)
    gdrive_url: str | None = None
    gdrive_error: str | None = None
    folder_id = parse_drive_folder_id(project.document_repo_url if project else None)
    if folder_id:
        try:
            gdrive_url = upload_bytes_to_folder(
                folder_id,
                file.filename or safe,
                content,
                db=db,
            )
        except Exception as exc:
            gdrive_error = str(exc)

    doc = Document(
        project_id=project_id,
        phase=ph,
        doc_type=dtype,
        filename=file.filename or safe,
        storage_path=str(rel).replace("\\", "/"),
        external_url=gdrive_url,
        uploaded_by_id=user.id,
    )
    db.add(doc)
    log_activity(
        db,
        project_id,
        user.id,
        "document.uploaded",
        {
            "type": doc_type,
            "filename": doc.filename,
            "gdrive": bool(gdrive_url),
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if dest.is_file():
            try:
                dest.unlink()
            except OSError:
                pass
        err = str(getattr(exc, "orig", exc)).lower()
        if "project_id" in err or "projects" in err:
            raise HTTPException(
                status_code=404,
                detail="Project not found — dokumen tidak disimpan",
            ) from None
        if "documenttype" in err or "doc_type" in err or "enum" in err:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Tipe dokumen belum terdaftar di database — restart backend "
                    "setelah update aplikasi (enum doc_type)."
                ),
            ) from None
        raise HTTPException(status_code=400, detail="Gagal menyimpan dokumen") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        if dest.is_file():
            try:
                dest.unlink()
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail=f"Gagal menyimpan dokumen: {exc.__class__.__name__}",
        ) from exc
    out: dict = {"id": doc.id, "filename": doc.filename}
    if gdrive_url:
        out["gdrive_url"] = gdrive_url
    if gdrive_error:
        out["gdrive_error"] = gdrive_error
    return out


def _document_file_response(doc: Document, inline: bool) -> FileResponse:
    path = Path(settings.upload_dir) / doc.storage_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")
    ext = path.suffix.lower()
    use_inline = inline and ext in PREVIEW_INLINE_EXT
    media_types = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    headers = {}
    if use_inline:
        headers["Content-Disposition"] = f'inline; filename="{doc.filename}"'
    return FileResponse(
        path,
        filename=doc.filename,
        media_type=media_types.get(ext),
        headers=headers if headers else None,
    )


@router.get("/projects/{project_id}/documents/{doc_id}/download")
def download_document(
    project_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "documents.download")
    ensure_project_read(project_id, user, codes, db)
    doc = db.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    return _document_file_response(doc, inline=False)


@router.delete("/projects/{project_id}/documents/{doc_id}", status_code=204)
def delete_document(
    project_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "documents.upload")
    ensure_project_write(project_id, user, codes, db)
    doc = db.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    if doc.is_template:
        raise HTTPException(status_code=400, detail="Cannot delete template")
    filename = doc.filename
    path = Path(settings.upload_dir) / doc.storage_path
    db.delete(doc)
    log_activity(db, project_id, user.id, "document.deleted", {"filename": filename})
    db.commit()
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


@router.get("/projects/{project_id}/documents/{doc_id}/preview")
def preview_document(
    project_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "documents.download")
    ensure_project_read(project_id, user, codes, db)
    doc = db.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise HTTPException(status_code=404, detail="Not found")
    return _document_file_response(doc, inline=True)


@router.get("/documents/templates/{deck_type}/download")
def download_template(
    deck_type: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    codes: set[str] = Depends(get_permission_codes),
):
    ensure_permission(codes, "documents.download")
    try:
        dtype = DocumentType(deck_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid deck type") from exc
    doc = db.scalar(
        select(Document).where(Document.is_template.is_(True), Document.doc_type == dtype)
    )
    if not doc:
        placeholder = Path(settings.upload_dir) / "templates" / f"{deck_type}.txt"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        if not placeholder.exists():
            placeholder.write_text(
                f"Placeholder template for {deck_type}. Replace with organization PPTX.",
                encoding="utf-8",
            )
        return FileResponse(placeholder, filename=f"{deck_type}_template.txt")
    path = Path(settings.upload_dir) / doc.storage_path
    return FileResponse(path, filename=doc.filename)
