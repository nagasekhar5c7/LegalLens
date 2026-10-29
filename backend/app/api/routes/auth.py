"""FR-27: authenticate one user by email and password with server-side sessions."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api import errors
from app.api.deps import CurrentUser, DbSession, client_ip
from app.config import get_settings
from app.schemas.misc import LoginRequest, LoginResponse, Me
from app.services import audit
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, db: DbSession
) -> LoginResponse:
    user = await auth_service.authenticate(db, body.email, body.password)
    if user is None:
        # One message for both failure modes: naming which was wrong is an
        # enumeration oracle.
        audit.record(
            db,
            action=audit.ACTION_LOGIN_FAILED,
            detail={"email_sha": body.email[:1] + "***"},
            ip=client_ip(request),
        )
        await db.commit()
        raise errors.AppError(
            "network",
            "That email and password do not match an account.",
            "Check both and try again.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    session = await auth_service.create_session(db, user, request.headers.get("user-agent"))
    audit.record(db, action=audit.ACTION_LOGIN, user_id=user.id, ip=client_ip(request))

    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        session.id,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return LoginResponse(ok=True, email=user.email, workspace_name=user.workspace_name)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> Response:
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        # Revoke the row, not just the cookie — otherwise "log out" is a lie a
        # stolen cookie can ignore.
        loaded = await auth_service.load_session(db, session_id)
        await auth_service.revoke_session(db, session_id)
        if loaded is not None:
            audit.record(
                db, action=audit.ACTION_LOGOUT, user_id=loaded[1].id, ip=client_ip(request)
            )
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=Me)
async def me(user: CurrentUser) -> Me:
    return Me(email=user.email, workspace_name=user.workspace_name)
