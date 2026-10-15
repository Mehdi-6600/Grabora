"""Entry point for Grabora bot."""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.handlers import admin, card_payment, download, start, subscription
from bot.middlewares.access import AccessMiddleware, AdminMiddleware
from bot.handlers.card_payment import TrackingCancelMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from config import (
    BOT_API_URL,
    BOT_TOKEN,
    CLEANUP_INTERVAL_HOURS,
    LOG_FILE,
    LOG_LEVEL,
    TEMP_DIR,
    TEMP_FILE_MAX_AGE_HOURS,
    validate,
)
from database.db import close_db, init_db
from payments.stars import init_stars, setup_stars
from web_server import start_web_server

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    log_path = Path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    logging.basicConfig(level=level)
    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)


async def cleanup_temp_files() -> None:
    import time

    temp_dir = Path(TEMP_DIR)
    if not temp_dir.exists():
        return
    cutoff = time.time() - TEMP_FILE_MAX_AGE_HOURS * 3600
    removed = 0
    for path in temp_dir.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to remove temp file %s: %s", path, exc)
    if removed:
        logger.info("Cleaned up %d temp files", removed)


async def main() -> None:
    validate()
    setup_logging()

    await init_db()
    from database.queries import seed_settings
    from config import CARD_NUMBER, CARD_HOLDER
    await seed_settings(CARD_NUMBER, CARD_HOLDER)

    from aiogram import Bot, Dispatcher

    session = AiohttpSession(api=TelegramAPIServer.from_base(BOT_API_URL))
    bot = Bot(token=BOT_TOKEN, session=session)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Middlewares (order matters).
    dp.message.middleware(ThrottlingMiddleware())
    # Cancels a pending tracking-number flow when the user sends a URL/command
    # (the update itself is still handled normally afterwards).
    dp.message.middleware(TrackingCancelMiddleware())
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())

    # Admin router with admin middleware.
    admin.router.message.middleware(AdminMiddleware())
    admin.router.callback_query.middleware(AdminMiddleware())

    # Routers (card first so "pay:card:" wins over the generic Stars handler).
    dp.include_router(start.router)
    dp.include_router(download.router)
    dp.include_router(card_payment.router)
    dp.include_router(subscription.router)
    dp.include_router(admin.router)

    init_stars(bot, dp)

    # Stars user/admin notifications are sent by payments.stars._handle_stars_payment
    # with the formatted PAYMENT_SUCCESS / PAYMENT_SUCCESS_ADDON templates, so no
    # notification hook is passed here.
    try:
        await setup_stars()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stars setup failed: %s", exc)

    # Scheduler.
    scheduler = AsyncIOScheduler()
    scheduler.add_job(cleanup_temp_files, "interval", hours=CLEANUP_INTERVAL_HOURS)
    # Daily counter reset: run hourly and reset those past due (handled in query).
    from database.queries import reset_all_daily_counters

    async def reset_counters_job() -> None:
        n = await reset_all_daily_counters()
        if n:
            logger.info("Reset %d daily counters", n)

    scheduler.add_job(reset_counters_job, "interval", hours=1)
    # Subscription expiry check daily.
    from database.queries import downgrade_expired_users

    async def expiry_job() -> None:
        n = await downgrade_expired_users()
        if n:
            logger.info("Downgraded %d expired users/addons", n)

    async def expiry_reminder_job() -> None:
        from bot.utils.messages import SUBSCRIPTION_EXPIRY_REMINDER
        from database.queries import get_users_expiring_in_days, set_reminder_sent

        users = await get_users_expiring_in_days(3)
        sent = 0
        for u in users:
            if u.reminder_sent_for_expiry is not None and u.tier_expires_at is not None and u.reminder_sent_for_expiry == u.tier_expires_at:
                continue
            try:
                days = 3
                await bot.send_message(u.user_id, SUBSCRIPTION_EXPIRY_REMINDER.format(days=days))
                await set_reminder_sent(u.user_id, u.tier_expires_at)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to send expiry reminder to %s: %s", u.user_id, exc)
        if sent:
            logger.info("Sent expiry reminders to %d users", sent)

    scheduler.add_job(expiry_job, "interval", hours=24)
    scheduler.add_job(expiry_reminder_job, "interval", hours=24)
    scheduler.start()

    # Render free-tier Web Service: bind $PORT and serve /health.
    web_runner = None
    try:
        web_runner = await start_web_server()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web server failed to start: %s", exc)

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Signal received, shutting down")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    bot_task = asyncio.create_task(dp.start_polling(bot))

    def _on_done(_task: asyncio.Task) -> None:
        stop_event.set()

    bot_task.add_done_callback(_on_done)

    await stop_event.wait()

    logger.info("Shutting down...")
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass

    if web_runner is not None:
        await web_runner.cleanup()

    scheduler.shutdown(wait=False)
    await bot.session.close()
    await close_db()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
