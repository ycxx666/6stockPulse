"""
infrastructure/cache/redis.py — Redis 客户端封装
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis Stream 封装（需要 redis[hiredis] 依赖）"""

    def __init__(self, url: str = "redis://localhost:6379/0"):
        self.url = url
        self._client: Any = None

    async def connect(self):
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(self.url)
            await self._client.ping()
            logger.info("Redis connected: %s", self.url)
        except ImportError:
            logger.warning("redis[hiredis] not installed, Redis disabled")
            self._client = None
        except Exception as e:
            logger.warning("Redis connection failed: %s", e)
            self._client = None

    async def close(self):
        if self._client:
            await self._client.close()

    @property
    def available(self) -> bool:
        return self._client is not None

    # ── Stream 操作 ──
    async def xadd(self, stream: str, data: dict, maxlen: int = 10000) -> str | None:
        """追加消息到 Stream"""
        if not self._client:
            return None
        return await self._client.xadd(stream, data, maxlen=maxlen, approximate=True)

    async def xread(self, stream: str, count: int = 100, block: int = 5000) -> list:
        """读取 Stream 新消息（阻塞）"""
        if not self._client:
            return []
        return await self._client.xread({stream: "0"}, count=count, block=block)

    async def xreadgroup(self, group: str, consumer: str, stream: str,
                         count: int = 100) -> list:
        """消费者组读取"""
        if not self._client:
            return []
        try:
            await self._client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # group already exists
        return await self._client.xreadgroup(group, consumer, {stream: ">"}, count=count)


# 全局单例
redis = RedisClient()
