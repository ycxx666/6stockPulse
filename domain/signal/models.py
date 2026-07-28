"""
domain/signal/models.py — 信号数据模型
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Signal:
    """统一信号结构"""
    symbol: str
    signal_type: str          # opportunity | risk
    title: str
    summary: str = ""
    evidence: list[str] = field(default_factory=list)
    direction: str | None = None
    score: int = 0
    confidence: float = 0.5
    severity: str = "info"    # info | warning | critical
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "title": self.title,
            "summary": self.summary,
            "evidence": self.evidence,
            "direction": self.direction,
            "score": self.score,
            "confidence": self.confidence,
            "severity": self.severity,
            "generated_at": self.generated_at,
        }
