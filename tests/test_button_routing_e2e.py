"""End-to-end routing test for reply-keyboard taps.

Reproduces the reported bug: Telegram delivers a reply-keyboard tap as a plain
text message, so the text must match a handler registered *after* the access
middleware (which injects ``db_user``). This test runs the real dispatcher with
the same middleware/router order as ``main.py``, with a network-free bot session.
"""

from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from bot.handlers import admin, card_payment, download, start, subscription
from bot.handlers.card_payment import TrackingCancelMiddleware
from bot.keyboards.reply import (
    BTN_ACCOUNT,
    BTN_DOWNLOAD,
    BTN_HELP,
    BTN_SUBSCRIPTION,
)
from bot.middlewares.access import AccessMiddleware, AdminMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.utils.messages import DOWNLOAD_PROMPT, HELP, SUBSCRIPTION_INFO
from config import TIER_DAILY_LIMITS, TIER_FREE

TOKEN = "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
USER_ID = 777001


class RecordingSession(BaseSession):
    """Session that records outgoing API calls instead of hitting Telegram."""

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
                chat=Chat(id=USER_ID, type="private"),
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
    def sent_texts(self) -> list[str]:
        return [text for name, text in self.calls if name == "SendMessage" and text]


@pytest.fixture(scope="session")
def dispatcher() -> Dispatcher:
    """Dispatcher wired exactly like main.py (routers can only attach once)."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(TrackingCancelMiddleware())
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    admin.router.message.middleware(AdminMiddleware())
    admin.router.callback_query.middleware(AdminMiddleware())
    dp.include_router(start.router)
    dp.include_router(download.router)
    dp.include_router(card_payment.router)
    dp.include_router(subscription.router)
    dp.include_router(admin.router)
    return dp


async def tap(dispatcher: Dispatcher, text: str, user_id: int, update_id: int = 1):
    """Feed one reply-keyboard tap through the real dispatcher and return the session."""
    session = RecordingSession()
    bot = Bot(token=TOKEN, session=session)
    try:
        await dispatcher.feed_update(bot, text_update(text, user_id=user_id, update_id=update_id))
    finally:
        await bot.session.close()
    return session


def text_update(text: str, user_id: int = USER_ID, update_id: int = 1) -> Update:
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


@pytest.fixture
def throttling_off(monkeypatch):
    monkeypatch.setattr("bot.middlewares.throttling.THROTTLE_SECONDS", 0.0)


@pytest.mark.asyncio
async def test_subscription_button_tap_reaches_the_handler(db, throttling_off, dispatcher):
    """Tapping 'اشتراک' must answer, not be silently dropped."""
    session = await tap(dispatcher, BTN_SUBSCRIPTION, USER_ID)

    assert session.sent_texts, "tapping the subscription button produced no reply"
    # The user is created on first contact, so tier=free and no downloads yet.
    assert session.sent_texts[0] == SUBSCRIPTION_INFO.format(
        tier="رایگان", used=0, limit=TIER_DAILY_LIMITS[TIER_FREE]
    )


@pytest.mark.asyncio
async def test_help_button_tap_reaches_the_handler(db, throttling_off, dispatcher):
    session = await tap(dispatcher, BTN_HELP, USER_ID + 1)

    assert session.sent_texts == [HELP]


@pytest.mark.asyncio
async def test_account_button_tap_answers_with_account_summary(db, throttling_off, dispatcher):
    session = await tap(dispatcher, BTN_ACCOUNT, USER_ID + 2)

    assert session.sent_texts
    assert str(USER_ID + 2) in session.sent_texts[0]


@pytest.mark.asyncio
async def test_download_button_tap_asks_for_a_link_and_stays_out_of_the_downloader(
    db, throttling_off, dispatcher
):
    """'🚀 دانلود' is a prompt; it must not be treated as a media URL."""
    session = await tap(dispatcher, BTN_DOWNLOAD, USER_ID + 3)

    assert session.sent_texts == [DOWNLOAD_PROMPT]


@pytest.mark.asyncio
async def test_plain_text_is_not_hijacked_by_the_buttons(db, throttling_off, dispatcher):
    """Non-button text still flows to the downloader router."""
    session = await tap(dispatcher, "این یک متن معمولی است", USER_ID + 4)

    # Whatever the downloader replies, it must not be the button prompts.
    assert DOWNLOAD_PROMPT not in session.sent_texts


@pytest.mark.asyncio
async def test_slash_commands_still_work(db, throttling_off, dispatcher):
    """The old slash commands must keep working alongside the buttons."""
    session = RecordingSession()
    bot = Bot(token=TOKEN, session=session)
    try:
        await dispatcher.feed_update(
            bot, text_update("/subscription", user_id=USER_ID + 5, update_id=2)
        )
        await dispatcher.feed_update(
            bot, text_update("/help", user_id=USER_ID + 5, update_id=3)
        )
    finally:
        await bot.session.close()

    assert session.sent_texts[0] == SUBSCRIPTION_INFO.format(
        tier="رایگان", used=0, limit=TIER_DAILY_LIMITS[TIER_FREE]
    )
    assert HELP in session.sent_texts


@pytest.mark.asyncio
async def test_button_text_and_command_share_one_body(db, throttling_off, dispatcher):
    """/subscription and the button must produce identical output."""
    session = RecordingSession()
    bot = Bot(token=TOKEN, session=session)
    try:
        await dispatcher.feed_update(
            bot, text_update(BTN_SUBSCRIPTION, user_id=USER_ID + 6, update_id=4)
        )
        await dispatcher.feed_update(
            bot, text_update("/subscription", user_id=USER_ID + 6, update_id=5)
        )
    finally:
        await bot.session.close()

    assert len(session.sent_texts) == 2
    assert session.sent_texts[0] == session.sent_texts[1]
