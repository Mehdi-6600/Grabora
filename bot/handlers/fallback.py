"""Last-resort handlers: nothing reaches the end of the chain unnoticed.

Included **last** in ``main.build_dispatcher``. Any update that no other router
claimed lands here, is logged at ``WARNING`` with enough detail to identify it
(user, chat, content type, text preview, whether it looked like a command) and
is answered instead of being silently ignored — silence is what made the
"the bot is dead" outage so hard to diagnose.

The throttle still applies to plain text, so a user cannot use this to spam the
bot; navigation commands and buttons are answered by their own routers earlier
in the chain.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from bot.keyboards.reply import main_menu
from bot.utils.messages import FALLBACK_CALLBACK_TEXT, FALLBACK_TEXT

logger = logging.getLogger(__name__)

router = Router(name="fallback")


def _describe(message: Message) -> str:
    """Short, log-friendly description of an unmatched message."""
    content_type = getattr(message, "content_type", None)
    text = (message.text or message.caption or "").replace("\n", " ")
    parts = [
        f"content_type={content_type}",
        f"text={text[:120]!r}",
        f"has_command={bool(message.text and message.text.startswith('/'))}",
    ]
    if message.photo:
        parts.append("photo=yes")
    if message.document:
        parts.append(f"document={message.document.file_name!r}")
    return " ".join(parts)


@router.message()
async def on_unmatched_message(message: Message) -> None:
    """Log and answer a message that no handler claimed."""
    logger.warning(
        "UNMATCHED message from user=%s chat=%s (%s) — replying with the fallback text",
        message.from_user.id if message.from_user else None,
        message.chat.id if message.chat else None,
        _describe(message),
    )
    try:
        await message.answer(FALLBACK_TEXT, reply_markup=main_menu())
    except Exception as exc:  # noqa: BLE001 - never crash on a diagnostic reply
        logger.error("Failed to send the fallback reply: %s", exc)


@router.callback_query()
async def on_unmatched_callback(callback: CallbackQuery) -> None:
    """Log and acknowledge a callback no handler claimed (stops the spinner)."""
    logger.warning(
        "UNMATCHED callback from user=%s data=%r",
        callback.from_user.id if callback.from_user else None,
        callback.data,
    )
    try:
        await callback.answer(FALLBACK_CALLBACK_TEXT)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to answer the fallback callback: %s", exc)
