"""
6stockPulse · 东方财富爬虫
============================
  - EastMoneyFundFlow  : A 股资金流向 (按主力净流入排序, 分页)
  - EastMoneySector    : 板块 (行业 + 概念) 资金流向
  - EastMoneyRealtime  : 实时行情 (单股 / 批量)
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, date
from typing import Any

from infrastructure.crawler.base import BaseCrawler
from core.config import EM_URLS
from infrastructure.database import postgres as db

logger = logging.getLogger(__name__)


class EastMoneyFundFlow(BaseCrawler):
    """A 股资金流向采集 (主力/超大/大/中/小单)"""
    name = "eastmoney_fund_flow"

    async def run(self, pages: int = 3) -> dict:
        await self.start_log({"type": "fund_flow", "pages": pages})
        all_rows: list[dict] = []
        success, fail = 0, 0
        try:
            for pn in range(1, pages + 1):
                url = EM_URLS.FUND_FLOW_LIST.format(pz=100, pn=pn)
                data = await self.fetch(url)
                if not data or not data.get("data", {}).get("diff"):
                    logger.info("[%s] 第%d页无数据", self.name, pn)
                    continue
                rows = [self._parse(r) for r in data["data"]["diff"]]
                rows = [r for r in rows if r["stock_code"]]
                all_rows.extend(rows)
                logger.info("[%s] 第%d页 %d 条", self.name, pn, len(rows))
            if all_rows:
                inserted = await db.insert_many(
                    "fund_flow", all_rows,
                    conflict_target="trade_date, stock_code, trade_time"
                )
                success = inserted
                logger.info("[%s] 入库 %d 条", self.name, inserted)
        except Exception as e:
            fail = 1
            logger.exception("[%s] 异常: %s", self.name, e)
            await self.finish_log(success, 0, fail, str(e))
            return {"success": success, "fail": fail, "error": str(e)}

        await self.finish_log(success, 0, fail)
        return {"success": success, "fail": fail, "total": len(all_rows)}

    def _parse(self, item: dict) -> dict:
        pct = self.to_float(item.get("f3"))
        sentiment = "bullish" if pct > 0 else "bearish" if pct < 0 else "neutral"
        return {
            "stock_code":      str(item.get("f12", "")),
            "trade_date":      date.today(),
            "trade_time":      datetime.now().isoformat(timespec="seconds"),
            "main_net_inflow":   self.to_float(item.get("f62")) * 10000,
            "main_net_ratio":    self.to_float(item.get("f184")),
            "super_large_net":   self.to_float(item.get("f66")) * 10000,
            "super_large_ratio": self.to_float(item.get("f69")),
            "large_net_inflow":  self.to_float(item.get("f72")) * 10000,
            "large_ratio":       self.to_float(item.get("f75")),
            "mid_net_inflow":    self.to_float(item.get("f78")) * 10000,
            "mid_ratio":         self.to_float(item.get("f81")),
            "small_net_inflow":  self.to_float(item.get("f84")) * 10000,
            "small_ratio":       self.to_float(item.get("f87")),
            "sentiment":         sentiment,
        }


class EastMoneySector(BaseCrawler):
    """板块 (行业 + 概念) 资金流向采集"""
    name = "eastmoney_sector"

    async def run(self, pages: int = 2) -> dict:
        await self.start_log({"type": "sector", "pages": pages})
        all_rows: list[dict] = []
        success, fail = 0, 0
        try:
            for stype, url_tpl in [
                ("industry", EM_URLS.SECTOR_INDUSTRY),
                ("concept",  EM_URLS.SECTOR_CONCEPT),
            ]:
                for pn in range(1, pages + 1):
                    url = url_tpl.format(pn=pn)
                    data = await self.fetch(url)
                    if not data or not data.get("data", {}).get("diff"):
                        continue
                    for item in data["data"]["diff"]:
                        row = self._parse(item, stype)
                        if row["sector_code"]:
                            all_rows.append(row)
            if all_rows:
                inserted = await db.insert_many(
                    "sector_fund_flow", all_rows,
                    conflict_target="trade_date, sector_code, trade_time"
                )
                success = inserted
        except Exception as e:
            fail = 1
            logger.exception("[%s] 异常: %s", self.name, e)
            await self.finish_log(success, 0, fail, str(e))
            return {"success": success, "fail": fail, "error": str(e)}
        await self.finish_log(success, 0, fail)
        return {"success": success, "fail": fail, "total": len(all_rows)}

    def _parse(self, item: dict, stype: str) -> dict:
        return {
            "sector_code":     str(item.get("f12", "")),
            "sector_name":     item.get("f14", ""),
            "trade_date":      date.today(),
            "trade_time":      datetime.now().isoformat(timespec="seconds"),
            "change_pct":      self.to_float(item.get("f3")),
            "main_net_inflow": self.to_float(item.get("f62")) * 10000,
            "main_net_ratio":  self.to_float(item.get("f184")),
            "turnover_rate":   self.to_float(item.get("f8")),
            "super_large_net": self.to_float(item.get("f100")) * 10000,
            "large_net":       0,
            "mid_net":         0,
            "small_net":       0,
        }


class EastMoneyRealtime(BaseCrawler):
    """实时行情拉取 (支持批量)"""
    name = "eastmoney_realtime"

    async def run(self, stock_codes: list[str] | None = None,
                  top_n: int = 100) -> dict:
        """
        stock_codes: 指定股票列表 (None = 拉 TOP N)
        """
        await self.start_log({"type": "realtime", "count": len(stock_codes) if stock_codes else top_n})
        success, fail = 0, 0
        try:
            if not stock_codes:
                # 从 DB 拉关注度 TOP N
                stock_codes = await self._top_n_stocks(top_n)
            # 分批 (避免 URL 过长)
            batch_size = 50
            for i in range(0, len(stock_codes), batch_size):
                batch = stock_codes[i:i+batch_size]
                tasks = [self._fetch_one(code) for code in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                rows = [r for r in results if isinstance(r, dict)]
                if rows:
                    inserted = await db.insert_many(
                        "stock_prices", rows,
                        conflict_target="trade_date, stock_code, trade_time"
                    )
                    success += inserted
        except Exception as e:
            fail = 1
            logger.exception("[%s] 异常: %s", self.name, e)
            await self.finish_log(success, 0, fail, str(e))
            return {"success": success, "fail": fail, "error": str(e)}
        await self.finish_log(success, 0, fail)
        return {"success": success, "fail": fail}

    async def _top_n_stocks(self, n: int) -> list[str]:
        rows = await db.fetch_all("""
            SELECT stock_code FROM stocks WHERE is_active = TRUE
            ORDER BY stock_code LIMIT $1
        """, n)
        return [r["stock_code"] for r in rows]

    async def _fetch_one(self, code: str, skip_delay: bool = False) -> dict | None:
        try:
            secid = self.secid(code)
            url = EM_URLS.QUOTE_REALTIME.format(secid=secid)
            data = await self.fetch(url, skip_delay=skip_delay)
            if not data or not data.get("data"):
                return None
            d = data["data"]
            price = self.to_float(d.get("f43")) / 100      # 原始单位"分" → 元
            prev_close = self.to_float(d.get("f60")) / 100
            change_pct = self.to_float(d.get("f170")) / 100   # API 返回基点值 (×100), -48 → -0.48%
            return {
                "stock_code":  code,
                "trade_date":  date.today(),
                "trade_time":  datetime.now().isoformat(timespec="seconds"),
                "open":        self.to_float(d.get("f46")) / 100,
                "high":        self.to_float(d.get("f44")) / 100,
                "low":         self.to_float(d.get("f45")) / 100,
                "close":       price,
                "volume":      int(self.to_float(d.get("f47"))),
                "amount":      self.to_float(d.get("f48")),
                "change_pct":  change_pct,
                "change_amt":  self.to_float(d.get("f169")) / 100,   # 单位"分" → 元, 同 f43
                "turnover_rate": self.to_float(d.get("f168")),        # API 已返回百分比 (3.25=3.25%)
                "pe_ratio":    self.to_float(d.get("f167")),
                "pb_ratio":    None,
                "market_cap":  None,
                "circ_market_cap": self.to_float(d.get("f161")) if d.get("f161") else None,
            }
        except Exception as e:
            logger.debug("[realtime] %s error: %s", code, e)
            return None


# ────────── 资金流 K 线历史回填 ──────────
class EastMoneyFundFlowHistory(BaseCrawler):
    """
    资金流历史 K 线回填 (从 OLD_backup/spider.py 移植)
    接口B: push2his.eastmoney.com/api/qt/stock/fflow/kline/get
    返回字段: f51=日期, f52=主力净额, f53=小单, f54=中单, f55=大单, f56=超大单
    """
    name = "eastmoney_fund_flow_history"

    KLINE_URL = (
        "https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get?"
        "lmt={days}&klt=101&secid={secid}"
        "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
        "&ut=b2884a393a59ad64002292a3e90d46a5"
    )

    async def run(self, stock_codes: list[str] | None = None,
                  days: int = 5) -> dict:
        """
        回填最近 N 个交易日的资金流数据
        stock_codes: None = 拉关注度 TOP 100
        """
        await self.start_log({"type": "fund_flow_history", "days": days})

        if not stock_codes:
            stock_codes = await self._top_stocks(100)

        all_rows: list[dict] = []
        success, fail = 0, 0

        for code in stock_codes:
            try:
                secid = self.secid(code)
                url = self.KLINE_URL.format(days=days, secid=secid)
                data = await self.fetch(url)
                if not data or not data.get("data"):
                    continue

                klines = data["data"].get("klines") or []
                if not klines:
                    continue

                # klines 格式: ["2025-07-10,12345.00,6789.00,...", ...]
                # 取最后 days 条
                for line in klines[-days:]:
                    row = self._parse_kline(code, line)
                    if row:
                        all_rows.append(row)
            except Exception as e:
                logger.debug("[fund_flow_history] %s error: %s", code, e)
                fail += 1

        if all_rows:
            inserted = await db.insert_many(
                "fund_flow", all_rows,
                conflict_target="trade_date, stock_code, trade_time"
            )
            success = inserted
            logger.info("[%s] 回填 %d 条", self.name, inserted)

        await self.finish_log(success, len(all_rows) - success, fail)
        return {"success": success, "fail": fail, "total": len(all_rows)}

    async def _top_stocks(self, n: int) -> list[str]:
        rows = await db.fetch_all("""
            SELECT stock_code FROM stocks WHERE is_active = TRUE
            ORDER BY stock_code LIMIT $1
        """, n)
        return [r["stock_code"] for r in rows]

    def _parse_kline(self, code: str, line: str) -> dict | None:
        """解析 K 线行: 日期,主力净额,小单净额,中单净额,大单净额,超大单净额"""
        parts = line.split(",")
        if len(parts) < 6:
            return None
        try:
            trade_date = datetime.strptime(parts[0].strip(), "%Y-%m-%d").date()
            main_net = self.to_float(parts[1])
            small_net = self.to_float(parts[2])
            mid_net = self.to_float(parts[3])
            large_net = self.to_float(parts[4])
            super_large_net = self.to_float(parts[5])

            sentiment = "bullish" if main_net > 0 else "bearish" if main_net < 0 else "neutral"
            return {
                "stock_code":      code,
                "trade_date":      trade_date,
                "trade_time":      datetime.strptime(f"{trade_date} 15:00:00", "%Y-%m-%d %H:%M:%S"),
                "main_net_inflow":   main_net,
                "main_net_ratio":    None,
                "super_large_net":   super_large_net,
                "super_large_ratio": None,
                "large_net_inflow":  large_net,
                "large_ratio":       None,
                "mid_net_inflow":    mid_net,
                "mid_ratio":         None,
                "small_net_inflow":  small_net,
                "small_ratio":       None,
                "sentiment":         sentiment,
            }
        except (ValueError, IndexError) as e:
            logger.debug("[fund_flow_history] parse error: %s", e)
            return None
