"""
快速验证脚本: 测试 API 端点是否能正确返回真实数据
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import database as db
from api import realtime as rt
from api import server


async def main():
    await db.init_pool()
    # 直接调用 endpoint 函数, 不通过 TestClient
    print("=" * 60)
    print(" 6stockPulse API 端点测试")
    print("=" * 60)

    print("\n[1] /health")
    h = await server.health()
    print(" ", json.dumps(h, ensure_ascii=False, default=str)[:200])

    print("\n[2] /api/stocks?limit=5")
    rows = await server.list_stocks(limit=5)
    print(f"  返回 {len(rows)} 条:")
    for r in rows[:3]:
        print(f"    {r['stock_code']} {r['stock_name']} [{r['market']}] {r.get('industry','')}")

    print("\n[3] /api/stocks/000001")
    detail = await server.get_stock("000001")
    print(f"  stock: {detail['stock']['stock_name']}")
    print(f"  quote: {detail['quote']}")

    print("\n[4] /api/fund-flow")
    flow = await server.fund_flow_today()
    print(" ", json.dumps(flow, ensure_ascii=False, default=str))

    print("\n[5] /api/fund-flow/top?limit=3")
    top = await server.fund_flow_top("inflow", 3)
    for r in top:
        print(f"    {r['stock_code']} {r['stock_name']}: 主力净流入 {r['main_net_inflow']:,.0f} 涨跌幅 {r['change_pct']}%")

    print("\n[6] /api/dashboard")
    d = await server.dashboard()
    print(f"  generated_at:  {d['generated_at']}")
    print(f"  indices:       {len(d['global_indices'])} 项")
    print(f"  market:        {d['market_summary']}")
    print(f"  sectors:       {len(d['sectors'])} 项")
    print(f"  top_inflow:    {len(d['top_inflow'])} 项")
    print(f"  top_outflow:   {len(d['top_outflow'])} 项")
    print(f"  sentiment:     {d['sentiment']}")
    print(f"  realtime:      {d['realtime_status']}")

    print("\n" + "=" * 60)
    print(" ✓ 所有端点返回正常")
    print("=" * 60)

    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
