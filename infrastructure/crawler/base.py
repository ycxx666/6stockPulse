"""
6stockPulse · 爬虫基类 (异步)
================================
提供:
  - 异步 HTTP 客户端 (aiohttp)
  - 随机 User-Agent 轮换
  - 指数退避重试
  - 代理自适应
  - 爬虫运行日志
"""
from __future__ import annotations
import asyncio
import json
import random
import time
import logging
from typing import Any, Optional
from datetime import datetime

import aiohttp

from core.config import CRAWLER
from infrastructure.database import postgres as db

logger = logging.getLogger(__name__)


class BaseCrawler:
    """所有爬虫的基类"""

    name: str = "base"

    def __init__(self, proxy: Optional[str] = None, **kwargs):
        self.proxy = proxy or CRAWLER.proxies.get("http")
        # 接受子类扩展参数 (如 refresh_interval_seconds)
        for k, v in kwargs.items():
            setattr(self, k, v)
        self._session: aiohttp.ClientSession | None = None
        self._log_id: int | None = None

    # ────── 会话管理 ──────
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "User-Agent": random.choice(CRAWLER.user_agents),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
            timeout = aiohttp.ClientTimeout(total=CRAWLER.timeout)
            connector = aiohttp.TCPConnector(limit=20, verify_ssl=CRAWLER.verify_ssl)
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=timeout, connector=connector
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self):
        await self._get_session()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ────── HTTP 请求 (带重试) ──────
    async def fetch(self, url: str, params: dict | None = None,
                    headers: dict | None = None, as_json: bool = True,
                    skip_delay: bool = False) -> Any:
        """异步 GET, 自动重试。skip_delay=True 跳过请求前冷却 (用于批量并行场景)"""
        session = await self._get_session()
        last_err = None
        for attempt in range(CRAWLER.max_retries):
            try:
                # 首次请求的随机延迟 (重试时有自己的 backoff)
                if attempt == 0 and not skip_delay:
                    wait = CRAWLER.request_interval + random.uniform(0, CRAWLER.request_jitter)
                    await asyncio.sleep(wait)

                async with session.get(url, params=params, headers=headers,
                                       proxy=self.proxy) as resp:
                    if resp.status == 429:
                        backoff = 30 + random.uniform(5, 15)
                        logger.warning("[%s] 429, sleep %.1fs", self.name, backoff)
                        await asyncio.sleep(backoff)
                        continue
                    resp.raise_for_status()
                    text = await resp.text()
                    if as_json:
                        return await resp.json(content_type=None)
                    return text
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                backoff = (CRAWLER.backoff_factor ** attempt) + random.uniform(0, 1)
                logger.warning("[%s] 第%d次请求失败: %s, 等待%.1fs",
                               self.name, attempt + 1, e, backoff)
                await asyncio.sleep(backoff)
        logger.error("[%s] %d 次重试后仍失败: %s", self.name, CRAWLER.max_retries, last_err)
        return None

    # ────── 工具 ──────
    @staticmethod
    def secid(code: str) -> str:
        """股票代码 → 东方财富 secid"""
        if code.startswith(("60", "68", "9")):
            return f"1.{code}"
        return f"0.{code}"

    @staticmethod
    def to_float(v, default: float = 0.0) -> float:
        try:
            return float(v) if v not in (None, "", "-") else default
        except (TypeError, ValueError):
            return default

    # ────── 日志 ──────
    async def start_log(self, meta: dict | None = None) -> int:
        self._start_ts = datetime.now()
        meta_str = json.dumps(meta or {}, ensure_ascii=False)
        async with db.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO crawl_log (crawler_name, started_at, meta)
                VALUES ($1, $2, $3::jsonb) RETURNING id
            """, self.name, self._start_ts, meta_str)
            self._log_id = row["id"]
        return self._log_id

    async def finish_log(self, success: int = 0, skip: int = 0, fail: int = 0,
                         error: str = ""):
        if self._log_id is None:
            return
        async with db.acquire() as conn:
            await conn.execute("""
                UPDATE crawl_log
                SET finished_at = NOW(), success_count = $2, skip_count = $3,
                    fail_count = $4, error_msg = $5
                WHERE id = $1
            """, self._log_id, success, skip, fail, error)

    # ────── 入口 (子类重写) ──────
    async def run(self) -> dict:
        raise NotImplementedError


async def run_many(*crawlers):
    """并发运行多个爬虫"""
    results = await asyncio.gather(
        *[c.run() for c in crawlers],
        return_exceptions=True
    )
    for c, r in zip(crawlers, results):
        if isinstance(r, Exception):
            logger.error("[%s] failed: %s", c.name, r)
        else:
            logger.info("[%s] done: %s", c.name, r)
    return results
