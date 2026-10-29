"""FR-27: one user, email and password, server-side sessions, no roles."""

from __future__ import annotations

from datetime import timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import Settings
from app.db import get_sessionmaker
from app.models import AuditLog, Session
from app.models.base import utcnow

PROTECTED = [
    ("GET", "/api/auth/me"),
    ("GET", "/api/conversations"),
    ("GET", "/api/documents"),
    ("GET", "/api/usage"),
    ("GET", "/api/workspace/health"),
    ("GET", "/api/spans/s_msa_1223"),
]


async def test_login_sets_an_httponly_session_cookie(
    client: AsyncClient, user, settings: Settings
) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"email": settings.seed_email, "password": settings.seed_password},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "email": settings.seed_email,
        "workspaceName": settings.workspace_name,
    }

    cookie = response.headers["set-cookie"]
    assert settings.session_cookie_name in cookie
    assert "HttpOnly" in cookie, "a readable session cookie is an XSS session grant"
    assert "samesite=lax" in cookie.lower()


async def test_login_creates_a_revocable_session_row(
    client: AsyncClient, user, settings: Settings
) -> None:
    """FR-27 says server-side sessions. A JWT could not be revoked."""
    await client.post(
        "/api/auth/login",
        json={"email": settings.seed_email, "password": settings.seed_password},
    )
    async with get_sessionmaker()() as db:
        rows = list((await db.execute(select(Session))).scalars())
    assert len(rows) == 1
    assert rows[0].revoked_at is None
    assert rows[0].expires_at > utcnow()


async def test_wrong_password_is_rejected_without_naming_which_field(
    client: AsyncClient, user, settings: Settings
) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"email": settings.seed_email, "password": "not-the-password"},
    )
    assert response.status_code == 401
    message = response.json()["message"]
    # Naming which field was wrong is an account-enumeration oracle.
    assert "email and password do not match" in message
    assert "password is" not in message.lower()


async def test_unknown_email_gives_the_same_message(
    client: AsyncClient, user, settings: Settings
) -> None:
    unknown = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": settings.seed_password},
    )
    wrong = await client.post(
        "/api/auth/login",
        json={"email": settings.seed_email, "password": "wrong"},
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["message"] == wrong.json()["message"]


async def test_malformed_email_is_rejected_at_the_boundary(client: AsyncClient, user) -> None:
    response = await client.post(
        "/api/auth/login", json={"email": "not-an-email", "password": "whatever123"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["kind"] == "network"
    assert body["action"], "FR-28: every error names a next action"


async def test_protected_routes_401_without_a_session(client: AsyncClient) -> None:
    for method, path in PROTECTED:
        response = await client.request(method, path)
        assert response.status_code == 401, f"{method} {path} was not protected"
        body = response.json()
        assert body["kind"] == "network"
        assert body["action"] == "Sign in to continue."


async def test_logout_revokes_the_row_not_just_the_cookie(
    auth: AsyncClient, settings: Settings
) -> None:
    """ "Log out" has to mean something in a product holding privileged material."""
    async with get_sessionmaker()() as db:
        session_id = (await db.execute(select(Session.id))).scalars().one()

    assert (await auth.post("/api/auth/logout")).status_code == 204

    async with get_sessionmaker()() as db:
        revoked = (await db.execute(select(Session).where(Session.id == session_id))).scalar_one()
    assert revoked.revoked_at is not None

    # Even replaying the cookie value does not work any more.
    auth.cookies.set(settings.session_cookie_name, session_id)
    assert (await auth.get("/api/auth/me")).status_code == 401


async def test_expired_session_is_rejected(auth: AsyncClient) -> None:
    async with get_sessionmaker()() as db:
        session = (await db.execute(select(Session))).scalars().one()
        session.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    response = await auth.get("/api/auth/me")
    assert response.status_code == 401
    body = response.json()
    assert "no longer valid" in body["message"]
    assert body["action"] == "Sign in again to continue."


async def test_forged_cookie_is_rejected(client: AsyncClient, user, settings: Settings) -> None:
    client.cookies.set(settings.session_cookie_name, "s_completely-made-up")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_and_failure_are_both_audited(
    client: AsyncClient, user, settings: Settings
) -> None:
    await client.post("/api/auth/login", json={"email": settings.seed_email, "password": "wrong"})
    await client.post(
        "/api/auth/login",
        json={"email": settings.seed_email, "password": settings.seed_password},
    )

    async with get_sessionmaker()() as db:
        actions = list((await db.execute(select(AuditLog.action))).scalars())
    assert "auth.login_failed" in actions
    assert "auth.login" in actions


async def test_password_is_never_stored_in_the_clear(user, settings: Settings) -> None:
    assert settings.seed_password not in user.password_hash
    assert user.password_hash.startswith("$argon2id$"), "Argon2id expected"


async def test_another_users_resources_are_404_not_403(auth: AsyncClient, app) -> None:
    """A 403 confirms the id exists. v1 has one user; this is cheap now and
    awkward to retrofit once multi-tenancy lands."""
    from app.models import User
    from app.services import auth as auth_service

    async with get_sessionmaker()() as db:
        other = User(
            email="marcus@example.com",
            password_hash=auth_service.hash_password("paralegal-pass-1"),
            workspace_name="Marcus",
        )
        db.add(other)
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as second:
        await second.post(
            "/api/auth/login",
            json={"email": "marcus@example.com", "password": "paralegal-pass-1"},
        )
        mine = (await auth.post("/api/conversations", json={"title": "Private"})).json()
        response = await second.get(f"/api/conversations/{mine['id']}")
        assert response.status_code == 404, "403 would confirm the conversation exists"


async def test_session_cookie_is_scoped_and_not_shared(auth: AsyncClient, app) -> None:
    """A second client with no cookie sees nothing of the first's workspace."""
    await auth.post("/api/conversations", json={"title": "Mine"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.get("/api/conversations")).status_code == 401
