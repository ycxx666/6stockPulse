"""
从腾讯日K接口补充近 N 个交易日的日K数据到 stock_prices。
用法：
  python scripts/backfill_daily_kline.py            # 全市场（耗时较长）
  python scripts/backfill_daily_kline.py 600519 000001  # 指定股票
"""
import asyncio
import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database import postgres as db

MARKET_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}
DAYS = 5


def fetch_day_kline(code: str, market: str, days: int = DAYS) -> list[dict]:
    sym = MARKET_PREFIX.get(market, "sh") + code
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,,,{days},qfq")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        node = data.get("data", {}).get(sym, {})
        rows = node.get("qfqday") or node.get("day") or []
        out = []
        for row in rows:
            if len(row) < 6:
                continue
            out.append({
                "trade_date": date.fromisoformat(row[0]),
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": int(float(row[5])),  # 腾讯日K成交量单位=手
                "trade_time": datetime.fromisoformat(row[0] + " 15:00:00"),
            })
        return out
    except Exception:
        return []


async def main():
    await db.init_pool()
    try:
        codes = [c.strip() for c in sys.argv[1:] if c.strip()]
        if codes:
            rows = await db.fetch_all(
                "SELECT stock_code, market FROM stocks WHERE stock_code = ANY($1)", codes)
        else:
            rows = await db.fetch_all(
                "SELECT stock_code, market FROM stocks WHERE is_active = TRUE ORDER BY stock_code")
        total = 0
        done = 0
        for r in rows:
            klines = fetch_day_kline(r["stock_code"], r["market"])
            if not klines:
                continue
            updates = []
            for k in klines:
                updates.append({
                    "stock_code": r["stock_code"],
                    "trade_date": k["trade_date"],
                    "trade_time": k["trade_time"],
                    "open": k["open"], "high": k["high"], "low": k["low"],
                    "close": k["close"], "volume": k["volume"],
                    "amount": None, "change_pct": None, "change_amt": None,
                })
            await db.upsert_many(
                "stock_prices", updates,
                conflict_target=["trade_date", "stock_code", "trade_time"],
                update_cols=["open", "high", "low", "close", "volume"],
            )
            total += len(updates)
            done += 1
            if done % 50 == 0:
                print(f"progress: {done}/{len(rows)} stocks, {total} klines", flush=True)
        print(f"backfilled {total} daily klines for {done} stocks", flush=True)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
