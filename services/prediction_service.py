"""
services/prediction_service.py
================================
次日方向预测 (规则基线)

三阶段实施:
  1. ✅ 规则基线: 情绪 + 量能 + 指数趋势 + 全球风险 → 概率
  2. 🔜 轻量 ML: LightGBM/XGBoost, 滚动训练, 严格按日期切分
  3. 🔜 模型融合: 规则 + ML + 校准器

LLM 只负责解释结构化预测结果，不直接预测涨跌。
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from typing import Any

from db import database as db

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 规则基线预测
# ═══════════════════════════════════════════════════════════════
async def predict_next_session() -> dict:
    """
    基于当日快照预测次日涨跌概率。

    因子:
      - A股情绪分 (权重 35%)
      - 量能比 (权重 20%)
      - 全球风险偏好 (权重 15%)
      - 主力资金方向 (权重 15%)
      - 涨跌停比 (权重 10%)
      - 指数趋势 (权重 5%)
    """
    now = datetime.now()
    factors = {}
    explanations = []

    # 1. A股情绪分
    sentiment = await _get_ashare_sentiment()
    sentiment_score = sentiment.get("score", 50)
    factors["ashare_sentiment"] = sentiment_score
    if sentiment_score > 60:
        explanations.append(f"A股情绪偏强({sentiment_score}分)")
    elif sentiment_score < 40:
        explanations.append(f"A股情绪偏弱({sentiment_score}分)")

    # 2. 量能比
    vol_data = await _get_volume_ratio()
    vol_ratio = vol_data.get("ratio", 1.0)
    factors["volume_ratio"] = vol_ratio
    if vol_ratio > 1.2:
        explanations.append(f"放量({vol_ratio:.1%} vs 昨日)")
    elif vol_ratio < 0.8:
        explanations.append(f"缩量({vol_ratio:.1%} vs 昨日)")

    # 3. 全球风险偏好
    global_sent = await _get_global_sentiment()
    global_score = global_sent.get("score", 50)
    factors["global_risk"] = global_score
    if global_score > 60:
        explanations.append(f"全球风险偏好较高({global_score}分)")
    elif global_score < 40:
        explanations.append(f"全球避险情绪({global_score}分)")

    # 4. 主力资金
    fund_data = await _get_fund_direction()
    fund_dir = fund_data.get("direction", 0)  # 正=流入
    factors["fund_direction"] = fund_dir
    if fund_dir > 50:
        explanations.append(f"主力资金净流入({fund_dir:.0f}亿)")
    elif fund_dir < -50:
        explanations.append(f"主力资金净流出({abs(fund_dir):.0f}亿)")

    # 5. 涨跌停比
    breadth = await _get_breadth()
    limit_up = breadth.get("limit_up", 0)
    limit_down = breadth.get("limit_down", 0)
    limit_ratio = limit_up / max(limit_down, 1)
    factors["limit_ratio"] = limit_ratio
    if limit_up > limit_down * 3:
        explanations.append(f"涨停{limit_up}家 >> 跌停{limit_down}家")
    elif limit_down > limit_up:
        explanations.append(f"跌停{limit_down}家 > 涨停{limit_up}家")

    # 6. 指数趋势 (简化: 当日涨跌)
    index_chg = await _get_index_change()
    factors["index_change"] = index_chg
    if index_chg > 0.5:
        explanations.append(f"大盘上涨{index_chg:.2f}%")
    elif index_chg < -0.5:
        explanations.append(f"大盘下跌{abs(index_chg):.2f}%")

    # ── 加权计算 ──
    # 所有因子先归一化到 0-1 (高分=偏涨)
    norm_sentiment = sentiment_score / 100
    norm_vol = min(1.0, max(0, (vol_ratio - 0.5) / 1.0))
    norm_global = global_score / 100
    norm_fund = min(1.0, max(0, (fund_dir + 200) / 400))  # -200~200亿 → 0~1
    norm_limit = min(1.0, max(0, limit_ratio / 10))
    norm_index = min(1.0, max(0, (index_chg + 5) / 10))   # -5%~5% → 0~1

    weights = [0.35, 0.20, 0.15, 0.15, 0.10, 0.05]
    values = [norm_sentiment, norm_vol, norm_global, norm_fund, norm_limit, norm_index]

    raw_score = sum(w * v for w, v in zip(weights, values))

    # 映射到三分类概率 (简单 sigmoid + 阈值)
    up_prob = round(raw_score * 0.7 + 0.1, 4)   # 0.1~0.8
    flat_prob = round(max(0.15, 0.45 - abs(raw_score - 0.5) * 0.6), 4)
    down_prob = round(1.0 - up_prob - flat_prob, 4)
    # 归一化
    total = up_prob + flat_prob + down_prob
    up_prob = round(up_prob / total, 4)
    flat_prob = round(flat_prob / total, 4)
    down_prob = round(down_prob / total, 4)

    confidence = round(0.5 + abs(raw_score - 0.5) * 0.3, 4)

    result = {
        "horizon": "next_session",
        "up_probability": up_prob,
        "flat_probability": flat_prob,
        "down_probability": down_prob,
        "confidence": confidence,
        "factors": factors,
        "explanation": explanations,
        "model_version": "rule-baseline-v1",
        "generated_at": now,
    }

    # 持久化
    await _store_prediction(result)
    return result


# ═══════════════════════════════════════════════════════════════
# 子因子获取
# ═══════════════════════════════════════════════════════════════
async def _get_ashare_sentiment() -> dict:
    row = await db.fetch_one("""
        SELECT score, label FROM sentiment_snapshot
        WHERE scope = 'ashare' ORDER BY snapshot_time DESC LIMIT 1
    """)
    return dict(row) if row else {"score": 50}


async def _get_global_sentiment() -> dict:
    row = await db.fetch_one("""
        SELECT score FROM sentiment_snapshot
        WHERE scope = 'global' ORDER BY snapshot_time DESC LIMIT 1
    """)
    return dict(row) if row else {"score": 50}


async def _get_volume_ratio() -> dict:
    row = await db.fetch_one("""
        SELECT
            COALESCE(SUM(amount) FILTER (WHERE trade_date = CURRENT_DATE), 0) /
            NULLIF(COALESCE(SUM(amount) FILTER (WHERE trade_date = CURRENT_DATE - INTERVAL '1 day'), 0), 0)
            AS ratio
        FROM stock_prices
    """)
    return {"ratio": float(row["ratio"]) if row and row.get("ratio") else 1.0}


async def _get_fund_direction() -> dict:
    row = await db.fetch_one("""
        SELECT COALESCE(SUM(main_net_inflow), 0) / 1e8 AS direction
        FROM fund_flow WHERE trade_date = CURRENT_DATE
    """)
    return {"direction": float(row["direction"]) if row else 0.0}


async def _get_breadth() -> dict:
    row = await db.fetch_one("""
        SELECT limit_up, limit_down FROM market_breadth
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    return dict(row) if row else {"limit_up": 0, "limit_down": 0}


async def _get_index_change() -> float:
    """上证指数当日涨跌幅。
    注意: latest_quotes / stock_prices 中的 '000001' 是平安银行(深市), 不是上证指数;
    上证指数快照在 market_quote_snapshot.symbol='000001.SH' (由 collect_indices  upsert 维护)。
    """
    row = await db.fetch_one("""
        SELECT change_pct FROM market_quote_snapshot
        WHERE symbol = '000001.SH' AND is_stale = FALSE
        ORDER BY quote_time DESC LIMIT 1
    """)
    return float(row["change_pct"]) if row and row.get("change_pct") is not None else 0.0


# ═══════════════════════════════════════════════════════════════
# 持久化
# ═══════════════════════════════════════════════════════════════
async def _store_prediction(result: dict):
    import json
    await db.execute("""
        INSERT INTO prediction_result
            (target, horizon, up_probability, flat_probability, down_probability,
             confidence, feature_snapshot, explanation, model_version, predicted_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10)
    """,
        "ashare_index", result["horizon"],
        result["up_probability"], result["flat_probability"],
        result["down_probability"], result["confidence"],
        json.dumps(result.get("factors", {}), ensure_ascii=False),
        json.dumps(result.get("explanation", []), ensure_ascii=False),
        result["model_version"], result["generated_at"],
    )


# ═══════════════════════════════════════════════════════════════
# 回测评估
# ═══════════════════════════════════════════════════════════════
async def evaluate_predictions(days: int = 30) -> dict:
    """最近 N 天预测准确率评估"""
    rows = await db.fetch_all("""
        SELECT predicted_at, up_probability, down_probability,
               actual_result
        FROM prediction_result
        WHERE actual_result IS NOT NULL
        ORDER BY predicted_at DESC
        LIMIT $1
    """, days)
    if not rows:
        return {"error": "no evaluation data"}

    correct = 0
    total = 0
    for r in rows:
        if not r.get("actual_result"):
            continue
        pred_up = r.get("up_probability", 0)
        pred_down = r.get("down_probability", 0)
        actual = r["actual_result"]  # "up" / "down" / "flat"
        if pred_up > pred_down and actual == "up":
            correct += 1
        elif pred_down > pred_up and actual == "down":
            correct += 1
        elif abs(pred_up - pred_down) < 0.15 and actual == "flat":
            correct += 1
        total += 1

    return {
        "days": days,
        "samples": total,
        "accuracy": round(correct / max(total, 1), 4),
        "model": "rule-baseline-v1",
    }
