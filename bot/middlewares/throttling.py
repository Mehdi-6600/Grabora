"""Throttling middleware (silent drop)."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import THROTTLE_SECONDS


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            now = time.monotonic()
            last = self._last.get(user.id, 0.0)
            if now - last < THROTTLE_SECONDS:
                return None
            self._last[user.id] = now
        return await handler(event, data)
