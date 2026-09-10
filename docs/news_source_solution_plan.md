# 新闻源遗留问题 · 解决方案规划

> 版本：v1 · 2026-09-04
> 关联：《docs/news_source_issues.md》（问题登记）+ 本规划（处置策略）
> 范围：微博/雪球等**无官方 RSS** 平台的获取路径（重点），以及全部遗留问题簇的处置矩阵与路线图。
> 关键原则：**不把任何一条数据通道押在单一公共实例上**——每源走"多通道冗余 + 健康自检 + 自动回写"，不同问题用不同解（举一反三），同一问题也备多路兜底。

---

## 一、现状判断（含 2026-09-04 实测）

接入 `config/sources_config.json` 共 **123 源**，`news_gateway` 统一网关 + `scripts/probe_sources.py` 健康探测已成型。但遗留问题呈**结构性**，不是逐个修 URL 能解决的：

| # | 问题簇 | 量级 | 根因 | 关键性 |
|---|---|---|---|---|
| P0 | rsshub 公共实例单点故障 | **28+ 源**（微博/雪球/头条/东财/知乎/小红书/凤凰/金十等） | 全挂在 `rsshub.rssforever.com`，实例全 503 | 🔴 最高——换 URL 修不完，要换"选路机制" |
| P1 | 无官方 RSS + 强反爬的平台 | 微博/雪球/小红书/今日头条热榜 | RSSHub 只是中转，其上游同样要对抗源站风控 | 🔴 本次重点 |
| P2 | 官方源失效 | 中国证券报/中国证券网 RSS 404 | 官网停 RSS 且无声明 | 🟠 人工找替代 |
| P3 | 反爬/封禁 | Investing 403、雅虎 403（代理已解） | geo/IP/headers 级封锁 | 🟠 |
| P4 | 海外源/Google News 类 | ~30 条 | 需代理；代理单点失效即全挂 | 🟠 机制问题 |
| P5 | 摘要过短 | Google News/热榜类 | 无全文 | 🟡 已有 fulltext_enrich 可强化 |
| P6 | 系统性缺口 | — | probe 是瞬时快照，无历史健康库、不自动回写可用实例；网关尚未接入调度 | 🟡 让以上问题"自愈"的前提 |

**当日实测事实**（本机 + 代理 7897）：

| 通道 | 结果 | 含义 |
|---|---|---|
| `rsshub.rssforever.com/xueqiu/hots` | **503** | 主实例仍故障 |
| `rss.injahow.cn/xueqiu/hots` | **503** | injahow 部分路由被限流，并非全部可用 |
| `rsshub.app/xueqiu/hots` | **403** | 该实例不可作为 fallback |
| 雪球 `xueqiu.com`（拿匿名 cookie 后请求 hot/listV2） | 首页 200，**API 命中阿里云 WAF JS 挑战** | 直连 API 不能靠普通 GET，需 cookie 池/浏览器级对抗或换道 |
| 微博 `m.weibo.cn` 热榜（无登录态） | 空/需登录态 | 直连需要 cookie |
| `tophub.today`（第三方热榜聚合） | **200** | 借道聚合页可行 |
| 本机代理 7897 | 可用 | 海外源（Google News/BBC 等）已通 |

结论一句话：**微博/雪球的问题不是"换个 RSSHub 实例"，而是"RSSHub 这条中转本身也不稳定、且其上游（微博/雪球站）同样有风控"——所以必须给它们开非 RSSHub 的直通道，并接受"热榜类信号低频、可降级"。**

---

## 二、微博/雪球专项：自建 RSSHub 之外的全部获取路径

> 先明确需求性质：`微博热搜/微博股票/雪球热门/雪球今日话题` 本质是**热榜/热度信号**而非完整新闻流。信息价值上，其中 80–90% 的"内容"已被本系统已接入的新浪 7×24、东财、华尔街见闻、金十等财经快讯覆盖；热榜的增量是"市场当下在关注什么"的情绪面。因此方案分层：**内容流求全、热度榜求稳不求全**。

### 路径谱（按"自建成本"从低到高）

| 路径 | 是什么 | 对微博/雪球的现实性（实测） | 适用 |
|---|---|---|---|
| **A. 公共 RSSHub 实例轮换池** | 现有 `RssHubAdapter._host_candidates` 已支持换 host，只是 fallback 列表残缺 | injahow 部分路由 503、rsshub.app 403 → 单纯轮换今天救不了雪球；微博热搜类路由多数公共实例被限 | 过渡保底，不是终态 |
| **B. 第三方聚合"热榜 API/页面"** | 别人已经爬好各站热榜：`tophub.today`（实测可达）、开源项目自部署（如 DailyHotApi 类，微博/知乎/头条/雪球分榜 JSON） | tophub 200 ✅；开源 API 公共域名常失效，**推荐自部署**（比 RSSHub 轻得多：纯 HTTP 拉取，无需反代全套路由） | 高性价比，适合"微博热搜/雪球热门"这类**榜单** |
| **C. 源站半公开 JSON 接口直连（custom adapter）** | 给微博/雪球写专用适配器进 `news_gateway`：微博 `m.weibo.cn` 容器接口/`weibo.com/ajax`；雪球 `xueqiu.com/statuses/hot/listV2.json` | 雪球需过阿里云 WAF、微博需登录 cookie → **可行但有风控成本**，必须低频+限量+可开关灰度 | 长期自用的"原汁原味"数据 |
| **D. 浏览器自动化兜底（Playwright 无头）** | 加载页面让 JS 挑战自解，拿 cookie 后转交 JSON 接口 | 能解 C 的 WAF/登录，但重、吃资源、更易被风控识别 | 只留给 C 走不通的 1–2 个源，低频 |
| **E. 等价内容源替代** | 不解决"爬微博/雪球"，而解决"我要的信号"：用已接入的新浪/东财/见闻/金十快讯替代 90% 信息价值 | 立即可用，零成本 | 所有"股票/热门"类主题源的默认兜底 |
| **F. 自建 RSSHub（或自研精简聚合）** | 33+ 源长期依赖中转时才划算 | 一次性成本，后续仍需养 cookie/风控配置 | 决策点，见 §五 |

### 推荐的每源默认链（任一成功即达标，靠 `news_id` 天然去重）

- **微博热搜/微博股票**：`custom 微博直连(灰度)` → `公共实例轮换(现 rsshub 链)` → `tophub 微博榜(B)` → `E 等价源(新浪热搜替代)` → `D 兜底`
- **雪球热门/雪球今日话题**：`custom 雪球直连(灰度，cookie 槽位)` → `rsshub 实例轮换` → `tophub 雪球分榜(B)` → `E 等价源(东财/见闻热度)` → `D 兜底`
- 知乎/小红书/头条同构套用（这些源知乎已有 injahow 可用实例，先走 A）。

> 设计要点：**cookie 配置外置**（`.env`/`auth` 文件，绝不进 JSON 配置进 git）；**直连适配器必须带开关与熔断**（连续 N 次被 WAF/风控 → 自动降级到 B/E 并告警，不阻塞采集）。

---

## 三、其余遗留问题处置矩阵

| 问题簇 | 根因 | 推荐动作（非唯一解） | 落点 | 优先级 |
|---|---|---|---|---|
| rsshub 28+ 源 503（P0） | 实例单点 | ① fallback.hosts 补全到**全部已知公共实例**；② probe 启动时对每个源轮询实例、把首个可达实例**回写**到配置 `_health.url`（自愈）；③ 微博/雪球等另走 §二 | `sources_config.json` 配置 + `RssHubAdapter` + probe 脚本 | 🔴 本周 |
| 中证报/中证网 RSS 404（P2） | 官方停 RSS | 改 Google News `site:` 查询兜底；官网无 RSS link 已确认 → 若 GNews 也差则**降级移除**并标注，避免死源空跑 | 源配置 type/google 或移除 | 🟠 |
| Investing 403（P3） | 反爬 | 试 auth.headers/cookies 补全；不行则 fallback `google_news_site_search: investing.com`（框架字段已支持，0 代码） | 配置 | 🟠 |
| Reuters 示例源 | 无公开 RSS | 删除示例源；若需 Reuters 内容 → Google News `site:reuters.com`（与 AP 同构，已验证可行） | 配置 | 🟡 |
| 海外源/Google News 超时（P4） | 代理依赖 | 现 session 逻辑"有代理则 trust_env=False"是**代理优先**；改成 **双路尝试**（先直连、超时/失败自动走代理），并对每个源支持 `auth.proxy` 单独覆盖；代理探活并入 probe | `BaseSourceAdapter` + 配置 | 🟠 |
| 摘要过短（P5） | 无全文 | fulltext_enrich 已有 → 扩为**可插拔正文解析器**（Google News/雪球/微博 m 站 JSON 正文各有不同抽取逻辑），按域名注册 | `news_gateway/runner.py` + 新 parser 注册表 | 🟡 |
| 系统性缺口（P6） | 无历史/无回写/未调度 | ① probe 结果写 `docs/health_history.jsonl`（时间序列）；② 探活自动回写可用 url；③ 网关采集挂入 `scheduler`（与 legacy crawler 并存，靠 news_id 去重，无冲突） | probe 脚本 + scheduler | 🟡 |

---

## 四、架构落点（改动最小化）

现有 `news_gateway` 是"注册表 + 适配器 + fixes + 统一输出"结构，全部扩展点**已预留**，不动 services/domain：

1. **新适配器类型**：`ADAPTER_REGISTRY.register_adapter("social_hot_json", ...)`（或 `custom_json`），复用 `BaseSourceAdapter`（节流/重试/代理/cookie 槽位）→ 输出 `UnifiedArticle` → 直接入库。微博/雪球各是一个 adapter 类，靠 `source_cfg` 区分 endpoint 与解析器。
2. **实例轮换自愈**：`RssHubAdapter._host_candidates` 已存在，只需：① 配置文件级补实例列表；② probe 输出"该源当前可用实例"并回写。
3. **fixes 规则注册表**：加"热搜查询参数/域名纠错"类规则（现有 `fix_registry` 支持，0 结构改动）。
4. **auth 外置**：adapter 已读 `source_cfg.auth.headers/cookies/proxy`，把 cookies 从"配置内字符串"改为"读 cookie 文件"即可。
5. **legacy crawlers（sina/eastmoney）不动**：它们已是官方接口直连的样板（证明"官方/半公开接口直连"这条路在本项目成立）。

---

## 五、执行路线图（三批，各带验收）

### 批 1 —— 零/低代码(配置层) ✅ 已完成(2026-09-04 ~ 09-10)
- [x] ① rsshub 源 fallback.hosts 补齐 + 3 实例实测标注;见闻/第一财经 于 2026-09-10 再补 `rsshub.rssforever.com`/`rsshub.app`;
- [x] ② 已死官方源(中证报/中证网)改 GNews 兜底;Investing 补 GNews fallback;Reuters 示例删除(源数 123→122);
- [x] ③ 代理双路(小改 adapter)+ 重跑 `probe_sources.py` 全量(122 源 36s:85 有效 / 37 问题);
- [x] ④ 更新 `docs/news_source_issues.md` 状态。
- **验收**:内容有效 85/122(受 rsshub 公共实例 503 拖累);每 rsshub 源配置有 ≥1 个实例;无 404 死源空跑。

### 批 2 —— 直连灰度 + 自愈 ✅ 已完成/已决策(2026-09-04 ~ 09-10)
- [x] ① `custom_json`/`social_hot_json` 适配器骨架(工厂注册;雪球经核验搁置——WAF/登录态);
- [x] ② 微博:已完成"热搜直连"打通(实测 52 条);后续 ⏭ **用户决定不接入**(源保持 `paused`,不进自动链路);
- [x] ③ probe 感知 `fallback.hosts` + 可用实例/健康自动回写(`--write-health`,2026-09-10);
- [x] ④ 网关按 `poll.group` 接入 scheduler(cn_fast/media/slow + 每日 refill,2026-09-04)。
- **验收**:微博/雪球均未进自动链路(用户决策);probe 自愈能力已落地;rsshub 死源待公共实例恢复或自建(批 3)。

### 批 3 —— 后续，视批 1/2 结果（决策点）
- [ ] tophub/热榜聚合自部署（若 B 通道优于 C 的稳定性）；
- [ ] Playwright 兜底（仅 C 无法覆盖的源）；
- [ ] 自建 RSSHub（若公共实例连续 ≥ 14 天不可用且依赖源仍 >20）。
- **验收**：触发条件明确定义，不默认执行。

---

## 六、需要你拍板的开放决策

1. **微博/雪球直连的接受度**：直连需低频 + cookie（微博需登录态），个人使用灰度可接受吗？还是直接以"第三方聚合/等价源"为正式通道、直连只作可选实验？
2. **热度信号定位**：同意"微博股票/雪球热门降级为可选信号源、信息价值以现有财经快讯为主"吗？
3. **自建倾向**：你是否已具备/愿意常驻一台可跑 Docker 的机器？（决定批 3 的自建路线 vs 精简聚合自部署）
