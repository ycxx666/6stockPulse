# 6stockPulse · 全球量化资讯系统

> 实时股市数据看板 · **FastAPI + PostgreSQL 16 + APScheduler(独立 worker)+ WebSocket**
> 由早期 `GlobaPulse` / v3 单体演进而来,bootstrap 与展示数据均已切到真实数据源(无演示假数据)。

---

## 运行架构(当前)

```
浏览器 ── HTTP/WS ──► FastAPI(app/api/routes)  ── asyncpg ──► PostgreSQL 16 (stockpulse)
                          ▲                                        ▲
                          │ 读快照/缓存                             │ 写入
                    application/ 聚合与业务                workers/scheduler_runner.py (APScheduler, 32+ jobs)
                          ▲                                        ▲
                          │                                        │
                    news_gateway(新闻采集, SQLite data/news.db)    infrastructure/crawler(新浪/东财…)
```

- **API 进程**:`main.py`(FastAPI REST + `/ws/realtime`),仅负责接口与连接池;调度/采集在 worker。
- **worker 进程**:`workers/scheduler_runner.py`,24h 运行 APScheduler(全天新闻 + 交易时段行情/板块/情绪/信号等)。
- **数据库**:PostgreSQL 16(Windows 服务 `stockpulse-pg16`,127.0.0.1:5432,库 `stockpulse`);新闻库为独立 SQLite `data/news.db`。
- **前端**:`frontend/` 静态目录(由 API 挂载于 `/frontend`)。当前含"全景市场"单页 `index.html`;已抽出公共层 `frontend/css/base.css` 与 `frontend/js/common.js`,后续"平行界面"按"独立 HTML 多页 + 公共层"扩展。

## 快速开始(Windows)

```bat
:: 一键启动(PG 服务 + worker + API)
start_all.bat
```
或手动:
```bat
:: 采集/调度 worker(后台)
.venv\Scripts\pythonw.exe workers\scheduler_runner.py
:: API(默认端口 8900)
.venv\Scripts\python.exe main.py --port 8900
```
访问:**http://localhost:8900/frontend/index.html**(根路径 `/` 会重定向到此)
健康检查:`GET http://localhost:8900/health`

> 依赖安装参考 `requirements.txt`(`pip install -r requirements.txt`);Python 虚拟环境在 `.venv/`。

## 项目结构(实际)

```
6stockPulse/
├── main.py                     # API 启动入口(FastAPI;--host/--port/--reload)
├── core/config.py              # 统一配置(环境变量覆盖)
├── app/
│   ├── api/server.py           # FastAPI 应用:注册路由 + 挂载 /frontend + /ws/realtime
│   └── api/routes/             # dashboard / market / news / sentiment / analysis 路由
├── application/
│   ├── dashboard/aggregator.py # 首屏聚合 /api/dashboard/snapshot(并行子查询 + 缓存)
│   ├── market/analyzer.py      # 分时/量能/连板/赚钱效应/概念领涨等分析
│   ├── realtime/service.py     # 实时行情采集 + WS 主题推送(worker 内运行)
│   └── news/processor.py       # 新闻 NLP/摘要
├── infrastructure/
│   ├── crawler/                # 新浪 / 东方财富 等采集器
│   ├── database/               # asyncpg 连接池
│   ├── cache/                  # 内存缓存
│   └── llm/                    # LLM(DeepSeek 等)简报/信号
├── domain/                     # 领域模型:anomaly / signal / prediction
├── services/                   # 业务服务:market / dashboard / prediction …
├── scheduler/                  # jobs.py(任务注册)/ task_registry.py(时段判定)
├── workers/scheduler_runner.py # 独立 worker 进程入口
├── news_gateway/               # 新闻网关(多源适配 + 健康探测),库 data/news.db
├── frontend/                   # index.html + css/base.css + js/common.js
├── db/                         # schema.sql + migration_v4~v6.sql
├── scripts/                    # 探针/导入/测试脚本(probe_sources.py 等)
├── tests/                      # 测试
└── docs/                       # 设计/API/部署/整改与新闻源文档
```

## 主要 REST 端点

| 端点 | 用途 |
|---|---|
| `GET /health` | 健康检查(DB / realtime / 市场时段) |
| `GET /api/dashboard/snapshot` | 首屏一站式聚合(tickers/breadth/anomalies/sector_flow/stats/ladder/news/sentiment/prediction/concept_leaders/signals) |
| `GET /api/market/tickers` · `/api/market/breadth` | 全球行情带 · 市场宽度 |
| `GET /api/market/index/{symbol}/intraday` | 指数分时(默认 000001) |
| `GET /api/market/volume-comparison` | 今日 vs 昨日 分钟量能/成交额 |
| `GET /api/short-term/limit-up-ladder` · `/api/short-term/heat` | 连板梯队 · 赚钱效应 |
| `GET /api/sectors/flow?type=concept\|industry` | 板块资金流(概念/行业,含 leader) |
| `GET /api/concepts/leaders` | 概念领涨板块 |
| `GET /api/anomalies` · `GET /api/news` · `GET /api/news/{id}` | 异动 · 新闻列表 · 单条详情 |
| `GET /api/sentiment/global` · `/api/sentiment/ashare` | 全球 / A股情绪快照(含 factors) |
| `GET /api/signals?direction=` · `GET /api/predictions/next-session` | 信号(机会/风险) · 次日方向预测 |
| `GET /api/analysis/briefing` · `/api/analysis/news-report` | AI 简报 · 新闻分析 |
| `POST /api/crawl/{news,fund-flow,sector,realtime,global}` | 手动触发采集 |

## WebSocket

- `WS /ws/realtime`:连接后立即收到 `{"type":"snapshot",...}`;交易时段推送
  - `{"type":"market.ticker.updated","payload":[...]}`(个股行情,~3s)
  - `{"type":"market.breadth.updated","payload":{...}}`(市场宽度,~60s)
- 前端 `frontend/js/common.js` 的 `connectWS(handlers)` 已封装(断线 5s 重连);"全景市场"页用其驱动市场宽度与连接状态。

## 前端模块(全景市场 `frontend/index.html`)

四列布局:① 异动监测 ② 板块资金流向(概念/行业 tab) ③ 全景市场分析(市场状态 / 涨跌统计 / 上证分时 / 全 A 量能 / 赚钱效应 / 连板梯队) ④ 信号复盘(机会·风险 + 新闻列表,点开详情) ⑤ 右侧:全球情绪指数(仪表盘+多空 bars)、A股情绪指数(圆环+因子+领先预测)、概念领涨板块。

> 页面所有面板由真实 API 驱动,数据缺失显示空态;30s 轮询 + WS 增量更新(内容变化才写 DOM,不闪烁)。

## 定时任务(worker,部分)

| job id | 说明 | 频率 |
|---|---|---|
| `sina_news` | 新浪 7×24 快讯 | 30s |
| `global_indices` | 全球指数 | 15s(交易时段) |
| `fund_flow` / `sector` | A股资金流 / 板块资金 | 交易时段 |
| `market_breadth` | 市场宽度 | 30s |
| `anomaly_detect` / `anomaly_today(_noon)` | 异动监测 / 当日复盘 | 10s / 15:05 · 11:35 |
| `sentiment` / `generate_signals` | 情绪快照 / 信号 | 60s / 300s |
| `prediction_noon` / `prediction_close` | 次日预测 | 11:30 / 15:00 |
| `em_probe` | 东财通道探测 + 板块补采 | 600s |
| `enrich_news` / `news_import` | 新闻增强 / 导入 | 600s / 1800s |
| `prune_news` / `prune_prices` | 清理旧数据 | 02:00 / 02:30 |

## 配置(环境变量,见 `core/config.py` 与 `.env.example`)

- 数据库:`PG_HOST` / `PG_PORT` / `PG_USER` / `PG_PASSWORD` / `PG_DATABASE` / `PG_POOL_MIN` / `PG_POOL_MAX`
- LLM:`DEEPSEEK_API_KEY`(或 `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL`)
- 采集/调度:各任务间隔(`SCHEDULER.*`,如 `NEWS_CN_FAST_DAY`、`NEWS_MEDIA_INTERVAL` 等)

## 故障排查

- **页面打不开**:确认 API 在 8900 监听(`netstat -ano | findstr 8900`)、`/health` 200;端口被占则改 `--port`。
- **数据库连接失败**:确认 Windows 服务 `stockpulse-pg16` 运行;`psql -h 127.0.0.1 -U davi60 -d stockpulse -c "select 1"`。
- **数据不更新**:worker 未运行(仅 API 在跑只会读旧快照);启动 `workers/scheduler_runner.py` 或 `start_all.bat`。
- **东方财富通道**:主域 `push2` 盘后/高频易限流;系统主用 `push2delay` 延迟镜像。
- **新浪全球指数**:偶发 `Server disconnected`,重试后可部分成功(已知限制,见 `docs/整改执行清单.md`)。

## 许可

仅供教学分析使用。数据来源为公开 API,不涉及个人信息。
