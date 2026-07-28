# 6stockPulse · API 文档

## 基础信息
- **Base URL**: `http://127.0.0.1:8902`
- **WebSocket**: `ws://127.0.0.1:8902/ws/realtime`
- **OpenAPI 文档**: `http://127.0.0.1:8902/docs`
- **响应格式**: JSON (UTF-8)
- **CORS**: 允许所有 (`*`)

## 通用响应
```json
{
  "data": [...],
  "total": 100,
  "error": null
}
```

错误时:
```json
{
  "detail": "Stock 600519 not found"
}
```

---

## REST 端点

### 健康检查
```
GET /health
```
响应:
```json
{
  "ok": true,
  "db": { "ok": true, "version": "PostgreSQL 16...", "stocks": 5000 },
  "realtime": {
    "running": true,
    "subscribers": 3,
    "last_refresh": "2026-07-13T16:30:00",
    "quotes_cached": 100
  }
}
```

### 股票列表
```
GET /api/stocks?limit=100&offset=0&market=SH&q=平安
```
参数:
- `limit` (int, default 100): 返回数量
- `offset` (int, default 0): 偏移
- `market` (str): 过滤市场 (SH/SZ/BJ)
- `q` (str): 模糊搜索 (代码或名称)

响应:
```json
[
  {
    "stock_code": "600519",
    "stock_name": "贵州茅台",
    "market": "SH",
    "industry": "白酒",
    "sector_code": null,
    "is_active": true
  }
]
```

### 单只股票
```
GET /api/stocks/{code}
```
响应:
```json
{
  "stock": { ... },
  "quote": {
    "stock_code": "600519",
    "close": 1682.50,
    "change_pct": 1.85,
    "trade_time": "2026-07-13T15:00:00"
  }
}
```

### K 线
```
GET /api/stocks/{code}/kline?days=60
```
响应:
```json
[
  {
    "trade_date": "2026-07-13",
    "open": 1680.0,
    "high": 1690.0,
    "low": 1675.0,
    "close": 1682.5,
    "volume": 1234567,
    "amount": 2078234567
  }
]
```

### 当日资金汇总
```
GET /api/fund-flow
```
响应:
```json
{
  "total": 2887,
  "up_count": 1823,
  "down_count": 842,
  "flat_count": 222,
  "limit_up": 67,
  "limit_down": 23,
  "avg_pct": 0.43,
  "main_inflow_yi": 128.5
}
```

### 资金 TOP
```
GET /api/fund-flow/top?direction=inflow&limit=10
```
参数:
- `direction`: `inflow` (流入) 或 `outflow` (流出)

### 板块资金
```
GET /api/sectors?limit=20
```

### 新闻列表
```
GET /api/news?limit=30&offset=0&category=macro&stock_code=600519
```

### 公告列表
```
GET /api/announcements?stock_code=600519&limit=30
```

### 一站式聚合
```
GET /api/dashboard
```
响应包含:
- `global_indices`: 全球指数
- `market_summary`: 资金汇总
- `sectors`: 板块 TOP
- `top_inflow` / `top_outflow`: 资金 TOP
- `news`: 新闻
- `realtime_quotes`: 最新 50 条实时行情
- `sentiment`: 情绪指数

### 爬虫日志
```
GET /api/crawl-log?limit=20
```

### 手动触发爬虫
```
POST /api/crawl/news
POST /api/crawl/fund-flow
POST /api/crawl/sector
POST /api/crawl/realtime
```
请求体:
```json
{ "pages": 3, "codes": ["600519", "000001"] }
```

---

## WebSocket

### 连接
```js
const ws = new WebSocket('ws://127.0.0.1:8902/ws/realtime');
```

### 客户端 → 服务端

**Ping**:
```json
{ "action": "ping" }
```

### 服务端 → 客户端

**Snapshot (连接时立即推送)**:
```json
{
  "type": "snapshot",
  "data": [
    {
      "stock_code": "600519",
      "close": 1682.5,
      "change_pct": 1.85,
      "trade_time": "2026-07-13T15:00:00"
    }
  ]
}
```

**Realtime (定时推送, 交易时段 3s/次)**:
```json
{
  "type": "realtime",
  "ts": "2026-07-13T15:00:03",
  "data": [
    {
      "stock_code": "600519",
      "close": 1683.2,
      "change_pct": 1.91,
      "trade_time": "2026-07-13T15:00:03"
    }
  ]
}
```

**Pong**:
```json
{ "type": "pong", "ts": "2026-07-13T15:00:00" }
```

## 状态码

| 码 | 含义 |
|----|------|
| 200 | OK |
| 404 | Not Found (股票/新闻不存在) |
| 422 | 参数错误 |
| 500 | 服务器错误 (查看日志) |

## 限流

开发模式无限流。生产建议:
- Nginx: `limit_req_zone`
- API: `slowapi` 中间件
