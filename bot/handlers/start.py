"""Start, help, account and reply-keyboard button handlers.

The reply keyboard sends plain text messages, so every button is routed here
by matching against the shared ``BTN_*`` constants from ``bot.keyboards.reply``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.keyboards.inline import subscription_keyboard
from bot.keyboards.reply import (
    BTN_ACCOUNT,
    BTN_DOWNLOAD,
    BTN_HELP,
    BTN_SUBSCRIPTION,
    main_menu,
)
from bot.utils.messages import (
    ACCOUNT_ADDON_ACTIVE,
    ACCOUNT_ADDON_INACTIVE,
    ACCOUNT_INFO,
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_FREE,
    DOWNLOAD_PROMPT,
    HELP,
    SUBSCRIPTION_INFO,
    UNLIMITED,
    WELCOME,
    tier_label,
)
from config import TIER_DAILY_LIMITS, TIER_FREE
from database.models import User

logger = logging.getLogger(__name__)

router = Router(name="start")


def _limit_text(limit: int) -> str:
    return UNLIMITED if limit == -1 else str(limit)


async def _show_subscription(message: Message, db_user: User) -> None:
    """Send the subscription overview (shared by /subscription and its button)."""
    used = db_user.daily_downloads
    limit = TIER_DAILY_LIMITS.get(db_user.tier, TIER_DAILY_LIMITS[TIER_FREE])
    await message.answer(
        SUBSCRIPTION_INFO.format(
            tier=tier_label(db_user.tier), used=used, limit=_limit_text(limit)
        ),
        reply_markup=subscription_keyboard(),
    )


async def _show_help(message: Message) -> None:
    """Send the help text (shared by /help and its button)."""
    await message.answer(HELP, reply_markup=main_menu())


def _expiry_text(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d")


def _account_text(db_user: User, now: datetime) -> str:
    is_paid = db_user.tier != TIER_FREE and (
        db_user.tier_expires_at is None or db_user.tier_expires_at > now
    )
    status = (
        ACCOUNT_STATUS_ACTIVE.format(expiry=_expiry_text(db_user.tier_expires_at))
        if is_paid
        else ACCOUNT_STATUS_FREE
    )
    addon_active = db_user.has_2k_addon and (
        db_user.addon_expires_at is None or db_user.addon_expires_at > now
    )
    addon_line = (
        ACCOUNT_ADDON_ACTIVE.format(expiry=_expiry_text(db_user.addon_expires_at))
        if addon_active
        else ACCOUNT_ADDON_INACTIVE
    )
    limit = TIER_DAILY_LIMITS.get(db_user.tier, TIER_DAILY_LIMITS[TIER_FREE])
    return ACCOUNT_INFO.format(
        user_id=db_user.user_id,
        tier=tier_label(db_user.tier),
        status=status,
        addon_line=addon_line,
        used=db_user.daily_downloads,
        limit=_limit_text(limit),
    )


async def _show_account(message: Message, db_user: User) -> None:
    """Send the user's account summary (tier, expiry, addon, daily usage)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await message.answer(
        _account_text(db_user, now),
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db_user: User) -> None:
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await _show_help(message)


# ---------------------------------------------------------------------------
# Reply keyboard buttons (Telegram delivers these as plain text)
# ---------------------------------------------------------------------------


@router.message(F.text == BTN_DOWNLOAD)
async def on_download_button(message: Message) -> None:
    await message.answer(DOWNLOAD_PROMPT, reply_markup=main_menu())


@router.message(F.text == BTN_SUBSCRIPTION)
async def on_subscription_button(message: Message, db_user: User) -> None:
    await _show_subscription(message, db_user)


@router.message(F.text == BTN_ACCOUNT)
async def on_account_button(message: Message, db_user: User) -> None:
    await _show_account(message, db_user)


@router.message(F.text == BTN_HELP)
async def on_help_button(message: Message) -> None:
    await _show_help(message)
