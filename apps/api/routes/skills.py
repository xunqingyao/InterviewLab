from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_db
from db.commands import skill_timeline
from db.models import User

router = APIRouter()


@router.get("/api/v1/skills")
async def skills(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return {"items": await skill_timeline(db, user.id)}
