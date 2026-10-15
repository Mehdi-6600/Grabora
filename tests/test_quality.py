from datetime import datetime, timedelta, timezone

from database.models import User
from bot.handlers.download import _quality_allowed


def test_quality_rules():
    free = User(user_id=1, tier="free", has_2k_addon=False)
    paid = User(user_id=2, tier="monthly", has_2k_addon=False)
    addon = User(user_id=3, tier="monthly", has_2k_addon=True)

    assert _quality_allowed(free, "video", "480") is True
    assert _quality_allowed(free, "video", "720") is False
    assert _quality_allowed(paid, "video", "1080") is True
    assert _quality_allowed(paid, "video", "1440") is False
    assert _quality_allowed(addon, "video", "1440") is True

    assert _quality_allowed(free, "audio", "192") is True
    assert _quality_allowed(free, "audio", "320") is False
    assert _quality_allowed(free, "audio", "flac") is False
    assert _quality_allowed(paid, "audio", "320") is True
    assert _quality_allowed(paid, "audio", "flac") is True
