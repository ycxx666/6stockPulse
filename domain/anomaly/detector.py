"""
services/anomaly_service.py
==============================
异动检测引擎

多窗口 (60s / 180s / 300s) 检测:
  - surge:  急涨 (价格/涨幅异常)
  - drop:   急跌
  - volume_break: 放量/缩量突破
  - limit_up_open: 涨停打开
  - sector_resonance: 板块共振

异常分 = 0.45 × |z_return| + 0.25 × log(1+volume_ratio)
       + 0.20 × 板块扩散强度 + 0.10 × 新闻事件加成

所有结果写入 anomaly_event 表，通过 WebSocket 推送 anomaly.created
"""
from __future__ import annotations
import math
import logging
from datetime import datetime, timedelta
from typing import Any

from infrastructure.database import postgres as db
from infrastructure.cache.memory import cache, CacheKeys

logger = logging.getLogger(__name__)

# ────────── 阈值配置 ──────────
WINDOWS = [60, 180, 300]       # 检测窗口 (秒)
Z_SCORE_THRESHOLD = 1.5        # z-score 触发阈值
VOLUME_RATIO_THRESHOLD = 2.0   # 量比触发阈值
MIN_ANOMALY_SCORE = 30         # 最低上报分

# ────────── 多窗口行情缓存 ──────────
# 简单实现: 在内存中保留最近 5 分钟的行情快照
_quote_history: dict[str, list[tuple[float, float, float]]] = {}
# symbol -> [(timestamp, price, volume), ...]


async def record_quote_snapshot(quotes: list[dict]):
    """记录一次行情快照 (由 realtime 或 scheduler 调用)"""
    now = datetime.now().timestamp()
    cutoff = now - max(WINDOWS) - 10  # 只保留窗口期内的数据

    for q in quotes:
        code = q.get("stock_code")
        if not code:
            continue
        price = float(q.get("close") or q.get("price") or 0)
        volume = float(q.get("volume") or 0)
        if price <= 0:
            continue

        if code not in _quote_history:
            _quote_history[code] = []
        _quote_history[code].append((now, price, volume))
        # 清理过期数据
        _quote_history[code] = [
            (t, p, v) for t, p, v in _quote_history[code] if t > cutoff
        ]


async def detect_anomalies() -> list[dict]:
    """
    扫描所有缓存股票，检测异动。
    返回检测到的异动事件列表。
    """
    now = datetime.now()
    anomalies: list[dict] = []

    for code, history in list(_quote_history.items()):
        if len(history) < 3:
            continue

        # 取最新价格
        latest_price = history[-1][1]
        latest_ts = history[-1][0]

        for window in WINDOWS:
            window_start = latest_ts - window
            window_data = [(t, p, v) for t, p, v in history if t >= window_start]

            if len(window_data) < 2:
                continue

            base_price = window_data[0][1]
            if base_price <= 0:
                continue

            # 价格变化
            price_change = (latest_price - base_price) / base_price

            # 量比 (当前窗口成交量 vs 前一日同期)
            volumes = [v for _, _, v in window_data]
            avg_vol = sum(volumes) / len(volumes) if volumes else 0
            prev_vol = _estimate_prev_volume(code, window_start, window)
            volume_ratio = avg_vol / max(prev_vol, 1)

            # z-score (简化: 用窗口内的标准差)
            prices = [p for _, p, _ in window_data]
            mean_p = sum(prices) / len(prices)
            std_p = (sum((p - mean_p) ** 2 for p in prices) / len(prices)) ** 0.5
            z_return = (latest_price - mean_p) / max(std_p, 0.001)

            # 板块扩散 (简化: 如果同板块多只股票同向异动, 强度更高)
            sector_spread = 0.0

            # 新闻事件 (简化: 如果有最近相关新闻, 加成)
            news_bonus = 0.0

            # 综合异常分
            anomaly_score = (
                0.45 * abs(z_return) * 10 +
                0.25 * math.log(1 + abs(volume_ratio - 1) * 5) * 5 +
                0.20 * sector_spread * 10 +
                0.10 * news_bonus * 10
            )
            anomaly_score = min(100, round(anomaly_score, 1))

            if anomaly_score < MIN_ANOMALY_SCORE:
                continue

            # 判定事件类型
            event_type = _classify_event(price_change, volume_ratio, z_return)
            direction = "up" if price_change > 0 else "down"

            anomalies.append({
                "symbol": code,
                "stock_name": None,
                "event_type": event_type,
                "direction": direction,
                "window_seconds": window,
                "change_pct": round(price_change * 100, 4),
                "volume_ratio": round(volume_ratio, 4),
                "z_score": round(z_return, 4),
                "anomaly_score": anomaly_score,
                "confidence": round(min(1.0, anomaly_score / 80), 4),
                "detected_at": now,
            })

    # 去重: 同一只股票只保留最高分的一条
    anomalies = _deduplicate(anomalies)

    # 写入数据库
    if anomalies:
        await _store_anomalies(anomalies)

    # 使缓存失效
    cache.delete(CacheKeys.ANOMALY_ACTIVE)

    return anomalies


def _classify_event(price_change: float, volume_ratio: float, z_score: float) -> str:
    """分类事件类型"""
    if abs(price_change) > 0.05:
        if volume_ratio > VOLUME_RATIO_THRESHOLD:
            return "surge" if price_change > 0 else "drop"
        return "surge" if price_change > 0 else "drop"
    if volume_ratio > VOLUME_RATIO_THRESHOLD:
        return "volume_break"
    if abs(z_score) > Z_SCORE_THRESHOLD:
        return "volatility_spike"
    return "other"


def _estimate_prev_volume(code: str, window_start: float, window: int) -> float:
    """估算前一日同期成交量 (简化: 用历史均值)"""
    history = _quote_history.get(code, [])
    if len(history) < 10:
        return _default_volume(code)
    volumes = [v for _, _, v in history]
    return sum(volumes) / len(volumes)


def _default_volume(code: str) -> float:
    """默认成交量 (避免除以零)"""
    return 1000000.0  # 100万股


def _deduplicate(anomalies: list[dict]) -> list[dict]:
    """同一只股票只保留最高分"""
    best: dict[str, dict] = {}
    for a in anomalies:
        code = a["symbol"]
        if code not in best or a["anomaly_score"] > best[code]["anomaly_score"]:
            best[code] = a
    return sorted(best.values(), key=lambda x: x["anomaly_score"], reverse=True)


async def _store_anomalies(anomalies: list[dict]):
    """批量写入 anomaly_event 表"""
    # 先标记旧事件为 expired
    await db.execute("""
        UPDATE anomaly_event SET status = 'expired', expired_at = NOW()
        WHERE status = 'active'
    """)

    for a in anomalies[:20]:  # 最多保留 20 条活跃异动
        await db.execute("""
            INSERT INTO anomaly_event
                (symbol, event_type, direction, window_seconds, change_pct,
                 volume_ratio, z_score, anomaly_score, confidence, status, detected_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'active', $10)
        """,
            a["symbol"], a["event_type"], a["direction"], a["window_seconds"],
            a["change_pct"], a["volume_ratio"], a["z_score"],
            a["anomaly_score"], a["confidence"], a["detected_at"],
        )


async def get_active_anomalies(limit: int = 20) -> list[dict]:
    """获取活跃异动列表"""
    rows = await db.fetch_all("""
        SELECT id, symbol, stock_name, event_type, direction,
               change_pct, volume_ratio, anomaly_score, confidence,
               detected_at::text AS detected_at
        FROM anomaly_event
        WHERE status = 'active'
        ORDER BY anomaly_score DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


# ────────── 板块共振检测 ──────────
async def detect_sector_resonance() -> list[dict]:
    """
    板块共振: 同板块多只股票同时异动
    扫描 anomaly_event 中活跃的股票，按 sector_code 分组
    如果同一板块 >= 3 只股票异动，则产生板块共振信号
    """
    rows = await db.fetch_all("""
        SELECT a.symbol, a.event_type, a.direction, s.industry, s.sector_code
        FROM anomaly_event a
        LEFT JOIN stocks s ON s.stock_code = a.symbol
        WHERE a.status = 'active'
          AND a.detected_at > NOW() - INTERVAL '5 minutes'
    """)

    # 按板块分组
    from collections import Counter
    sector_groups: dict[str, list[dict]] = {}
    for r in rows:
        sc = (r.get("sector_code") or r.get("industry") or "unknown")
        sector_groups.setdefault(sc, []).append(dict(r))

    resonances = []
    for sc, members in sector_groups.items():
        if len(members) >= 3:
            directions = Counter(m["direction"] for m in members)
            dominant_dir = directions.most_common(1)[0][0]
            resonances.append({
                "sector_code": sc,
                "member_count": len(members),
                "dominant_direction": dominant_dir,
                "members": [m["symbol"] for m in members[:5]],
                "confidence": min(1.0, len(members) / 5),
            })

    return resonances
