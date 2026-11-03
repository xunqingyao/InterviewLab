from fastapi import APIRouter
from sqlalchemy import text

from db.session import engine

router = APIRouter()


@router.get("/health/live")
async def live():
    return {"ok": True}


@router.get("/health/ready")
async def ready():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"ok": True, "db": True}
