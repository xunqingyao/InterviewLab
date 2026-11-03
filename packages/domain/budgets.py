from __future__ import annotations

from decimal import Decimal

from domain.errors import QuotaExceeded


def can_reserve(*, spent: Decimal, reserved: Decimal, requested: Decimal, limit: Decimal) -> None:
    if spent + reserved + requested > limit:
        raise QuotaExceeded(
            "model budget exceeded",
            details={
                "spent": str(spent),
                "reserved": str(reserved),
                "requested": str(requested),
                "limit": str(limit),
            },
        )


def settle(*, reserved_amount: Decimal, actual: Decimal) -> Decimal:
    if actual < 0:
        raise ValueError("actual usage cannot be negative")
    return max(reserved_amount - actual, Decimal("0"))
