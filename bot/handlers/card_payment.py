"""Card-to-card (manual) payment handlers: instructions, tracking number, admin notice."""

from __future__ import annotations

import logging
import re

from aiogram import BaseMiddleware, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.keyboards.inline import admin_approval_keyboard
from bot.utils.admin_notify import escape_markdown, sanitize_code_span, send_to_admins
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
from config import PACKAGE_KEYS
from database.models import Payment, User
from database.queries import create_manual_payment, get_price, get_setting

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


def build_admin_payment_text(
    payment: Payment, user: User | None, markdown: bool = True
) -> str:
    """Admin-facing summary of a manual payment (with approval buttons).

    ``markdown=True`` escapes the user-controlled fields (name, username) so
    Telegram's parser cannot reject the message — an unbalanced ``_`` in a name
    used to make the whole notification undeliverable. Pass ``markdown=False``
    for a body that will be sent or edited without ``parse_mode``.
    """
    username = (user.username if user and user.username else "—")
    full_name = (user.full_name if user and user.full_name else "—")
    created = payment.created_at.strftime("%Y-%m-%d %H:%M") if payment.created_at else "—"
    if markdown:
        username = escape_markdown(username)
        full_name = escape_markdown(full_name)
    return ADMIN_NEW_PAYMENT.format(
        full_name=full_name,
        username=username,
        user_id=payment.user_id,
        tier_label=escape_markdown(tier_label(payment.tier)) if markdown else tier_label(payment.tier),
        amount=format_amount(payment.amount),
        # Inside a `code span`, so backticks must go rather than be escaped.
        tracking_number=sanitize_code_span(payment.tracking_number or "—"),
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

    if tier_key not in PACKAGE_KEYS:
        await callback.answer(PAYMENT_COMING_SOON, show_alert=True)
        return
    # Prices are admin-editable (see the /prices command), so read them live.
    amount, _stars = await get_price(tier_key)

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
    await notify_admins_new_payment(message.bot, payment, user)


async def notify_admins_new_payment(bot, payment: Payment, user: User | None) -> list:
    """Send the new-payment card to every admin (with approval buttons).

    Delivery is delegated to ``bot.utils.admin_notify.send_to_admins``: Markdown
    values are escaped, failures are logged at ERROR level, transient errors are
    retried once and the message is resent as plain text if Telegram rejects the
    formatting. Returns one result per admin so callers can report the outcome.
    """
    # Read the config lazily (like the other admin checks do) so a change to
    # ADMIN_IDS is always honoured.
    from config import ADMIN_IDS

    if not ADMIN_IDS:
        logger.error(
            "ADMIN_IDS is empty — payment #%s cannot be sent to any admin",
            payment.id,
        )
        return []
    return await send_to_admins(
        bot,
        build_admin_payment_text(payment, user),
        parse_mode="Markdown",
        # Fallback body: same content, no formatting, so a Markdown problem
        # degrades instead of losing the notification.
        plain_text=build_admin_payment_text(payment, user, markdown=False),
        reply_markup=admin_approval_keyboard(payment),
    )


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    """Locked approval buttons: acknowledge silently."""
    await callback.answer()
