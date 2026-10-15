import pytest
from datetime import datetime, timedelta, timezone

from config import TIER_MONTHLY
from database.queries import (
    activate_subscription,
    create_payment,
    create_user,
    get_user,
    set_user_tier,
)


@pytest.mark.asyncio
async def test_activation_extends_and_idempotent(db):
    await create_user(20)
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)
    await set_user_tier(20, TIER_MONTHLY, future)

    payment = await create_payment(20, "stars", TIER_MONTHLY, 50, "XTR")
    first = await activate_subscription(payment.id)
    assert first is True
    u = await get_user(20)
    assert u.tier_expires_at > future  # extended, not reset

    second = await activate_subscription(payment.id)
    assert second is False
    u2 = await get_user(20)
    assert u2.tier_expires_at == u.tier_expires_at  # no double extension


@pytest.mark.asyncio
async def test_addon_activation_keeps_tier(db):
    await create_user(21)
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)
    await set_user_tier(21, TIER_MONTHLY, future)

    payment = await create_payment(21, "stars", "addon", 30, "XTR", is_addon=True)
    activated = await activate_subscription(payment.id)
    assert activated is True
    u = await get_user(21)
    assert u.tier == TIER_MONTHLY
    assert u.has_2k_addon is True
    assert u.tier_expires_at == future  # tier expiry unchanged
