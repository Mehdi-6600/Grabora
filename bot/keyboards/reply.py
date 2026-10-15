"""Reply keyboards."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.utils.messages import BTN_HELP, BTN_SUBSCRIPTION


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SUBSCRIPTION), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )
