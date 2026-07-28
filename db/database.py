"""
6stockPulse · 数据库连接池
==========================
基于 asyncpg 的异步连接池，支持:
  - 自动重连
  - 连接健康检查
  - 事务封装
  - 批量插入 (executemany / copy_records_to_table)
"""
from __future__ import annotations
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable

import asyncpg

from config import DB as DB_CONFIG

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """初始化全局连接池 (应用启动时调用)"""
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(
        host=DB_CONFIG.host,
        port=DB_CONFIG.port,
        user=DB_CONFIG.user,
        password=DB_CONFIG.password,
        database=DB_CONFIG.database,
        min_size=DB_CONFIG.pool_min,
        max_size=DB_CONFIG.pool_max,
        command_timeout=30,
    )
    logger.info("PostgreSQL pool ready: %s/%s (size=%d-%d)",
                DB_CONFIG.user, DB_CONFIG.database,
                DB_CONFIG.pool_min, DB_CONFIG.pool_max)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool not initialized. Call init_pool() first.")
    return _pool


# ────────── 上下文管理 ──────────
@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """获取连接 (自动归还)"""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """事务上下文"""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn


# ────────── 健康检查 ──────────
async def health_check() -> dict[str, Any]:
    try:
        async with acquire() as conn:
            v = await conn.fetchval("SELECT version()")
            n = await conn.fetchval("SELECT COUNT(*) FROM stocks")
        return {"ok": True, "version": v[:60], "stocks": n}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ────────── 通用 CRUD ──────────
async def fetch_all(sql: str, *args) -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def fetch_one(sql: str, *args) -> dict | None:
    async with acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def fetch_val(sql: str, *args):
    async with acquire() as conn:
        return await conn.fetchval(sql, *args)


async def execute(sql: str, *args) -> str:
    async with acquire() as conn:
        return await conn.execute(sql, *args)


# ────────── 批量插入 ──────────
async def insert_many(table: str, records: list[dict],
                      conflict_target: str | None = None,
                      chunk_size: int = 1000) -> int:
    """
    批量插入 (带去重)
    records: list[dict]，键名必须与表字段名一致
    conflict_target: "ON CONFLICT (...)" 的目标列
    """
    if not records:
        return 0
    keys = list(records[0].keys())
    columns = ", ".join(keys)
    placeholders = ", ".join(f"${i+1}" for i in range(len(keys)))
    conflict_clause = ""
    if conflict_target:
        conflict_clause = f"ON CONFLICT ({conflict_target}) DO NOTHING"

    sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) {conflict_clause}"

    pool = get_pool()
    inserted = 0
    async with pool.acquire() as conn:
        for i in range(0, len(records), chunk_size):
            chunk = records[i:i+chunk_size]
            values = [[r.get(k) for k in keys] for r in chunk]
            try:
                result = await conn.executemany(sql, values)
                inserted += result if result is not None else len(values)
            except Exception as e:
                logger.warning("insert_many failed on chunk %d: %s", i // chunk_size, e)
                # 退化: 单条插入
                async with conn.transaction():
                    for v in values:
                        try:
                            await conn.execute(sql, *v)
                            inserted += 1
                        except Exception:
                            logger.debug("insert_many single-row fallback failed", exc_info=True)
    return inserted


async def upsert_many(table: str, records: list[dict],
                      conflict_target: str | list[str],
                      update_cols: list[str] | None = None) -> int:
    """
    UPSERT (INSERT ... ON CONFLICT DO UPDATE)
    """
    if not records:
        return 0
    keys = list(records[0].keys())
    columns = ", ".join(keys)
    placeholders = ", ".join(f"${i+1}" for i in range(len(keys)))

    if isinstance(conflict_target, str):
        conflict_target = [conflict_target]
    target = ", ".join(conflict_target)

    if update_cols is None:
        update_cols = [k for k in keys if k not in conflict_target]
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)

    sql = (f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
           f"ON CONFLICT ({target}) DO UPDATE SET {update_set}")

    pool = get_pool()
    n = 0
    async with pool.acquire() as conn:
        for i in range(0, len(records), 1000):
            chunk = records[i:i+1000]
            values = [[r.get(k) for k in keys] for r in chunk]
            try:
                result = await conn.executemany(sql, values)
                n += result if result is not None else len(values)
            except Exception as e:
                logger.warning("upsert_many failed on chunk %d: %s", i // 1000, e)
    return n


# ────────── 物化视图刷新 ──────────
async def refresh_latest_quotes():
    """刷新最新行情物化视图"""
    async with acquire() as conn:
        try:
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY latest_quotes")
        except Exception:
            logger.debug("CONCURRENTLY refresh failed for latest_quotes, falling back", exc_info=True)
            await conn.execute("REFRESH MATERIALIZED VIEW latest_quotes")


async def refresh_latest_sector():
    async with acquire() as conn:
        try:
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY latest_sector")
        except Exception:
            logger.debug("CONCURRENTLY refresh failed for latest_sector, falling back", exc_info=True)
            await conn.execute("REFRESH MATERIALIZED VIEW latest_sector")


async def refresh_latest_global_indices():
    """刷新全球指数物化视图"""
    async with acquire() as conn:
        try:
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY latest_global_indices")
        except Exception:
            logger.debug("CONCURRENTLY refresh failed for latest_global_indices, falling back", exc_info=True)
            await conn.execute("REFRESH MATERIALIZED VIEW latest_global_indices")


# ────────── 简化使用示例 ──────────
if __name__ == "__main__":
    async def demo():
        logging.basicConfig(level=logging.INFO)
        await init_pool()
        print(await health_check())
        rows = await fetch_all("SELECT stock_code, stock_name, market FROM stocks LIMIT 5")
        for r in rows:
            print(r)
        await close_pool()

    asyncio.run(demo())
