"""
数据质量校验脚本
  1) 恒等校验：turnover_rate ≈ amount / (circ_shares * close) * 100
  2) 资金量级：去重后全市场主力净流入，> 2000 亿标记异常
  3) 时间戳新鲜度：各表最新时间与当前时间差

用法：python scripts/data_quality_check.py
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.database import postgres as db


async def check_identity(limit: int = 500) -> dict:
    rows = await db.fetch_all("""
        SELECT p.stock_code, p.close, p.amount, p.turnover_rate, s.circ_shares
        FROM latest_quotes p
        JOIN stocks s ON s.stock_code = p.stock_code
        WHERE p.turnover_rate IS NOT NULL AND s.circ_shares > 0 AND p.close > 0
        LIMIT $1
    """, limit)
    bad = 0
    for r in rows:
        calc = float(r["amount"]) / (float(r["circ_shares"]) * float(r["close"])) * 100
        if abs(calc - float(r["turnover_rate"])) > 0.5:  # 偏差 > 0.5 个百分点
            bad += 1
    return {"total": len(rows), "bad": bad}


async def check_fund() -> dict:
    row = await db.fetch_one("""
        SELECT COALESCE(SUM(main_net_inflow), 0) / 1e8 AS total
        FROM (
            SELECT DISTINCT ON (stock_code) stock_code, main_net_inflow
            FROM fund_flow WHERE trade_date = CURRENT_DATE
            ORDER BY stock_code, trade_time DESC
        ) t
    """)
    total = float(row["total"]) if row else 0.0
    return {"total_yi": total, "abnormal": abs(total) > 2000}


async def check_freshness() -> list[dict]:
    now = datetime.now(timezone.utc)
    checks = [
        ("stock_prices", "trade_time"),
        ("market_breadth", "snapshot_time"),
        ("news", "publish_time"),
        ("sector_fund_flow", "trade_time"),
        ("global_indices", "trade_time"),
    ]
    out = []
    for table, col in checks:
        try:
            v = await db.fetch_val(f"SELECT max({col}) FROM {table}")
        except Exception:
            v = None
        age = None
        if v is not None:
            dt = v if getattr(v, "tzinfo", None) else v.replace(tzinfo=timezone.utc)
            age = int((now - dt).total_seconds())
        out.append({"table": table, "age_seconds": age, "stale": age is None or age > 900})
    return out


async def check_anomalies() -> list[dict]:
    """异常跳变检测：板块涨跌幅 >20%、单只主力资金 >100 亿"""
    out = []
    try:
        rows = await db.fetch_all("""
            SELECT sector_name, change_pct FROM v_sector_top
            WHERE ABS(change_pct) > 20 LIMIT 10
        """)
        for r in rows:
            out.append({"type": "sector_jump",
                        "detail": f"{r['sector_name']} 涨跌幅 {float(r['change_pct']):.2f}%"})
    except Exception:
        pass
    try:
        rows = await db.fetch_all("""
            SELECT stock_code, main_net_inflow / 1e8 AS yi
            FROM fund_flow
            WHERE trade_date = CURRENT_DATE AND ABS(main_net_inflow) > 100e8
            ORDER BY ABS(main_net_inflow) DESC LIMIT 10
        """)
        for r in rows:
            out.append({"type": "fund_jump",
                        "detail": f"{r['stock_code']} 主力净流入 {float(r['yi']):.1f} 亿"})
    except Exception:
        pass
    return out


async def main():
    await db.init_pool()
    try:
        identity = await check_identity()
        fund = await check_fund()
        freshness = await check_freshness()
        print("=== 恒等校验（换手率 vs 成交额/流通市值）===")
        print(f"  检查 {identity['total']} 条，偏差>0.5pp: {identity['bad']}")
        print("=== 资金量级 ===")
        print(f"  主力净流入: {fund['total_yi']:.1f} 亿, 异常(>2000亿): {fund['abnormal']}")
        print("=== 时间戳新鲜度 ===")
        for f in freshness:
            print(f"  {f['table']}: age={f['age_seconds']}s stale={f['stale']}")
        anomalies = await check_anomalies()
        print("=== 异常跳变检测 ===")
        if anomalies:
            for a in anomalies:
                print(f"  [{a['type']}] {a['detail']}")
        else:
            print("  无异常")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
