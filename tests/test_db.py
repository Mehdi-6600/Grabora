import pytest
from datetime import datetime, timedelta, timezone

from database.queries import (
    create_user,
    get_user,
    set_user_tier,
    reset_all_daily_counters,
    downgrade_expired_users,
)
from config import TIER_FREE, TIER_MONTHLY


@pytest.mark.asyncio
async def test_daily_reset(db):
    user = await create_user(1)
    # Force a past reset time by updating via set? Use direct reset_all with now future.
    from database.db import get_db
    dbconn = get_db()
    past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).isoformat()
    await dbconn.execute("UPDATE users SET daily_downloads=3, daily_reset_at=? WHERE user_id=1", (past,))
    await dbconn.commit()
    n = await reset_all_daily_counters()
    assert n == 1
    u = await get_user(1)
    assert u.daily_downloads == 0


@pytest.mark.asyncio
async def test_subscription_expiry(db):
    user = await create_user(2)
    expired = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    await set_user_tier(2, TIER_MONTHLY, expired)
    n = await downgrade_expired_users()
    assert n >= 1
    u = await get_user(2)
    assert u.tier == TIER_FREE


@pytest.mark.asyncio
async def test_idempotent_activation(db):
    from database.queries import create_payment, activate_subscription, get_user

    await create_user(3)
    payment = await create_payment(3, "stars", TIER_MONTHLY, 50, "stars")
    first = await activate_subscription(payment.id)
    second = await activate_subscription(payment.id)
    assert first is True
    assert second is False
    u = await get_user(3)
    assert u.tier == TIER_MONTHLY
