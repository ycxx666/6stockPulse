import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api.schemas import APIResponse, DashboardSnapshot
from services.dashboard_service import market_session, _default_sentiment

assert APIResponse.success({'test': 1}, latency_ms=5)['ok'] == True
assert DashboardSnapshot.empty()['data']['tickers'] == []
assert market_session() in ('trading', 'lunch_break', 'closed')
assert _default_sentiment('global')['score'] == 50
print('All integration checks passed')
