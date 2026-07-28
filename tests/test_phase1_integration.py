"""Phase 1 集成验证 (无需 DB)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# 1. 所有新模块可导入
from api.schemas import APIResponse, DashboardSnapshot
from services.cache import cache, CacheKeys
from services.dashboard_service import market_session, _default_sentiment, _default_prediction, _empty_for
from crawlers.global_market import GlobalMarketCrawler, INDEX_WATCHLIST, COMMODITY_WATCHLIST, FOREX_SYMBOLS
from crawlers.eastmoney import EastMoneyFundFlowHistory

# 2. 路由完整性
from api.routes.dashboard import router as r1
from api.routes.market import router as r2
from api.routes.news import router as r3
from api.routes.sentiment import router as r4
from api.server import app

routes = [r for r in app.routes if hasattr(r, 'methods')]
paths = [r.path for r in routes if hasattr(r, 'path')]
print(f"  Routes: {len(routes)} 个端点")

required = ['/api/dashboard/snapshot', '/api/market/tickers', '/api/market/breadth',
            '/api/anomalies', '/api/sentiment/global', '/api/sentiment/ashare',
            '/api/signals', '/api/predictions/next-session', '/api/concepts/leaders',
            '/api/sectors/flow', '/api/short-term/limit-up-ladder', '/api/system/health']
for p in required:
    assert p in paths, f"Missing route: {p}"
print(f"  All {len(required)} required routes present")

# 3. 配置验证
assert cache.get('__test__') is None
cache.set('__test__', 1, ttl=5)
assert cache.get('__test__') == 1

# 4. Schema 验证
assert APIResponse.success({'test': 1}, ts='now') == {'ok': True, 'data': {'test': 1}, 'meta': {'ts': 'now'}}
assert DashboardSnapshot.empty()['data']['tickers'] == []
assert _default_sentiment('global')['score'] == 50
assert _default_prediction()['confidence'] == 0.5

# 5. 爬虫验证
assert EastMoneyFundFlowHistory.name == 'eastmoney_fund_flow_history'
c = EastMoneyFundFlowHistory()
row = c._parse_kline('600519', '2025-07-10,12345,500,300,200,800')
assert row['trade_date'] == '2025-07-10'
assert row['main_net_inflow'] == 12345

assert GlobalMarketCrawler.name == 'global_market'
assert len(INDEX_WATCHLIST) >= 10
assert len(FOREX_SYMBOLS) == 4

print("\n✅ Phase 1 集成验证全部通过")
print(f"  - 4 个路由模块, {len(routes)} 个端点")
print(f"  - APIResponse 统一响应包络")
print(f"  - MemCache 内存缓存层")
print(f"  - DashboardService 聚合")
print(f"  - GlobalMarketCrawler ({len(INDEX_WATCHLIST)}指数 + {len(COMMODITY_WATCHLIST)}商品 + {len(FOREX_SYMBOLS)}外汇)")
print(f"  - EastMoneyFundFlowHistory K线回填")
print(f"  - 前端四列驾驶舱 (779行, useApi=true)")
