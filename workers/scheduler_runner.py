"""
workers/scheduler_runner.py
=============================
独立 Worker 进程：定时调度 + 实时行情采集

与 api.server 分离, 避免 uvicorn workers>1 时
多个 Scheduler 同时运行导致重复写入。
"""
from __future__ import annotations
import asyncio
import logging
import os
import signal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import REALTIME
from infrastructure.database import postgres as db
from db import database as db_service  # services/* 层使用的连接池 (与 infrastructure 池并存)
from scheduler import scheduler, register_all
from application.realtime.service import realtime

# ────────── 日志 ──────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "worker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("6stockPulse.worker")


async def main():
    """Worker 主入口"""
    logger.info("=" * 50)
    logger.info("6stockPulse Worker 启动中...")

    # 初始化 DB（两套池：infrastructure 供 worker/调度，db 供 services/* 层）
    await db.init_pool()
    await db_service.init_pool()
    logger.info("Database pools ready")

    # 注册并启动调度器
    register_all(scheduler)
    scheduler.start()
    logger.info("Scheduler started (%d jobs)", len(scheduler.scheduler.get_jobs()))

    # 启动实时行情采集
    if REALTIME.enabled:
        if not os.environ.get("NO_REALTIME"):
            await realtime.start()
            logger.info("RealtimeService started (interval=%.1fs)", REALTIME.poll_interval)
        else:
            logger.info("RealtimeService disabled (NO_REALTIME=1)")

    logger.info("Worker running. Press Ctrl+C to stop.")
    logger.info("=" * 50)

    # 保持运行直到收到停止信号
    stop_event = asyncio.Event()

    def _shutdown(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    await stop_event.wait()

    # 优雅关闭
    logger.info("Worker shutting down...")
    await realtime.stop()
    scheduler.shutdown()
    await db_service.close_pool()
    await db.close_pool()
    logger.info("Worker stopped.")


if __name__ == "__main__":
    asyncio.run(main())
