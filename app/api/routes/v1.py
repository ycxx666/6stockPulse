"""
api/routes/v1.py — 6Signal 兼容适配层
=====================================
把 6stockPulse 的实时数据转成 6signal 看板期望的格式：
  GET /api/v1/dashboard/global   全球市场（风险判断 + 指数）
  GET /api/v1/dashboard/ashare   A股（宽度 + 板块 + 行情 + 领涨 + 数据健康）
  GET /api/v1/dashboard/health   数据源健康
  GET /api/v1/news               新闻
  GET /api/v1/collection         采集情况
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from infrastructure.database import postgres as db

router = APIRouter(tags=["v1"])


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


async def _latest_breadth() -> dict:
    row = await db.fetch_one("""
        SELECT up_count, down_count, flat_count, limit_up, limit_down,
               big_up, big_down, total, sentiment_score, snapshot_time
        FROM market_breadth ORDER BY snapshot_time DESC LIMIT 1
    """)
    return dict(row) if row else {}


async def _sectors(limit: int = 60) -> list[dict]:
    rows = await db.fetch_all("""
        SELECT sector_code, sector_name, sector_type, change_pct,
               main_net_inflow, main_net_ratio, turnover_rate,
               leader_stock, leader_name
        FROM v_sector_top ORDER BY rnk LIMIT $1
    """, limit)
    return [{
        "code": r["sector_code"],
        "name": r["sector_name"],
        "kind": r["sector_type"] or "concept",
        "change_pct": _f(r["change_pct"]),
        "main_net": str(r["main_net_inflow"] or 0),
        "main_ratio": _f(r["main_net_ratio"]),
        "turnover_rate": _f(r["turnover_rate"]),
        "leader_stock": r["leader_stock"],
        "leader_name": r["leader_name"],
    } for r in rows]


async def _quotes(limit: int = 100) -> list[dict]:
    rows = await db.fetch_all("""
        SELECT lq.stock_code, s.stock_name, lq.close, lq.change_pct,
               lq.volume, lq.amount, lq.trade_time
        FROM latest_quotes lq
        LEFT JOIN stocks s ON s.stock_code = lq.stock_code
        ORDER BY lq.change_pct DESC NULLS LAST
        LIMIT $1
    """, limit)
    return [{
        "code": r["stock_code"],
        "name": r["stock_name"] or r["stock_code"],
        "price": str(r["close"] or ""),
        "change_pct": _f(r["change_pct"]),
        "volume": str(r["volume"] or ""),
        "amount": str(r["amount"] or ""),
        "ts": _iso(r["trade_time"]),
    } for r in rows]


async def _news(limit: int = 100) -> list[dict]:
    rows = await db.fetch_all("""
        SELECT news_id, title, summary, content, source, source_url,
               publish_time, sentiment, importance, stocks
        FROM news ORDER BY publish_time DESC LIMIT $1
    """, limit)
    return [{
        "id": r["news_id"],
        "title": r["title"],
        "body": r["summary"] or (r["content"] or "")[:200],
        "url": r["source_url"],
        "published_at": _iso(r["publish_time"]),
        "importance": r["importance"] or 0,
        "sentiment": _f(r["sentiment"]),
        "source": r["source"] or "news",
        "affected_assets": list(r["stocks"] or []),
    } for r in rows]


async def _health() -> list[dict]:
    checks = [
        ("ashare", "market_breadth", "snapshot_time"),
        ("news", "news", "publish_time"),
        ("global_market", "global_indices", "trade_time"),
        ("quotes", "stock_prices", "trade_time"),
        ("sector", "sector_fund_flow", "trade_time"),
    ]
    out = []
    now = datetime.now(timezone.utc)
    for name, table, col in checks:
        try:
            v = await db.fetch_val(f"SELECT max({col}) FROM {table}")
        except Exception:
            v = None
        age = None
        if v is not None:
            try:
                dt = v if getattr(v, "tzinfo", None) else v.replace(tzinfo=timezone.utc)
                age = int((now - dt).total_seconds())
            except Exception:
                age = None
        out.append({
            "source": name,
            "last_event": _iso(v),
            "latency_seconds": age,
            "status": "LIVE" if (age is not None and age < 900) else "STALE",
        })
    return out


@router.get("/api/v1/dashboard/health")
async def v1_health():
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "sources": await _health(),
    }


@router.get("/api/v1/dashboard/ashare")
async def v1_dashboard_ashare():
    breadth = await _latest_breadth()
    sectors = await _sectors(60)
    quotes = await _quotes(100)
    leaders = sorted(quotes, key=lambda x: x["change_pct"] or 0, reverse=True)[:10]
    health = await _health()
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "coverage": {"quotes": len(quotes), "sectors": len(sectors)},
        "indices": [],
        "breadth": {
            "snapshot_time": _iso(breadth.get("snapshot_time")),
            "up_count": breadth.get("up_count", 0),
            "down_count": breadth.get("down_count", 0),
            "flat_count": breadth.get("flat_count", 0),
            "limit_up": breadth.get("limit_up", 0),
            "limit_down": breadth.get("limit_down", 0),
            "big_up": breadth.get("big_up", 0),
            "big_down": breadth.get("big_down", 0),
            "total": breadth.get("total", 0),
            "score": breadth.get("sentiment_score", 50),
        },
        "sectors": sectors,
        "quotes": quotes,
        "leaders": leaders,
        "lhb": [],
        "margin": {},
        "health": health,
    }


@router.get("/api/v1/dashboard/global")
async def v1_dashboard_global():
    sent = await db.fetch_one("""
        SELECT score, label, factors, snapshot_time
        FROM sentiment_snapshot WHERE scope = 'global'
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    score = int(sent["score"]) if sent else 50
    label = "Risk-On" if score >= 60 else ("Risk-Off" if score <= 40 else "Neutral")
    rows = await db.fetch_all("""
        SELECT symbol, name, market, price, change_pct, quote_time
        FROM market_quote_snapshot
        WHERE asset_type IN ('index', 'commodity', 'forex')
        ORDER BY asset_type, symbol
    """)
    markets = [{
        "code": r["symbol"],
        "name": r["name"],
        "price": str(r["price"] or ""),
        "change_pct": _f(r["change_pct"]),
        "ts": _iso(r["quote_time"]),
        "market": r["market"],
    } for r in rows]
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "coverage": {"expected": len(rows), "available": len(rows), "pct": 100.0},
        "regime": {"label": label, "score": score, "coverage": 1.0, "factors": []},
        "risk": {
            "label": "NORMAL" if 40 <= score <= 70 else "CAUTION",
            "score": 100 - score,
            "reasons": [f"全球情绪 {score} 分（{label}）"],
        },
        "markets": markets,
    }


@router.get("/api/v1/news")
async def v1_news(start: str | None = None, end: str | None = None, limit: int = 100):
    return await _news(limit)


@router.get("/api/v1/collection")
async def v1_collection(limit: int = 50):
    rows = await db.fetch_all("""
        SELECT crawler_name, started_at, finished_at, success_count,
               skip_count, fail_count, error_msg
        FROM crawl_log ORDER BY started_at DESC LIMIT $1
    """, limit)
    return [{
        "crawler": r["crawler_name"],
        "started_at": _iso(r["started_at"]),
        "finished_at": _iso(r["finished_at"]),
        "success": r["success_count"] or 0,
        "skip": r["skip_count"] or 0,
        "fail": r["fail_count"] or 0,
        "error": r["error_msg"],
    } for r in rows]


@router.get("/api/v1/collection/status")
async def v1_collection_status():
    """采集情况统计（6signal collection.html 期望的格式）。"""
    now = datetime.now(timezone.utc)

    async def _stat(table: str, tcol: str):
        try:
            count = await db.fetch_val(f"SELECT count(*) FROM {table}")
            last = await db.fetch_val(f"SELECT max({tcol}) FROM {table}")
            first = await db.fetch_val(f"SELECT min({tcol}) FROM {table}")
        except Exception:
            count, last, first = None, None, None
        return count, last, first

    specs = [
        ("ashare_quotes", "A股行情", "行情快照", "stock_prices", "trade_time"),
        ("global_quotes", "全球指数", "行情快照", "global_indices", "trade_time"),
        ("breadth", "市场宽度", "高频服务库", "market_breadth", "snapshot_time"),
        ("sector_flow", "板块资金流", "高频服务库", "sector_fund_flow", "trade_time"),
        ("news", "新闻资讯", "新闻订阅", "news", "publish_time"),
        ("crawl_log", "采集日志", "统计检查", "crawl_log", "started_at"),
    ]
    rows = []
    for rid, name, category, table, tcol in specs:
        count, last, first = await _stat(table, tcol)
        age = None
        if last is not None:
            try:
                dt = last if getattr(last, "tzinfo", None) else last.replace(tzinfo=timezone.utc)
                age = int((now - dt).total_seconds())
            except Exception:
                age = None
        status = "LIVE" if (age is not None and age < 900) else "STALE"
        rows.append({
            "id": rid,
            "name": name,
            "category": category,
            "source": table,
            "status": status,
            "status_label": "实时" if status == "LIVE" else "过期",
            "count": count,
            "count_unit": "条",
            "coverage_count": None,
            "coverage_unit": "条",
            "last_ingested": _iso(last),
            "last_checked": _iso(now),
            "coverage_start": _iso(first),
            "coverage_end": _iso(last),
            "size_bytes": None,
            "size_shared": False,
            "location": f"PG.{table}",
            "note": "6stockPulse 实时库",
            "needs_attention": status != "LIVE",
        })

    db_size = None
    table_count = None
    try:
        db_size = await db.fetch_val("SELECT pg_database_size('stockpulse')")
        table_count = await db.fetch_val(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
    except Exception:
        pass

    return {
        "as_of": now.isoformat(),
        "summary": {
            "datasets": len(rows),
            "updating": sum(1 for r in rows if r["status"] == "LIVE"),
            "raw_bytes": None,
            "database_bytes": db_size,
            "database_tables": table_count,
            "attention": sum(1 for r in rows if r["needs_attention"]),
        },
        "disk": {"used_percent": 0.0, "free_bytes": None, "total_bytes": None},
        "partial": False,
        "rows": rows,
    }
