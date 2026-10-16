"""Download flow handlers."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.keyboards.inline import quality_keyboard
from bot.middlewares.access import (
    AccessResult,
    check_download_access,
    check_quality_access,
    file_size_limit_mb,
    is_daily_limit_reached,
    is_paid,
)
from bot.utils.helpers import domain_of
from bot.utils.messages import (
    BANNED,
    CHOOSE_QUALITY,
    DAILY_LIMIT_REACHED,
    DOWNLOAD_FAILED,
    FILE_TOO_LARGE,
    GENERIC_ERROR,
    INSTAGRAM_RATE_LIMITED,
    PLAYLIST_NOTICE,
    PROCESSING,
    RATE_LIMITED,
    UPGRADE_FOR_QUALITY,
    UPGRADE_FOR_SIZE,
    URL_NOT_FOUND,
)
from config import (
    FREE_MAX_FILE_SIZE_MB,
    MAX_FILE_SIZE_MB,
    TEMP_DIR,
    TIER_FREE,
)
from database.models import User
from database.queries import (
    increment_daily_downloads,
    log_download,
)
from downloader.queue import download_queue
from downloader.ytdlp_wrapper import MediaTooLargeError
from downloader.ytdlp_wrapper import download as download_media
from downloader.ytdlp_wrapper import extract_info, is_instagram_url, is_rate_limited, supports_size_probe

logger = logging.getLogger(__name__)

_last_alert_time: datetime | None = None
_ALERT_COOLDOWN_SECONDS = 30 * 60

router = Router(name="download")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class DownloadStates(StatesGroup):
    choosing_quality = State()


def _decision_message(result: AccessResult, user: User, size_mb: float | None = None) -> str:
    """Pick the user-facing message for a non-ALLOWED access decision."""
    if result is AccessResult.BLOCKED:
        return BANNED
    if result is AccessResult.LIMIT_REACHED:
        return DAILY_LIMIT_REACHED
    if result is AccessResult.SIZE_LIMIT_REACHED:
        if is_paid(user):
            return FILE_TOO_LARGE
        size_text = f"{size_mb:.0f}" if size_mb is not None else "?"
        return UPGRADE_FOR_SIZE.format(
            size=size_text,
            limit=FREE_MAX_FILE_SIZE_MB,
            paid_limit=MAX_FILE_SIZE_MB,
        )
    if result in (AccessResult.NEED_SUBSCRIPTION, AccessResult.NEED_2K_ADDON):
        return UPGRADE_FOR_QUALITY
    return GENERIC_ERROR


def _estimate_size_mb(info: dict) -> float | None:
    """Best-effort size estimate (MB) from yt-dlp metadata.

    Merged video+audio formats often report 0 or None, so anything that is not
    a positive number is treated as "unknown" (returns None) and the caller
    falls through to the post-download size check.
    """
    total = 0.0
    found = False
    for key in ("filesize", "filesize_approx"):
        value = info.get(key)
        if value:
            total += float(value)
            found = True
            break
    if not found:
        for fmt in info.get("requested_formats") or []:
            value = fmt.get("filesize") or fmt.get("filesize_approx")
            if value:
                total += float(value)
                found = True
    if not found:
        return None
    size_mb = total / (1024 * 1024)
    if size_mb <= 0:
        # yt-dlp reported 0 / negative — the size is unknown, not "zero bytes".
        return None
    return size_mb


async def _probe_size_mb(url: str) -> float | None:
    """Fetch metadata without downloading to estimate the output size."""
    try:
        info = await extract_info(url)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Size probe failed for %s: %s", url, exc)
        return None
    return _estimate_size_mb(info)


def _failure_message(exc: BaseException, url: str, user: User) -> str:
    """User-facing text for a failed download — never any internal detail."""
    if isinstance(exc, MediaTooLargeError):
        # Same wording as the post-download size rejection, so a free user is
        # still told about the subscription option (no size is known here).
        return _decision_message(AccessResult.SIZE_LIMIT_REACHED, user, None)
    if is_rate_limited(exc):
        # Instagram's own rate limit deserves its own wording; both messages
        # stay free of stack traces, headers and credentials.
        return INSTAGRAM_RATE_LIMITED if is_instagram_url(url) else RATE_LIMITED
    return DOWNLOAD_FAILED


def _is_retryable(exc: BaseException) -> bool:
    """Whether the single retry in the download flow may be spent on ``exc``.

    A rate limit (HTTP 429) is not transient on a seconds timescale and every
    retry is another request against the limit that is being enforced, so it is
    never retried here — yt-dlp already retries *media* transfers with bounded
    backoff, and the user is told to come back later. A media that yt-dlp
    refused for exceeding ``max_filesize`` is retried no more than a locked
    quality would be. Everything else keeps the pre-existing behaviour.
    """
    if isinstance(exc, MediaTooLargeError) or is_rate_limited(exc):
        return False
    return True


def _is_limit_reached(user: User):
    """Backwards-compatible wrapper around the shared quota check."""
    return is_daily_limit_reached(user)


def _quality_allowed(user: User, media_type: str, quality: str) -> bool:
    """Backwards-compatible quality-only check."""
    return check_quality_access(user, media_type, quality) is AccessResult.ALLOWED


async def _notify_admins_if_needed(bot: Bot) -> None:
    global _last_alert_time
    from config import ADMIN_IDS
    from database.queries import count_failures_last_hour

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if _last_alert_time is not None and (now - _last_alert_time).total_seconds() < _ALERT_COOLDOWN_SECONDS:
        return

    try:
        failures = await count_failures_last_hour()
    except Exception:  # noqa: BLE001
        return
    if failures <= 5:
        return

    _last_alert_time = now
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"⚠️ Download failures in last hour: {failures}")
        except Exception:  # noqa: BLE001
            logger.debug("Failed to notify admin %s", admin_id)


@router.message(F.text.regexp(URL_RE))
async def on_url(message: Message, db_user: User, state: FSMContext) -> None:
    match = URL_RE.search(message.text or "")
    if not match:
        return
    url = match.group(0)

    if await is_daily_limit_reached(db_user):
        await message.answer(DAILY_LIMIT_REACHED)
        return

    if "playlist" in url.lower() or "list=" in url.lower():
        await message.answer(PLAYLIST_NOTICE)

    await state.set_state(DownloadStates.choosing_quality)
    await state.update_data(url=url)

    is_subscriber = db_user.tier != TIER_FREE
    keyboard = quality_keyboard(is_subscriber, db_user.has_2k_addon)
    await message.answer(CHOOSE_QUALITY, reply_markup=keyboard)


@router.callback_query(F.data.startswith("q:locked:"))
async def on_locked_quality(callback: CallbackQuery) -> None:
    await callback.answer(UPGRADE_FOR_QUALITY, show_alert=True)


@router.callback_query(F.data.startswith("q:"))
async def on_quality_selected(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    url = data.get("url")
    if not url:
        await callback.answer(URL_NOT_FOUND, show_alert=True)
        await state.clear()
        return

    parts = (callback.data or "").split(":")
    if len(parts) < 3 or parts[0] != "q":
        await callback.answer()
        return
    media_type = parts[1]
    quality = parts[2]

    if media_type not in {"video", "audio"}:
        await callback.answer()
        await state.clear()
        return

    # Gate 1: locked format — never start a download for it.
    decision = await check_download_access(db_user, media_type, quality)
    if decision is not AccessResult.ALLOWED:
        await callback.answer(_decision_message(decision, db_user), show_alert=True)
        return

    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text(PROCESSING)

    temp_dir = Path(TEMP_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_path: Path | None = None
    try:
        # Gate 2: size estimate from metadata, before downloading anything.
        # Unknown (None or 0) estimates skip this gate entirely; Gate 3 below
        # always runs on the real file. Sites where the probe costs an extra
        # rate-limited request (Instagram) skip it and rely on yt-dlp's
        # max_filesize abort plus Gate 3 instead.
        limit_bytes = file_size_limit_mb(db_user) * 1024 * 1024
        probe = supports_size_probe(url)
        if not probe:
            logger.info(
                "Skipping the metadata size probe for %s (saves one request); "
                "yt-dlp will abort media larger than %d MB and the real file is "
                "checked after download",
                domain_of(url),
                file_size_limit_mb(db_user),
            )
        estimated_mb = await _probe_size_mb(url) if probe else None
        if estimated_mb is not None and estimated_mb > 0:
            decision = await check_download_access(
                db_user, media_type, quality, estimated_size_mb=estimated_mb
            )
            if decision is not AccessResult.ALLOWED:
                await log_download(
                    user_id=db_user.user_id,
                    url_domain=domain_of(url),
                    media_type=media_type,
                    quality=quality,
                    status="failed",
                    file_size_mb=estimated_mb,
                    error_message=f"blocked before download: {decision.value}",
                )
                if callback.message is not None:
                    await callback.message.answer(
                        _decision_message(decision, db_user, estimated_mb)
                    )
                return

        # Retry once on transient failure. Rate limits and "too large" are
        # final: retrying them would only add requests against a limit that is
        # already being enforced (verified cause of repeated 429s).
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                file_path = await download_queue.run(
                    db_user.user_id,
                    lambda u=url, q=quality, m=media_type, d=temp_dir, mb=limit_bytes: download_media(
                        u, q, m, d, max_filesize_bytes=mb
                    ),
                )
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Download attempt %d/%d failed for %s (%s): %s",
                    attempt + 1,
                    2,
                    domain_of(url),
                    type(exc).__name__,
                    exc,
                )
                if attempt == 0 and _is_retryable(exc):
                    continue
                break
        if last_exc is not None:
            raise last_exc

        if file_path is None or not file_path.exists():
            raise RuntimeError("Downloaded file not found")

        # Gate 3: re-check the real size (covers unknown metadata estimates).
        size_mb = file_path.stat().st_size / (1024 * 1024)
        limit_mb = file_size_limit_mb(db_user)
        if size_mb > limit_mb:
            await log_download(
                user_id=db_user.user_id,
                url_domain=domain_of(url),
                media_type=media_type,
                quality=quality,
                status="failed",
                file_size_mb=size_mb,
                error_message="file too large",
            )
            if callback.message is not None:
                await callback.message.answer(
                    _decision_message(AccessResult.SIZE_LIMIT_REACHED, db_user, size_mb)
                )
            return

        # Send file.
        fs_file = FSInputFile(file_path)
        if media_type == "video":
            await callback.bot.send_video(
                chat_id=db_user.user_id,
                video=fs_file,
                supports_streaming=True,
            )
        else:
            await callback.bot.send_audio(chat_id=db_user.user_id, audio=fs_file)

        await log_download(
            user_id=db_user.user_id,
            url_domain=domain_of(url),
            media_type=media_type,
            quality=quality,
            status="success",
            file_size_mb=size_mb,
        )
        await increment_daily_downloads(db_user.user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Download flow error for %s (%s): %s", domain_of(url), type(exc).__name__, exc
        )
        await log_download(
            user_id=db_user.user_id,
            url_domain=domain_of(url),
            media_type=media_type,
            quality=quality,
            status="failed",
            error_message=str(exc)[:500],
        )
        if callback.message is not None:
            await callback.message.answer(_failure_message(exc, url, db_user))
        # A rate limit is worth telling the admins about: it usually means
        # several users are affected at once.
        if is_rate_limited(exc):
            logger.warning(
                "Rate limit (HTTP 429) while downloading from %s — see the "
                "INSTAGRAM_COOKIES_* documentation if this repeats",
                domain_of(url),
            )
        await _notify_admins_if_needed(callback.bot)
    finally:
        if file_path is not None:
            try:
                file_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                logger.debug("Failed to delete temp file %s", file_path)
        await state.clear()
