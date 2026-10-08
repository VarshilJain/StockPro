"""
rate_limiter.py — Multi-dimensional rate limiting with in-memory sliding window and Redis support.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request, status

from config import BEHIND_TRUSTED_PROXY, REDIS_URL

logger = logging.getLogger(__name__)


class RateLimitStore(ABC):
    @abstractmethod
    def record_attempt(self, key: str, window_seconds: float) -> int:
        """Record an attempt and return the total number of attempts in the current window."""
        pass

    @abstractmethod
    def reset(self, key: str) -> None:
        """Reset attempts for a key."""
        pass


class InMemorySlidingWindowStore(RateLimitStore):
    def __init__(self):
        self._store: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def record_attempt(self, key: str, window_seconds: float) -> int:
        now = time.monotonic()
        with self._lock:
            # Filter timestamps outside window
            timestamps = [t for t in self._store[key] if now - t < window_seconds]
            timestamps.append(now)
            self._store[key] = timestamps
            
            # Periodic cleanup of empty keys if memory grows
            if len(self._store) > 10000:
                for k in list(self._store.keys()):
                    self._store[k] = [t for t in self._store[k] if now - t < window_seconds]
                    if not self._store[k]:
                        del self._store[k]
                        
            return len(timestamps)

    def reset(self, key: str) -> None:
        with self._lock:
            if key in self._store:
                del self._store[key]


class RedisRateLimitStore(RateLimitStore):
    def __init__(self, redis_url: str):
        import redis
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def record_attempt(self, key: str, window_seconds: float) -> int:
        try:
            pipe = self._client.pipeline()
            now = time.time()
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zadd(key, {str(now): now})
            pipe.zcard(key)
            pipe.expire(key, int(window_seconds) + 1)
            results = pipe.execute()
            return int(results[2])
        except Exception as exc:
            logger.warning("Redis rate limiter error, failing open: %s", exc)
            return 1

    def reset(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception as exc:
            logger.warning("Redis rate limiter reset error: %s", exc)


# Choose backend store
if REDIS_URL:
    try:
        _store: RateLimitStore = RedisRateLimitStore(REDIS_URL)
        logger.info("Rate limiter configured with Redis backend at %s", REDIS_URL)
    except Exception as exc:
        logger.warning("Failed to initialize Redis store (%s), falling back to in-memory", exc)
        _store = InMemorySlidingWindowStore()
else:
    _store = InMemorySlidingWindowStore()


def get_client_ip(request: Request) -> str:
    """
    Safely extract client IP address.
    Only trusts proxy headers (X-Forwarded-For, X-Real-IP) if BEHIND_TRUSTED_PROXY is True.
    """
    if BEHIND_TRUSTED_PROXY:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # First IP in comma-separated list is the original client
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class LoginRateLimiter:
    """
    Multi-dimensional rate limiter for authentication endpoints.
    Protects against both IP-based brute force and targeted account/email brute force.
    """
    def __init__(
        self,
        max_ip_attempts: int = 5,
        ip_window_seconds: float = 60.0,
        max_account_attempts: int = 5,
        account_window_seconds: float = 300.0,
        store: RateLimitStore = _store,
    ):
        self.max_ip_attempts = max_ip_attempts
        self.ip_window_seconds = ip_window_seconds
        self.max_account_attempts = max_account_attempts
        self.account_window_seconds = account_window_seconds
        self.store = store

    def check_and_record(self, request: Request, email: Optional[str] = None) -> None:
        ip = get_client_ip(request)
        
        # 1. Check IP rate limit
        ip_key = f"ratelimit:ip:{ip}"
        ip_count = self.store.record_attempt(ip_key, self.ip_window_seconds)
        if ip_count > self.max_ip_attempts:
            logger.warning("SECURITY_EVENT: Rate limit exceeded for IP: %s (attempts: %d)", ip, ip_count)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts from your network. Please wait a minute and try again.",
                headers={"Retry-After": str(int(self.ip_window_seconds))},
            )

        # 2. Check Account / Email rate limit
        if email:
            normalized_email = email.strip().lower()
            account_key = f"ratelimit:account:{normalized_email}"
            acc_count = self.store.record_attempt(account_key, self.account_window_seconds)
            if acc_count > self.max_account_attempts:
                logger.warning(
                    "SECURITY_EVENT: Rate limit exceeded for account: %s from IP: %s (attempts: %d)",
                    normalized_email, ip, acc_count
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed login attempts for this account. Please wait 5 minutes and try again.",
                    headers={"Retry-After": str(int(self.account_window_seconds))},
                )

    def reset_account(self, email: str) -> None:
        """Reset failed account attempts on successful login."""
        normalized_email = email.strip().lower()
        self.store.reset(f"ratelimit:account:{normalized_email}")

    def reset_ip(self, ip: str) -> None:
        """Reset attempts for a specific IP address."""
        self.store.reset(f"ratelimit:ip:{ip}")


# Global default login rate limiter instance
login_rate_limiter = LoginRateLimiter()
