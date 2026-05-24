"""Redis connection management."""

import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create the Redis connection pool."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            encoding="utf-8",
        )
        logger.info("Redis connection established to %s:%s", settings.redis_host, settings.redis_port)
    return _redis


async def close_redis() -> None:
    """Close the Redis connection pool."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis connection closed")


class RedisCache:
    """Redis-based cache for WikiFs page content."""

    def __init__(self, redis_client: aioredis.Redis, ttl: int = 3600) -> None:
        self._redis = redis_client
        self._ttl = ttl

    def _page_key(self, namespace: str, version: str, path: str) -> str:
        """Build Redis key for a cached page."""
        return f"page:{namespace}:{version}:{path}"

    async def get(self, namespace: str, version: str, path: str) -> str | None:
        """Get cached page content."""
        key = self._page_key(namespace, version, path)
        return await self._redis.get(key)

    async def set(self, namespace: str, version: str, path: str, content: str) -> None:
        """Set cached page content with TTL."""
        key = self._page_key(namespace, version, path)
        await self._redis.set(key, content, ex=self._ttl)

    async def delete(self, namespace: str, version: str, path: str) -> None:
        """Delete cached page content."""
        key = self._page_key(namespace, version, path)
        await self._redis.delete(key)

    async def get_path_tree(self, namespace: str, version: str) -> dict[str, Any] | None:
        """Get path tree from Redis (gzip compressed JSON)."""
        import gzip
        import json

        key = f"path_tree:{namespace}:{version}"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        # Redis returns string with decode_responses=True; need to handle gzip
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            decompressed = gzip.decompress(raw)
            return json.loads(decompressed)
        except Exception:
            # If not gzip, try plain JSON
            return json.loads(raw)