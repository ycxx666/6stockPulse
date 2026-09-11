"""
infrastructure/crawler/global_market.py
========================================
全球市场爬虫:指数 + 大宗商品 + 外汇 → market_quote_snapshot

修复(2026-09-11):
  - 原实现用 push2.eastmoney.com(主域,盘后/限流不稳)+ fs=m:113/114(实测返回的是**期货**,
    不是美股/港股指数)→ 指数/商品几乎抓不到,表里只剩 4 条外汇。
  - 改为 push2delay.eastmoney.com 延迟镜像 + ulist.np/get 按 **secid 批量**取指数/商品,稳定可用。

数据源
------
1. 东方财富(延迟镜像 push2delay) — 全球指数 + 商品:`/api/qt/ulist.np/get?secids=...`
2. 新浪外汇:`hq.sinajs.cn/list=fx_...`
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from infrastructure.crawler.base import BaseCrawler
from infrastructure.database import postgres as db

logger = logging.getLogger(__name__)


# ────────── URL ──────────
class GlobalMarketURLs:
    # 东方财富延迟镜像:按 secid 批量取(指数/商品通用)
    EM_ULIST = ("https://push2delay.eastmoney.com/api/qt/ulist.np/get"
                "?fltt=2&invt=2&fields=f12,f14,f2,f3,f4,f18&secids={secids}")
    # 新浪外汇(sina 返回 JS 变量文本,非 JSON)
    SINA_FX = "https://hq.sinajs.cn/list={symbols}"


# ────────── 关注列表(secid → 展示名/市场/类型) ──────────
# 注:东财 ulist 返回的 f12 为无前缀代码(DJIA/SPX/HSI/N225/UDI/GC00Y…),与 secid 去前缀后匹配
INDEX_SECIDS = [
    ("100.DJIA", "道琼斯工业指数", "US", "index"),
    ("100.NDX",  "纳斯达克指数",   "US", "index"),
    ("100.SPX",  "标普500指数",    "US", "index"),
    ("100.HSI",  "恒生指数",       "HK", "index"),
    ("100.N225", "日经225指数",    "JP", "index"),
    ("100.UDI",  "美元指数",       "FX", "index"),
]
COMMODITY_SECIDS = [
    ("101.GC00Y", "COMEX黄金", "COMM", "commodity"),
    ("101.SI00Y", "COMEX白银", "COMM", "commodity"),
    ("101.CL00Y", "NYMEX原油", "COMM", "commodity"),
    ("101.HG00Y", "COMEX铜",   "COMM", "commodity"),
]
_SECID_META = {sid: (name, mkt, at) for sid, name, mkt, at in (INDEX_SECIDS + COMMODITY_SECIDS)}

# 外汇 (新浪代码)
FOREX_SYMBOLS = [
    ("fx_seurcny",  "欧元/人民币",    "FX", "forex"),
    ("fx_susdcny",  "美元/离岸人民币", "FX", "forex"),
    ("fx_susdjpy",  "美元/日元",      "FX", "forex"),
    ("fx_sgbpcny",  "英镑/人民币",    "FX", "forex"),
]


# ────────── 爬虫 ──────────
class GlobalMarketCrawler(BaseCrawler):
    """全球市场爬虫(指数/商品走东财延迟镜像;外汇走新浪)"""

    name = "global_market"
    refresh_interval_seconds: int = 60

    def truncate_time(self, now: datetime | None = None) -> datetime:
        if now is None:
            now = datetime.now(timezone.utc).astimezone()
        bucket = self.refresh_interval_seconds
        epoch = int(now.timestamp())
        return datetime.fromtimestamp(epoch - (epoch % bucket)).astimezone()

    # ─── 解析东方财富 ulist 行 ───
    @staticmethod
    def _parse_ulist_row(row: dict, secid: str, name: str, market: str, asset_type: str) -> dict | None:
        try:
            price = float(row.get("f2") or 0)
            change_pct = float(row.get("f3") or 0)
            change_amt = float(row.get("f4") or 0)
            prev_close = float(row.get("f18") or 0) or None
            if not price:
                return None
            return {
                "symbol": secid,
                "asset_type": asset_type,
                "market": market,
                "name": name,
                "price": price,
                "prev_close": prev_close,
                "change": change_amt,
                "change_pct": change_pct,
            }
        except Exception as e:
            logger.debug("parse_ulist_row error: %s", e)
            return None

    # ─── 解析新浪外汇 ───
    SINA_RE = re.compile(r'var hq_str_(\w+)="([^"]+)"')

    @staticmethod
    def _parse_sina_fx(text: str) -> list[dict]:
        out = []
        for m in GlobalMarketCrawler.SINA_RE.finditer(text):
            sym, body = m.group(1), m.group(2)
            parts = body.split(",")
            if len(parts) < 6:
                continue
            try:
                name = parts[0].strip()
                price = float(parts[1] or 0)
                change = float(parts[2] or 0)
                change_pct = float(parts[3] or 0)
                if not price:
                    continue
                out.append({"symbol": sym, "name": name, "price": price,
                            "change": change, "change_pct": change_pct})
            except (ValueError, IndexError):
                continue
        return out

    # ─── 主入口 ───
    async def run_once(self) -> dict[str, Any]:
        await self.start_log(meta={"interval": self.refresh_interval_seconds})
        records: list[dict] = []
        try:
            # 1) 东方财富(延迟镜像)批量取 指数 + 商品
            secids = ",".join(s[0] for s in (INDEX_SECIDS + COMMODITY_SECIDS))
            text = await self.fetch(
                GlobalMarketURLs.EM_ULIST.format(secids=secids),
                headers={"Referer": "https://quote.eastmoney.com/"},
                skip_delay=True,
            )
            if text:
                obj = json.loads(text) if isinstance(text, str) else text
                rows = (obj.get("data") or {}).get("diff") or []
                for r in rows:
                    code = str(r.get("f12") or "")
                    # 与 secid 去前缀后匹配(如 "100.DJIA" → "DJIA")
                    secid = next((sid for sid in _SECID_META if sid.split(".", 1)[1] == code), None)
                    if not secid:
                        continue
                    name, market, asset_type = _SECID_META[secid]
                    parsed = self._parse_ulist_row(r, secid, name, market, asset_type)
                    if parsed:
                        records.append(parsed)

            # 2) 新浪外汇
            fx_symbols_str = ",".join(s[0] for s in FOREX_SYMBOLS)
            fx_text = await self.fetch(
                GlobalMarketURLs.SINA_FX.format(symbols=fx_symbols_str),
                headers={"Referer": "https://finance.sina.com.cn/"},
                skip_delay=True, as_json=False,
            )
            if fx_text:
                fx_map = {s[0]: (s[1], s[2], s[3]) for s in FOREX_SYMBOLS}
                for r in self._parse_sina_fx(fx_text):
                    if r["symbol"] in fx_map:
                        name, market, atype = fx_map[r["symbol"]]
                        r.update({"name": name, "market": market, "asset_type": atype, "prev_close": None})
                        records.append(r)

            # 3) 写库
            if records:
                await self._upsert_snapshots(records)

            await self.finish_log(success=len(records), skip=0, fail=0)
            logger.info("[global_market] fetched=%d", len(records))
            return {"fetched": len(records), "inserted": len(records)}
        except Exception as e:
            await self.finish_log(success=0, skip=0, fail=1, error=str(e))
            logger.exception("[global_market] run error: %s", e)
            return {"error": str(e)}

    # ─── 入库 ───
    async def _upsert_snapshots(self, records: list[dict]):
        trade_time = self.truncate_time()
        sql = """
            INSERT INTO market_quote_snapshot
                (symbol, asset_type, market, name, price, prev_close,
                 change, change_pct, quote_time, source, is_stale, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'em-delay', FALSE, NOW())
            ON CONFLICT (symbol) DO UPDATE
            SET price      = EXCLUDED.price,
                prev_close = EXCLUDED.prev_close,
                change     = EXCLUDED.change,
                change_pct = EXCLUDED.change_pct,
                quote_time = EXCLUDED.quote_time,
                is_stale   = FALSE,
                updated_at = NOW()
        """
        async with db.acquire() as conn:
            for r in records:
                try:
                    await conn.execute(sql,
                        r["symbol"], r.get("asset_type", "index"),
                        r.get("market", "US"), r["name"],
                        r.get("price"), r.get("prev_close"),
                        r.get("change"), r.get("change_pct"),
                        trade_time,
                    )
                except Exception as e:
                    logger.debug("upsert snapshot %s: %s", r.get("symbol"), e)


# ────────── 种子数据(首次运行) ──────────
async def seed_global_snapshots():
    """初始化全球行情种子(占位,price=0/is_stale;真实值由 run_once 覆盖)"""
    seed = [
        (sid, at, mkt, name, 0, 0, 0, 0)
        for sid, name, mkt, at in (INDEX_SECIDS + COMMODITY_SECIDS)
    ]
    seed += [
        ("fx_seurcny", "forex", "FX", "欧元/人民币", 0, 0, 0, 0),
        ("fx_susdcny", "forex", "FX", "美元/离岸人民币", 0, 0, 0, 0),
        ("fx_susdjpy", "forex", "FX", "美元/日元", 0, 0, 0, 0),
        ("fx_sgbpcny", "forex", "FX", "英镑/人民币", 0, 0, 0, 0),
    ]
    async with db.acquire() as conn:
        for s in seed:
            await conn.execute("""
                INSERT INTO market_quote_snapshot
                    (symbol, asset_type, market, name, price, prev_close, change, change_pct, source,
                     is_stale, quote_time, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'seed', TRUE, NOW(), NOW())
                ON CONFLICT (symbol) DO NOTHING
            """, *s)
    logger.info("Seeded %d global market symbols", len(seed))


async def fetch_once(interval: int = 60) -> dict:
    async with GlobalMarketCrawler(refresh_interval_seconds=interval) as c:
        return await c.run_once()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _main():
        await db.init_pool()
        try:
            print(await fetch_once(60))
        finally:
            await db.close_pool()

    asyncio.run(_main())
