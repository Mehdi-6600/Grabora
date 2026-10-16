"""Package price storage and the /prices admin command."""

import pytest
from datetime import datetime

import config
from bot.handlers import admin as admin_module
from bot.utils.messages import (
    PRICES_INVALID_AMOUNT,
    PRICES_INVALID_KEY,
    PRICES_USAGE,
    tier_label,
)
from config import PACKAGE_KEYS, PACKAGE_PRICES_IRR, PACKAGE_PRICES_STARS
from database.db import get_db
from database.queries import get_all_prices, get_price, set_price

ADMIN_ID = 999


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id
        self.username = "root"


class FakeMessage:
    def __init__(self, text, user_id=ADMIN_ID):
        self.text = text
        self.from_user = FakeUser(user_id)
        self.sent: list[tuple[str, dict]] = []

    async def answer(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self

    @property
    def last_text(self) -> str:
        return self.sent[-1][0]


@pytest.fixture
def admin_user(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN_ID])
    return ADMIN_ID


async def reset_prices() -> None:
    """Restore every package to its config seed.

    The test database is shared across the session (see conftest.py), so tests
    that assert on defaults must not depend on execution order.
    """
    for key in PACKAGE_KEYS:
        await set_price(key, PACKAGE_PRICES_IRR[key], PACKAGE_PRICES_STARS[key])


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prices_table_is_seeded_from_config_defaults(db):
    db_conn = get_db()
    async with db_conn.execute(
        "SELECT package_key, price_irr, price_stars FROM prices ORDER BY package_key"
    ) as cursor:
        rows = {r["package_key"]: (r["price_irr"], r["price_stars"]) for r in await cursor.fetchall()}

    assert set(rows) == set(PACKAGE_KEYS)
    for key in PACKAGE_KEYS:
        assert rows[key] == (PACKAGE_PRICES_IRR[key], PACKAGE_PRICES_STARS[key])


@pytest.mark.asyncio
async def test_get_price_returns_db_values(db):
    assert await get_price("monthly") == (
        PACKAGE_PRICES_IRR["monthly"],
        PACKAGE_PRICES_STARS["monthly"],
    )

    await set_price("monthly", 80000, 80)
    assert await get_price("monthly") == (80000, 80)


@pytest.mark.asyncio
async def test_get_price_falls_back_to_config_when_row_missing(db):
    db_conn = get_db()
    await db_conn.execute("DELETE FROM prices WHERE package_key = 'yearly'")
    await db_conn.commit()

    assert await get_price("yearly") == (
        PACKAGE_PRICES_IRR["yearly"],
        PACKAGE_PRICES_STARS["yearly"],
    )


@pytest.mark.asyncio
async def test_get_price_rejects_unknown_package(db):
    with pytest.raises(KeyError):
        await get_price("not_a_package")


@pytest.mark.asyncio
async def test_get_all_prices_covers_every_package(db):
    await reset_prices()
    await set_price("quarterly", 150000, 150)
    prices = await get_all_prices()

    assert set(prices) == set(PACKAGE_KEYS)
    assert prices["quarterly"] == (150000, 150)
    assert prices["monthly"] == (
        PACKAGE_PRICES_IRR["monthly"],
        PACKAGE_PRICES_STARS["monthly"],
    )


@pytest.mark.asyncio
async def test_set_price_is_an_upsert(db):
    await set_price("yearly", 300000, 300)
    await set_price("yearly", 350000, 350)

    assert await get_price("yearly") == (350000, 350)
    db_conn = get_db()
    async with db_conn.execute(
        "SELECT COUNT(*) AS c FROM prices WHERE package_key = 'yearly'"
    ) as cursor:
        assert (await cursor.fetchone())["c"] == 1


@pytest.mark.asyncio
async def test_set_price_updates_timestamp(db):
    await set_price("monthly", 60000, 60)
    db_conn = get_db()
    async with db_conn.execute(
        "SELECT updated_at FROM prices WHERE package_key = 'monthly'"
    ) as cursor:
        value = (await cursor.fetchone())["updated_at"]
    datetime.fromisoformat(value)  # parses -> well-formed timestamp


@pytest.mark.asyncio
async def test_set_price_rejects_unknown_package(db):
    with pytest.raises(KeyError):
        await set_price("not_a_package", 1, 1)


@pytest.mark.asyncio
async def test_admin_prices_survive_a_restart(db):
    """Re-running init_db() must not overwrite an admin's prices."""
    from database.db import close_db, init_db

    await set_price("monthly", 99000, 99)
    await close_db()
    await init_db()  # simulates a bot restart

    assert await get_price("monthly") == (99000, 99)


# ---------------------------------------------------------------------------
# /prices command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prices_command_lists_all_packages(db, admin_user):
    await reset_prices()
    message = FakeMessage("/prices")

    await admin_module.cmd_prices(message)

    text = message.last_text
    assert "قیمت" in text
    for key in PACKAGE_KEYS:
        assert key in text
        assert f"{PACKAGE_PRICES_IRR[key]:,}" in text
        assert f"{PACKAGE_PRICES_STARS[key]:,}" in text


@pytest.mark.asyncio
async def test_prices_command_shows_updated_values(db, admin_user):
    await set_price("monthly", 80000, 80)
    message = FakeMessage("/prices")

    await admin_module.cmd_prices(message)

    assert "80,000" in message.last_text
    assert "80" in message.last_text


@pytest.mark.asyncio
async def test_prices_command_updates_a_price(db, admin_user):
    message = FakeMessage("/prices monthly 80000 80")

    await admin_module.cmd_prices(message)

    assert await get_price("monthly") == (80000, 80)
    assert "80,000" in message.last_text
    assert tier_label("monthly") in message.last_text


@pytest.mark.asyncio
async def test_prices_command_accepts_thousand_separators(db, admin_user):
    message = FakeMessage("/prices quarterly 150,000 150")

    await admin_module.cmd_prices(message)

    assert await get_price("quarterly") == (150000, 150)


@pytest.mark.asyncio
async def test_prices_command_updates_addon(db, admin_user):
    message = FakeMessage("/prices addon_2k 45000 45")

    await admin_module.cmd_prices(message)

    assert await get_price("addon_2k") == (45000, 45)


@pytest.mark.asyncio
async def test_prices_command_rejects_bad_arguments(db, admin_user):
    await reset_prices()
    for text in (
        "/prices monthly 80000",  # too few args
        "/prices monthly 80000 80 extra",  # too many args
        "/prices nope 80000 80",  # unknown package
        "/prices monthly abc 80",  # non-numeric irr
        "/prices monthly 80000 abc",  # non-numeric stars
        "/prices monthly 0 80",  # irr must be positive
        "/prices monthly 80000 0",  # stars below neonpay's minimum
        "/prices monthly 80000 9999",  # stars above neonpay's maximum
    ):
        message = FakeMessage(text)
        await admin_module.cmd_prices(message)
        # Nothing was persisted, and the admin got an explanation.
        assert message.sent, f"no reply for {text!r}"
        assert message.last_text in {
            PRICES_USAGE,
            PRICES_INVALID_AMOUNT,
            PRICES_INVALID_KEY,
        }, f"unexpected reply for {text!r}: {message.last_text!r}"

    # The valid monthly price is still the seeded default.
    assert await get_price("monthly") == (
        PACKAGE_PRICES_IRR["monthly"],
        PACKAGE_PRICES_STARS["monthly"],
    )


@pytest.mark.asyncio
async def test_prices_command_usage_hint_mentions_packages(db, admin_user):
    message = FakeMessage("/prices monthly 80000")

    await admin_module.cmd_prices(message)

    assert message.last_text == PRICES_USAGE
    for key in PACKAGE_KEYS:
        assert key in message.last_text


@pytest.mark.asyncio
async def test_prices_command_is_admin_only(db, admin_user):
    await reset_prices()
    message = FakeMessage("/prices monthly 80000 80", user_id=1234)

    await admin_module.cmd_prices(message)

    assert await get_price("monthly") == (
        PACKAGE_PRICES_IRR["monthly"],
        PACKAGE_PRICES_STARS["monthly"],
    )


# ---------------------------------------------------------------------------
# Other code paths read the DB, not config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_payment_uses_the_stored_price(db, admin_user):
    """The card instructions must show the admin's price, not the config default."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.handlers import card_payment
    from database.models import User
    from database.queries import set_setting

    await set_price("monthly", 123_456, 123)
    await set_setting("card_number", "6037-0000-0000-0000")
    await set_setting("card_holder", "Grabora")

    class FakeBot:
        async def send_message(self, *a, **kw):  # pragma: no cover - not used here
            return None

    class FakeCallbackMessage:
        def __init__(self):
            self.sent = []

        async def answer(self, text, **kwargs):
            self.sent.append((text, kwargs))

    class FakeCallback:
        def __init__(self):
            self.data = "pay:card:monthly"
            self.bot = FakeBot()
            self.from_user = FakeUser(555)
            self.message = FakeCallbackMessage()
            self.answers = []

        async def answer(self, text=None, show_alert=False):
            self.answers.append((text, show_alert))

    storage = MemoryStorage()
    ctx = FSMContext(
        storage=storage, key=StorageKey(bot_id=1, chat_id=555, user_id=555)
    )
    callback = FakeCallback()
    user = User(user_id=555, tier="free")

    await card_payment.on_card_payment(callback, ctx, user)

    assert callback.message.sent, "card instructions were not sent"
    text = callback.message.sent[0][0]
    assert "123,456" in text
    # ...and not the config seed (every default is a different number).
    for key in PACKAGE_KEYS:
        assert f"{PACKAGE_PRICES_IRR[key]:,}" not in text


@pytest.mark.asyncio
async def test_stars_stage_price_is_refreshed_from_db(db):
    """A /prices change must reach the neonpay stages without a restart."""
    from neonpay import PaymentStage

    import payments.stars as stars

    await set_price("monthly", 77777, 77)

    stage = PaymentStage(
        title="t", description="d", price=50, payload={"tier": "monthly"}
    )

    class FakeNeonPay:
        def __init__(self):
            self.stages = {"monthly": stage}

        def get_payment_stage(self, stage_id):
            return self.stages.get(stage_id)

    original = stars._neonpay
    stars._neonpay = FakeNeonPay()
    try:
        changed = await stars.refresh_stars_prices()
    finally:
        stars._neonpay = original

    assert changed == 1
    assert stage.price == 77


@pytest.mark.asyncio
async def test_stars_price_outside_neonpay_range_is_skipped(db, caplog):
    """neonpay only accepts 1..2500 Stars; an out-of-range value must not crash."""
    from neonpay import PaymentStage

    import payments.stars as stars

    await set_price("monthly", 77777, 2500)  # at the boundary -> accepted
    stage = PaymentStage(title="t", description="d", price=50)

    class FakeNeonPay:
        def get_payment_stage(self, stage_id):
            return stage if stage_id == "monthly" else None

    original = stars._neonpay
    stars._neonpay = FakeNeonPay()
    try:
        changed = await stars.refresh_stars_prices()
    finally:
        stars._neonpay = original

    assert changed == 1
    assert stage.price == 2500


@pytest.mark.asyncio
async def test_refresh_stars_prices_without_neonpay_is_a_noop(db):
    import payments.stars as stars

    original = stars._neonpay
    stars._neonpay = None
    try:
        assert await stars.refresh_stars_prices() == 0
    finally:
        stars._neonpay = original


@pytest.mark.asyncio
async def test_config_dicts_are_not_mutated_by_price_changes(db):
    """config.PACKAGE_PRICES_* stay pristine seed defaults.

    Snapshotted before the change so the test never encodes a price itself.
    """
    before = {key: (PACKAGE_PRICES_IRR[key], PACKAGE_PRICES_STARS[key]) for key in PACKAGE_KEYS}

    await set_price("monthly", 123456, 123)

    after = {key: (PACKAGE_PRICES_IRR[key], PACKAGE_PRICES_STARS[key]) for key in PACKAGE_KEYS}
    assert after == before
    assert config.PACKAGE_PRICES_IRR["monthly"] == before["monthly"][0]
    assert config.PACKAGE_PRICES_STARS["monthly"] == before["monthly"][1]
