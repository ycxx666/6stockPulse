"""
scripts/aggregate_sectors.py
============================
从已有 stocks + fund_flow 数据聚合成 sector_fund_flow

对每个 industry (来自 stocks.industry), 聚合当日:
  · change_pct        (成员股平均涨跌幅)
  · main_net_inflow   (主力净流入合计)
  · turnover_rate     (成员股平均换手率)
  · super/large/mid/small_net  (按档位汇总)
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg
from config import DB


async def main():
    pg = await asyncpg.connect(
        host=DB.host, port=DB.port, user=DB.user,
        password=DB.password, database=DB.database
    )

    # 1) 自动生成 sectors (来自 stocks.industry)
    print("=" * 60)
    print(" 6stockPulse · 板块聚合")
    print("=" * 60)

    industries = await pg.fetch("""
        SELECT DISTINCT industry FROM stocks
        WHERE industry IS NOT NULL AND industry != ''
        ORDER BY industry
    """)
    print(f"  发现 {len(industries)} 个 industry")

    # 用 industry 名称生成 sector_code
    sector_id = 0
    for r in industries:
        sector_id += 1
        code = f"IN{sector_id:04d}"
        name = r["industry"]
        await pg.execute("""
            INSERT INTO sectors (sector_code, sector_name, sector_type)
            VALUES ($1, $2, 'industry')
            ON CONFLICT (sector_code) DO UPDATE
            SET sector_name = EXCLUDED.sector_name
        """, code, name)
    print(f"  ✓ sectors 表已同步 ({sector_id} 个)")

    # 2) 获取最新交易日期
    latest_td = await pg.fetchval("""
        SELECT trade_date FROM fund_flow
        WHERE main_net_inflow IS NOT NULL
        ORDER BY trade_date DESC LIMIT 1
    """)
    if not latest_td:
        print("  ⚠️  fund_flow 为空, 无法聚合板块")
        await pg.close()
        return
    print(f"  最新交易日: {latest_td}")

    # 3) 按 industry 聚合
    rows = await pg.fetch("""
        SELECT
            s.industry AS sector_name,
            COUNT(DISTINCT s.stock_code)             AS members,
            ROUND(AVG(lq.change_pct)::numeric, 3)    AS avg_change_pct,
            ROUND(AVG(lq.turnover_rate)::numeric, 3) AS avg_turnover,
            SUM(ff.main_net_inflow)                  AS sum_main_inflow,
            ROUND(AVG(ff.main_net_ratio)::numeric, 3) AS avg_main_ratio,
            SUM(ff.super_large_net)                  AS sum_super,
            SUM(ff.large_net_inflow)                 AS sum_large,
            SUM(ff.mid_net_inflow)                   AS sum_mid,
            SUM(ff.small_net_inflow)                 AS sum_small
        FROM stocks s
        JOIN fund_flow ff ON ff.stock_code = s.stock_code
                          AND ff.trade_date = $1
        LEFT JOIN latest_quotes lq ON lq.stock_code = s.stock_code
                                    AND lq.trade_date = $1
        WHERE s.industry IS NOT NULL AND s.industry != ''
        GROUP BY s.industry
        ORDER BY sum_main_inflow DESC NULLS LAST
    """, latest_td)

    print(f"  聚合得到 {len(rows)} 个板块资金流向")

    # 4) 找到 sector_code (按 industry 名称)
    sector_map = {}
    sm = await pg.fetch("SELECT sector_code, sector_name FROM sectors WHERE sector_type='industry'")
    for s in sm:
        sector_map[s["sector_name"]] = s["sector_code"]

    # 5) 写入 sector_fund_flow
    inserted = 0
    for r in rows:
        name = r["sector_name"]
        code = sector_map.get(name)
        if not code:
            continue
        await pg.execute("""
            INSERT INTO sector_fund_flow
              (sector_code, sector_name, trade_date, trade_time,
               change_pct, main_net_inflow, main_net_ratio, turnover_rate,
               super_large_net, large_net, mid_net, small_net)
            VALUES ($1, $2, $3, NOW(), $4, $5, $6, $7, $8, $9, $10, $11)
        """,
            code, name, latest_td,
            r["avg_change_pct"], r["sum_main_inflow"], r["avg_main_ratio"],
            r["avg_turnover"], r["sum_super"], r["sum_large"],
            r["sum_mid"], r["sum_small"]
        )
        inserted += 1
    print(f"  ✓ sector_fund_flow 入库: {inserted} 条")

    # 6) 刷新物化视图
    await pg.execute("REFRESH MATERIALIZED VIEW latest_sector")
    print("  ✓ latest_sector 已刷新")

    # 7) 预览
    print("\n 板块 TOP 10 (按主力净流入):")
    print(f"  {'板块':<12} {'成员':<6} {'涨跌幅%':<10} {'主力净流入(亿)':<18} {'换手率%'}")
    for r in rows[:10]:
        inflow_yi = float(r["sum_main_inflow"] or 0) / 1e8
        print(f"  {r['sector_name']:<12} {r['members']:<6} "
              f"{r['avg_change_pct']!s:<10} {inflow_yi:>15,.2f}  {r['avg_turnover']!s}")

    await pg.close()
    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
