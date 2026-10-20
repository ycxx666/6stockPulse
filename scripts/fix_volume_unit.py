"""修正旧记录成交量单位：turnover_rate 为 null 的记录（新浪旧代码，单位股）除以 100 转手"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database import postgres as db


async def main():
    await db.init_pool()
    try:
        n = await db.execute("""
            UPDATE stock_prices
            SET volume = volume / 100
            WHERE turnover_rate IS NULL AND volume IS NOT NULL AND volume > 0
        """)
        print(f"updated rows: {n}")
        # 校验：600519 最近记录
        rows = await db.fetch_all("""
            SELECT trade_time, volume, amount, turnover_rate
            FROM stock_prices WHERE stock_code = '600519'
            ORDER BY trade_time DESC LIMIT 4
        """)
        for r in rows:
            print(dict(r))
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
