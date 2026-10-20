"""
6stockPulse · 仪表盘聚合服务
================================
首屏单次聚合 (/api/dashboard/snapshot)，避免浏览器发 15+ 请求。
所有子查询并行执行，结果通过 MemCache 缓存。
"""
from __future__ import annotations
import time
import asyncio
import logging
from datetime import datetime

from db import database as db
from services.cache import cache, CacheKeys
from application.realtime.service import realtime, in_trading_hours
from scheduler.task_registry import get_market_session, MarketSession

logger = logging.getLogger(__name__)


# ────────── 市场时段判断 ──────────
_SESSION_MAP = {
    MarketSession.MORNING: "trading",
    MarketSession.AFTERNOON: "trading",
    MarketSession.LUNCH: "lunch_break",
    MarketSession.CLOSED: "closed",
    MarketSession.PRE_OPEN: "closed",
    MarketSession.POST_CLOSE: "closed",
}


def market_session() -> str:
    return _SESSION_MAP.get(get_market_session(), "closed")


# ────────── 主聚合 ──────────
async def get_snapshot(use_cache: bool = True) -> dict:
    """一次性获取仪表盘所有首屏数据"""
    t0 = time.monotonic()

    if use_cache:
        cached = cache.get(CacheKeys.DASH_SNAPSHOT)
        if cached:
            return cached

    # 并行执行所有子查询
    results = await asyncio.gather(
        _get_tickers(),
        _get_breadth(),
        _get_anomalies(),
        _get_sector_flow(),
        _get_stats(),
        _get_ladder(),
        _get_news(),
        _get_sentiment(),
        _get_prediction(),
        _get_concept_leaders(),
        _get_signals(),
        return_exceptions=True,
    )

    keys = ["tickers", "breadth", "anomalies", "sector_flow",
            "stats", "ladder", "news", "sentiment", "prediction",
            "concept_leaders", "signals"]
    data = {}
    for k, r in zip(keys, results):
        if isinstance(r, Exception):
            logger.error("snapshot sub-query [%s] failed: %s", k, r)
            data[k] = _empty_for(k)
        else:
            data[k] = r

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    snapshot = {
        "server_time": datetime.now().isoformat(),
        "market_session": market_session(),
        "freshness_ms": elapsed_ms,
        "data": data,
    }

    # 缓存 (交易时段 3s, 非交易 30s)
    ttl = 3 if in_trading_hours() else 30
    cache.set(CacheKeys.DASH_SNAPSHOT, snapshot, ttl=ttl)

    return snapshot


def _empty_for(key: str):
    defaults = {
        "tickers": [], "breadth": {}, "anomalies": [], "sector_flow": [],
        "stats": [], "ladder": [], "news": [], "concept_leaders": [], "signals": [],
        "sentiment": {"global": {}, "ashare": {}}, "prediction": {},
    }
    return defaults.get(key, [])


# ────────── 子查询实现 ──────────

async def _get_tickers() -> list[dict]:
    """全球行情滚动带: 指数 + 商品 + 外汇"""
    rows = await db.fetch_all("""
        SELECT symbol, name, asset_type, market, price, prev_close,
               change, change_pct, quote_time, source, is_stale
        FROM market_quote_snapshot
        ORDER BY
          CASE asset_type
            WHEN 'index' THEN 1 WHEN 'commodity' THEN 2 WHEN 'forex' THEN 3
          END,
          CASE market
            WHEN 'SH' THEN 1 WHEN 'US' THEN 2 WHEN 'HK' THEN 3
            WHEN 'JP' THEN 4 WHEN 'EU' THEN 5 WHEN 'COMM' THEN 6 WHEN 'FX' THEN 7
          END
    """)
    if not rows:
        # Fallback: 从 global_indices 物化视图读取
        rows = await db.fetch_all("""
            SELECT index_code AS symbol, index_name AS name, 'index' AS asset_type,
                   market, close AS price, prev_close,
                   change_amt AS change, change_pct,
                   trade_time::text AS quote_time, source, FALSE AS is_stale
            FROM latest_global_indices
        """)
    return [_to_dict(r) for r in rows]


async def _get_breadth() -> dict:
    """市场宽度"""
    row = await db.fetch_one("""
        SELECT up_count, down_count, flat_count, limit_up, limit_down,
               big_up, big_down, total, sentiment_score,
               snapshot_time::text AS snapshot_time
        FROM market_breadth
        ORDER BY snapshot_time DESC
        LIMIT 1
    """)
    if not row:
        # 实时计算
        return await _compute_breadth_realtime()
    return _to_dict(row)


async def _compute_breadth_realtime() -> dict:
    """实时计算市场宽度 (从 latest_quotes)"""
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
    if not row:
        return {}
    r = _to_dict(row)
    up = r.get("up_count", 0) or 0
    total = max(r.get("total", 1) or 1, 1)
    up_pct = up / total * 100
    r["sentiment_score"] = max(5, min(95, int(up_pct * 0.65 + 35)))
    r["snapshot_time"] = datetime.now().isoformat()
    return r


async def _get_anomalies() -> list[dict]:
    """活跃异动事件"""
    from services.anomaly_service import get_active_anomalies
    return await get_active_anomalies(limit=20)


async def _get_sector_flow() -> list[dict]:
    """板块资金流 TOP 10"""
    rows = await db.fetch_all("""
        SELECT s.sector_code, s.sector_name, s.sector_type,
               s.change_pct, s.main_net_inflow, s.main_net_ratio,
               s.turnover_rate
        FROM v_sector_top s
        WHERE s.rnk <= 10
        ORDER BY s.rnk
    """)
    if not rows:
        return []

    # 计算 bar 宽度: 取绝对值最大的 inflow 为 100%
    max_inflow = max(abs(r.get("main_net_inflow") or 0) for r in rows) or 1
    result = []
    for r in rows:
        d = _to_dict(r)
        inflow = d.get("main_net_inflow") or 0
        d["bar_pct"] = round(abs(inflow) / max_inflow * 100, 1)
        # 龙头占位 (后续通过 JOIN stocks 补充)
        d["leader_stock"] = None
        d["leader_name"] = None
        d["leader_change_pct"] = None
        result.append(d)
    return result


async def _get_stats() -> list[dict]:
    """市场统计卡片: 上涨/下跌/情绪/涨停/跌停/赚钱效应"""
    breadth = await _get_breadth()
    return [
        {"icon": "↑", "value": breadth.get("up_count", 0), "label": "上涨家数",
         "sub": f"{breadth.get('big_up', 0)} 大涨", "colorClass": "red-text"},
        {"icon": "↓", "value": breadth.get("down_count", 0), "label": "下跌家数",
         "sub": f"{breadth.get('big_down', 0)} 大跌", "colorClass": "green-text"},
        {"icon": "◉", "value": breadth.get("sentiment_score", 50), "label": "今日情绪",
         "sub": "综合分", "colorClass": "yellow-text"},
        {"icon": "▲", "value": breadth.get("limit_up", 0), "label": "涨停家数",
         "sub": "含ST", "colorClass": "red-text"},
        {"icon": "▽", "value": breadth.get("limit_down", 0), "label": "跌停家数",
         "sub": "含ST", "colorClass": "green-text"},
        {"icon": "⚡", "value": breadth.get("flat_count", 0), "label": "平盘",
         "sub": "横盘整理", "colorClass": "yellow-text"},
    ]


async def _get_ladder() -> list[dict]:
    """连板梯队 (数据不足时降级为空)"""
    rows = await db.fetch_all("""
        SELECT label, count
        FROM (
            SELECT
                CASE
                    WHEN cnt >= 5 THEN '5板+'
                    ELSE cnt::text || '板'
                END AS label,
                cnt,
                COUNT(*) AS count
            FROM (
                SELECT stock_code,
                       COUNT(*) FILTER (WHERE change_pct >= 9.95) AS cnt
                FROM stock_prices
                WHERE trade_date >= CURRENT_DATE - INTERVAL '10 days'
                  AND change_pct >= 9.9
                GROUP BY stock_code
            ) t
            WHERE cnt >= 2
            GROUP BY label, cnt
        ) t2
        ORDER BY
            CASE label
                WHEN '2板' THEN 1 WHEN '3板' THEN 2
                WHEN '4板' THEN 3 WHEN '5板+' THEN 4
            END
    """)
    if not rows:
        return []
    max_count = max(r.get("count", 0) for r in rows) or 1
    return [
        {"label": r["label"], "count": r["count"],
         "bar_pct": round(r["count"] / max_count * 100, 1)}
        for r in rows
    ]


async def _get_news() -> list[dict]:
    """最新 30 条新闻"""
    rows = await db.fetch_all("""
        SELECT news_id, title,
               COALESCE(NULLIF(content, ''), summary, '') AS content,
               source, publish_time::text AS publish_time,
               category, sentiment, importance, stocks, keywords
        FROM news
        ORDER BY publish_time DESC
        LIMIT 30
    """)
    return [_to_dict(r) for r in rows]


async def _get_sentiment() -> dict:
    """全球 + A股情绪"""
    # 全球情绪
    global_row = await db.fetch_one("""
        SELECT score, label, long_count, neutral_count, short_count,
               factors, snapshot_time::text AS snapshot_time
        FROM sentiment_snapshot
        WHERE scope = 'global'
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    # A股情绪
    ashare_row = await db.fetch_one("""
        SELECT score, label, long_count, neutral_count, short_count,
               factors, snapshot_time::text AS snapshot_time
        FROM sentiment_snapshot
        WHERE scope = 'ashare'
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    return {
        "global": _to_dict(global_row) if global_row else _default_sentiment("global"),
        "ashare": _to_dict(ashare_row) if ashare_row else _default_sentiment("ashare"),
    }


def _default_sentiment(scope: str) -> dict:
    return {"scope": scope, "score": 50, "label": "中性", "snapshot_time": None}


async def _get_prediction() -> dict:
    """次日方向预测"""
    row = await db.fetch_one("""
        SELECT horizon, up_probability, flat_probability, down_probability,
               confidence, explanation, model_version,
               generated_at::text AS generated_at
        FROM prediction_result
        ORDER BY generated_at DESC LIMIT 1
    """)
    if not row:
        return _default_prediction()
    return _to_dict(row)


def _default_prediction() -> dict:
    return {
        "horizon": "next_session",
        "up_probability": 0.33, "flat_probability": 0.34, "down_probability": 0.33,
        "confidence": 0.5, "explanation": [],
        "model_version": "rule-baseline-v1", "generated_at": None,
    }


async def _get_concept_leaders() -> list[dict]:
    """概念领涨板块"""
    rows = await db.fetch_all("""
        SELECT s.sector_code, s.sector_name,
               s.change_pct, s.main_net_inflow
        FROM v_sector_top s
        WHERE s.rnk <= 6 AND s.change_pct IS NOT NULL
        ORDER BY s.change_pct DESC
    """)
    result = []
    for r in rows:
        d = _to_dict(r)
        d["consecutive_days"] = 1
        d["news_count"] = 0
        d["leader_stock"] = None
        d["leader_name"] = None
        d["risk_tags"] = []
        result.append(d)
    return result


async def _get_signals() -> list[dict]:
    """活跃信号"""
    rows = await db.fetch_all("""
        SELECT id, signal_type, title, summary, evidence,
               direction, confidence, severity,
               generated_at::text AS generated_at
        FROM signal_snapshot
        WHERE expires_at > NOW() OR expires_at IS NULL
        ORDER BY confidence DESC, generated_at DESC
        LIMIT 10
    """)
    return [_to_dict(r) for r in rows]


# ────────── 辅助 ──────────
def _to_dict(row) -> dict:
    """asyncpg Record → dict，保留基本类型"""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        logger.warning("_to_dict failed for row type=%s", type(row).__name__, exc_info=True)
        return {}


# ────────── 缓存刷新 ──────────
def invalidate_snapshot():
    """强制刷新缓存 (在行情更新后调用)"""
    cache.delete(CacheKeys.DASH_SNAPSHOT)
    cache.delete(CacheKeys.BREADTH_LATEST)
    cache.delete(CacheKeys.SENTIMENT_GLOBAL)
    cache.delete(CacheKeys.SENTIMENT_ASHARE)
    cache.delete(CacheKeys.ANOMALY_ACTIVE)
    cache.delete(CacheKeys.SIGNALS_ACTIVE)
