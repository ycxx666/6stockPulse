"""
6stockPulse · 内存缓存层
==========================
轻量级 TTL 缓存，无外部依赖，替代 Redis 用于:
  - 热点接口 1-30s 缓存
  - 最新行情 / 聚合快照
  - WebSocket seq 计数器
  - 分布式任务锁 (单进程版)
"""
from __future__ import annotations
import time
import asyncio
import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class MemCache:
    """简单的 TTL 内存缓存"""

    def __init__(self):
        self._store: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ── 基础读写 ──
    def get(self, key: str) -> Any | None:
        """获取缓存值 (过期返回 None)"""
        entry = self._store.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires and time.monotonic() > expires:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int = 0):
        """写入缓存, ttl=0 表示永不过期"""
        expires = time.monotonic() + ttl if ttl > 0 else 0
        self._store[key] = (expires, value)

    def delete(self, key: str):
        self._store.pop(key, None)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    # ── 批量操作 ──
    def get_many(self, *keys: str) -> dict[str, Any]:
        return {k: self.get(k) for k in keys if self.get(k) is not None}

    def set_many(self, mapping: dict[str, Any], ttl: int = 0):
        for k, v in mapping.items():
            self.set(k, v, ttl)

    def mget_or_default(self, keys: list[str], default: Any = None) -> dict[str, Any]:
        """批量获取，缺失 key 填充 default"""
        return {k: self.get(k) if self.exists(k) else default for k in keys}

    # ── 原子操作 ──
    def incr(self, key: str, delta: int = 1) -> int:
        v = self.get(key)
        new_v = (v or 0) + delta
        self.set(key, new_v)
        return new_v

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl: int = 0) -> Any:
        """同步工厂: 如果不存在则计算并缓存"""
        v = self.get(key)
        if v is not None:
            return v
        v = factory()
        self.set(key, v, ttl)
        return v

    # ── 异步工厂 (带锁，防缓存击穿) ──
    async def get_or_set_async(
        self, key: str,
        factory: Callable[[], Awaitable[Any]],
        ttl: int = 0
    ) -> Any:
        """异步获取或计算，同一 key 同一时间只计算一次"""
        v = self.get(key)
        if v is not None:
            return v

        # 防缓存击穿: 同一 key 只让一个协程计算
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                # Double-check: 锁内再查一次
                v = self.get(key)
                if v is not None:
                    return v
                v = await factory()
                self.set(key, v, ttl)
            finally:
                self._locks.pop(key, None)
        return v

    # ── 统计 ──
    def stats(self) -> dict:
        return {
            "keys": len(self._store),
            "locks": len(self._locks),
            "keys_sample": list(self._store.keys())[:10],
        }

    def clear_expired(self) -> int:
        """清理过期条目 (返回清理数)"""
        before = len(self._store)
        now = time.monotonic()
        expired = [
            k for k, (exp, _) in self._store.items()
            if exp and now > exp
        ]
        for k in expired:
            del self._store[k]
        return before - len(self._store)


# ────────── 预定义的缓存键 ──────────
class CacheKeys:
    """缓存键常量，统一管理避免散落字符串"""
    # 行情
    QUOTE_LATEST   = "quote:latest:{}"       # format(symbol)
    QUOTES_BATCH   = "quote:batch:top100"
    # 聚合
    DASH_SNAPSHOT  = "dash:snapshot"
    BREADTH_LATEST = "breadth:latest"
    SECTOR_FLOW    = "sector:flow:{}"         # format(type)
    ANOMALY_ACTIVE = "anomaly:active"
    # 情绪
    SENTIMENT_GLOBAL = "sentiment:global:latest"
    SENTIMENT_ASHARE = "sentiment:ashare:latest"
    # 信号
    SIGNALS_ACTIVE = "signals:active"
    # WS
    WS_SEQUENCE    = "ws:sequence"
    # 锁
    LOCK_JOB       = "lock:job:{}"            # format(job_name)
    LOCK_CRAWL     = "lock:crawl:{}"          # format(crawler_name)


# ────────── 全局单例 ──────────
cache = MemCache()
