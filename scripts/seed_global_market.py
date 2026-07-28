"""Seed global market data into market_quote_snapshot"""
import sys, asyncio, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import database as db
from crawlers.global_market import seed_global_snapshots

async def main():
    logging.basicConfig(level=logging.INFO)
    await db.init_pool()
    await seed_global_snapshots()
    # Verify
    rows = await db.fetch_all("SELECT symbol, name, asset_type FROM market_quote_snapshot ORDER BY asset_type, symbol")
    for r in rows:
        print(f"  {r['symbol']:20s} {r['asset_type']:12s} {r['name']}")
    print(f"\nTotal: {len(rows)} symbols")
    await db.close_pool()

asyncio.run(main())
