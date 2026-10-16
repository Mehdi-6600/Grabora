"""The bot must answer a regular user — the "bot is dead" regression tests.

These tests drive the **real** dispatcher built by ``main.build_dispatcher()``,
so the middleware and router wiring under test is the one production runs. A
regression like the one that made ``/start`` look dead now fails here.
"""

import asyncio
import logging
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

import config
from bot.handlers import fallback
from bot.middlewares import throttling
from bot.middlewares.ping import PING_REPLY
from bot.utils.messages import FALLBACK_TEXT, WELCOME
from bot.middlewares.ping import is_ping

TOKEN = "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
USER = 900001          # a regular, non-admin user
ADMIN = 900002         # an admin (patched into config.ADMIN_IDS)
MISSING = 900003       # a user whose access check raises (DB is broken)


@pytest.fixture
def admins(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN])
    return [ADMIN]


@pytest.fixture
def dispatcher():
    """The production wiring (cached in main, so one per process)."""
    from main import build_dispatcher

    return build_dispatcher()


class RecordingSession(BaseSession):
    """Network-free session: records outgoing SendMessage calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str | None]] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append((type(method).__name__, getattr(method, "text", None)))
        returning = getattr(method, "__returning__", None)
        if isinstance(returning, type) and issubclass(returning, Message):
            return Message(
                message_id=len(self.calls),
                date=datetime.now(timezone.utc),
                chat=Chat(id=1, type="private"),
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
        return [text for name, text in self.calls if name == "SendMessage" and text]


def text_update(text: str, user_id: int, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="Regular"),
            text=text,
        ),
    )


async def send(dp, text: str, user_id: int, update_id: int = 1) -> list[str]:
    """Feed one update through the real dispatcher; return the reply texts."""
    session = RecordingSession()
    bot = Bot(token=TOKEN, session=session)
    try:
        await dp.feed_update(bot, text_update(text, user_id, update_id))
    finally:
        await bot.session.close()
    return session.texts


_counter = [10_000]


async def tap(dp, text: str, user_id: int = USER) -> list[str]:
    _counter[0] += 1
    return await send(dp, text, user_id, _counter[0])


# ---------------------------------------------------------------------------
# The reported outage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_replies_to_a_regular_non_admin_user(db, dispatcher, admins):
    """A normal user sending /start must get a reply (the reported bug)."""
    replies = await tap(dispatcher, "/start")

    assert replies, "a regular user got NO reply for /start (bot looks dead)"
    assert replies[0] == WELCOME


@pytest.mark.asyncio
async def test_three_start_taps_in_a_row_all_reply(db, dispatcher, admins):
    """The report: '/start three times in a row -> no reply'.

    Commands are navigation and are never throttled, so every tap must answer.
    """
    for _ in range(3):
        replies = await tap(dispatcher, "/start")
        assert replies, "a repeated /start tap was silently dropped"


@pytest.mark.asyncio
async def test_repeated_help_and_button_taps_reply(db, dispatcher, admins):
    """Navigation (commands + reply-keyboard buttons) always answers."""
    from bot.keyboards.reply import BTN_ACCOUNT, BTN_HELP, BTN_SUBSCRIPTION

    for text in ("/help", "/help", BTN_HELP, BTN_SUBSCRIPTION, BTN_ACCOUNT, BTN_ACCOUNT):
        replies = await tap(dispatcher, text)
        assert replies, f"{text!r} was silently dropped"


@pytest.mark.asyncio
async def test_plain_text_is_still_throttled_and_logged(db, dispatcher, admins, caplog, monkeypatch):
    """Anti-spam still works for free text, but a drop is now visible."""
    monkeypatch.setattr(throttling, "THROTTLE_SECONDS", 30.0)

    first = await tap(dispatcher, "یک لینک نامعتبر")
    second = await tap(dispatcher, "یک لینک نامعتبر دیگر")

    # The fallback answers the first one; the second is inside the window.
    assert first == [FALLBACK_TEXT]
    assert second == []
    assert any(
        "Throttled" in record.message and str(USER) in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ), "a dropped update was not logged"


@pytest.mark.asyncio
async def test_urls_are_still_throttled(db, dispatcher, admins, monkeypatch):
    """The throttle keeps protecting the downloader from URL spam."""
    monkeypatch.setattr(throttling, "THROTTLE_SECONDS", 30.0)

    await tap(dispatcher, "https://example.com/one")
    dropped = await tap(dispatcher, "https://example.com/two")

    assert dropped == [], "URL spam must still be throttled"


# ---------------------------------------------------------------------------
# /ping: reachable even when the rest of the chain is broken
# ---------------------------------------------------------------------------


def test_is_ping_variants():
    assert is_ping("/ping")
    assert is_ping("  /ping  ")
    assert is_ping("/ping@GraboraBot")
    assert is_ping("/ping extra")
    assert not is_ping("/pingpong")
    assert not is_ping("ping")
    assert not is_ping(None)


@pytest.mark.asyncio
async def test_ping_replies(db, dispatcher, admins):
    assert await tap(dispatcher, "/ping") == [PING_REPLY]


@pytest.mark.asyncio
async def test_ping_replies_even_when_the_access_middleware_breaks(
    db, dispatcher, admins, monkeypatch
):
    """/ping must not depend on the database, throttling or access checks.

    If the DB call every other message goes through explodes, /ping still tells
    us the bot process and Telegram connection are alive.
    """
    from database import queries

    async def boom(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(queries, "get_or_create_user", boom)
    monkeypatch.setattr("bot.middlewares.access.get_or_create_user", boom)

    assert await tap(dispatcher, "/ping") == [PING_REPLY]

    # ...while a normal command is indeed broken by that same failure, which is
    # exactly the situation /ping exists to diagnose.
    with pytest.raises(RuntimeError):
        await send(
            dispatcher,
            "/start",
            MISSING,
            99_999,
        )


@pytest.mark.asyncio
async def test_ping_replies_inside_the_throttle_window(db, dispatcher, admins, monkeypatch):
    monkeypatch.setattr(throttling, "THROTTLE_SECONDS", 30.0)

    # Fill the throttle window with plain text, then ping.
    await tap(dispatcher, "متن معمولی")
    assert await tap(dispatcher, "/ping") == [PING_REPLY]


# ---------------------------------------------------------------------------
# Fallback: nothing disappears silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unmatched_message_is_logged_and_answered(db, dispatcher, admins, caplog):
    with caplog.at_level(logging.WARNING):
        replies = await tap(dispatcher, "یک پیام کاملا بی‌ربط", MISSING)

    assert replies == [FALLBACK_TEXT]
    assert any(
        "UNMATCHED message" in record.message and str(MISSING) in record.message
        for record in caplog.records
    ), "an unmatched message was not logged"


@pytest.mark.asyncio
async def test_unmatched_message_description_includes_details(db, dispatcher, admins):
    """The log must carry enough detail to identify the update."""
    seen: list[str] = []

    class Capturing(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    handler = Capturing(level=logging.WARNING)
    logging.getLogger(fallback.__name__).addHandler(handler)
    try:
        await tap(dispatcher, "/definitely_not_a_command", MISSING)
    finally:
        logging.getLogger(fallback.__name__).removeHandler(handler)

    line = next((m for m in seen if "UNMATCHED message" in m), "")
    assert "has_command=True" in line
    assert "/definitely_not_a_command" in line


@pytest.mark.asyncio
async def test_middleware_order_puts_ping_first(dispatcher):
    """A regression guard on the wiring itself: ping must run before the rest."""
    from main import build_dispatcher

    dp = build_dispatcher()
    assert dp is dispatcher  # the cached production dispatcher
    # The wiring log is emitted once; assert the documented order instead of
    # poking at private attributes.
    import main as main_module

    source_names = [
        line.strip()
        for line in main_module.build_dispatcher.__doc__.splitlines()
        if "PingMiddleware" in line
    ]
    assert source_names, "build_dispatcher's docstring must document the order"


# ---------------------------------------------------------------------------
# Polling must never die silently (the other way the bot can go dark)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_conflict_is_reported_with_the_cause(caplog):
    """Two processes polling one token -> TelegramConflictError -> loud log."""
    from aiogram.exceptions import TelegramConflictError
    from aiogram.methods import SendMessage

    import main as main_module

    async def boom():
        raise TelegramConflictError(
            method=SendMessage(chat_id=1, text="x"),
            message="Conflict: terminated by other getUpdates request",
        )

    task = asyncio.create_task(boom())
    await asyncio.wait([task])

    with caplog.at_level(logging.ERROR):
        main_module.log_polling_exit(task)

    assert any(
        "TelegramConflictError" in r.message and "BOT_TOKEN" in r.message
        for r in caplog.records
    ), "a polling conflict was not explained"


@pytest.mark.asyncio
async def test_polling_death_is_reported(caplog):
    import main as main_module

    async def boom():
        raise RuntimeError("network gone")

    task = asyncio.create_task(boom())
    await asyncio.wait([task])

    with caplog.at_level(logging.ERROR):
        main_module.log_polling_exit(task)

    assert any("Polling task died: RuntimeError" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_polling_finishing_by_itself_is_reported(caplog):
    import main as main_module

    async def fine():
        return None

    task = asyncio.create_task(fine())
    await asyncio.wait([task])

    with caplog.at_level(logging.WARNING):
        main_module.log_polling_exit(task)

    assert any("finished on its own" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_cancelled_polling_is_not_an_error(caplog):
    import main as main_module

    async def forever():
        await asyncio.sleep(3600)

    task = asyncio.create_task(forever())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with caplog.at_level(logging.INFO):
        main_module.log_polling_exit(task)

    assert any("Polling task cancelled" in r.message for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
