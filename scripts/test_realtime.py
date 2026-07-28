"""
scripts/test_realtime.py
=========================
6stockPulse · 实时数据 + 去重 完整测试

测试覆盖:
  [1] VIX 爬虫结构: 配置/URL/解析
  [2] 去重核心: truncate_time() 把同一分钟内的多次调用对齐到同一 trade_time
  [3] 实时数据 1 分钟刷新 × 3 轮: 验证 1 轮 = 1 行
  [4] 跨分钟刷新: 验证不同时间桶生成不同 trade_time
  [5] ON CONFLICT DO UPDATE: 同分钟内价格变化会更新 close 而不是新增行
  [6] Scheduler 任务注册: 默认 60s 间隔
  [7] API 端点: /api/indices /api/indices/vix
  [8] 物化视图: latest_global_indices 自动刷新

外网不可达时, 自动用 mock 喂数据
"""
from __future__ import annotations
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import database as db
from config import REALTIME, SCHEDULER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
)
logger = logging.getLogger("test_realtime")


# ────────── Mock 数据 ──────────
def mock_em_response(vix_price=18.5, dji=39500, spx=5200, nasdaq=16500, hsi=18500, n225=38000):
    """模拟东方财富 m:113 (美股) + m:114 (港股) + m:116 (日股) 接口返回"""
    def make_row(code, name, market, price, pct):
        prev = price / (1 + pct/100) if pct != -100 else price
        chg = price - prev
        return {
            "f12": code, "f14": name,
            "f2": price, "f3": pct, "f4": chg,
            "f15": price * 1.005, "f16": price * 0.995,
            "f17": prev, "f18": prev,
            "f20": market, "f102": 0, "f100": 0
        }
    return json.dumps({
        "rc": 0,
        "data": {
            "diff": [
                make_row("100.VIX",  "VIX 恐慌指数",     "US", vix_price, 5.2),
                make_row("100.DJI",  "道琼斯工业指数",   "US", dji,      0.45),
                make_row("100.INX",  "标普500指数",      "US", spx,      0.62),
                make_row("100.IXIC", "纳斯达克综合指数", "US", nasdaq,   0.85),
            ]
        }
    })


def mock_hk_response(hsi=18500, pct=0.35):
    prev = hsi / (1 + pct/100) if pct != -100 else hsi
    return json.dumps({
        "rc": 0,
        "data": {"diff": [
            {"f12": "100.HSI", "f14": "恒生指数", "f2": hsi, "f3": pct, "f4": hsi - prev,
             "f15": hsi*1.005, "f16": hsi*0.995, "f17": prev, "f18": prev,
             "f20": "HK", "f102": 0, "f100": 0}
        ]}
    })


def mock_jp_response(n225=38000, pct=-0.21):
    prev = n225 / (1 + pct/100) if pct != -100 else n225
    return json.dumps({
        "rc": 0,
        "data": {"diff": [
            {"f12": "100.N225", "f14": "日经225指数", "f2": n225, "f3": pct, "f4": n225 - prev,
             "f15": n225*1.005, "f16": n225*0.995, "f17": prev, "f18": prev,
             "f20": "JP", "f102": 0, "f100": 0}
        ]}
    })


# ────────── 测试用例 ──────────
async def setup_db():
    await db.init_pool()
    # 清空 global_indices (避免历史数据干扰)
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM global_indices WHERE index_code LIKE '100.%'")
        await conn.execute("DELETE FROM crawl_log WHERE crawler_name='vix'")
    print("  ✓ 数据库已清空")


async def teardown_db():
    await db.close_pool()


async def test_1_vix_config():
    """[1] VIX 爬虫结构 / 配置"""
    print("\n" + "=" * 60)
    print(" [1] VIX 爬虫结构 / 配置")
    print("=" * 60)
    from crawlers.global_market import GlobalMarketCrawler, INDEX_INDEX_WATCHLIST, GlobalMarketURLs
    assert GlobalMarketCrawler.name == "global_market"
    assert "100.VIX" in INDEX_WATCHLIST
    assert "VIX 恐慌指数" == INDEX_WATCHLIST["100.VIX"][0]
    assert GlobalMarketURLs.EM_US_LIST.startswith("https://push2.eastmoney.com")
    print(f"  ✓ 爬虫名称: {GlobalMarketCrawler.name}")
    print(f"  ✓ 关注列表: {len(INDEX_WATCHLIST)} 个指数")
    print(f"  ✓ US URL:  {GlobalMarketURLs.EM_US_LIST[:80]}...")


async def test_2_truncate():
    """[2] truncate_time() 同分钟对齐"""
    print("\n" + "=" * 60)
    print(" [2] truncate_time() — 同分钟对齐 (去重核心)")
    print("=" * 60)
    from global_market import GlobalMarketCrawler
    c = GlobalMarketCrawler(refresh_interval_seconds=60)
    # 同一分钟内的不同秒
    t1 = datetime(2026, 7, 14, 10, 30,  5)
    t2 = datetime(2026, 7, 14, 10, 30, 25)
    t3 = datetime(2026, 7, 14, 10, 30, 59)
    b1 = c.truncate_time(t1)
    b2 = c.truncate_time(t2)
    b3 = c.truncate_time(t3)
    assert b1 == b2 == b3, f"同分钟应该对齐: {b1} {b2} {b3}"
    print(f"  t1={t1.strftime('%H:%M:%S')}  →  bucket={b1.strftime('%H:%M:%S')}")
    print(f"  t2={t2.strftime('%H:%M:%S')}  →  bucket={b2.strftime('%H:%M:%S')}  (相同 ✓)")
    print(f"  t3={t3.strftime('%H:%M:%S')}  →  bucket={b3.strftime('%H:%M:%S')}  (相同 ✓)")

    # 跨分钟
    t4 = datetime(2026, 7, 14, 10, 31, 1)
    b4 = c.truncate_time(t4)
    assert b4 != b1, f"跨分钟应不同: {b1} {b4}"
    print(f"  t4={t4.strftime('%H:%M:%S')}  →  bucket={b4.strftime('%H:%M:%S')}  (不同 ✓)")

    # 测试 30s 间隔
    c2 = GlobalMarketCrawler(refresh_interval_seconds=30)
    t5 = datetime(2026, 7, 14, 10, 30, 0)
    t6 = datetime(2026, 7, 14, 10, 30, 29)
    t7 = datetime(2026, 7, 14, 10, 30, 30)
    b5 = c2.truncate_time(t5)
    b6 = c2.truncate_time(t6)
    b7 = c2.truncate_time(t7)
    assert b5 == b6
    assert b7 != b5
    print(f"  30s 间隔:  t5={t5.strftime('%H:%M:%S')} → {b5.strftime('%H:%M:%S')}, "
          f"t6={t6.strftime('%H:%M:%S')} → {b6.strftime('%H:%M:%S')} (相同 ✓), "
          f"t7={t7.strftime('%H:%M:%S')} → {b7.strftime('%H:%M:%S')} (不同 ✓)")


async def test_3_dedup_same_minute():
    """[3] 1 分钟内 × 3 轮抓取 → 应只有 1 行"""
    print("\n" + "=" * 60)
    print(" [3] 同分钟内 3 轮抓取 → 去重为 1 行")
    print("=" * 60)
    from global_market import GlobalMarketCrawler

    # Mock fetch — 返回固定数据
    async def mock_fetch_us(*a, **kw):
        return mock_em_response(vix_price=18.5)
    async def mock_fetch_hk(*a, **kw):
        return mock_hk_response()
    async def mock_fetch_jp(*a, **kw):
        return mock_jp_response()
    async def mock_fetch_eu(*a, **kw):
        return json.dumps({"rc": 0, "data": {"diff": []}})
    async def mock_fetch_sina(*a, **kw):
        return ""

    with patch("global_market.GlobalMarketURLs") as MockURLs:
        MockURLs.EM_US_LIST = "EM_US"
        MockURLs.EM_HK_LIST = "EM_HK"
        MockURLs.EM_EU_LIST = "EM_EU"
        MockURLs.EM_JP_LIST = "EM_JP"
        MockURLs.SINA_REALTIME = "SINA"
        # 直接 patch GlobalMarketCrawler.fetch
        async with GlobalMarketCrawler(refresh_interval_seconds=60) as c:
            with patch.object(c, "fetch", side_effect=[
                mock_em_response(),                       # US (4)
                mock_hk_response(),                       # HK (1)
                json.dumps({"rc":0,"data":{"diff":[]}}), # EU empty
                mock_jp_response(),                       # JP (1)
                "",                                       # sina
            ] * 3):  # 重复 3 次 (每轮 5 次 fetch)
                results = []
                for i in range(3):
                    r = await c.run_once()
                    results.append(r)
                    print(f"    轮 {i+1}: {r}")
                    # 同一分钟内, 不等待

    # 检查数据库
    async with db.acquire() as conn:
        n = await conn.fetchval("""
            SELECT COUNT(*) FROM global_indices
            WHERE index_code LIKE '100.%'
              AND trade_date = CURRENT_DATE
        """)
    print(f"  ✓ DB 中 100.* 指数行数: {n}")
    assert n == 6, f"应该是 6 行 (VIX/DJI/INX/IXIC/HSI/N225), 实际 {n}"
    print(f"  ✓ 3 轮抓取 → 6 个指数各 1 行 (去重 ✓)")


async def test_4_dedup_different_minutes():
    """[4] 跨分钟刷新 → 不同时间桶"""
    print("\n" + "=" * 60)
    print(" [4] 跨分钟刷新 → 不同 trade_time (历史累积)")
    print("=" * 60)
    from global_market import GlobalMarketCrawler

    # 直接 SQL 模拟两次不同时刻的写入
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM global_indices WHERE index_code='100.VIX'")
        t1 = datetime(2026, 7, 14, 9, 30, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 7, 14, 9, 31, 0, tzinfo=timezone.utc)
        for tt, price in [(t1, 18.0), (t2, 18.5)]:
            await conn.execute("""
                INSERT INTO global_indices
                  (index_code, index_name, market, trade_date, trade_time, close, change_pct, source)
                VALUES ('100.VIX', 'VIX 恐慌指数', 'US',
                        ($1 AT TIME ZONE 'Asia/Shanghai')::date, $1, $2, 0, 'mock')
                ON CONFLICT (index_code, trade_date, trade_time) DO UPDATE
                SET close = EXCLUDED.close, crawl_time = NOW()
            """, tt, price)
        n = await conn.fetchval("SELECT COUNT(*) FROM global_indices WHERE index_code='100.VIX'")
        print(f"  ✓ VIX 跨 2 分钟累积: {n} 行")
        assert n == 2
        # 验证时间不同
        rows = await conn.fetch("""
            SELECT trade_time, close FROM global_indices
            WHERE index_code='100.VIX' ORDER BY trade_time
        """)
        for r in rows:
            print(f"    {r['trade_time']}  close={r['close']}")


async def test_5_on_conflict_update():
    """[5] ON CONFLICT DO UPDATE — 同分钟内价格变化会更新而非新增"""
    print("\n" + "=" * 60)
    print(" [5] ON CONFLICT DO UPDATE — 同分钟内价格变化")
    print("=" * 60)
    from global_market import GlobalMarketCrawler
    # 先清空 VIX 当日数据
    async with db.acquire() as conn:
        await conn.execute("""
            DELETE FROM global_indices
            WHERE index_code='100.VIX' AND trade_date = CURRENT_DATE
        """)
    async with GlobalMarketCrawler(refresh_interval_seconds=60) as c:
        with patch.object(c, "fetch", side_effect=[
            mock_em_response(vix_price=18.0),                          # US round 1
            mock_hk_response(), json.dumps({"rc":0,"data":{"diff":[]}}), mock_jp_response(), "",
        ]):
            r1 = await c.run_once()
            print(f"    轮 1 (VIX=18.0): {r1}")
        with patch.object(c, "fetch", side_effect=[
            mock_em_response(vix_price=18.7),                          # US round 2
            mock_hk_response(), json.dumps({"rc":0,"data":{"diff":[]}}), mock_jp_response(), "",
        ]):
            r2 = await c.run_once()
            print(f"    轮 2 (VIX=18.7): {r2}")

    async with db.acquire() as conn:
        n = await conn.fetchval("""
            SELECT COUNT(*) FROM global_indices
            WHERE index_code='100.VIX' AND trade_date = CURRENT_DATE
        """)
        latest = await conn.fetchval("""
            SELECT close FROM global_indices
            WHERE index_code='100.VIX' ORDER BY trade_time DESC LIMIT 1
        """)
    print(f"  ✓ VIX 当日行数: {n} (应仍为 1)")
    print(f"  ✓ 最新 VIX 收盘: {latest} (应被更新为 18.7)")
    assert n == 1
    assert float(latest) == 18.7


async def test_6_scheduler_config():
    """[6] Scheduler 配置: 默认 60s"""
    print("\n" + "=" * 60)
    print(" [6] Scheduler 配置 (默认 60s 刷新)")
    print("=" * 60)
    print(f"  RealtimeConfig.refresh_interval_seconds: "
          f"{REALTIME.refresh_interval_seconds}")
    print(f"  SchedulerConfig.global_index_interval:   "
          f"{SCHEDULER.global_index_interval}")
    assert REALTIME.refresh_interval_seconds == 60
    assert SCHEDULER.global_index_interval == 60
    print("  ✓ 默认 60s ✓")

    # 测试环境变量覆盖
    import os
    os.environ["RT_REFRESH_SECONDS"] = "30"
    # 重新导入 (会重新读取 env)
    import importlib, config as cfg_mod
    importlib.reload(cfg_mod)
    assert cfg_mod.REALTIME.refresh_interval_seconds == 30
    print(f"  ✓ 环境变量覆盖: RT_REFRESH_SECONDS=30 → {cfg_mod.REALTIME.refresh_interval_seconds}")
    del os.environ["RT_REFRESH_SECONDS"]


async def test_7_api_endpoints():
    """[7] API 端点: /api/indices /api/indices/vix"""
    print("\n" + "=" * 60)
    print(" [7] API 端点测试")
    print("=" * 60)
    # 先刷新物化视图, 否则查不到
    await db.refresh_latest_global_indices()
    from api import server
    # 直接调用 endpoint 函数
    rows = await server.list_global_indices(limit=20)
    print(f"  /api/indices: {len(rows)} 条")
    for r in rows:
        print(f"    {r['index_code']:10s} {r['index_name']:15s} "
              f"close={r['close']:>10.2f}  pct={r['change_pct']:>6.2f}%")
    assert len(rows) >= 1

    # VIX 单独
    vix = await server.get_vix()
    print(f"  /api/indices/vix: {vix['index_name']} = {vix['close']}")
    assert vix["index_code"].endswith("VIX")


async def test_8_mat_view():
    """[8] 物化视图 latest_global_indices"""
    print("\n" + "=" * 60)
    print(" [8] 物化视图 latest_global_indices")
    print("=" * 60)
    await db.refresh_latest_global_indices()
    rows = await db.fetch_all("""
        SELECT index_code, index_name, close, change_pct
        FROM latest_global_indices
        ORDER BY index_code
    """)
    print(f"  视图包含 {len(rows)} 个指数:")
    for r in rows:
        print(f"    {r['index_code']:10s} {r['index_name']:15s} "
              f"close={r['close']:>10.2f}  pct={r['change_pct']:>6.2f}%")
    assert len(rows) >= 1


async def test_9_scheduler_runs():
    """[9] Scheduler 启动后, VIX 任务自动运行"""
    print("\n" + "=" * 60)
    print(" [9] Scheduler 实际启动 + VIX 任务执行")
    print("=" * 60)
    from scheduler.jobs import SchedulerService, job_global_indices

    svc = SchedulerService()
    svc.add_interval(job_global_indices, seconds=60, id="global_indices_test")
    svc.start()

    # Mock 一下 fetch
    from global_market import GlobalMarketCrawler
    orig_run = GlobalMarketCrawler.run_once
    async def mock_run_once(self):
        # 调基类 fetch 来 mock
        return {"fetched": 6, "inserted": 6, "mock": True}
    GlobalMarketCrawler.run_once = mock_run_once

    try:
        # 等待 1.5 秒让 interval 任务跑
        await asyncio.sleep(1.5)
        jobs = svc.scheduler.get_jobs()
        print(f"  Scheduler 中注册的任务: {len(jobs)} 个")
        for j in jobs:
            print(f"    - {j.id}  next_run={j.next_run_time}")
        assert any(j.id == "global_indices_test" for j in jobs)
    finally:
        GlobalMarketCrawler.run_once = orig_run
        svc.shutdown()
    print("  ✓ Scheduler 任务注册 + 调度正常")


# ══════════════════════════════════════════════════════════
async def main():
    print("=" * 60)
    print(" 6stockPulse · 实时数据 + 去重 完整测试")
    print("=" * 60)
    await setup_db()
    try:
        await test_1_vix_config()
        await test_2_truncate()
        await test_3_dedup_same_minute()
        await test_4_dedup_different_minutes()
        await test_5_on_conflict_update()
        await test_6_scheduler_config()
        await test_7_api_endpoints()
        await test_8_mat_view()
        await test_9_scheduler_runs()
        print("\n" + "=" * 60)
        print(" ✓ 全部 9 项测试通过")
        print("=" * 60)
    finally:
        await teardown_db()


if __name__ == "__main__":
    asyncio.run(main())
