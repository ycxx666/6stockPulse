"""
多源批量采集全市场行情 → stock_prices，并刷新 latest_quotes。

主源：新浪 hq.sinajs.cn；备源：腾讯 qt.gtimg.cn（新浪失败/空时自动切换）。
周期约 60-90 秒一轮，覆盖全市场 5000+ 只。
"""
import asyncio
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database import postgres as db

SINA_URL = "https://hq.sinajs.cn/list={codes}"
TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
}
MARKET_PREFIX = {"SH": "sh", "SZ": "sz", "BJ": "bj"}


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("gbk", errors="ignore")


def fetch_sina(codes: list[str]) -> dict:
    text = _fetch_text(SINA_URL.format(codes=",".join(codes)))
    out = {}
    for line in text.splitlines():
        if "hq_str_" not in line or '="' not in line:
            continue
        try:
            key = line.split("hq_str_")[1].split("=")[0]
            payload = line.split('"')[1]
            if not payload:
                continue
            p = payload.split(",")
            if len(p) < 32:
                continue
            close = float(p[3])
            prev = float(p[2])
            if close <= 0 or prev <= 0:
                continue
            out[key[2:]] = {
                "name": p[0], "open": float(p[1]), "close": close,
                "high": float(p[4]), "low": float(p[5]),
                "volume": int(float(p[8]) / 100), "amount": float(p[9]),  # 新浪成交量单位是股，转手
                "change_pct": round((close - prev) / prev * 100, 4),
                "change_amt": round(close - prev, 3),
                "trade_date": p[30], "trade_time": f"{p[30]} {p[31]}",
            }
        except Exception:
            continue
    return out


def fetch_tencent(codes: list[str]) -> dict:
    text = _fetch_text(TENCENT_URL.format(codes=",".join(codes)))
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
            if len(p) < 38:
                continue
            close = float(p[3])
            prev = float(p[4])
            if close <= 0 or prev <= 0:
                continue
            tm = p[30]
            d = f"{tm[0:4]}-{tm[4:6]}-{tm[6:8]}"
            t = f"{tm[8:10]}:{tm[10:12]}:{tm[12:14]}"
            out[key[2:]] = {
                "name": p[1], "open": float(p[5]), "close": close,
                "high": float(p[33]), "low": float(p[34]),
                "volume": int(float(p[6])), "amount": float(p[37]) * 10000,
                "change_pct": round((close - prev) / prev * 100, 4),
                "change_amt": round(close - prev, 3),
                "trade_date": d, "trade_time": f"{d} {t}",
            }
        except Exception:
            continue
    return out


def fetch_batch(codes: list[str]) -> dict:
    """新浪优先，失败或空则腾讯备源。"""
    try:
        data = fetch_sina(codes)
        if data:
            return data
    except Exception:
        pass
    try:
        return fetch_tencent(codes)
    except Exception:
        return {}


# ────────── 指数采集（写入 market_quote_snapshot）──────────
INDEX_DEFS = ["sh000001", "sz399001", "sz399006", "sh000300"]


def fetch_indices() -> list[dict]:
    """采集上证/深证/创业板/沪深300 指数。"""
    text = _fetch_text(SINA_URL.format(codes=",".join(INDEX_DEFS)))
    out = []
    for line in text.splitlines():
        if "hq_str_" not in line or '="' not in line:
            continue
        try:
            key = line.split("hq_str_")[1].split("=")[0]
            payload = line.split('"')[1]
            if not payload:
                continue
            p = payload.split(",")
            if len(p) < 32:
                continue
            close = float(p[3])
            prev = float(p[2])
            if close <= 0 or prev <= 0:
                continue
            out.append({
                "symbol": key[2:].upper() + "." + ("SH" if key.startswith("sh") else "SZ"),
                "name": p[0],
                "price": close,
                "prev_close": prev,
                "change": round(close - prev, 3),
                "change_pct": round((close - prev) / prev * 100, 4),
                "trade_time": f"{p[30]} {p[31]}",
            })
        except Exception:
            continue
    return out


async def collect_indices() -> int:
    try:
        rows = fetch_indices()
    except Exception:
        rows = []
    n = 0
    for r in rows:
        try:
            await db.execute("""
                INSERT INTO market_quote_snapshot
                    (symbol, asset_type, market, name, price, prev_close,
                     change, change_pct, quote_time, source, is_stale, updated_at)
                VALUES ($1, 'index', $2, $3, $4, $5, $6, $7, $8, 'sina', FALSE, NOW())
                ON CONFLICT (symbol) DO UPDATE
                SET price = EXCLUDED.price, prev_close = EXCLUDED.prev_close,
                    change = EXCLUDED.change, change_pct = EXCLUDED.change_pct,
                    quote_time = EXCLUDED.quote_time, is_stale = FALSE, updated_at = NOW()
            """, r["symbol"], r["symbol"].split(".")[-1], r["name"],
                r["price"], r["prev_close"], r["change"], r["change_pct"],
                datetime.fromisoformat(r["trade_time"]))
            n += 1
        except Exception:
            continue
    return n


async def collect_all() -> int:
    rows = await db.fetch_all(
        "SELECT stock_code, market, circ_shares FROM stocks WHERE is_active = TRUE ORDER BY stock_code")
    circ_map = {r["stock_code"]: r["circ_shares"] for r in rows}
    codes = [MARKET_PREFIX.get(r["market"], "sh") + r["stock_code"] for r in rows]

    batch_size = 80
    updates = []
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        data = fetch_batch(batch)
        for code, q in data.items():
            try:
                circ = circ_map.get(code)
                turnover = None
                if circ and float(circ) > 0:
                    # volume 单位手 → 股；换手率 = 成交量(股) / 流通股本 × 100
                    turnover = round(q["volume"] * 100 / float(circ) * 100, 4)
                updates.append({
                    "stock_code": code,
                    "trade_date": date.fromisoformat(q["trade_date"]),
                    "trade_time": datetime.fromisoformat(q["trade_time"]),
                    "open": q["open"], "high": q["high"], "low": q["low"],
                    "close": q["close"], "volume": q["volume"], "amount": q["amount"],
                    "change_pct": q["change_pct"], "change_amt": q["change_amt"],
                    "turnover_rate": turnover,
                })
            except Exception:
                continue
        await asyncio.sleep(0.25)

    if updates:
        inserted = await db.upsert_many(
            "stock_prices", updates,
            conflict_target=["trade_date", "stock_code", "trade_time"],
            update_cols=["open", "high", "low", "close", "volume", "amount",
                         "change_pct", "change_amt", "turnover_rate"],
        )
        print(f"collected {len(updates)} quotes, inserted {inserted}")
        await db.refresh_latest_quotes()

    # 指数采集（上证/深证/创业板/沪深300 → market_quote_snapshot）
    try:
        idx = await collect_indices()
        print(f"collected {idx} indices")
    except Exception as e:
        print(f"indices error: {e}")
    return len(updates)


async def main():
    await db.init_pool()
    try:
        while True:
            started = time.time()
            try:
                n = await collect_all()
                print(f"[{time.strftime('%H:%M:%S')}] collected {n} in {time.time()-started:.1f}s")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] error: {e}")
            await asyncio.sleep(max(10, 30 - int(time.time() - started)))
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
