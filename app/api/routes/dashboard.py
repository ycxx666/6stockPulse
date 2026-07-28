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
