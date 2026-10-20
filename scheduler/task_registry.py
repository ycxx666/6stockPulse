"""
scheduler/task_registry.py
=============================
交易日历驱动的任务控制

替代简单的 in_trading_hours() 时间判断，
支持 A 股交易日历、时段细分、频率自适应。
"""
from __future__ import annotations
from datetime import datetime, date, time as dtime, timedelta, timezone
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class MarketSession(Enum):
    PRE_OPEN   = "pre_open"     # 09:00-09:30
    MORNING    = "morning"      # 09:30-11:30
    LUNCH      = "lunch_break"  # 11:30-13:00
    AFTERNOON  = "afternoon"    # 13:00-15:00
    POST_CLOSE = "post_close"   # 15:00-16:00
    CLOSED     = "closed"       # 非交易日


# ────────── 交易日历 ──────────
# 简化版: 使用固定的节假日列表 + 周末判断
# 正式环境应接入 tushare / akshare 的交易日历 API
CN_HOLIDAYS_2025 = {
    date(2025, 1, 1),   date(2025, 1, 28),  date(2025, 1, 29),  date(2025, 1, 30),
    date(2025, 1, 31),  date(2025, 2, 3),   date(2025, 2, 4),
    date(2025, 4, 4),   date(2025, 4, 5),
    date(2025, 5, 1),   date(2025, 5, 2),   date(2025, 5, 5),
    date(2025, 6, 2),
    date(2025, 10, 1),  date(2025, 10, 2),  date(2025, 10, 3),
    date(2025, 10, 6),  date(2025, 10, 7),  date(2025, 10, 8),
}


def is_trading_day(d: date | None = None) -> bool:
    """判断是否为 A 股交易日"""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:  # 周六周日
        return False
    if d in CN_HOLIDAYS_2025:
        return False
    return True


def get_market_session(now: datetime | None = None) -> MarketSession:
    """获取当前市场时段"""
    if now is None:
        # 用北京时间判断（容器可能跑在 UTC，避免 8 小时时区错位）
        now = datetime.now(timezone(timedelta(hours=8)))

    if not is_trading_day(now.date()):
        return MarketSession.CLOSED

    cur = now.time()
    if dtime(9, 0) <= cur < dtime(9, 30):
        return MarketSession.PRE_OPEN
    if dtime(9, 30) <= cur <= dtime(11, 30):
        return MarketSession.MORNING
    if dtime(11, 30) < cur < dtime(13, 0):
        return MarketSession.LUNCH
    if dtime(13, 0) <= cur <= dtime(15, 0):
        return MarketSession.AFTERNOON
    if dtime(15, 0) < cur <= dtime(16, 0):
        return MarketSession.POST_CLOSE
    return MarketSession.CLOSED


def is_market_open(now: datetime | None = None) -> bool:
    """市场是否在连续竞价时段"""
    session = get_market_session(now)
    return session in (MarketSession.MORNING, MarketSession.AFTERNOON)


# ────────── 任务频率表 ──────────
# 不同时段采用不同频率
TASK_FREQUENCIES = {
    # task_id: {session: interval_seconds}
    "sina_news":       {MarketSession.MORNING: 15, MarketSession.AFTERNOON: 15,
                        MarketSession.LUNCH: 30, MarketSession.CLOSED: 60},
    "fund_flow":       {MarketSession.MORNING: 30, MarketSession.AFTERNOON: 30,
                        MarketSession.CLOSED: 300},
    "sector":          {MarketSession.MORNING: 60, MarketSession.AFTERNOON: 60,
                        MarketSession.CLOSED: 300},
    "stock_news":      {MarketSession.MORNING: 120, MarketSession.AFTERNOON: 120,
                        MarketSession.CLOSED: 300},
    "announcement":    {MarketSession.MORNING: 300, MarketSession.AFTERNOON: 300,
                        MarketSession.CLOSED: 900},
    "global_indices":  {MarketSession.MORNING: 15, MarketSession.AFTERNOON: 15,
                        MarketSession.LUNCH: 30, MarketSession.CLOSED: 60},
    "market_breadth":  {MarketSession.MORNING: 15, MarketSession.AFTERNOON: 15,
                        MarketSession.CLOSED: 120},
    "sentiment":       {MarketSession.MORNING: 30, MarketSession.AFTERNOON: 30,
                        MarketSession.LUNCH: 120, MarketSession.CLOSED: 300},
    "anomaly_detect":  {MarketSession.MORNING: 10, MarketSession.AFTERNOON: 10},
    "refresh_views":   {MarketSession.MORNING: 300, MarketSession.AFTERNOON: 300,
                        MarketSession.CLOSED: 300},
}


def get_task_interval(task_id: str) -> int | None:
    """根据当前市场时段，返回任务应执行的间隔 (秒)"""
    session = get_market_session()
    freq_map = TASK_FREQUENCIES.get(task_id, {})
    return freq_map.get(session)


def should_run(task_id: str) -> bool:
    """判断任务当前是否应该执行"""
    interval = get_task_interval(task_id)
    return interval is not None


# ────────── 调度辅助 ──────────
def next_trading_day() -> date:
    """获取下一个交易日"""
    d = date.today() + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def trading_days_between(start: date, end: date) -> int:
    """计算两个日期之间的交易日数"""
    count = 0
    d = start
    while d <= end:
        if is_trading_day(d):
            count += 1
        d += timedelta(days=1)
    return count


# ────────── 测试 ──────────
if __name__ == "__main__":
    print(f"Today: {date.today()} is_trading={is_trading_day()}")
    print(f"Session: {get_market_session()}")
    print(f"Open: {is_market_open()}")
    for tid in ["fund_flow", "sector", "global_indices"]:
        print(f"  {tid}: interval={get_task_interval(tid)}s, run={should_run(tid)}")
