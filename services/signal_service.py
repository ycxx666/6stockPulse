"""
services/signal_service.py
=============================
结构化信号编排引擎

将资金、量价、新闻、公告、异动汇总为:
  - opportunity (机会信号)
  - risk (风险信号)

每条信号包含: title, summary, evidence[], direction, confidence, severity
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Any

from db import database as db
from services.cache import cache, CacheKeys

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 信号编排
# ═══════════════════════════════════════════════════════════════
async def generate_signals() -> list[dict]:
    """
    综合多数据源生成结构化信号。
    返回信号列表，每条包含证据引用。
    """
    signals: list[dict] = []
    now = datetime.now()

    # ── 机会信号 ──
    signals.extend(await _detect_fund_inflow_opportunity(now))
    signals.extend(await _detect_sector_resonance_opportunity(now))
    signals.extend(await _detect_volume_breakout_opportunity(now))
    signals.extend(await _detect_positive_news_signal(now))

    # ── 风险信号 ──
    signals.extend(await _detect_retail_trap_risk(now))
    signals.extend(await _detect_high_position_stall_risk(now))
    signals.extend(await _detect_negative_announcement_risk(now))
    signals.extend(await _detect_broad_market_weakness(now))

    # 按置信度排序
    signals.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    # 持久化
    if signals:
        await _store_signals(signals[:15])

    cache.delete(CacheKeys.SIGNALS_ACTIVE)
    return signals


# ═══════════════════════════════════════════════════════════════
# 机会检测
# ═══════════════════════════════════════════════════════════════

async def _detect_fund_inflow_opportunity(now: datetime) -> list[dict]:
    """主力连续净流入 + 板块共振"""
    rows = await db.fetch_all("""
        WITH recent_days AS (
            SELECT stock_code, trade_date,
                   SUM(main_net_inflow) AS daily_inflow
            FROM fund_flow
            WHERE trade_date >= CURRENT_DATE - INTERVAL '3 days'
            GROUP BY stock_code, trade_date
        ),
        consecutive AS (
            SELECT stock_code,
                   COUNT(*) AS inflow_days,
                   SUM(daily_inflow) AS total_inflow
            FROM recent_days
            WHERE daily_inflow > 0
            GROUP BY stock_code
            HAVING COUNT(*) >= 2
        )
        SELECT c.stock_code, s.stock_name, s.industry,
               c.inflow_days, c.total_inflow
        FROM consecutive c
        JOIN stocks s ON s.stock_code = c.stock_code
        ORDER BY c.total_inflow DESC
        LIMIT 5
    """)
    signals = []
    for r in rows:
        name = r.get("stock_name") or r["stock_code"]
        signals.append({
            "signal_type": "opportunity",
            "title": f"{name} 主力连续{r['inflow_days']}日净流入",
            "summary": f"{name}({r['stock_code']}) 近{r['inflow_days']}日主力累计净流入{float(r['total_inflow'])/1e8:.1f}亿，所属{r.get('industry','')}板块",
            "evidence": [
                {"type": "fund_flow", "value": f"连续{r['inflow_days']}日流入, 累计{float(r['total_inflow'])/1e8:.1f}亿"},
                {"type": "sector", "value": r.get("industry", "")},
            ],
            "direction": "bullish",
            "confidence": min(0.9, 0.5 + r["inflow_days"] * 0.15),
            "severity": "info",
            "generated_at": now,
        })
    return signals


async def _detect_sector_resonance_opportunity(now: datetime) -> list[dict]:
    """板块共振: 同板块多只股票异动"""
    from services.anomaly_service import detect_sector_resonance
    resonances = await detect_sector_resonance()
    signals = []
    for r in resonances[:3]:
        signals.append({
            "signal_type": "opportunity",
            "title": f"板块共振: {r['sector_code']} ({r['member_count']}只异动)",
            "summary": f"{r['sector_code']}板块{r['member_count']}只股票同步{r['dominant_direction']}向异动",
            "evidence": [
                {"type": "sector_resonance", "value": f"{r['member_count']}只同步{r['dominant_direction']}"},
                {"type": "members", "value": r.get("members", [])},
            ],
            "direction": "bullish" if r["dominant_direction"] == "up" else "bearish",
            "confidence": r.get("confidence", 0.6),
            "severity": "warning",
            "generated_at": now,
        })
    return signals


async def _detect_volume_breakout_opportunity(now: datetime) -> list[dict]:
    """放量突破信号"""
    rows = await db.fetch_all("""
        SELECT a.symbol, a.change_pct, a.volume_ratio, a.anomaly_score
        FROM anomaly_event a
        WHERE a.status = 'active'
          AND a.event_type = 'volume_break'
          AND a.direction = 'up'
        ORDER BY a.anomaly_score DESC
        LIMIT 3
    """)
    signals = []
    for r in rows:
        signals.append({
            "signal_type": "opportunity",
            "title": f"{r['symbol']} 放量突破",
            "summary": f"{r['symbol']} 放量突破，涨幅{r.get('change_pct',0):.2f}%，量比{r.get('volume_ratio',0):.1f}",
            "evidence": [
                {"type": "anomaly", "value": f"量比{r.get('volume_ratio',0):.1f}, score={r.get('anomaly_score',0):.0f}"},
            ],
            "direction": "bullish",
            "confidence": 0.65,
            "severity": "info",
            "generated_at": now,
        })
    return signals


async def _detect_positive_news_signal(now: datetime) -> list[dict]:
    """正面新闻信号（标题含负面词则跳过，避免利空被误标为利好）"""
    rows = await db.fetch_all("""
        SELECT title, sentiment, importance, stocks, publish_time, source
        FROM news
        WHERE is_processed = TRUE
          AND sentiment > 0.3
          AND importance >= 3
        ORDER BY publish_time DESC
        LIMIT 10
    """)
    signals = []
    for r in rows:
        if len(signals) >= 3:
            break
        title = r.get("title") or ""
        # 标题负面词二次校验：情感分可能误判，标题含负面词直接跳过
        if any(w in title for w in ("跌", "回落", "重挫", "走低", "收跌", "下挫", "暴跌", "下滑", "跌幅")):
            continue
        stocks = r.get("stocks") or []
        signals.append({
            "signal_type": "opportunity",
            "title": f"利好消息: {r['title'][:50]}",
            "summary": r['title'][:150],
            "evidence": [
                {"type": "news", "value": f"sentiment={r.get('sentiment',0):.2f}, importance={r.get('importance',0)}"},
                {"type": "source", "value": r.get("source") or "未知来源"},
                {"type": "publish_time", "value": str(r.get("publish_time") or "")},
                {"type": "stocks", "value": ", ".join(str(s) for s in stocks[:5]) if stocks else "无关联个股"},
            ],
            "direction": "bullish",
            "confidence": 0.6,
            "severity": "info",
            "generated_at": now,
        })
    return signals


# ═══════════════════════════════════════════════════════════════
# 风险检测
# ═══════════════════════════════════════════════════════════════

async def _detect_retail_trap_risk(now: datetime) -> list[dict]:
    """机构出货散户接盘: 大单流出 + 小单流入"""
    rows = await db.fetch_all("""
        SELECT stock_code,
               large_net_inflow, small_net_inflow,
               main_net_inflow
        FROM fund_flow
        WHERE trade_date = CURRENT_DATE
          AND large_net_inflow < 0
          AND small_net_inflow > 0
        ORDER BY ABS(large_net_inflow) DESC
        LIMIT 5
    """)
    signals = []
    for r in rows:
        if (r.get("main_net_inflow") or 0) >= 0:
            continue  # 主力整体未流出
        signals.append({
            "signal_type": "risk",
            "title": f"{r['stock_code']} 大单流出散户接盘",
            "summary": f"{r['stock_code']} 大单净流出{float(r['large_net_inflow'])/1e4:.0f}万，小单净流入{float(r['small_net_inflow'])/1e4:.0f}万，警惕出货",
            "evidence": [
                {"type": "fund_flow", "value": f"大单{float(r['large_net_inflow'])/1e4:.0f}万流出"},
                {"type": "fund_flow", "value": f"小单{float(r['small_net_inflow'])/1e4:.0f}万流入"},
            ],
            "direction": "bearish",
            "confidence": 0.7,
            "severity": "warning",
            "generated_at": now,
        })
    return signals[:3]


async def _detect_high_position_stall_risk(now: datetime) -> list[dict]:
    """高位放量滞涨"""
    rows = await db.fetch_all("""
        SELECT a.symbol, a.change_pct, a.volume_ratio, a.anomaly_score
        FROM anomaly_event a
        WHERE a.status = 'active'
          AND a.event_type = 'volume_break'
          AND a.direction = 'down'
        ORDER BY a.anomaly_score DESC
        LIMIT 3
    """)
    signals = []
    for r in rows:
        signals.append({
            "signal_type": "risk",
            "title": f"{r['symbol']} 放量下跌",
            "summary": f"{r['symbol']} 放量下跌{r.get('change_pct',0):.2f}%，量比{r.get('volume_ratio',0):.1f}",
            "evidence": [
                {"type": "anomaly", "value": f"量比{r.get('volume_ratio',0):.1f}, score={r.get('anomaly_score',0):.0f}"},
            ],
            "direction": "bearish",
            "confidence": 0.6,
            "severity": "warning",
            "generated_at": now,
        })
    return signals


async def _detect_negative_announcement_risk(now: datetime) -> list[dict]:
    """利空公告"""
    rows = await db.fetch_all("""
        SELECT stock_code, title, ann_type
        FROM stock_announcements
        WHERE publish_time > NOW() - INTERVAL '24 hours'
          AND (title ILIKE '%减持%' OR title ILIKE '%亏损%'
               OR title ILIKE '%退市%' OR title ILIKE '%警示%'
               OR title ILIKE '%处罚%')
        ORDER BY publish_time DESC
        LIMIT 3
    """)
    signals = []
    for r in rows:
        signals.append({
            "signal_type": "risk",
            "title": f"{r['stock_code']} 利空公告: {r['title'][:40]}",
            "summary": r['title'][:150],
            "evidence": [
                {"type": "announcement", "value": r.get("ann_type", "")},
                {"type": "title", "value": r.get("title", "")[:60]},
            ],
            "direction": "bearish",
            "confidence": 0.75,
            "severity": "critical",
            "generated_at": now,
        })
    return signals


async def _detect_broad_market_weakness(now: datetime) -> list[dict]:
    """大盘弱势: 下跌家数占比 > 70%"""
    row = await db.fetch_one("""
        SELECT up_count, down_count, total, sentiment_score
        FROM market_breadth
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    if not row:
        return []
    down = row.get("down_count", 0)
    total = max(row.get("total", 1), 1)
    if down / total < 0.7:
        return []
    return [{
        "signal_type": "risk",
        "title": "大盘弱势: 下跌家数超70%",
        "summary": f"全市场{total}只股票中{down}只下跌({down*100//total}%)，情绪分{row.get('sentiment_score',50)}",
        "evidence": [
            {"type": "breadth", "value": f"下跌{down}/{total} ({down*100//total}%)"},
            {"type": "sentiment", "value": f"情绪分{row.get('sentiment_score',50)}"},
        ],
        "direction": "bearish",
        "confidence": 0.8,
        "severity": "critical",
        "generated_at": now,
    }]


# ═══════════════════════════════════════════════════════════════
# 持久化
# ═══════════════════════════════════════════════════════════════
async def _store_signals(signals: list[dict]):
    import json
    # 标记旧信号过期
    await db.execute("""
        UPDATE signal_snapshot SET expires_at = NOW()
        WHERE expires_at IS NULL
    """)
    for s in signals:
        await db.execute("""
            INSERT INTO signal_snapshot
                (signal_type, title, summary, evidence, direction,
                 confidence, severity, generated_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
        """,
            s["signal_type"], s["title"], s["summary"],
            json.dumps(s.get("evidence", []), ensure_ascii=False),
            s.get("direction"), s.get("confidence", 0.5),
            s.get("severity", "info"), s["generated_at"],
        )
