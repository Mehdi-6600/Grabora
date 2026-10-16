"""Independent admin panel: ``/admin``, ``/panel`` and the admin reply keyboard.

The panel is a router of its own so admin UI can grow without touching the
regular user flow. Every button of :func:`bot.keyboards.reply.admin_menu` is a
plain text message (Telegram sends reply-keyboard taps as text), so each button
has a handler here whose filter matches the shared ``BTN_ADMIN_*`` constant and
whose body reuses the very same function the corresponding slash command calls.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.handlers import admin as admin_handlers
from bot.keyboards.reply import (
    BTN_ADMIN_BROADCAST,
    BTN_ADMIN_CARD,
    BTN_ADMIN_EXIT,
    BTN_ADMIN_PENDING,
    BTN_ADMIN_PRICES,
    BTN_ADMIN_STATS,
    BTN_ADMIN_TEST_ALERT,
    admin_menu,
    main_menu,
)
from bot.middlewares.access import AdminMiddleware
from bot.utils.messages import (
    ADMIN_ONLY,
    ADMIN_PANEL_EXIT,
    ADMIN_PANEL_TITLE,
)

logger = logging.getLogger(__name__)

admin_panel_router = Router(name="admin_panel")


class AdminPanelMiddleware(AdminMiddleware):
    """``AdminMiddleware`` that also tells a non-admin *why* nothing happened.

    The plain :class:`AdminMiddleware` rejects silently, which is right for the
    command router but confusing for a panel whose buttons a stranger might
    type by hand: here a non-admin gets ``ADMIN_ONLY`` instead of silence.
    """

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        from config import ADMIN_IDS

        user = data.get("event_from_user")
        if user is None or user.id not in ADMIN_IDS:
            logger.info("Admin panel blocked for non-admin %s", getattr(user, "id", None))
            if isinstance(event, Message):
                await event.answer(ADMIN_ONLY)
            elif isinstance(event, CallbackQuery):
                await event.answer(ADMIN_ONLY, show_alert=True)
            return None
        logger.debug("Admin panel pass for admin %s (%s)", user.id, type(event).__name__)
        return await handler(event, data)


def _is_admin(message: Message) -> bool:
    """Second line of defence for handlers called without the middleware."""
    from config import ADMIN_IDS

    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


async def _guard(message: Message) -> bool:
    """True when the sender is an admin; otherwise answers ADMIN_ONLY."""
    if _is_admin(message):
        return True
    await message.answer(ADMIN_ONLY)
    return False


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@admin_panel_router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Open the admin panel (admins only)."""
    if not await _guard(message):
        return
    await message.answer(
        ADMIN_PANEL_TITLE.format(exit_button=BTN_ADMIN_EXIT),
        reply_markup=admin_menu(),
    )


@admin_panel_router.message(Command("panel"))
async def cmd_panel(message: Message) -> None:
    """Alias of /admin."""
    await cmd_admin(message)


@admin_panel_router.message(F.text == BTN_ADMIN_EXIT)
async def on_exit(message: Message) -> None:
    """Leave the panel and restore the regular user keyboard."""
    if not await _guard(message):
        return
    await message.answer(ADMIN_PANEL_EXIT, reply_markup=main_menu())


# ---------------------------------------------------------------------------
# Panel buttons
# ---------------------------------------------------------------------------


@admin_panel_router.message(F.text == BTN_ADMIN_STATS)
async def on_stats(message: Message) -> None:
    if not await _guard(message):
        return
    await admin_handlers.send_stats(message)


@admin_panel_router.message(F.text == BTN_ADMIN_PENDING)
async def on_pending(message: Message) -> None:
    if not await _guard(message):
        return
    await admin_handlers.send_pending_payments(message)


@admin_panel_router.message(F.text == BTN_ADMIN_PRICES)
async def on_prices(message: Message) -> None:
    if not await _guard(message):
        return
    await admin_handlers.send_prices_table(message)


@admin_panel_router.message(F.text == BTN_ADMIN_CARD)
async def on_card(message: Message) -> None:
    if not await _guard(message):
        return
    await admin_handlers.send_card_overview(message)


@admin_panel_router.message(F.text == BTN_ADMIN_BROADCAST)
async def on_broadcast(message: Message) -> None:
    """Ask for the broadcast text (typing it would broadcast the label itself)."""
    if not await _guard(message):
        return
    await admin_handlers.send_broadcast_prompt(message)


@admin_panel_router.message(F.text == BTN_ADMIN_TEST_ALERT)
async def on_test_alert(message: Message) -> None:
    if not await _guard(message):
        return
    await admin_handlers.send_test_alert(message)

