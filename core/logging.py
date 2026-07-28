"""
core/logging.py — 统一日志配置
"""
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def setup_logging(name: str = "6stockPulse", level: int = logging.INFO):
    """配置日志：文件 + 控制台输出"""
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_dir / f"{name.lower()}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger(name)
