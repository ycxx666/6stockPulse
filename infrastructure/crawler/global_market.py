"""
crawlers/global_market.py
===========================
全球市场爬虫: 指数 + 大宗商品 + 外汇

数据源
------
1. 东方财富全球指数 (集成了原 vix.py):
   m:113 = 美股, m:114 = 港股, m:115 = 欧股, m:116 = 日股
   重点: 100.VIX / 100.DJI / 100.INX / 100.IXIC / 100.HSI / 100.N225

2. 东方财富商品期货 (m:113 可覆盖部分):
   黄金(AU9999) / 白银 / WTI原油 / 布伦特 / 天然气 / 铜

3. 新浪外汇数据:
   hq.sinajs.cn/list=fx_susdcny,fx_seurcny,...

写入: market_quote_snapshot 表
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


# ────────── URL 模板 ──────────
class GlobalMarketURLs:
    # 东方财富 — 各市场指数
    EM_US  = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=200&pn=1&np=1&fltt=2&invt=2&fs=m:113&fields=f12,f14,f2,f3,f4,f15,f16,f17,f18,f20"
    EM_HK  = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=200&pn=1&np=1&fltt=2&invt=2&fs=m:114&fields=f12,f14,f2,f3,f4,f15,f16,f17,f18,f20"
    EM_EU  = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=200&pn=1&np=1&fltt=2&invt=2&fs=m:115&fields=f12,f14,f2,f3,f4,f15,f16,f17,f18,f20"
    EM_JP  = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=200&pn=1&np=1&fltt=2&invt=2&fs=m:116&fields=f12,f14,f2,f3,f4,f15,f16,f17,f18,f20"
    # 商品期货 (东方财富)
    EM_COMM = "https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=50&pn=1&np=1&fltt=2&invt=2&fs=m:113,m:114&fields=f12,f14,f2,f3,f4,f15,f16,f17,f18"
    # 国际商品 (新浪 — 作为备用)
    SINA_COMM = "https://hq.sinajs.cn/list={symbols}"
    # 外汇
    SINA_FX = "https://hq.sinajs.cn/list={symbols}"


# ────────── 关注列表 ──────────
INDEX_WATCHLIST = {
    # 美股
    "100.VIX":   ("VIX 恐慌指数",     "US", "index"),
    "100.DJI":   ("道琼斯工业指数",   "US", "index"),
    "100.INX":   ("标普500指数",      "US", "index"),
    "100.IXIC":  ("纳斯达克综合指数", "US", "index"),
    ".VIX":      ("VIX 恐慌指数",     "US", "index"),
    ".DJI":      ("道琼斯工业指数",   "US", "index"),
    ".INX":      ("标普500指数",      "US", "index"),
    ".IXIC":     ("纳斯达克综合指数", "US", "index"),
    # 港股
    "100.HSI":   ("恒生指数",         "HK", "index"),
    ".HSI":      ("恒生指数",         "HK", "index"),
    # 日股
    "100.N225":  ("日经225指数",      "JP", "index"),
    ".N225":     ("日经225指数",      "JP", "index"),
}

# 大宗商品 (东方财富代码)
COMMODITY_WATCHLIST = {
    "100.AU9999": ("黄金9999",   "COMM", "commodity"),
    "100.AG9999": ("白银9999",   "COMM", "commodity"),
    "100.CL":     ("WTI原油",     "COMM", "commodity"),
    "100.NG":     ("天然气",      "COMM", "commodity"),
    "100.HG":     ("铜",          "COMM", "commodity"),
}

# 外汇 (新浪代码)
FOREX_SYMBOLS = [
    ("fx_seurcny",  "欧元/人民币",   "FX", "forex"),
    ("fx_susdcny",  "美元/离岸人民币","FX", "forex"),
    ("fx_susdjpy",  "美元/日元",     "FX", "forex"),
    ("fx_sgbpcny",  "英镑/人民币",   "FX", "forex"),
]


# ────────── 爬虫 ──────────
class GlobalMarketCrawler(BaseCrawler):
    """
    全球市场爬虫 (取代原 vix.py)
    refresh_interval_seconds: 时间桶粒度 (默认 60s), 用于去重
    """
    name = "global_market"
    refresh_interval_seconds: int = 60

    def truncate_time(self, now: datetime | None = None) -> datetime:
        if now is None:
            now = datetime.now(timezone.utc).astimezone()
        bucket = self.refresh_interval_seconds
        epoch = int(now.timestamp())
        return datetime.fromtimestamp(epoch - (epoch % bucket)).astimezone()

    # ─── 解析东方财富行 ───
    @staticmethod
    def _parse_em_row(row: dict) -> dict | None:
        try:
            code = str(row.get("f12") or "")
            name = str(row.get("f14") or "")
            if not code or not name:
                return None
            close = float(row.get("f2") or 0)
            change_pct = float(row.get("f3") or 0)
            change_amt = float(row.get("f4") or 0)
            high = float(row.get("f15") or 0) or None
            low = float(row.get("f16") or 0) or None
            open_p = float(row.get("f17") or 0) or None
            prev_close = float(row.get("f18") or 0) or None
            return {
                "symbol": code,
                "name": name,
                "price": close,
                "prev_close": prev_close,
                "change": change_amt,
                "change_pct": change_pct,
                "open": open_p,
                "high": high,
                "low": low,
            }
        except Exception as e:
            logger.debug("parse_em_row error: %s", e)
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
                # 新浪外汇: parts[1]=最新价, parts[2]=涨跌额, parts[3]=涨跌幅
                price = float(parts[1] or 0)
                change = float(parts[2] or 0)
                change_pct = float(parts[3] or 0)
                out.append({
                    "symbol": sym,
                    "name": name,
                    "price": price,
                    "change": change,
                    "change_pct": change_pct,
                })
            except (ValueError, IndexError):
                continue
        return out

    # ─── 主入口 ───
    async def run_once(self) -> dict[str, Any]:
        """执行一次完整抓取"""
        await self.start_log(meta={"interval": self.refresh_interval_seconds})

        records: list[dict] = []

        try:
            # 1) 东方财富 4 市场指数
            for url_tpl, mkt in [
                (GlobalMarketURLs.EM_US, "US"),
                (GlobalMarketURLs.EM_HK, "HK"),
                (GlobalMarketURLs.EM_EU, "EU"),
                (GlobalMarketURLs.EM_JP, "JP"),
            ]:
                text = await self.fetch(url_tpl, headers={"Referer": "https://quote.eastmoney.com/"}, skip_delay=True)
                if not text:
                    continue
                try:
                    obj = json.loads(text) if isinstance(text, str) else text
                except json.JSONDecodeError:
                    continue
                rows = (obj.get("data") or {}).get("diff") or []
                for r in rows:
                    parsed = self._parse_em_row(r)
                    if not parsed:
                        continue
                    code = parsed["symbol"]
                    # 筛选: 只看关注列表
                    matched = False
                    for prefix, (name, market, atype) in INDEX_WATCHLIST.items():
                        if code == prefix or code.lower().endswith(prefix.lower().lstrip(".")):
                            parsed["asset_type"] = atype
                            parsed["market"] = market
                            parsed["name"] = name
                            matched = True
                            break
                    if matched:
                        records.append(parsed)
                await asyncio.sleep(0.3)  # 市场间短冷却

            # 2) 新浪外汇
            fx_symbols_str = ",".join(s[0] for s in FOREX_SYMBOLS)
            fx_text = await self.fetch(
                GlobalMarketURLs.SINA_FX.format(symbols=fx_symbols_str),
                headers={"Referer": "https://finance.sina.com.cn/"},
                skip_delay=True
            )
            if fx_text:
                fx_records = self._parse_sina_fx(fx_text)
                fx_map = {s[0]: (s[1], s[2], s[3]) for s in FOREX_SYMBOLS}
                for r in fx_records:
                    if r["symbol"] in fx_map:
                        name, market, atype = fx_map[r["symbol"]]
                        r["name"] = name
                        r["market"] = market
                        r["asset_type"] = atype
                        r["prev_close"] = None
                        records.append(r)

            # 3) 写入 market_quote_snapshot
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
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'eastmoney', FALSE, NOW())
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


# ────────── 种子数据 (首次运行) ──────────
async def seed_global_snapshots():
    """初始化全球行情种子数据"""
    seed = [
        ("000001.SH", "index", "SH", "上证指数", 0, 0, 0, 0),
        ("399001.SZ", "index", "SZ", "深证成指", 0, 0, 0, 0),
        ("399006.SZ", "index", "SZ", "创业板指", 0, 0, 0, 0),
        ("100.VIX",   "index", "US", "VIX 恐慌指数", 0, 0, 0, 0),
        ("100.DJI",   "index", "US", "道琼斯工业指数", 0, 0, 0, 0),
        ("100.INX",   "index", "US", "标普500指数", 0, 0, 0, 0),
        ("100.IXIC",  "index", "US", "纳斯达克综合指数", 0, 0, 0, 0),
        ("100.HSI",   "index", "HK", "恒生指数", 0, 0, 0, 0),
        ("100.N225",  "index", "JP", "日经225指数", 0, 0, 0, 0),
        ("GC.F",      "commodity", "COMM", "黄金", 0, 0, 0, 0),
        ("SI.F",      "commodity", "COMM", "白银", 0, 0, 0, 0),
        ("CL.F",      "commodity", "COMM", "WTI原油", 0, 0, 0, 0),
        ("NG.F",      "commodity", "COMM", "天然气", 0, 0, 0, 0),
        ("HG.F",      "commodity", "COMM", "铜", 0, 0, 0, 0),
        ("EURUSD.F",  "forex",     "FX",   "欧元/美元", 0, 0, 0, 0),
        ("USDJPY.F",  "forex",     "FX",   "美元/日元", 0, 0, 0, 0),
        ("USDCNH.F",  "forex",     "FX",   "美元/离岸人民币", 0, 0, 0, 0),
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


# ────────── 便捷入口 ──────────
async def fetch_once(interval: int = 60) -> dict:
    async with GlobalMarketCrawler(refresh_interval_seconds=interval) as c:
        return await c.run_once()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_global_snapshots())
    print(asyncio.run(fetch_once(60)))
