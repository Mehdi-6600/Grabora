"""Subscription handlers (package picker, Stars invoices, card flow entry)."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.keyboards.inline import payment_method_keyboard, subscription_keyboard
from bot.utils.messages import (
    PAYMENT_COMING_SOON,
    PAYMENT_METHOD_PROMPT,
    STARS_INVOICE_SENT,
    SUBSCRIPTION_INFO,
    UNLIMITED,
)
from config import TIER_DAILY_LIMITS
from database.models import User
from payments.stars import send_stars_invoice

router = Router(name="subscription")


def _limit_text(limit: int) -> str:
    return UNLIMITED if limit == -1 else str(limit)


@router.message(Command("subscription"))
async def cmd_subscription(message: Message, db_user: User) -> None:
    used = db_user.daily_downloads
    limit = TIER_DAILY_LIMITS.get(db_user.tier, 3)
    await message.answer(
        SUBSCRIPTION_INFO.format(tier=db_user.tier, used=used, limit=_limit_text(limit)),
        reply_markup=subscription_keyboard(),
    )


@router.callback_query(F.data.startswith("sub:"))
async def on_subscription_choice(callback: CallbackQuery) -> None:
    choice = (callback.data or "").split(":", 1)[1]
    if choice == "addon2k":
        target = "addon"
    else:
        target = choice  # monthly/quarterly/yearly
    if callback.message is not None:
        await callback.message.answer(
            PAYMENT_METHOD_PROMPT,
            reply_markup=payment_method_keyboard(target),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("pay:stars:"))
async def on_payment_choice(callback: CallbackQuery, db_user: User) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    target = parts[2]
    stars_stage = "addon_2k" if target == "addon" else target

    ok = await send_stars_invoice(db_user.user_id, stars_stage)
    if not ok:
        await callback.answer(PAYMENT_COMING_SOON, show_alert=True)
        return
    await callback.answer(STARS_INVOICE_SENT)
