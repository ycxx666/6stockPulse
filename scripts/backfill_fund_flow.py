#!/usr/bin/env python3
"""
scripts/backfill_fund_flow.py
================================
资金流历史数据回填脚本

用途: 使用 EastMoneyFundFlowHistory 爬虫回填最近 N 个交易日的数据
运行: python3 scripts/backfill_fund_flow.py [--days 5] [--stocks 100]
"""
from __future__ import annotations
import sys, asyncio, argparse, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import database as db
from crawlers.eastmoney import EastMoneyFundFlowHistory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("backfill")


async def main():
    parser = argparse.ArgumentParser(description="回填资金流历史数据")
    parser.add_argument("--days", type=int, default=5, help="回填交易日数 (默认 5)")
    parser.add_argument("--stocks", type=int, default=100, help="回填股票数 (默认 100)")
    args = parser.parse_args()

    await db.init_pool()
    logger.info("开始回填: days=%d, stocks=%d", args.days, args.stocks)

    async with EastMoneyFundFlowHistory() as crawler:
        result = await crawler.run(days=args.days)

    logger.info("回填完成: %s", result)

    # 刷新物化视图
    await db.refresh_latest_quotes()
    logger.info("物化视图已刷新")

    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
