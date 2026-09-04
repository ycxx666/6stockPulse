"""
6stockPulse · 新闻采集器
========================
从新浪财经抓取 7×24 快讯和个股新闻，存入 SQLite 数据库。

用法:
  python scripts/news_collector.py                  # 采集 7×24 快讯（默认 5 页）
  python scripts/news_collector.py --pages 10        # 采集 10 页
  python scripts/news_collector.py --stock           # 同时采集个股新闻
  python scripts/news_collector.py --loop 300        # 每 300 秒循环采集
  python scripts/news_collector.py --show            # 查看最近新闻
  python scripts/news_collector.py --stats           # 查看统计
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 把项目根加入 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ─── 设置日志（先于其他 import） ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("news_collector")

import aiohttp

from infrastructure.database import sqlite_news as db
from core.config import SINA_URLS


# ────────── 新浪 7×24 快讯 ──────────

SINA_ZHIBO_URL = "https://zhibo.sina.com.cn/api/zhibo/feed?page={page}&page_size=30&zhibo_id=152&tag=0&dire=f"

def _strip_html(s: str) -> str:
    import re
    s = re.sub(r"<[^>]+>", "", s or "")
    return (s.replace("&nbsp;", " ")
             .replace("&amp;", "&")
             .replace("&lt;", "<")
             .replace("&gt;", ">")
             .replace("&quot;", '"').strip())


def _ts2iso(ts):
    if ts is None:
        return datetime.now().isoformat()
    if isinstance(ts, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(ts.replace("Z", "+00:00"), fmt).isoformat()
            except ValueError:
                continue
        try:
            return datetime.fromtimestamp(int(ts)).isoformat()
        except (ValueError, OSError):
            pass
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts).isoformat()
        except (ValueError, OSError):
            pass
    return datetime.now().isoformat()


def _classify(title: str, content: str) -> str:
    """简单分类: macro / policy / company / global / other"""
    text = (title or "") + (content or "")
    import re
    if re.search(r"央行|美联储|降准|降息|通胀|GDP|宏观|经济", text):
        return "macro"
    if re.search(r"证监会|监管|政策|法规|发布|印发", text):
        return "policy"
    if re.search(r"美股|纳指|标普|美元|黄金|原油|特朗普|美联储", text):
        return "global"
    if re.search(r"\d{6}|股票|公司|股份|集团", text):
        return "company"
    return "other"


async def fetch_7x24(session: aiohttp.ClientSession, pages: int = 5) -> list[dict]:
    """抓取新浪 7×24 快讯"""
    rows: list[dict] = []
    headers = {"Referer": "https://zhibo.sina.com.cn/"}

    for page in range(1, pages + 1):
        url = SINA_ZHIBO_URL.format(page=page)
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        except Exception as e:
            logger.warning("第 %d 页请求失败: %s", page, e)
            continue

        # 解析列表
        items = _extract_zhibo_list(data)
        if not items:
            continue

        for item in items:
            row = _parse_zhibo_item(item)
            if row:
                rows.append(row)

        await asyncio.sleep(0.5)  # 避免请求过快

    logger.info("7×24 快讯采集完成: %d 条", len(rows))
    return rows


def _extract_zhibo_list(data: dict) -> list:
    paths = [
        ["result", "data", "feed", "list"],
        ["data", "feed", "list"],
        ["result", "data", "list"],
        ["list"],
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


def _parse_zhibo_item(item: dict) -> dict | None:
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
                    import json as _json
                    url = (_json.loads(ext) or {}).get("docurl", "")
                except Exception:
                    pass
        source = _extract_source(content)
        category = _classify(title, content)
        return {
            "news_id": news_id,
            "title": title,
            "content": content[:3000],
            "publish_time": publish_time,
            "source": source[:50],
            "source_url": url[:500],
            "category": category,
            "crawl_time": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.debug("解析快讯条目失败: %s", e)
        return None


def _extract_source(content: str) -> str:
    import re
    m = re.search(r"[（(](?:据|来源[：:]?)?([^）)]{1,30})[）)]", content)
    if m:
        s = m.group(1).strip()
        if s and s != "新华社":
            return s
    return "新浪财经"


# ────────── 新浪个股新闻 ──────────

SINA_STOCK_NEWS_URL = (
    "https://feed.mix.sina.com.cn/api/roll/get?"
    "pageid=153&lid=1686&num=30&page={page}&k={code}"
)

# 热门股票代码列表（用于个股新闻采集）
HOT_STOCKS = [
    "sh600519", "sz000858", "sh601318", "sz300750", "sz000333",
    "sz002594", "sh600036", "sh600900", "sh601166", "sz300059",
    "sh600030", "sh600276", "sh601398", "sh600887", "sh601857",
    "sh601012", "sz002415", "sz000651", "sh600809", "sh603259",
    "sz002230", "sz300124", "sz300274", "sz000725", "sz002714",
    "sh688981", "sh601899", "sh601088", "sh600585", "sh601628",
]


async def fetch_stock_news(session: aiohttp.ClientSession, codes: list[str] | None = None,
                           pages_per_code: int = 1) -> list[dict]:
    """抓取个股新闻"""
    if codes is None:
        codes = HOT_STOCKS
    rows: list[dict] = []
    headers = {"Referer": "https://finance.sina.com.cn/"}

    for code in codes:
        for pn in range(1, pages_per_code + 1):
            url = SINA_STOCK_NEWS_URL.format(page=pn, code=code)
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    data = await resp.json()
            except Exception as e:
                logger.debug("个股新闻 %s 第 %d 页失败: %s", code, pn, e)
                continue

            items = data.get("result", {}).get("data", []) or []
            for item in items:
                row = _parse_stock_news_item(code, item)
                if row:
                    rows.append(row)

    logger.info("个股新闻采集完成: %d 条 (涉及 %d 只股票)", len(rows), len(codes))
    return rows


def _parse_stock_news_item(code: str, item: dict) -> dict | None:
    try:
        news_id = str(item.get("id") or item.get("oid") or "").strip()
        if not news_id:
            return None
        title = (item.get("title") or "").strip()
        content = _strip_html(item.get("content") or item.get("intro") or "")
        if not title and not content:
            return None
        ts = item.get("ctime") or item.get("intime")
        publish_time = _ts2iso(ts)
        return {
            "stock_code": code,
            "news_id": news_id,
            "title": title[:200],
            "content": content[:2000],
            "publish_time": publish_time,
            "source": (item.get("media_name") or item.get("source") or "新浪财经")[:50],
            "source_url": (item.get("url") or "")[:500],
            "category": item.get("category") or "company",
            "read_count": int(item.get("attitudes_count", 0) or 0),
            "crawl_time": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.debug("解析个股新闻条目失败: %s", e)
        return None


# ────────── 主采集逻辑 ──────────

async def collect_news(pages: int = 5, collect_stock: bool = False) -> dict:
    """执行一次完整的新闻采集"""
    db.init_db()
    result = {"7x24": 0, "stock_news": 0}

    async with aiohttp.ClientSession() as session:
        # 1) 采集 7×24 快讯
        news_rows = await fetch_7x24(session, pages=pages)
        if news_rows:
            inserted = db.insert_news_batch(news_rows)
            result["7x24"] = inserted
            logger.info("7×24 快讯: 采集 %d 条, 新增 %d 条", len(news_rows), inserted)

        # 2) 可选：采集个股新闻
        if collect_stock:
            stock_rows = await fetch_stock_news(session, pages_per_code=1)
            if stock_rows:
                # 先写入主表
                main_rows = []
                for r in stock_rows:
                    main_rows.append({
                        "news_id": r["news_id"],
                        "title": r["title"],
                        "content": r.get("content"),
                        "publish_time": r["publish_time"],
                        "source": r["source"],
                        "source_url": r["source_url"],
                        "category": "company",
                        "crawl_time": r["crawl_time"],
                    })
                db.insert_news_batch(main_rows)
                # 写入个股新闻表
                inserted = db.insert_stock_news_batch(stock_rows)
                result["stock_news"] = inserted
                logger.info("个股新闻: 采集 %d 条, 新增 %d 条", len(stock_rows), inserted)

    return result


async def collect_loop(interval: int = 300, pages: int = 5, collect_stock: bool = False):
    """循环采集模式"""
    logger.info("启动循环采集模式，间隔 %d 秒", interval)
    db.init_db()
    round_num = 0
    while True:
        round_num += 1
        logger.info("=== 第 %d 轮采集开始 ===", round_num)
        try:
            result = await collect_news(pages=pages, collect_stock=collect_stock)
            stats = db.get_news_stats()
            logger.info("=== 第 %d 轮完成: 新增 7×24=%d, 个股=%d | 总数: %d ===",
                        round_num, result["7x24"], result["stock_news"],
                        stats.get("total_news", 0))
        except Exception as e:
            logger.exception("第 %d 轮采集出错: %s", round_num, e)
        logger.info("等待 %d 秒...", interval)
        await asyncio.sleep(interval)


# ────────── CLI 入口 ──────────

def main():
    parser = argparse.ArgumentParser(description="6stockPulse 新闻采集器")
    parser.add_argument("--pages", type=int, default=5, help="采集页数（默认 5）")
    parser.add_argument("--stock", action="store_true", help="同时采集个股新闻")
    parser.add_argument("--loop", type=int, default=0, help="循环采集间隔秒数（0=单次）")
    parser.add_argument("--show", action="store_true", help="查看最近新闻")
    parser.add_argument("--stats", action="store_true", help="查看数据库统计")
    args = parser.parse_args()

    if args.show:
        db.init_db()
        db.show_recent(20)
        return

    if args.stats:
        db.init_db()
        stats = db.get_news_stats()
        print(f"\n📊 新闻数据库统计")
        print(f"{'='*40}")
        print(f"  数据库路径: {stats.get('db_path', '?')}")
        print(f"  新闻总数:   {stats.get('total_news', 0)}")
        print(f"  今日新增:   {stats.get('today', 0)}")
        print(f"  分类分布:   {stats.get('by_category', {})}")
        print(f"{'='*40}\n")
        return

    if args.loop > 0:
        asyncio.run(collect_loop(
            interval=args.loop,
            pages=args.pages,
            collect_stock=args.stock,
        ))
    else:
        asyncio.run(collect_news(
            pages=args.pages,
            collect_stock=args.stock,
        ))
        db.show_recent(10)


if __name__ == "__main__":
    main()
