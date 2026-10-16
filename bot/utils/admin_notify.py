"""Reliable delivery of admin notifications.

Every admin notification (new card payment, Stars payment, download-failure
alert, ``/test_alert``) goes through :func:`send_to_admins` so that a failure is
always visible: logged at ERROR level, retried once when the error is
transient, reported back to the caller, and — for the "admin never started the
bot" case — explained with an actionable hint instead of being swallowed.

The ``parse_mode="Markdown"`` trap
----------------------------------
Telegram's legacy Markdown parser rejects a *whole message* when a
user-controlled value contains an unbalanced ``_``, ``*``, ``[`` or backtick
("Bad Request: can't parse entities"). User full names and usernames are
exactly such values, so a payment notification used to fail for those users and
the admin simply never saw the payment. Escape the values with
:func:`escape_markdown` before formatting, and pass ``plain_text`` as a safety
net so a parse error can still be delivered.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from bot.utils.messages import ADMIN_NOTIFY_START_HINT

logger = logging.getLogger(__name__)

# Legacy-Markdown special characters Telegram rejects when unbalanced.
_MARKDOWN_SPECIALS = ("_", "*", "`", "[")
_MARKDOWN_TRANSLATION = str.maketrans({c: f"\\{c}" for c in _MARKDOWN_SPECIALS})

# Backoff before the single retry (patched to 0 in tests).
RETRY_BACKOFF_SECONDS = 1.0
# TelegramRetryAfter is honoured but capped, so a handler cannot stall for long.
MAX_RETRY_AFTER_SECONDS = 5.0


def escape_markdown(value: object) -> str:
    """Escape user-controlled text for ``parse_mode="Markdown"`` (legacy)."""
    if value is None:
        return ""
    return str(value).translate(_MARKDOWN_TRANSLATION)


def sanitize_code_span(value: object) -> str:
    """Prepare a value that the message wraps in backticks.

    Inside a legacy-Markdown code span a backtick cannot be escaped — it ends
    the span — so such characters are removed instead.
    """
    if value is None:
        return ""
    return str(value).replace("`", "")


@dataclass
class AdminSendResult:
    """Outcome of delivering one notification to one admin."""

    admin_id: int
    ok: bool
    error: str | None = None
    hint: str | None = None
    attempts: int = 0
    degraded: bool = False  # delivered, but only as plain text


async def send_to_admins(
    bot,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
    plain_text: str | None = None,
    admin_ids: Sequence[int] | None = None,
    retries: int = 1,
) -> list[AdminSendResult]:
    """Send ``text`` to every admin and report what happened.

    * ``plain_text`` — fallback body used when a Markdown send is rejected, so a
      formatting problem can never cost the notification entirely.
    * ``admin_ids`` — override, mostly for tests; defaults to ``config.ADMIN_IDS``.
    * ``retries`` — how many extra attempts are made for transient errors.

    Never raises: every failure is logged and returned as a result so callers
    (and ``/test_alert``) can report it.
    """
    from config import ADMIN_IDS

    ids = list(ADMIN_IDS if admin_ids is None else admin_ids)
    if not ids:
        logger.error(
            "ADMIN_IDS is empty — no admin can be notified. Set ADMIN_IDS in the "
            "environment to receive notifications."
        )
        return []

    kwargs: dict = {}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup

    results: list[AdminSendResult] = []
    for admin_id in ids:
        results.append(
            await _send_one(bot, admin_id, text, kwargs, plain_text, max(retries, 0))
        )

    failed = [r for r in results if not r.ok]
    if failed:
        logger.error(
            "Admin notification NOT delivered to %d of %d admin(s): %s",
            len(failed),
            len(results),
            "; ".join(f"{r.admin_id} -> {r.error}" for r in failed),
        )
    else:
        degraded = [r for r in results if r.degraded]
        logger.info(
            "Admin notification delivered to %d admin(s)%s",
            len(results),
            f" ({len(degraded)} as plain text)" if degraded else "",
        )
    return results


async def _send_one(
    bot,
    admin_id: int,
    text: str,
    kwargs: dict,
    plain_text: str | None,
    retries: int,
) -> AdminSendResult:
    """Deliver one notification, retrying transient failures once."""
    attempts = 0
    last_error = "unknown error"

    for attempt in range(retries + 1):
        attempts += 1
        try:
            await bot.send_message(admin_id, text, **kwargs)
            if attempts > 1:
                logger.info("Admin %s notified on attempt %d", admin_id, attempts)
            return AdminSendResult(admin_id=admin_id, ok=True, attempts=attempts)
        except TelegramForbiddenError as exc:
            # Permanent: the admin never started a chat with the bot (or blocked
            # it). Retrying cannot help — tell the caller what to do instead.
            last_error = str(exc)
            logger.error(
                "Cannot message admin %s: %s. The admin must press /start in the "
                "bot (or unblock it) before notifications can be delivered.",
                admin_id,
                exc,
            )
            return AdminSendResult(
                admin_id=admin_id,
                ok=False,
                error=last_error,
                hint=ADMIN_NOTIFY_START_HINT,
                attempts=attempts,
            )
        except TelegramBadRequest as exc:
            last_error = str(exc)
            if plain_text is not None and kwargs.get("parse_mode"):
                # Formatting problem (e.g. "can't parse entities"): resend the
                # same content as plain text rather than losing the notification.
                plain_kwargs = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                try:
                    await bot.send_message(admin_id, plain_text, **plain_kwargs)
                    logger.warning(
                        "Markdown notification for admin %s was rejected (%s); "
                        "resent as plain text",
                        admin_id,
                        exc,
                    )
                    return AdminSendResult(
                        admin_id=admin_id,
                        ok=True,
                        attempts=attempts,
                        degraded=True,
                    )
                except Exception as fallback_exc:  # noqa: BLE001
                    last_error = str(fallback_exc)
            logger.error(
                "Admin %s rejected the notification: %s", admin_id, last_error
            )
            return AdminSendResult(
                admin_id=admin_id,
                ok=False,
                error=last_error,
                hint=ADMIN_NOTIFY_START_HINT,
                attempts=attempts,
            )
        except Exception as exc:  # noqa: BLE001 - transient / unexpected
            last_error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, TelegramRetryAfter):
                await asyncio.sleep(min(float(exc.retry_after), MAX_RETRY_AFTER_SECONDS))
            if attempt < retries:
                logger.warning(
                    "Admin %s notification failed (%s); retrying once",
                    admin_id,
                    last_error,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                continue
            logger.error(
                "Giving up on admin %s notification after %d attempt(s): %s",
                admin_id,
                attempts,
                last_error,
            )

    return AdminSendResult(
        admin_id=admin_id,
        ok=False,
        error=last_error,
        hint=ADMIN_NOTIFY_START_HINT,
        attempts=attempts,
    )
