"""Throttling middleware.

**Why this file is written so defensively:** a middleware registered on
``dp.message`` wraps the *entire* router chain (in aiogram 3,
``Dispatcher.propagate_event`` runs the dispatcher's middlewares around
``_propagate_event``, which then recurses into every included router). Returning
``None`` therefore does not just "skip one handler" — it makes aiogram treat the
whole update as handled, log ``Update id=N is handled`` and deliver **nothing**,
with no error and (before this fix) no log entry at all. That is exactly how the
bot looked dead in production: every ``/start`` inside the throttling window was
eaten before any handler could run.

Two rules follow from that:

1. Navigation must never be throttled. Commands (``/start``, ``/help``, …) and
   reply-keyboard buttons are cheap and are the user's only way to bring the bot
   back, so they always pass through.
2. A drop must never be invisible: it is logged at ``WARNING`` with the user,
   the elapsed time, the window and a text preview.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import THROTTLE_SECONDS

logger = logging.getLogger(__name__)


def is_navigation_text(text: str) -> bool:
    """True for commands and reply-keyboard buttons (never throttled)."""
    text = text.strip()
    if not text:
        return False
    if text.startswith("/"):
        return True
    from bot.keyboards.reply import ADMIN_BUTTON_TEXTS, REPLY_BUTTON_TEXTS

    return text in REPLY_BUTTON_TEXTS or text in ADMIN_BUTTON_TEXTS


class ThrottlingMiddleware(BaseMiddleware):
    """Rate-limit free text and URLs per user; always let navigation through."""

    def __init__(self) -> None:
        super().__init__()
        self._last: dict[int, float] = {}
        self.dropped: dict[int, int] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        text = getattr(event, "text", None) or ""

        if user is None or is_navigation_text(text):
            # Commands/buttons: always handled, even inside the window.
            return await handler(event, data)

        now = time.monotonic()
        elapsed = now - self._last.get(user.id, 0.0)
        if elapsed < THROTTLE_SECONDS:
            self.dropped[user.id] = self.dropped.get(user.id, 0) + 1
            logger.warning(
                "Throttled: user %s sent %r %.2fs after the previous message "
                "(window %.1fs) — the update is dropped and no handler runs "
                "(%d dropped from this user so far)",
                user.id,
                text[:80],
                elapsed,
                THROTTLE_SECONDS,
                self.dropped[user.id],
            )
            return None

        self._last[user.id] = now
        logger.debug(
            "Throttle window open for user %s (%.2fs since the previous message)",
            user.id,
            elapsed,
        )
        return await handler(event, data)
