"""Card-to-card (manual) payment handlers: instructions, tracking number, admin notice."""

from __future__ import annotations

import logging
import re

from aiogram import BaseMiddleware, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.keyboards.inline import admin_approval_keyboard
from bot.utils.messages import (
    ADMIN_NEW_PAYMENT,
    CARD_PAYMENT_INSTRUCTIONS,
    GENERIC_ERROR,
    PAYMENT_COMING_SOON,
    PENDING_EXISTS,
    TRACKING_INVALID,
    TRACKING_RECEIVED,
    tier_label,
)
from config import ADMIN_IDS, PACKAGE_PRICES_IRR
from database.models import Payment, User
from database.queries import create_manual_payment, get_setting

logger = logging.getLogger(__name__)

router = Router(name="card_payment")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# 4-64 chars, letters/digits/dashes only (no spaces).
TRACKING_RE = re.compile(r"^[A-Za-z0-9-]{4,64}$")

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class CardPaymentStates(StatesGroup):
    awaiting_tracking = State()


def normalize_tracking_number(text: str) -> str:
    """Normalize Persian/Arabic-Indic digits to ASCII and strip outer spaces."""
    return text.translate(_PERSIAN_DIGITS).strip()


def is_valid_tracking_number(text: str) -> bool:
    """True for 4-64 char alphanumeric/dash tracking numbers (no spaces)."""
    if not text or text != text.strip() or " " in text:
        return False
    return bool(TRACKING_RE.match(text))


async def get_card_details() -> tuple[str, str]:
    """Card number/holder from settings, falling back to the .env config."""
    from config import CARD_HOLDER, CARD_NUMBER

    number = (await get_setting("card_number")) or CARD_NUMBER
    holder = (await get_setting("card_holder")) or CARD_HOLDER
    return number, holder


def format_amount(amount: int) -> str:
    """IRR amounts with thousand separators."""
    return f"{amount:,}"


def build_admin_payment_text(payment: Payment, user: User | None) -> str:
    """Admin-facing summary of a manual payment (with approval buttons)."""
    username = (user.username if user and user.username else "—")
    full_name = (user.full_name if user and user.full_name else "—")
    created = payment.created_at.strftime("%Y-%m-%d %H:%M") if payment.created_at else "—"
    return ADMIN_NEW_PAYMENT.format(
        full_name=full_name,
        username=username,
        user_id=payment.user_id,
        tier_label=tier_label(payment.tier),
        amount=format_amount(payment.amount),
        tracking_number=payment.tracking_number or "—",
        created_at=created,
        payment_id=payment.id,
    )


class TrackingCancelMiddleware(BaseMiddleware):
    """Cancel a pending tracking-number flow when the user sends a URL or a command.

    Registered on the dispatcher so it runs before the routers: the command or
    URL is NOT consumed here, it is handled normally afterwards.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict):
        state: FSMContext | None = data.get("state")
        text = getattr(event, "text", None)
        if state is not None and text:
            try:
                current = await state.get_state()
            except Exception:  # noqa: BLE001
                current = None
            if current == CardPaymentStates.awaiting_tracking.state and (
                text.startswith("/") or URL_RE.match(text.strip())
            ):
                await state.clear()
        return await handler(event, data)


@router.callback_query(F.data.startswith("pay:card:"))
async def on_card_payment(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    """Show card details and switch to the tracking-number state."""
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    target = parts[2]
    tier_key = "addon_2k" if target in {"addon", "addon_2k"} else target

    amount = PACKAGE_PRICES_IRR.get(tier_key)
    if amount is None:
        await callback.answer(PAYMENT_COMING_SOON, show_alert=True)
        return

    from database.queries import has_pending_manual

    if await has_pending_manual(db_user.user_id):
        await callback.answer(PENDING_EXISTS, show_alert=True)
        return

    card_number, card_holder = await get_card_details()
    if not card_number:
        await callback.answer(GENERIC_ERROR, show_alert=True)
        logger.warning("Card number is not configured")
        return

    await state.set_state(CardPaymentStates.awaiting_tracking)
    await state.update_data(tier=tier_key, amount=amount)

    text = CARD_PAYMENT_INSTRUCTIONS.format(
        tier_label=tier_label(tier_key),
        amount=format_amount(amount),
        card_number=card_number,
        card_holder=card_holder,
    )
    if callback.message is not None:
        await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


@router.message(CardPaymentStates.awaiting_tracking, F.text)
async def on_tracking_number(message: Message, state: FSMContext, db_user: User) -> None:
    """Receive and store the tracking number (state kept on invalid input)."""
    raw = (message.text or "").strip()
    tracking = normalize_tracking_number(raw)

    if not is_valid_tracking_number(tracking):
        # Keep the state so the user can simply retry.
        await message.answer(TRACKING_INVALID)
        return

    data = await state.get_data()
    tier_key = data.get("tier")
    amount = data.get("amount")
    if not tier_key or amount is None:
        await state.clear()
        await message.answer(GENERIC_ERROR)
        return

    payment = await create_manual_payment(
        user_id=db_user.user_id,
        tier=tier_key,
        amount=int(amount),
        tracking_number=tracking,
    )
    if payment is None:
        # Another pending payment appeared in the meantime.
        await state.clear()
        await message.answer(PENDING_EXISTS)
        return

    await state.clear()
    await message.answer(TRACKING_RECEIVED)

    from database.queries import get_user as _get_user

    user = await _get_user(db_user.user_id) or db_user
    text = build_admin_payment_text(payment, user)
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                text,
                parse_mode="Markdown",
                reply_markup=admin_approval_keyboard(payment),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    """Locked approval buttons: acknowledge silently."""
    await callback.answer()
