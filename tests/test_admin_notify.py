"""Admin notification delivery: escaping, retry, reporting, /test_alert.

Regression tests for the reported bug "the admin never receives a notification
when a non-admin submits a card payment".
"""

import logging
import re

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

import config
from bot.handlers import admin as admin_module
from bot.handlers.admin import send_test_alert
from bot.handlers.card_payment import build_admin_payment_text, notify_admins_new_payment
from bot.keyboards.inline import admin_approval_keyboard
from bot.utils import admin_notify
from bot.utils.admin_notify import (
    escape_markdown,
    sanitize_code_span,
    send_to_admins,
)
from bot.utils.messages import (
    ADMIN_NOTIFY_START_HINT,
    ADMIN_TEST_ALERT_NO_ADMINS,
    ADMIN_TEST_ALERT_ROW_FAILED,
    ADMIN_TEST_ALERT_ROW_OK,
    ADMIN_TEST_ALERT_TEXT,
)
from database.models import Payment, User

ADMIN_A = 111
ADMIN_B = 222
_METHOD = SendMessage(chat_id=1, text="x")


def forbidden() -> TelegramForbiddenError:
    return TelegramForbiddenError(
        method=_METHOD, message="Forbidden: bot can't initiate conversation with a user"
    )


def parse_error() -> TelegramBadRequest:
    return TelegramBadRequest(method=_METHOD, message="Bad Request: can't parse entities")


class FakeBot:
    """Records sends; can be told to raise per attempt/chat."""

    def __init__(self, errors: dict[int, list[Exception | None]] | None = None):
        self.sent: list[tuple[int, str, dict]] = []
        self.errors = errors or {}
        self.calls = 0

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.calls += 1
        queue = self.errors.get(chat_id)
        if queue:
            error = queue.pop(0)
            if error is not None:
                raise error
        self.sent.append((chat_id, text, kwargs))
        return True


class FakeMessage:
    def __init__(self, bot, user_id: int):
        self.bot = bot
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[str] = []

    async def answer(self, text, **kwargs):
        self.sent.append(text)
        return self

    @property
    def last_text(self) -> str:
        return self.sent[-1]


class RealisticTelegramBot:
    """Simulates Telegram's legacy-Markdown parser and the /start restriction.

    * a message with unbalanced ``_``/``*``/backtick is rejected with
      "can't parse entities" (exactly what happens in production),
    * chats in ``unreachable`` answer "Forbidden: bot can't initiate
      conversation with a user".
    """

    def __init__(self, unreachable: set[int] | None = None):
        self.sent: list[tuple[int, str, dict]] = []
        self.unreachable = unreachable or set()

    @staticmethod
    def _unbalanced(text: str) -> bool:
        """True when an *unescaped* entity character is left dangling."""
        # Telegram treats "\x" as the literal character x, so drop escapes first.
        stripped = re.sub(r"\\[_*`\[]", "", text)
        for char in ("_", "*", "`"):
            if stripped.count(char) % 2:
                return True
        return False

    async def send_message(self, chat_id: int, text: str, **kwargs):
        if chat_id in self.unreachable:
            raise forbidden()
        if kwargs.get("parse_mode") == "Markdown" and self._unbalanced(text):
            raise parse_error()
        self.sent.append((chat_id, text, kwargs))
        return True


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """No real sleeping in tests."""
    monkeypatch.setattr(admin_notify, "RETRY_BACKOFF_SECONDS", 0.0)


# ---------------------------------------------------------------------------
# Escaping (the root cause of the lost notifications)
# ---------------------------------------------------------------------------


def test_escape_markdown_neutralises_unbalanced_characters():
    assert escape_markdown("Ali_Reza_i") == r"Ali\_Reza\_i"
    assert escape_markdown("*bold") == r"\*bold"
    assert escape_markdown("a[b") == r"a\[b"
    assert escape_markdown("") == ""
    assert escape_markdown(None) == ""


def test_escape_markdown_leaves_safe_text_alone():
    assert escape_markdown("علی رضایی") == "علی رضایی"
    assert escape_markdown("ali") == "ali"


def test_sanitize_code_span_drops_backticks():
    # A backtick cannot be escaped inside a code span, so it is removed.
    assert sanitize_code_span("TRK`1") == "TRK1"
    assert sanitize_code_span(None) == ""


def test_admin_payment_text_escapes_user_controlled_values():
    payment = Payment(id=5, user_id=9, tier="monthly", amount=800000, tracking_number="TRK-1")
    user = User(user_id=9, username="ali_reza_72x", full_name="Ali_Reza_i")

    text = build_admin_payment_text(payment, user)
    assert r"ali\_reza\_72x" in text
    assert r"Ali\_Reza\_i" in text
    # Nothing left for Telegram's parser to choke on.
    assert not RealisticTelegramBot._unbalanced(text)


def test_admin_payment_text_raw_mode_is_unescaped():
    payment = Payment(id=5, user_id=9, tier="monthly", amount=800000)
    user = User(user_id=9, username="ali_reza_72x", full_name="Ali_Reza_i")

    text = build_admin_payment_text(payment, user, markdown=False)
    assert "ali_reza_72x" in text
    assert r"\_" not in text


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notification_reaches_admins_with_a_hostile_name(db, monkeypatch):
    """The reported bug: a user name that breaks Markdown killed the notice."""
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    bot = RealisticTelegramBot()
    payment = Payment(id=11, user_id=555, tier="monthly", amount=800000, tracking_number="TRK-9")
    user = User(user_id=555, username="user_99", full_name="Ali_Reza_i")

    results = await notify_admins_new_payment(bot, payment, user)

    assert [r.ok for r in results] == [True]
    assert len(bot.sent) == 1
    chat_id, text, kwargs = bot.sent[0]
    assert chat_id == ADMIN_A
    assert "800,000" in text
    assert "TRK-9" in text
    # Markdown survived (escaped), so this is not a degraded send.
    assert kwargs["parse_mode"] == "Markdown"
    assert results[0].degraded is False


@pytest.mark.asyncio
async def test_notification_falls_back_to_plain_text_when_markdown_is_rejected(db, monkeypatch):
    """Even an unescapable body must still reach the admin."""
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    bot = RealisticTelegramBot()

    # Force the Markdown send to be rejected regardless of escaping.
    results = await send_to_admins(
        bot,
        "💥 *broken markdown* _here",
        parse_mode="Markdown",
        plain_text="broken markdown here",
    )

    assert [r.ok for r in results] == [True]
    assert results[0].degraded is True
    assert bot.sent[0][1] == "broken markdown here"
    assert "parse_mode" not in bot.sent[0][2]


@pytest.mark.asyncio
async def test_notification_includes_the_approval_keyboard(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A, ADMIN_B])
    bot = RealisticTelegramBot()
    payment = Payment(id=12, user_id=555, tier="yearly", amount=7200000, tracking_number="TRK-8")

    await notify_admins_new_payment(bot, payment, User(user_id=555, username="u", full_name="U"))

    expected = admin_approval_keyboard(payment)
    callbacks = [
        b.callback_data for row in expected.inline_keyboard for b in row
    ]
    assert "admin_approve:12:yearly" in callbacks
    for chat_id, _text, kwargs in bot.sent:
        assert chat_id in {ADMIN_A, ADMIN_B}
        markup = kwargs["reply_markup"]
        assert any(
            b.callback_data == "admin_approve:12:yearly"
            for row in markup.inline_keyboard
            for b in row
        )


@pytest.mark.asyncio
async def test_forbidden_admin_is_reported_with_a_start_hint(db, monkeypatch, caplog):
    """An admin who never started the bot must be reported, not silenced."""
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A, ADMIN_B])
    bot = FakeBot({ADMIN_A: [forbidden()]})

    with caplog.at_level(logging.ERROR):
        results = await send_to_admins(bot, "hello")

    assert [r.ok for r in results] == [False, True]
    failed, delivered = results
    assert failed.admin_id == ADMIN_A
    assert "Forbidden" in (failed.error or "")
    assert failed.hint == ADMIN_NOTIFY_START_HINT
    # Forbidden is permanent: no pointless retry.
    assert failed.attempts == 1
    assert delivered.admin_id == ADMIN_B
    # The failure is loud in the logs.
    assert any("Cannot message admin 111" in r.message for r in caplog.records)
    assert any("/start" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_transient_error_is_retried_once_and_succeeds(db, monkeypatch, caplog):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    bot = FakeBot({ADMIN_A: [TimeoutError("timed out"), None]})

    with caplog.at_level(logging.INFO):
        results = await send_to_admins(bot, "hello")

    assert [r.ok for r in results] == [True]
    assert results[0].attempts == 2
    assert bot.sent[0][0] == ADMIN_A
    assert any("notification failed" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_persistent_transient_error_gives_up_after_one_retry(db, monkeypatch, caplog):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    bot = FakeBot({ADMIN_A: [TimeoutError("t1"), TimeoutError("t2"), None]})

    with caplog.at_level(logging.ERROR):
        results = await send_to_admins(bot, "hello")

    assert [r.ok for r in results] == [False]
    assert results[0].attempts == 2  # initial attempt + 1 retry
    assert "t2" in (results[0].error or "")
    assert any("Giving up on admin 111" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_retry_after_is_honoured(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    bot = FakeBot(
        {ADMIN_A: [TelegramRetryAfter(method=_METHOD, message="Too Many Requests", retry_after=0), None]}
    )

    results = await send_to_admins(bot, "hello")

    assert [r.ok for r in results] == [True]
    assert results[0].attempts == 2


@pytest.mark.asyncio
async def test_empty_admin_ids_is_loud_and_returns_nothing(db, monkeypatch, caplog):
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    bot = FakeBot()

    with caplog.at_level(logging.ERROR):
        results = await send_to_admins(bot, "hello")

    assert results == []
    assert bot.sent == []
    assert any("ADMIN_IDS is empty" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_overall_failure_summary_is_logged(db, monkeypatch, caplog):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A, ADMIN_B])
    bot = FakeBot({ADMIN_A: [forbidden()], ADMIN_B: [forbidden()]})

    with caplog.at_level(logging.ERROR):
        await send_to_admins(bot, "hello")

    assert any(
        "NOT delivered to 2 of 2 admin(s)" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_card_payment_handler_notifies_admins_end_to_end(db, monkeypatch):
    """The exact reported flow: a user sends a tracking number, admins get told."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.handlers.card_payment import (
        CardPaymentStates,
        on_tracking_number,
    )
    from database.queries import get_or_create_user

    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A, ADMIN_B])
    bot = RealisticTelegramBot()
    user = await get_or_create_user(4242, "user_99", "Ali_Reza_i")  # hostile name

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=4242, user_id=4242)
    state = FSMContext(storage=storage, key=key)
    await state.set_state(CardPaymentStates.awaiting_tracking)
    await state.update_data(tier="quarterly", amount=2_100_000)

    message = FakeMessage(bot, 4242)
    message.text = "TRK-E2E-1"

    await on_tracking_number(message, state, user)

    assert message.sent, "the user was not acknowledged"
    # Both admins got the payment card, Markdown intact.
    assert [chat_id for chat_id, _, _ in bot.sent] == [ADMIN_A, ADMIN_B]
    for _chat_id, text, kwargs in bot.sent:
        assert "TRK-E2E-1" in text
        assert "2,100,000" in text
        assert kwargs["parse_mode"] == "Markdown"
        assert kwargs["reply_markup"] is not None
        assert not RealisticTelegramBot._unbalanced(text)


# ---------------------------------------------------------------------------
# /test_alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_alert_reports_each_admin(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A, ADMIN_B])
    bot = FakeBot({ADMIN_B: [forbidden()]})
    message = FakeMessage(bot, ADMIN_A)

    await send_test_alert(message)

    text = message.last_text
    assert ADMIN_TEST_ALERT_ROW_OK.format(admin_id=ADMIN_A) in text
    assert ADMIN_TEST_ALERT_ROW_FAILED.format(
        admin_id=ADMIN_B, error="Telegram server says - Forbidden: bot can't initiate conversation with a user"
    ) in text
    # The actionable hint is included when something failed.
    assert ADMIN_NOTIFY_START_HINT in text
    # The test message itself was delivered to the reachable admin.
    assert bot.sent[0][0] == ADMIN_A
    assert bot.sent[0][1] == ADMIN_TEST_ALERT_TEXT


@pytest.mark.asyncio
async def test_test_alert_reports_full_success(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    message = FakeMessage(FakeBot(), ADMIN_A)

    await send_test_alert(message)

    assert ADMIN_TEST_ALERT_ROW_OK.format(admin_id=ADMIN_A) in message.last_text
    assert ADMIN_NOTIFY_START_HINT not in message.last_text


@pytest.mark.asyncio
async def test_test_alert_without_configured_admins(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    message = FakeMessage(FakeBot(), ADMIN_A)

    await send_test_alert(message)

    assert message.last_text == ADMIN_TEST_ALERT_NO_ADMINS


@pytest.mark.asyncio
async def test_test_alert_command_is_admin_only(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    bot = FakeBot()
    message = FakeMessage(bot, 999999)

    await admin_module.cmd_test_alert(message)

    assert message.sent
    assert bot.sent == []  # nothing probed on behalf of a stranger


@pytest.mark.asyncio
async def test_test_alert_command_probes_for_an_admin(db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_A])
    bot = FakeBot()
    message = FakeMessage(bot, ADMIN_A)

    await admin_module.cmd_test_alert(message)

    assert len(bot.sent) == 1
