# 新闻源问题登记清单

> 更新时间：2026-09-10（初版 2026-09-04；含 50 源全量健康探测）
> 全量探测命令：`python scripts/probe_sources.py` → 输出 `docs/source_health_report.json`
> 处理策略：先登记，统一处理（接入/修复/换源/加代理）

---

## 一、健康总览（50 源探测）

| 状态 | 数量 | 说明 |
|------|------|------|
| ✅ 内容有效（可直接采集） | **16** | 见下表 |
| ⚠️ 连接超时 | 30 | 多为海外网络/代理问题或 rsshub 实例不稳定 |
| 🔴 HTTP 错误 | 4 | 404×2(CNBC) / 503(财联社电报) / 403(Investing) |

### ✅ 正常源（16）
MarketWatch · TechCrunch · The Verge · 新华社财经 · 华尔街见闻 · MarketPulse · Wired · Ars Technica · VentureBeat · 洛杉矶时报商业 · IT之家 · 爱范儿 · 少数派 · Solidot · （另 BBC/WSJ 修复协议头后可达，但本机超时未计）

---

## 二、问题分类明细

### 🔴 A. HTTP 错误（4 条，需处理）

| 源 | 类型 | 问题 | 处理建议 |
|---|---|---|---|
| CNBC | standard_rss | 404（url 尾 `rss.` → fix 后 `rss`，直连 404） | 网关内靠 `fallback.probe_variants` 命中 `rss.html` ✅；建议把 url 直接改为 `.../rss.html` |
| CNBC科技 | standard_rss | 404 | 同上 |
| 财联社电报 | rsshub_rss | 503 | rsshub.rssforever.com 限流/不稳定 → 换 `rss.injahow.cn` 或其他 rsshub 实例 |
| Investing | standard_rss | 403 | investing.com 反爬 → 需完整浏览器 headers / cookies，或改用 Google News 备用 |

### 🟠 B. 连接超时（30 条）

**B1. 需海外网络/代理（标准 RSS 官方源）**
BBC商业 · BBC科技 · FT市场 · FT·查询替代 · 卫报商业 · Economist · Economist商业 · Bloomberg市场 · Bloomberg科技 · HackerNews · Business Insider · 纽约时报商业 · 华盛顿邮报商业 · 环球邮报商业 · Reuter-示例（Reuters 本无公开 RSS，仅演示 discover）
→ 换有代理的网络环境即可；URL 均已确认为官方标准格式。

**B2. Google News 查询源（全部需代理）**
AP头条 · AP商业 · 联合早报中国/国际 · 日经中文 · 今日美国 · 芝加哥论坛报 · 每日电讯报 · 财联社·查询替代 · 东方财富·查询替代 · 36氪·查询替代 · 凤凰财经·查询替代 · 证券时报·查询替代 · 界面·查询替代（13 条）
→ `news.google.com` 需海外网络；域名已补全（如 `site:usatoday.com`），有代理即可采集。

**B3. rsshub 实例不稳定**
财联社电报(503) · 华尔街见闻 · 第一财经·查询替代（偶发超时）
→ rsshub.rssforever.com 实例波动大，建议备选实例 `rss.injahow.cn` / `rsshub.app`。

---

## 三、待统一处理动作

- [x] **CNBC/CNBC科技**:✅ 已完成 — config 中 url 已直接写 `.../rss.html`(实测 200,无需 fallback 变体)
- [x] **财联社电报/华尔街见闻/第一财经**:✅ 已完成 — 均已配 rsshub 备选实例(见闻/第一财经 的 fallback.hosts 于 2026-09-10 补齐 `rsshub.rssforever.com`/`rsshub.app`;财联社原已有)
- [x] **Investing**:✅ 已完成 — 已配 `fallback.google_news_site_search`(403 时自动降级 GNews site:investing.com)
- [ ] **有代理环境**:对 B1/B2 全部源实测,更新状态 verified — 未做(需代理环境)
- [x] **Reuter-示例**:✅ 已完成 — config 中已无 Reuters 示例条目(已移除;Reuters 官方无 RSS)
- [x] **全文增强源**:✅ 已完成 — 已实现(`job_enrich_news_content`,worker 日志 enrich_news 正常产出)

---

## 四、参考文件

- 源状态库：`config/news_sources.json`（122 条）
- 统一网关：`config/sources_config.json`（50 条，已接入文本#7/#9 全部源）
- 探针脚本：`scripts/probe_sources.py` · 报告：`docs/source_health_report.json`
- 采集器：`news_gateway/`（Unified News Source Gateway）


---

## 五、追加批次：文本 #10（19 条 CN 源，2026-09-04 接入后探测）

### ✅ 正常（9）
极客公园(官方+查询替代同URL) · 钛媒体官方 · 人人都是产品 · 雷锋网 · 财新(injahow) · 澎湃新闻(injahow) · 见闻实时(injahow) · 金十数据(rsshub 2KB 正常)

### ⚠️ 问题（9）
| 源 | 类型 | 问题 | 建议 |
|---|---|---|---|
| 每日经济·查询替代 | google | 超时 | 需代理 |
| 澎湃财经·查询替代 | google | 超时 | 需代理（注: 澎湃另有 injahow 官方源可用） |
| 中国金融·查询替代 | google | 超时 | 需代理 |
| 和讯财经·查询替代 | google | 超时 | 需代理 |
| 虎嗅·查询替代 / 虎嗅 | google | 超时 | 需代理（注: 虎嗅官网 rss 曾遭阿里云 WAF） |
| 界面新闻 | google | 超时 | 需代理 |
| 雪球今日 | rsshub | HTTP 503 | rsshub.rssforever.com 限流 |
| 格隆汇 | rsshub | HTTP 503 | rsshub.rssforever.com 限流 |

> 报告：`docs/source_health_cn10.json`


---

## 六、追加批次：文本 #11（17 条，2026-09-04 接入后探测）

### ✅ 正常（3）
InfoQ · 开源中国 · 掘金后端(injahow)

### ⚠️ 问题（14）
| 源 | 类型 | 问题 | 建议 |
|---|---|---|---|
| 微博热搜 / 知乎热榜 / 百度热搜 / 今日头条 | rsshub | HTTP 503 | rsshub.rssforever.com 实例全线下限，需换实例或等待恢复 |
| 小红书热点 / X海外热点 / 凤凰科技 | rsshub | HTTP 503 | 同上 |
| 小红书热点(备) / X海外热点(备) | rsshub(app) | 连接失败 | rsshub.app 实例不可达 |
| 凤凰资讯 | standard | DNS 错误 | feed.ifeng.com 域名解析失败，需查证最新 RSS 域名 |
| 掘金前端 / 纽时中国 | google | 超时 | 需代理 |
| BBC中文 / 德国之声 | standard | 超时 | 需海外网络 |

> 报告：`docs/source_health_t11.json` · 重要发现：**rsshub.rssforever.com 全线 503**（10 条源受影响），后续需整体迁移 rsshub 实例（备选 rss.injahow.cn / 自建 RSSHub）。


---

## 七、追加批次：文本 #12（19 条，2026-09-04 接入后探测）

### ✅ 正常（2）
见闻热门 · 见闻全球（rss.injahow.cn 实例正常——印证迁移方向）

### ⚠️ 问题（16）
| 源 | 类型 | 问题 | 建议 |
|---|---|---|---|
| 中国证券报 | standard | **HTTP 404** | 官方 `cs.com.cn/sylm/jdxw/rss.xml` 已失效，需找新地址 |
| 中国证券网 | standard | **HTTP 404** | 官方 `cnstock.com/rss/headline.xml` 已失效，需找新地址 |
| 区块律动 / 中证要闻 / 上交所公告 / 深交所公告 / 中证指数 / 金融界 / 中国金融信息 | rsshub | HTTP 503 | rsshub.rssforever.com 全线故障（累计 17 源受影响） |
| ODaily / 证券日报 / 证券报焦点 | rsshub | 超时 | rsshub 实例波动 |
| FT中国 / 汽车之家 / 一财全球 | google | 超时 | 需代理 |
| 法广中文 | standard | 超时 | 需海外网络 |

> 报告：`docs/source_health_t12.json`


---

## 八、追加批次：文本 #13（15 条，2026-09-04 接入后探测）

### ✅ 正常（1）
金十重要（rsshub，999B 但内容有效）

### ⚠️ 问题（14）
| 源 | 类型 | 问题 | 建议 |
|---|---|---|---|
| 新浪基金 / 腾讯财经 / 头条股票 / 头条A股 / 头条财经 / 微博股票 / 雪球热门 / 雪球今日话题 / 东财财经新闻 / 东财上市公司 / 新浪美股 | rsshub | HTTP 503 | rsshub.rssforever.com 全线故障（累计 28 源受影响）——**急需整体迁移** |
| 知乎股市 / 财联社移动版 | rsshub | 超时 | rsshub 实例波动 |
| 东财策略 | google | 超时 | 需代理 |

> 报告：`docs/source_health_t13.json` · 注：财联社移动版权威 URL 实为 cls/telegraph（与财联社电报同源）


---

## 九、追加批次：文本 #15（5 条全新源 en，2026-09-04 接入后探测）

### ✅ 正常（1）
NPR商业（feeds.npr.org，14KB ✅ 本机可达可采集）

### ⚠️ 问题（4）
| 源 | 问题 | 建议 |
|---|---|---|
| 雅虎财经 | HTTP 403 | finance.yahoo.com 反爬（news/rssindex 需 UA/cookies 或换查询参数） |
| 卫报世界 | 超时 | 需海外网络（注：卫报商业同站此前也超时） |
| 半岛电视台 | 超时 | 需海外网络 |
| Reddit经济 | 超时 | 需海外网络（reddit .rss 通常需 UA） |

> 报告：`docs/source_health_t15.json` · 新增源格式含 lang 字段（en）

---

## 十、已处理项（2026-09-04 统一处理第一批）

| 源 | 处理 | 状态 |
|---|---|---|
| CNBC / CNBC科技 | url 直改 `.../rss.html`（实测 200，30 条可抓） | ✅ 已解决 |
| 中国证券报 / 中国证券网 | 官网首页无 RSS link 声明，官方 RSS 疑似已停 | 🔒 转暂留 |
| 雅虎财经 | 403 无法通过 UA/headers 规避（疑 geo/IP 封禁） | 🔒 转暂留 |

---

## 十一、暂留问题（需代理/人工/后续处理）

> 以下问题当前环境无法直接解决，统一暂留，待具备条件后处理。

### 🔴 需整体迁移（rsshub 实例故障）
rsshub.rssforever.com 全线 503，受影响 **28+ 源**（微博/知乎/百度/头条/小红书/雪球/东财/凤凰科技/区块律动/中证系/沪深交所公告/金融界/中国金融信息/金十等）
→ 方案：整体切到 `rss.injahow.cn` 可用路由或自建 RSSHub；`rsshub.app` 亦不可达需验证

### 🔴 官方源失效（需人工找新地址）
- 中国证券报 `cs.com.cn/sylm/jdxw/rss.xml` → 404，官网无 RSS link
- 中国证券网 `cnstock.com/rss/headline.xml` → 404，官网无 RSS link

### 🟠 反爬/封禁（需 headers/cookies/代理）
- Investing → 403
- 雅虎财经 → 403（疑 geo 封禁）

### 🟠 需海外网络/代理（URL 均已确认为官方格式）
- BBC商业/科技/中文/世界 · FT×2 · Economist×2 · Bloomberg×2 · HackerNews · Business Insider · NYT · 华邮 · 环球邮报 · 卫报(商业/世界) · 半岛 · Reddit · Reuter示例
- Google News 类 ~30 条（AP/联合早报/日经/今日美国/芝报/电讯报/财联社/东财/36氪/凤凰/证券时报/界面/掘金/纽时中国/FT中国/汽车之家/一财/每日经济/澎湃财经/中国金融/和讯/虎嗅/东财策略等）


---

## 十二、网络打通后状态更新（2026-09-04，VPN/代理已可用）

**背景**：本地代理 7897 已生效（系统代理/TUN），海外源（Google News/BBC/FT/卫报/半岛等）全部可达。

### ✅ 已解决
- 79/123 源内容有效（原 16），已回写 `_health: verified`
- Google News 类 ~30 源全部可采集（AP头条已入库 103 条验证）
- BBC商业 43 条、CNBC/CNBC科技 直改 rss.html 后各 30+ 条入库
- rsshub：华尔街见闻/第一财经/知乎热榜/澎湃新闻 迁移到 rss.injahow.cn 实采成功
- 数据库 826 → 1073 条
- global.proxy=http://127.0.0.1:7897 已写入网关配置

### 🔒 仍暂留（真实不可用）
1. **rsshub 33 源**：rssforever 全 503、rsshub.app 403、injahow 无此路由 → **需自建 RSSHub** 或等公共实例恢复
2. **中国证券报/中国证券网**：官方 RSS 404 且官网无 link，需人工找替代
3. **华尔街见闻(已修复)/凤凰资讯/证券日报**：连接层偶发（重试可通）
4. Reuters：官方无公开 RSS（401 正常），示例源建议移除
5. 环球邮报：推测 URL 404，需实测正确路径
6. 雅虎财经：403（geo）—— 代理下已 200 ✅（已解决）

---

## 十三、批 1 处理记录（2026-09-04 · 按《news_source_solution_plan.md》执行）

### ✅ 已完成
| 动作 | 结果 |
|---|---|
| Reuter-示例（无公开 RSS）删除 | 源总数 123→122 |
| Investing 403 | `fallback.google_news_site_search=investing.com` → 实测自动降级 GNews 抓 10 条 ✅ |
| 中国证券报 官方 RSS 404 | 改 `google_news_rss` + `site:cs.com.cn`（实测 0 条，GNews 收录空 → 保留观察） |
| 中国证券网 官方 RSS 404 | 改 `google_news_rss` + `site:cnstock.com`（实测抓 100 条 ✅） |
| 44 个 rsshub 源 | 全部显式补 `fallback.hosts=["rss.injahow.cn"]`（实测 zhihu 200 / weibo 503 / toutiao 404 / xueqiu 503） |
| 知乎热榜 | injahow 通道端到端实测抓 40 条 ✅ |
| **代理双路**（小改 `BaseSourceAdapter`） | 直连短超时优先 → 失败自动走显式代理；实测 BBC 47 条/知乎 40 条均自动兜代理 ✅ |
| **全量探测** | `probe_sources.py`（代理）122 源 36s：**85 有效 / 37 问题**；google_news_rss **29/29**、standard_rss 43/47、ft_rss 2/2、rsshub_rss 11/44 |

### ⏭️ 批 2 进度
- [x] `social_hot_json` 直连适配器骨架（`news_gateway/custom.py` + 工厂注册）—— WAF 挑战页显式识别为 `SourceBlocked`，不产脏数据
- [x] **微博热搜-直连打通**：`weibo.com/ajax/side/hotSearch` + SUB cookie + Referer/X-Requested-With → 实测 **52 条**（直连、无代理）；后按用户『**排除有风险源**』决策标 `paused`+`enabled:false`，**不进自动链路**（手动 `--only 微博热搜-直连` 仍可调试）
- [x] 雪球通道核验 + **已搁置**（2026-09-04 用户确认无法维护登录态）：主域 `listV2` 阿里云 WAF 需 JS；`stock.xueqiu.com` 子域无 WAF 但需真实登录态（cookie `uid=-1`）。**依赖登录 cookie 的方案放弃**，源已标 `_health=paused`。自动化替代路径（供未来选择）：① Playwright 无头匿名执行 WAF JS → 主域 listV2（免登录）；② 等价内容源（新浪 7×24/东财/见闻/金十，已接入，零成本）；③ 公共 rsshub 实例恢复后原 `rsshub_rss` 雪球源自动复活（fallback 链已配）
- ~~微博热搜-直连接入 scheduler~~ → ❌ **不做**(2026-09-10 用户决定:微博不接入;源保持 `_health=paused` / `enabled:false`,不进自动链路,手动 `--only 微博热搜-直连` 仍可调试)
- [x] probe 感知 `fallback.hosts` / 健康自动回写 — ✅ 2026-09-10 完成:`scripts/probe_sources.py` 现支持主 URL 失败时按 host 替换遍历备选实例,并新增 `--write-health` 回写 `_health` 与把可用备选提至 hosts 首位
- [x] **网关采集按 poll.group 分档接入 scheduler**（2026-09-04，详见 `docs/爬取时间表.md`）：cn_fast(20) 盘中 120s/盘后 600s、media(51) 300s 恒定、slow(9) 600s、cron 每日 04:00 refill 全量兜底；仅采 `_health=verified` 零风险源，登录态/paused 自动排除，`asyncio.to_thread` 防阻塞；分组断言 20+51+9=80 通过，实采 cn_fast 1156条/105s、slow 295条/28s

### 🔴 未决核心（2026-09-04 探测后）
33 个 rsshub 源（微博/雪球/头条/东财/中证系/交易所公告等）仍无可用公共实例 → 批 2 主战场：custom 直连（雪球骨架已就绪）+ 第三方聚合兜底 + 必要时自建 RSSHub。
