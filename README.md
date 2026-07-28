# 6stockPulse v3.0

> **实时股市数据看板** · PostgreSQL + FastAPI + APScheduler + WebSocket
> 完整重构自 `GlobaPulse`，新增实时数据流、PostgreSQL 持久化、定时任务调度。

## ✨ 核心特性

- ⚡ **WebSocket 实时推送** — 行情秒级刷新，浏览器零轮询
- 🗄️ **PostgreSQL 持久化** — asyncpg 异步连接池 + 分区表 + 物化视图
- 🤖 **4 类爬虫** — 新浪 7×24 / 东方财富 A 股 / 板块 / 个股公告
- ⏰ **APScheduler 定时任务** — 7 个任务, 智能适配交易时段
- 📊 **12 模块深色看板** — 实时股票表 / 板块 / K 线 / 情绪 / 新闻 / 投行
- 🧠 **情绪指数计算** — 上涨比例 × 0.65 + 35, 截断 5-95
- 🔍 **模糊搜索** — ILIKE + pg_trgm GIN 索引
- 🛠️ **REST + WS 双协议** — FastAPI 自动生成 OpenAPI 文档

## 🚀 快速开始

### 1. 启动 PostgreSQL
```bash
brew services start postgresql@16
# 或 (如果未设置)
pg_ctl -D /opt/homebrew/var/postgresql@16 -l /opt/homebrew/var/postgresql@16/logfile.log start
```

### 2. 初始化数据库
```bash
cd 6stockPulse
psql -U davi60 -h /tmp -d stockpulse -f db/schema.sql
```

### 3. 安装依赖
```bash
pip3 install asyncpg fastapi "uvicorn[standard]" websockets aiohttp apscheduler pydantic
```

### 4. 启动服务
```bash
python3 main.py --port 8902
# 或
python3 main.py --port 8902 --reload   # 开发模式
```

### 5. 打开前端
访问 [http://127.0.0.1:8902/frontend/index.html](http://127.0.0.1:8902/frontend/index.html)

> 前端需将 API_BASE 改为 `http://127.0.0.1:8902`（默认已配）

### 6. 导入历史数据 (可选, 一键迁移现有 SQLite 数据)

如果项目目录下已有 `东方财富/data/finance.db` 和 `东方财富2/eastmoney.db` 历史数据，可执行：

```bash
# 1) 从 SQLite 迁移到 PostgreSQL (stocks + fund_flow + stock_prices)
python3 scripts/import_from_sqlite.py

# 2) 按行业聚合成 sector_fund_flow
python3 scripts/aggregate_sectors.py

# 3) 测试 API
python3 scripts/test_api.py
```

导入后预期结果:
- `stocks` ≈ 600+ (从 finance.db) + 10 (seed)
- `fund_flow` ≈ 2,228 (从 eastmoney.db, 10 个交易日)
- `stock_prices` ≈ 1,788+481 (两源快照)
- `sectors` + `sector_fund_flow` ≈ 9+ (按 industry 聚合)
- `latest_quotes` / `latest_sector` 物化视图已刷新

## 📁 项目结构

```
6stockPulse/
├── main.py                       # 启动入口
├── config.py                     # 统一配置
│
├── db/
│   ├── schema.sql                # PostgreSQL schema
│   ├── database.py               # asyncpg 连接池
│   └── __init__.py
│
├── crawlers/
│   ├── base.py                   # 爬虫基类 (异步 HTTP / 重试 / 日志)
│   ├── eastmoney.py              # 东方财富 (资金流 / 板块 / 实时)
│   ├── sina_news.py              # 新浪 (7×24 / 个股新闻)
│   ├── stock_announcement.py     # 个股公告
│   └── __init__.py
│
├── api/
│   ├── server.py                 # FastAPI (REST + WebSocket)
│   ├── realtime.py               # 实时服务 (轮询 + WS 推送)
│   └── __init__.py
│
├── scheduler/
│   ├── jobs.py                   # APScheduler 任务注册
│   └── __init__.py
│
├── frontend/
│   ├── index.html                # 单页看板
│   └── chart.js                  # Chart.js 4.4.0
│
├── logs/                         # 运行日志
└── docs/
    ├── 数据库设计.md
    ├── 爬虫使用.md
    ├── API文档.md
    └── 部署指南.md
```

## 🗃️ 数据库设计

### 12 张表
| 表 | 用途 | 记录数级 |
|----|------|----------|
| `stocks` | 股票基础信息 (SH/SZ/BJ) | 5000+ |
| `stock_prices` | 行情时间序列 (OHLCV) | 千万级 (分区) |
| `fund_flow` | 资金流向 (主力/超大/大/中/小) | 千万级 (分区) |
| `sector_fund_flow` | 板块资金 | 百万级 (分区) |
| `news` | 7×24 快讯 | 百万级 |
| `stock_news` | 个股新闻 | 百万级 |
| `stock_announcements` | 个股公告 | 十万级 |
| `sectors` | 板块基础 | 千级 |
| `crawl_log` | 爬虫审计 | 万级 |
| `latest_quotes` | 物化视图 (最新行情) | 5000+ |
| `latest_sector` | 物化视图 (最新板块) | 千级 |
| `v_sector_top` / `v_stock_top_inflow` | 排名视图 | - |

### 关键设计
- **分区表**: `stock_prices / fund_flow / sector_fund_flow` 按 `trade_date` 分区
- **物化视图**: 加速 latest 行情查询
- **GIN 索引**: 模糊搜索 (`pg_trgm`)
- **JSONB**: 扩展字段 + 爬虫日志 meta
- **ENUM**: 标准化 `market_type / sentiment_t`

## 🕷️ 爬虫

### 4 个爬虫

| 爬虫 | 数据源 | 频率 | 表 |
|------|--------|------|-----|
| `SinaNewsFeed` | 新浪 7×24 | 60s | `news` |
| `SinaStockNews` | 新浪个股 | 300s | `stock_news` |
| `EastMoneyFundFlow` | 东方财富 A 股 | 60s (交易时段) | `fund_flow` |
| `EastMoneySector` | 东方财富板块 | 120s (交易时段) | `sector_fund_flow` |
| `StockAnnouncementCrawler` | 东方财富公告 | 600s | `stock_announcements` |
| `EastMoneyRealtime` | 东方财富实时 | 3s (交易时段) | `stock_prices` |

### 手动触发
```bash
# 通过 API
curl -X POST -H "Content-Type: application/json" -d '{"pages":3}' http://127.0.0.1:8902/api/crawl/news
curl -X POST -H "Content-Type: application/json" -d '{"pages":1}' http://127.0.0.1:8902/api/crawl/fund-flow
curl -X POST -H "Content-Type: application/json" -d '{"pages":2}' http://127.0.0.1:8902/api/crawl/sector
curl -X POST -H "Content-Type: application/json" -d '{}' http://127.0.0.1:8902/api/crawl/realtime
```

## ⏰ 定时任务

| 任务 ID | 名称 | 频率 | 智能时段 |
|---------|------|------|----------|
| `sina_news` | 7×24 快讯 | 60s | 全天 |
| `fund_flow` | 资金流向 | 60s | 交易时段 |
| `sector` | 板块资金 | 120s | 交易时段 |
| `stock_news` | 个股新闻 | 300s | 全天 |
| `announcement` | 公告 | 600s | 全天 |
| `refresh_views` | 物化视图刷新 | 300s | 全天 |
| `prune_news` | 清理 7 天前新闻 | 每天 02:00 | - |
| `prune_prices` | 清理 90 天前 tick | 每天 02:30 | - |

## 🔌 API 端点

### REST
- `GET  /`                              - 服务信息
- `GET  /health`                        - 健康检查 (含 DB / WS 状态)
- `GET  /api/stocks?limit=10&q=...`     - 股票列表 (支持搜索)
- `GET  /api/stocks/{code}`             - 单只股票详情
- `GET  /api/stocks/{code}/kline`       - K 线
- `GET  /api/fund-flow`                 - 当日全市场资金汇总
- `GET  /api/fund-flow/top?direction=inflow&limit=10` - TOP 10
- `GET  /api/sectors?limit=20`          - 板块资金 TOP
- `GET  /api/news?limit=30&category=...` - 新闻列表
- `GET  /api/announcements?stock_code=...` - 公告
- `GET  /api/dashboard`                 - 一站式聚合 (前端单次拉取)
- `GET  /api/crawl-log`                 - 爬虫运行日志
- `POST /api/crawl/news`                - 手动触发新闻爬
- `POST /api/crawl/fund-flow`           - 手动触发资金流爬
- `POST /api/crawl/sector`              - 手动触发板块爬
- `POST /api/crawl/realtime`            - 手动触发实时刷新

### WebSocket
- `WS /ws/realtime`                     - 实时行情推送
  - 客户端可发送 `{"action":"ping"}` 心跳
  - 服务端推送 `{"type":"realtime", "data": [...]}`

## 🎨 前端模块

12 个核心模块, 4 列 12 模块布局:

1. **顶部品牌条** — 实时时钟 / DB 缓存数 / 任务数
2. **全球指数 Ticker** — 自动滚动
3. **板块动态** (5 卡) — 实时热点板块
4. **板块资金流向** (8 行) — 渐变进度条
5. **市场组合体** — 涨停/跌停/封板 + 涨跌平 + K 线
6. **多空分布** — 横向 sentiment bar + 换手率柱图
7. **7×24 实时快讯** — 3 层 Tab 过滤, 新新闻 slideIn
8. **实时股票表** — WebSocket 推送, 价格闪动
9. **市场情绪指数** — SVG 仪表盘 (渐变 + 发光指针)
10. **明日上行概率** — SVG 圆环
11. **投行地区涨跌预测**
12. **Footer** — WS 状态 / 推送数

## ⚙️ 配置

通过环境变量覆盖默认值:

```bash
export PG_HOST=/tmp
export PG_PORT=5432
export PG_USER=davi60
export PG_DATABASE=stockpulse
export PG_POOL_MIN=2
export PG_POOL_MAX=10
```

详见 [config.py](config.py)

## 🔧 故障排查

### 数据库连接失败
```bash
# 1. 检查 PG 是否运行
pg_isready
# 2. 测试连接
psql -U davi60 -h /tmp -d stockpulse -c "SELECT 1;"
# 3. 重启 PG
pg_ctl -D /opt/homebrew/var/postgresql@16 restart
```

### 爬虫被封 IP
- 增加 `CRAWLER.request_interval` (默认 2s)
- 配置代理: 修改 `config.py` 中 `CRAWLER.proxies`

### WebSocket 断开
- 前端自动重连 (3s 后)
- 检查防火墙是否放行 8902 端口

## 📜 许可

仅供教学分析使用。数据来源为公开 API, 不涉及个人信息。
