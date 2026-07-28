"""
infrastructure/crawler/factory.py
===================================
爬虫工厂：统一注册和创建入口。
新增数据源只需在此注册，业务代码无需改动。
"""
from __future__ import annotations
from typing import Type

from infrastructure.crawler.base import BaseCrawler
from infrastructure.crawler.eastmoney import (
    EastMoneyFundFlow, EastMoneySector, EastMoneyRealtime, EastMoneyFundFlowHistory
)
from infrastructure.crawler.sina_news import SinaNewsFeed, SinaStockNews
from infrastructure.crawler.global_market import GlobalMarketCrawler
from infrastructure.crawler.stock_announcement import StockAnnouncementCrawler
from infrastructure.crawler.yahoo_finance import YahooFinanceCrawler


# ── 注册表: {(source, type): CrawlerClass} ──
_registry: dict[tuple[str, str], Type[BaseCrawler]] = {
    ("eastmoney", "fund_flow"):       EastMoneyFundFlow,
    ("eastmoney", "sector"):          EastMoneySector,
    ("eastmoney", "realtime"):        EastMoneyRealtime,
    ("eastmoney", "fund_flow_history"): EastMoneyFundFlowHistory,
    ("sina",     "news_feed"):        SinaNewsFeed,
    ("sina",     "stock_news"):       SinaStockNews,
    ("global",   "market"):           GlobalMarketCrawler,
    ("eastmoney", "announcement"):    StockAnnouncementCrawler,
    ("yahoo",     "market"):           YahooFinanceCrawler,
}


def create_crawler(source: str, crawler_type: str, **kwargs) -> BaseCrawler:
    """
    工厂方法：按 (source, type) 创建爬虫实例。

    Usage:
        c = create_crawler("eastmoney", "fund_flow")
        c = create_crawler("sina", "news_feed")
        c = create_crawler("global", "market", refresh_interval_seconds=15)
    """
    key = (source, crawler_type)
    cls = _registry.get(key)
    if cls is None:
        available = ", ".join(f"{s}/{t}" for s, t in _registry)
        raise ValueError(
            f"Unknown crawler: source={source}, type={crawler_type}. "
            f"Available: {available}"
        )
    return cls(**kwargs)


def list_crawlers() -> list[dict]:
    """列出所有已注册的爬虫"""
    return [
        {"source": s, "type": t, "class": cls.__name__}
        for (s, t), cls in _registry.items()
    ]


def register_crawler(source: str, crawler_type: str, cls: Type[BaseCrawler]):
    """动态注册新爬虫（插件扩展用）"""
    _registry[(source, crawler_type)] = cls
