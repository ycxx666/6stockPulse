"""
api/routes/sentiment.py — 情绪 / 异动 / 信号 / 预测
"""
from __future__ import annotations

from fastapi import APIRouter

from api.schemas import APIResponse
from db import database as db

router = APIRouter(tags=["sentiment"])


# ────────── 情绪 ──────────
@router.get("/api/sentiment/global")
async def global_sentiment():
    row = await db.fetch_one("""
        SELECT scope, score, label, long_count, neutral_count, short_count,
               factors, snapshot_time::text AS snapshot_time
        FROM sentiment_snapshot
        WHERE scope = 'global'
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    if not row:
        return APIResponse.success({"scope": "global", "score": 50, "label": "中性"})
    return APIResponse.success(dict(row))


@router.get("/api/sentiment/ashare")
async def ashare_sentiment():
    row = await db.fetch_one("""
        SELECT scope, score, label, long_count, neutral_count, short_count,
               factors, snapshot_time::text AS snapshot_time
        FROM sentiment_snapshot
        WHERE scope = 'ashare'
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    if not row:
        return APIResponse.success({"scope": "ashare", "score": 50, "label": "中性"})
    return APIResponse.success(dict(row))


# ────────── 异动 ──────────
@router.get("/api/anomalies")
async def list_anomalies(status: str = "active", limit: int = 20):
    rows = await db.fetch_all("""
        SELECT id, symbol, stock_name, event_type, direction,
               change_pct, volume_ratio, anomaly_score, confidence,
               detected_at::text AS detected_at
        FROM anomaly_event
        WHERE status = $1
        ORDER BY anomaly_score DESC NULLS LAST
        LIMIT $2
    """, status, limit)
    return APIResponse.success([dict(r) for r in rows])


# ────────── 信号 ──────────
@router.get("/api/signals")
async def list_signals(direction: str | None = None, limit: int = 10):
    sql = """
        SELECT id, signal_type, title, summary, evidence,
               direction, confidence, severity, generated_at::text AS generated_at
        FROM signal_snapshot
        WHERE (expires_at > NOW() OR expires_at IS NULL)
    """
    params: list = []
    if direction:
        sql += f" AND direction = ${len(params)+1}"
        params.append(direction)
    sql += f" ORDER BY confidence DESC, generated_at DESC LIMIT ${len(params)+1}"
    params.append(limit)
    rows = await db.fetch_all(sql, *params)
    return APIResponse.success([dict(r) for r in rows])


# ────────── 预测 ──────────
@router.get("/api/predictions/next-session")
async def next_session_prediction():
    row = await db.fetch_one("""
        SELECT horizon, up_probability, flat_probability, down_probability,
               confidence, explanation, model_version,
               generated_at::text AS generated_at
        FROM prediction_result
        ORDER BY generated_at DESC LIMIT 1
    """)
    if not row:
        return APIResponse.success({
            "horizon": "next_session",
            "up_probability": 0.33, "flat_probability": 0.34, "down_probability": 0.33,
            "confidence": 0.5, "model_version": "rule-baseline-v1"
        })
    return APIResponse.success(dict(row))
