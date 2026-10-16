"""``/ping`` — the "is the bot alive?" probe, handled before everything else.

This middleware is registered **first** on ``dp.message`` (see
``main.build_dispatcher``), so it runs before throttling, the tracking-cancel,
the access middleware and every router. It touches no database, no state and no
other handler: it only answers the update. That makes it the one reliable
diagnostic when users report "the bot does not answer":

* ``/ping`` replies → the process is alive, long-polling works and the bot can
  send messages. If ``/start`` still stays silent, the message is being dropped
  further down the chain (check the middleware log lines for that update id).
* ``/ping`` does not reply → the bot cannot reach Telegram at all (bad token,
  another instance polling the same token, network).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)

PING_COMMAND = "/ping"
PING_REPLY = "pong"


def is_ping(text: str | None) -> bool:
    """True for ``/ping`` and ``/ping@this_bot`` (with or without arguments)."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped.split(maxsplit=1)[0]
    return first.split("@", 1)[0] == PING_COMMAND


class PingMiddleware(BaseMiddleware):
    """Answer ``/ping`` immediately, bypassing every other middleware."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not is_ping(getattr(event, "text", None)):
            return await handler(event, data)

        user = data.get("event_from_user")
        logger.info(
            "/ping from user %s (chat %s) -> %s [answered before all other middleware]",
            getattr(user, "id", None),
            getattr(event, "chat", None) and getattr(event.chat, "id", None),
            PING_REPLY,
        )
        try:
            await event.answer(PING_REPLY)
        except Exception as exc:  # noqa: BLE001 - report, never crash the update
            logger.error("Failed to answer /ping: %s", exc)
        return None
