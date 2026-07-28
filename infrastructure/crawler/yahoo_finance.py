"""
infrastructure/crawler/yahoo_finance.py
=========================================
Yahoo Finance 全球行情爬虫

数据源: Yahoo Finance v8 chart API
分级刷新:
  Tier 1 (15s): SPX/NDX/DJI/HSI/N225/FTSE/DAX/SSEC + 黄金/原油
  Tier 2 (60s): 20 只核心个股 + 外汇
  Tier 3 (day): 其余股票, 每日收盘后
"""
from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infrastructure.crawler.base import BaseCrawler
from infrastructure.database import postgres as db

logger = logging.getLogger(__name__)

# 加载 symbol 列表
_SYMBOLS_PATH = Path(__file__).resolve().parent.parent.parent / "shared" / "global_symbols.json"
with open(_SYMBOLS_PATH, "r") as f:
    _SYMBOL_DATA = json.load(f)

SYMBOLS = _SYMBOL_DATA["symbols"]
SYMBOL_MAP = {s["symbol"]: s for s in SYMBOLS}

T1_SYMBOLS = [s["symbol"] for s in SYMBOLS if s["tier"] == 1]
T2_SYMBOLS = [s["symbol"] for s in SYMBOLS if s["tier"] == 2]


class YahooFinanceCrawler(BaseCrawler):
    """Yahoo Finance 全球行情采集"""

    name = "yahoo_finance"
    _crumb: str | None = None
    _crumb_ts: float = 0

    # v8 chart API
    CHART_URL = (
        "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        "?interval=1m&range=1d&includePrePost=false&crumb={crumb}"
    )
    CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
    COOKIE_URL = "https://fc.yahoo.com/"

    async def _ensure_crumb(self):
        """获取 Yahoo Finance crumb (cookie 已由 session 自动管理, 有效约 1 小时)"""
        import time
        now = time.monotonic()
        if self._crumb and (now - self._crumb_ts) < 1800:  # 30min 复用
            return

        session = await self._get_session()
        # 先访问 Yahoo 首页获取 cookie
        try:
            async with session.get(self.COOKIE_URL) as resp:
                await resp.text()
        except Exception:
            pass

        # 获取 crumb
        async with session.get(self.CRUMB_URL) as resp:
            self._crumb = (await resp.text()).strip()
            self._crumb_ts = now
            logger.debug("[yahoo] crumb acquired: %s...", self._crumb[:20] if self._crumb else "EMPTY")

    async def run(self, symbols: list[str] | None = None,
                  batch_size: int = 5) -> dict:
        """
        拉取指定 symbol 列表的行情。
        symbols=None 时拉取 T1。
        """
        if symbols is None:
            symbols = T1_SYMBOLS

        await self.start_log({"type": "yahoo", "count": len(symbols)})
        records: list[dict] = []
        success, fail = 0, 0

        try:
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                tasks = [self._fetch_one(s) for s in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for s, r in zip(batch, results):
                    if isinstance(r, dict):
                        records.append(r)
                        success += 1
                    else:
                        fail += 1
                # 批次间冷却
                if i + batch_size < len(symbols):
                    await asyncio.sleep(0.5)

            if records:
                await self._upsert(records)

        except Exception as e:
            logger.exception("[%s] error: %s", self.name, e)
            await self.finish_log(success, 0, fail, str(e))
            return {"success": success, "fail": fail, "error": str(e)}

        await self.finish_log(success, 0, fail)
        return {"success": success, "fail": fail, "total": len(records)}

    async def _fetch_one(self, symbol: str) -> dict | None:
        """拉取单个 symbol 行情"""
        try:
            await self._ensure_crumb()
            if not self._crumb:
                logger.warning("[yahoo] no crumb available for %s", symbol)
                return None
            url = self.CHART_URL.format(symbol=symbol, crumb=self._crumb)
            data = await self.fetch(url, skip_delay=True)
            if not data:
                return None

            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            quote = result[0].get("indicators", {}).get("quote", [{}])[0]
            meta_info = SYMBOL_MAP.get(symbol, {})

            price = meta.get("regularMarketPrice")
            prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
            if not price:
                return None

            return {
                "symbol": symbol,
                "name": meta_info.get("name", meta.get("shortName", symbol)),
                "market": _resolve_market(symbol, meta_info),
                "asset_type": meta_info.get("type", "stock"),
                "price": price,
                "change_pct": meta.get("regularMarketChangePercent"),
                "change_amt": meta.get("regularMarketChange"),
                "volume": meta.get("regularMarketVolume"),
                "prev_close": prev_close,
                "high": meta.get("regularMarketDayHigh"),
                "low": meta.get("regularMarketDayLow"),
                "open": quote.get("open", [None])[0] if quote.get("open") else None,
                "currency": meta.get("currency", meta_info.get("currency", "USD")),
                "sector": meta_info.get("sector", ""),
                "source": "yahoo",
                "quote_time": _safe_ts(meta.get("regularMarketTime")),
            }
        except Exception as e:
            logger.debug("[yahoo] %s: %s", symbol, e)
            return None

    async def _upsert(self, records: list[dict]):
        """批量写入 global_market_quote"""
        sql = """
            INSERT INTO global_market_quote
                (symbol, name, market, asset_type, price, change_pct, change_amt,
                 volume, prev_close, high, low, open, currency, sector,
                 source, quote_time)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (symbol, quote_time) DO UPDATE
            SET price      = EXCLUDED.price,
                change_pct = EXCLUDED.change_pct,
                volume     = EXCLUDED.volume,
                prev_close = EXCLUDED.prev_close
        """
        async with db.acquire() as conn:
            for r in records:
                try:
                    await conn.execute(sql,
                        r["symbol"], r["name"], r["market"], r["asset_type"],
                        r["price"], r.get("change_pct"), r.get("change_amt"),
                        r.get("volume"), r.get("prev_close"),
                        r.get("high"), r.get("low"), r.get("open"),
                        r.get("currency"), r.get("sector"),
                        r.get("source"), r["quote_time"],
                    )
                except Exception as e:
                    logger.debug("[yahoo] upsert %s: %s", r.get("symbol"), e)


def _resolve_market(symbol: str, meta_info: dict) -> str:
    """推断市场代码"""
    m = meta_info.get("market", "")
    if m:
        return m
    if symbol.endswith(".SS"):
        return "SSE"
    if symbol.endswith(".HK"):
        return "HKEX"
    if symbol.endswith(".NS"):
        return "NSE"
    if "=X" in symbol:
        return "FX"
    if "-USD" in symbol:
        return "CRYPTO"
    if "=F" in symbol:
        return "COMM"
    return "US"


def _safe_ts(ts) -> datetime | None:
    """Unix timestamp → datetime"""
    if ts is None:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (ValueError, OSError, TypeError):
        return datetime.now(timezone.utc)


# ────────── 便捷入口 ──────────
async def fetch_tier1() -> dict:
    """拉取 Tier 1（核心指数 + 商品）"""
    async with YahooFinanceCrawler() as c:
        return await c.run(symbols=T1_SYMBOLS)


async def fetch_tier2() -> dict:
    """拉取 Tier 2（核心个股 + 外汇）"""
    async with YahooFinanceCrawler() as c:
        return await c.run(symbols=T2_SYMBOLS)
