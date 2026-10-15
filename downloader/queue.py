"""Download queue with concurrency control."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Awaitable

from config import GLOBAL_CONCURRENCY, PER_USER_CONCURRENCY

logger = logging.getLogger(__name__)


class DownloadQueue:
    def __init__(self) -> None:
        self._global = asyncio.Semaphore(GLOBAL_CONCURRENCY)
        self._user_semaphores: dict[int, asyncio.Semaphore] = {}

    def _user_sem(self, user_id: int) -> asyncio.Semaphore:
        if user_id not in self._user_semaphores:
            self._user_semaphores[user_id] = asyncio.Semaphore(PER_USER_CONCURRENCY)
        return self._user_semaphores[user_id]

    async def run(self, user_id: int, coro_factory: Callable[[], Awaitable[Path]]) -> Path:
        async with self._global:
            async with self._user_sem(user_id):
                return await coro_factory()


download_queue = DownloadQueue()
