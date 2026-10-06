"""Persisted refresh-token sessions (jti): rotation on refresh, revoke on logout."""

from datetime import datetime, timezone

from jose import JWTError
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.security import create_refresh_token, decode_token, refresh_token_expires_at_utc
from app.core.timezone import APP_TZ, now_jakarta
from app.models.auth import RefreshTokenSession


def _expires_at_naive(utc_dt: datetime) -> datetime:
    """Store JWT exp as naive Jakarta time (same convention as other DateTime columns)."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(APP_TZ).replace(tzinfo=None)


def _session_active(row: RefreshTokenSession, *, now: datetime | None = None) -> bool:
    if row.revoked_at is not None:
        return False
    ref = now or now_jakarta()
    return row.expires_at > ref


def persist_refresh_session(db: Session, user_id: int, jti: str, raw_refresh: str) -> None:
    expires_utc = refresh_token_expires_at_utc(raw_refresh)
    db.add(
        RefreshTokenSession(
            jti=jti,
            user_id=user_id,
            expires_at=_expires_at_naive(expires_utc),
        )
    )


def issue_refresh_token(db: Session, user_id: int) -> str:
    token, jti = create_refresh_token(user_id)
    persist_refresh_session(db, user_id, jti, token)
    return token


def revoke_refresh_token_raw(db: Session, raw: str | None) -> None:
    if not raw:
        return
    try:
        payload = decode_token(raw, verify_exp=False)
    except JWTError:
        return
    if payload.get("type") != "refresh":
        return
    jti = payload.get("jti")
    if not jti:
        return
    row = db.get(RefreshTokenSession, jti)
    if row and row.revoked_at is None:
        row.revoked_at = now_jakarta()


def revoke_all_user_refresh_sessions(db: Session, user_id: int) -> None:
    now = now_jakarta()
    db.execute(
        update(RefreshTokenSession)
        .where(
            RefreshTokenSession.user_id == user_id,
            RefreshTokenSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


def consume_refresh_token(db: Session, raw: str) -> int:
    """
    Validate refresh JWT + DB session, revoke it (rotation), return user_id.
    Caller issues new access/refresh pair and commits.
    """
    try:
        payload = decode_token(raw, verify_exp=True)
    except JWTError as exc:
        raise ValueError("invalid_refresh") from exc
    if payload.get("type") != "refresh":
        raise ValueError("invalid_refresh")
    jti = payload.get("jti")
    if not jti:
        raise ValueError("invalid_refresh")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid_refresh") from exc

    row = db.get(RefreshTokenSession, jti)
    if not row or row.user_id != user_id or not _session_active(row):
        raise ValueError("invalid_refresh")

    row.revoked_at = now_jakarta()
    return user_id


def purge_expired_refresh_sessions(db: Session, *, batch: int = 500) -> None:
    """Best-effort cleanup of expired session rows."""
    now = now_jakarta()
    cutoff = now.replace(tzinfo=None) if now.tzinfo else now
    db.execute(
        delete(RefreshTokenSession)
        .where(RefreshTokenSession.expires_at < cutoff)
        .execution_options(synchronize_session=False)
    )
