"""
6stockPulse · Pydantic 响应模型
=================================
统一响应包络 + 各模块数据模型
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


# ────────── 统一响应包络 ──────────
class APIResponse(BaseModel):
    """所有 REST 接口的统一外层"""
    ok: bool = True
    data: Any = None
    meta: dict = Field(default_factory=dict)

    @classmethod
    def success(cls, data: Any = None, **meta) -> dict:
        return {"ok": True, "data": data, "meta": meta}

    @classmethod
    def error(cls, message: str, code: int = 500) -> dict:
        return {"ok": False, "error": message, "code": code}

    @classmethod
    def not_found(cls, message: str = "Not found") -> dict:
        return cls.error(message, code=404)


# ────────── Ticker (全球行情滚动带) ──────────
class TickerItem(BaseModel):
    symbol: str
    name: str
    asset_type: str = "index"       # index / commodity / forex
    market: str = "US"
    price: float = 0.0
    prev_close: float | None = None
    change: float = 0.0
    change_pct: float = 0.0
    quote_time: str | None = None
    source: str = "eastmoney"
    is_stale: bool = False


# ────────── 市场宽度 ──────────
class MarketBreadth(BaseModel):
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    limit_up: int = 0
    limit_down: int = 0
    big_up: int = 0
    big_down: int = 0
    total: int = 0
    sentiment_score: int = 50
    snapshot_time: str | None = None


# ────────── 异动事件 ──────────
class AnomalyEvent(BaseModel):
    id: int | None = None
    symbol: str
    stock_name: str | None = None
    event_type: str                  # surge / drop / volume_break / limit_up_open
    direction: str | None = None
    change_pct: float | None = None
    volume_ratio: float | None = None
    anomaly_score: float | None = None
    confidence: float = 0.5
    detected_at: str | None = None


# ────────── 板块资金流 ──────────
class SectorFlowItem(BaseModel):
    sector_code: str
    sector_name: str
    sector_type: str = "concept"     # concept / industry
    change_pct: float = 0.0
    main_net_inflow: float = 0.0     # 元
    main_net_ratio: float | None = None
    turnover_rate: float | None = None
    leader_stock: str | None = None
    leader_name: str | None = None
    leader_change_pct: float | None = None
    bar_pct: float = 0.0             # 前端 bar 宽度 0-100


# ────────── 新闻 ──────────
class NewsItem(BaseModel):
    news_id: str
    title: str
    content: str | None = None
    source: str | None = None
    publish_time: str | None = None
    category: str | None = None
    sentiment: float | None = None
    importance: int = 0
    stocks: list[str] | None = None
    keywords: list[str] | None = None


# ────────── 情绪 ──────────
class SentimentSnapshot(BaseModel):
    scope: str                      # global / ashare
    score: int = 50
    label: str = "中性"
    long_count: int = 0
    neutral_count: int = 0
    short_count: int = 0
    factors: dict = Field(default_factory=dict)
    snapshot_time: str | None = None


# ────────── 信号 ──────────
class SignalItem(BaseModel):
    id: int | None = None
    signal_type: str                # opportunity / risk
    title: str
    summary: str | None = None
    evidence: list = Field(default_factory=list)
    direction: str | None = None
    confidence: float = 0.5
    severity: str = "info"
    generated_at: str | None = None


# ────────── 概念领涨 ──────────
class ConceptLeader(BaseModel):
    sector_code: str
    sector_name: str
    change_pct: float = 0.0
    main_net_inflow: float = 0.0
    consecutive_days: int = 1
    news_count: int = 0
    leader_stock: str | None = None
    leader_name: str | None = None
    risk_tags: list[str] = Field(default_factory=list)


# ────────── 次日预测 ──────────
class PredictionResult(BaseModel):
    horizon: str = "next_session"
    up_probability: float = 0.33
    flat_probability: float = 0.34
    down_probability: float = 0.33
    confidence: float = 0.5
    explanation: list[str] = Field(default_factory=list)
    model_version: str = "rule-baseline-v1"
    generated_at: str | None = None


# ────────── 连板梯队 ──────────
class LadderItem(BaseModel):
    label: str                      # "2板" / "3板" / "4板" / "5板+"
    count: int = 0
    bar_pct: float = 0.0            # 0-100


# ────────── 首屏聚合 (Dashboard Snapshot) ──────────
class DashboardSnapshot(BaseModel):
    server_time: str
    market_session: str = "unknown"  # trading / lunch_break / closed
    freshness_ms: int = 0
    data: dict = Field(default_factory=dict)

    @classmethod
    def empty(cls) -> dict:
        return {
            "server_time": datetime.now().isoformat(),
            "market_session": "unknown",
            "freshness_ms": 0,
            "data": {
                "tickers": [],
                "breadth": {},
                "anomalies": [],
                "sector_flow": [],
                "stats": [],
                "ladder": [],
                "signals": [],
                "news": [],
                "sentiment": {"global": {}, "ashare": {}},
                "prediction": {},
                "concept_leaders": [],
            }
        }
