"""
domain/events/events.py — 领域事件定义
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StockPriceUpdated:
    symbol: str
    price: float
    change_pct: float
    volume: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AnomalyDetected:
    symbol: str
    event_type: str
    anomaly_score: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SignalGenerated:
    signal: dict
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
