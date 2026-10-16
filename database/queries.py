"""Database query helpers for Grabora."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Sequence

import aiosqlite

from config import ADDON_2K_TIER, TIER_FREE
from database.db import get_db
from database.models import DownloadLog, Payment, PaymentStatus, User

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def get_user(user_id: int) -> User | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return User.from_row(row)


async def create_user(
    user_id: int,
    username: str | None = None,
    full_name: str | None = None,
    daily_reset_at: datetime | None = None,
) -> User:
    now = _now()
    reset_at = daily_reset_at or now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    db = get_db()
    await db.execute(
        """
        INSERT INTO users (
            user_id, username, full_name, tier, daily_downloads,
            daily_reset_at, is_banned, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 0, ?, 0, ?, ?)
        """,
        (user_id, username, full_name, TIER_FREE, _iso(reset_at), _iso(now), _iso(now)),
    )
    await db.commit()
    user = await get_user(user_id)
    if user is None:
        raise RuntimeError("Failed to create user")
    return user


async def get_or_create_user(
    user_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> User:
    user = await get_user(user_id)
    if user is not None:
        # Keep profile fresh.
        db = get_db()
        await db.execute(
            "UPDATE users SET username = ?, full_name = ?, updated_at = ? WHERE user_id = ?",
            (username, full_name, _iso(_now()), user_id),
        )
        await db.commit()
        return await get_user(user_id) or user
    return await create_user(user_id, username, full_name)


async def set_banned(user_id: int, banned: bool) -> None:
    db = get_db()
    await db.execute(
        "UPDATE users SET is_banned = ?, updated_at = ? WHERE user_id = ?",
        (1 if banned else 0, _iso(_now()), user_id),
    )
    await db.commit()


async def set_user_tier(
    user_id: int,
    tier: str,
    expires_at: datetime | None,
    has_2k_addon: bool = False,
    addon_expires_at: datetime | None = None,
) -> None:
    db = get_db()
    await db.execute(
        """
        UPDATE users
        SET tier = ?, tier_expires_at = ?, has_2k_addon = ?,
            addon_expires_at = ?, reminder_sent_for_expiry = NULL, updated_at = ?
        WHERE user_id = ?
        """,
        (
            tier,
            _iso(expires_at),
            1 if has_2k_addon else 0,
            _iso(addon_expires_at),
            _iso(_now()),
            user_id,
        ),
    )
    await db.commit()


async def increment_daily_downloads(user_id: int, amount: int = 1) -> int:
    db = get_db()
    await db.execute(
        "UPDATE users SET daily_downloads = daily_downloads + ?, updated_at = ? WHERE user_id = ?",
        (amount, _iso(_now()), user_id),
    )
    await db.commit()
    user = await get_user(user_id)
    return user.daily_downloads if user else 0


async def set_reminder_sent(user_id: int, expiry: datetime | None) -> None:
    db = get_db()
    await db.execute(
        "UPDATE users SET reminder_sent_for_expiry = ?, updated_at = ? WHERE user_id = ?",
        (_iso(expiry), _iso(_now()), user_id),
    )
    await db.commit()


async def reset_daily_counter(user_id: int, reset_at: datetime) -> None:
    db = get_db()
    await db.execute(
        "UPDATE users SET daily_downloads = 0, daily_reset_at = ?, updated_at = ? WHERE user_id = ?",
        (_iso(reset_at), _iso(_now()), user_id),
    )
    await db.commit()


async def reset_daily_counter_if_due(user_id: int, now: datetime | None = None) -> bool:
    """Reset a single user's daily counter if its reset time has passed."""
    now = now or _now()
    next_reset = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    db = get_db()
    cursor = await db.execute(
        "UPDATE users SET daily_downloads = 0, daily_reset_at = ?, updated_at = ? "
        "WHERE user_id = ? AND daily_reset_at <= ?",
        (_iso(next_reset), _iso(now), user_id, _iso(now)),
    )
    await db.commit()
    return (cursor.rowcount or 0) > 0



async def reset_all_daily_counters(now: datetime | None = None) -> int:
    """Reset counters whose reset time has passed. Returns number updated."""
    now = now or _now()
    db = get_db()
    cursor = await db.execute(
        "UPDATE users SET daily_downloads = 0, daily_reset_at = ? WHERE daily_reset_at <= ?",
        (_iso(now + timedelta(days=1)), _iso(now)),
    )
    await db.commit()
    return cursor.rowcount or 0


async def get_expired_subscriptions(now: datetime | None = None) -> Sequence[User]:
    now = now or _now()
    db = get_db()
    async with db.execute(
        "SELECT * FROM users WHERE tier != ? AND tier_expires_at IS NOT NULL AND tier_expires_at <= ?",
        (TIER_FREE, _iso(now)),
    ) as cursor:
        rows = await cursor.fetchall()
    return [User.from_row(r) for r in rows]


async def get_expired_addons(now: datetime | None = None) -> Sequence[User]:
    now = now or _now()
    db = get_db()
    async with db.execute(
        "SELECT * FROM users WHERE has_2k_addon = 1 AND addon_expires_at IS NOT NULL AND addon_expires_at <= ?",
        (_iso(now),),
    ) as cursor:
        rows = await cursor.fetchall()
    return [User.from_row(r) for r in rows]


async def get_users_expiring_in_days(days: int = 3, now: datetime | None = None) -> Sequence[User]:
    """Users whose paid tier expires within [days-0.5, days+0.5] days."""
    now = now or _now()
    lower = now + timedelta(days=days - 0.5)
    upper = now + timedelta(days=days + 0.5)
    db = get_db()
    async with db.execute(
        "SELECT * FROM users WHERE tier != ? AND tier_expires_at IS NOT NULL",
        (TIER_FREE,),
    ) as cursor:
        rows = await cursor.fetchall()
    result: list[User] = []
    for r in rows:
        u = User.from_row(r)
        if u.tier_expires_at is not None and lower <= u.tier_expires_at <= upper:
            result.append(u)
    return result


async def get_stats_summary() -> dict[str, int]:
    db = get_db()
    async with db.execute("SELECT COUNT(*) AS c FROM users") as cursor:
        total_users = int((await cursor.fetchone())["c"])
    async with db.execute("SELECT COUNT(*) AS c FROM users WHERE tier != 'free'") as cursor:
        paid_users = int((await cursor.fetchone())["c"])
    async with db.execute(
        "SELECT COALESCE(SUM(daily_downloads), 0) AS c FROM users"
    ) as cursor:
        downloads_today = int((await cursor.fetchone())["c"])
    async with db.execute(
        "SELECT COUNT(*) AS c FROM payments WHERE status = ?",
        (PaymentStatus.VERIFIED.value,),
    ) as cursor:
        verified_payments = int((await cursor.fetchone())["c"])
    return {
        "total_users": total_users,
        "paid_users": paid_users,
        "downloads_today": downloads_today,
        "verified_payments": verified_payments,
    }


async def get_setting(key: str) -> str | None:
    db = get_db()
    async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row["value"]


async def set_setting(key: str, value: str) -> None:
    db = get_db()
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await db.commit()


async def seed_settings(card_number: str, card_holder: str) -> None:
    db = get_db()
    await db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("card_number", card_number),
    )
    await db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("card_holder", card_holder),
    )
    await db.commit()


async def get_pending_manual_payments() -> list[Payment]:
    db = get_db()
    async with db.execute(
        "SELECT * FROM payments WHERE status = ? ORDER BY created_at ASC",
        (PaymentStatus.PENDING_MANUAL.value,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [Payment.from_row(r) for r in rows]


async def has_pending_manual(user_id: int) -> bool:
    db = get_db()
    async with db.execute(
        "SELECT 1 FROM payments WHERE user_id = ? AND status = ? LIMIT 1",
        (user_id, PaymentStatus.PENDING_MANUAL.value),
    ) as cursor:
        row = await cursor.fetchone()
    return row is not None


async def get_payment_by_tracking_number(tracking_number: str) -> Payment | None:
    """Look up an accepted payment by tracking number.

    Only 'pending_manual' and 'verified' rows are considered, so rejected or
    failed attempts do not count as duplicates. SQL NULL comparison means a
    row without a tracking number can never match.
    """
    db = get_db()
    async with db.execute(
        "SELECT * FROM payments WHERE tracking_number = ? AND status IN (?, ?) LIMIT 1",
        (
            tracking_number,
            PaymentStatus.PENDING_MANUAL.value,
            PaymentStatus.VERIFIED.value,
        ),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return Payment.from_row(row)


async def reject_payment(payment_id: int) -> None:
    payment = await get_payment_by_id(payment_id)
    if payment is None or payment.status != PaymentStatus.PENDING_MANUAL.value:
        return
    await update_payment_status(payment_id, PaymentStatus.REJECTED.value)


async def downgrade_expired_users(now: datetime | None = None) -> int:
    """Downgrade expired paid tiers/addons. Returns number affected."""
    now = now or _now()
    count = 0
    for user in await get_expired_subscriptions(now):
        await set_user_tier(user.user_id, TIER_FREE, None, False, None)
        count += 1
    for user in await get_expired_addons(now):
        # Keep tier, remove addon.
        await set_user_tier(
            user.user_id, user.tier, user.tier_expires_at, False, None
        )
        count += 1
    return count


async def list_all_user_ids() -> list[int]:
    db = get_db()
    async with db.execute("SELECT user_id FROM users") as cursor:
        rows = await cursor.fetchall()
    return [int(r["user_id"]) for r in rows]


# ---------------------------------------------------------------------------
# Package prices (admin-editable, seeded from config defaults)
# ---------------------------------------------------------------------------


def _default_price(package_key: str) -> tuple[int, int]:
    """Seed/default price from config, used when the DB has no row."""
    from config import PACKAGE_PRICES_IRR, PACKAGE_PRICES_STARS

    if package_key not in PACKAGE_PRICES_IRR or package_key not in PACKAGE_PRICES_STARS:
        raise KeyError(package_key)
    return int(PACKAGE_PRICES_IRR[package_key]), int(PACKAGE_PRICES_STARS[package_key])


async def get_price(package_key: str) -> tuple[int, int]:
    """Return ``(price_irr, price_stars)`` for a package.

    Looks the package up in the ``prices`` table and falls back to the
    config defaults when no row exists. Raises ``KeyError`` for a package
    key that is not configured at all.
    """
    _default_price(package_key)  # validates the key
    db = get_db()
    async with db.execute(
        "SELECT price_irr, price_stars FROM prices WHERE package_key = ?",
        (package_key,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        logger.debug("No stored price for %s; using config default", package_key)
        return _default_price(package_key)
    return int(row["price_irr"]), int(row["price_stars"])


async def get_all_prices() -> dict[str, tuple[int, int]]:
    """Return ``{package_key: (irr, stars)}`` for every configured package."""
    from config import PACKAGE_KEYS

    prices: dict[str, tuple[int, int]] = {}
    db = get_db()
    async with db.execute(
        "SELECT package_key, price_irr, price_stars FROM prices"
    ) as cursor:
        rows = await cursor.fetchall()
    stored = {
        str(r["package_key"]): (int(r["price_irr"]), int(r["price_stars"])) for r in rows
    }
    # Only the configured packages are sold, so this dict is authoritative.
    for package_key in PACKAGE_KEYS:
        prices[package_key] = stored.get(package_key) or _default_price(package_key)
    return prices


async def set_price(package_key: str, irr: int, stars: int) -> None:
    """Insert or update a package price."""
    from config import PACKAGE_KEYS

    if package_key not in PACKAGE_KEYS:
        raise KeyError(package_key)
    irr = int(irr)
    stars = int(stars)
    now = _iso(_now())
    db = get_db()
    await db.execute(
        "INSERT INTO prices (package_key, price_irr, price_stars, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(package_key) DO UPDATE SET "
        "price_irr = excluded.price_irr, "
        "price_stars = excluded.price_stars, "
        "updated_at = excluded.updated_at",
        (package_key, irr, stars, now),
    )
    await db.commit()
    logger.info("Price for %s set to %d IRR / %d Stars", package_key, irr, stars)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


async def create_payment(
    user_id: int,
    provider: str,
    tier: str,
    amount: int,
    currency: str,
    authority: str | None = None,
    is_addon: bool = False,
    metadata: str | None = None,
    tracking_number: str | None = None,
    transaction_id: str | None = None,
) -> Payment:
    now = _now()
    db = get_db()
    cursor = await db.execute(
        """
        INSERT INTO payments (
            user_id, provider, tier, amount, currency, status,
            authority, is_addon, created_at, metadata, tracking_number,
            transaction_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            provider,
            tier,
            amount,
            currency,
            PaymentStatus.PENDING.value,
            authority,
            1 if is_addon else 0,
            _iso(now),
            metadata,
            tracking_number,
            transaction_id,
        ),
    )
    await db.commit()
    payment_id = cursor.lastrowid
    payment = await get_payment_by_id(int(payment_id)) if payment_id is not None else None
    if payment is None:
        raise RuntimeError("Failed to create payment")
    return payment


async def create_manual_payment(
    user_id: int,
    tier: str,
    amount: int,
    tracking_number: str,
    provider: str = "card",
    currency: str = "IRR",
    is_addon: bool | None = None,
) -> Payment | None:
    """Create a card-to-card payment awaiting admin approval.

    Returns ``None`` (without inserting anything) when the user already has a
    payment in ``pending_manual`` state — only one is allowed at a time.
    """
    if await has_pending_manual(user_id):
        logger.info("User %s already has a pending manual payment", user_id)
        return None
    if is_addon is None:
        is_addon = tier == ADDON_2K_TIER
    now = _now()
    db = get_db()
    cursor = await db.execute(
        """
        INSERT INTO payments (
            user_id, provider, tier, amount, currency, status,
            is_addon, created_at, tracking_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            provider,
            tier,
            amount,
            currency,
            PaymentStatus.PENDING_MANUAL.value,
            1 if is_addon else 0,
            _iso(now),
            tracking_number,
        ),
    )
    await db.commit()
    payment_id = cursor.lastrowid
    if payment_id is None:
        raise RuntimeError("Failed to create manual payment")
    payment = await get_payment_by_id(int(payment_id))
    if payment is None:
        raise RuntimeError("Failed to create manual payment")
    return payment


async def get_payment_by_id(payment_id: int) -> Payment | None:
    db = get_db()
    async with db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return Payment.from_row(row)


async def get_payment_by_transaction_id(transaction_id: str) -> Payment | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM payments WHERE transaction_id = ?", (transaction_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return Payment.from_row(row)


async def get_payment_by_authority(authority: str) -> Payment | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM payments WHERE authority = ?", (authority,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return Payment.from_row(row)


async def update_payment_status(
    payment_id: int,
    status: str,
    transaction_id: str | None = None,
    verified_at: datetime | None = None,
) -> None:
    db = get_db()
    await db.execute(
        """
        UPDATE payments
        SET status = ?,
            transaction_id = COALESCE(?, transaction_id),
            verified_at = COALESCE(?, verified_at)
        WHERE id = ?
        """,
        (status, transaction_id, _iso(verified_at), payment_id),
    )
    await db.commit()


async def get_recent_payments(limit: int = 20) -> list[Payment]:
    db = get_db()
    async with db.execute(
        "SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,)
    ) as cursor:
        rows = await cursor.fetchall()
    return [Payment.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Download logs
# ---------------------------------------------------------------------------


async def log_download(
    user_id: int,
    url_domain: str | None,
    media_type: str | None,
    quality: str | None,
    status: str,
    file_size_mb: float | None = None,
    error_message: str | None = None,
) -> None:
    db = get_db()
    await db.execute(
        """
        INSERT INTO download_logs (
            user_id, url_domain, media_type, quality, status,
            file_size_mb, error_message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, url_domain, media_type, quality, status, file_size_mb, error_message, _iso(_now())),
    )
    await db.commit()


async def count_failures_last_hour() -> int:
    since = _now() - timedelta(hours=1)
    db = get_db()
    async with db.execute(
        "SELECT COUNT(*) AS c FROM download_logs WHERE status = 'failed' AND created_at >= ?",
        (_iso(since),),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------------------
# Subscription activation (idempotent)
# ---------------------------------------------------------------------------


async def activate_subscription(payment_id: int) -> bool:
    """Grant the purchased tier/addon for a payment. Returns True if newly activated.

    Shared by both payment paths: Telegram Stars (``pending``) and card-to-card
    (``pending_manual``). Idempotent — a payment that is already ``verified``
    (or rejected/failed) is never granted twice.
    """
    payment = await get_payment_by_id(payment_id)
    if payment is None:
        return False
    if payment.status not in (
        PaymentStatus.PENDING.value,
        PaymentStatus.PENDING_MANUAL.value,
    ):
        # Already processed (idempotent).
        return False

    await update_payment_status(
        payment_id, PaymentStatus.VERIFIED.value, verified_at=_now()
    )

    user = await get_user(payment.user_id)
    if user is None:
        await create_user(payment.user_id)

    if payment.is_addon:
        base = user.addon_expires_at if (user and user.addon_expires_at and user.addon_expires_at > _now()) else _now()
        from config import ADDON_2K_DURATION_DAYS

        new_expiry = base + timedelta(days=ADDON_2K_DURATION_DAYS)
        current_tier = user.tier if user else TIER_FREE
        current_expiry = user.tier_expires_at if user else None
        await set_user_tier(
            payment.user_id, current_tier, current_expiry, True, new_expiry
        )
        return True

    from config import TIER_DURATIONS_DAYS

    days = TIER_DURATIONS_DAYS.get(payment.tier, 0)
    if days <= 0:
        return True

    user_expiry = user.tier_expires_at if user else None
    base = user_expiry if (user_expiry is not None and user_expiry > _now()) else _now()
    new_expiry = base + timedelta(days=days)
    has_addon = user.has_2k_addon if user else False
    addon_expiry = user.addon_expires_at if user else None
    await set_user_tier(payment.user_id, payment.tier, new_expiry, has_addon, addon_expiry)
    return True

