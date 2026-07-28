#!/usr/bin/env python3
"""
scripts/test_all_crawlers.py
===============================
逐一测试 8 个爬虫: 连通性 + 数据解析 + 条目数
"""
from __future__ import annotations
import sys, asyncio, logging, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("crawler_test")

RESULTS: dict[str, dict] = {}


async def test_one(name: str, coro):
    """运行一个爬虫测试，捕获所有异常"""
    t0 = time.monotonic()
    try:
        result = await coro
        elapsed = round(time.monotonic() - t0, 2)
        RESULTS[name] = {"ok": True, "elapsed": elapsed, **result}
        logger.info("✅ %s: %s (%.1fs)", name, {k: v for k, v in result.items() if k != "error"}, elapsed)
    except Exception as e:
        elapsed = round(time.monotonic() - t0, 2)
        RESULTS[name] = {"ok": False, "elapsed": elapsed, "error": str(e)[:200]}
        logger.error("❌ %s: %s (%.1fs)", name, e, elapsed)


async def main():
    await db.init_pool()

    # ── 1. SinaNewsFeed (新浪7×24快讯) ──
    logger.info("── Testing SinaNewsFeed ──")
    from crawlers.sina_news import SinaNewsFeed
    async def t1():
        async with SinaNewsFeed() as c:
            return await c.run(pages=1)
    await test_one("SinaNewsFeed", t1())

    # ── 2. SinaStockNews (新浪个股新闻) ──
    logger.info("── Testing SinaStockNews ──")
    from crawlers.sina_news import SinaStockNews
    async def t2():
        async with SinaStockNews() as c:
            return await c.run(stock_codes=["600519"], pages_per_code=1)
    await test_one("SinaStockNews", t2())

    # ── 3. EastMoneyFundFlow (东方财富资金流) ──
    logger.info("── Testing EastMoneyFundFlow ──")
    from crawlers.eastmoney import EastMoneyFundFlow
    async def t3():
        async with EastMoneyFundFlow() as c:
            return await c.run(pages=1)
    await test_one("EastMoneyFundFlow", t3())

    # ── 4. EastMoneySector (东方财富板块) ──
    logger.info("── Testing EastMoneySector ──")
    from crawlers.eastmoney import EastMoneySector
    async def t4():
        async with EastMoneySector() as c:
            return await c.run(pages=1)
    await test_one("EastMoneySector", t4())

    # ── 5. EastMoneyRealtime (实时行情) ──
    logger.info("── Testing EastMoneyRealtime ──")
    from crawlers.eastmoney import EastMoneyRealtime
    async def t5():
        async with EastMoneyRealtime() as c:
            return await c.run(stock_codes=["600519", "000001", "300750"], top_n=3)
    await test_one("EastMoneyRealtime", t5())

    # ── 6. GlobalMarketCrawler (全球行情) ──
    logger.info("── Testing GlobalMarketCrawler ──")
    from crawlers.global_market import GlobalMarketCrawler
    async def t6():
        async with GlobalMarketCrawler(refresh_interval_seconds=60) as c:
            return await c.run_once()
    await test_one("GlobalMarketCrawler", t6())

    # ── 7. StockAnnouncementCrawler (个股公告) ──
    logger.info("── Testing StockAnnouncementCrawler ──")
    from crawlers.stock_announcement import StockAnnouncementCrawler
    async def t7():
        async with StockAnnouncementCrawler() as c:
            return await c.run(stock_codes=["600519"], pages_per_code=1)
    await test_one("StockAnnouncementCrawler", t7())

    # ── 8. EastMoneyFundFlowHistory (资金流K线回填) ──
    logger.info("── Testing EastMoneyFundFlowHistory ──")
    from crawlers.eastmoney import EastMoneyFundFlowHistory
    async def t8():
        async with EastMoneyFundFlowHistory() as c:
            return await c.run(stock_codes=["600519"], days=3)
    await test_one("EastMoneyFundFlowHistory", t8())

    await db.close_pool()

    # ── 汇总 ──
    print("\n" + "=" * 70)
    print("CRAWLER TEST SUMMARY")
    print("=" * 70)
    passed = sum(1 for r in RESULTS.values() if r["ok"])
    failed = len(RESULTS) - passed
    for name, r in RESULTS.items():
        icon = "✅" if r["ok"] else "❌"
        info = f"elapsed={r.get('elapsed',0)}s"
        if r.get("total"):
            info += f" total={r['total']}"
        if r.get("success"):
            info += f" success={r['success']}"
        if r.get("fetched"):
            info += f" fetched={r['fetched']}"
        if r.get("processed"):
            info += f" processed={r['processed']}"
        if r.get("error"):
            info += f" error={r['error'][:80]}"
        print(f"  {icon} {name:<35s} {info}")
    print(f"\n  Passed: {passed}/{len(RESULTS)}  Failed: {failed}/{len(RESULTS)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
