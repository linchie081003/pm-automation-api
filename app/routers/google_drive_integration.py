"""Org-level Google Drive service account (Settings UI)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import PermissionChecker, get_current_user
from app.database import get_db
from app.models import IntegrationSettings, User
from app.services.google_drive import (
    drive_configured,
    format_drive_api_error,
    service_account_email,
    test_drive_connection,
)

router = APIRouter(prefix="/integrations/google-drive", tags=["google-drive"])

_MAX_JSON_BYTES = 256_000
_DEBUG_LOG = Path(__file__).resolve().parents[3] / "debug-aa7388.log"


class ServiceAccountJsonBody(BaseModel):
    service_account_json: dict


def _agent_log(message: str, data: dict, hypothesis_id: str) -> None:
    # #region agent log
    try:
        import time

        payload = {
            "sessionId": "aa7388",
            "timestamp": int(time.time() * 1000),
            "location": "google_drive_integration.py",
            "message": message,
            "data": data,
            "hypothesisId": hypothesis_id,
        }
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except OSError:
        pass
    # #endregion


def _integration_row(db: Session) -> IntegrationSettings:
    row = db.scalar(select(IntegrationSettings).limit(1))
    if not row:
        row = IntegrationSettings(clickup_offer_on_kickoff=True)
        db.add(row)
        db.flush()
    return row


def _parse_service_account_json(raw: bytes) -> tuple[dict, str]:
    if len(raw) > _MAX_JSON_BYTES:
        raise HTTPException(status_code=400, detail="File JSON terlalu besar")
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="File bukan JSON UTF-8 yang valid") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON harus berupa objek")
    if data.get("type") != "service_account":
        raise HTTPException(status_code=400, detail="Bukan kunci service account Google (type harus service_account)")
    email = data.get("client_email")
    if not email or not isinstance(email, str):
        raise HTTPException(status_code=400, detail="client_email tidak ditemukan di JSON")
    if not data.get("private_key"):
        raise HTTPException(status_code=400, detail="private_key tidak ditemukan di JSON")
    return data, email.strip()


def _parse_service_account_dict(data: dict) -> tuple[dict, str]:
    encoded = json.dumps(data).encode("utf-8")
    return _parse_service_account_json(encoded)


def _persist_service_account(db: Session, data: dict, email: str) -> None:
    row = _integration_row(db)
    row.google_drive_service_account_json = json.dumps(data)
    row.google_drive_service_account_email = email
    row.google_drive_configured_at = datetime.utcnow()
    db.commit()


def _env_file_active() -> bool:
    path = settings.google_drive_service_account_file
    return bool(path and Path(path).is_file())


@router.get("")
def get_config(
    db: Session = Depends(get_db),
    _: set[str] = Depends(
        PermissionChecker("integrations.google_drive.configure", "documents.upload"),
    ),
):
    row = db.scalar(select(IntegrationSettings).limit(1))
    has_stored = bool(row and row.google_drive_service_account_json)
    configured = drive_configured(db)
    email = service_account_email(db)
    return {
        "configured": configured,
        "service_account_email": email,
        "has_credentials": has_stored,
        "configured_at": (
            row.google_drive_configured_at.isoformat()
            if row and row.google_drive_configured_at
            else None
        ),
        "env_fallback": configured and not has_stored and _env_file_active(),
    }


@router.post("/credentials/json")
def save_credentials_json(
    body: ServiceAccountJsonBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("integrations.google_drive.configure")),
):
    _agent_log(
        "save_credentials_json",
        {"user_id": user.id, "transport": "json"},
        "A",
    )
    data, email = _parse_service_account_dict(body.service_account_json)
    _persist_service_account(db, data, email)
    _agent_log(
        "save_credentials_json_ok",
        {"user_id": user.id, "client_email": email},
        "A",
    )
    return {"updated": True, "service_account_email": email}


@router.post("/credentials")
async def upload_credentials_multipart(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: set[str] = Depends(PermissionChecker("integrations.google_drive.configure")),
):
    _agent_log(
        "upload_credentials_multipart",
        {"user_id": user.id, "transport": "multipart", "filename": file.filename},
        "A",
    )
    raw = await file.read()
    data, email = _parse_service_account_json(raw)
    _persist_service_account(db, data, email)
    return {"updated": True, "service_account_email": email}


@router.delete("/credentials")
def delete_credentials(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.google_drive.configure")),
):
    row = db.scalar(select(IntegrationSettings).limit(1))
    if row:
        row.google_drive_service_account_json = None
        row.google_drive_service_account_email = None
        row.google_drive_configured_at = None
        db.commit()
    return {"deleted": True}


@router.post("/test")
def test_connection(
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("integrations.google_drive.configure")),
):
    if not drive_configured(db):
        raise HTTPException(
            status_code=400,
            detail="Belum dikonfigurasi — unggah file JSON service account terlebih dahulu",
        )
    try:
        test_drive_connection(db)
    except Exception as exc:
        detail = format_drive_api_error(exc)
        _agent_log(
            "test_connection_failed",
            {"error_type": type(exc).__name__, "detail": detail[:300]},
            "B",
        )
        raise HTTPException(status_code=400, detail=detail) from exc
    _agent_log(
        "test_connection_ok",
        {"client_email": service_account_email(db)},
        "B",
    )
    return {"ok": True, "service_account_email": service_account_email(db)}
