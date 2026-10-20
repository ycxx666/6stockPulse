"""
api/routes/plugin.py — 智能体插件网关
========================================
把 6stockPulse 的量化研判能力聚合为 6 个语义化工具端点，
供校级智能体平台(aihome)的「插件 / OpenAPI」调用。

6 个工具:
  1. GET /plugin/sentiment    市场情绪 (A股 + 全球)
  2. GET /plugin/breadth      市场宽度
  3. GET /plugin/signals      机会 / 风险信号
  4. GET /plugin/prediction   次日方向研判
  5. GET /plugin/sector-flow  板块资金流
  6. GET /plugin/stock        个股概览 (代码或名称)

设计原则:
  - 只读、聚合、字段对 LLM 友好（结论 + 依据 + 快照时间）
  - 不暴露内部 30+ 细碎端点，也不暴露 POST /api/crawl/* 写接口
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from app.api.schemas import APIResponse
from app.api._utils import _jsonable
from infrastructure.database import postgres as db

router = APIRouter(tags=["plugin"])


async def _data_meta() -> dict:
    """数据新鲜度：最后更新时间 + 是否交易时段，标注非交易时段停更为正常。"""
    from application.realtime.service import in_trading_hours
    row = await db.fetch_one(
        "SELECT max(trade_time)::text AS data_updated_at "
        "FROM stock_prices WHERE trade_date = CURRENT_DATE")
    d = dict(row) if row else {}
    return {
        "data_updated_at": d.get("data_updated_at"),
        "is_trading_hours": in_trading_hours(),
        "note": "非交易时段数据源返回固定收盘快照，数值不变化属正常",
    }


def _row(row, default: dict | None = None) -> dict:
    """asyncpg Record → dict，容错解析可能的 json 字符串字段"""
    if not row:
        return default or {}
    d = dict(row)
    for k in ("factors", "evidence", "explanation"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


# ────────── 1. 市场情绪 ──────────
@router.get("/plugin/sentiment")
async def plugin_sentiment():
    """A 股与全球市场情绪分（0-100）及多因子贡献。"""
    ashare = await db.fetch_one("""
        SELECT scope, score, label, factors, snapshot_time::text AS snapshot_time
        FROM sentiment_snapshot WHERE scope = 'ashare'
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    global_ = await db.fetch_one("""
        SELECT scope, score, label, factors, snapshot_time::text AS snapshot_time
        FROM sentiment_snapshot WHERE scope = 'global'
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    ashare_data = _row(ashare, {"scope": "ashare", "score": 50, "label": "中性"})
    global_data = _row(global_, {"scope": "global", "score": 50, "label": "中性"})
    ashare_data["score_type"] = "composite"  # 8 因子加权的综合情绪分
    global_data["score_type"] = "composite"
    return APIResponse.success({
        "ashare": ashare_data, "global": global_data,
        "meta": await _data_meta(),
    })


# ────────── 2. 市场宽度 ──────────
@router.get("/plugin/breadth")
async def plugin_breadth():
    """市场宽度：涨跌家数、涨跌停、连板梯队与赚钱效应。"""
    from application.market.analyzer import (
        get_latest_breadth, get_limit_up_ladder, get_enhanced_profit_effect,
    )
    breadth = await get_latest_breadth()
    ladder = await get_limit_up_ladder()
    heat = await get_enhanced_profit_effect()
    if isinstance(breadth, dict):
        breadth["score_type"] = "breadth"  # 仅由涨跌家数计算的宽度分，与综合情绪分不同口径
    return APIResponse.success({
        "breadth": breadth,
        "limit_up_ladder": ladder,
        "profit_effect": heat,
        "meta": await _data_meta(),
    })


# ────────── 3. 机会 / 风险信号 ──────────
@router.get("/plugin/signals")
async def plugin_signals(direction: str | None = None, limit: int = 10):
    """结构化机会(opportunity)/风险(risk)信号，含证据链与置信度。"""
    sql = """
        SELECT signal_type, title, summary, evidence, direction,
               confidence, severity, generated_at::text AS generated_at
        FROM signal_snapshot
        WHERE (expires_at > NOW() OR expires_at IS NULL)
    """
    params: list = []
    if direction in ("opportunity", "risk"):
        sql += f" AND signal_type = ${len(params)+1}"
        params.append(direction)
    sql += f" ORDER BY confidence DESC, generated_at DESC LIMIT ${len(params)+1}"
    params.append(limit)
    rows = await db.fetch_all(sql, *params)
    out = []
    for r in rows:
        d = _row(r)
        ev = d.get("evidence")
        if isinstance(ev, list):
            # 统一转成字符串数组，兼容平台对 evidence 为 string[] 的校验
            d["evidence"] = [
                f"{e.get('type', '')}: {e.get('value', '')}" if isinstance(e, dict) else str(e)
                for e in ev
            ]
        out.append(d)
    return APIResponse.success(out)


# ────────── 4. 次日方向研判 ──────────
@router.get("/plugin/prediction")
async def plugin_prediction():
    """次日大盘涨/平/跌概率与置信度（规则基线），附因子解释。"""
    row = await db.fetch_one("""
        SELECT horizon, up_probability, flat_probability, down_probability,
               confidence, explanation, model_version,
               predicted_at::text AS generated_at
        FROM prediction_result ORDER BY predicted_at DESC LIMIT 1
    """)
    if not row:
        return APIResponse.success({
            "horizon": "next_session",
            "up_probability": 0.33, "flat_probability": 0.34, "down_probability": 0.33,
            "confidence": 0.5, "model_version": "rule-baseline-v1",
            "note": "暂无预测结果",
        })
    data = _row(row)
    # 附当前宽度快照，便于判断预测解释是否陈旧
    breadth = await db.fetch_one("""
        SELECT up_count, down_count, limit_up, limit_down,
               snapshot_time::text AS based_on
        FROM market_breadth ORDER BY snapshot_time DESC LIMIT 1
    """)
    if breadth:
        data["current_breadth"] = {
            "based_on": breadth.get("based_on"),
            "up_count": breadth.get("up_count"),
            "down_count": breadth.get("down_count"),
            "limit_up": breadth.get("limit_up"),
            "limit_down": breadth.get("limit_down"),
        }
    return APIResponse.success(data)


# ────────── 5. 板块资金流 ──────────
@router.get("/plugin/sector-flow")
async def plugin_sector_flow(type: str = "concept", limit: int = 10):
    """概念/行业板块资金流向与领涨板块。type: concept | industry"""
    if type not in ("concept", "industry"):
        type = "concept"  # 容错：非法值默认按概念板块处理，避免模型误传导致 400
    rows = await db.fetch_all("""
        SELECT sector_code, sector_name, sector_type, change_pct,
               main_net_inflow, main_net_ratio, turnover_rate
        FROM v_sector_top
        WHERE sector_type = $1
        ORDER BY rnk
        LIMIT $2
    """, type, limit)
    return APIResponse.success([_row(r) for r in rows])


# ────────── 6. 个股概览 ──────────
@router.get("/plugin/stock")
async def plugin_stock(symbol: str):
    """个股实时行情 + 分时/日K概览 + 所属板块。symbol 支持代码或名称。"""
    q = symbol.strip()
    stock = await db.fetch_one(
        "SELECT * FROM stocks WHERE stock_code = $1 AND is_active = TRUE", q)
    if not stock:
        stock = await db.fetch_one("""
            SELECT * FROM stocks
            WHERE stock_name ILIKE $1 AND is_active = TRUE
            ORDER BY stock_code LIMIT 1
        """, f"%{q}%")
    if not stock:
        raise HTTPException(404, f"未找到股票: {symbol}")

    code = stock["stock_code"]
    quote = await db.fetch_one("SELECT * FROM latest_quotes WHERE stock_code = $1", code)
    kline = await db.fetch_all("""
        SELECT DISTINCT ON (trade_date)
               trade_date, open, high, low, close, volume, amount
        FROM stock_prices WHERE stock_code = $1
        ORDER BY trade_date DESC, trade_time DESC
        LIMIT 5
    """, code)
    return APIResponse.success({
        "stock": _jsonable(stock),
        "quote": _jsonable(quote),
        "recent_kline": _jsonable(list(reversed(kline))),
    })
