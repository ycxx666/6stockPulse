"""
6stockPulse · 个股公告爬虫
============================
  - 拉取上市公司公告 (东方财富)
  - 公告类型: 临时公告 / 定期报告 / 重大事项等
"""
from __future__ import annotations
import re
import json
import logging
from datetime import datetime, date
from typing import Any

from infrastructure.crawler.base import BaseCrawler
from core.config import EM_URLS
from infrastructure.database import postgres as db

logger = logging.getLogger(__name__)


def _strip_jsonp(text: str) -> str:
    """去除 jQuery JSONP 包裹"""
    m = re.search(r"\((.*)\)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _ts2date(ts):
    try:
        return datetime.fromtimestamp(int(ts) / 1000).date()
    except Exception:
        return date.today()


class StockAnnouncementCrawler(BaseCrawler):
    """东方财富个股公告采集"""
    name = "stock_announcement"

    async def run(self, stock_codes: list[str] | None = None,
                  pages_per_code: int = 2) -> dict:
        """
        stock_codes: None = 拉关注度 TOP 50
        """
        await self.start_log({"type": "announcement"})
        all_rows: list[dict] = []
        success, fail = 0, 0
        try:
            if not stock_codes:
                stock_codes = await self._top_stocks(50)
            for code in stock_codes:
                for pn in range(1, pages_per_code + 1):
                    url = EM_URLS.STOCK_NEWS.format(pn=pn, code=code)
                    raw = await self.fetch(url, as_json=False)
                    if not raw:
                        continue
                    cleaned = _strip_jsonp(raw)
                    try:
                        data = json.loads(cleaned)
                    except Exception:
                        continue
                    items = self._extract_list(data)
                    for it in items:
                        row = self._parse(code, it)
                        if row:
                            all_rows.append(row)
            if all_rows:
                success = await db.insert_many(
                    "stock_announcements", all_rows,
                    conflict_target="stock_code, ann_id"
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
    def _extract_list(data: dict) -> list:
        # 实际接口结构: data["data"]["list"] 或 data["data"]["notice_list"]
        for path in [['data', 'list'], ['data', 'notice_list'], ['data', 'items'], ['list']]:
            cur = data
            try:
                for k in path:
                    cur = cur[k]
                if isinstance(cur, list):
                    return cur
            except (KeyError, TypeError):
                continue
        return []

    def _parse(self, code: str, it: dict) -> dict | None:
        try:
            art_code = str(it.get("art_code") or it.get("notice_id") or "").strip()
            if not art_code:
                return None
            title = (it.get("title") or it.get("notice_title") or "").strip()
            if not title:
                return None
            # 公告类型
            ann_type = (it.get("columns_name") or it.get("notice_type") or "公告").strip()
            ts = it.get("notice_time") or it.get("notice_date") or it.get("ctime")
            publish = _ts2date(ts) if ts else date.today()
            # 拼接 PDF URL
            pdf_url = ""
            source_url = ""
            if it.get("notice_url"):
                source_url = it["notice_url"]
            if it.get("art_code"):
                pdf_url = f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"
            return {
                "stock_code":   code,
                "ann_id":       art_code,
                "title":        title[:300],
                "content":      (it.get("notice_content") or "")[:5000],
                "publish_time": publish,
                "ann_type":     ann_type[:50],
                "pdf_url":      pdf_url[:500],
                "source_url":   source_url[:500],
                "crawl_time":   datetime.now(),
            }
        except Exception as e:
            logger.debug("[ann] parse error: %s", e)
            return None
