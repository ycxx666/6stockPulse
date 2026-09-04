"""
6stockPulse · 定时任务调度器
==============================
基于 APScheduler 的异步调度:
  - 7×24 新闻        : 1 分钟
  - 资金流向          : 1 分钟 (交易时段)
  - 板块资金          : 2 分钟
  - 个股新闻          : 5 分钟
  - 公告              : 10 分钟
  - 行情轮询          : 实时服务独立管理
  - 物化视图刷新      : 5 分钟
  - 历史清理          : 每天 02:00
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from typing import Callable, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from core.config import SCHEDULER, REALTIME
from infrastructure.database import postgres as db
from application.realtime.service import realtime, in_trading_hours

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._listeners_attached = False

    # ────── 监听 ──────
    def _on_event(self, event):
        if event.exception:
            logger.error("Job %s failed: %s", event.job_id, event.exception)

    def _attach_listeners(self):
        if self._listeners_attached:
            return
        self.scheduler.add_listener(self._on_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        self._listeners_attached = True

    # ────── 启动/停止 ──────
    def start(self):
        self._attach_listeners()
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ────── 注册任务 ──────
    def add_job(self, func, trigger, **kwargs):
        return self.scheduler.add_job(func, trigger, **kwargs)

    def add_interval(self, func, seconds: int, **kwargs):
        return self.scheduler.add_job(func, IntervalTrigger(seconds=seconds), **kwargs)

    def add_cron(self, func, hour: int, minute: int = 0, **kwargs):
        return self.scheduler.add_job(
            func, CronTrigger(hour=hour, minute=minute), **kwargs
        )


# ────────── 任务实现 ──────────
async def job_sina_news():
    from infrastructure.crawler.sina_news import SinaNewsFeed
    async with SinaNewsFeed() as c:
        r = await c.run(pages=2)
    logger.info("job_sina_news: %s", r)


async def job_stock_news():
    from infrastructure.crawler.sina_news import SinaStockNews
    async with SinaStockNews() as c:
        r = await c.run(pages_per_code=1)
    logger.info("job_stock_news: %s", r)


async def job_eastmoney_fund_flow():
    from infrastructure.crawler.eastmoney import EastMoneyFundFlow
    async with EastMoneyFundFlow() as c:
        r = await c.run(pages=2)
    logger.info("job_eastmoney_fund_flow: %s", r)


async def job_sector():
    from infrastructure.crawler.eastmoney import EastMoneySector
    async with EastMoneySector() as c:
        r = await c.run(pages=2)
    logger.info("job_sector: %s", r)


async def job_announcement():
    from infrastructure.crawler.stock_announcement import StockAnnouncementCrawler
    async with StockAnnouncementCrawler() as c:
        r = await c.run(pages_per_code=1)
    logger.info("job_announcement: %s", r)


async def job_refresh_views():
    await db.refresh_latest_quotes()
    await db.refresh_latest_sector()
    await db.refresh_latest_global_indices()
    logger.info("Materialized views refreshed")


async def job_global_indices():
    """全球指数 + 商品 + 外汇 实时刷新 (默认 1 分钟)"""
    from infrastructure.crawler.global_market import GlobalMarketCrawler
    interval = SCHEDULER.global_index_interval
    async with GlobalMarketCrawler(refresh_interval_seconds=interval) as c:
        r = await c.run_once()
    logger.info("job_global_indices (interval=%ds): %s", interval, r)


async def job_prune_old_news():
    """清理 7 天前的原始新闻 (节省空间)"""
    sql = """
        DELETE FROM news
        WHERE publish_time < NOW() - INTERVAL '7 days'
        AND news_id NOT IN (SELECT news_id FROM stock_news)
    """
    n = await db.execute(sql)
    logger.info("prune_old_news: %s", n)


async def job_prune_old_prices():
    """保留 90 天的 tick 行情"""
    sql = """
        DELETE FROM stock_prices
        WHERE trade_time < NOW() - INTERVAL '90 days'
    """
    n = await db.execute(sql)
    logger.info("prune_old_prices: %s", n)


async def job_market_breadth():
    """市场宽度计算 (每分钟)"""
    from domain.market import compute_and_store_breadth
    from domain.dashboard import invalidate_snapshot
    try:
        breadth = await compute_and_store_breadth()
        invalidate_snapshot()
        logger.info("market_breadth: up=%d down=%d score=%d",
                    breadth.get("up_count", 0), breadth.get("down_count", 0),
                    breadth.get("sentiment_score", 50))
    except Exception as e:
        logger.error("market_breadth failed: %s", e)


async def job_seed_global_market():
    """初始化 / 刷新全球行情种子数据"""
    from infrastructure.crawler.global_market import GlobalMarketCrawler
    interval = SCHEDULER.global_index_interval
    async with GlobalMarketCrawler(refresh_interval_seconds=interval) as c:
        r = await c.run_once()
    logger.info("job_seed_global_market: %s", r)


async def job_anomaly_detect():
    """异动检测 (每 10 秒, 仅交易时段)"""
    from domain.anomaly import detect_anomalies, record_quote_snapshot
    from application.realtime.service import realtime
    try:
        # 记录当前行情快照
        quotes = realtime.snapshot()
        if quotes:
            await record_quote_snapshot(quotes)
        # 检测异动
        anomalies = await detect_anomalies()
        if anomalies:
            logger.info("anomaly_detect: %d events, top=%s(%.1f)",
                        len(anomalies),
                        anomalies[0].get("symbol", "?"),
                        anomalies[0].get("anomaly_score", 0))
    except Exception as e:
        logger.error("anomaly_detect failed: %s", e)


async def job_sentiment_snapshot():
    """情绪快照计算 (每 1 分钟)"""
    from domain.sentiment import compute_all
    try:
        await compute_all()
    except Exception as e:
        logger.error("sentiment_snapshot failed: %s", e)


async def job_minute_aggregation():
    """分钟聚合: 指数分时 + 量能对比 (每分钟, 仅交易时段)"""
    try:
        from domain.market import compute_and_store_breadth
        await compute_and_store_breadth()
    except Exception as e:
        logger.error("minute_aggregation failed: %s", e)


async def job_nlp_process_news():
    """NLP 新闻处理 (每 2 分钟)"""
    from domain.news import process_news_batch
    try:
        r = await process_news_batch(limit=50)
        if r.get("processed", 0) > 0:
            logger.info("nlp_process: %s", r)
    except Exception as e:
        logger.error("nlp_process failed: %s", e)


async def job_generate_signals():
    """信号编排 (每 5 分钟)"""
    from domain.signal import generate_signals
    try:
        signals = await generate_signals()
        logger.info("generate_signals: %d signals", len(signals))
    except Exception as e:
        logger.error("generate_signals failed: %s", e)


async def job_predict_next_session():
    """次日预测 (午盘/收盘)"""
    from domain.prediction import predict_next_session
    try:
        r = await predict_next_session()
        logger.info("prediction: up=%.1f%% down=%.1f%% confidence=%.1f%%",
                    r["up_probability"] * 100,
                    r["down_probability"] * 100,
                    r["confidence"] * 100)
    except Exception as e:
        logger.error("prediction failed: %s", e)


async def job_enrich_news_content():
    """全文抓取补全 (每 10 分钟)"""
    from infrastructure.crawler.sina_news import enrich_news_content
    try:
        r = await enrich_news_content(limit=10)
        logger.info("enrich_news: %s", r)
    except Exception as e:
        logger.error("enrich_news failed: %s", e)


async def job_yahoo_tier1():
    """Yahoo Finance Tier 1: 核心指数 + 商品 (15s)"""
    from infrastructure.crawler.yahoo_finance import fetch_tier1
    try:
        r = await fetch_tier1()
        logger.info("yahoo_tier1: %s", {k: v for k, v in r.items() if k != "error"})
    except Exception as e:
        logger.error("yahoo_tier1 failed: %s", e)


async def job_yahoo_tier2():
    """Yahoo Finance Tier 2: 核心个股 + 外汇 (60s)"""
    from infrastructure.crawler.yahoo_finance import fetch_tier2
    try:
        r = await fetch_tier2()
        logger.info("yahoo_tier2: %s", {k: v for k, v in r.items() if k != "error"})
    except Exception as e:
        logger.error("yahoo_tier2 failed: %s", e)


# ────────── 注册全部任务 ──────────
def register_all(svc: SchedulerService):
    """注册所有定时任务 (智能适配交易时段)"""
    from scheduler.task_registry import is_market_open, get_task_interval

    # 新闻 1 分钟
    svc.add_interval(job_sina_news, seconds=SCHEDULER.sina_news_interval, id="sina_news")

    # 资金流向 1 分钟 (仅交易时段)
    async def fund_flow_smart():
        if in_trading_hours():
            await job_eastmoney_fund_flow()
    svc.add_interval(fund_flow_smart, seconds=SCHEDULER.fund_flow_interval, id="fund_flow")

    # 板块 2 分钟
    async def sector_smart():
        if in_trading_hours():
            await job_sector()
    svc.add_interval(sector_smart, seconds=SCHEDULER.sector_flow_interval, id="sector")

    # 个股新闻 5 分钟
    svc.add_interval(job_stock_news, seconds=SCHEDULER.stock_news_interval, id="stock_news")

    # 公告 10 分钟
    svc.add_interval(job_announcement, seconds=SCHEDULER.ann_interval, id="announcement")

    # 全球指数 / VIX 实时刷新
    svc.add_interval(job_global_indices,
                     seconds=SCHEDULER.global_index_interval,
                     id="global_indices")

    # 物化视图 5 分钟
    svc.add_interval(job_refresh_views, seconds=300, id="refresh_views")

    # 网关新闻采集: 按 poll.group 分档 (仅 _health=verified, 见 runner._filter_sources)
    async def _gateway_poll(group_name: str | None):
        from news_gateway.runner import fetch_all_sources
        try:
            # 同步阻塞型采集放入线程, 避免卡住调度器事件循环
            summary = await asyncio.to_thread(
                fetch_all_sources,
                config_path="config/sources_config.json",
                persist=True, print_result=False,
                healthy_only=True, group=group_name)
            logger.info("gateway_news[%s]: 抓取 %s | 入库 %s | 错误 %s",
                        group_name or "refill", summary["total"],
                        summary["inserted"], len(summary["errors"]))
            if summary["errors"]:
                logger.warning("gateway_news[%s] 错误源: %s",
                               group_name or "refill",
                               [e["name"] for e in summary["errors"][:10]])
        except Exception as e:
            logger.error("gateway_news[%s] 失败: %s", group_name or "refill", e)

    # cn_fast: 交易时段加密 120s; 盘后降频 600s (两 job 互斥窗口)
    async def cn_fast_day_smart():
        if in_trading_hours():
            await _gateway_poll("cn_fast")
    svc.add_interval(cn_fast_day_smart,
                     seconds=SCHEDULER.news_cn_fast_day,
                     id="gateway_cn_fast_day")

    async def cn_fast_off_smart():
        if not in_trading_hours():
            await _gateway_poll("cn_fast")
    svc.add_interval(cn_fast_off_smart,
                     seconds=SCHEDULER.news_cn_fast_off,
                     id="gateway_cn_fast_off")

    # media: 常规媒体/全球聚合, 24h 恒定 300s (海外源全天出稿)
    # 51 源单轮实测 467s → 拆3子组仍 343s(海外双路开销大) → 拆 6 子组(a~f, 各 8-9 源) 单轮 ~170s
    for _mg in ("media_a", "media_b", "media_c", "media_d", "media_e", "media_f"):
        async def _media_poll(group_name=_mg):
            await _gateway_poll(group_name)
        svc.add_interval(_media_poll,
                         seconds=SCHEDULER.news_media_interval,
                         id=f"gateway_{_mg}")

    # slow: 低频/社区/垂类, 600s
    async def slow_smart():
        await _gateway_poll("slow")
    svc.add_interval(slow_smart,
                     seconds=SCHEDULER.news_slow_interval,
                     id="gateway_slow")

    # 每日 04:00 全量补漏 (兜底白天网络抖动漏采)
    async def gateway_refill():
        await _gateway_poll(None)
    svc.add_cron(gateway_refill, hour=4, minute=0,
                 id="gateway_news_refill")

    # 市场宽度 30 秒 (交易时段)
    async def breadth_smart():
        if in_trading_hours():
            await job_market_breadth()
    svc.add_interval(breadth_smart, seconds=30, id="market_breadth")

    # ── Phase 2 新增任务 ──

    # 异动检测 10 秒 (仅交易时段)
    async def anomaly_smart():
        if is_market_open():
            await job_anomaly_detect()
    svc.add_interval(anomaly_smart, seconds=10, id="anomaly_detect")

    # 情绪快照 1 分钟
    async def sentiment_smart():
        if is_market_open():
            await job_sentiment_snapshot()
    svc.add_interval(sentiment_smart, seconds=60, id="sentiment")

    # 分钟聚合 60 秒 (仅交易时段)
    async def minute_smart():
        if is_market_open():
            await job_minute_aggregation()
    svc.add_interval(minute_smart, seconds=60, id="minute_aggregation")

    # ── Phase 3 新增任务 ──

    # NLP 新闻处理 2 分钟
    svc.add_interval(job_nlp_process_news, seconds=120, id="nlp_process")

    # 信号编排 5 分钟
    async def signals_smart():
        if is_market_open():
            await job_generate_signals()
    svc.add_interval(signals_smart, seconds=300, id="generate_signals")

    # 全文抓取 10 分钟
    svc.add_interval(job_enrich_news_content, seconds=600, id="enrich_news")

    # 次日预测: 午盘 11:30 + 收盘 15:00
    svc.add_cron(job_predict_next_session, hour=11, minute=30, id="prediction_noon")
    svc.add_cron(job_predict_next_session, hour=15, minute=0,  id="prediction_close")

    # 清理: 每天 02:00
    svc.add_cron(job_prune_old_news, hour=2, minute=0, id="prune_news")
    svc.add_cron(job_prune_old_prices, hour=2, minute=30, id="prune_prices")

    # ── Yahoo Finance 全球行情 ──
    svc.add_interval(job_yahoo_tier1, seconds=15, id="yahoo_tier1")
    svc.add_interval(job_yahoo_tier2, seconds=60, id="yahoo_tier2")

    logger.info("All scheduled jobs registered (v5: +yahoo_tier1/tier2)")


# 全局单例
scheduler = SchedulerService()
