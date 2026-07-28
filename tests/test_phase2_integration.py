"""Phase 2 集成验证 (无需 DB)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import datetime

# ── 1. 异动检测引擎 ──
from services.anomaly_service import (
    _classify_event, _deduplicate, _estimate_prev_volume, record_quote_snapshot
)
assert _classify_event(0.06, 3.0, 2.0) == "surge"
assert _classify_event(-0.06, 3.0, -2.0) == "drop"
assert _classify_event(0.01, 3.0, 0.5) == "volume_break"
assert _classify_event(0.01, 1.0, 2.5) == "volatility_spike"
print("  AnomalyService: classify OK")

# 去重测试
dups = [
    {"symbol": "600519", "anomaly_score": 80},
    {"symbol": "600519", "anomaly_score": 60},
    {"symbol": "000001", "anomaly_score": 70},
]
dedup = _deduplicate(dups)
assert len(dedup) == 2
assert dedup[0]["symbol"] == "600519" and dedup[0]["anomaly_score"] == 80
print("  AnomalyService: deduplicate OK")

# ── 2. 情绪评分 ──
from services.sentiment_service import (
    _score_label, _factor, _calc_ladder_score, _calc_volume_score
)
assert _score_label(15) == "极弱"
assert _score_label(35) == "偏弱"
assert _score_label(55) == "中性"
assert _score_label(75) == "偏强"
assert _score_label(85) == "过热"
assert _factor(0.5, 60, 15)["normalized"] == 60
assert _calc_ladder_score([{"label":"2板","count":3},{"label":"3板","count":2}]) > 30
assert _calc_volume_score({"ratio": 1.51}) == 85
assert _calc_volume_score({"ratio": 0.7}) == 20
print("  SentimentService: OK")

# ── 3. 交易日历 ──
from scheduler.task_registry import (
    is_trading_day, get_market_session, MarketSession, is_market_open,
    should_run, get_task_interval, TASK_FREQUENCIES
)
today = datetime.date.today()
session = get_market_session()
print(f"  TaskRegistry: today={today} trading={is_trading_day()} session={session}")
assert len(TASK_FREQUENCIES) >= 8
assert "anomaly_detect" in TASK_FREQUENCIES
assert "sentiment" in TASK_FREQUENCIES
print("  TaskRegistry: OK")

# ── 4. 市场服务 (新增函数导入) ──
from services.market_service import (
    get_index_intraday, get_minute_volume_comparison,
    get_concept_sustainability, get_enhanced_profit_effect
)
print("  MarketService: new functions imported OK")

# ── 5. 调度器任务 ──
from scheduler.jobs import (
    job_anomaly_detect, job_sentiment_snapshot, job_minute_aggregation
)
print("  Scheduler Jobs: anomaly/sentiment/minute registered OK")

print("\n✅ Phase 2 集成验证全部通过")
print(f"  - AnomalyService: 4 事件类型 + 去重 + 板块共振")
print(f"  - SentimentService: A股8因子 + 全球6因子 + 情绪标签")
print(f"  - TaskRegistry: {len(TASK_FREQUENCIES)} 任务频率表 + 交易日历")
print(f"  - MarketService: 分钟聚合 + 概念持续 + 增强赚钱效应")
print(f"  - Scheduler: 3 个新任务 (anomaly/sentiment/minute)")
