"""
domain/signal/engine.py — 兼容转发层

实际实现已统一到 services/signal_service.py。
本文件仅做 re-export，避免两份重复实现（改一处不生效）。
"""
from services.signal_service import generate_signals  # noqa: F401
