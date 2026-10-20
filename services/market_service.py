"""
6stockPulse · 市场服务
========================
市场宽度 / 量能对比 / 分钟聚合 / 赚钱效应 / 连板梯队
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from typing import Any

from db import database as db
from services.cache import cache, CacheKeys

logger = logging.getLogger(__name__)


# ────────── 市场宽度 ──────────
async def compute_and_store_breadth() -> dict:
    """计算全市场宽度并写入 market_breadth 表"""
    row = await db.fetch_one("""
        SELECT
            COUNT(*) FILTER (WHERE change_pct > 0)   AS up_count,
            COUNT(*) FILTER (WHERE change_pct < 0)   AS down_count,
            COUNT(*) FILTER (WHERE change_pct = 0)   AS flat_count,
            COUNT(*) FILTER (WHERE change_pct >= 9.95)  AS limit_up,
            COUNT(*) FILTER (WHERE change_pct <= -9.95) AS limit_down,
            COUNT(*) FILTER (WHERE change_pct > 5)    AS big_up,
            COUNT(*) FILTER (WHERE change_pct < -5)   AS big_down,
            COUNT(*)                                   AS total
        FROM latest_quotes
    """)
    if not row or (row["total"] or 0) == 0:
        return {"up_count": 0, "down_count": 0, "flat_count": 0,
                "limit_up": 0, "limit_down": 0, "big_up": 0, "big_down": 0,
                "total": 0, "sentiment_score": 50}

    r = dict(row)
    up = r.get("up_count", 0) or 0
    total = max(r.get("total", 1) or 1, 1)
    up_pct = up / total * 100
    r["sentiment_score"] = max(5, min(95, int(up_pct * 0.65 + 35)))

    now = datetime.now()
    await db.execute("""
        INSERT INTO market_breadth
            (snapshot_time, up_count, down_count, flat_count, limit_up, limit_down,
             big_up, big_down, total, sentiment_score, source)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'computed')
        ON CONFLICT (snapshot_time) DO NOTHING
    """, now, r["up_count"], r["down_count"], r["flat_count"],
       r["limit_up"], r["limit_down"], r["big_up"], r["big_down"],
       r["total"], r["sentiment_score"])

    cache.set(CacheKeys.BREADTH_LATEST, r, ttl=30)
    return r


async def get_latest_breadth() -> dict:
    """获取最新市场宽度 (缓存优先)"""
    cached = cache.get(CacheKeys.BREADTH_LATEST)
    if cached:
        return cached
    row = await db.fetch_one("""
        SELECT up_count, down_count, flat_count, limit_up, limit_down,
               big_up, big_down, total, sentiment_score,
               snapshot_time::text AS snapshot_time
        FROM market_breadth
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    if row:
        r = dict(row)
        cache.set(CacheKeys.BREADTH_LATEST, r, ttl=30)
        return r
    return await compute_and_store_breadth()


# ────────── 量能对比 ──────────
async def get_volume_comparison() -> dict:
    """今日 vs 昨日同期成交量/成交额"""
    today = date.today()
    row = await db.fetch_one("""
        SELECT
            (SELECT COALESCE(SUM(amount), 0) FROM stock_prices
             WHERE trade_date = $1) AS today_amount,
            (SELECT COALESCE(SUM(amount), 0) FROM stock_prices
             WHERE trade_date = $1 - INTERVAL '1 day') AS yesterday_amount,
            (SELECT COALESCE(SUM(volume), 0) FROM stock_prices
             WHERE trade_date = $1) AS today_volume,
            (SELECT COALESCE(SUM(volume), 0) FROM stock_prices
             WHERE trade_date = $1 - INTERVAL '1 day') AS yesterday_volume
    """, today)
    if not row:
        return {"today_amount_yi": 0, "yesterday_amount_yi": 0,
                "ratio": 1.0, "trend": "持平"}
    r = dict(row)
    today_amt = float(r.get("today_amount") or 0) / 1e8
    yesterday_amt = float(r.get("yesterday_amount") or 0) / 1e8
    ratio = today_amt / max(yesterday_amt, 1)
    trend = "放量" if ratio > 1.2 else "缩量" if ratio < 0.8 else "持平"
    return {
        "today_amount_yi": round(today_amt, 2),
        "yesterday_amount_yi": round(yesterday_amt, 2),
        "ratio": round(ratio, 2),
        "trend": trend,
    }


# ────────── 赚钱效应 ──────────
async def get_profit_effect() -> dict:
    """计算赚钱效应指标"""
    # 简化版: 基于涨停股次日表现
    row = await db.fetch_one("""
        WITH yesterday_limit_up AS (
            SELECT stock_code FROM stock_prices
            WHERE trade_date = CURRENT_DATE - INTERVAL '1 day'
              AND change_pct >= 9.95
        ),
        today_perf AS (
            SELECT p.stock_code, p.change_pct
            FROM stock_prices p
            JOIN yesterday_limit_up y ON y.stock_code = p.stock_code
            WHERE p.trade_date = CURRENT_DATE
        )
        SELECT
            COUNT(*) AS sample_count,
            ROUND(AVG(change_pct)::numeric, 2) AS avg_open_return
        FROM today_perf
    """)
    if not row or not row.get("sample_count"):
        return {"sample_count": 0, "avg_open_return": 0,
                "avg_high_return": 0, "avg_close_return": 0,
                "no_data": True, "note": "暂无样本（上一交易日无涨停股或数据未更新）"}
    r = dict(row)
    r["avg_high_return"] = r.get("avg_open_return", 0)
    r["avg_close_return"] = r.get("avg_open_return", 0)
    return r


# ────────── 连板梯队 ──────────
async def get_limit_up_ladder() -> list[dict]:
    """识别连续涨停的连板梯队"""
    rows = await db.fetch_all("""
        WITH consecutive_days AS (
            SELECT stock_code,
                   COUNT(DISTINCT trade_date) AS days
            FROM stock_prices
            WHERE trade_date >= CURRENT_DATE - INTERVAL '15 days'
              AND change_pct >= 9.9
            GROUP BY stock_code
        )
        SELECT
            CASE
                WHEN days = 2 THEN '2板'
                WHEN days = 3 THEN '3板'
                WHEN days = 4 THEN '4板'
                WHEN days >= 5 THEN '5板+'
            END AS label,
            days,
            COUNT(*) AS count
        FROM consecutive_days
        WHERE days >= 2
        GROUP BY label, days
        ORDER BY days
    """)
    if not rows:
        return []
    max_count = max(r["count"] for r in rows) or 1
    return [{"label": r["label"], "count": r["count"],
             "bar_pct": round(r["count"] / max_count * 100, 1)}
            for r in rows]


# ────────── 概念持续性 ──────────
async def get_concept_leaders(limit: int = 6) -> list[dict]:
    """概念领涨板块 (含持续性)"""
    rows = await db.fetch_all("""
        SELECT s.sector_code, s.sector_name,
               s.change_pct, s.main_net_inflow
        FROM v_sector_top s
        WHERE s.change_pct IS NOT NULL
        ORDER BY s.change_pct DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


# ────────── 分钟聚合: 指数分时图 ──────────
async def get_index_intraday(symbol: str = "000001", date_str: str | None = None) -> list[dict]:
    """
    获取指数分钟级 OHLCV (供前端分时图)
    symbol: 000001=上证, 399001=深证, 399006=创业板
    """
    from datetime import date
    trade_date = date.fromisoformat(date_str) if date_str else date.today()
    rows = await db.fetch_all("""
        SELECT trade_time::text AS t, open, high, low, close, volume, amount
        FROM stock_prices
        WHERE stock_code = $1 AND trade_date = $2
        ORDER BY trade_time ASC
    """, symbol, trade_date)
    if not rows:
        return []
    return [dict(r) for r in rows]


# ────────── 分钟量能对比 ──────────
async def get_minute_volume_comparison() -> dict:
    """
    今日 vs 昨日 分钟级成交量对比
    返回 {today: [{t, close, volume}], yesterday: [{t, close, volume}]}
    """
    from datetime import date, timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)

    today_rows = await db.fetch_all("""
        SELECT trade_time::text AS t, close, volume
        FROM stock_prices
        WHERE stock_code = '000001' AND trade_date = $1
        ORDER BY trade_time ASC
    """, today)

    yesterday_rows = await db.fetch_all("""
        SELECT trade_time::text AS t, close, volume
        FROM stock_prices
        WHERE stock_code = '000001' AND trade_date = $1
        ORDER BY trade_time ASC
    """, yesterday)

    return {
        "today": [dict(r) for r in today_rows],
        "yesterday": [dict(r) for r in yesterday_rows],
        "today_total_amount": await _get_daily_amount(today),
        "yesterday_total_amount": await _get_daily_amount(yesterday),
    }


async def _get_daily_amount(d: date) -> float:
    row = await db.fetch_one("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM stock_prices WHERE trade_date = $1
    """, d)
    return float(row["total"]) if row else 0.0


# ────────── 概念持续性评分 ──────────
async def get_concept_sustainability(limit: int = 10) -> list[dict]:
    """
    概念领导力 = 25% 当日涨幅 + 20% 主力净流入 + 15% 上涨成分股占比
               + 15% 龙头贡献度 + 10% 连续强势天数 + 10% 新闻热度 + 5% 涨停数量
    """
    rows = await db.fetch_all("""
        SELECT s.sector_code, s.sector_name, s.sector_type,
               s.change_pct, s.main_net_inflow, s.main_net_ratio, s.turnover_rate
        FROM v_sector_top s
        ORDER BY s.change_pct DESC
        LIMIT $1
    """, limit * 2)  # 多取一些做筛选

    result = []
    for r in rows:
        d = dict(r)
        inflow = abs(d.get("main_net_inflow") or 0) / 1e8
        chg = d.get("change_pct") or 0
        # 简化评分
        score = (
            0.25 * min(100, max(0, chg * 10 + 50)) +
            0.20 * min(100, inflow * 2) +
            0.25 * 50  # 默认中性
        )
        d["sustainability_score"] = round(score, 1)
        d["consecutive_days"] = 1  # 简化
        d["risk_tags"] = []
        if inflow < 0 and chg > 0:
            d["risk_tags"].append("资金流出")
        result.append(d)

    result.sort(key=lambda x: x["sustainability_score"], reverse=True)
    return result[:limit]


# ────────── 赚钱效应 (增强版) ──────────
async def get_enhanced_profit_effect() -> dict:
    """
    增强版赚钱效应:
    - 昨日涨停今日表现
    - 封板率 / 炸板率
    - 追涨/抄底收益
    """
    from datetime import date, timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)

    # 昨日涨停股今日表现
    row = await db.fetch_one("""
        WITH y_limit AS (
            SELECT stock_code, close FROM stock_prices
            WHERE trade_date = $1 AND change_pct >= 9.95
        ),
        today_perf AS (
            SELECT p.stock_code,
                   y.close AS prev_close,
                   FIRST_VALUE(p.open)  OVER w AS open_p,
                   MAX(p.high)          OVER w AS high_p,
                   LAST_VALUE(p.close)  OVER w AS close_p
            FROM stock_prices p
            JOIN y_limit y ON y.stock_code = p.stock_code
            WHERE p.trade_date = $2
            WINDOW w AS (PARTITION BY p.stock_code ORDER BY p.trade_time)
        )
        SELECT
            COUNT(DISTINCT stock_code) AS sample,
            ROUND(AVG((open_p - prev_close)/prev_close*100)::numeric, 2) AS avg_open_return,
            ROUND(AVG((high_p - prev_close)/prev_close*100)::numeric, 2) AS avg_high_return,
            ROUND(AVG((close_p - prev_close)/prev_close*100)::numeric, 2) AS avg_close_return
        FROM today_perf
    """, yesterday, today)

    if not row or not row.get("sample"):
        return {"sample_count": 0, "avg_open_return": 0, "avg_high_return": 0,
                "avg_close_return": 0, "seal_rate": 0, "break_rate": 0}

    r = dict(row)
    r["seal_rate"] = 0.85  # TODO: 需要涨停封板明细数据
    r["break_rate"] = 0.15
    return r
