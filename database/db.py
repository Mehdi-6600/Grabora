"""SQLite database connection management."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Final

import aiosqlite

from config import DATABASE_PATH
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
    await _connection.commit()
    logger.info("Database initialized at %s", db_path)


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
