/* 6Signal console: four bounded pages, one shared 180-second refresh policy. */
(() => {
  var page = document.body.dataset.page || "global";
  var state = {data: null, news: [], filter: "all", query: ""};
  var $ = function(id) { return document.getElementById(id); };
  var metaApi = (document.querySelector('meta[name="api-url"]') || {}).content || "";
  var API = metaApi || (location.port === "8100" ? location.origin : location.origin + "/api");
  var esc = function(v) { return String(v == null ? "—" : v).replace(/[&<>"']/g, function(c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); };
  var number = function(v) { var n = Number(v); return Number.isFinite(n) ? n : null; };
  var fmt = function(v, d) { var n = number(v); return n == null ? "—" : n.toLocaleString("zh-CN", {maximumFractionDigits: d == null ? 2 : d}); };
  var pct = function(v) { var n = number(v); return n == null ? "—" : (n >= 0 ? "+" : "") + n.toFixed(2) + "%"; };
  var pctClass = function(v) { var n = number(v); return n == null ? "" : n >= 0 ? "positive" : "negative"; };
  var isoTime = function(v) { return v ? new Date(v).toLocaleString("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"}) : "—"; };
  var shortTime = function(v) { return v ? new Date(v).toLocaleTimeString("zh-CN", {hour12:false, hour:"2-digit", minute:"2-digit"}) : "—"; };
  var sourceName = function(x) { return (x && x.metadata && (x.metadata.source || x.metadata.category)) || "source-" + ((x && x.source_id) || "—"); };
  var setText = function(id, value) { if ($(id)) $(id).textContent = value; };

  async function getJSON(path) {
    var response = await fetch(API + path, {headers:{Accept:"application/json"}});
    if (!response.ok) throw new Error(response.status + " " + response.statusText);
    return response.json();
  }
  function errorBanner(error) { var box = $("errorBanner"); if (!box) return; box.textContent = "数据读取失败：" + (error.message || error); box.classList.add("show"); }
  function clearError() { if ($("errorBanner")) $("errorBanner").classList.remove("show"); }
  function renderHealth(health) {
    var rows = Array.isArray(health) ? health : (health && health.sources) || [];
    var bad = rows.filter(function(x) { return x.status === "STALE" || x.status === "ERROR"; }).length;
    if ($("healthPill")) { $("healthPill").textContent = bad ? bad + " 个源需关注" : "数据健康"; $("healthPill").classList.toggle("bad", bad > 0); }
    if ($("drawerSummary")) $("drawerSummary").textContent = rows.length ? rows.length + " 个数据源 · " + (bad ? bad + " 个延迟或失联" : "当前没有明显异常") : "暂无来源状态记录";
    var html = rows.length ? rows.map(function(x) { return '<div class="health-row"><div><b>' + esc(x.source || x.name) + '</b><span class="health-status ' + esc(x.status || "UNKNOWN") + '">' + esc(x.status || "UNKNOWN") + '</span></div><small>延迟 ' + (x.latency_seconds == null ? "—" : fmt(x.latency_seconds, 0) + "s") + ' · ' + esc(isoTime(x.last_event)) + '</small></div>'; }).join("") : '<span class="empty">暂无来源状态</span>';
    if ($("healthList")) $("healthList").innerHTML = html;
    ["cryptoHealth", "newsHealth"].forEach(function(id) { if ($(id)) $(id).innerHTML = rows.slice(0, 6).map(function(x) { return '<div class="health-mini-row"><span>' + esc(x.source) + '</span><b class="' + esc(String(x.status || "").toLowerCase()) + '">' + esc(x.status || "—") + '</b></div>'; }).join("") || '<span class="empty">暂无状态</span>'; });
  }
  function openHealth() { if ($("drawerBackdrop")) $("drawerBackdrop").classList.add("open"); if ($("healthDrawer")) $("healthDrawer").classList.add("open"); }
  function closeHealth() { if ($("drawerBackdrop")) $("drawerBackdrop").classList.remove("open"); if ($("healthDrawer")) $("healthDrawer").classList.remove("open"); }
  function renderTicker(items) {
    if (!$("tickerRow")) return;
    $("tickerRow").innerHTML = (items || []).slice(0, 12).map(function(x) { return '<span class="ticker-item"><b>' + esc(x.name || x.code || x.symbol) + '</b><strong>' + esc(x.price == null ? (x.mark_price == null ? "—" : x.mark_price) : x.price) + '</strong><em class="' + pctClass(x.change_pct) + '">' + pct(x.change_pct) + '</em></span>'; }).join("") || '<span class="empty">暂无行情</span>';
  }

  function renderGlobal(data) {
    state.data = data;
    var regime = data.regime || {}, risk = data.risk || {}, coverage = data.coverage || {}, markets = data.markets || [];
    setText("asOf", "快照 " + isoTime(data.as_of)); setText("dockAsOf", isoTime(data.as_of)); setText("dockStatus", data.status === "ok" ? "全球快照正常" : "全球数据不足");
    setText("coverageBadge", "覆盖 " + (coverage.available || 0) + "/" + (coverage.expected || 0)); setText("coverageValue", coverage.pct == null ? "—" : coverage.pct + "%"); setText("coverageDetail", (coverage.available || 0) + " / " + (coverage.expected || 0) + " 个核心序列");
    setText("marketRegime", regime.label || "数据不足"); setText("regimeScore", "Regime score " + (regime.score == null ? "—" : regime.score)); setText("riskScore", risk.score == null ? "—" : risk.score); setText("riskLabel", risk.label || "等待风险因子");
    setText("sentimentScore", regime.score == null ? "—" : regime.score); setText("sentimentLabel", regime.label || "跨资产规则评分"); setText("riskNumber", risk.score == null ? "—" : risk.score); setText("riskTitle", risk.label || "数据不足"); setText("riskCopy", (risk.reasons || []).join(" · ") || "等待 VIX 与市场状态"); setText("riskBadge", risk.label || "—");
    if ($("riskRing") && risk.score != null) $("riskRing").style.setProperty("--score", Math.min(100, Math.max(0, risk.score)) + "%");
    renderTicker(markets); renderHealth(data.health || []);
    if ($("marketTable")) $("marketTable").innerHTML = markets.length ? markets.map(function(x) { return '<tr><td><b>' + esc(x.name || x.code) + '</b><small>' + esc(x.code) + '</small></td><td class="mono">' + esc(x.price) + '</td><td class="' + pctClass(x.change_pct) + ' mono">' + pct(x.change_pct) + '</td><td>' + esc(isoTime(x.event_time || x.ts)) + '</td><td><span class="status ' + esc(x.status || "UNKNOWN") + '">' + esc(x.status || "—") + '</span></td></tr>'; }).join("") : '<tr><td colspan="5" class="empty">暂无全球行情</td></tr>';
    if ($("riskFactors")) $("riskFactors").innerHTML = (regime.factors || []).filter(function(x) { return x.value != null; }).slice(0, 6).map(function(x) { return '<div class="factor-row"><span>' + esc(x.code) + '</span><span class="factor-track"><i style="width:' + Math.min(100, Math.abs(Number(x.contribution || 0)) * 100) + '%"></i></span><b>' + pct(x.value) + '</b></div>'; }).join("") || '<span class="empty">无可用因子</span>';
    if ($("crossAssetList")) $("crossAssetList").innerHTML = (data.cross_asset || []).map(function(x) { return '<div class="signal-line"><span class="health-status ' + esc(x.severity || "medium") + '">' + esc(x.severity || "观察") + '</span><div><b>' + esc(x.title) + '</b><small>' + esc(x.detail) + '</small></div></div>'; }).join("") || '<span class="empty">暂无跨资产异常</span>';
    if ($("alertList")) $("alertList").innerHTML = (data.alerts || []).slice(0, 8).map(function(x) { return '<div class="alert-line"><b>' + esc(x.title) + '</b><span class="' + (x.severity === "high" ? "negative" : "warning") + '">' + esc(x.severity || "观察") + '</span></div>'; }).join("") || '<span class="empty">暂无市场提醒</span>';
    if ($("historyGrid")) $("historyGrid").innerHTML = Object.entries(data.histories || {}).filter(function(pair) { return pair[1] && pair[1].length; }).map(function(pair) { var last = pair[1][pair[1].length - 1]; return '<div class="history-cell"><b>' + esc(pair[0]) + '</b><strong>' + esc(last.price) + '</strong><small>' + esc(isoTime(last.ts)) + '</small></div>'; }).join("") || '<span class="empty">暂无历史序列</span>';
    renderNewsList(data.news || [], "newsList", 8);
  }

  function renderAshare(data) {
    state.data = data; var b = data.breadth || {}, coverage = data.coverage || {};
    setText("asOf", "快照 " + isoTime(data.as_of)); setText("dockAsOf", isoTime(data.as_of)); setText("dockStatus", data.status === "ok" ? "A 股快照正常" : "A 股数据不足"); setText("coverageBadge", (coverage.quotes || 0) + " 只行情");
    setText("upCount", fmt(b.up_count, 0)); setText("downCount", fmt(b.down_count, 0)); setText("limitCount", fmt(b.limit_up, 0) + " / " + fmt(b.limit_down, 0)); setText("breadthScore", b.score == null ? "—" : pct(b.score)); setText("breadthTotal", fmt(b.total, 0) + " 只股票");
    renderTicker(data.indices || data.quotes || []); renderHealth(data.health || []);
    if ($("sectorTable")) $("sectorTable").innerHTML = (data.sectors || []).slice(0, 50).map(function(x) { return '<tr><td><b>' + esc(x.name || x.code) + '</b></td><td>' + esc(x.kind === "concept" ? "概念" : "行业") + '</td><td class="' + pctClass(x.change_pct) + ' mono">' + pct(x.change_pct) + '</td><td class="mono">' + (x.main_net == null ? "—" : fmt(Number(x.main_net) / 100000000, 2) + "亿") + '</td><td class="mono">' + (x.main_ratio == null ? "—" : pct(x.main_ratio)) + '</td></tr>'; }).join("") || '<tr><td colspan="5" class="empty">暂无板块数据</td></tr>';
    if ($("indexList")) $("indexList").innerHTML = (data.indices || []).slice(0, 8).map(function(x) { return '<div class="quote-line"><b>' + esc(x.name || x.code) + '</b><strong>' + esc(x.price) + '</strong><span class="' + pctClass(x.change_pct) + '">' + pct(x.change_pct) + '</span></div>'; }).join("") || '<span class="empty">暂无指数</span>';
    if ($("quoteTable")) $("quoteTable").innerHTML = (data.quotes || []).slice(0, 30).map(function(x) { return '<tr><td class="mono">' + esc(x.code) + '</td><td>' + esc(x.name) + '</td><td class="mono">' + esc(x.price) + '</td><td class="' + pctClass(x.change_pct) + ' mono">' + pct(x.change_pct) + '</td><td class="mono">' + (x.amount == null ? "—" : fmt(Number(x.amount) / 100000000, 2) + "亿") + '</td></tr>'; }).join("") || '<tr><td colspan="5" class="empty">暂无股票行情</td></tr>';
    if ($("breadthBars")) { var total = Math.max(1, Number(b.total || 0)); $("breadthBars").innerHTML = [["上涨", b.up_count, "positive"], ["下跌", b.down_count, "negative"], ["平盘", b.flat_count, "warning"]].map(function(x) { return '<div class="breadth-row"><span>' + x[0] + '</span><div><i class="' + x[2] + '" style="width:' + Math.min(100, Number(x[1] || 0) / total * 100) + '%"></i></div><b>' + fmt(x[1], 0) + '</b></div>'; }).join(""); }
    if ($("lhbList")) $("lhbList").innerHTML = (data.lhb || []).slice(0, 12).map(function(x) { return '<div class="news-item"><div class="news-meta"><span class="source-pill red">龙虎榜</span><span>' + esc(x.date) + '</span></div><b>' + esc(x.code) + " " + esc(x.name) + '</b><p>' + esc(x.reason || "上榜记录") + '</p></div>'; }).join("") || '<span class="empty">暂无龙虎榜记录</span>';
  }

  function renderCrypto(data) {
    state.data = data; setText("asOf", "快照 " + isoTime(data.as_of)); setText("dockAsOf", isoTime(data.as_of)); setText("dockStatus", "加密衍生品快照"); renderHealth(data.health || []);
    var crypto = data.crypto || []; renderTicker(crypto);
    if ($("cryptoTable")) $("cryptoTable").innerHTML = crypto.map(function(x) { return '<tr><td><b>' + esc(x.symbol) + '</b></td><td class="mono">' + esc(x.open) + '</td><td class="mono">' + esc(x.high) + '</td><td class="mono">' + esc(x.low) + '</td><td class="mono">' + esc(x.price) + '</td><td class="mono">' + (x.volume == null ? "—" : fmt(x.volume, 2)) + '</td><td class="mono">' + esc(x.event_time ? new Date(x.event_time).toLocaleDateString() : "—") + '</td><td><span class="status ' + esc(x.status || "—") + '">' + esc(x.status || "—") + '</span></td></tr>'; }).join("") || '<tr><td colspan="8" class="empty">暂无日线快照</td></tr>';
    var stressed = crypto.filter(function(x) { return Math.abs(Number(x.basis_pct || 0)) > .5 || Math.abs(Number(x.funding_rate || 0)) > .0005; });
    if ($("cryptoRisk")) $("cryptoRisk").innerHTML = stressed.map(function(x) { return '<div class="alert-line"><b>' + esc(x.symbol) + '</b><span class="warning">注意</span><small>基差 ' + esc(x.basis_pct) + ' · 资金费率 ' + esc(x.funding_rate) + '</small></div>'; }).join("") || '<div class="empty">当前没有明显拥挤度异常</div>';
  }

  function renderNewsList(items, id, limit) {
    var box = $(id || "newsList"); if (!box) return;
    var filtered = (items || []).filter(function(item) { var text = ((item.title || "") + " " + (item.body || "") + " " + sourceName(item)).toLowerCase(); return (!state.query || text.indexOf(state.query) >= 0) && (state.filter === "all" || text.indexOf(state.filter) >= 0); });
    if (id === "newsList") setText("newsCount", filtered.length + " 条");
    box.innerHTML = filtered.slice(0, limit || 100).map(function(item) { return '<article class="news-item"><div class="news-meta"><span class="source-pill">' + esc(sourceName(item)) + '</span><span>' + esc(isoTime(item.published_at)) + '</span><span class="importance">重要度 ' + esc(item.importance) + '</span></div><a class="news-title" href="' + esc(item.url || "#") + '" target="_blank" rel="noopener">' + esc(item.title) + '</a><p>' + esc((item.body || "").replace(/\s+/g, " ").slice(0, 180)) + '</p><small class="news-tags">' + esc((item.affected_assets || []).slice(0, 5).join(" · ") || "市场新闻") + '</small></article>'; }).join("") || '<span class="empty">没有符合条件的新闻</span>';
  }

  async function loadNews() {
    var end = new Date(), start = new Date(end.getTime() - 24 * 3600 * 1000);
    var result = await Promise.all([getJSON("/v1/news?start=" + encodeURIComponent(start.toISOString()) + "&end=" + encodeURIComponent(end.toISOString())), getJSON("/v1/dashboard/health")]);
    state.news = result[0]; setText("asOf", "更新 " + shortTime(end)); setText("dockAsOf", shortTime(end)); setText("dockStatus", state.news.length + " 条新闻"); renderHealth(result[1].sources || []); renderNewsList(state.news); renderTicker(state.news.map(function(x) { return {name:sourceName(x), price:x.title}; }));
    var counts = {}; state.news.forEach(function(x) { var key = sourceName(x); counts[key] = (counts[key] || 0) + 1; });
    if ($("newsQuality")) $("newsQuality").innerHTML = Object.entries(counts).slice(0, 8).map(function(x) { return '<div class="quality-row"><span>' + esc(x[0]) + '</span><b>' + x[1] + '</b></div>'; }).join("") || '<span class="empty">暂无来源</span>';
  }
  async function load() {
    clearError();
    try { if (page === "global") renderGlobal(await getJSON("/v1/dashboard/global?skip_ai=true")); else if (page === "ashare") renderAshare(await getJSON("/v1/dashboard/ashare")); else if (page === "crypto") renderCrypto(await getJSON("/v1/dashboard/global?skip_ai=true")); else await loadNews(); } catch (error) { errorBanner(error); }
  }
  function wire() {
    if ($("refreshBtn")) $("refreshBtn").addEventListener("click", load); if ($("healthBtn")) $("healthBtn").addEventListener("click", openHealth); if ($("healthBtn2")) $("healthBtn2").addEventListener("click", openHealth); if ($("drawerBackdrop")) $("drawerBackdrop").addEventListener("click", closeHealth); document.querySelectorAll("[data-close-drawer]").forEach(function(x) { x.addEventListener("click", closeHealth); });
    if ($("newsSearch")) $("newsSearch").addEventListener("input", function(e) { state.query = e.target.value.trim().toLowerCase(); renderNewsList(state.news); });
    document.querySelectorAll("[data-filter]").forEach(function(button) { button.addEventListener("click", function() { state.filter = button.dataset.filter; document.querySelectorAll("[data-filter]").forEach(function(x) { x.classList.toggle("active", x === button); }); renderNewsList(state.news); }); });
    setInterval(function() { setText("clock", new Date().toLocaleTimeString("zh-CN", {hour12:false})); }, 1000); setText("clock", new Date().toLocaleTimeString("zh-CN", {hour12:false})); setInterval(load, 180000); load();
  }
  wire();
})();
