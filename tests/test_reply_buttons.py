"""Reply-keyboard button routing tests.

Telegram delivers reply-keyboard taps as plain text, so these tests check both
halves of the contract: that the keyboard sends the shared ``BTN_*`` texts and
that the router's filters actually match them.
"""

import asyncio

import pytest

from bot.handlers.start import (
    _account_text,
    on_account_button,
    on_download_button,
    on_help_button,
    on_subscription_button,
    router as start_router,
)
from bot.keyboards.reply import (
    BTN_ACCOUNT,
    BTN_DOWNLOAD,
    BTN_HELP,
    BTN_SUBSCRIPTION,
    REPLY_BUTTON_TEXTS,
    main_menu,
)
from bot.utils.messages import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_FREE,
    DOWNLOAD_PROMPT,
    HELP,
    SUBSCRIPTION_INFO,
)
from config import TIER_DAILY_LIMITS, TIER_FREE, TIER_MONTHLY
from database.models import User


class FakeMessage:
    """Minimal Message stand-in recording what the handler sends."""

    def __init__(self, text=None):
        self.text = text
        self.entities = None
        self.caption = None
        self.from_user = None
        self.sent: list[tuple[str, dict]] = []

    async def answer(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]


def _matches(handler, text) -> bool:
    """Evaluate a registered handler's filters against a text message."""
    fake = FakeMessage(text)
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
    for handler in start_router.message.handlers:
        if getattr(handler.callback, "__name__", "") == name:
            return handler
    raise AssertionError(f"handler {name} is not registered on the start router")


# ---------------------------------------------------------------------------
# Keyboard <-> constants stay in sync
# ---------------------------------------------------------------------------


def test_keyboard_texts_are_the_shared_constants():
    buttons = [b.text for row in main_menu().keyboard for b in row]
    assert buttons == list(REPLY_BUTTON_TEXTS)
    assert BTN_DOWNLOAD in buttons
    assert BTN_SUBSCRIPTION in buttons
    assert BTN_ACCOUNT in buttons
    assert BTN_HELP in buttons


def test_button_labels_live_in_exactly_one_place():
    """The label constants must not be duplicated in bot/utils/messages.py.

    A second copy is what made the keyboard and the handlers drift apart.
    """
    import bot.utils.messages as messages

    assert not hasattr(messages, "BTN_SUBSCRIPTION")
    assert not hasattr(messages, "BTN_HELP")
    assert not hasattr(messages, "BTN_DOWNLOAD")
    assert not hasattr(messages, "BTN_ACCOUNT")


def test_button_texts_match_the_expected_persian_labels():
    # These exact strings are what Telegram sends back; changing them is a
    # breaking change for every deployed client.
    assert BTN_DOWNLOAD == "🚀 دانلود"
    assert BTN_SUBSCRIPTION == "اشتراک"
    assert BTN_ACCOUNT == "👤 حساب من"
    assert BTN_HELP == "راهنما"


# ---------------------------------------------------------------------------
# The router routes each button text to its handler
# ---------------------------------------------------------------------------


def test_subscription_button_is_routed():
    handler = _handler_named("on_subscription_button")
    assert _matches(handler, BTN_SUBSCRIPTION)
    # And it must not swallow the other buttons.
    assert not _matches(handler, BTN_HELP)
    assert not _matches(handler, BTN_ACCOUNT)
    assert not _matches(handler, BTN_DOWNLOAD)


def test_help_button_is_routed():
    handler = _handler_named("on_help_button")
    assert _matches(handler, BTN_HELP)
    assert not _matches(handler, BTN_SUBSCRIPTION)


def test_account_and_download_buttons_are_routed():
    assert _matches(_handler_named("on_account_button"), BTN_ACCOUNT)
    assert _matches(_handler_named("on_download_button"), BTN_DOWNLOAD)


def test_buttons_do_not_hijack_other_text():
    for name in (
        "on_subscription_button",
        "on_help_button",
        "on_account_button",
        "on_download_button",
    ):
        handler = _handler_named(name)
        assert not _matches(handler, "سلام")
        assert not _matches(handler, "https://youtu.be/abc")


# ---------------------------------------------------------------------------
# Handler behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_button_shows_subscription_info(db):
    user = User(user_id=401, tier=TIER_FREE, daily_downloads=2)
    message = FakeMessage(BTN_SUBSCRIPTION)

    await on_subscription_button(message, user)

    expected = SUBSCRIPTION_INFO.format(
        tier="رایگان", used=2, limit=TIER_DAILY_LIMITS[TIER_FREE]
    )
    assert message.last_text == expected
    # The subscription keyboard with the package buttons is attached.
    markup = message.sent[-1][1]["reply_markup"]
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "sub:monthly" in callbacks
    assert "sub:addon2k" in callbacks


@pytest.mark.asyncio
async def test_help_button_sends_help_and_keeps_the_menu(db):
    message = FakeMessage(BTN_HELP)

    await on_help_button(message)

    assert message.last_text == HELP
    assert message.sent[-1][1]["reply_markup"] is not None


@pytest.mark.asyncio
async def test_download_button_prompts_for_a_link(db):
    message = FakeMessage(BTN_DOWNLOAD)

    await on_download_button(message)

    assert message.last_text == DOWNLOAD_PROMPT
    assert "لینک" in message.last_text


@pytest.mark.asyncio
async def test_account_button_shows_free_user_summary(db):
    user = User(user_id=402, tier=TIER_FREE, daily_downloads=1)
    message = FakeMessage(BTN_ACCOUNT)

    await on_account_button(message, user)

    text = message.last_text
    assert "402" in text
    assert "رایگان" in text
    assert ACCOUNT_STATUS_FREE in text
    assert "غیرفعال" in text  # no 2K addon
    assert f"1/{TIER_DAILY_LIMITS[TIER_FREE]}" in text


@pytest.mark.asyncio
async def test_account_button_shows_paid_summary_with_addon(db):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expiry = now + timedelta(days=20)
    addon_expiry = now + timedelta(days=10)
    user = User(
        user_id=403,
        tier=TIER_MONTHLY,
        tier_expires_at=expiry,
        has_2k_addon=True,
        addon_expires_at=addon_expiry,
        daily_downloads=5,
    )

    text = _account_text(user, now)

    assert "اشتراک ماهانه" in text
    assert ACCOUNT_STATUS_ACTIVE.format(expiry=expiry.strftime("%Y-%m-%d")) in text
    assert "افزونه 2K: فعال" in text
    assert f"5/{TIER_DAILY_LIMITS[TIER_MONTHLY]}" in text


def test_account_text_treats_expired_tier_as_free():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = User(
        user_id=404,
        tier=TIER_MONTHLY,
        tier_expires_at=now - timedelta(days=1),
    )

    text = _account_text(user, now)

    assert ACCOUNT_STATUS_FREE in text


@pytest.mark.asyncio
async def test_unlimited_tier_shows_persian_unlimited(db):
    user = User(user_id=405, tier="yearly", daily_downloads=0)
    message = FakeMessage(BTN_SUBSCRIPTION)

    await on_subscription_button(message, user)

    assert "نامحدود" in message.last_text
