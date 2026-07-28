"""
6stockPulse · 新浪财经爬虫
============================
  - SinaNewsFeed   : 7×24 直播快讯 (全市场滚动新闻)
  - SinaStockNews  : 个股新闻 (按 stock_code)
"""
from __future__ import annotations
import re
import json
import logging
from datetime import datetime
from typing import Any

from .base import BaseCrawler
from config import SINA_URLS
from db import database as db

logger = logging.getLogger(__name__)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return (s.replace("&nbsp;", " ")
             .replace("&amp;", "&")
             .replace("&lt;", "<")
             .replace("&gt;", ">")
             .replace("&quot;", '"').strip())


def _ts2iso(ts):
    """转换时间戳 → datetime。支持 ISO 字符串和 Unix 整数时间戳"""
    if ts is None:
        return datetime.now()
    # 1) ISO 格式字符串 (如 "2026-07-18 19:55:07")
    if isinstance(ts, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(ts.replace("Z", "+00:00"), fmt)
            except ValueError:
                continue
        # 可能是纯数字时间戳字符串
        try:
            return datetime.fromtimestamp(int(ts))
        except (ValueError, OSError):
            pass
    # 2) Unix 整数/浮点时间戳
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError):
            pass
    logger.debug("_ts2iso fallback for ts=%r", ts)
    return datetime.now()


def _classify(title: str, content: str) -> str:
    """简单分类: macro / policy / company / global / other"""
    text = (title or "") + (content or "")
    if re.search(r"央行|美联储|降准|降息|通胀|GDP|宏观|经济", text):
        return "macro"
    if re.search(r"证监会|监管|政策|法规|发布|印发", text):
        return "policy"
    if re.search(r"美股|纳指|标普|美元|黄金|原油|特朗普|美联储", text):
        return "global"
    if re.search(r"\d{6}|股票|公司|股份|集团", text):
        return "company"
    return "other"


class SinaNewsFeed(BaseCrawler):
    """新浪 7×24 快讯"""
    name = "sina_news_feed"

    async def run(self, pages: int = 5) -> dict:
        await self.start_log({"type": "news_7x24", "pages": pages})
        all_rows: list[dict] = []
        success, fail = 0, 0
        try:
            for page in range(1, pages + 1):
                url = SINA_URLS.ZHIBO_FEED.format(page=page)
                data = await self.fetch(url)
                if not data:
                    continue
                items = self._extract_list(data)
                if not items:
                    continue
                for it in items:
                    row = self._parse(it)
                    if row:
                        all_rows.append(row)
            if all_rows:
                inserted = await db.upsert_many(
                    "news", all_rows,
                    conflict_target="news_id",
                    update_cols=["title", "content", "category", "source", "source_url"]
                )
                success = inserted
        except Exception as e:
            fail = 1
            logger.exception("[%s] 异常: %s", self.name, e)
            await self.finish_log(success, 0, fail, str(e))
            return {"success": success, "fail": fail, "error": str(e)}
        await self.finish_log(success, 0, fail)
        return {"success": success, "fail": fail, "total": len(all_rows)}

    @staticmethod
    def _extract_list(data: dict) -> list:
        paths = [
            ['result', 'data', 'feed', 'list'],
            ['data', 'feed', 'list'],
            ['result', 'data', 'list'],
            ['list'],
        ]
        for path in paths:
            cur = data
            try:
                for k in path:
                    cur = cur[k]
                if isinstance(cur, list):
                    return cur
            except (KeyError, TypeError):
                continue
        return []

    def _parse(self, item: dict) -> dict | None:
        try:
            if not isinstance(item, dict):
                return None
            news_id = str(item.get("id") or item.get("news_id") or "").strip()
            if not news_id:
                return None
            rich = item.get("rich_text", "") or ""
            content = _strip_html(rich)
            if not content:
                return None
            title = content[:200]
            ts = item.get("create_time") or item.get("ctime") or item.get("time")
            publish_time = _ts2iso(ts)
            url = (item.get("docurl") or "").strip()
            if not url:
                ext = item.get("ext")
                if isinstance(ext, str) and ext.strip().startswith("{"):
                    try:
                        url = (json.loads(ext) or {}).get("docurl", "")
                    except Exception:
                        pass
            source = self._extract_source(content) or "新浪财经"
            category = _classify(title, content)
            return {
                "news_id":      news_id,
                "title":        title,
                "content":      content[:3000],
                "publish_time": publish_time,
                "source":       source[:50],
                "source_url":   url[:500],
                "category":     category,
                "crawl_time":   datetime.now(),
            }
        except Exception as e:
            logger.debug("[news] parse error: %s", e)
            return None

    @staticmethod
    def _extract_source(content: str) -> str:
        m = re.search(r"[（(](?:据|来源[：:]?)?([^）)]{1,30})[）)]", content)
        if m:
            s = m.group(1).strip()
            if s and s != "新华社":
                return s
        return "新浪财经"


class SinaStockNews(BaseCrawler):
    """新浪个股新闻 (按 stock_code 拉取)"""
    name = "sina_stock_news"

    async def run(self, stock_codes: list[str] | None = None,
                  pages_per_code: int = 2) -> dict:
        """
        stock_codes: 关注的股票代码列表 (None = 从 DB 拉关注度 TOP 50)
        """
        await self.start_log({"type": "stock_news"})
        all_rows: list[dict] = []
        success, fail = 0, 0
        try:
            if not stock_codes:
                stock_codes = await self._top_stocks(50)
            for code in stock_codes:
                for pn in range(1, pages_per_code + 1):
                    url = SINA_URLS.STOCK_NEWS.format(page=pn, code=code)
                    data = await self.fetch(url)
                    if not data:
                        continue
                    items = self._extract_items(data)
                    for it in items:
                        row = self._parse(code, it)
                        if row:
                            all_rows.append(row)
            if all_rows:
                # 关联主表 news (若 title 在 news 中已存在则跳过)
                main_rows = []
                for r in all_rows:
                    main_rows.append({
                        "news_id": r["news_id"],
                        "title":   r["title"],
                        "content": r.get("content"),
                        "publish_time": r["publish_time"],
                        "source":  r["source"],
                        "source_url": r["source_url"],
                        "category": "company",
                        "crawl_time": r["crawl_time"],
                    })
                await db.upsert_many("news", main_rows,
                                     conflict_target="news_id",
                                     update_cols=["title", "content"])
                success = await db.insert_many(
                    "stock_news", all_rows,
                    conflict_target="stock_code, news_id"
                )
        except Exception as e:
            fail = 1
            logger.exception("[%s] 异常: %s", self.name, e)
            await self.finish_log(success, 0, fail, str(e))
            return {"success": success, "fail": fail, "error": str(e)}
        await self.finish_log(success, 0, fail)
        return {"success": success, "fail": fail, "total": len(all_rows)}

    async def _top_stocks(self, n: int) -> list[str]:
        rows = await db.fetch_all("""
            SELECT stock_code FROM stocks WHERE is_active = TRUE
            ORDER BY stock_code LIMIT $1
        """, n)
        return [r["stock_code"] for r in rows]

    @staticmethod
    def _extract_items(data: dict) -> list:
        return data.get("result", {}).get("data", []) or []

    def _parse(self, code: str, it: dict) -> dict | None:
        try:
            news_id = str(it.get("id") or it.get("oid") or "").strip()
            if not news_id:
                return None
            title = (it.get("title") or "").strip()
            content = _strip_html(it.get("content") or it.get("intro") or "")
            if not title and not content:
                return None
            ts = it.get("ctime") or it.get("intime")
            publish_time = _ts2iso(ts)
            return {
                "stock_code":   code,
                "news_id":      news_id,
                "title":        title[:200],
                "content":      content[:2000],
                "publish_time": publish_time,
                "source":       (it.get("media_name") or it.get("source") or "新浪财经")[:50],
                "source_url":   (it.get("url") or "")[:500],
                "category":     it.get("category") or "company",
                "read_count":   int(it.get("attitudes_count", 0) or 0),
                "crawl_time":   datetime.now(),
            }
        except Exception as e:
            logger.debug("[stock_news] parse error: %s", e)
            return None

    # ─── 全文抓取 (Phase 3) ───
    async def fetch_full_content(self, url: str) -> str | None:
        """抓取新闻正文 (从文章页面)"""
        if not url:
            return None
        try:
            html = await self.fetch(url, as_json=False)
            if not html:
                return None
            # 解析正文: 新浪 #artibody / #content, 通用 <article>
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            # 多个候选选择器
            for selector in ["#artibody", "#content", "article", ".article-content", ".news-content", ".content"]:
                el = soup.select_one(selector)
                if el:
                    # 去除 script/style
                    for tag in el.find_all(["script", "style", "iframe"]):
                        tag.decompose()
                    text = el.get_text(separator="\n", strip=True)
                    if len(text) > 100:
                        return text[:5000]
            # 降级: 取 <body> 中较长段落
            body = soup.find("body")
            if body:
                for tag in body.find_all(["script", "style"]):
                    tag.decompose()
                text = body.get_text(separator="\n", strip=True)
                return text[:5000]
        except Exception as e:
            logger.debug("[full_content] %s: %s", url[:60], e)
        return None


# ────────── 便捷入口 ──────────
async def enrich_news_content(limit: int = 20):
    """补全最近新闻的正文内容 (从 source_url 抓取)"""
    from db import database as db
    rows = await db.fetch_all("""
        SELECT news_id, source_url
        FROM news
        WHERE (content IS NULL OR length(content) < 100)
          AND source_url IS NOT NULL AND source_url != ''
        ORDER BY publish_time DESC
        LIMIT $1
    """, limit)

    if not rows:
        return {"enriched": 0}

    async with SinaStockNews() as crawler:
        enriched = 0
        for r in rows:
            url = r.get("source_url")
            if not url:
                continue
            content = await crawler.fetch_full_content(url)
            if content:
                await db.execute(
                    "UPDATE news SET content = $2 WHERE news_id = $1",
                    r["news_id"], content
                )
                enriched += 1
        return {"enriched": enriched, "total": len(rows)}
