"""
api/routes/market.py — 市场行情 / 板块 / 全球指数
"""
from __future__ import annotations
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import APIResponse
from infrastructure.database import postgres as db
from application.realtime.service import realtime
from app.api._utils import _jsonable

logger = logging.getLogger(__name__)

router = APIRouter(tags=["market"])

# ────────── A 股指数实时获取 (Sina 行情) ──────────
import aiohttp
import re

_SINA_INDEX_CODES = {
    "s_sh000001": ("000001.SH", "上证指数",  "SH", "index"),
    "s_sz399001": ("399001.SZ", "深证成指",  "SZ", "index"),
    "s_sz399006": ("399006.SZ", "创业板指",  "SZ", "index"),
    "s_sh000688": ("000688.SH", "科创50",    "SH", "index"),
}

_ashare_index_cache: list[dict] = []
_cache_ts: float = 0
_cache_lock = asyncio.Lock()
_CACHE_TTL = 5.0


async def _fetch_ashare_indices() -> list[dict]:
    """从新浪实时行情拉取 A 股指数 (带 5s 缓存)"""
    global _ashare_index_cache, _cache_ts
    import time
    now = time.monotonic()
    if _cache_ts and (now - _cache_ts) < _CACHE_TTL and _ashare_index_cache:
        return _ashare_index_cache

    async with _cache_lock:
        if _cache_ts and (time.monotonic() - _cache_ts) < _CACHE_TTL and _ashare_index_cache:
            return _ashare_index_cache
        results = []
        codes_str = ",".join(_SINA_INDEX_CODES.keys())
        url = f"https://hq.sinajs.cn/list={codes_str}"
        headers = {"Referer": "https://finance.sina.com.cn/"}
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    text = await resp.text()
            for line in text.splitlines():
                m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
                if not m:
                    continue
                sina_code = m.group(1)
                body = m.group(2)
                parts = body.split(",")
                if sina_code not in _SINA_INDEX_CODES or len(parts) < 4:
                    continue
                symbol, name, market, asset_type = _SINA_INDEX_CODES[sina_code]
                try:
                    prev_close = float(parts[2]) if parts[2] else 0
                    price = float(parts[3]) if parts[3] else 0
                    change = price - prev_close
                    change_pct = (change / prev_close * 100) if prev_close else 0
                    results.append({
                        "symbol": symbol,
                        "name": name,
                        "asset_type": asset_type,
                        "market": market,
                        "price": round(price, 2),
                        "prev_close": prev_close,
                        "change": round(change, 2),
                        "change_pct": round(change_pct, 2),
                        "quote_time": None,
                        "source": "sina",
                        "is_stale": False,
                    })
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            logger.warning("Failed to fetch A-share indices from Sina: %s", e)

        _ashare_index_cache = results
        _cache_ts = time.monotonic()
        return results


# ────────── Tickers (全球行情) ──────────
@router.get("/api/market/tickers")
async def market_tickers():
    """全球行情滚动带 — 含 A 股指数 + 全球指数 + 商品 + 外汇"""
    # 1) 全球行情 (market_quote_snapshot)
    global_rows = await db.fetch_all("""
        SELECT symbol, name, asset_type, market, price, prev_close,
               change, change_pct, quote_time::text AS quote_time,
               source, is_stale
        FROM market_quote_snapshot
        WHERE price IS NOT NULL AND price != 0
        ORDER BY
          CASE asset_type WHEN 'index' THEN 1 WHEN 'commodity' THEN 2 WHEN 'forex' THEN 3 END,
          symbol
    """)

    # 2) A 股指数 (实时)
    ashare = await _fetch_ashare_indices()

    # 合并: A 股指数排最前
    merged = ashare + [dict(r) for r in global_rows]

    # 去重
    seen = set()
    deduped = []
    for item in merged:
        if item["symbol"] not in seen:
            seen.add(item["symbol"])
            deduped.append(item)

    return APIResponse.success(deduped, source="market_quote_snapshot+sina")


# ────────── Market Breadth ──────────
@router.get("/api/market/breadth")
async def market_breadth():
    """市场宽度"""
    from application.market.analyzer import get_latest_breadth
    data = await get_latest_breadth()
    return APIResponse.success(data)


# ────────── 股票 ──────────
@router.get("/api/stocks")
async def list_stocks(limit: int = 100, offset: int = 0,
                       market: str | None = None, q: str | None = None):
    sql = "SELECT * FROM stocks WHERE is_active = TRUE"
    params: list = []
    if market:
        sql += f" AND market = ${len(params)+1}"
        params.append(market)
    if q:
        sql += f" AND (stock_code ILIKE ${len(params)+1} OR stock_name ILIKE ${len(params)+1})"
        params.extend([f"%{q}%", f"%{q}%"])
    sql += f" ORDER BY stock_code LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
    params.extend([limit, offset])
    rows = await db.fetch_all(sql, *params)
    return APIResponse.success(_jsonable(rows))


@router.get("/api/stocks/{code}")
async def get_stock(code: str):
    stock = await db.fetch_one("SELECT * FROM stocks WHERE stock_code = $1", code)
    if not stock:
        raise HTTPException(404, f"Stock {code} not found")
    quote = await db.fetch_one("SELECT * FROM latest_quotes WHERE stock_code = $1", code)
    return APIResponse.success({"stock": _jsonable(stock), "quote": _jsonable(quote)})


@router.get("/api/stocks/{code}/kline")
async def get_kline(code: str, days: int = 60):
    rows = await db.fetch_all("""
        SELECT trade_date, open, high, low, close, volume, amount
        FROM stock_prices WHERE stock_code = $1
        ORDER BY trade_time DESC LIMIT $2
    """, code, days)
    return APIResponse.success(_jsonable(list(reversed(rows))))


# ────────── 资金流向 ──────────
@router.get("/api/fund-flow")
async def fund_flow_today():
    row = await db.fetch_one("""
        SELECT COUNT(*) AS total,
            COUNT(*) FILTER (WHERE lq.change_pct > 0) AS up_count,
            COUNT(*) FILTER (WHERE lq.change_pct < 0) AS down_count,
            COUNT(*) FILTER (WHERE lq.change_pct = 0) AS flat_count,
            COUNT(*) FILTER (WHERE lq.change_pct >= 9.95) AS limit_up,
            COUNT(*) FILTER (WHERE lq.change_pct <= -9.95) AS limit_down,
            ROUND(AVG(lq.change_pct)::numeric, 3) AS avg_pct,
            SUM(ff.main_net_inflow) / 1e8 AS main_inflow_yi
        FROM latest_quotes lq
        JOIN fund_flow ff ON ff.stock_code = lq.stock_code
                          AND ff.trade_date = lq.trade_date
    """)
    return APIResponse.success(_jsonable(row))


@router.get("/api/fund-flow/top")
async def fund_flow_top(direction: str = "inflow", limit: int = 10):
    _ORDER = {"inflow": "DESC", "outflow": "ASC"}
    order = _ORDER.get(direction, "DESC")
    rows = await db.fetch_all(f"""
        SELECT s.stock_code, s.stock_name, s.industry,
               lq.close, lq.change_pct, lq.turnover_rate,
               ff.main_net_inflow, ff.super_large_net, ff.large_net_inflow,
               ff.mid_net_inflow, ff.small_net_inflow
        FROM latest_quotes lq
        JOIN stocks s ON s.stock_code = lq.stock_code
        LEFT JOIN fund_flow ff ON ff.stock_code = lq.stock_code
                              AND ff.trade_date = lq.trade_date
        WHERE ff.main_net_inflow IS NOT NULL
        ORDER BY ff.main_net_inflow {order}
        LIMIT $1
    """, limit)
    return APIResponse.success(_jsonable(rows))


# ────────── 板块 ──────────
@router.get("/api/sectors")
async def list_sectors(limit: int = 20):
    rows = await db.fetch_all("""
        SELECT * FROM v_sector_top WHERE rnk <= $1 ORDER BY rnk
    """, limit)
    return APIResponse.success(_jsonable(rows))


@router.get("/api/sectors/flow")
async def sector_flow(type: str = "concept", limit: int = 10):
    """板块资金流向 (前端 format)"""
    rows = await db.fetch_all("""
        SELECT s.sector_code, s.sector_name, s.sector_type,
               s.change_pct, s.main_net_inflow, s.main_net_ratio, s.turnover_rate
        FROM v_sector_top s
        WHERE s.rnk <= $1
        ORDER BY s.rnk
    """, limit)
    return APIResponse.success(_jsonable(rows))


# ────────── 全球指数 ──────────
@router.get("/api/indices")
async def list_global_indices(market: str | None = None, limit: int = 20):
    sql = "SELECT * FROM latest_global_indices WHERE 1=1"
    params: list = []
    if market:
        sql += f" AND market = ${len(params)+1}"
        params.append(market)
    sql += f" ORDER BY index_code LIMIT ${len(params)+1}"
    params.append(limit)
    rows = await db.fetch_all(sql, *params)
    return APIResponse.success(_jsonable(rows))


@router.get("/api/indices/vix")
async def get_vix():
    row = await db.fetch_one("SELECT * FROM latest_global_indices WHERE index_code ILIKE '%VIX%' LIMIT 1")
    if not row: raise HTTPException(404, "VIX not yet fetched")
    return APIResponse.success(_jsonable(row))


# ────────── 连板 / 赚钱效应 ──────────
@router.get("/api/short-term/limit-up-ladder")
async def limit_up_ladder():
    from application.market.analyzer import get_limit_up_ladder
    return APIResponse.success(await get_limit_up_ladder())


@router.get("/api/short-term/heat")
async def short_term_heat():
    from application.market.analyzer import get_enhanced_profit_effect
    return APIResponse.success(await get_enhanced_profit_effect())


# ────────── 分钟数据 ──────────
@router.get("/api/market/index/{symbol}/intraday")
async def index_intraday(symbol: str = "000001", date: str | None = None):
    """指数分时图数据"""
    from application.market.analyzer import get_index_intraday
    data = await get_index_intraday(symbol, date)
    return APIResponse.success(data)


@router.get("/api/market/volume-comparison")
async def volume_comparison():
    """今日 vs 昨日 分钟量能对比"""
    from application.market.analyzer import get_minute_volume_comparison
    data = await get_minute_volume_comparison()
    return APIResponse.success(data)


# ────────── 概念领涨 ──────────
@router.get("/api/concepts/leaders")
async def concept_leaders(limit: int = 6):
    from application.market.analyzer import get_concept_leaders
    return APIResponse.success(await get_concept_leaders(limit))
