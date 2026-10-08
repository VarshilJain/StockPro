"""
cache.py — In-memory caching layer with data-version fingerprinting and TTL.

Provides fast O(1) in-memory response caching for screen stages, summaries, and scanner results.
Entries are validated against a data_version string (e.g. latest DB Timestamp).
When test.py or ingest.py updates the database, the data_version changes and
all stale entries are automatically invalidated.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_store: Dict[str, Dict[str, Any]] = {}


def get(key: str, data_version: Optional[str] = None) -> Optional[Any]:
    """
    Retrieve cached value for key.
    If data_version is provided, entry is valid only if entry['data_version'] matches.
    If TTL is expired, returns None.
    """
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None

        # Check TTL
        if entry.get("expires_at") is not None and now > entry["expires_at"]:
            del _store[key]
            return None

        # Check data version if given
        if data_version is not None and entry.get("data_version") != data_version:
            del _store[key]
            return None

        return entry["value"]


def set(key: str, value: Any, data_version: Optional[str] = None, ttl: Optional[int] = 300) -> None:
    """
    Store value in cache with optional data_version fingerprint and TTL (seconds).
    Default TTL is 5 minutes (300s).
    """
    now = time.monotonic()
    expires_at = (now + ttl) if ttl else None
    with _lock:
        _store[key] = {
            "value": value,
            "data_version": data_version,
            "expires_at": expires_at,
            "created_at": now,
        }


def invalidate(key: str) -> None:
    """Remove a specific key from cache."""
    with _lock:
        _store.pop(key, None)


def invalidate_all() -> None:
    """Clear entire cache."""
    with _lock:
        _store.clear()
