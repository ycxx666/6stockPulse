"""
domain/sentiment/analyzer.py — 兼容转发层

实际实现已统一到 services/sentiment_service.py。
本文件仅做 re-export，避免两份重复实现（改一处不生效）。
"""
from services.sentiment_service import (  # noqa: F401
    compute_all,
    compute_ashare_sentiment,
    compute_global_sentiment,
)
