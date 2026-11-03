from decimal import Decimal

from domain.budgets import can_reserve, settle
from domain.errors import QuotaExceeded


def test_reserve_blocks_overspend():
    try:
        can_reserve(spent=Decimal("4.5"), reserved=Decimal("0.4"), requested=Decimal("0.2"), limit=Decimal("5"))
        raise AssertionError
    except QuotaExceeded:
        pass


def test_settle_releases_remainder():
    assert settle(reserved_amount=Decimal("1.00"), actual=Decimal("0.25")) == Decimal("0.75")
