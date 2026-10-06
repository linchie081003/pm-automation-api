import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_token(
    subject: str,
    expires_delta: timedelta,
    token_type: str,
    *,
    jti: str | None = None,
) -> str:
    # JWT ``exp`` uses UTC per RFC 7519 (independent of app display timezone WIB).
    expire = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": token_type}
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_access_token(user_id: int) -> str:
    return _create_token(
        str(user_id),
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def new_refresh_jti() -> str:
    return str(uuid.uuid4())


def create_refresh_token(user_id: int, jti: str | None = None) -> tuple[str, str]:
    token_jti = jti or new_refresh_jti()
    token = _create_token(
        str(user_id),
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
        jti=token_jti,
    )
    return token, token_jti


def refresh_token_expires_at_utc(token: str) -> datetime:
    payload = decode_token(token, verify_exp=False)
    exp = payload.get("exp")
    if exp is None:
        raise JWTError("missing exp")
    if isinstance(exp, datetime):
        return exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(int(exp), tz=timezone.utc)


def decode_token(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    options = {"verify_exp": verify_exp}
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[ALGORITHM],
        options=options,
    )
