"""Admin panel: entry points, keyboard routing, per-button behaviour.

Covers both layers:
* the handlers themselves (so each button has a deterministic body), and
* the real dispatcher with the panel's admin middleware, so a non-admin cannot
  reach the panel and an admin's button taps actually arrive.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

import config
from bot.handlers import admin as admin_handlers
from bot.handlers import admin_panel
from bot.handlers.admin_panel import admin_panel_router
from bot.keyboards.reply import (
    ADMIN_BUTTON_TEXTS,
    BTN_ACCOUNT,
    BTN_ADMIN_BROADCAST,
    BTN_ADMIN_CARD,
    BTN_ADMIN_EXIT,
    BTN_ADMIN_PENDING,
    BTN_ADMIN_PRICES,
    BTN_ADMIN_STATS,
    BTN_ADMIN_TEST_ALERT,
    REPLY_BUTTON_TEXTS,
    admin_menu,
    main_menu,
)
from bot.middlewares.access import AccessMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.utils.messages import (
    ADMIN_ONLY,
    ADMIN_PANEL_EXIT,
    ADMIN_PANEL_TITLE,
    ENTER_BROADCAST_TEXT,
    NO_PENDING_PAYMENTS,
)
from database.queries import create_manual_payment, get_or_create_user, seed_settings

ADMIN_ID = 424242
STRANGER_ID = 999999
TOKEN = "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

# Button text -> the handler that must own it, and the body it must call.
EXPECTED_ROUTING = {
    BTN_ADMIN_STATS: "on_stats",
    BTN_ADMIN_PENDING: "on_pending",
    BTN_ADMIN_PRICES: "on_prices",
    BTN_ADMIN_CARD: "on_card",
    BTN_ADMIN_BROADCAST: "on_broadcast",
    BTN_ADMIN_TEST_ALERT: "on_test_alert",
    BTN_ADMIN_EXIT: "on_exit",
}


class FakeMessage:
    def __init__(self, bot=None, user_id: int = ADMIN_ID, text: str = ""):
        self.bot = bot
        self.text = text
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[tuple[str, dict]] = []

    async def answer(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]

    @property
    def last_markup(self):
        return self.sent[-1][1].get("reply_markup")


@pytest.fixture
def admin_ids(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    return [ADMIN_ID]


async def _clear_pending_payments() -> None:
    """Make the "no pending payments" state deterministic.

    The suite shares a single database file (see conftest.py), so an earlier
    test may have left pending manual payments behind.
    """
    from database.db import get_db

    conn = get_db()
    await conn.execute(
        "UPDATE payments SET status = 'rejected' WHERE status = 'pending_manual'"
    )
    await conn.commit()


async def reset_prices(db_conn) -> None:
    from config import PACKAGE_KEYS, PACKAGE_PRICES_IRR, PACKAGE_PRICES_STARS
    from database.queries import set_price

    for key in PACKAGE_KEYS:
        await set_price(key, PACKAGE_PRICES_IRR[key], PACKAGE_PRICES_STARS[key])


def _matches(handler, text: str) -> bool:
    """Evaluate a registered handler's filters against a text message."""
    fake = FakeMessage(text=text)
    for flt in handler.filters:
        callback = getattr(flt, "callback", None)
        if callback is None:
            continue
        result = callback(fake)
        if asyncio.iscoroutine(result):
            result = asyncio.get_event_loop().run_until_complete(result)
        if not result:
            return False
    return True


def _handler_named(name: str):
    for handler in admin_panel_router.message.handlers:
        if getattr(handler.callback, "__name__", "") == name:
            return handler
    raise AssertionError(f"{name} is not registered on the admin panel router")


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------


def test_admin_menu_has_every_button_in_order():
    buttons = [b.text for row in admin_menu().keyboard for b in row]
    assert buttons == list(ADMIN_BUTTON_TEXTS)
    assert buttons == [
        "📊 آمار",
        "💳 پرداخت‌های در انتظار",
        "💰 قیمت‌ها",
        "💳 شماره کارت",
        "📢 پیام همگانی",
        "🔧 تست اطلاع‌رسانی",
        "❌ خروج از پنل",
    ]


def test_admin_menu_is_one_button_per_row():
    assert all(len(row) == 1 for row in admin_menu().keyboard)


def test_admin_and_user_keyboards_do_not_share_button_texts():
    assert not set(ADMIN_BUTTON_TEXTS) & set(REPLY_BUTTON_TEXTS)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_every_admin_button_routes_to_its_own_handler():
    for text, handler_name in EXPECTED_ROUTING.items():
        handler = _handler_named(handler_name)
        assert _matches(handler, text), f"{text!r} does not reach {handler_name}"

        # ...and no other button handler claims the same text.
        for other_text, other_name in EXPECTED_ROUTING.items():
            if other_name == handler_name:
                continue
            assert not _matches(_handler_named(other_name), text), (
                f"{other_name} also claims {text!r}"
            )


def test_user_menu_buttons_are_not_claimed_by_the_panel():
    for text in REPLY_BUTTON_TEXTS:
        for handler_name in EXPECTED_ROUTING.values():
            assert not _matches(_handler_named(handler_name), text)


def test_plain_text_is_not_claimed_by_the_panel():
    for handler_name in EXPECTED_ROUTING.values():
        assert not _matches(_handler_named(handler_name), "سلام")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_command_opens_the_panel(db, admin_ids):
    message = FakeMessage(text="/admin")

    await admin_panel.cmd_admin(message)

    assert message.last_text == ADMIN_PANEL_TITLE.format(exit_button=BTN_ADMIN_EXIT)
    assert [b.text for row in message.last_markup.keyboard for b in row] == list(
        ADMIN_BUTTON_TEXTS
    )


@pytest.mark.asyncio
async def test_panel_command_is_an_alias(db, admin_ids):
    via_admin = FakeMessage(text="/admin")
    via_panel = FakeMessage(text="/panel")

    await admin_panel.cmd_admin(via_admin)
    await admin_panel.cmd_panel(via_panel)

    assert via_admin.last_text == via_panel.last_text
    assert via_admin.last_markup.keyboard == via_panel.last_markup.keyboard


@pytest.mark.asyncio
async def test_admin_command_refuses_non_admins(db, admin_ids):
    message = FakeMessage(user_id=STRANGER_ID, text="/admin")

    await admin_panel.cmd_admin(message)

    assert message.last_text == ADMIN_ONLY
    assert message.last_markup is None


@pytest.mark.asyncio
async def test_every_panel_button_refuses_non_admins(db, admin_ids):
    for text, handler_name in EXPECTED_ROUTING.items():
        message = FakeMessage(user_id=STRANGER_ID, text=text)
        handler = _handler_named(handler_name).callback

        await handler(message)

        assert message.sent, f"{handler_name} answered nothing for {text!r}"
        assert message.last_text == ADMIN_ONLY, f"{handler_name} leaked data to a stranger"


@pytest.mark.asyncio
async def test_exit_restores_the_user_keyboard(db, admin_ids):
    message = FakeMessage(text=BTN_ADMIN_EXIT)

    await admin_panel.on_exit(message)

    assert message.last_text == ADMIN_PANEL_EXIT
    assert [b.text for row in message.last_markup.keyboard for b in row] == list(
        REPLY_BUTTON_TEXTS
    )
    assert main_menu().keyboard == message.last_markup.keyboard


# ---------------------------------------------------------------------------
# Button bodies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_button_without_payments(db, admin_ids):
    await _clear_pending_payments()
    message = FakeMessage(text=BTN_ADMIN_PENDING)

    await admin_panel.on_pending(message)

    assert message.last_text == NO_PENDING_PAYMENTS
    assert NO_PENDING_PAYMENTS == "هیچ پرداخت در انتظاری نیست."


@pytest.mark.asyncio
async def test_pending_button_shows_inline_approval_buttons(db, admin_ids):
    await get_or_create_user(777, "payer777", "Panel Payer")
    await create_manual_payment(
        user_id=777, tier="monthly", amount=800000, tracking_number="TRK-PANEL-1"
    )
    message = FakeMessage(text=BTN_ADMIN_PENDING)

    await admin_panel.on_pending(message)

    payment_texts = [text for text, _ in message.sent]
    assert any("TRK-PANEL-1" in text for text in payment_texts)
    keyboards = [kw.get("reply_markup") for _, kw in message.sent if kw.get("reply_markup")]
    assert keyboards, "no approval keyboard was attached"
    callbacks = [b.callback_data for row in keyboards[-1].inline_keyboard for b in row]
    assert any(c.startswith("admin_approve:") for c in callbacks)


@pytest.mark.asyncio
async def test_pending_button_matches_the_slash_command(db, admin_ids):
    await get_or_create_user(778, "payer778", "Panel Payer 2")
    await create_manual_payment(
        user_id=778, tier="yearly", amount=7200000, tracking_number="TRK-PANEL-2"
    )
    button = FakeMessage(text=BTN_ADMIN_PENDING)
    command = FakeMessage(text="/pending")

    await admin_panel.on_pending(button)
    await admin_handlers.cmd_pending(command)

    assert [t for t, _ in button.sent] == [t for t, _ in command.sent]


@pytest.mark.asyncio
async def test_prices_button_matches_the_slash_command(db, admin_ids):
    await reset_prices(None)
    button = FakeMessage(text=BTN_ADMIN_PRICES)
    command = FakeMessage(text="/prices")

    await admin_panel.on_prices(button)
    await admin_handlers.cmd_prices(command)

    assert button.last_text == command.last_text
    assert "ریال" in button.last_text and "استارز" in button.last_text
    for key in ("monthly", "quarterly", "yearly", "addon_2k"):
        assert key in button.last_text


@pytest.mark.asyncio
async def test_card_button_matches_the_slash_command(db, admin_ids):
    await seed_settings("6037-1111-2222-3333", "Grabora")
    button = FakeMessage(text=BTN_ADMIN_CARD)
    command = FakeMessage(text="/card")

    await admin_panel.on_card(button)
    await admin_handlers.cmd_card(command)

    assert button.last_text == command.last_text
    assert "6037-1111-2222-3333" in button.last_text


@pytest.mark.asyncio
async def test_stats_button_matches_the_slash_command(db, admin_ids):
    button = FakeMessage(text=BTN_ADMIN_STATS)
    command = FakeMessage(text="/stats")

    await admin_panel.on_stats(button)
    await admin_handlers.cmd_stats(command)

    assert button.last_text == command.last_text
    assert "کاربران" in button.last_text


@pytest.mark.asyncio
async def test_broadcast_button_asks_for_a_text_instead_of_broadcasting_its_label(db, admin_ids):
    bot = type("B", (), {"sent": []})()
    message = FakeMessage(bot=bot, text=BTN_ADMIN_BROADCAST)

    await admin_panel.on_broadcast(message)

    assert message.last_text == ENTER_BROADCAST_TEXT
    assert bot.sent == []


@pytest.mark.asyncio
async def test_test_alert_button_reports_delivery(db, admin_ids, monkeypatch):
    from bot.utils import admin_notify

    monkeypatch.setattr(admin_notify, "RETRY_BACKOFF_SECONDS", 0.0)

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sent.append((chat_id, text))
            return True

    bot = Bot()
    message = FakeMessage(bot=bot, text=BTN_ADMIN_TEST_ALERT)

    await admin_panel.on_test_alert(message)

    assert bot.sent and bot.sent[0][0] == ADMIN_ID
    assert str(ADMIN_ID) in message.last_text
    assert "✅" in message.last_text


# ---------------------------------------------------------------------------
# End-to-end through the real dispatcher
# ---------------------------------------------------------------------------


class RecordingSession(BaseSession):
    """Network-free bot session that records outgoing calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str | None, object]] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(
            (type(method).__name__, getattr(method, "text", None), getattr(method, "reply_markup", None))
        )
        returning = getattr(method, "__returning__", None)
        if isinstance(returning, type) and issubclass(returning, Message):
            return Message(
                message_id=len(self.calls),
                date=datetime.now(timezone.utc),
                chat=Chat(id=ADMIN_ID, type="private"),
            )
        return True

    async def stream_content(
        self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True
    ):
        if False:  # pragma: no cover - makes this an async generator
            yield b""

    async def close(self) -> None:
        """Nothing to release: this stub never opens a connection."""

    @property
    def texts(self) -> list[str]:
        return [t for name, t, _ in self.calls if name == "SendMessage" and t]

    @property
    def keyboards(self) -> list:
        return [m for name, _, m in self.calls if name == "SendMessage" and m is not None]


@pytest.fixture(scope="session")
def panel_dispatcher() -> Dispatcher:
    """Dispatcher with just the admin panel — its own routers, so it can include
    the module-level router without clashing with the other e2e test."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(AccessMiddleware())
    admin_panel_router.message.middleware(admin_panel.AdminPanelMiddleware())
    dp.include_router(admin_panel_router)
    return dp


@pytest.fixture
def no_throttle(monkeypatch):
    monkeypatch.setattr("bot.middlewares.throttling.THROTTLE_SECONDS", 0.0)


def text_update(text: str, user_id: int, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="Tester"),
            text=text,
        ),
    )


async def tap(dispatcher: Dispatcher, text: str, user_id: int, update_id: int = 1):
    session = RecordingSession()
    bot = Bot(token=TOKEN, session=session)
    try:
        await dispatcher.feed_update(bot, text_update(text, user_id, update_id))
    finally:
        await bot.session.close()
    return session


@pytest.mark.asyncio
async def test_admin_opens_the_panel_through_the_dispatcher(
    db, admin_ids, no_throttle, panel_dispatcher
):
    session = await tap(panel_dispatcher, "/admin", ADMIN_ID)

    assert session.texts
    assert session.texts[0] == ADMIN_PANEL_TITLE.format(exit_button=BTN_ADMIN_EXIT)
    buttons = [b.text for row in session.keyboards[0].keyboard for b in row]
    assert buttons == list(ADMIN_BUTTON_TEXTS)


@pytest.mark.asyncio
async def test_every_button_tap_is_handled_by_the_dispatcher(
    db, admin_ids, no_throttle, panel_dispatcher
):
    for index, text in enumerate(ADMIN_BUTTON_TEXTS, start=10):
        session = await tap(panel_dispatcher, text, ADMIN_ID, update_id=index)
        assert session.texts, f"tapping {text!r} produced no reply"


@pytest.mark.asyncio
async def test_non_admin_button_tap_is_answered_with_admin_only(
    db, admin_ids, no_throttle, panel_dispatcher
):
    session = await tap(panel_dispatcher, BTN_ADMIN_STATS, STRANGER_ID)

    assert session.texts == [ADMIN_ONLY]


@pytest.mark.asyncio
async def test_non_admin_cannot_open_the_panel(
    db, admin_ids, no_throttle, panel_dispatcher
):
    session = await tap(panel_dispatcher, "/admin", STRANGER_ID)

    assert session.texts == [ADMIN_ONLY]
    assert session.keyboards == []


@pytest.mark.asyncio
async def test_user_menu_button_is_ignored_by_the_panel(
    db, admin_ids, no_throttle, panel_dispatcher
):
    """The panel must not react to the regular user keyboard."""
    session = await tap(panel_dispatcher, BTN_ACCOUNT, ADMIN_ID)

    assert session.texts == []
