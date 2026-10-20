"""手动触发情绪计算并验证写入"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database import postgres as db
from db import database as db_service
from services.sentiment_service import compute_all


async def main():
    await db.init_pool()
    await db_service.init_pool()
    try:
        result = await compute_all()
        ashare = result["ashare"]
        print("ashare score:", ashare["score"], ashare["label"])
        print("涨跌停强度:", ashare["factors"].get("涨跌停强度"))
        print("global score:", result["global"]["score"])
    finally:
        await db.close_pool()
        await db_service.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
