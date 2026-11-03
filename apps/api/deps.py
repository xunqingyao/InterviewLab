from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from db.commands import get_or_create_dev_user, user_from_token
from db.session import SessionLocal
from db.settings import settings
from domain.errors import AuthRequired, ActionNotAllowed
from db.models import User


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    session: Annotated[str | None, Cookie()] = None,
    x_dev_auth: Annotated[str | None, Header()] = None,
) -> User:
    user = await user_from_token(db, session)
    if user:
        csrf = request.cookies.get("csrf_token")
        request.state.csrf = csrf
        return user
    if settings.interviewlab_dev_auth and request.client and request.client.host in {"127.0.0.1", "localhost", "testclient"}:
        user = await get_or_create_dev_user(db)
        await db.commit()
        return user
    if x_dev_auth and settings.interviewlab_dev_auth:
        return await get_or_create_dev_user(db)
    raise AuthRequired("login required")


def require_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if not request.cookies.get("session"):
        return
    if request.headers.get("x-csrf-token") != request.cookies.get("csrf_token"):
        raise ActionNotAllowed("csrf token missing or mismatched")
