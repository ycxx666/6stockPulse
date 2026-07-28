"""
scripts/import_from_sqlite.py
==============================
从现有 SQLite 数据库导入历史数据到 PostgreSQL:

数据源
------
  · /Users/davi60/Desktop/6newsPulse/东方财富/data/finance.db
      stocks (code, name, price, change_pct, turnover, amount,
              main_net_inflow, super_large_inflow, large_inflow,
              medium_inflow, small_inflow, sentiment_label,
              keywords, collect_date, collect_time)
      crawl_log / crawl_checkpoint  — 审计/状态, 不导入

  · /Users/davi60/Desktop/6newsPulse/东方财富2/eastmoney.db
      fund_flow (stock_code, trade_date, stock_name, price, pct_change,
                 main_net_inflow, main_net_ratio, super_large_net,
                 super_large_ratio, large_net_inflow, large_ratio,
                 mid_net_inflow, mid_ratio, small_net_inflow,
                 small_ratio, source_url, crawl_time)

字段差异 & 映射
--------------
  · 东方财富 SQLite 没有 `industry` / `market` 列
      → market 由股票代码前缀推导 (6/5/9=SH, 0/3=SZ, 4/8=BJ)
      → industry 从 keywords 第一项 (如果有) 提取, 否则 NULL
  · 东方财富 SQLite 没有 `trade_time` 字段
      → 使用 collect_date + collect_time 组合
  · 东方财富2 fund_flow 没有 `sentiment` 字段
      → 通过 main_net_inflow 自动判断 (>=0 bullish, <0 bearish)
  · sentiment_label (消极/中性/积极) → ENUM(bearish/neutral/bullish)
"""
from __future__ import annotations
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg
from config import DB


# ────────── 工具函数 ──────────
def to_date(s: Any) -> date | None:
    """字符串 → date (兼容 'YYYY-MM-DD')"""
    if s is None:
        return None
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def to_ts(s: Any) -> datetime | None:
    """字符串 → datetime (兼容 'YYYY-MM-DD' / ISO)"""
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    txt = str(s)
    if "." in txt:
        txt = txt.split(".")[0]
    try:
        return datetime.fromisoformat(txt.replace("T", " "))
    except Exception:
        try:
            return datetime.fromisoformat(txt[:10])
        except Exception:
            return None


SRC_EM1 = "/Users/davi60/Desktop/6newsPulse/东方财富/data/finance.db"
SRC_EM2 = "/Users/davi60/Desktop/6newsPulse/东方财富2/eastmoney.db"


# ────────── 工具函数 ──────────
def detect_market(code: str) -> str:
    """根据股票代码前缀推导市场"""
    if not code:
        return "SH"
    s = str(code)
    if s.startswith(("60", "68", "90", "11", "13", "5")):   # 沪市主板/科创板/B股/债券
        return "SH"
    if s.startswith(("00", "30", "20")):                    # 深市主板/创业板/B股
        return "SZ"
    if s.startswith(("43", "83", "87", "92")):              # 北交所
        return "BJ"
    if s.startswith(("4", "8")):                            # 北交所/科创老代码
        return "BJ"
    return "SH"


def map_sentiment_cn(label: str | None) -> str:
    """中文情绪标签 → ENUM"""
    if not label:
        return "neutral"
    label = str(label).strip()
    if label in ("积极", "利好", "看多", "bullish", "强", "+"):
        return "bullish"
    if label in ("消极", "利空", "看空", "bearish", "弱", "-"):
        return "bearish"
    return "neutral"


def parse_trade_dt(d: str, t: str | None) -> datetime:
    """合并日期 + 时间到 datetime"""
    try:
        if t and "T" in t:
            return datetime.fromisoformat(t)
        if t:
            return datetime.fromisoformat(f"{d}T{t}")
    except Exception:
        pass
    try:
        return datetime.fromisoformat(d)
    except Exception:
        return datetime.now()


def chunked(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ────────── 数据库连接 ──────────
async def pg_connect():
    return await asyncpg.connect(
        host=DB.host, port=DB.port, user=DB.user,
        password=DB.password, database=DB.database
    )


# ══════════════════════════════════════════════════════════
# 1. 导入 stocks (来自 东方财富 finance.db)
# ══════════════════════════════════════════════════════════
async def import_stocks_from_em1(verbose: bool = True) -> int:
    """导入 / 增量更新 stocks 表"""
    if not Path(SRC_EM1).exists():
        print(f"  ⚠️  {SRC_EM1} 不存在")
        return 0
    conn = sqlite3.connect(SRC_EM1)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT code, name, price, change_pct, turnover, amount,
               main_net_inflow, super_large_inflow, large_inflow,
               medium_inflow, small_inflow, sentiment_label,
               keywords, collect_date, collect_time
        FROM stocks
        WHERE code IS NOT NULL
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    print(f"  源数据: {len(rows)} 条 stocks")

    pg = await pg_connect()
    inserted = 0
    failed = 0
    for r in rows:
        try:
            code = str(r["code"]).strip()
            name = (r.get("name") or "").strip()
            if not code or not name:
                continue
            market = detect_market(code)
            # industry: 尝试从 keywords 第一项提取, 否则 NULL
            industry = None
            kw = r.get("keywords")
            if kw:
                if "," in kw:
                    industry = kw.split(",")[0].strip()
                else:
                    industry = kw.strip()
            await pg.execute("""
                INSERT INTO stocks (stock_code, stock_name, market, industry, extra)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (stock_code) DO UPDATE
                SET stock_name = EXCLUDED.stock_name,
                    market     = EXCLUDED.market,
                    industry   = EXCLUDED.industry,
                    extra      = EXCLUDED.extra,
                    updated_at = NOW()
            """,
                code, name, market, industry,
                json.dumps({
                    "last_change_pct": r.get("change_pct"),
                    "last_turnover":  r.get("turnover"),
                    "last_amount":     r.get("amount"),
                    "last_collect":    f"{r.get('collect_date','')} {r.get('collect_time','')}".strip(),
                }, ensure_ascii=False)
            )
            inserted += 1
        except Exception as e:
            failed += 1
            if verbose and failed < 5:
                print(f"  ⚠️  {r.get('code')}: {e}")
    await pg.close()
    print(f"  ✓ stocks 入库: {inserted} 条 (失败 {failed})")
    return inserted


# ══════════════════════════════════════════════════════════
# 2. 导入 fund_flow (来自 东方财富2 eastmoney.db)
# ══════════════════════════════════════════════════════════
async def import_fund_flow_from_em2(verbose: bool = True) -> int:
    """导入 fund_flow 表"""
    if not Path(SRC_EM2).exists():
        print(f"  ⚠️  {SRC_EM2} 不存在")
        return 0
    conn = sqlite3.connect(SRC_EM2)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT stock_code, trade_date, stock_name, price, pct_change,
               main_net_inflow, main_net_ratio,
               super_large_net, super_large_ratio,
               large_net_inflow, large_ratio,
               mid_net_inflow, mid_ratio,
               small_net_inflow, small_ratio,
               source_url, crawl_time
        FROM fund_flow
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    print(f"  源数据: {len(rows)} 条 fund_flow")

    pg = await pg_connect()
    inserted = 0
    failed = 0
    # 1) 先把 stock_name 同步到 stocks 表
    seen_stocks: set[str] = set()
    for r in rows:
        code = str(r["stock_code"]).strip()
        name = (r.get("stock_name") or "").strip()
        if code and name and code not in seen_stocks:
            seen_stocks.add(code)
            try:
                market = detect_market(code)
                await pg.execute("""
                    INSERT INTO stocks (stock_code, stock_name, market)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (stock_code) DO UPDATE
                    SET stock_name = EXCLUDED.stock_name
                """, code, name, market)
            except Exception:
                pass

    # 2) 批量插入 fund_flow
    for r in rows:
        try:
            code = str(r["stock_code"]).strip()
            td = to_date(r["trade_date"])
            if not code or not td:
                continue
            # 解析 trade_time: 优先用 crawl_time (更精确), 退化到 trade_date
            trade_time = to_ts(r.get("crawl_time")) or datetime.combine(td, datetime.min.time())
            # 情绪: 根据主力净流入判断
            mni = r.get("main_net_inflow") or 0
            sentiment = "bullish" if mni > 0 else ("bearish" if mni < 0 else "neutral")

            await pg.execute("""
                INSERT INTO fund_flow
                  (stock_code, trade_date, trade_time,
                   main_net_inflow, main_net_ratio,
                   super_large_net, super_large_ratio,
                   large_net_inflow, large_ratio,
                   mid_net_inflow, mid_ratio,
                   small_net_inflow, small_ratio, sentiment)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::sentiment_t)
                ON CONFLICT (trade_date, stock_code, trade_time) DO NOTHING
            """,
                code, td, trade_time,
                r.get("main_net_inflow"), r.get("main_net_ratio"),
                r.get("super_large_net"), r.get("super_large_ratio"),
                r.get("large_net_inflow"), r.get("large_ratio"),
                r.get("mid_net_inflow"), r.get("mid_ratio"),
                r.get("small_net_inflow"), r.get("small_ratio"),
                sentiment
            )
            inserted += 1
        except Exception as e:
            failed += 1
            if verbose and failed < 5:
                print(f"  ⚠️  fund_flow {r.get('stock_code')} {r.get('trade_date')}: {e}")
    await pg.close()
    print(f"  ✓ fund_flow 入库: {inserted} 条 (失败 {failed})")
    return inserted


# ══════════════════════════════════════════════════════════
# 3. 构造 stock_prices (来自 fund_flow 的 price + pct_change)
# ══════════════════════════════════════════════════════════
async def import_prices_from_em2(verbose: bool = True) -> int:
    """从 fund_flow 的 price + pct_change 构造 stock_prices 历史快照"""
    if not Path(SRC_EM2).exists():
        return 0
    conn = sqlite3.connect(SRC_EM2)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT stock_code, trade_date, price, pct_change, crawl_time
        FROM fund_flow
        WHERE price > 0
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    print(f"  源数据: {len(rows)} 条 price 快照")

    pg = await pg_connect()
    inserted = 0
    failed = 0
    for r in rows:
        try:
            code = str(r["stock_code"]).strip()
            td = to_date(r["trade_date"])
            price = r.get("price")
            pct = r.get("pct_change") or 0
            if not code or not td or not price:
                continue
            trade_time = to_ts(r.get("crawl_time")) or datetime.combine(td, datetime.min.time())
            # 反推昨收 (close): price = prev_close * (1 + pct/100)
            prev_close = price / (1 + pct / 100) if pct != -100 else price
            change_amt = price - prev_close
            await pg.execute("""
                INSERT INTO stock_prices
                  (stock_code, trade_date, trade_time,
                   open, high, low, close,
                   change_pct, change_amt)
                VALUES ($1,$2,$3,$4,$4,$4,$4,$5,$6)
                ON CONFLICT (trade_date, stock_code, trade_time) DO NOTHING
            """, code, td, trade_time, price, pct, change_amt)
            inserted += 1
        except Exception as e:
            failed += 1
            if verbose and failed < 5:
                print(f"  ⚠️  price {r.get('stock_code')}: {e}")
    await pg.close()
    print(f"  ✓ stock_prices 入库: {inserted} 条 (失败 {failed})")
    return inserted


# ══════════════════════════════════════════════════════════
# 4. 导入 东方财富 finance.db 的快照价格 (备用)
# ══════════════════════════════════════════════════════════
async def import_prices_from_em1(verbose: bool = True) -> int:
    """把 finance.db 中每个 collect_date 当日的快照作为 stock_prices"""
    if not Path(SRC_EM1).exists():
        return 0
    conn = sqlite3.connect(SRC_EM1)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT code, name, price, change_pct, turnover, amount,
               collect_date, collect_time
        FROM stocks
        WHERE price > 0 AND collect_date IS NOT NULL
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    print(f"  源数据: {len(rows)} 条 em1 price 快照")

    pg = await pg_connect()
    inserted = 0
    failed = 0
    for r in rows:
        try:
            code = str(r["code"]).strip()
            td = to_date(r.get("collect_date"))
            price = r.get("price")
            pct = r.get("change_pct") or 0
            if not code or not td or not price:
                continue
            trade_time = to_ts(r.get("collect_time")) or datetime.combine(td, datetime.min.time())
            prev_close = price / (1 + pct / 100) if pct != -100 else price
            change_amt = price - prev_close
            await pg.execute("""
                INSERT INTO stock_prices
                  (stock_code, trade_date, trade_time,
                   open, high, low, close, volume, amount,
                   change_pct, change_amt, turnover_rate)
                VALUES ($1,$2,$3,$4,$4,$4,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (trade_date, stock_code, trade_time) DO NOTHING
            """,
                code, td, trade_time, price,
                int(r.get("turnover") or 0), r.get("amount") or 0,
                pct, change_amt, r.get("turnover") or 0
            )
            inserted += 1
        except Exception as e:
            failed += 1
            if verbose and failed < 5:
                print(f"  ⚠️  em1 price {r.get('code')}: {e}")
    await pg.close()
    print(f"  ✓ stock_prices (em1) 入库: {inserted} 条 (失败 {failed})")
    return inserted


# ══════════════════════════════════════════════════════════
# 5. 刷新物化视图
# ══════════════════════════════════════════════════════════
async def refresh_views() -> None:
    pg = await pg_connect()
    for view in ("latest_quotes", "latest_sector"):
        try:
            await pg.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}")
            print(f"  ✓ {view} 已刷新 (CONCURRENTLY)")
        except Exception as e:
            try:
                await pg.execute(f"REFRESH MATERIALIZED VIEW {view}")
                print(f"  ✓ {view} 已刷新")
            except Exception as e2:
                print(f"  ⚠️  {view} 刷新失败: {e2}")
    await pg.close()


# ══════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════
async def main():
    print("=" * 64)
    print(" 6stockPulse · 数据导入 (SQLite → PostgreSQL)")
    print("=" * 64)

    print("\n[1/4] Importing stocks (from 东方财富/data/finance.db) ...")
    n1 = await import_stocks_from_em1()

    print("\n[2/4] Importing fund_flow (from 东方财富2/eastmoney.db) ...")
    n2 = await import_fund_flow_from_em2()

    print("\n[3/4] Importing stock_prices (from 东方财富2/eastmoney.db) ...")
    n3 = await import_prices_from_em2()

    print("\n[3b/4] Importing stock_prices (from 东方财富/finance.db) ...")
    n3b = await import_prices_from_em1()

    print("\n[4/4] Refreshing materialized views ...")
    await refresh_views()

    print("\n" + "=" * 64)
    print(f" 总计入库: stocks={n1}, fund_flow={n2}, "
          f"prices(em2)={n3}, prices(em1)={n3b}")
    print("=" * 64)

    # 最终统计
    pg = await pg_connect()
    for tbl in ("stocks", "fund_flow", "stock_prices", "news",
                "stock_news", "stock_announcements", "sectors"):
        n = await pg.fetchval(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  · {tbl:24s} {n:>10,} 行")
    await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
