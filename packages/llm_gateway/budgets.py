from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from db.models import BudgetAccount, LlmSlot
from db.session import SessionLocal
from db.settings import settings
from domain.budgets import can_reserve, settle
from domain.errors import QuotaExceeded
from llm_gateway.redaction import redact_prompt_metadata

ESTIMATE_PER_CALL = Decimal("0.05")


def live_cost_limit() -> Decimal | None:
    raw = (settings.max_session_cost or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise QuotaExceeded("MAX_SESSION_COST is not a valid decimal") from exc


def assert_live_configured() -> Decimal:
    limit = live_cost_limit()
    if limit is None:
        raise QuotaExceeded("Live provider requires MAX_SESSION_COST to be set")
    if limit <= 0:
        raise QuotaExceeded("MAX_SESSION_COST must be greater than 0")
    return limit


async def ensure_llm_slots() -> None:
    async with SessionLocal() as db:
        for slot_no in range(settings.global_llm_slots):
            stmt = (
                insert(LlmSlot)
                .values(id=uuid.uuid4(), scope="global", slot_no=slot_no, status="FREE")
                .on_conflict_do_nothing(index_elements=["scope", "slot_no"])
            )
            await db.execute(stmt)
        await db.commit()


async def acquire_slot(call_id: uuid.UUID) -> uuid.UUID:
    await ensure_llm_slots()
    async with SessionLocal() as db:
        now = datetime.now(UTC)
        await db.execute(
            text(
                """
                UPDATE llm_slots
                SET status = 'FREE', llm_call_id = NULL
                WHERE scope = 'global'
                  AND status = 'BUSY'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < :now
                """
            ),
            {"now": now},
        )
        row = (
            await db.execute(
                text(
                    """
                    WITH picked AS (
                        SELECT id
                        FROM llm_slots
                        WHERE scope = 'global' AND status = 'FREE'
                        ORDER BY slot_no
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE llm_slots AS slot
                    SET status = 'BUSY',
                        llm_call_id = :call_id,
                        lease_expires_at = :expires
                    FROM picked
                    WHERE slot.id = picked.id
                    RETURNING slot.id
                    """
                ),
                {"call_id": call_id, "expires": now + timedelta(seconds=45)},
            )
        ).first()
        await db.commit()
    if row is None:
        raise QuotaExceeded("global LLM slot limit reached")
    return row[0]


async def release_slot(slot_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        slot = await db.get(LlmSlot, slot_id)
        if slot:
            slot.status = "FREE"
            slot.llm_call_id = None
            slot.lease_expires_at = None
        await db.commit()


async def reserve_session_budget(session_id: str | None, amount: Decimal) -> Decimal:
    if not session_id:
        return amount
    async with SessionLocal() as db:
        acc = (
            await db.execute(
                select(BudgetAccount)
                .where(BudgetAccount.scope == f"session:{session_id}")
                .with_for_update()
            )
        ).scalar_one_or_none()
        if acc is None:
            await db.commit()
            return amount
        can_reserve(
            spent=Decimal(str(acc.spent_amount)),
            reserved=Decimal(str(acc.reserved_amount)),
            requested=amount,
            limit=Decimal(str(acc.limit_amount)),
        )
        acc.reserved_amount = Decimal(str(acc.reserved_amount)) + amount
        await db.commit()
    return amount


async def settle_session_budget(session_id: str | None, reserved: Decimal, actual: Decimal) -> None:
    if not session_id:
        return
    unused = settle(reserved_amount=reserved, actual=actual)
    async with SessionLocal() as db:
        acc = (
            await db.execute(
                select(BudgetAccount)
                .where(BudgetAccount.scope == f"session:{session_id}")
                .with_for_update()
            )
        ).scalar_one_or_none()
        if acc is None:
            await db.commit()
            return
        acc.reserved_amount = max(Decimal(str(acc.reserved_amount)) - reserved, Decimal("0"))
        acc.spent_amount = Decimal(str(acc.spent_amount)) + actual
        unused  # remainder already released from reserved
        await db.commit()


assert redact_prompt_metadata
