# 6stockPulse · 前端/数据整改清单

> 创建: 2026-09-04 | 维护: 前端真实数据化项目
> 状态说明: `open`=待处理 · `wip`=进行中 · `done`=已完成 · `pending`=排队(需条件触发)

---

## P0 · 东财数据源回填 — ✅ done (2026-09-06)

**状态: done · 板块已通过 push2delay 延迟镜像通道回填**

### 完成说明
- 诊断发现东财**主域 push2 被限、延迟镜像 push2delay 可用**(curl/aiohttp 指纹差异:成分接口用 requests 通道)。
- ✅ 板块资金流(概念504/行业496)经 push2delay 全量回填,概念 tab(AI应用+85.5亿等)、行业 tab(传媒等)真实展示;
- ✅ 采集/龙头/em_probe 已切到 push2delay;em_probe 每10分钟自动探测+缺数据自动补采;
- ⏳ **龙头 leader 渐进补充中**(东财 fs=b 成分接口对连续请求限流 rc102,每 2 分钟慢速补 3 个;主域恢复后自动加快);
- ✅ tickers A 股指数已修复(新浪);外汇部分已自动恢复(global fetched=4)。

### 受影响区块(同一东财源)
- `sector_flow` 板块资金流向 → 当前 **0**
- `tickers` 顶部行情带(全球指数部分) → 当前仅 4~8 条
- `concept_leaders` 概念领涨板块 → 当前 **0**

### 整改动作
1. 开盘后确认东财接口可用(以 `curl push2.eastmoney.com/api/qt/clist/get` 探测)
2. 依赖 worker 的 `sector_smart`/`fund_flow_smart`/`job_global_indices` 自动重采;若 `in_trading_hours()` 之外仍缺,手动触发 `EastMoneySector.run()` / `GlobalMarketCrawler.run_once()` 回填
3. 回填后依次 `REFRESH MATERIALIZED VIEW latest_sector; latest_quotes; latest_global_indices`
4. 验证 `/api/sectors/flow`、`/api/market/tickers`、`/api/concepts/leaders` 返回真实非空数据

### 验收
- `sector_fund_flow` 行数 > 0,且 `main_net_inflow` 单位正确(亿元级,约为 8 位数,非 8×10^13 万亿级)
- `/api/sectors/flow` 返回真实板块(如 AI应用/AIGC概念 等)
- `/api/market/tickers` 补齐指数/商品/外汇

---

## P1 · stats(涨跌统计)口径 — ✅ done(阶段) (2026-09-06)

**状态: done · 样本10只→股票池402只口径已落地; 全市场5000只口径需东财全量行情(待P0)**

### 实现说明
- 刷新 `latest_quotes` 物化视图(纳入回填的 402 只日线)后,基于全池重算 `market_breadth`:
  **上涨140/下跌261/涨停9/跌停6/大涨27/大跌69/total=402/情绪58**(原实时样本仅 total=10)。
- 页面 stats/市场宽度现按股票池 402 只统计,数字量级真实(不再是 10 只的个位数)。
- **已知限制**: 402 只仍是成交额榜样本,非全市场 5000+;待 P0(东财全量)可提升到全市场口径。

---

## P1.5 · 图表真实化(分时/量能) — ✅ done (2026-09-04)

**状态: done**

### 实现说明
- `indexChart`/`volumeChart` 已改接真实数据,不再画随机曲线;增加昨收基准虚线、红涨绿跌分段着色、最新值+涨跌幅角标、时间刻度。
- 空数据时显示"暂无分时/量能数据"提示。
- **上证指数分时已接入**:后端 `get_index_intraday` 优先从**新浪指数分钟接口**(quotes.sina.cn CN_MarketDataService.getKLineData)拉取真实上证指数分时(如 open≈3955/close≈3930),失败回退库内数据。前端 `/api/market/index/000001/intraday` 即返回真实指数分时。
- **量能图注意**: `volumeChart` 数据源 `/api/market/volume-comparison` 仍基于库内 `stock_prices`(分钟量能对比),当前样本为真实分钟量能,但非上证指数口径(如需与指数分时一致可后续将 volume-comparison 也切到新浪指数源)。

### 整改动作
- 接入 `/api/stocks/000001/kline`(分时)与 `/api/market/breadth`(量能)真实数据
- 重写 `drawIndexChart`/`drawVolumeChart` 从后端数据绘制
- 数据为空时显示空网格而非随机曲线

### 验收
- 图表反映真实指数分时/量能走势,无随机曲线

---

## P2 · 空区块友好提示 — ✅ done (2026-09-04)

**状态: done**

### 实现说明
- 新增 `.list-empty` 空态样式;新闻/资金流/异动/概念等列表区块空数据时显示"采集数据中…/暂无异动"占位。

### 整改动作
- 各 `renderXxx([])` 为空时显示"采集中/暂无数据"占位提示(如 CSS 空态 class)

---

## P3 · 防复发: 数据库操作规范(备忘)

**状态: open · 流程性,无触发时间**

### 背景
本次 `TRUNCATE` 清空无备份导致数据丢失,教训:
- 修正"字段单位/解析"类 bug 时,**优先 `UPDATE` 修正存量数据**,不要 `TRUNCATE`
- 确需清空数据前,先备份(如 `CREATE TABLE ... AS SELECT` 或用 `pg_dump`)

---

## P4 · 板块资金流采集(概念/行业+龙头) — ✅ done (2026-09-06)

**状态: done · 经 push2delay 通道完整回填；龙头渐进补充中**

### 完成说明
- `sector_fund_flow` 加 sector_type/leader 列、latest_sector/v_sector_top 重建(真实列);
- EastMoneySector 经 push2delay 全量采集概念(504)/行业(496),`/api/sectors/flow?type=concept|industry` 返回真实净流入(AI应用+85.5亿/传媒+61.7亿)与类型;
- 前端概念/行业 tab + 模板(序号/名/亿/涨跌幅/龙头)自动展示;
- 龙头:东财 fs=b 成分对连续请求限流(rc102),sector_smart 每 2 分钟限速慢补 3 个(requests 通道),渐近补齐。

---

## P5 · 开盘验证项(验证日 2026-09-08 周二)— ⏳ 部分达成,盘后核验

**状态: ①未达成 · ②主数据达成/龙头未补 · ③主域不稳定 · 核验 2026-09-08 22:50**

> 核验背景: 09-08 当天 worker/API 全天停摆(09-06 22:35 后未运行),22:30 手动拉起恢复;以下以 PG 数据 + 接口实测为准。

- ① **异动监测**: ❌ 未达成。`anomaly_event` 09-08 当天 0 条(全表仅 09-04 的 15 条:surge/up 9 + drop/down 6),因盘中 worker 停摆 cron 无从触发。恢复后 `anomaly_smart` 每 10s 运行,**待下一交易日盘中复核**是否产出当日异动。
- ② **板块资金流(概念/行业+龙头)**: ✅ 主数据自动回填生效——worker 拉起后 `em_probe` 22:41 自动补采当日 200 条(concept 100 + industry 100,AI应用 +85.6亿 等),`latest_sector` 已刷新;❌ **龙头 leader 仍未补**——09-06 的 400 条与 09-08 的 200 条 `leader_name` 填充均为 0,fs=b 成分接口慢速补充至今零成功。
- ③ **东财 push2**: ⚠️ 主域不稳定、push2delay 稳定。两次探测 push2 主域 200→000 抖动;push2delay 稳定 200 且返回真实数据(total 496)。系统应继续以 **push2delay 为主通道**,主域恢复前龙头/成分走慢速补充。

**遗留(下一交易日 09-09 盘中)**: 复核 ① 异动当日产出;观察 ② leader 是否随 sector_smart 慢速补充,若仍为 0 考虑成分接口改走 push2delay 通道。

---

## P6 · 前端面板数据不显示:契约不一致 / 回退映射 / 兜底伪装

**状态: open · 立项 2026-09-11 · 范围 = 前端+后端契约修复 + 诚实空态(用户已选 A)**

### 现象
多个面板「取到数据却不渲染」或「把默认值当真实分析结果」:
- 板块资金流向**条形恒等宽**、龙头恒显示「龙头待补」;
- 赚钱效应**炸板率/封板率恒为 0%**;
- 快照接口不可用时,异动/概念列表**空白或出现 `undefined`**;
- 无真实情绪/预测快照时,A股情绪圆环**固定 50**、预测**固定「明日:震荡」**,掩盖「无真实分析结果」。

### 根因(含定位)
1. **字段契约不一致(后端有数据、前端读不到)**
   - `app/api/routes/market.py:221` `GET /api/sectors/flow` 只返回 `sector_code/sector_name/sector_type/change_pct/main_net_inflow/main_net_ratio/turnover_rate`,**缺 `bar_pct`、缺 `leader_name`**;前端 `frontend/index.html:395` 读 `s.bar_pct||50` 与 `s.leader_name` → 条形全 `50%`、龙头恒「龙头待补」。
   - 讽刺点:`application/dashboard/aggregator.py:192` `_get_sector_flow()` **已算好 `bar_pct`**,但前端未用 snapshot 的 `sector_flow`,反而单独请求了缺字段的 `/api/sectors/flow`。
   - 赚钱效应:前端 `frontend/index.html:612` 读 `h.break_rate/seal_rate/avg_close_return`,`application/market/analyzer.py` 的 `get_enhanced_profit_effect()` 默认分支缺 `break_rate/seal_rate` → 恒 0%。
2. **回退分支字段未归一**
   - `frontend/index.html:882/885` `loadPerEndpoint()` 在 snapshot 不可用时把原始 dict 直接喂给 `renderAnomalies`(期望 `{name,value,dir,time,sub}`)与 `renderConcepts`(期望 `[name,change,meta]`)→ 列表空白 / `undefined`。
3. **默认兜底伪装真实分析结果**
   - `application/dashboard/aggregator.py:297-321` `_default_sentiment()` = `score:50/label:中性`、`_default_prediction()` = `0.33/0.34/0.33`;无 `sentiment_snapshot`/`prediction_result` 时前端照常画出 50 / 「明日:震荡」。
4. **数据管道缺数(非契约问题)**
   - 异动 / 信号 / 连板 / 赚钱效应 / 板块 leader 依赖 worker 24h 运行 + 交易时段 + 历史数据;worker 停摆或盘外即空态(见本文件 P5 与「已知数据源限制」)。

### 整改动作(方案 A + 诚实空态)
1. 后端补字段:`/api/sectors/flow` 增 `bar_pct`(按 `max|main_net_inflow|` 归一)与 `leader_name/leader_change_pct`;对齐 `get_enhanced_profit_effect` 的 `break_rate/seal_rate/avg_close_return`;核对 `volume-comparison`、`concepts/leaders` 字段名与前端一致。
2. 前端抽公共归一函数(raw dict → 归一数组),snapshot 路径与 `loadPerEndpoint` 回退路径共用;`loadSectors` 复用 snapshot 的 `sector_flow`(含 `bar_pct`)或改用修好的字段;`/api/hotspots` 的 `stocks` 做字符串/数组兼容,避免 `.map` 抛错导致整块「热点服务暂不可用」。
3. 诚实空态:后端缺真实数据时附 `is_fallback` / `data_quality` 标记,前端据此显示「暂无真实分析结果」,不再显示 50 / 明日震荡(覆盖 A股/全球情绪、领先预测、stats);空态文案分级(采集中 / 需盘中数据 / 数据源受限)。

### 验收
- `/api/sectors/flow` 返回含 `bar_pct`(非恒 50)且龙头字段就位;赚钱效应炸板率/封板率非恒 0。
- 无 worker 态:情绪/预测面板显示「暂无真实分析结果」而非 50 / 明日震荡;有数据态:各面板正常显示。
- 前端控制台无报错、无 `undefined`、无等宽条;`curl` 断言关键字段存在。

---

## 已知数据源限制(备忘,非 bug)

| 数据源 | 限制 | 处理 |
|---|---|---|
| 东方财富 push2 | 盘后/高频请求会 `Server disconnected` | 依赖重试与 `in_trading_hours()` 守护;避免高频 |
| Yahoo Finance | 大陆直连恒 403 | 已加 `YAHOO_ENABLED` 开关,默认禁用;有代理时 `YAHOO_ENABLED=1` 恢复 |
| 全球市场 sina | 部分接口返回 JS 文本非 JSON | 已修(调用 `fetch as_json=False`) |
| 积累型区块: `anomalies`/`signals`/`ladder` | 需历史+交易时段运行 | 随交易日自然充实,无需人工整改 |
