"""
services/sentiment_service.py
================================
全球 & A股 情绪评分引擎

A股情绪分 (0-100):
  15% 市场宽度 + 15% 涨跌停强度 + 15% 连板晋级率
+ 15% 成交量能 + 15% 板块扩散 + 10% 主力资金
+  8% 指数趋势 +  7% 新闻情绪

全球情绪分 (0-100):
  25% 股票指数风险偏好 + 20% VIX反向分
+ 15% 美元强弱反向 + 10% 黄金避险反向
+ 10% 原油/铜增长预期 + 20% 全球新闻情绪

所有因子保留 raw / normalized / weight，写入 sentiment_snapshot 表
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Any

from db import database as db
from services.cache import cache, CacheKeys

logger = logging.getLogger(__name__)


# ────────── A股情绪 ──────────
async def compute_ashare_sentiment() -> dict:
    """
    A股情绪评分 (可解释加权模型)
    返回: {score, label, factors: {name: {raw, normalized, weight}}}
    """
    factors = {}
    now = datetime.now()

    # 1. 市场宽度 (权重 15%)
    breadth = await _get_latest_breadth()
    up = breadth.get("up_count", 0)
    down = breadth.get("down_count", 0)
    total = max(breadth.get("total", 1), 1)
    up_ratio = up / total
    breadth_score = max(0, min(100, int(up_ratio * 100)))
    factors["市场宽度"] = _factor(up_ratio, breadth_score, 15)

    # 2. 涨跌停强度 (权重 15%)
    limit_up = breadth.get("limit_up", 0)
    limit_down = breadth.get("limit_down", 0)
    # 涨停数/总股票数 映射到 0-100
    limit_up_ratio = limit_up / max(total, 1)
    limit_score = max(0, min(100, int(limit_up_ratio * 500)))  # 100/5000=2% → 10分
    factors["涨跌停强度"] = _factor(limit_up, limit_score, 15)

    # 3. 连板晋级率 (权重 15%)
    ladder = await _get_ladder_data()
    ladder_score = _calc_ladder_score(ladder)
    factors["连板晋级"] = _factor(len(ladder), ladder_score, 15)

    # 4. 成交量能 (权重 15%)
    vol_data = await _get_volume_data()
    vol_score = _calc_volume_score(vol_data)
    factors["成交量能"] = _factor(vol_data.get("ratio", 1.0), vol_score, 15)

    # 5. 板块扩散 (权重 15%)
    sector_score = await _calc_sector_spread()
    factors["板块扩散"] = _factor(None, sector_score, 15)

    # 6. 主力资金 (权重 10%)
    fund_score = await _calc_fund_score()
    factors["主力资金"] = _factor(None, fund_score, 10)

    # 7. 指数趋势 (权重 8%)
    index_score = await _calc_index_trend()
    factors["指数趋势"] = _factor(None, index_score, 8)

    # 8. 新闻情绪 (权重 7%)
    news_score = await _calc_news_sentiment()
    factors["新闻情绪"] = _factor(None, news_score, 7)

    # 加权求和
    total_score = sum(f["normalized"] * f["weight"] / 100 for f in factors.values())
    total_score = max(5, min(95, int(total_score)))

    label = _score_label(total_score)

    result = {
        "scope": "global" if False else "ashare",
        "score": total_score,
        "label": label,
        "factors": {k: {"raw": v["raw"], "normalized": v["normalized"], "weight": v["weight"]}
                     for k, v in factors.items()},
        "snapshot_time": now,
    }
    result["scope"] = "ashare"  # fix

    # 写入数据库
    await _store_sentiment(result)

    # 更新缓存
    cache.set(CacheKeys.SENTIMENT_ASHARE, result, ttl=60)

    return result


def _factor(raw_value, normalized: int, weight: int) -> dict:
    return {"raw": raw_value, "normalized": normalized, "weight": weight}


def _score_label(score: int) -> str:
    if score <= 20: return "极弱"
    if score <= 40: return "偏弱"
    if score <= 60: return "中性"
    if score <= 80: return "偏强"
    return "过热"


# ────────── 全球情绪 ──────────
async def compute_global_sentiment() -> dict:
    """
    全球情绪评分
    """
    factors = {}
    now = datetime.now()

    # 1. 全球股票指数风险偏好 (权重 25%)
    global_index_score = await _calc_global_index_risk()
    factors["全球股指"] = _factor(None, global_index_score, 25)

    # 2. VIX 反向分 (权重 20%)
    vix_score = await _calc_vix_score()
    factors["VIX恐慌"] = _factor(None, vix_score, 20)

    # 3. 美元强弱反向 (权重 15%)
    dollar_score = await _calc_dollar_score()
    factors["美元指数"] = _factor(None, dollar_score, 15)

    # 4. 黄金避险反向 (权重 10%)
    gold_score = await _calc_gold_score()
    factors["黄金避险"] = _factor(None, gold_score, 10)

    # 5. 原油/铜增长预期 (权重 10%)
    commodity_score = await _calc_commodity_score()
    factors["商品预期"] = _factor(None, commodity_score, 10)

    # 6. 全球新闻情绪 (权重 20%)
    global_news_score = 50  # 暂时中性
    factors["全球新闻"] = _factor(None, global_news_score, 20)

    total_score = sum(f["normalized"] * f["weight"] / 100 for f in factors.values())
    total_score = max(5, min(95, int(total_score)))

    result = {
        "scope": "global",
        "score": total_score,
        "label": _score_label(total_score),
        "long_count": global_index_score,   # 简化: 上涨市场数
        "neutral_count": 0,
        "short_count": 100 - global_index_score if global_index_score < 50 else 0,
        "factors": {k: {"raw": v["raw"], "normalized": v["normalized"], "weight": v["weight"]}
                     for k, v in factors.items()},
        "snapshot_time": now,
    }

    await _store_sentiment(result)
    cache.set(CacheKeys.SENTIMENT_GLOBAL, result, ttl=120)
    return result


# ────────── 子因子计算 ──────────

async def _get_latest_breadth() -> dict:
    row = await db.fetch_one("""
        SELECT up_count, down_count, flat_count, limit_up, limit_down, big_up, big_down, total
        FROM market_breadth ORDER BY snapshot_time DESC LIMIT 1
    """)
    return dict(row) if row else {"up_count": 0, "down_count": 0, "total": 1}


async def _get_ladder_data() -> list[dict]:
    from services.market_service import get_limit_up_ladder
    return await get_limit_up_ladder()


def _calc_ladder_score(ladder: list[dict]) -> int:
    """连板越多 → 分数越高"""
    if not ladder:
        return 30  # 无连板 = 中性偏弱
    total = sum(item.get("count", 0) for item in ladder)
    return max(0, min(100, int(total * 2 + 30)))


async def _get_volume_data() -> dict:
    from services.market_service import get_volume_comparison
    return await get_volume_comparison()


def _calc_volume_score(vol_data: dict) -> int:
    """量比 > 1.2=放量偏强, < 0.8=缩量偏弱"""
    ratio = vol_data.get("ratio", 1.0)
    if ratio >= 1.5: return 85
    if ratio >= 1.2: return 70
    if ratio >= 1.0: return 55
    if ratio >= 0.8: return 40
    return 20


async def _calc_sector_spread() -> int:
    """上涨板块占比"""
    row = await db.fetch_one("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE change_pct > 0) AS up_count
        FROM v_sector_top
    """)
    if not row or not row.get("total"):
        return 50
    return max(0, min(100, int(row["up_count"] / row["total"] * 100)))


async def _calc_fund_score() -> int:
    """主力资金方向"""
    row = await db.fetch_one("""
        SELECT COALESCE(SUM(main_net_inflow), 0) AS total_inflow
        FROM fund_flow WHERE trade_date = CURRENT_DATE
    """)
    if not row:
        return 50
    inflow = (row.get("total_inflow") or 0) / 1e8  # 亿元
    if inflow > 100: return 85
    if inflow > 30:  return 70
    if inflow > 0:   return 55
    if inflow > -30: return 40
    return 20


async def _calc_index_trend() -> int:
    """上证指数近5日趋势"""
    row = await db.fetch_one("""
        SELECT AVG(close) FILTER (WHERE trade_date >= CURRENT_DATE - INTERVAL '5 days') AS avg5,
               AVG(close) FILTER (WHERE trade_date >= CURRENT_DATE - INTERVAL '20 days') AS avg20
        FROM stock_prices
        WHERE stock_code = '000001'
    """)
    if not row or not row.get("avg5"):
        return 50
    avg5 = float(row["avg5"] or 0)
    avg20 = float(row["avg20"] or avg5)
    ratio = (avg5 / max(avg20, 0.01) - 1) * 100
    return max(10, min(90, int(50 + ratio * 10)))


async def _calc_news_sentiment() -> int:
    """近期新闻情感"""
    row = await db.fetch_one("""
        SELECT AVG(sentiment) AS avg_sent FROM news
        WHERE publish_time > NOW() - INTERVAL '2 hours'
          AND sentiment IS NOT NULL
    """)
    if not row or row.get("avg_sent") is None:
        return 50
    return max(10, min(90, int(50 + float(row["avg_sent"]) * 40)))


# ────────── 全球情绪子因子 ──────────

async def _calc_global_index_risk() -> int:
    """全球主要指数涨跌比例"""
    rows = await db.fetch_all("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE asset_type = 'index' AND market IN ('US', 'HK', 'JP', 'EU')
    """)
    if not rows:
        return 50
    up = sum(1 for r in rows if (r.get("change_pct") or 0) > 0)
    return max(0, min(100, int(up / len(rows) * 100)))


async def _calc_vix_score() -> int:
    """VIX 反向映射: VIX越高 → 分数越低"""
    row = await db.fetch_one("""
        SELECT close FROM latest_global_indices
        WHERE index_code ILIKE '%VIX%' LIMIT 1
    """)
    if not row:
        return 50
    vix = float(row.get("close") or 20)
    # VIX 10-15=极度乐观, 15-20=正常, 20-30=担忧, >30=恐慌
    if vix < 12: return 90
    if vix < 15: return 75
    if vix < 20: return 60
    if vix < 25: return 40
    if vix < 30: return 25
    return 10


async def _calc_dollar_score() -> int:
    """美元强 → 新兴市场压力 → 分数低 (反向)"""
    row = await db.fetch_one("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE symbol ILIKE '%USD%CNH%' OR symbol = 'USDCNH.F'
        LIMIT 1
    """)
    if not row:
        return 50
    chg = float(row.get("change_pct") or 0)
    # 美元涨 → 反向分低
    return max(10, min(90, int(50 - chg * 10)))


async def _calc_gold_score() -> int:
    """黄金涨 → 避险情绪 → 分数低 (反向)"""
    row = await db.fetch_one("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE symbol ILIKE '%GC%' OR symbol ILIKE '%gold%' OR name ILIKE '%黄金%'
        LIMIT 1
    """)
    if not row:
        return 50
    chg = float(row.get("change_pct") or 0)
    return max(10, min(90, int(50 - chg * 5)))


async def _calc_commodity_score() -> int:
    """原油/铜涨 → 经济增长预期 → 分数高"""
    rows = await db.fetch_all("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE symbol IN ('CL.F', 'HG.F') OR name IN ('WTI原油', '铜')
    """)
    if not rows:
        return 50
    avg_chg = sum(float(r.get("change_pct") or 0) for r in rows) / len(rows)
    return max(10, min(90, int(50 + avg_chg * 5)))


# ────────── 持久化 ──────────
async def _store_sentiment(result: dict):
    import json
    await db.execute("""
        INSERT INTO sentiment_snapshot
            (scope, score, label, long_count, neutral_count, short_count, factors, snapshot_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
    """,
        result["scope"], result["score"], result["label"],
        result.get("long_count", 0), result.get("neutral_count", 0),
        result.get("short_count", 0),
        json.dumps(result.get("factors", {}), ensure_ascii=False),
        result["snapshot_time"],
    )


# ────────── 便捷入口 ──────────
async def compute_all():
    """计算并存储所有情绪"""
    ashare = await compute_ashare_sentiment()
    global_ = await compute_global_sentiment()
    logger.info("Sentiment: A-share=%d(%s) Global=%d(%s)",
                ashare["score"], ashare["label"],
                global_["score"], global_["label"])
    return {"ashare": ashare, "global": global_}
