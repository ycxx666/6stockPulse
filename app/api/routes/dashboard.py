"""
api/routes/dashboard.py — 首屏聚合 + 系统健康
"""
from __future__ import annotations
from datetime import datetime

from fastapi import APIRouter

from app.api.schemas import APIResponse
from application.realtime.service import realtime
from application.dashboard.aggregator import get_snapshot, market_session
from infrastructure.cache.memory import cache, CacheKeys
from infrastructure.database import postgres as db

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard/snapshot")
async def dashboard_snapshot(refresh: bool = False):
    """一站式仪表盘数据 (首屏单次拉取)"""
    if refresh:
        cache.delete(CacheKeys.DASH_SNAPSHOT)
    data = await get_snapshot(use_cache=not refresh)
    return data


@router.get("/api/system/health")
async def system_health():
    """系统健康检查 (扩展版)"""
    db_status = await db.health_check()
    return APIResponse.success({
        "ok": db_status.get("ok", False),
        "db": db_status,
        "realtime": {
            "running": realtime.running,
            "subscribers": len(realtime._subscribers),
            "last_refresh": realtime._last_refresh.isoformat() if realtime._last_refresh else None,
            "quotes_cached": len(realtime._latest_quotes),
        },
        "cache": cache.stats(),
        "market_session": market_session(),
        "server_time": datetime.now().isoformat(),
    })


# 各数据源“最新时间”检查表: (key, label, 查询 SQL)
_FRESHNESS_CHECKS = [
    ("quotes",     "A股行情",  "SELECT max(trade_time)   FROM stock_prices"),
    ("global",     "全球指数", "SELECT max(trade_time)   FROM global_indices"),
    ("sector",     "板块资金", "SELECT max(trade_time)   FROM sector_fund_flow"),
    ("news",       "新闻资讯", "SELECT max(publish_time) FROM news"),
    ("breadth",    "市场宽度", "SELECT max(snapshot_time) FROM market_breadth"),
    ("sentiment",  "情绪快照", "SELECT max(snapshot_time) FROM sentiment_snapshot"),
    ("anomaly",    "异动",     "SELECT max(detected_at)  FROM anomaly_event"),
    ("signal",     "信号",     "SELECT max(generated_at) FROM signal_snapshot"),
    ("prediction", "次日预测", "SELECT max(predicted_at) FROM prediction_result"),
]


@router.get("/api/system/freshness")
async def system_freshness(max_age_minutes: int = 30):
    """各数据源最新数据时间与“年龄”, 供前端显示数据新鲜度/陈旧标记。

    stale 判定: 数据年龄 > max_age_minutes; 数据源为空时 stale=True 且 last_time=None。
    """
    from datetime import timezone
    now = datetime.now(timezone.utc)
    out = []
    for key, label, sql in _FRESHNESS_CHECKS:
        try:
            v = await db.fetch_val(sql)
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
            "key": key,
            "label": label,
            "last_time": v.isoformat() if v is not None else None,
            "age_seconds": age,
            "stale": (age is None) or (age > max_age_minutes * 60),
        })
    ages = [s["age_seconds"] for s in out if s["age_seconds"] is not None]
    return APIResponse.success({
        "generated_at": datetime.now().isoformat(),
        "max_age_minutes": max_age_minutes,
        "oldest_age_seconds": (max(ages) if ages else None),
        "any_stale": any(s["stale"] for s in out),
        "sources": out,
    })
