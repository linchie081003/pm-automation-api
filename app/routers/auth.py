from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.auth_cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from app.core.deps import get_current_user
from app.core.rate_limit import check_rate_limit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.database import get_db
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse, RefreshRequest, RoleBrief
from app.services.authorization import load_user_permissions

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{email.strip().lower()}"


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    check_rate_limit(
        _client_key(request, body.email),
        max_attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    user = db.scalar(
        select(User)
        .where(User.email == body.email)
        .options(selectinload(User.roles))
    )
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account inactive")
    user.last_login = datetime.utcnow()
    db.commit()
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    set_auth_cookies(response, access, refresh)
    return LoginResponse(ok=True)


@router.post("/refresh", response_model=LoginResponse)
def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest = RefreshRequest(),
    db: Session = Depends(get_db),
):
    raw = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    check_rate_limit(
        f"refresh:{request.client.host if request.client else 'unknown'}",
        max_attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    try:
        payload = decode_token(raw)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        user_id = int(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    set_auth_cookies(response, access, refresh)
    return LoginResponse(ok=True)


@router.post("/logout", response_model=LoginResponse)
def logout(response: Response):
    clear_auth_cookies(response)
    return LoginResponse(ok=True)


@router.get("/me", response_model=MeResponse)
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.roles))
    )
    perms = load_user_permissions(db, user)
    perm_list = sorted(perms) if "*" not in perms else ["*"]
    return MeResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        is_active=user.is_active,
        roles=[RoleBrief.model_validate(r) for r in user.roles],
        permissions=perm_list,
    )
