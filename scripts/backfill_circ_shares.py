"""
从腾讯批量接口补全 stocks.circ_shares（流通股本）。

腾讯 v_shXXXXXX 字段：
  parts[3]  现价
  parts[38] 换手率(%)
  parts[44] 流通市值(亿)
流通股本(股) = 流通市值(亿) * 1e8 / 现价
"""
import asyncio
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database import postgres as db

TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
}
MARKET_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}


def fetch_batch(codes: list[str]) -> dict:
    req = urllib.request.Request(TENCENT_URL.format(codes=",".join(codes)), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("gbk", errors="ignore")
    out = {}
    for line in text.splitlines():
        if "v_" not in line or '"' not in line:
            continue
        try:
            key = line.split("=")[0].replace("v_", "").strip()
            payload = line.split('"')[1]
            if not payload:
                continue
            p = payload.split("~")
            if len(p) < 45:
                continue
            price = float(p[3])
            circ_mv_yi = float(p[44])
            if price > 0 and circ_mv_yi > 0:
                out[key[2:]] = int(circ_mv_yi * 1e8 / price)
        except Exception:
            continue
    return out


async def main():
    await db.init_pool()
    try:
        rows = await db.fetch_all(
            "SELECT stock_code, market FROM stocks WHERE is_active = TRUE ORDER BY stock_code")
        codes = [MARKET_PREFIX.get(r["market"], "sh") + r["stock_code"] for r in rows]
        updated = 0
        for i in range(0, len(codes), 80):
            batch = codes[i:i + 80]
            data = fetch_batch(batch)
            for code, shares in data.items():
                try:
                    await db.execute(
                        "UPDATE stocks SET circ_shares = $1 WHERE stock_code = $2",
                        shares, code)
                    updated += 1
                except Exception:
                    continue
            await asyncio.sleep(0.2)
        print(f"updated circ_shares: {updated}")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
