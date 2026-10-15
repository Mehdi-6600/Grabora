import pytest

from bot.middlewares.access import AccessResult, check_download_access
from bot.utils.helpers import is_admin
from database.models import User


def _future(days: int = 30):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)


def _free(user_id: int = 1) -> User:
    return User(user_id=user_id, tier="free", has_2k_addon=False)


def _paid(user_id: int = 2) -> User:
    return User(user_id=user_id, tier="monthly", tier_expires_at=_future(), has_2k_addon=False)


def _paid_with_2k(user_id: int = 3) -> User:
    return User(
        user_id=user_id,
        tier="monthly",
        tier_expires_at=_future(),
        has_2k_addon=True,
        addon_expires_at=_future(),
    )


def test_is_admin():
    assert is_admin(1, [1, 2])
    assert not is_admin(3, [1, 2])


@pytest.mark.asyncio
async def test_free_720p_needs_subscription(db):
    result = await check_download_access(_free(101), "video", "720")
    assert result is AccessResult.NEED_SUBSCRIPTION


@pytest.mark.asyncio
async def test_free_1080p_needs_subscription(db):
    result = await check_download_access(_free(102), "video", "1080")
    assert result is AccessResult.NEED_SUBSCRIPTION


@pytest.mark.asyncio
async def test_free_480p_400mb_allowed(db):
    result = await check_download_access(
        _free(103), "video", "480", estimated_size_mb=400
    )
    assert result is AccessResult.ALLOWED


@pytest.mark.asyncio
async def test_free_480p_600mb_size_limit_reached(db):
    result = await check_download_access(
        _free(104), "video", "480", estimated_size_mb=600
    )
    assert result is AccessResult.SIZE_LIMIT_REACHED


@pytest.mark.asyncio
async def test_paid_1080p_900mb_allowed(db):
    result = await check_download_access(
        _paid(105), "video", "1080", estimated_size_mb=900
    )
    assert result is AccessResult.ALLOWED


@pytest.mark.asyncio
async def test_paid_1440p_without_addon_needs_2k_addon(db):
    result = await check_download_access(_paid(106), "video", "1440")
    assert result is AccessResult.NEED_2K_ADDON


@pytest.mark.asyncio
async def test_paid_1440p_with_active_addon_allowed(db):
    result = await check_download_access(_paid_with_2k(107), "video", "1440")
    assert result is AccessResult.ALLOWED


@pytest.mark.asyncio
async def test_expired_addon_needs_2k_addon(db):
    from datetime import datetime, timedelta, timezone

    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    user = User(
        user_id=108,
        tier="monthly",
        tier_expires_at=_future(),
        has_2k_addon=True,
        addon_expires_at=past,
    )
    result = await check_download_access(user, "video", "1440")
    assert result is AccessResult.NEED_2K_ADDON


@pytest.mark.asyncio
async def test_free_480p_without_estimate_allowed(db):
    result = await check_download_access(_free(109), "video", "480")
    assert result is AccessResult.ALLOWED


@pytest.mark.asyncio
async def test_paid_over_1900mb_size_limit_reached(db):
    result = await check_download_access(
        _paid(110), "video", "1080", estimated_size_mb=2000
    )
    assert result is AccessResult.SIZE_LIMIT_REACHED
