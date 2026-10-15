"""Helper utilities."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def domain_of(url: str) -> str | None:
    """Extract the network location (domain) from a URL."""
    try:
        return urlparse(url).netloc.lower() or None
    except Exception:  # noqa: BLE001
        return None


def ensure_dir(path: str | Path) -> Path:
    """Ensure a directory exists and return it as Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_admin(user_id: int, admin_ids: list[int]) -> bool:
    return user_id in admin_ids
