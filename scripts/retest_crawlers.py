"""Quick re-test of fixed crawlers"""
import sys, asyncio, logging
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from db import database as db

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("retest")

async def main():
    await db.init_pool()

    # Test 1: SinaNewsFeed (fetch + upsert)
    from crawlers.sina_news import SinaNewsFeed
    async with SinaNewsFeed() as c:
        r = await c.run(pages=1)
    print(f"  SinaNewsFeed: total={r.get('total',0)} success={r.get('success',0)} fail={r.get('fail',0)}")

    # Test 2: EastMoneyFundFlow (fetch + insert)
    from crawlers.eastmoney import EastMoneyFundFlow
    async with EastMoneyFundFlow() as c:
        r = await c.run(pages=1)
    print(f"  EastMoneyFundFlow: total={r.get('total',0)} success={r.get('success',0)} fail={r.get('fail',0)}")

    # Test 3: EastMoneySector
    from crawlers.eastmoney import EastMoneySector
    async with EastMoneySector() as c:
        r = await c.run(pages=1)
    print(f"  EastMoneySector: total={r.get('total',0)} success={r.get('success',0)} fail={r.get('fail',0)}")

    # Test 4: StockAnnouncementCrawler
    from crawlers.stock_announcement import StockAnnouncementCrawler
    async with StockAnnouncementCrawler() as c:
        r = await c.run(stock_codes=["600519"], pages_per_code=1)
    print(f"  StockAnnouncement: total={r.get('total',0)} success={r.get('success',0)} fail={r.get('fail',0)}")

    # Test 5: EastMoneyFundFlowHistory
    from crawlers.eastmoney import EastMoneyFundFlowHistory
    async with EastMoneyFundFlowHistory() as c:
        r = await c.run(stock_codes=["600519"], days=3)
    print(f"  FundFlowHistory: total={r.get('total',0)} success={r.get('success',0)} fail={r.get('fail',0)}")

    await db.close_pool()
    print("\nDone.")

asyncio.run(main())
