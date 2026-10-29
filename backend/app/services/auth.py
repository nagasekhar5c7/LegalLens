"""FR-27: one user, email + password, server-side sessions, no roles.

Password hashing is Argon2id. Session state is a revocable row, not a JWT — in a
product holding privileged material, "log out" has to actually mean something.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Session, User
from app.models.base import utcnow

_hasher = PasswordHasher()

#: Verified against a throwaway hash when the email is unknown, so a missing
#: user and a wrong password take the same time. Enumeration by stopwatch is
#: still enumeration.
_DUMMY_HASH = _hasher.hash("not-a-real-password-" + secrets.token_hex(8))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.strip().lower()))
    return result.scalar_one_or_none()


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(db, email)
    if not verify_password(user.password_hash if user else None, password):
        return None
    assert user is not None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


async def create_session(db: AsyncSession, user: User, user_agent: str | None) -> Session:
    settings = get_settings()
    session = Session(
        user_id=user.id,
        expires_at=utcnow() + timedelta(days=settings.session_ttl_days),
        user_agent=(user_agent or "")[:500] or None,
    )
    db.add(session)
    await db.flush()
    return session


async def load_session(db: AsyncSession, session_id: str) -> tuple[Session, User] | None:
    result = await db.execute(
        select(Session, User).join(User, Session.user_id == User.id).where(Session.id == session_id)
    )
    row = result.first()
    if row is None:
        return None
    session, user = row
    if not session.is_live():
        return None
    return session, user


async def touch_session(db: AsyncSession, session: Session) -> None:
    """Sliding expiry, but only written when it moves by more than a day — a
    DB write on every request would make every read a write."""
    settings = get_settings()
    target = utcnow() + timedelta(days=settings.session_ttl_days)
    if (target - session.expires_at) > timedelta(days=1):
        session.expires_at = target


async def revoke_session(db: AsyncSession, session_id: str) -> None:
    await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all_sessions(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
