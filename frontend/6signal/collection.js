/* Read-only operational table. Unknown measurements stay unknown. */
(() => {
  const $ = id => document.getElementById(id);
  const meta = document.querySelector('meta[name="api-url"]');
  const api = (meta && meta.content) || (location.port === "8100" ? location.origin : location.origin + "/api");
  let snapshot = null;
  let loading = false;
  const esc = value => String(value == null ? "—" : value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const number = value => value == null ? "未统计" : Number(value).toLocaleString("zh-CN");
  function bytes(value) {
    if (value == null) return "未统计";
    let size = Number(value), unit = 0;
    while (size >= 1024 && unit < 4) { size /= 1024; unit += 1; }
    return size.toLocaleString("zh-CN", {maximumFractionDigits: unit ? 2 : 0}) + " " + ["B", "KiB", "MiB", "GiB", "TiB"][unit];
  }
  function timestamp(value) {
    if (!value) return "—";
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("zh-CN", {timeZone:"Asia/Shanghai", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false});
  }
  function renderRows() {
    if (!snapshot) return;
    const query = $("collectionSearch").value.trim().toLowerCase();
    const category = $("collectionCategory").value;
    const status = $("collectionStatus").value;
    const rows = snapshot.rows.filter(row => (!category || row.category === category) &&
      (!status || (status === "attention" ? row.needs_attention : row.status === status)) &&
      (!query || [row.name, row.source, row.category, row.location, row.note].join(" ").toLowerCase().includes(query)));
    $("collectionVisibleCount").textContent = "显示 " + rows.length + " / " + snapshot.rows.length + " 项";
    $("collectionRows").innerHTML = rows.length ? rows.map(row => {
      const count = number(row.count) + (row.count == null ? "" : " " + row.count_unit);
      const coverage = row.coverage_count == null ? "" : number(row.coverage_count) + " " + row.coverage_unit;
      const recent = '<span class="cell-date">' + esc(timestamp(row.last_ingested)) + '</span>' +
        (row.last_checked && row.status !== "REMOVED" ? '<small>检查 ' + esc(timestamp(row.last_checked)) + '</small>' : "");
      const range = row.coverage_start ? esc(timestamp(row.coverage_start)) + '<br><span class="cell-dim">至</span> ' + esc(timestamp(row.coverage_end)) :
        (row.coverage_end ? '<small>最近记录</small>' + esc(timestamp(row.coverage_end)) : "—");
      return '<tr data-row-id="' + esc(row.id) + '"><td><b>' + esc(row.name) + '</b><small>' + esc(row.category) + (row.source ? " · " + esc(row.source) : "") + '</small></td>' +
        '<td><span class="collection-status ' + esc(row.status) + '">' + esc(row.status_label) + '</span></td>' +
        '<td class="cell-number">' + esc(count) + (coverage ? '<small>' + esc(coverage) + '</small>' : "") + '</td>' +
        '<td class="cell-number">' + esc(bytes(row.size_bytes)) + (row.size_shared ? '<small>共享整表</small>' : "") + '</td>' +
        '<td class="cell-location">' + esc(row.location) + '</td><td class="cell-date">' + range + '</td><td>' + recent + '</td><td class="cell-note">' + esc(row.note) + '</td></tr>';
    }).join("") : '<tr><td colspan="8" class="empty">没有符合筛选条件的采集项目。</td></tr>';
  }
  function render(data) {
    const categories = ["原始数据", "行情快照", "高频服务库", "K 线历史", "基本面与参考", "新闻订阅", "社区", "统计检查"];
    const datasets = ["ashare_quotes", "global_quotes", "cn_index_quotes", "breadth", "sector_flow"];
    const rank = row => categories.indexOf(row.category) * 100 + (datasets.includes(row.id) ? datasets.indexOf(row.id) : 20);
    snapshot = {...data, rows: data.rows.slice().sort((a, b) => rank(a) - rank(b))};
    $("collectionAsOf").textContent = "快照 " + timestamp(data.as_of);
    $("collectionDatasetCount").textContent = number(data.summary.datasets);
    $("collectionUpdating").textContent = data.summary.updating + " 项近期持续更新";
    $("collectionRawSize").textContent = bytes(data.summary.raw_bytes);
    $("collectionDbSize").textContent = bytes(data.summary.database_bytes);
    $("collectionDbTables").textContent = data.summary.database_tables == null ? "本次存储统计未完成" : data.summary.database_tables + " 张表 · 数据与索引合计";
    $("collectionAttention").textContent = number(data.summary.attention);
    $("collectionAttention").style.color = data.summary.attention ? "var(--yellow)" : "var(--green)";
    const used = Number(data.disk.used_percent);
    $("collectionDiskLabel").textContent = "磁盘使用 " + used.toFixed(1) + "%";
    $("collectionDiskFree").textContent = "剩余 " + bytes(data.disk.free_bytes) + " / 总计 " + bytes(data.disk.total_bytes);
    $("collectionDiskBar").setAttribute("aria-valuenow", String(used));
    $("collectionDiskBar").classList.toggle("high", used >= 85);
    $("collectionDiskBar").querySelector("i").style.width = Math.min(100, Math.max(0, used)) + "%";
    $("collectionStateDot").style.background = data.summary.attention || used >= 90 ? "var(--yellow)" : "var(--green)";
    $("collectionPartial").hidden = !data.partial;
    const selected = $("collectionCategory").value;
    $("collectionCategory").innerHTML = '<option value="">全部类别</option>' + [...new Set(snapshot.rows.map(row => row.category))].map(category => '<option value="' + esc(category) + '">' + esc(category) + '</option>').join("");
    $("collectionCategory").value = selected;
    renderRows();
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    $("collectionRefresh").disabled = true;
    $("collectionRefresh").setAttribute("aria-busy", "true");
    $("collectionError").hidden = true;
    try {
      const response = await fetch(api + "/v1/collection/status", {headers:{Accept:"application/json"}, signal:AbortSignal.timeout(65000)});
      if (!response.ok) throw new Error("HTTP " + response.status);
      render(await response.json());
    } catch (error) {
      $("collectionError").textContent = "采集统计读取失败：" + (error.name === "TimeoutError" ? "请求超时，请稍后重试。" : error.message) + (snapshot ? " 当前保留上一次成功快照。" : "");
      $("collectionError").hidden = false;
      if (!snapshot) $("collectionRows").innerHTML = '<tr><td colspan="8" class="empty">暂时无法读取统计，请点击右上角刷新。</td></tr>';
    } finally {
      loading = false;
      $("collectionRefresh").disabled = false;
      $("collectionRefresh").setAttribute("aria-busy", "false");
    }
  }
  $("collectionSearch").addEventListener("input", renderRows);
  $("collectionCategory").addEventListener("change", renderRows);
  $("collectionStatus").addEventListener("change", renderRows);
  $("collectionRefresh").addEventListener("click", refresh);
  $("collectionReset").addEventListener("click", () => {
    ["collectionSearch", "collectionCategory", "collectionStatus"].forEach(id => { $(id).value = ""; }); renderRows();
  });
  function clock() { $("collectionClock").textContent = new Date().toLocaleTimeString("zh-CN", {timeZone:"Asia/Shanghai",hour12:false}); }
  clock(); setInterval(clock, 1000); setInterval(refresh, 180000); refresh();
})();
