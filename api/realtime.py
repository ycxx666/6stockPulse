"""
6stockPulse · 实时数据服务
============================
  - 异步轮询东方财富接口, 写入 stock_prices
  - 维护内存中的最新行情 (供 WebSocket 推送)
  - 自动适配交易时段 (9:30-11:30 / 13:00-15:00)
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime

from crawlers.eastmoney import EastMoneyRealtime
from config import REALTIME
from db import database as db
from scheduler.task_registry import is_market_open as in_trading_hours

logger = logging.getLogger(__name__)


class RealtimeService:
    """实时数据采集 + 推送服务"""

    def __init__(self):
        self.running = False
        self._latest_quotes: dict[str, dict] = {}   # code → quote
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._last_refresh: datetime | None = None
        self._last_view_refresh: float = 0

    # ────── 订阅管理 (WebSocket 用) ──────
    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        # 立即推送当前快照
        await q.put({"type": "snapshot", "payload": list(self._latest_quotes.values())})
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    async def _broadcast(self, msg: dict):
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    # ────── 启动/停止 ──────
    async def start(self):
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("RealtimeService started (interval=%.1fs)", REALTIME.poll_interval)

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("RealtimeService stopped")

    # ────── 主循环 ──────
    async def _loop(self):
        while self.running:
            try:
                if not in_trading_hours():
                    logger.debug("非交易时段, 跳过拉取")
                    await asyncio.sleep(60)
                    continue
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("RealtimeService loop error: %s", e)
            await asyncio.sleep(REALTIME.poll_interval)

    async def _tick(self):
        async with EastMoneyRealtime() as crawler:
            codes = await self._top_n(REALTIME.top_n_stocks)
            batch_size = REALTIME.quotes_per_batch
            updates = []
            for i in range(0, len(codes), batch_size):
                batch = codes[i:i+batch_size]
                tasks = [crawler._fetch_one(c, skip_delay=True) for c in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for code, r in zip(batch, results):
                    if isinstance(r, dict):
                        r["stock_code"] = code
                        self._latest_quotes[code] = r
                        updates.append(r)
                # 批次间冷却: 避免瞬间打满连接触发限流
                if i + batch_size < len(codes):
                    await asyncio.sleep(0.5)
            # 写库
            if updates:
                inserted = await db.insert_many(
                    "stock_prices", updates,
                    conflict_target="trade_date, stock_code, trade_time"
                )
                logger.info("实时行情: 拉取 %d 条, 写入 %d 条", len(updates), inserted)
                # 推送: 主题化消息
                await self._broadcast({
                    "type": "market.ticker.updated",
                    "ts":   datetime.now().isoformat(timespec="seconds"),
                    "payload": updates,
                })
                # 每分钟: 刷新物化视图 + 推送市场宽度
                now = time.monotonic()
                if now - self._last_view_refresh >= 60:
                    self._last_view_refresh = now
                    await db.refresh_latest_quotes()
                    # 计算并推送市场宽度
                    try:
                        from services.market_service import compute_and_store_breadth
                        breadth = await compute_and_store_breadth()
                        await self._broadcast({
                            "type": "market.breadth.updated",
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "payload": breadth,
                        })
                    except Exception:
                        logger.warning("market_breadth broadcast failed", exc_info=True)
                # 使聚合缓存失效
                try:
                    from services.dashboard_service import invalidate_snapshot
                    invalidate_snapshot()
                except Exception:
                    logger.warning("snapshot invalidation failed", exc_info=True)
            self._last_refresh = datetime.now()

    async def _top_n(self, n: int) -> list[str]:
        rows = await db.fetch_all("""
            SELECT stock_code FROM stocks WHERE is_active = TRUE
            ORDER BY stock_code LIMIT $1
        """, n)
        return [r["stock_code"] for r in rows]

    # ────── 手动触发 ──────
    async def force_refresh(self, codes: list[str] | None = None) -> int:
        async with EastMoneyRealtime() as crawler:
            if not codes:
                codes = await self._top_n(REALTIME.top_n_stocks)
            updated = 0
            for code in codes:
                r = await crawler._fetch_one(code)
                if r:
                    r["stock_code"] = code
                    self._latest_quotes[code] = r
                    updated += 1
        if self._latest_quotes:
            await db.insert_many(
                "stock_prices", list(self._latest_quotes.values()),
                conflict_target="trade_date, stock_code, trade_time"
            )
        await self._broadcast({"type": "market.ticker.updated", "ts": datetime.now().isoformat(),
                               "payload": list(self._latest_quotes.values())})
        return updated

    def snapshot(self) -> list[dict]:
        return list(self._latest_quotes.values())


# 全局单例
realtime = RealtimeService()
