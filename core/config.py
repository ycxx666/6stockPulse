"""
6stockPulse · 配置中心
========================
统一管理 DB / 爬虫 / 调度 / API 配置
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class DBConfig:
    host:     str = os.getenv("PG_HOST", "localhost")
    port:     int = int(os.getenv("PG_PORT", "5432"))
    user:     str = os.getenv("PG_USER", "davi60")
    password: str = os.getenv("PG_PASSWORD", "")
    database: str = os.getenv("PG_DATABASE", "stockpulse")
    pool_min: int = int(os.getenv("PG_POOL_MIN", "2"))
    pool_max: int = int(os.getenv("PG_POOL_MAX", "10"))


@dataclass
class CrawlerConfig:
    """爬虫通用配置"""
    user_agents: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    ])
    request_interval: float = 2.0        # 秒
    request_jitter:   float = 1.0        # 随机扰动 [0, jitter]
    timeout:          int   = 15
    max_retries:      int   = 3
    backoff_factor:   float = 1.5
    verify_ssl:       bool  = False
    proxies:          dict  = field(default_factory=dict)  # {"http": "...", "https": "..."}


@dataclass
class RealtimeConfig:
    """实时数据配置"""
    enabled:    bool  = True
    poll_interval: float = 3.0          # 行情轮询间隔 (秒)
    quotes_per_batch: int = 50          # 每批拉取股票数
    top_n_stocks:   int = 100           # 实时跟踪 TOP N
    websocket_broadcast: bool = True    # 是否通过 WS 推送
    # ── 调度器侧的实时刷新配置 (爬虫 → DB) ──
    # 默认 60 秒 = 1 分钟, 同一时间桶内自动去重
    # 可通过环境变量 RT_REFRESH_SECONDS 调整 (如 30, 60, 120, 300)
    refresh_interval_seconds: int = int(os.getenv("RT_REFRESH_SECONDS", "60"))


@dataclass
class SchedulerConfig:
    """定时任务配置"""
    sina_news_interval:    int = 30         # 新闻 30 秒
    fund_flow_interval:    int = 60         # 资金流 1 分钟
    sector_flow_interval:  int = 120        # 板块 2 分钟
    stock_news_interval:   int = 300        # 个股新闻 5 分钟
    ann_interval:          int = 600        # 公告 10 分钟
    prune_news_hours:      int = 168        # 新闻保留 7 天
    # ── 全球指数 (VIX 等) 实时刷新间隔 ──
    # 默认 15 秒 (交易时段), 可通过环境变量 GLOBAL_INDEX_INTERVAL 调整
    global_index_interval: int = int(os.getenv(
        "GLOBAL_INDEX_INTERVAL",
        os.getenv("RT_REFRESH_SECONDS", "15")
    ))
    # ── 网关新闻(零风险健康源)分档定时采集 ──
    # 仅采 _health=verified 源; 按 poll.group 分级: cn_fast 交易时段加密 / media 恒定 / slow 低频
    news_gateway_interval: int = int(os.getenv(   # 兼容旧 env, 同时作 media 档默认值
        "NEWS_GATEWAY_INTERVAL", "300"))
    news_cn_fast_day: int = int(os.getenv("NEWS_CN_FAST_DAY", "120"))  # 盘中财经快讯
    news_cn_fast_off: int = int(os.getenv("NEWS_CN_FAST_OFF", "600"))  # 盘后降频
    news_media_interval: int = int(os.getenv(
        "NEWS_MEDIA_INTERVAL", os.getenv("NEWS_GATEWAY_INTERVAL", "300")))
    news_slow_interval: int = int(os.getenv("NEWS_SLOW_INTERVAL", "600"))


@dataclass
class APIConfig:
    """FastAPI 配置"""
    host: str = "0.0.0.0"
    port: int = 8900
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    title: str = "6stockPulse API"
    version: str = "4.0.0"


# ────────── 东方财富 / 新浪 / 板块 URL 模板 ──────────
@dataclass(frozen=True)
class EastMoneyURLs:
    """东方财富接口"""
    # A 股资金流向 (按主力净流入排序)
    FUND_FLOW_LIST = (
        "https://push2delay.eastmoney.com/api/qt/clist/get?"
        "fid=f62&po=1&pz={pz}&pn={pn}&np=1&fltt=2&invt=2"
        "&ut=b2884a393a59ad64002292a3e90d46a5"
        "&fields=f12,f14,f2,f3,f5,f6,f8,f9,f10,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
        "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    )
    # 实时行情 (单股)
    QUOTE_REALTIME = (
        "https://push2.eastmoney.com/api/qt/stock/get?"
        "secid={secid}&fields=f43,f44,f45,f46,f47,f48,f60,f51,f52,f57,f58,f107,f162,f167,f168,f169,f170"
        "&ut=b2884a393a59ad64002292a3e90d46a5"
    )
    # K线 (历史)
    KLINE = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        "secid={secid}&ut=b2884a393a59ad64002292a3e90d46a5"
        "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        "&klt=101&fqt=1&lmt={lmt}"
    )
    # 板块 (行业)
    SECTOR_INDUSTRY = (
        "https://push2delay.eastmoney.com/api/qt/clist/get?"
        "fid=f3&po=1&pz=50&pn={pn}&np=1&fltt=2&invt=2"
        "&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f8,f100,f102"
    )
    # 板块 (概念)
    SECTOR_CONCEPT = (
        "https://push2delay.eastmoney.com/api/qt/clist/get?"
        "fid=f3&po=1&pz=50&pn={pn}&np=1&fltt=2&invt=2"
        "&fs=m:90+t:3&fields=f12,f14,f3,f62,f184,f8,f100,f102"
    )
    # 个股新闻
    STOCK_NEWS = (
        "https://np-anotice-stock.eastmoney.com/api/security/ann?"
        "cb=jQuery&sr=-1&page_size=20&page_index={pn}"
        "&ann_type=A&client_source=web&stock_list={code}"
        "&f_node=0&s_node=0"
    )
    # 公告 (新)
    STOCK_ANN = (
        "https://np-cnotice-stock.eastmoney.com/api/content/ann?"
        "art_code={art_code}&client_source=web&page_index=1"
    )


@dataclass(frozen=True)
class SinaURLs:
    """新浪财经接口"""
    # 7×24 快讯
    ZHIBO_FEED = (
        "https://zhibo.sina.com.cn/api/zhibo/feed?"
        "page={page}&page_size=30&zhibo_id=152&tag=0&dire=f"
    )
    # 个股新闻
    STOCK_NEWS = (
        "https://feed.mix.sina.com.cn/api/roll/get?"
        "pageid=153&lid=1686&num=30&page={page}&k={code}"
    )
    # 个股公告 (新浪财经 hq.sinajs.cn 不可直接拉, 用 eastmoney)
    # 实时行情
    REALTIME = "https://hq.sinajs.cn/list={codes}"


# 全局配置
DB = DBConfig()
CRAWLER = CrawlerConfig()
REALTIME = RealtimeConfig()
SCHEDULER = SchedulerConfig()
API = APIConfig()
EM_URLS = EastMoneyURLs()
SINA_URLS = SinaURLs()
