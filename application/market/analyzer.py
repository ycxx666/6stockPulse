"""
6stockPulse · 市场服务
========================
市场宽度 / 量能对比 / 分钟聚合 / 赚钱效应 / 连板梯队
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from typing import Any

from infrastructure.database import postgres as db
from infrastructure.cache.memory import cache, CacheKeys

logger = logging.getLogger(__name__)


# ────────── 市场宽度 ──────────
async def compute_and_store_breadth() -> dict:
    """计算全市场宽度并写入 market_breadth 表"""
    row = await db.fetch_one("""
        SELECT
            COUNT(*) FILTER (WHERE change_pct > 0)   AS up_count,
            COUNT(*) FILTER (WHERE change_pct < 0)   AS down_count,
            COUNT(*) FILTER (WHERE change_pct = 0)   AS flat_count,
            COUNT(*) FILTER (WHERE change_pct >= CASE
                WHEN LEFT(lq.stock_code, 3) IN ('688', '689', '300', '301') THEN 19.95
                WHEN LEFT(lq.stock_code, 1) IN ('4', '8') THEN 29.95
                WHEN s.stock_name LIKE '%ST%' THEN 4.95
                ELSE 9.95 END)  AS limit_up,
            COUNT(*) FILTER (WHERE change_pct <= -CASE
                WHEN LEFT(lq.stock_code, 3) IN ('688', '689', '300', '301') THEN 19.95
                WHEN LEFT(lq.stock_code, 1) IN ('4', '8') THEN 29.95
                WHEN s.stock_name LIKE '%ST%' THEN 4.95
                ELSE 9.95 END) AS limit_down,
            COUNT(*) FILTER (WHERE change_pct > 5)    AS big_up,
            COUNT(*) FILTER (WHERE change_pct < -5)   AS big_down,
            COUNT(*)                                   AS total
        FROM latest_quotes lq
        LEFT JOIN stocks s ON s.stock_code = lq.stock_code
    """)
    if not row or (row["total"] or 0) == 0:
        return {"up_count": 0, "down_count": 0, "flat_count": 0,
                "limit_up": 0, "limit_down": 0, "big_up": 0, "big_down": 0,
                "total": 0, "sentiment_score": 50}

    r = dict(row)
    up = r.get("up_count", 0) or 0
    total = max(r.get("total", 1) or 1, 1)
    up_pct = up / total * 100
    r["sentiment_score"] = max(5, min(95, int(up_pct * 0.65 + 35)))

    now = datetime.now()
    await db.execute("""
        INSERT INTO market_breadth
            (snapshot_time, up_count, down_count, flat_count, limit_up, limit_down,
             big_up, big_down, total, sentiment_score, source)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'computed')
        ON CONFLICT (snapshot_time) DO NOTHING
    """, now, r["up_count"], r["down_count"], r["flat_count"],
       r["limit_up"], r["limit_down"], r["big_up"], r["big_down"],
       r["total"], r["sentiment_score"])

    cache.set(CacheKeys.BREADTH_LATEST, r, ttl=30)
    return r


async def get_latest_breadth() -> dict:
    """获取最新市场宽度 (缓存优先)"""
    cached = cache.get(CacheKeys.BREADTH_LATEST)
    if cached:
        return cached
    row = await db.fetch_one("""
        SELECT up_count, down_count, flat_count, limit_up, limit_down,
               big_up, big_down, total, sentiment_score,
               snapshot_time::text AS snapshot_time
        FROM market_breadth
        ORDER BY snapshot_time DESC LIMIT 1
    """)
    if row:
        r = dict(row)
        cache.set(CacheKeys.BREADTH_LATEST, r, ttl=30)
        return r
    return await compute_and_store_breadth()


# ────────── 量能对比 ──────────
async def get_volume_comparison() -> dict:
    """今日 vs 昨日成交量/成交额 (每股取当日最后一条累计快照, 避免分钟明细重复求和)"""
    today = date.today()
    row = await db.fetch_one("""
        SELECT
            (SELECT COALESCE(SUM(amount), 0) FROM (
                SELECT DISTINCT ON (stock_code) amount
                FROM stock_prices WHERE trade_date = $1
                ORDER BY stock_code, trade_time DESC
            ) d) AS today_amount,
            (SELECT COALESCE(SUM(amount), 0) FROM (
                SELECT DISTINCT ON (stock_code) amount
                FROM stock_prices WHERE trade_date = $1 - INTERVAL '1 day'
                ORDER BY stock_code, trade_time DESC
            ) d) AS yesterday_amount,
            (SELECT COALESCE(SUM(volume), 0) FROM (
                SELECT DISTINCT ON (stock_code) volume
                FROM stock_prices WHERE trade_date = $1
                ORDER BY stock_code, trade_time DESC
            ) d) AS today_volume,
            (SELECT COALESCE(SUM(volume), 0) FROM (
                SELECT DISTINCT ON (stock_code) volume
                FROM stock_prices WHERE trade_date = $1 - INTERVAL '1 day'
                ORDER BY stock_code, trade_time DESC
            ) d) AS yesterday_volume
    """, today)
    if not row:
        return {"today_amount_yi": 0, "yesterday_amount_yi": 0,
                "ratio": 1.0, "trend": "持平"}
    r = dict(row)
    today_amt = (r.get("today_amount") or 0) / 1e8
    yesterday_amt = (r.get("yesterday_amount") or 0) / 1e8
    ratio = today_amt / max(yesterday_amt, 1)
    trend = "放量" if ratio > 1.2 else "缩量" if ratio < 0.8 else "持平"
    return {
        "today_amount_yi": round(today_amt, 2),
        "yesterday_amount_yi": round(yesterday_amt, 2),
        "ratio": round(ratio, 2),
        "trend": trend,
    }


# ────────── 赚钱效应 ──────────
async def get_profit_effect() -> dict:
    """计算赚钱效应指标 (简化版: 基于涨停股次日表现; 涨停按板别阈值判定)"""
    row = await db.fetch_one("""
        WITH yesterday_limit_up AS (
            SELECT p.stock_code FROM stock_prices p
            LEFT JOIN stocks s ON s.stock_code = p.stock_code
            WHERE p.trade_date = CURRENT_DATE - INTERVAL '1 day'
              AND p.change_pct >= CASE
                    WHEN LEFT(p.stock_code, 3) IN ('688', '689', '300', '301') THEN 19.95
                    WHEN LEFT(p.stock_code, 1) IN ('4', '8') THEN 29.95
                    WHEN s.stock_name LIKE '%ST%' THEN 4.95
                    ELSE 9.95 END
        ),
        today_perf AS (
            SELECT p.stock_code, p.change_pct
            FROM stock_prices p
            JOIN yesterday_limit_up y ON y.stock_code = p.stock_code
            WHERE p.trade_date = CURRENT_DATE
        )
        SELECT
            COUNT(*) AS sample_count,
            ROUND(AVG(change_pct)::numeric, 2) AS avg_open_return
        FROM today_perf
    """)
    if not row or not row.get("sample_count"):
        return {"sample_count": 0, "avg_open_return": 0,
                "avg_high_return": 0, "avg_close_return": 0}
    r = dict(row)
    r["avg_high_return"] = r.get("avg_open_return", 0)
    r["avg_close_return"] = r.get("avg_open_return", 0)
    return r


# ────────── 连板梯队 ──────────
async def get_limit_up_ladder() -> list[dict]:
    """连板梯队(连续交易日口径)。

    以“最近交易日”为尾,统计各股连续涨停(收盘涨幅达所属板别涨停阈值:
    科创创业 +20% / 北交所 +30% / 主板ST +5% / 主板 +10%)的**连续交易日**高度,
    再按高度分档(2板/3板/4板/5板+)。snapshot 与 /api/short-term/limit-up-ladder 共用本实现。
    """
    from datetime import date as _date, timedelta
    since = _date.today() - timedelta(days=30)

    # 全局交易日序列(近 30 天有数据的日期)
    day_rows = await db.fetch_all("""
        SELECT DISTINCT trade_date FROM stock_prices
        WHERE trade_date >= $1 ORDER BY trade_date
    """, since)
    days = [r["trade_date"] for r in day_rows]
    if not days:
        return []
    day_idx = {d: i for i, d in enumerate(days)}
    last_day = days[-1]

    # 每股“收盘涨停日”(按板别阈值判定)
    rows = await db.fetch_all("""
        SELECT p.stock_code, p.trade_date FROM (
            SELECT p.stock_code, p.trade_date,
                   (array_agg(p.change_pct ORDER BY p.trade_time DESC))[1] AS close_pct,
                   COALESCE(s.stock_name, '') AS stock_name
            FROM stock_prices p
            LEFT JOIN stocks s ON s.stock_code = p.stock_code
            WHERE p.trade_date >= $1
            GROUP BY p.stock_code, p.trade_date, s.stock_name
        ) t
        WHERE close_pct >= CASE
                WHEN LEFT(stock_code, 3) IN ('688', '689', '300', '301') THEN 19.95
                WHEN LEFT(stock_code, 1) IN ('4', '8') THEN 29.95
                WHEN stock_name LIKE '%ST%' THEN 4.95
                ELSE 9.95 END
        ORDER BY stock_code, trade_date
    """, since)

    by_stock: dict[str, list] = {}
    for r in rows:
        by_stock.setdefault(r["stock_code"], []).append(r["trade_date"])

    buckets: dict[int, int] = {}
    for code, dl in by_stock.items():
        idxs = sorted(day_idx[d] for d in dl if d in day_idx)
        if not idxs or idxs[-1] != day_idx[last_day]:
            continue  # 最近交易日未涨停,不计入梯队
        height, cur, j = 1, idxs[-1], len(idxs) - 2
        while j >= 0 and idxs[j] == cur - 1:
            height += 1
            cur = idxs[j]
            j -= 1
        if height >= 2:
            k = 5 if height >= 5 else height
            buckets[k] = buckets.get(k, 0) + 1

    if not buckets:
        return []
    max_c = max(buckets.values()) or 1
    out = []
    for h in sorted(buckets):
        label = "5板+" if h >= 5 else f"{h}板"
        out.append({"label": label, "count": buckets[h],
                    "bar_pct": round(buckets[h] / max_c * 100, 1)})
    return out


# ────────── 概念持续性 ──────────
async def get_concept_leaders(limit: int = 6) -> list[dict]:
    """概念领涨板块 (含持续性)"""
    rows = await db.fetch_all("""
        SELECT s.sector_code, s.sector_name,
               s.change_pct, s.main_net_inflow
        FROM v_sector_top s
        WHERE s.change_pct IS NOT NULL
        ORDER BY s.change_pct DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


# ────────── 分钟聚合: 指数分时图 ──────────
_SINA_INDEX_SYMBOL = {"000001": "sh000001", "399001": "sz399001", "399006": "sz399006"}

async def _fetch_sina_index_minutes(symbol: str, date_str: str | None,
                                    datalen: int = 248, all_days: bool = False) -> list[dict]:
    """从新浪获取真实指数分钟线(json), 返回 [{t,open,high,low,close,volume,amount}]
    datalen: 拉取记录数 (248≈最近一个交易日, 1500≈覆盖约6个交易日)
    all_days=True 时不做日期过滤, 返回窗口内全部交易日(由调用方按日期分组)
    注意: volume/amount 均为分钟增量(非累计), 全天求和即当日总量
    """
    import aiohttp, json, re
    from datetime import date as _date
    sina_sym = _SINA_INDEX_SYMBOL.get(symbol, "sh" + symbol)
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/"
           "CN_MarketDataService.getKLineData"
           f"?symbol={sina_sym}&scale=1&ma=no&datalen={datalen}")
    headers = {"Referer": "https://finance.sina.com.cn/"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return []
                txt = await r.text()
    except Exception:
        return []
    m = re.search(r'\[.*\]', txt, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    keep_date = None if all_days else (date_str or _date.today().isoformat())
    out = []
    for item in arr or []:
        day = str(item.get("day", ""))
        if keep_date and day[:10] != keep_date:
            continue
        out.append({
            "t": day,
            "open": float(item.get("open", 0) or 0),
            "high": float(item.get("high", 0) or 0),
            "low": float(item.get("low", 0) or 0),
            "close": float(item.get("close", 0) or 0),
            "volume": float(item.get("volume", 0) or 0),
            "amount": float(item.get("amount", 0) or 0),
        })
    return out


async def get_index_intraday(symbol: str = "000001", date_str: str | None = None) -> list[dict]:
    """
    获取指数分钟级 OHLCV (供前端分时图)
    symbol: 000001=上证指数, 399001=深证成指, 399006=创业板指
    数据源: 新浪指数分钟线; 失败返回空列表(前端显示空态)。
    禁止回退 stock_prices: 其中 '000001' 是平安银行(深市), 与指数无关。
    """
    return await _fetch_sina_index_minutes(symbol, date_str)


# ────────── 分钟量能对比 ──────────
async def get_minute_volume_comparison() -> dict:
    """
    上证指数 今日 vs 上一交易日 分钟级成交量对比 (数据源: 新浪指数分钟线 sh000001)。
    - 上一交易日取"今天之前最近一个有分钟数据的日期"(自动跳过周末/节假日)
    - today_total_amount / yesterday_total_amount = 当日分钟 amount 增量求和(真实成交额)
    返回 {today: [{t,open,high,low,close,volume,amount}], yesterday: [...],
          today_total_amount, yesterday_total_amount}
    注意: stock_prices 的 '000001' 是平安银行(深市), 严禁用于指数量能。
    """
    from datetime import date as _date
    today = _date.today()
    rows = await _fetch_sina_index_minutes("000001", None, datalen=1500, all_days=True)
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["t"][:10], []).append(r)
    today_key = today.isoformat()
    today_rows = by_day.get(today_key, [])
    prev_key = max((k for k in by_day if k < today_key), default=None)
    prev_rows = by_day.get(prev_key, []) if prev_key else []
    return {
        "today": today_rows,
        "yesterday": prev_rows,
        "today_total_amount": round(sum(r["amount"] for r in today_rows), 2),
        "yesterday_total_amount": round(sum(r["amount"] for r in prev_rows), 2),
    }


# ────────── 概念持续性评分 ──────────
async def get_concept_sustainability(limit: int = 10) -> list[dict]:
    """
    概念领导力 = 25% 当日涨幅 + 20% 主力净流入 + 15% 上涨成分股占比
               + 15% 龙头贡献度 + 10% 连续强势天数 + 10% 新闻热度 + 5% 涨停数量
    """
    rows = await db.fetch_all("""
        SELECT s.sector_code, s.sector_name, s.sector_type,
               s.change_pct, s.main_net_inflow, s.main_net_ratio, s.turnover_rate
        FROM v_sector_top s
        ORDER BY s.change_pct DESC
        LIMIT $1
    """, limit * 2)  # 多取一些做筛选

    result = []
    for r in rows:
        d = dict(r)
        inflow = abs(d.get("main_net_inflow") or 0) / 1e8
        chg = d.get("change_pct") or 0
        # 简化评分
        score = (
            0.25 * min(100, max(0, chg * 10 + 50)) +
            0.20 * min(100, inflow * 2) +
            0.25 * 50  # 默认中性
        )
        d["sustainability_score"] = round(score, 1)
        d["consecutive_days"] = 1  # 简化
        d["risk_tags"] = []
        if inflow < 0 and chg > 0:
            d["risk_tags"].append("资金流出")
        result.append(d)

    result.sort(key=lambda x: x["sustainability_score"], reverse=True)
    return result[:limit]


# ────────── 赚钱效应 (增强版) ──────────
async def get_enhanced_profit_effect() -> dict:
    """
    增强版赚钱效应: 上一交易日涨停股今日表现 + 封板率/炸板率(基于分钟数据真实计算)。
    - 上一交易日: 取 stock_prices 中 < today 的最大 trade_date(真实交易日, 非 today-1)
    - 日聚合: 每只股票 今开=首分钟 open、今高=当日 max(high)、今收=末分钟 close
    - 涨停价判定: 主板 +10% / ST +5% / 科创创业 +20% / 北交所 +30%
      触及涨停且收盘封住 = 封板; 触及涨停未封住 = 炸板
    返回字段与前端对齐: sample_count / avg_open_return / avg_high_return /
                      avg_close_return / seal_rate / break_rate
    """
    from datetime import date as _date
    today = _date.today()
    empty = {"sample_count": 0, "sample": 0, "avg_open_return": 0,
             "avg_high_return": 0, "avg_close_return": 0,
             "seal_rate": 0, "break_rate": 0,
             "no_data": True, "note": "暂无样本（上一交易日无涨停股或数据未更新）"}

    # 1) 真实上一交易日(不依赖 today-1)
    prev_date = await db.fetch_val("""
        SELECT MAX(trade_date) FROM (
            SELECT DISTINCT trade_date FROM stock_prices WHERE trade_date < $1
        ) t
    """, today)
    if not prev_date:
        return empty

    # 2) 昨日涨停股 + 今日日聚合(每股一行)
    rows = await db.fetch_all("""
        WITH y_limit AS (
            SELECT p.stock_code, MAX(p.close) AS prev_close
            FROM stock_prices p
            LEFT JOIN stocks s ON s.stock_code = p.stock_code
            WHERE p.trade_date = $1
              AND p.change_pct >= CASE
                    WHEN LEFT(p.stock_code, 3) IN ('688', '689', '300', '301') THEN 19.95
                    WHEN LEFT(p.stock_code, 1) IN ('4', '8') THEN 29.95
                    WHEN s.stock_name LIKE '%ST%' THEN 4.95
                    ELSE 9.95 END
            GROUP BY p.stock_code
        ),
        today_agg AS (
            SELECT p.stock_code,
                   (array_agg(p.open  ORDER BY p.trade_time))[1]      AS open_p,
                   MAX(p.high)                                        AS high_p,
                   (array_agg(p.close ORDER BY p.trade_time DESC))[1] AS close_p
            FROM stock_prices p
            JOIN y_limit y ON y.stock_code = p.stock_code
            WHERE p.trade_date = $2
            GROUP BY p.stock_code
        )
        SELECT a.stock_code, y.prev_close, a.open_p, a.high_p, a.close_p,
               COALESCE(s.stock_name, '') AS stock_name
        FROM today_agg a
        JOIN y_limit y ON y.stock_code = a.stock_code
        LEFT JOIN stocks s ON s.stock_code = a.stock_code
    """, prev_date, today)

    n = len(rows)
    if not n:
        return empty

    def _limit_rate(code: str, name: str) -> float:
        # 板别优先: 科创/创业板(含其ST)20%, 北交所30%, 主板ST 5%, 主板10%
        c = code or ""
        if c[:3] in ("300", "301", "688", "689"):
            return 0.20
        if c[:1] in ("4", "8"):   # 北交所
            return 0.30
        nm = (name or "").upper().replace(" ", "")
        if "ST" in nm:
            return 0.05
        return 0.10

    acc_o = acc_h = acc_c = 0.0
    sealed = broke = 0
    EPS = 1e-6
    for r in rows:
        pc = float(r["prev_close"] or 0)
        if pc <= 0:
            continue
        op = float(r["open_p"] or pc)
        hi = float(r["high_p"] or pc)
        cl = float(r["close_p"] or pc)
        acc_o += (op - pc) / pc * 100
        acc_h += (hi - pc) / pc * 100
        acc_c += (cl - pc) / pc * 100
        limit_price = round(pc * (1 + _limit_rate(r["stock_code"], r["stock_name"])), 2)
        touched = hi >= limit_price - EPS
        closed_sealed = cl >= limit_price - EPS
        if touched and closed_sealed:
            sealed += 1
        elif touched and not closed_sealed:
            broke += 1

    return {
        "sample_count": n,
        "sample": n,                       # 兼容旧字段名
        "avg_open_return": round(acc_o / n, 2),
        "avg_high_return": round(acc_h / n, 2),
        "avg_close_return": round(acc_c / n, 2),
        "seal_rate": round(sealed / n, 3),
        "break_rate": round(broke / n, 3),
        "prev_trade_date": prev_date.isoformat(),
        "trade_date": today.isoformat(),
    }
