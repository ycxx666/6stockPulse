"""
6StockPulse · RSS 新闻聚合采集器
================================
读取 config/news_sources.json 中登记的 RSS/Atom 源，
并发抓取并解析后存入 SQLite 新闻库 (data/news.db)。

用法:
  python scripts/rss_collector.py                      # 采集全部 ok/verified 源
  python scripts/rss_collector.py --region US           # 只采集美国源
  python scripts/rss_collector.py --status verified     # 只采集实测可用源
  python scripts/rss_collector.py --name "CNBC"         # 按名称过滤
  python scripts/rss_collector.py --limit 5             # 最多采集 5 个源
  python scripts/rss_collector.py --concurrency 10      # 并发数
  python scripts/rss_collector.py --check-sources       # 只检测源可用性, 不入库
  python scripts/rss_collector.py --show                # 查看最近入库新闻
  python scripts/rss_collector.py --stats               # 查看数据库统计
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# 把项目根加入 path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ─── 设置日志（先于其他 import） ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rss_collector")

import aiohttp

from infrastructure.database import sqlite_news as db

# 配置文件路径
CONFIG_PATH = ROOT / "config" / "news_sources.json"

# 允许的采集状态（URL 已被验证/属官方格式）
ALLOWED_STATUS = {"ok", "verified"}

# RSS/Atom 常用命名空间
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 6StockPulse/1.0"
)


# ────────── 配置读取 ──────────

def load_sources(
    region: str | None = None,
    status: str | None = None,
    name: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """从 news_sources.json 加载源，支持过滤"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    sources = data["sources"]

    if region:
        region = region.upper()
        sources = [s for s in sources if s.get("region", "").upper() == region]
    if status:
        sources = [s for s in sources if s.get("status") == status]
    if name:
        sources = [s for s in sources if name.lower() in s.get("name", "").lower()]
    if limit:
        sources = sources[:limit]
    logger.info("待采集源 %d 个", len(sources))
    return sources


# ────────── HTML/时间/分类工具 ──────────

def _strip_html(s: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">")
          .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", s).strip()


def _parse_date(value: str | None) -> str:
    """解析 RSS/Atom 时间字段 → ISO 字符串"""
    if not value:
        return datetime.now().isoformat()
    v = value.strip()
    # RFC 822: Sat, 27 Jul 2026 10:00:00 GMT
    try:
        return parsedate_to_datetime(v).isoformat()
    except Exception:
        pass
    # ISO 8601
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
    except Exception:
        pass
    # 纯数字时间戳
    if v.isdigit():
        try:
            return datetime.fromtimestamp(int(v)).isoformat()
        except Exception:
            pass
    return datetime.now().isoformat()


def _classify(title: str, content: str) -> str:
    """简单分类: macro / policy / company / global / other"""
    text = (title or "") + (content or "")
    if re.search(r"央行|美联储|降准|降息|通胀|GDP|宏观|经济数据|利率决议", text):
        return "macro"
    if re.search(r"证监会|监管|政策|法规|印发|国常会|立案", text):
        return "policy"
    if re.search(r"美股|纳指|标普|美元|黄金|原油|特朗普|鲍威尔|比特币|BTC", text):
        return "global"
    if re.search(r"(?:^|[^A-Za-z])\d{6}|股票|公司|股份|集团|股份|财报|业绩", text):
        return "company"
    return "other"


def _guid(link: str, title: str, fallback: str) -> str:
    """生成稳定的 news_id：优先用链接，其次标题+fallback 哈希"""
    if link:
        return link[:200]
    raw = f"{title}|{fallback}"
    return f"rss:{hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]}"


# ────────── RSS / Atom 解析 ──────────

def _tag(tag: str) -> str:
    """去掉命名空间前缀"""
    return tag.rsplit("}", 1)[-1]


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child_text(item: ET.Element, *names: str) -> str:
    """按本地名取子节点文本（兼容命名空间）"""
    for child in item:
        if _local_name(child) in names:
            return (child.text or "").strip()
    return ""


def _child_attr(item: ET.Element, name: str, attr: str) -> str:
    for child in item:
        if _local_name(child) == name:
            return child.get(attr, "") or ""
    return ""


def parse_feed(xml_text: str) -> list[dict]:
    """解析 RSS 2.0 / RSS 1.0 / Atom XML → 条目 dict 列表"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("XML 解析失败: %s", e)
        return []

    root_tag = _tag(root.tag)
    items: list[ET.Element] = []

    if root_tag == "rss":  # RSS 2.0
        channel = root.find("channel")
        if channel is None:
            return []
        items = [c for c in channel if _local_name(c) == "item"]
        return _parse_rss_items(items)

    if root_tag == "feed":  # Atom
        items = [c for c in root if _local_name(c) == "entry"]
        return _parse_atom_items(items)

    if root_tag in ("RDF", "rdf"):  # RSS 1.0 (RDF 格式)
        items = [c for c in root if _local_name(c) == "item"]
        return _parse_rss_items(items)

    logger.warning("未知 XML 根节点: %s", root_tag)
    return []


def _parse_rss_items(items: list[ET.Element]) -> list[dict]:
    rows = []
    for it in items:
        title = _child_text(it, "title")
        if not title:
            continue
        link = _child_text(it, "link")
        # RSS 2.0 的 guid 带 isPermaLink 属性
        guid = ""
        for child in it:
            if _local_name(child) == "guid":
                guid = (child.text or "").strip()
        desc = _child_text(it, "description", "encoded") or _child_text(it, "description")
        # content:encoded 可能带命名空间
        content = ""
        for child in it:
            if _local_name(child) == "encoded":
                content = (child.text or "").strip()
        pub = _child_text(it, "pubDate", "date", "published") or _child_text(it, "pubDate")
        if not pub:
            for child in it:
                if _local_name(child) in ("pubDate", "date"):
                    pub = (child.text or "").strip()

        rows.append(_make_row(
            news_id=guid or _guid(link, title, pub or datetime.now().isoformat()),
            title=title,
            content=content or desc,
            publish_time=_parse_date(pub),
            link=link,
        ))
    return rows


def _parse_atom_items(items: list[ET.Element]) -> list[dict]:
    rows = []
    for it in items:
        title = _child_text(it, "title")
        if not title:
            continue
        link = _child_attr(it, "link", "href") or _child_text(it, "link")
        summary = _child_text(it, "summary")
        content = _child_text(it, "content")
        pub = _child_text(it, "published", "updated")
        news_id = ""
        for child in it:
            if _local_name(child) == "id":
                news_id = (child.text or "").strip()

        rows.append(_make_row(
            news_id=news_id or _guid(link, title, pub or datetime.now().isoformat()),
            title=title,
            content=content or summary,
            publish_time=_parse_date(pub),
            link=link,
        ))
    return rows


def _make_row(news_id, title, content, publish_time, link) -> dict:
    body = _strip_html(content or "")
    category = _classify(title, body)
    return {
        "news_id": (news_id or _guid(link, title, publish_time))[:200],
        "title": title[:300],
        "content": body[:5000] or title[:300],
        "summary": None,
        "publish_time": publish_time,
        "source_url": (link or "")[:500],
        "category": category,
        "sentiment": None,
        "importance": 0,
        "crawl_time": datetime.now().isoformat(),
    }


# ────────── 抓取 ──────────

async def fetch_one(session: aiohttp.ClientSession, src: dict,
                    timeout: int = 12) -> tuple[dict, list[dict], str | None]:
    """抓取并解析单个源。返回 (源, 条目, 错误)"""
    url = src.get("url", "")
    if not url:
        return src, [], "empty-url"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    try:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                return src, [], f"http-{resp.status}"
            text = await resp.text(errors="ignore")
    except Exception as e:
        return src, [], f"fetch-error:{type(e).__name__}"

    rows = parse_feed(text)
    return src, rows, None


async def collect_sources(
    sources: list[dict],
    concurrency: int = 5,
    only_status: tuple[str, ...] = ALLOWED_STATUS,
) -> dict:
    """并发采集多个源，返回统计。默认只采集 ok/verified 状态源"""
    db.init_db()
    sem = asyncio.Semaphore(concurrency)
    total_new = 0
    summary = {"ok_sources": 0, "failed_sources": 0, "items": 0, "inserted": 0, "failures": []}

    async def worker(src: dict):
        nonlocal total_new
        async with sem:
            if src.get("status") not in only_status:
                return
            _, rows, err = await fetch_one(session, src)
            if err:
                summary["failed_sources"] += 1
                summary["failures"].append((src.get("name", "?"), err))
                logger.debug("源 %s 失败: %s", src.get("name"), err)
                return
            summary["ok_sources"] += 1
            summary["items"] += len(rows)
            if not rows:
                return
            # 关联来源信息
            for r in rows:
                r["source"] = src.get("name", "")[:50]
            inserted = db.insert_news_batch(rows)
            total_new += inserted
            logger.info("源 %s: 解析 %d 条, 新增 %d", src.get("name"), len(rows), inserted)

    async with aiohttp.ClientSession() as session:
        tasks = [asyncio.create_task(worker(s)) for s in sources]
        await asyncio.gather(*tasks, return_exceptions=True)

    summary["inserted"] = total_new
    return summary


async def check_sources(sources: list[dict], concurrency: int = 10) -> None:
    """只检测源可达性，不入库"""
    sem = asyncio.Semaphore(concurrency)

    async def probe(src: dict):
        async with sem:
            _, rows, err = await fetch_one(session, src, timeout=10)
            mark = "✅" if not err else "❌"
            detail = f"{len(rows)} 条" if not err else err
            logger.info("%s [%s] %s → %s", mark, src.get("region"), src.get("name"), detail)

    async with aiohttp.ClientSession() as session:
        tasks = [asyncio.create_task(probe(s)) for s in sources]
        await asyncio.gather(*tasks, return_exceptions=True)


# ────────── CLI 入口 ──────────

def main():
    parser = argparse.ArgumentParser(description="6stockPulse RSS 新闻聚合采集器")
    parser.add_argument("--region", help="地区过滤: GLOBAL/US/CN")
    parser.add_argument("--status", help="状态过滤: ok/verified/needs-proxy/incomplete")
    parser.add_argument("--name", help="按源名包含匹配")
    parser.add_argument("--limit", type=int, help="最多采集 N 个源")
    parser.add_argument("--concurrency", type=int, default=5, help="并发数（默认 5）")
    parser.add_argument("--all-status", action="store_true", help="忽略状态，采集全部源")
    parser.add_argument("--check-sources", action="store_true", help="只检测源可用性")
    parser.add_argument("--show", action="store_true", help="查看最近入库新闻")
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

    sources = load_sources(region=args.region, status=args.status,
                           name=args.name, limit=args.limit)

    if not sources:
        logger.error("没有匹配的新闻源，请检查过滤条件")
        return

    if args.check_sources:
        asyncio.run(check_sources(sources, concurrency=args.concurrency))
        return

    if args.all_status:
        only = ALLOWED_STATUS  # 仍然跳过不完整 URL
    else:
        only = ALLOWED_STATUS
    summary = asyncio.run(collect_sources(sources, concurrency=args.concurrency))
    logger.info("=== 采集完成: 成功源 %d, 失败源 %d, 解析条目 %d, 新增入库 %d ===",
                summary["ok_sources"], summary["failed_sources"],
                summary["items"], summary["inserted"])
    if summary["failures"]:
        shown = summary["failures"][:10]
        logger.info("失败源示例: %s", shown)
    db.show_recent(10)


if __name__ == "__main__":
    main()
