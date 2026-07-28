"""
domain/anomaly — 异动检测引擎
==============================
多窗口价格/成交量异动检测 + 板块共振
"""
from domain.anomaly.detector import (
    record_quote_snapshot,
    detect_anomalies,
    get_active_anomalies,
    detect_sector_resonance,
)
