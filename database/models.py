"""Data models for Grabora."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


@dataclass(slots=True)
class User:
    user_id: int
    username: str | None = None
    full_name: str | None = None
    tier: str = "free"
    tier_expires_at: datetime | None = None
    has_2k_addon: bool = False
    addon_expires_at: datetime | None = None
    daily_downloads: int = 0
    daily_reset_at: datetime | None = None
    is_banned: bool = False
    reminder_sent_for_expiry: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_row(cls, row: Any) -> "User":
        def _dt(value: Any) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value))

        return cls(
            user_id=int(row["user_id"]),
            username=row["username"],
            full_name=row["full_name"],
            tier=str(row["tier"]),
            tier_expires_at=_dt(row["tier_expires_at"]),
            has_2k_addon=bool(row["has_2k_addon"]),
            addon_expires_at=_dt(row["addon_expires_at"]),
            daily_downloads=int(row["daily_downloads"]),
            daily_reset_at=_dt(row["daily_reset_at"]),
            is_banned=bool(row["is_banned"]),
            reminder_sent_for_expiry=_dt(row["reminder_sent_for_expiry"]),
            created_at=_dt(row["created_at"]) or datetime.utcnow(),
            updated_at=_dt(row["updated_at"]) or datetime.utcnow(),
        )


class PaymentStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    PENDING_MANUAL = "pending_manual"
    REJECTED = "rejected"


@dataclass(slots=True)
class Payment:
    id: int | None = None
    user_id: int = 0
    provider: str = ""
    tier: str = ""
    amount: int = 0
    currency: str = ""
    status: str = "pending"
    authority: str | None = None
    transaction_id: str | None = None
    is_addon: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    verified_at: datetime | None = None
    metadata: str | None = None
    tracking_number: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Payment":
        def _dt(value: Any) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value))

        return cls(
            id=int(row["id"]) if row["id"] is not None else None,
            user_id=int(row["user_id"]),
            provider=str(row["provider"]),
            tier=str(row["tier"]),
            amount=int(row["amount"]),
            currency=str(row["currency"]),
            status=str(row["status"]),
            authority=row["authority"],
            transaction_id=row["transaction_id"],
            is_addon=bool(row["is_addon"]),
            created_at=_dt(row["created_at"]) or datetime.utcnow(),
            verified_at=_dt(row["verified_at"]),
            metadata=row["metadata"],
            tracking_number=row["tracking_number"],
        )


@dataclass(slots=True)
class DownloadLog:
    id: int | None = None
    user_id: int = 0
    url_domain: str | None = None
    media_type: str | None = None
    quality: str | None = None
    status: str = "success"
    file_size_mb: float | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_row(cls, row: Any) -> "DownloadLog":
        def _dt(value: Any) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value))

        return cls(
            id=int(row["id"]) if row["id"] is not None else None,
            user_id=int(row["user_id"]),
            url_domain=row["url_domain"],
            media_type=row["media_type"],
            quality=row["quality"],
            status=str(row["status"]),
            file_size_mb=float(row["file_size_mb"]) if row["file_size_mb"] is not None else None,
            error_message=row["error_message"],
            created_at=_dt(row["created_at"]) or datetime.utcnow(),
        )
