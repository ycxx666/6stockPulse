"""
6stockPulse · FastAPI 后端 (v4 重构)
======================================
  - REST API: 路由拆分到 api/routes/
  - WebSocket: 实时行情推送 (主题化)
  - CORS: 允许所有来源 (开发用)
"""
from __future__ import annotations
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from infrastructure.database import postgres as db
from core.config import API
from application.realtime.service import realtime
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.market import router as market_router
from app.api.routes.news import router as news_router
from app.api.routes.sentiment import router as sentiment_router
from app.api.routes.analysis import router as analysis_router
from app.api.routes.hotspots import router as hotspots_router

logger = logging.getLogger(__name__)


# ────────── 应用生命周期 ──────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # API 服务仅负责 DB 连接池（调度 & 实时采集由 worker 进程独立管理）
    await db.init_pool()
    logger.info("6stockPulse v4 API started on %s:%d", API.host, API.port)
    yield
    await db.close_pool()


app = FastAPI(title=API.title, version=API.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=API.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 注册路由 ──
app.include_router(dashboard_router)
app.include_router(market_router)
app.include_router(news_router)
app.include_router(sentiment_router)
app.include_router(analysis_router)
app.include_router(hotspots_router)

# ── 静态文件 (前端) ──
from pathlib import Path
_frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/frontend", StaticFiles(directory=str(_frontend_dir)), name="frontend")


from app.api._utils import _jsonable


# ────────── 根 / 重定向到前端 ──────────
@app.get("/")
async def root():
    return RedirectResponse(url="/frontend/index.html")


@app.get("/health")
async def health():
    db_status = await db.health_check()
    return {
        "ok": db_status.get("ok", False),
        "db": db_status,
        "realtime": {
            "running": realtime.running,
            "subscribers": len(realtime._subscribers),
            "last_refresh": realtime._last_refresh.isoformat() if realtime._last_refresh else None,
            "quotes_cached": len(realtime._latest_quotes),
        },
    }


# ────────── 向下兼容: 旧版 /api/dashboard ──────────
@app.get("/api/dashboard")
async def dashboard_legacy():
    """旧版仪表盘 (向下兼容)"""
    from application.dashboard.aggregator import get_snapshot
    return await get_snapshot(use_cache=False)


# ────────── WebSocket: 实时行情推送 (主题化) ──────────
@app.websocket("/ws/realtime")
async def ws_realtime(ws: WebSocket):
    await ws.accept()
    q = await realtime.subscribe()
    seq = 0
    try:
        while True:
            receive_task = asyncio.create_task(ws.receive_text())
            send_task = asyncio.create_task(q.get())
            done, pending = await asyncio.wait(
                {receive_task, send_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in done:
                if t is send_task:
                    msg = t.result()
                    seq += 1
                    # 主题化推送
                    msg.setdefault("seq", seq)
                    await ws.send_text(json.dumps(_jsonable(msg), ensure_ascii=False))
                elif t is receive_task:
                    try:
                        text = t.result()
                        data = json.loads(text)
                        action = data.get("action", "")
                        if action == "ping":
                            await ws.send_text(json.dumps({"type": "pong", "ts": datetime.now().isoformat(), "seq": seq}))
                        elif action == "subscribe":
                            codes = data.get("codes", [])
                            if codes:
                                logger.debug("WS client subscribed to %d codes", len(codes))
                    except Exception:
                        logger.debug("WS client message parse error", exc_info=True)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
    finally:
        realtime.unsubscribe(q)


# ────────── 入口 ──────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host=API.host, port=API.port, reload=False)
