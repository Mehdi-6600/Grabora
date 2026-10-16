"""Subscription handlers (package picker, Stars invoices, card flow entry)."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.handlers.start import _show_subscription
from bot.keyboards.inline import payment_method_keyboard
from bot.utils.messages import (
    PAYMENT_COMING_SOON,
    PAYMENT_METHOD_PROMPT,
    STARS_INVOICE_SENT,
)
from database.models import User
from payments.stars import send_stars_invoice

router = Router(name="subscription")


@router.message(Command("subscription"))
async def cmd_subscription(message: Message, db_user: User) -> None:
    # Same body as the "اشتراک" reply-keyboard button (see bot/handlers/start.py).
    await _show_subscription(message, db_user)


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
