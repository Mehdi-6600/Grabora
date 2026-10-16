"""Reply keyboards.

The ``BTN_*`` constants below are the single source of truth for the reply
keyboard: they are used both to build the keyboard and to match incoming
message texts (Telegram sends reply-keyboard taps as plain text), so the two
can never drift apart.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_DOWNLOAD = "🚀 دانلود"
BTN_SUBSCRIPTION = "اشتراک"
BTN_ACCOUNT = "👤 حساب من"
BTN_HELP = "راهنما"

# Every text the reply keyboard can send; used by handler filters.
REPLY_BUTTON_TEXTS: tuple[str, ...] = (
    BTN_DOWNLOAD,
    BTN_SUBSCRIPTION,
    BTN_ACCOUNT,
    BTN_HELP,
)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DOWNLOAD), KeyboardButton(text=BTN_SUBSCRIPTION)],
            [KeyboardButton(text=BTN_ACCOUNT), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )
