import pytest
from datetime import datetime, timezone

from bot.handlers.admin import ApprovalOutcome, approve_manual_payment
from bot.handlers.card_payment import (
    build_admin_payment_text,
    format_amount,
    is_valid_tracking_number,
    normalize_tracking_number,
)
from bot.keyboards.inline import admin_approval_keyboard
from config import ADDON_2K_TIER, PACKAGE_PRICES_IRR, TIER_FREE
from database.queries import (
    create_manual_payment,
    create_user,
    get_payment_by_tracking_number,
    get_user,
    has_pending_manual,
)


@pytest.mark.asyncio
async def test_one_pending_manual_per_user(db):
    await create_user(301)
    first = await create_manual_payment(301, "monthly", PACKAGE_PRICES_IRR["monthly"], "TRK-AAA1")
    assert first is not None
    assert first.status == "pending_manual"
    assert first.provider == "card"
    assert first.currency == "IRR"
    assert first.tracking_number == "TRK-AAA1"
    assert await has_pending_manual(301) is True

    # Second request while one is pending must be rejected.
    second = await create_manual_payment(301, "yearly", PACKAGE_PRICES_IRR["yearly"], "TRK-BBB2")
    assert second is None
    assert await has_pending_manual(301) is True


@pytest.mark.asyncio
async def test_admin_approval_activates_subscription(db):
    await create_user(302)
    payment = await create_manual_payment(302, "monthly", PACKAGE_PRICES_IRR["monthly"], "TRK-CCC3")

    outcome, approved, other = await approve_manual_payment(payment.id, "monthly")
    assert outcome is ApprovalOutcome.APPROVED
    assert other is None

    user = await get_user(302)
    assert user.tier == "monthly"
    assert user.tier_expires_at is not None
    assert user.tier_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
    assert await has_pending_manual(302) is False

    refreshed = await get_payment_by_tracking_number("TRK-CCC3")
    assert refreshed is not None
    assert refreshed.status == "verified"
    assert refreshed.verified_at is not None


@pytest.mark.asyncio
async def test_duplicate_approval_does_not_double_grant(db):
    await create_user(303)
    payment = await create_manual_payment(303, "monthly", PACKAGE_PRICES_IRR["monthly"], "TRK-DDD4")

    first_outcome, _, _ = await approve_manual_payment(payment.id, "monthly")
    assert first_outcome is ApprovalOutcome.APPROVED
    expiry_after_first = (await get_user(303)).tier_expires_at

    # Clicking approve again (stale message) must not extend the subscription.
    second_outcome, _, _ = await approve_manual_payment(payment.id, "monthly")
    assert second_outcome is ApprovalOutcome.NOT_PENDING
    assert (await get_user(303)).tier_expires_at == expiry_after_first


@pytest.mark.asyncio
async def test_wrong_tier_button_is_ignored(db):
    await create_user(304)
    payment = await create_manual_payment(304, "monthly", PACKAGE_PRICES_IRR["monthly"], "TRK-EEE5")

    outcome, _, _ = await approve_manual_payment(payment.id, "yearly")
    assert outcome is ApprovalOutcome.TIER_MISMATCH

    user = await get_user(304)
    assert user.tier == TIER_FREE
    assert user.tier_expires_at is None
    # Still pending, so it can be approved with the right button later.
    assert await has_pending_manual(304) is True
    outcome_ok, _, _ = await approve_manual_payment(payment.id, "monthly")
    assert outcome_ok is ApprovalOutcome.APPROVED


@pytest.mark.asyncio
async def test_duplicate_tracking_number_detection(db):
    await create_user(305)
    await create_user(306)

    first = await create_manual_payment(305, "monthly", PACKAGE_PRICES_IRR["monthly"], "TRK-DUP9")
    outcome_first, _, _ = await approve_manual_payment(first.id, "monthly")
    assert outcome_first is ApprovalOutcome.APPROVED

    # A second user submits the same tracking number.
    second = await create_manual_payment(306, "monthly", PACKAGE_PRICES_IRR["monthly"], "TRK-DUP9")
    assert second is not None

    outcome, payment, other = await approve_manual_payment(second.id, "monthly")
    assert outcome is ApprovalOutcome.DUPLICATE_TRACKING
    assert other is not None
    assert other.id == first.id

    # Not activated, still pending for manual investigation.
    assert (await get_user(306)).tier == TIER_FREE
    assert (await get_user(306)).tier_expires_at is None
    remaining = await has_pending_manual(306)
    assert remaining is True


@pytest.mark.asyncio
async def test_addon_approval_grants_2k_addon(db):
    await create_user(307)
    payment = await create_manual_payment(
        307, ADDON_2K_TIER, PACKAGE_PRICES_IRR["addon_2k"], "TRK-ADD7"
    )
    assert payment.is_addon is True

    outcome, _, _ = await approve_manual_payment(payment.id, ADDON_2K_TIER)
    assert outcome is ApprovalOutcome.APPROVED

    user = await get_user(307)
    assert user.has_2k_addon is True
    assert user.addon_expires_at is not None


def test_tracking_number_validation():
    assert is_valid_tracking_number("1234")
    assert is_valid_tracking_number("ABC-1234")
    assert is_valid_tracking_number("a" * 64)
    assert not is_valid_tracking_number("abc")  # too short
    assert not is_valid_tracking_number("a" * 65)  # too long
    assert not is_valid_tracking_number("ABC 1234")  # space
    assert not is_valid_tracking_number("ABC_1234")  # invalid char
    assert not is_valid_tracking_number("")
    # Persian digits are normalized to ASCII before validation.
    assert normalize_tracking_number("۱۲۳۴۵") == "12345"
    assert is_valid_tracking_number(normalize_tracking_number("۱۲۳۴۵"))


def test_amount_formatting_and_admin_text():
    assert format_amount(50000) == "50,000"
    assert format_amount(400000) == "400,000"

    from database.models import Payment

    payment = Payment(
        id=42,
        user_id=9,
        provider="card",
        tier="quarterly",
        amount=120000,
        currency="IRR",
        status="pending_manual",
        tracking_number="TRK-XYZ",
        created_at=datetime(2026, 1, 2, 3, 4),
    )
    from database.models import User

    text = build_admin_payment_text(payment, User(user_id=9, username="ali", full_name="Ali A"))
    assert "120,000" in text
    assert "اشتراک سه‌ماهه" in text
    assert "TRK-XYZ" in text
    assert "@ali" in text
    assert "42" in text


def test_admin_keyboard_only_matching_package_enabled():
    from database.models import Payment

    payment = Payment(id=7, user_id=1, tier="yearly", amount=400000, status="pending_manual")
    keyboard = admin_approval_keyboard(payment)
    buttons = [b for row in keyboard.inline_keyboard for b in row]
    assert len(buttons) == 4

    enabled = [b for b in buttons if b.callback_data != "noop"]
    assert len(enabled) == 1
    assert enabled[0].callback_data == "admin_approve:7:yearly"
    assert not enabled[0].text.startswith("🔒")

    locked = [b for b in buttons if b.callback_data == "noop"]
    assert len(locked) == 3
    assert all(b.text.startswith("🔒") for b in locked)

    # Legacy addon rows stored as tier="addon" still enable the 2K button.
    legacy = Payment(id=8, user_id=1, tier="addon", amount=30000, is_addon=True)
    legacy_buttons = [b for row in admin_approval_keyboard(legacy).inline_keyboard for b in row]
    enabled_legacy = [b for b in legacy_buttons if b.callback_data != "noop"]
    assert enabled_legacy[0].callback_data == "admin_approve:8:addon_2k"
