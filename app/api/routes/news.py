"""
api/routes/news.py — 新闻 / 公告 / 爬虫触发
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.schemas import APIResponse
from infrastructure.database import postgres as db
from application.realtime.service import realtime
from app.api._utils import _jsonable
from infrastructure.crawler.sina_news import SinaNewsFeed, SinaStockNews
from infrastructure.crawler.eastmoney import EastMoneyFundFlow, EastMoneySector
from infrastructure.crawler.stock_announcement import StockAnnouncementCrawler
from infrastructure.crawler.global_market import GlobalMarketCrawler

router = APIRouter(tags=["news"])


class CrawlRequest(BaseModel):
    pages: int = 3
    codes: list[str] | None = None


# ────────── 新闻 ──────────
@router.get("/api/news")
async def list_news(limit: int = 30, offset: int = 0,
                     category: str | None = None, stock_code: str | None = None):
    if stock_code:
        rows = await db.fetch_all("""
            SELECT n.* FROM news n
            JOIN stock_news sn ON sn.news_id = n.news_id
            WHERE sn.stock_code = $1
            ORDER BY n.publish_time DESC
            LIMIT $2 OFFSET $3
        """, stock_code, limit, offset)
    else:
        sql = "SELECT * FROM news"
        params: list = []
        if category:
            sql += f" WHERE category = ${len(params)+1}"
            params.append(category)
        sql += f" ORDER BY publish_time DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
        params.extend([limit, offset])
        rows = await db.fetch_all(sql, *params)
    return APIResponse.success(_jsonable(rows))


@router.get("/api/news/{news_id}")
async def get_news(news_id: str):
    row = await db.fetch_one("SELECT * FROM news WHERE news_id = $1", news_id)
    if not row: raise HTTPException(404, "News not found")
    return APIResponse.success(dict(row))


# ────────── 公告 ──────────
@router.get("/api/announcements")
async def list_announcements(stock_code: str | None = None,
                              ann_type: str | None = None,
                              limit: int = 30, offset: int = 0):
    sql = "SELECT * FROM stock_announcements WHERE 1=1"
    params: list = []
    if stock_code:
        sql += f" AND stock_code = ${len(params)+1}"
        params.append(stock_code)
    if ann_type:
        sql += f" AND ann_type = ${len(params)+1}"
        params.append(ann_type)
    sql += f" ORDER BY publish_time DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
    params.extend([limit, offset])
    rows = await db.fetch_all(sql, *params)
    return APIResponse.success(_jsonable(rows))


# ────────── 手动触发爬虫 ──────────
@router.post("/api/crawl/news")
async def trigger_news_crawl(req: CrawlRequest):
    async with SinaNewsFeed() as c:
        result = await c.run(pages=req.pages)
    return result


@router.post("/api/crawl/fund-flow")
async def trigger_fund_flow(req: CrawlRequest):
    async with EastMoneyFundFlow() as c:
        result = await c.run(pages=req.pages)
    return result


@router.post("/api/crawl/sector")
async def trigger_sector_crawl(req: CrawlRequest):
    async with EastMoneySector() as c:
        result = await c.run(pages=req.pages)
    return result


@router.post("/api/crawl/realtime")
async def trigger_realtime_refresh(req: CrawlRequest):
    n = await realtime.force_refresh(req.codes)
    return {"updated": n}


@router.post("/api/crawl/global")
async def trigger_global_crawl(interval: int = 60):
    async with GlobalMarketCrawler(refresh_interval_seconds=interval) as c:
        return await c.run_once()


# ────────── 爬虫日志 ──────────
@router.get("/api/crawl-log")
async def crawl_log(limit: int = 20):
    rows = await db.fetch_all("SELECT * FROM crawl_log ORDER BY started_at DESC LIMIT $1", limit)
    return APIResponse.success(_jsonable(rows))
