"""Minimal HTTP server so Grabora can run on Render's free Web Service plan."""

from __future__ import annotations

import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080


async def _health(_request: web.Request) -> web.Response:
    return web.Response(text="OK", status=200)


async def _root(_request: web.Request) -> web.Response:
    return web.Response(text="Grabora is running", status=200)


def _port() -> int:
    try:
        return int(os.getenv("PORT", DEFAULT_PORT))
    except (TypeError, ValueError):
        logger.warning("Invalid PORT value, falling back to %d", DEFAULT_PORT)
        return DEFAULT_PORT


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/", _root)

    runner = web.AppRunner(app)
    await runner.setup()
    port = _port()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("Web server listening on 0.0.0.0:%s (/health)", port)
    return runner
