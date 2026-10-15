"""Inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.messages import (
    BTN_ADDON_2K,
    BTN_MONTHLY,
    BTN_QUARTERLY,
    BTN_YEARLY,
    tier_label,
)

from config import ADDON_2K_TIER, PACKAGE_KEYS

# Locked buttons all share this callback; a single no-op handler answers them.
NOOP_CALLBACK = "noop"


def quality_keyboard(is_paid: bool, has_2k: bool) -> InlineKeyboardMarkup:
    """Quality selection keyboard with locked options for free users."""
    video_row1 = [
        InlineKeyboardButton(text="144p", callback_data="q:video:144"),
        InlineKeyboardButton(text="240p", callback_data="q:video:240"),
        InlineKeyboardButton(text="360p", callback_data="q:video:360"),
    ]
    video_row2 = [
        InlineKeyboardButton(text="480p", callback_data="q:video:480"),
    ]
    if is_paid:
        video_row2 += [
            InlineKeyboardButton(text="720p", callback_data="q:video:720"),
            InlineKeyboardButton(text="1080p", callback_data="q:video:1080"),
        ]
    else:
        video_row2 += [
            InlineKeyboardButton(text="🔒720p", callback_data="q:locked:720"),
            InlineKeyboardButton(text="🔒1080p", callback_data="q:locked:1080"),
        ]
    if has_2k:
        video_row2.append(InlineKeyboardButton(text="💎2K", callback_data="q:video:1440"))
    else:
        video_row2.append(InlineKeyboardButton(text="🔒2K", callback_data="q:locked:2k"))

    audio_row1 = [
        InlineKeyboardButton(text="128kbps", callback_data="q:audio:128"),
        InlineKeyboardButton(text="192kbps", callback_data="q:audio:192"),
    ]
    if is_paid:
        audio_row1 += [
            InlineKeyboardButton(text="320kbps", callback_data="q:audio:320"),
            InlineKeyboardButton(text="FLAC", callback_data="q:audio:flac"),
        ]
    else:
        audio_row1 += [
            InlineKeyboardButton(text="🔒320kbps", callback_data="q:locked:320"),
            InlineKeyboardButton(text="🔒FLAC", callback_data="q:locked:flac"),
        ]

    rows = [video_row1, video_row2, audio_row1]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=BTN_MONTHLY, callback_data="sub:monthly"),
                InlineKeyboardButton(text=BTN_QUARTERLY, callback_data="sub:quarterly"),
            ],
            [InlineKeyboardButton(text=BTN_YEARLY, callback_data="sub:yearly")],
            [InlineKeyboardButton(text=BTN_ADDON_2K, callback_data="sub:addon2k")],
        ]
    )


def payment_method_keyboard(tier_or_addon: str) -> InlineKeyboardMarkup:
    """Choose the payment provider for a tier/addon."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ پرداخت با استارز",
                    callback_data=f"pay:stars:{tier_or_addon}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 پرداخت با کارت",
                    callback_data=f"pay:card:{tier_or_addon}",
                )
            ],
        ]
    )


def admin_approval_keyboard(payment) -> InlineKeyboardMarkup:
    """Four package buttons for a manual payment; only the matching one is active.

    ``payment.tier`` is one of monthly | quarterly | yearly | addon_2k (legacy
    rows use "addon", which maps to addon_2k). Every other button is rendered
    locked with callback_data="noop".
    """
    tier = getattr(payment, "tier", "")
    is_addon = bool(getattr(payment, "is_addon", False))
    if is_addon or tier in {"addon", ADDON_2K_TIER}:
        active_key = ADDON_2K_TIER
    else:
        active_key = tier
    payment_id = getattr(payment, "id", 0)

    def button(key: str) -> InlineKeyboardButton:
        if key == active_key:
            return InlineKeyboardButton(
                text=tier_label(key),
                callback_data=f"admin_approve:{payment_id}:{key}",
            )
        return InlineKeyboardButton(text=f"🔒 {tier_label(key)}", callback_data=NOOP_CALLBACK)

    ordered = [k for k in PACKAGE_KEYS]
    row1 = [button(ordered[0]), button(ordered[1])]
    row2 = [button(ordered[2]), button(ordered[3])]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])
