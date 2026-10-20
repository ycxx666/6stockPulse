"""
从新浪拉取全市场股票列表，导入 stocks 表。
用法（服务器上，conda 环境 stockpulse）：
    python scripts/import_sina_stocks.py
"""
import json
import asyncio
import urllib.request

import asyncpg

SINA_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=hs_a"
)


def fetch_all():
    stocks = []
    page = 1
    while True:
        url = SINA_URL.format(page=page)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"page {page} failed: {e}")
            break
        if not data:
            break
        stocks.extend(data)
        print(f"page {page}: {len(data)}")
        if len(data) < 100:
            break
        page += 1
    return stocks


async def main():
    stocks = fetch_all()
    print(f"total fetched: {len(stocks)}")
    conn = await asyncpg.connect(
        host="localhost", port=5433, user="stockpulse",
        password="stockpulse123", database="stockpulse",
    )
    inserted = 0
    for s in stocks:
        code = s.get("code") or ""
        name = s.get("name") or ""
        symbol = s.get("symbol") or ""
        if not code or not name:
            continue
        prefix = symbol[:2].lower()
        market = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(prefix)
        if not market:
            continue
        await conn.execute(
            """
            INSERT INTO stocks (stock_code, stock_name, market, is_active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (stock_code) DO UPDATE
              SET stock_name = EXCLUDED.stock_name, is_active = TRUE
            """,
            code, name, market,
        )
        inserted += 1
    await conn.close()
    print(f"inserted/updated: {inserted}")


if __name__ == "__main__":
    asyncio.run(main())
