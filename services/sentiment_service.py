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

# 上证指数日线收盘价缓存 (情绪快照每分钟计算, 避免每分钟请求新浪)
_INDEX_DAILY_MEMO: dict[str, Any] = {"ts": 0.0, "closes": []}


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
    up_ratio = float(up) / float(total)
    breadth_score = max(0, min(100, int(up_ratio * 100)))
    factors["市场宽度"] = _factor(up_ratio, breadth_score, 15)

    # 2. 涨跌停强度 (权重 15%)
    limit_up = breadth.get("limit_up", 0)
    limit_down = breadth.get("limit_down", 0)
    # 涨停数/总股票数 映射到 0-100
    limit_up_ratio = float(limit_up) / max(float(total), 1)
    limit_down_ratio = float(limit_down) / max(float(total), 1)
    # 涨跌停净占比分段映射（A股经验：净占比 1%≈60分、2%≈80分、3%+≈95分）
    net_pct = (limit_up_ratio - limit_down_ratio) * 100
    if net_pct >= 3:
        limit_score = 95
    elif net_pct >= 2:
        limit_score = 80
    elif net_pct >= 1:
        limit_score = 60
    elif net_pct >= 0.5:
        limit_score = 50
    elif net_pct >= 0:
        limit_score = 40
    else:
        limit_score = 20
    factors["涨跌停强度"] = _factor(limit_up, limit_score, 15)

    # 3. 连板晋级率 (权重 15%)；无数据时权重降为 0（不参与加权）
    ladder = await _get_ladder_data()
    ladder_score = _calc_ladder_score(ladder)
    ladder_weight = 15 if ladder else 0
    ladder_factor = _factor(len(ladder), ladder_score, ladder_weight)
    if not ladder:
        ladder_factor["no_data"] = True
        ladder_factor["weight_applied"] = 0
        ladder_factor["note"] = "连板梯队为空（无数据或非交易时段），权重暂降为 0"
    factors["连板晋级"] = ladder_factor

    # 4. 成交量能 (权重 15%)
    vol_data = await _get_volume_data()
    vol_score = _calc_volume_score(vol_data)
    factors["成交量能"] = _factor(vol_data.get("ratio", 1.0), vol_score, 15)

    # 5. 板块扩散 (权重 15%)
    sector_score, sector_raw = await _calc_sector_spread()
    factors["板块扩散"] = _factor(sector_raw, sector_score, 15)

    # 6. 主力资金 (权重 10%)
    fund_score, fund_raw = await _calc_fund_score()
    factors["主力资金"] = _factor(fund_raw, fund_score, 10)

    # 7. 指数趋势 (权重 8%)
    index_score, index_raw = await _calc_index_trend()
    factors["指数趋势"] = _factor(index_raw, index_score, 8)

    # 8. 新闻情绪 (权重 7%)
    news_score, news_raw = await _calc_news_sentiment()
    factors["新闻情绪"] = _factor(news_raw, news_score, 7)

    # 加权求和（用实际权重和归一化，支持某因子权重为 0）
    total_weight = sum(f["weight"] for f in factors.values())
    total_score = sum(f["normalized"] * f["weight"] for f in factors.values()) / max(total_weight, 1)
    total_score = max(5, min(95, int(total_score)))

    label = _score_label(total_score)

    result = {
        "scope": "ashare",
        "score": total_score,
        "label": label,
        "factors": {k: dict(v) for k, v in factors.items()},
        "snapshot_time": now,
    }
    result["scope"] = "ashare"  # fix

    # 写入数据库
    await _store_sentiment(result)

    # 更新缓存
    cache.set(CacheKeys.SENTIMENT_ASHARE, result, ttl=60)

    return result


def _factor(raw_value, normalized: int, weight: int) -> dict:
    d = {"raw": raw_value, "normalized": normalized, "weight": weight}
    if raw_value is None:
        d["no_data"] = True  # raw 缺失，normalized 为默认值填充
    return d


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
    global_index_score, global_index_raw = await _calc_global_index_risk()
    factors["全球股指"] = _factor(global_index_raw, global_index_score, 25)

    # 2. VIX 反向分 (权重 20%)
    vix_score, vix_raw = await _calc_vix_score()
    factors["VIX恐慌"] = _factor(vix_raw, vix_score, 20)

    # 3. 美元强弱反向 (权重 15%)
    dollar_score, dollar_raw = await _calc_dollar_score()
    factors["美元指数"] = _factor(dollar_raw, dollar_score, 15)

    # 4. 黄金避险反向 (权重 10%)
    gold_score, gold_raw = await _calc_gold_score()
    factors["黄金避险"] = _factor(gold_raw, gold_score, 10)

    # 5. 原油/铜增长预期 (权重 10%)
    commodity_score, commodity_raw = await _calc_commodity_score()
    factors["商品预期"] = _factor(commodity_raw, commodity_score, 10)

    # 6. 全球新闻情绪 (权重 20%)
    global_news_score = 50  # 暂时中性
    factors["全球新闻"] = _factor(None, global_news_score, 20)

    # 缺失值降权：raw=null 的因子 weight 置 0（参照连板做法，避免中性值稀释总分）
    for f in factors.values():
        if f.get("raw") is None:
            f["weight"] = 0
            f["weight_applied"] = 0

    # 加权求和（用实际权重和归一化）
    total_weight = sum(f["weight"] for f in factors.values())
    total_score = sum(f["normalized"] * f["weight"] for f in factors.values()) / max(total_weight, 1)
    total_score = max(5, min(95, int(total_score)))

    result = {
        "scope": "global",
        "score": total_score,
        "label": _score_label(total_score),
        "long_count": global_index_score,
        "neutral_count": 0,
        "short_count": 100 - global_index_score if global_index_score < 50 else 0,
        "factors": {k: dict(v) for k, v in factors.items()},
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
    """连板越多 → 分数越高；无连板返回中性 50（不再给 30 的偏弱默认值）"""
    if not ladder:
        return 50
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


async def _calc_sector_spread() -> tuple[int, float | None]:
    """上涨板块占比 → (score, 上涨板块占比 raw)"""
    row = await db.fetch_one("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE change_pct > 0) AS up_count
        FROM v_sector_top
    """)
    if not row or not row.get("total"):
        return 50, None
    up_ratio = float(row["up_count"]) / float(row["total"])
    return max(0, min(100, int(up_ratio * 100))), round(up_ratio, 4)


async def _calc_fund_score() -> tuple[int, float | None]:
    """主力资金方向 → (score, 净流入亿元 raw)"""
    row = await db.fetch_one("""
        SELECT COALESCE(SUM(main_net_inflow), 0) AS total_inflow
        FROM (
            SELECT DISTINCT ON (stock_code) stock_code, main_net_inflow
            FROM fund_flow
            WHERE trade_date = CURRENT_DATE
            ORDER BY stock_code, trade_time DESC
        ) t
    """)
    if not row:
        return 50, None
    inflow = float(row.get("total_inflow") or 0) / 1e8  # 亿元
    if inflow > 100: return 85, round(inflow, 2)
    if inflow > 30:  return 70, round(inflow, 2)
    if inflow > 0:   return 55, round(inflow, 2)
    if inflow > -30: return 40, round(inflow, 2)
    return 20, round(inflow, 2)


async def _calc_index_trend() -> tuple[int, float | None]:
    """上证指数近5日 vs 20日均线趋势 → (score, 5/20日均线偏离%)
    数据源: 新浪日线 sh000001 (scale=240, datalen=21)。
    注意: stock_prices 的 '000001' 是平安银行(深市), 不代表指数;
    market_quote_snapshot 只有最新快照无历史, 故取新浪日线。
    任一环节失败返回 (50, None); 日线结果缓存 300 秒(情绪快照每分钟计算)。
    """
    import json as _json
    import re as _re
    import time as _time

    import aiohttp

    now_ts = _time.time()
    closes: list[float] = []
    if now_ts - _INDEX_DAILY_MEMO["ts"] < 300 and _INDEX_DAILY_MEMO["closes"]:
        closes = list(_INDEX_DAILY_MEMO["closes"])
    else:
        try:
            url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/"
                   "CN_MarketDataService.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=21")
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers={"Referer": "https://finance.sina.com.cn/"},
                                 timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return 50, None
                    txt = await r.text()
            m = _re.search(r"\[.*\]", txt, _re.S)
            if not m:
                return 50, None
            arr = _json.loads(m.group(0))
            closes = [float(x["close"]) for x in arr if x.get("close")]
        except Exception:
            return 50, None
        if len(closes) < 5:
            return 50, None
        _INDEX_DAILY_MEMO["ts"] = now_ts
        _INDEX_DAILY_MEMO["closes"] = list(closes)

    if len(closes) < 5:
        return 50, None
    avg5 = sum(closes[-5:]) / 5.0
    avg20 = sum(closes[-20:]) / float(min(20, len(closes)))
    ratio = (avg5 / max(avg20, 0.01) - 1) * 100
    return max(10, min(90, int(50 + ratio * 10))), round(ratio, 2)


async def _calc_news_sentiment() -> tuple[int, float | None]:
    """近期新闻情感 → (score, 平均情感 raw)"""
    row = await db.fetch_one("""
        SELECT AVG(sentiment) AS avg_sent FROM news
        WHERE publish_time > NOW() - INTERVAL '2 hours'
          AND sentiment IS NOT NULL
    """)
    if not row or row.get("avg_sent") is None:
        return 50, None
    avg_sent = float(row["avg_sent"])
    return max(10, min(90, int(50 + avg_sent * 40))), round(avg_sent, 4)


# ────────── 全球情绪子因子 ──────────

async def _calc_global_index_risk() -> tuple[int, float | None]:
    """全球主要指数涨跌比例（采集器把 market 写成 symbol 后缀：DJIA/HSI/N225/NDX/SPX）→ (score, 上涨占比)"""
    rows = await db.fetch_all("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE asset_type = 'index'
          AND (symbol LIKE '100.%' OR market IN ('DJIA','HSI','N225','NDX','SPX'))
    """)
    if not rows:
        return 50, None
    up = sum(1 for r in rows if (r.get("change_pct") or 0) > 0)
    ratio = up / len(rows)
    return max(0, min(100, int(ratio * 100))), round(ratio, 4)


async def _calc_vix_score() -> tuple[int, float | None]:
    """VIX 反向映射 → (score, VIX 点位)"""
    row = await db.fetch_one("""
        SELECT price FROM market_quote_snapshot
        WHERE (symbol ILIKE '%VIX%' OR name ILIKE '%VIX%') AND asset_type = 'index'
        ORDER BY quote_time DESC LIMIT 1
    """)
    if not row:
        return 50, None
    vix = float(row.get("price") or 20)
    # VIX 10-15=极度乐观, 15-20=正常, 20-30=担忧, >30=恐慌
    if vix < 12: return 90, round(vix, 2)
    if vix < 15: return 75, round(vix, 2)
    if vix < 20: return 60, round(vix, 2)
    if vix < 25: return 40, round(vix, 2)
    if vix < 30: return 25, round(vix, 2)
    return 10, round(vix, 2)


async def _calc_dollar_score() -> tuple[int, float | None]:
    """美元强 → 新兴市场压力 → 分数低 (反向) → (score, 美元涨跌幅%)"""
    row = await db.fetch_one("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE symbol = '100.UDI' OR symbol ILIKE '%USDCNH%' OR name ILIKE '%美元%'
        ORDER BY quote_time DESC LIMIT 1
    """)
    if not row:
        return 50, None
    chg = float(row.get("change_pct") or 0)
    # 美元涨 → 反向分低
    return max(10, min(90, int(50 - chg * 10))), round(chg, 4)


async def _calc_gold_score() -> tuple[int, float | None]:
    """黄金涨 → 避险情绪 → 分数低 (反向) → (score, 黄金涨跌幅%)"""
    row = await db.fetch_one("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE symbol ILIKE '%GC%' OR symbol ILIKE '%gold%' OR name ILIKE '%黄金%'
        ORDER BY quote_time DESC LIMIT 1
    """)
    if not row:
        return 50, None
    chg = float(row.get("change_pct") or 0)
    return max(10, min(90, int(50 - chg * 5))), round(chg, 4)


async def _calc_commodity_score() -> tuple[int, float | None]:
    """原油/铜涨 → 经济增长预期 → 分数高 → (score, 平均涨跌幅%)"""
    rows = await db.fetch_all("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE symbol IN ('CL.F', 'HG.F') OR name IN ('WTI原油', '铜')
          OR symbol LIKE '101.%'
    """)
    if not rows:
        return 50, None
    avg_chg = sum(float(r.get("change_pct") or 0) for r in rows) / len(rows)
    return max(10, min(90, int(50 + avg_chg * 5))), round(avg_chg, 4)


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
