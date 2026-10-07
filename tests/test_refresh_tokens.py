from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.core.security import create_refresh_token, decode_token
from app.models.auth import RefreshTokenSession
from app.services.refresh_tokens import (
    consume_refresh_token,
    revoke_all_user_refresh_sessions,
    revoke_refresh_token_raw,
)


def test_refresh_jwt_contains_jti():
    token, jti = create_refresh_token(42)
    payload = decode_token(token, verify_exp=False)
    assert payload["type"] == "refresh"
    assert payload["jti"] == jti
    assert payload["sub"] == "42"


def test_consume_refresh_rotates_session():
    db = MagicMock()
    token, jti = create_refresh_token(7)
    row = RefreshTokenSession(
        jti=jti,
        user_id=7,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        revoked_at=None,
    )
    db.get.return_value = row

    user_id = consume_refresh_token(db, token)
    assert user_id == 7
    assert row.revoked_at is not None


def test_consume_rejects_revoked_session():
    db = MagicMock()
    token, jti = create_refresh_token(7)
    row = RefreshTokenSession(
        jti=jti,
        user_id=7,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        revoked_at=datetime.now(),
    )
    db.get.return_value = row

    with pytest.raises(ValueError, match="invalid_refresh"):
        consume_refresh_token(db, token)


def test_revoke_marks_session():
    db = MagicMock()
    token, jti = create_refresh_token(3)
    row = RefreshTokenSession(
        jti=jti,
        user_id=3,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        revoked_at=None,
    )
    db.get.return_value = row

    revoke_refresh_token_raw(db, token)
    assert row.revoked_at is not None


def test_revoke_all_user_sessions_updates_db():
    db = MagicMock()
    revoke_all_user_refresh_sessions(db, 9)
    db.execute.assert_called_once()
