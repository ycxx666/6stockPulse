"""Quick test of 6stockPulse DB connection."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DB
from db.database import init_pool
import asyncio


async def test():
    pool = await init_pool()
    r = await pool.fetchval('SELECT count(*) FROM stocks')
    print(f'DB OK — {r} stocks in database')
    await pool.close()


if __name__ == "__main__":
    asyncio.run(test())
