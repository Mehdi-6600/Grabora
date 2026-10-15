"""Telegram Stars payment integration (neonpay)."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot

from config import (
    ADDON_2K_TIER,
    ADMIN_IDS,
    PACKAGE_PRICES_STARS,
    TIER_DAILY_LIMITS,
    TIER_MONTHLY,
    TIER_QUARTERLY,
    TIER_YEARLY,
)
from database.queries import (
    activate_subscription,
    create_payment,
    get_payment_by_transaction_id,
    get_user,
)
from bot.utils.messages import (
    ADMIN_PAYMENT_NOTICE,
    PAYMENT_SUCCESS,
    PAYMENT_SUCCESS_ADDON,
    STAGE_ADDON_2K,
    STAGE_MONTHLY,
    STAGE_QUARTERLY,
    STAGE_YEARLY,
    UNLIMITED,
)
from neonpay import PaymentResult, PaymentStage, create_neonpay

logger = logging.getLogger(__name__)

_neonpay: Any = None
_bot: Any = None
_on_payment_callback: Callable[[PaymentResult], Awaitable[None]] | None = None


def init_stars(bot: Bot, dispatcher: Any) -> Any:
    """Create neonpay instance (sync)."""
    global _neonpay, _bot
    _bot = bot
    try:
        _neonpay = create_neonpay(bot, dispatcher=dispatcher)
        logger.info("Neonpay (Stars) instance created")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to create neonpay: %s", exc)
        _neonpay = None
    return _neonpay


async def setup_stars(
    on_payment: Callable[[PaymentResult], Awaitable[None]] | None = None,
) -> None:
    """Setup neonpay stages and register the payment callback.

    NOTE: user/admin notifications are sent from ``_handle_stars_payment``
    using the formatted PAYMENT_SUCCESS / PAYMENT_SUCCESS_ADDON templates.
    ``on_payment`` is only an optional extra hook and must not re-send them.
    """
    global _on_payment_callback
    if _neonpay is None:
        logger.warning("Neonpay not initialized")
        return
    _on_payment_callback = on_payment

    stages = {
        "monthly": (TIER_MONTHLY, PACKAGE_PRICES_STARS["monthly"], False, STAGE_MONTHLY),
        "quarterly": (TIER_QUARTERLY, PACKAGE_PRICES_STARS["quarterly"], False, STAGE_QUARTERLY),
        "yearly": (TIER_YEARLY, PACKAGE_PRICES_STARS["yearly"], False, STAGE_YEARLY),
        "addon_2k": (ADDON_2K_TIER, PACKAGE_PRICES_STARS["addon_2k"], True, STAGE_ADDON_2K),
    }
    for stage_id, (tier, price, is_addon, title) in stages.items():
        stage = PaymentStage(
            title=title[:32],
            description=f"Grabora subscription: {title}",
            price=price,
            payload={"tier": tier, "is_addon": is_addon},
        )
        try:
            _neonpay.create_payment_stage(stage_id, stage)
        except ValueError:
            # Already exists (re-setup).
            pass

    async def _callback(result: PaymentResult) -> None:
        await _handle_stars_payment(result)

    _neonpay.on_payment(_callback)
    await _neonpay.setup()
    logger.info("Neonpay (Stars) setup complete")


async def _handle_stars_payment(result: PaymentResult) -> None:
    if _neonpay is None:
        return
    stage = result.stage
    payload = (stage.payload if stage else {}) or {}
    tier = payload.get("tier", TIER_MONTHLY)
    is_addon = bool(payload.get("is_addon", False))
    db_tier = ADDON_2K_TIER if is_addon else tier

    # Idempotency by transaction id.
    existing = await get_payment_by_transaction_id(str(result.transaction_id)) if result.transaction_id else None
    if existing is not None:
        await activate_subscription(existing.id)
        return

    payment = await create_payment(
        user_id=result.user_id,
        provider="stars",
        tier=db_tier,
        amount=result.amount,
        currency="XTR",
        transaction_id=result.transaction_id,
        is_addon=is_addon,
    )
    activated = await activate_subscription(payment.id)
    if not activated:
        return

    user = await get_user(result.user_id)
    if is_addon:
        expiry = user.addon_expires_at.isoformat() if (user and user.addon_expires_at) else ""
        if _bot is not None:
            await _bot.send_message(result.user_id, PAYMENT_SUCCESS_ADDON.format(expiry=expiry))
    else:
        tier_name = user.tier if user else tier
        expiry = user.tier_expires_at.isoformat() if (user and user.tier_expires_at) else ""
        limit = TIER_DAILY_LIMITS.get(tier_name, 0)
        limit_text = UNLIMITED if limit == -1 else str(limit)
        if _bot is not None:
            await _bot.send_message(
                result.user_id,
                PAYMENT_SUCCESS.format(tier=tier_name, expiry=expiry, limit=limit_text),
            )

    if _bot is not None:
        for admin_id in ADMIN_IDS:
            try:
                await _bot.send_message(
                    admin_id,
                    ADMIN_PAYMENT_NOTICE.format(
                        user_id=result.user_id, provider="stars", tier=tier, amount=result.amount
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.debug("Failed to notify admin %s", admin_id)


def get_stars() -> Any:
    return _neonpay


async def send_stars_invoice(user_id: int, stage_id: str) -> bool:
    if _neonpay is None:
        return False
    try:
        return await _neonpay.send_payment(user_id, stage_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send Stars invoice: %s", exc)
        return False
