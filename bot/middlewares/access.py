"""Access control middleware and download-access decisions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.utils.messages import BANNED
from config import (
    ADMIN_IDS,
    FREE_AUDIO_MAX_KBPS,
    FREE_MAX_FILE_SIZE_MB,
    FREE_VIDEO_MAX_HEIGHT,
    MAX_FILE_SIZE_MB,
    TIER_DAILY_LIMITS,
    TIER_FREE,
)
from database.models import User
from database.queries import (
    get_or_create_user,
    get_user,
    reset_daily_counter_if_due,
    set_user_tier,
)

logger = logging.getLogger(__name__)


class AccessResult(str, Enum):
    """Outcome of a download-access check."""

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    LIMIT_REACHED = "limit_reached"
    NEED_SUBSCRIPTION = "need_subscription"
    NEED_2K_ADDON = "need_2k_addon"
    SIZE_LIMIT_REACHED = "size_limit_reached"
    INVALID = "invalid"

    def __bool__(self) -> bool:
        # ``if decision:`` should read as "allowed".
        return self is AccessResult.ALLOWED


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def daily_limit_for(user: User) -> int:
    """Daily download quota for the user's tier (-1 = unlimited)."""
    return TIER_DAILY_LIMITS.get(user.tier, TIER_DAILY_LIMITS[TIER_FREE])


def is_paid(user: User, now: datetime | None = None) -> bool:
    """Whether the user holds a non-expired paid tier."""
    if user.tier == TIER_FREE or not user.tier:
        return False
    if user.tier_expires_at is None:
        return True
    return user.tier_expires_at > (now or now_utc())


def has_active_2k_addon(user: User, now: datetime | None = None) -> bool:
    """Whether the user holds a non-expired 2K addon."""
    if not user.has_2k_addon:
        return False
    if user.addon_expires_at is None:
        return True
    return user.addon_expires_at > (now or now_utc())


def file_size_limit_mb(user: User) -> int:
    """Max allowed file size in MB: 500 for free, MAX_FILE_SIZE_MB for paid."""
    return MAX_FILE_SIZE_MB if is_paid(user) else FREE_MAX_FILE_SIZE_MB


def check_quality_access(user: User, media_type: str, quality: str) -> AccessResult:
    """Quality gate only (no size/quota checks).

    * free:        video 144p/240p/360p/480p, audio 128/192 kbps
    * paid:        video up to 1080p, audio 320 kbps + FLAC
    * paid + 2K:   video 1440p
    """
    now = now_utc()
    paid = is_paid(user, now)

    if media_type == "video":
        raw = str(quality).strip().lower().removesuffix("p")
        if raw in {"1440", "2k", "2160", "4k"}:
            if not paid:
                return AccessResult.NEED_SUBSCRIPTION
            if raw != "1440" and raw != "2k":
                # 2160/4k are not offered at all.
                return AccessResult.INVALID
            if not has_active_2k_addon(user, now):
                return AccessResult.NEED_2K_ADDON
            return AccessResult.ALLOWED
        try:
            height = int(raw)
        except ValueError:
            return AccessResult.INVALID
        if height <= FREE_VIDEO_MAX_HEIGHT:
            return AccessResult.ALLOWED
        # 720p and 1080p (and anything in between) require a subscription.
        if height <= 1080:
            return AccessResult.ALLOWED if paid else AccessResult.NEED_SUBSCRIPTION
        return AccessResult.INVALID

    if media_type == "audio":
        raw = str(quality).strip().lower()
        if raw == "flac":
            return AccessResult.ALLOWED if paid else AccessResult.NEED_SUBSCRIPTION
        try:
            kbps = int(raw)
        except ValueError:
            return AccessResult.INVALID
        if kbps <= FREE_AUDIO_MAX_KBPS:
            return AccessResult.ALLOWED
        if kbps <= 320:
            return AccessResult.ALLOWED if paid else AccessResult.NEED_SUBSCRIPTION
        return AccessResult.INVALID

    return AccessResult.INVALID


async def is_daily_limit_reached(user: User) -> bool:
    """True if the user's daily quota is exhausted (resets the counter if due)."""
    limit = daily_limit_for(user)
    if limit == -1:
        return False
    await reset_daily_counter_if_due(user.user_id, now_utc())
    fresh = await get_user(user.user_id)
    if fresh is not None:
        user = fresh
    return user.daily_downloads >= limit


async def check_download_access(
    user: User,
    media_type: str,
    quality: str,
    estimated_size_mb: float | None = None,
) -> AccessResult:
    """Single entry point for download gating.

    Order: banned -> daily quota -> quality (tier/addon) -> file size.
    ``estimated_size_mb`` is optional; when omitted only the quality and quota
    gates run (size is then re-checked on the real file after download).
    """
    if user.is_banned:
        return AccessResult.BLOCKED

    if await is_daily_limit_reached(user):
        return AccessResult.LIMIT_REACHED

    quality_result = check_quality_access(user, media_type, quality)
    if quality_result is not AccessResult.ALLOWED:
        return quality_result

    if estimated_size_mb is not None:
        limit_mb = file_size_limit_mb(user)
        if estimated_size_mb > limit_mb:
            return AccessResult.SIZE_LIMIT_REACHED

    return AccessResult.ALLOWED


class AccessMiddleware(BaseMiddleware):
    """Ensure user exists in DB and is not banned."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        db_user = await get_or_create_user(
            user.id, user.username, user.full_name
        )

        # Enforce expiry on access (keeps tier/addon state fresh).
        now = now_utc()
        if db_user.tier != TIER_FREE and db_user.tier_expires_at is not None and db_user.tier_expires_at <= now:
            await set_user_tier(db_user.user_id, TIER_FREE, None, db_user.has_2k_addon, db_user.addon_expires_at)
        if db_user.has_2k_addon and db_user.addon_expires_at is not None and db_user.addon_expires_at <= now:
            fresh = await get_user(db_user.user_id)
            await set_user_tier(
                db_user.user_id,
                fresh.tier if fresh else db_user.tier,
                fresh.tier_expires_at if fresh else db_user.tier_expires_at,
                False,
                None,
            )
        db_user = await get_user(db_user.user_id) or db_user
        data["db_user"] = db_user

        if db_user.is_banned:
            # Dropping an update here means no reply at all: say so in the log,
            # and make sure the user is told (a silent drop looks like an outage).
            logger.warning(
                "Access denied: user %s is banned — update dropped (%s)",
                db_user.user_id,
                type(event).__name__,
            )
            if isinstance(event, Message):
                await event.answer(BANNED)
            elif isinstance(event, CallbackQuery):
                await event.answer(BANNED, show_alert=True)
            return None

        logger.debug("Access granted: user %s (%s)", db_user.user_id, type(event).__name__)
        return await handler(event, data)


class AdminMiddleware(BaseMiddleware):
    """Restrict a router to admins.

    Non-admins are rejected without an answer (the command routers stay quiet so
    ordinary users do not learn the admin surface exists), but never without a
    log line: a silent rejection is indistinguishable from a dead bot.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id not in ADMIN_IDS:
            logger.info(
                "Admin-only handler skipped: user %s is not an admin (%s)",
                getattr(user, "id", None),
                type(event).__name__,
            )
            return None
        logger.debug("Admin handler allowed for %s", user.id)
        return await handler(event, data)
