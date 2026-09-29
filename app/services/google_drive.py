"""Optional upload to Google Drive folder (project.document_repo_url)."""

from __future__ import annotations



import io

import json

import mimetypes

import re

import time

from pathlib import Path



from sqlalchemy import select

from sqlalchemy.orm import Session



from app.config import settings



DRIVE_SCOPES = (

    "https://www.googleapis.com/auth/drive.file",

    "https://www.googleapis.com/auth/drive.metadata.readonly",

)



_FOLDER_PATTERNS = (

    re.compile(r"/folders/([a-zA-Z0-9_-]+)"),

    re.compile(r"/drive/folders/([a-zA-Z0-9_-]+)"),

    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),

    re.compile(r"^([a-zA-Z0-9_-]{20,})$"),

)




def _normalize_drive_id(raw: str) -> str:

    """Strip trailing punctuation often copied from URLs or sentences."""

    return raw.strip().rstrip(".,;:)\"'")





def parse_drive_folder_id(repo_url: str | None) -> str | None:

    if not repo_url or not str(repo_url).strip():

        return None

    s = str(repo_url).strip()

    for pat in _FOLDER_PATTERNS:

        m = pat.search(s)

        if m:

            return _normalize_drive_id(m.group(1))

    return None





def _load_sa_from_db(db: Session) -> dict | None:

    from app.models import IntegrationSettings



    row = db.scalar(select(IntegrationSettings).limit(1))

    if not row or not row.google_drive_service_account_json:

        return None

    try:

        data = json.loads(row.google_drive_service_account_json)

    except json.JSONDecodeError:

        return None

    return data if isinstance(data, dict) else None





def _load_sa_from_env_file() -> dict | None:

    path = settings.google_drive_service_account_file

    if not path:

        return None

    p = Path(path)

    if not p.is_file():

        return None

    try:

        data = json.loads(p.read_text(encoding="utf-8"))

    except (OSError, json.JSONDecodeError):

        return None

    return data if isinstance(data, dict) else None





def resolve_service_account_info(db: Session | None = None) -> dict | None:

    if db is not None:

        stored = _load_sa_from_db(db)

        if stored:

            return stored

    return _load_sa_from_env_file()





def service_account_email(db: Session | None = None) -> str | None:

    data = resolve_service_account_info(db)

    if not data:

        return None

    email = data.get("client_email")

    return str(email) if email else None





def drive_configured(db: Session | None = None) -> bool:

    data = resolve_service_account_info(db)

    return bool(data and data.get("private_key") and data.get("client_email"))





def _build_credentials(data: dict):

    from google.oauth2 import service_account



    return service_account.Credentials.from_service_account_info(

        data,

        scopes=list(DRIVE_SCOPES),

    )





def _drive_service(db: Session | None = None):

    data = resolve_service_account_info(db)

    if not data:

        return None

    from googleapiclient.discovery import build



    creds = _build_credentials(data)

    return build("drive", "v3", credentials=creds, cache_discovery=False)





def format_drive_api_error(exc: BaseException) -> str:

    if isinstance(exc, ModuleNotFoundError) and "google" in str(exc).lower():

        return (

            "Paket Google Drive belum terpasang di Python backend. "

            "Jalankan: pip install -r requirements.txt (folder backend), lalu restart uvicorn."

        )

    text = str(exc)

    lower = text.lower()

    if "not found" in lower and ("fileid" in lower or "parents" in lower or "404" in lower):

        return (

            "Folder Google Drive tidak ditemukan atau service account belum punya akses. "

            "Pastikan link adalah folder (bukan file), ID benar, dan folder di-share ke "

            f"{service_account_email() or 'email service account'} sebagai Editor."

        )

    if "accessnotconfigured" in lower or "has not been used" in lower or "is disabled" in lower:

        return (

            "Google Drive API belum di-enable di project GCP yang sama dengan service account. "

            "APIs & Services → Enable APIs → Google Drive API."

        )

    if "insufficient" in lower and "scope" in lower:

        return (

            "Scope OAuth tidak cukup untuk test — restart backend setelah update aplikasi, "

            "lalu test lagi."

        )

    if "invalid_grant" in lower or "account not found" in lower:

        return "Kunci service account tidak valid atau sudah dicabut di Google Cloud — buat key JSON baru."

    return text[:500] if len(text) > 500 else text





def _assert_upload_folder(service, folder_id: str, db: Session | None) -> None:

    from googleapiclient.errors import HttpError



    email = service_account_email(db) or "service account"

    try:

        meta = (

            service.files()

            .get(

                fileId=folder_id,

                fields="id,mimeType,trashed,name",

                supportsAllDrives=True,

            )

            .execute()

        )

    except HttpError as exc:

        if exc.resp.status in (403, 404):

            raise RuntimeError(

                f"Folder Drive tidak dapat diakses (HTTP {exc.resp.status}). "

                f"Share folder ke {email} sebagai Editor, atau perbaiki link folder di proyek."

            ) from exc

        raise RuntimeError(format_drive_api_error(exc)) from exc



    if meta.get("trashed"):

        raise RuntimeError("Folder Google Drive ada di Trash — pulihkan atau ganti link folder.")

    mime = meta.get("mimeType") or ""

    if mime != "application/vnd.google-apps.folder":

        raise RuntimeError(

            "Link repo bukan folder Drive (mungkin link file). "

            "Gunakan URL folder: …/drive/folders/<id>"

        )





def test_drive_connection(db: Session | None = None) -> None:

    data = resolve_service_account_info(db)

    if not data:

        raise RuntimeError(

            "Google Drive belum dikonfigurasi — unggah service account di Setting → Google Drive"

        )

    from google.auth.transport.requests import Request



    creds = _build_credentials(data)

    creds.refresh(Request())

    service = _drive_service(db)

    if not service:

        raise RuntimeError("Gagal membuat klien Google Drive")

    service.about().get(fields="user(emailAddress)").execute()





def upload_bytes_to_folder(

    folder_id: str,

    filename: str,

    content: bytes,

    *,

    mime_type: str | None = None,

    db: Session | None = None,

) -> str:

    folder_id = _normalize_drive_id(folder_id)

    service = _drive_service(db)

    if not service:

        raise RuntimeError(

            "Google Drive belum dikonfigurasi — unggah service account di Setting → Google Drive"

        )

    _assert_upload_folder(service, folder_id, db)

    mime = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    meta = {"name": filename, "parents": [folder_id]}

    from googleapiclient.errors import HttpError

    from googleapiclient.http import MediaIoBaseUpload



    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime, resumable=False)

    try:

        created = (

            service.files()

            .create(

                body=meta,

                media_body=media,

                fields="id,webViewLink",

                supportsAllDrives=True,

            )

            .execute()

        )

    except HttpError as exc:

        raise RuntimeError(format_drive_api_error(exc)) from exc

    link = created.get("webViewLink")

    if link:

        return str(link)

    fid = created.get("id")

    if fid:

        return f"https://drive.google.com/file/d/{fid}/view"

    raise RuntimeError("Google Drive tidak mengembalikan link file")

