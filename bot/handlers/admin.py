"""Admin handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.handlers.card_payment import build_admin_payment_text
from bot.keyboards.inline import admin_approval_keyboard
from bot.utils.messages import (
    ADMIN_APPROVED_USER,
    ADMIN_APPROVAL_PROMPT,
    ADMIN_DUPLICATE_TRACKING_WARNING,
    ADMIN_ONLY,
    ADMIN_PAYMENT_APPROVED,
    ADMIN_PAYMENT_REJECTED,
    CARD_CURRENT,
    CARD_UPDATED,
    CARD_USAGE,
    NO_PENDING_PAYMENTS,
    PAYMENT_NOT_FOUND,
    PAYMENT_REJECTED,
    PENDING_EXISTS,
    PRICES_CURRENT,
    PRICES_INVALID_AMOUNT,
    PRICES_INVALID_KEY,
    PRICES_ROW,
    PRICES_UPDATED,
    PRICES_USAGE,
    USAGE_REJECT,
    tier_label,
    BROADCAST_DONE,
    ENTER_BROADCAST_TEXT,
    INVALID_ID,
    INVALID_PARAMS,
    NO_PAYMENTS,
    RECENT_PAYMENTS,
    STATS_SUMMARY,
    SUBSCRIPTION_ACTIVATED,
    SUBSCRIPTION_REVOKED,
    USAGE_BAN,
    USAGE_GIVE,
    USAGE_REVOKE,
    USAGE_UNBAN,
    USER_BANNED,
    USER_UNBANNED,
)
from config import ADDON_2K_TIER, PACKAGE_KEYS, TIER_DURATIONS_DAYS
from database.models import Payment
from database.queries import (
    activate_subscription,
    get_all_prices,
    set_price,
    get_payment_by_id,
    get_payment_by_tracking_number,
    get_pending_manual_payments,
    get_setting,
    get_user,
    list_all_user_ids,
    reject_payment,
    set_banned,
    set_setting,
    set_user_tier,
    update_payment_status,
)

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    from config import ADMIN_IDS

    return user_id in ADMIN_IDS


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    from database.queries import get_recent_payments, get_stats_summary

    stats = await get_stats_summary()
    payments = await get_recent_payments(5)
    text = STATS_SUMMARY.format(
        users=stats["total_users"],
        paid=stats["paid_users"],
        downloads=stats["downloads_today"],
        payments=stats["verified_payments"],
        recent=len(payments),
    )
    await message.answer(text)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    text = (message.text or "").removeprefix("/broadcast").strip()
    if not text:
        await message.answer(ENTER_BROADCAST_TEXT)
        return
    from aiogram import Bot

    bot: Bot = message.bot  # type: ignore[assignment]
    ids = await list_all_user_ids()
    sent = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:  # noqa: BLE001
            logger.debug("Failed to send broadcast to %s", uid)
    await message.answer(BROADCAST_DONE.format(count=sent))


@router.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(USAGE_BAN)
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer(INVALID_ID)
        return
    await set_banned(uid, True)
    await message.answer(USER_BANNED)


@router.message(Command("unban"))
async def cmd_unban(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(USAGE_UNBAN)
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer(INVALID_ID)
        return
    await set_banned(uid, False)
    await message.answer(USER_UNBANNED)


@router.message(Command("give"))
async def cmd_give(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer(USAGE_GIVE)
        return
    try:
        uid = int(parts[1])
        tier = parts[2]
        days = int(parts[3])
    except ValueError:
        await message.answer(INVALID_PARAMS)
        return
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)
    await set_user_tier(uid, tier, expires, False, None)
    await message.answer(SUBSCRIPTION_ACTIVATED)


@router.message(Command("revoke"))
async def cmd_revoke(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(USAGE_REVOKE)
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer(INVALID_ID)
        return
    await set_user_tier(uid, "free", None, False, None)
    await message.answer(SUBSCRIPTION_REVOKED)


@router.message(Command("payments"))
async def cmd_payments(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    from database.queries import get_recent_payments

    payments = await get_recent_payments(20)
    lines = [f"{p.id} | {p.user_id} | {p.provider} | {p.tier} | {p.amount} | {p.status}" for p in payments]
    await message.answer("\n".join(lines) if lines else NO_PAYMENTS)


# ---------------------------------------------------------------------------
# Card-to-card (manual) payment admin flow
# ---------------------------------------------------------------------------


class ApprovalOutcome(str, Enum):
    """Result of an admin approval attempt."""

    APPROVED = "approved"
    NOT_FOUND = "not_found"
    NOT_PENDING = "not_pending"
    TIER_MISMATCH = "tier_mismatch"
    DUPLICATE_TRACKING = "duplicate_tracking"
    USER_PENDING_CONFLICT = "user_pending_conflict"


def _normalize_tier_key(tier: str, is_addon: bool = False) -> str:
    """Map a stored tier onto a package key (monthly|quarterly|yearly|addon_2k)."""
    if is_addon or tier == "addon":
        return ADDON_2K_TIER
    return tier


async def approve_manual_payment(
    payment_id: int, requested_tier: str
) -> tuple[ApprovalOutcome, Payment | None, Payment | None]:
    """Approve a pending manual payment.

    Returns ``(outcome, payment, other)`` where ``other`` is the conflicting
    payment for DUPLICATE_TRACKING. Never activates on suspicion — a duplicate
    tracking number only warns the admin.
    """
    payment = await get_payment_by_id(payment_id)
    if payment is None:
        return ApprovalOutcome.NOT_FOUND, None, None

    if payment.status != "pending_manual":
        # Already approved/rejected — never grant twice.
        return ApprovalOutcome.NOT_PENDING, payment, None

    if _normalize_tier_key(requested_tier) != _normalize_tier_key(payment.tier, payment.is_addon):
        # Admin pressed a button that does not match this payment.
        return ApprovalOutcome.TIER_MISMATCH, payment, None

    if payment.tracking_number:
        other = await get_payment_by_tracking_number(payment.tracking_number)
        if other is not None and other.id != payment.id:
            return ApprovalOutcome.DUPLICATE_TRACKING, payment, other

    # Defensive: another pending manual payment for the same user.
    pendings = await get_pending_manual_payments()
    if any(p.user_id == payment.user_id and p.id != payment.id for p in pendings):
        return ApprovalOutcome.USER_PENDING_CONFLICT, payment, None

    activated = await activate_subscription(payment.id)
    if not activated:
        return ApprovalOutcome.NOT_PENDING, payment, None
    await update_payment_status(
        payment.id,
        "verified",
        verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    return ApprovalOutcome.APPROVED, payment, None


@router.callback_query(F.data.startswith("admin_approve:"))
async def on_admin_approve(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    if not _is_admin(callback.from_user.id if callback.from_user else 0):
        await callback.answer(ADMIN_ONLY, show_alert=True)
        return

    try:
        payment_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    requested_tier = parts[2]

    outcome, payment, other = await approve_manual_payment(payment_id, requested_tier)

    if outcome is ApprovalOutcome.NOT_FOUND:
        await callback.answer(PAYMENT_NOT_FOUND, show_alert=True)
        return
    if outcome is ApprovalOutcome.NOT_PENDING:
        await callback.answer(ADMIN_APPROVED_USER.format(payment_id=payment_id), show_alert=True)
        return
    if outcome is ApprovalOutcome.TIER_MISMATCH:
        # Wrong button for this payment — ignore silently.
        await callback.answer()
        return
    if outcome is ApprovalOutcome.USER_PENDING_CONFLICT:
        await callback.answer(PENDING_EXISTS, show_alert=True)
        return
    if outcome is ApprovalOutcome.DUPLICATE_TRACKING:
        await callback.answer(
            ADMIN_DUPLICATE_TRACKING_WARNING.format(
                tracking_number=payment.tracking_number,
                other_payment_id=other.id if other else "?",
            ),
            show_alert=True,
        )
        return

    # Approved: notify the user and update the admin message.
    admin_username = (callback.from_user.username if callback.from_user else None) or "admin"
    await _notify_user_activated(callback, payment)
    await _mark_admin_message(callback, payment, approved=True, admin_username=admin_username)
    await callback.answer(ADMIN_APPROVED_USER.format(payment_id=payment.id))


async def _notify_user_activated(callback: CallbackQuery, payment: Payment) -> None:
    from bot.utils.messages import (
        PAYMENT_SUCCESS,
        PAYMENT_SUCCESS_ADDON,
        UNLIMITED,
    )

    user = await get_user(payment.user_id)
    try:
        if payment.is_addon:
            expiry = user.addon_expires_at.isoformat() if (user and user.addon_expires_at) else ""
            text = PAYMENT_SUCCESS_ADDON.format(expiry=expiry)
        else:
            tier_name = user.tier if user else payment.tier
            expiry = user.tier_expires_at.isoformat() if (user and user.tier_expires_at) else ""
            from config import TIER_DAILY_LIMITS

            limit = TIER_DAILY_LIMITS.get(tier_name, 0)
            text = PAYMENT_SUCCESS.format(
                tier=tier_name,
                expiry=expiry,
                limit=UNLIMITED if limit == -1 else str(limit),
            )
        await callback.bot.send_message(payment.user_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to notify user %s: %s", payment.user_id, exc)


async def _mark_admin_message(
    callback: CallbackQuery,
    payment: Payment,
    approved: bool,
    admin_username: str,
) -> None:
    if callback.message is None:
        return
    base = getattr(callback.message, "text", None) or build_admin_payment_text(
        payment, await get_user(payment.user_id)
    )
    suffix = (
        ADMIN_PAYMENT_APPROVED.format(admin_username=admin_username)
        if approved
        else ADMIN_PAYMENT_REJECTED.format(admin_username=admin_username)
    )
    try:
        await callback.message.edit_text(f"{base}\n\n{suffix}", reply_markup=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to edit admin message: %s", exc)


@router.message(Command("pending"))
async def cmd_pending(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    payments = await get_pending_manual_payments()
    if not payments:
        await message.answer(NO_PENDING_PAYMENTS)
        return
    await message.answer(ADMIN_APPROVAL_PROMPT)
    for payment in payments:
        user = await get_user(payment.user_id)
        await message.answer(
            build_admin_payment_text(payment, user),
            parse_mode="Markdown",
            reply_markup=admin_approval_keyboard(payment),
        )


@router.message(Command("card"))
async def cmd_card(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return

    from config import CARD_HOLDER, CARD_NUMBER

    parts = (message.text or "").split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ""

    if not args:
        number = (await get_setting("card_number")) or CARD_NUMBER
        holder = (await get_setting("card_holder")) or CARD_HOLDER
        await message.answer(
            CARD_CURRENT.format(card_number=number, card_holder=holder),
            parse_mode="Markdown",
        )
        return

    if "|" not in args:
        await message.answer(CARD_USAGE)
        return
    number, _, holder = args.partition("|")
    number = number.strip()
    holder = holder.strip()
    if not number or not holder:
        await message.answer(CARD_USAGE)
        return

    await set_setting("card_number", number)
    await set_setting("card_holder", holder)
    await message.answer(
        CARD_UPDATED.format(card_number=number, card_holder=holder),
        parse_mode="Markdown",
    )


@router.message(Command("reject"))
async def cmd_reject(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(USAGE_REJECT)
        return
    try:
        payment_id = int(parts[1])
    except ValueError:
        await message.answer(INVALID_ID)
        return

    payment = await get_payment_by_id(payment_id)
    if payment is None:
        await message.answer(PAYMENT_NOT_FOUND)
        return
    if payment.status != "pending_manual":
        await message.answer(PAYMENT_NOT_FOUND)
        return

    await reject_payment(payment_id)

    try:
        await message.bot.send_message(payment.user_id, PAYMENT_REJECTED)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to notify rejected user %s: %s", payment.user_id, exc)

    admin_username = (message.from_user.username if message.from_user else None) or "admin"
    await message.answer(
        f"{tier_label(payment.tier)} | #{payment_id}\n"
        + ADMIN_PAYMENT_REJECTED.format(admin_username=admin_username)
    )


# ---------------------------------------------------------------------------
# Package prices (admin-editable, stored in the prices table)
# ---------------------------------------------------------------------------

# neonpay enforces 1..2500 Stars per invoice; IRR has no such ceiling.
MIN_STARS = 1
MAX_STARS = 2500


async def _prices_text() -> str:
    prices = await get_all_prices()
    rows = "\n".join(
        PRICES_ROW.format(
            label=f"{tier_label(key)} ({key})",
            irr=f"{prices[key][0]:,}",
            stars=f"{prices[key][1]:,}",
        )
        for key in PACKAGE_KEYS
        if key in prices
    )
    return PRICES_CURRENT.format(rows=rows)


@router.message(Command("prices"))
async def cmd_prices(message: Message) -> None:
    if not _is_admin(message.from_user.id if message.from_user else 0):
        await message.answer(ADMIN_ONLY)
        return

    parts = (message.text or "").split()
    if len(parts) == 1:
        await message.answer(await _prices_text())
        return

    if len(parts) != 4:
        await message.answer(PRICES_USAGE)
        return

    package_key = parts[1].strip().lower()
    if package_key not in PACKAGE_KEYS:
        await message.answer(PRICES_INVALID_KEY)
        return

    try:
        irr = int(parts[2].replace(",", ""))
        stars = int(parts[3].replace(",", ""))
    except ValueError:
        await message.answer(PRICES_INVALID_AMOUNT)
        return

    if irr <= 0 or not (MIN_STARS <= stars <= MAX_STARS):
        await message.answer(PRICES_INVALID_AMOUNT)
        return

    await set_price(package_key, irr, stars)

    # Stars invoices read the price from the neonpay stage, so re-sync it now
    # instead of waiting for the next restart.
    try:
        from payments.stars import refresh_stars_prices

        await refresh_stars_prices()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not refresh Stars prices: %s", exc)

    await message.answer(
        PRICES_UPDATED.format(
            label=f"{tier_label(package_key)} ({package_key})",
            irr=f"{irr:,}",
            stars=f"{stars:,}",
        )
    )
