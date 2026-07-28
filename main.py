"""
6stockPulse · 主入口
======================
启动 FastAPI 接口服务（调度 & 实时采集由 worker 进程独立管理）
"""
from __future__ import annotations
import sys
import logging
import argparse
from pathlib import Path

# 把项目根加入 path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import API

# ────────── 日志配置 ──────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "6stockpulse.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("6stockPulse")


def main():
    parser = argparse.ArgumentParser(description="6stockPulse · 全球股市实时数据看板")
    parser.add_argument("--host", default=API.host, help="绑定地址")
    parser.add_argument("--port", type=int, default=API.port, help="端口")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    import uvicorn
    from app.api.server import app

    uvicorn.run(
        app, host=args.host, port=args.port,
        reload=args.reload, log_level="info"
    )


if __name__ == "__main__":
    main()
