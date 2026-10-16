"""Reply keyboards.

The ``BTN_*`` constants below are the single source of truth for the reply
keyboards: they are used both to build the keyboard and to match incoming
message texts (Telegram sends reply-keyboard taps as plain text), so the two
can never drift apart.
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_DOWNLOAD = "🚀 دانلود"
BTN_SUBSCRIPTION = "اشتراک"
BTN_ACCOUNT = "👤 حساب من"
BTN_HELP = "راهنما"

# ---------------------------------------------------------------------------
# Admin panel
# ---------------------------------------------------------------------------

BTN_ADMIN_STATS = "📊 آمار"
BTN_ADMIN_PENDING = "💳 پرداخت‌های در انتظار"
BTN_ADMIN_PRICES = "💰 قیمت‌ها"
BTN_ADMIN_CARD = "💳 شماره کارت"
BTN_ADMIN_BROADCAST = "📢 پیام همگانی"
BTN_ADMIN_TEST_ALERT = "🔧 تست اطلاع‌رسانی"
BTN_ADMIN_EXIT = "❌ خروج از پنل"

# Every text the reply keyboard can send; used by handler filters.
REPLY_BUTTON_TEXTS: tuple[str, ...] = (
    BTN_DOWNLOAD,
    BTN_SUBSCRIPTION,
    BTN_ACCOUNT,
    BTN_HELP,
)

# Every text the admin keyboard can send.
ADMIN_BUTTON_TEXTS: tuple[str, ...] = (
    BTN_ADMIN_STATS,
    BTN_ADMIN_PENDING,
    BTN_ADMIN_PRICES,
    BTN_ADMIN_CARD,
    BTN_ADMIN_BROADCAST,
    BTN_ADMIN_TEST_ALERT,
    BTN_ADMIN_EXIT,
)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DOWNLOAD), KeyboardButton(text=BTN_SUBSCRIPTION)],
            [KeyboardButton(text=BTN_ACCOUNT), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


def admin_menu() -> ReplyKeyboardMarkup:
    """Admin panel keyboard (one button per row, exit last)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADMIN_STATS)],
            [KeyboardButton(text=BTN_ADMIN_PENDING)],
            [KeyboardButton(text=BTN_ADMIN_PRICES)],
            [KeyboardButton(text=BTN_ADMIN_CARD)],
            [KeyboardButton(text=BTN_ADMIN_BROADCAST)],
            [KeyboardButton(text=BTN_ADMIN_TEST_ALERT)],
            [KeyboardButton(text=BTN_ADMIN_EXIT)],
        ],
        resize_keyboard=True,
    )
