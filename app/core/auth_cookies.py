from fastapi import Response

from app.config import settings
from app.core.security import ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS

ACCESS_COOKIE = "pdc_access"
REFRESH_COOKIE = "pdc_refresh"
# Root path so cookies are sent for all /api/* requests via Vite dev proxy (same host:port).
COOKIE_PATH = "/"


def _cookie_secure() -> bool:
    return settings.app_env.lower() == "production"


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    secure = _cookie_secure()
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path=COOKIE_PATH,
    )
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=COOKIE_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    secure = _cookie_secure()
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(key=name, path=COOKIE_PATH, secure=secure, samesite="lax")
