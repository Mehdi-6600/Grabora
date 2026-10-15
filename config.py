"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from typing import Final

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw.strip())


def _get_list_int(name: str, default: list[int] | None = None) -> list[int]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return list(default or [])
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            result.append(int(part))
    return result


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

BOT_TOKEN: Final[str] = _get_str("BOT_TOKEN")
BOT_API_URL: Final[str] = _get_str("BOT_API_URL", "https://api.telegram.org")
ADMIN_IDS: Final[list[int]] = _get_list_int("ADMIN_IDS")

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

DATABASE_PATH: Final[str] = _get_str("DATABASE_PATH", "./grabora.db")
TEMP_DIR: Final[str] = _get_str("TEMP_DIR", "./tmp/downloads")
MAX_FILE_SIZE_MB: Final[int] = _get_int("MAX_FILE_SIZE_MB", 1900)
FREE_MAX_FILE_SIZE_MB: Final[int] = 500

# Card-to-card (manual) payment
CARD_NUMBER: Final[str] = _get_str("CARD_NUMBER")
CARD_HOLDER: Final[str] = _get_str("CARD_HOLDER")

# YouTube cookies (needed on cloud/datacenter IPs such as Render, which
# YouTube treats as bots). Provide the Netscape cookies.txt as base64.
YOUTUBE_COOKIES_B64: Final[str] = _get_str("YOUTUBE_COOKIES_B64")
YOUTUBE_COOKIES_FILE: Final[str] = _get_str("YOUTUBE_COOKIES_FILE", "/tmp/cookies.txt")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL: Final[str] = _get_str("LOG_LEVEL", "INFO").upper()
LOG_FILE: Final[str] = _get_str("LOG_FILE", "logs/grabora.log")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

CLEANUP_INTERVAL_HOURS: Final[int] = _get_int("CLEANUP_INTERVAL_HOURS", 1)
TEMP_FILE_MAX_AGE_HOURS: Final[int] = _get_int("TEMP_FILE_MAX_AGE_HOURS", 1)

# ---------------------------------------------------------------------------
# Subscription tiers
# ---------------------------------------------------------------------------

TIER_FREE: Final[str] = "free"
TIER_MONTHLY: Final[str] = "monthly"
TIER_QUARTERLY: Final[str] = "quarterly"
TIER_YEARLY: Final[str] = "yearly"

TIER_DAILY_LIMITS: Final[dict[str, int]] = {
    TIER_FREE: 3,
    TIER_MONTHLY: 30,
    TIER_QUARTERLY: 50,
    TIER_YEARLY: -1,  # unlimited
}

TIER_DURATIONS_DAYS: Final[dict[str, int]] = {
    TIER_MONTHLY: 30,
    TIER_QUARTERLY: 90,
    TIER_YEARLY: 365,
}

# Package prices — single source of truth.
# Keys: monthly | quarterly | yearly | addon_2k
PACKAGE_PRICES_IRR: Final[dict[str, int]] = {
    "monthly": 50_000,
    "quarterly": 120_000,
    "yearly": 400_000,
    "addon_2k": 30_000,
}

PACKAGE_PRICES_STARS: Final[dict[str, int]] = {
    "monthly": 50,
    "quarterly": 120,
    "yearly": 400,
    "addon_2k": 30,
}

# 2K (1440p) addon validity in days — its price lives in PACKAGE_PRICES_*["addon_2k"].
ADDON_2K_DURATION_DAYS: Final[int] = 30

# Canonical package key for the 2K addon (used as payments.tier for card payments).
ADDON_2K_TIER: Final[str] = "addon_2k"

# Package keys in display order (monthly/quarterly/yearly/addon_2k).
PACKAGE_KEYS: Final[tuple[str, ...]] = (
    TIER_MONTHLY,
    TIER_QUARTERLY,
    TIER_YEARLY,
    ADDON_2K_TIER,
)

# ---------------------------------------------------------------------------
# Quality gating
# ---------------------------------------------------------------------------

FREE_VIDEO_MAX_HEIGHT: Final[int] = 480
FREE_AUDIO_MAX_KBPS: Final[int] = 192
TWO_K_HEIGHT: Final[int] = 1440

# ---------------------------------------------------------------------------
# Queue / throttling
# ---------------------------------------------------------------------------

GLOBAL_CONCURRENCY: Final[int] = 3
PER_USER_CONCURRENCY: Final[int] = 1
THROTTLE_SECONDS: Final[float] = 2.0
DOWNLOAD_RETRY_COUNT: Final[int] = 1

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate() -> None:
    """Raise if required configuration is missing."""
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment")
