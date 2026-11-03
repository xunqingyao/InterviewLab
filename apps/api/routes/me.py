from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_db
from db.commands import create_auth_session, get_or_create_dev_user
from db.models import User
from db.settings import settings

router = APIRouter()


@router.get("/api/v1/me")
async def me(user: User = Depends(current_user)):
    return {
        "id": str(user.id),
        "display_name": user.display_name,
        "dev_auth": settings.interviewlab_dev_auth,
        "quota": {"max_session_cost": settings.max_session_cost},
    }


@router.post("/auth/dev-login")
async def dev_login(response: Response, db: AsyncSession = Depends(get_db)):
    if not settings.interviewlab_dev_auth:
        return Response(status_code=404)
    user = await get_or_create_dev_user(db)
    auth = await create_auth_session(db, user)
    raw = auth._raw_token  # type: ignore[attr-defined]
    response.set_cookie("session", raw, httponly=True, samesite="lax")
    response.set_cookie("csrf_token", auth.csrf_token, httponly=False, samesite="lax")
    return {"ok": True, "csrf_token": auth.csrf_token, "user_id": str(user.id)}


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("session")
    response.delete_cookie("csrf_token")
    return {"ok": True}
