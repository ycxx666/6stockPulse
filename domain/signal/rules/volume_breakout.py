"""
domain/signal/rules/volume_breakout.py — 量价突破信号规则
"""
from domain.signal.models import Signal


async def evaluate(context: dict) -> list[Signal]:
    """检测量价突破信号"""
    signals: list[Signal] = []
    anomalies = context.get("anomalies", [])
    for a in anomalies:
        if a.get("event_type") != "volume_break":
            continue
        signals.append(Signal(
            symbol=a.get("symbol", ""),
            signal_type="opportunity",
            title=f"{a.get('stock_name', '')} 放量突破",
            summary=f"成交量异常放大, 量比{a.get('volume_ratio', 0):.1f}",
            evidence=[f"量比: {a.get('volume_ratio', 0):.1f}"],
            direction="up",
            score=min(100, int(a.get("anomaly_score", 50))),
            confidence=a.get("confidence", 0.5),
        ))
    return signals
