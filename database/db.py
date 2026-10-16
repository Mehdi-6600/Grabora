"""SQLite database connection management."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import aiosqlite

from config import (
    DATABASE_PATH,
    PACKAGE_KEYS,
    PACKAGE_PRICES_IRR,
    PACKAGE_PRICES_STARS,
)
from database.models import PaymentStatus

logger = logging.getLogger(__name__)

_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS users (
    -- SQLite: auto-rowid | Postgres: replace with SERIAL or GENERATED ALWAYS AS IDENTITY
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    tier TEXT NOT NULL DEFAULT 'free',
    tier_expires_at TIMESTAMP,
    has_2k_addon INTEGER NOT NULL DEFAULT 0,
    addon_expires_at TIMESTAMP,
    daily_downloads INTEGER NOT NULL DEFAULT 0,
    daily_reset_at TIMESTAMP NOT NULL,
    is_banned INTEGER NOT NULL DEFAULT 0,
    reminder_sent_for_expiry TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    -- SQLite: auto-rowid | Postgres: replace with SERIAL or GENERATED ALWAYS AS IDENTITY
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    tier TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    authority TEXT,
    transaction_id TEXT,
    is_addon INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    verified_at TIMESTAMP,
    metadata TEXT,
    tracking_number TEXT,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);

CREATE TABLE IF NOT EXISTS download_logs (
    -- SQLite: auto-rowid | Postgres: replace with SERIAL or GENERATED ALWAYS AS IDENTITY
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    url_domain TEXT,
    media_type TEXT,
    quality TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    file_size_mb REAL,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);

CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments (user_id);
CREATE INDEX IF NOT EXISTS idx_payments_authority ON payments (authority);
CREATE INDEX IF NOT EXISTS idx_download_logs_user_id ON download_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_download_logs_created_at ON download_logs (created_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Admin-editable package prices. config.PACKAGE_PRICES_* are only seed
-- defaults; rows already present are never overwritten, so admin changes
-- survive restarts.
CREATE TABLE IF NOT EXISTS prices (
    package_key TEXT PRIMARY KEY,
    price_irr INTEGER NOT NULL,
    price_stars INTEGER NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
"""

_connection: aiosqlite.Connection | None = None


async def init_db() -> None:
    """Initialize the database connection and schema."""
    global _connection
    db_path = Path(DATABASE_PATH)
    if db_path.parent and str(db_path.parent) not in (".", ""):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    _connection = await aiosqlite.connect(str(db_path))
    _connection.row_factory = aiosqlite.Row
    await _connection.execute("PRAGMA foreign_keys = ON")
    await _connection.executescript(_SCHEMA)
    # Lightweight migration for existing databases.
    async with _connection.execute("PRAGMA table_info(users)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "reminder_sent_for_expiry" not in columns:
        await _connection.execute(
            "ALTER TABLE users ADD COLUMN reminder_sent_for_expiry TIMESTAMP"
        )
    async with _connection.execute("PRAGMA table_info(payments)") as cursor:
        pay_cols = [row[1] for row in await cursor.fetchall()]
    if "tracking_number" not in pay_cols:
        await _connection.execute(
            "ALTER TABLE payments ADD COLUMN tracking_number TEXT"
        )
    # Partial unique index: a tracking number may be verified at most once.
    # Created after the migration above, because it references that column and
    # legacy databases do not have it yet. SQLite does not accept parameters in
    # a partial index WHERE clause, so the enum value is interpolated.
    try:
        await _connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_tracking_unique "
            "ON payments (tracking_number) "
            f"WHERE tracking_number IS NOT NULL AND status = '{PaymentStatus.VERIFIED.value}'"
        )
    except sqlite3.IntegrityError:
        logger.warning(
            "Could not create idx_payments_tracking_unique: duplicate verified "
            "tracking numbers already exist in the database"
        )
    await _seed_prices()
    await _connection.commit()
    logger.info("Database initialized at %s", db_path)


async def _seed_prices() -> None:
    """Insert default prices for packages that have no row yet.

    INSERT OR IGNORE, so prices an admin changed with /prices are preserved.
    """
    if _connection is None:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    seeded = 0
    for package_key in PACKAGE_KEYS:
        irr = PACKAGE_PRICES_IRR.get(package_key)
        stars = PACKAGE_PRICES_STARS.get(package_key)
        if irr is None or stars is None:
            logger.warning("No default price configured for %s; skipping seed", package_key)
            continue
        cursor = await _connection.execute(
            "INSERT OR IGNORE INTO prices (package_key, price_irr, price_stars, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (package_key, int(irr), int(stars), now),
        )
        seeded += cursor.rowcount or 0
    if seeded:
        logger.info("Seeded %d default price(s)", seeded)


async def close_db() -> None:
    """Close the database connection."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
        logger.info("Database connection closed")


def get_db() -> aiosqlite.Connection:
    """Return the active database connection."""
    if _connection is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _connection
