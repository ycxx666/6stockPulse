/* ============================================================
   6StockPulse · 公共运行时(common.js)
   所有界面共享:API 端点表 / 工具函数 / 时钟 / 连接状态条 /
   防闪烁 DOM 写入 / WebSocket 实时通道 / 新闻详情弹层。
   界面专属的渲染与业务逻辑放在各自页面的 <script> 内。
   约定:新页面新增全局函数请加前缀或挂到 window.NS 下,勿与公共名冲突。
   ============================================================ */

const CONFIG = {
  useApi: true,
  apiBase: '',
  wsUrl: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/realtime`,
  endpoints: {
    tickers: '/api/market/tickers',
    anomalies: '/api/anomalies',
    sectorFlow: '/api/sectors/flow',
    overview: '/api/dashboard/snapshot',
    intraday: '/api/market/index/000001/intraday',
    volume: '/api/market/volume-comparison',
    news: '/api/news',
    sentiment: '/api/sentiment/ashare',
    concepts: '/api/concepts/leaders',
    ladder: '/api/short-term/limit-up-ladder',
    heat: '/api/short-term/heat',
    signals: '/api/signals',
    hotspots: '/api/hotspots'
  }
};

// 涨跌配色类(A股:红涨绿跌)
function fmtColor(change) { return String(change == null ? '' : change).startsWith('+') ? 'red-text' : 'green-text'; }

// HTML 转义:所有 innerHTML 直插外部文本前必须调用(防 XSS)
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// sentiment_snapshot.factors 存 JSON 文本 → [[label, value], ...]
function parseFactors(f) {
  if (!f) return [];
  if (typeof f === 'string') { try { f = JSON.parse(f); } catch (e) { return []; } }
  if (typeof f !== 'object') return [];
  return Object.entries(f).map(([k, v]) => {
    let val = v;
    if (v && typeof v === 'object') val = (v.normalized != null) ? v.normalized : (v.score != null ? v.score : 50);
    return [k, Number(val) || 0];
  });
}

// 统一请求:兼容 {ok,data,meta} 包络与直接返回体
async function fetchJson(path) {
  const r = await fetch(CONFIG.apiBase + path, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const json = await r.json();
  return json.ok !== undefined ? json.data : json;
}

// 内容未变则不写 DOM(防 30s 轮询整块重绘造成闪烁/焦点丢失)
const _lastHtml = new WeakMap();
function setHtmlIfChanged(el, html) {
  if (!el) return;
  if (_lastHtml.get(el) === html) return;
  _lastHtml.set(el, html);
  el.innerHTML = html;
}
const _lastText = new WeakMap();
function setTextIfChanged(el, txt) {
  if (!el) return;
  if (_lastText.get(el) === txt) return;
  _lastText.set(el, txt);
  el.textContent = txt;
}

// 右下角连接状态条(每页含 #connDock/#connText 即生效)
function setConn(state, t) {
  const el = document.getElementById('connText');
  if (!el) return;
  const dock = document.getElementById('connDock');
  if (dock) dock.style.borderColor = state === 'error' ? 'rgba(255,102,119,.55)' : state === 'ok' ? 'rgba(72,227,154,.45)' : '';
  if (state === 'ok') {
    const now = t ? String(t).slice(11, 19) : new Date().toLocaleTimeString('zh-CN', { hour12: false });
    el.textContent = `已连接 · 更新于 ${now}`;
  } else if (state === 'error') {
    el.textContent = '连接中断 · 30s 后自动重试';
  } else {
    el.textContent = '连接中…';
  }
}

// 顶栏时钟(页面含 #clock / #marketTime 即生效,缺失则跳过)
function tickClock() {
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const c = document.getElementById('clock'); if (c) c.textContent = t;
  const m = document.getElementById('marketTime'); if (m) m.textContent = t;
  refreshFreshness();
}

// 数据新鲜度: 每 30s 拉取 /api/system/freshness, 写入 #freshTag(缺失则跳过)
// 全部新鲜 → “数据 HH:MM”；存在陈旧源 → 红字“数据陈旧 · 最新 HH:MM”
let _freshnessAt = 0;
async function refreshFreshness() {
  const el = document.getElementById('freshTag');
  if (!el) return;
  if (Date.now() - _freshnessAt < 30000) return;
  _freshnessAt = Date.now();
  try {
    const f = await fetchJson('/api/system/freshness');
    const srcs = (f && f.sources) || [];
    const times = srcs.map(s => s.last_time).filter(Boolean).sort();
    const newest = times.length ? String(times[times.length - 1]) : '';
    const hhmm = newest ? newest.slice(11, 16) : '--';
    if (f && f.any_stale) {
      el.className = 'pill red-text';
      setTextIfChanged(el, `⚠ 数据陈旧 · 最新 ${hhmm}`);
    } else {
      el.className = 'pill';
      setTextIfChanged(el, `数据 ${hhmm}`);
    }
  } catch (e) { /* 忽略: 接口不可用时保持原样 */ }
}

// WebSocket 实时通道:连接 /ws/realtime,按主题分派,断开自动重连(5s)
// handlers: { onSnapshot, onTicker, onBreadth, onMessage, onStatus('open'|'closed') }
function connectWS(handlers) {
  handlers = handlers || {};
  let ws = null, timer = null, closed = false;
  const schedule = () => { if (closed) return; clearTimeout(timer); timer = setTimeout(open, 5000); };
  const open = () => {
    if (closed) return;
    try { ws = new WebSocket(CONFIG.wsUrl); } catch (e) { schedule(); return; }
    ws.onopen = () => { if (handlers.onStatus) handlers.onStatus('open'); };
    ws.onmessage = ev => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (handlers.onMessage) handlers.onMessage(msg);
      const fn = msg.type === 'market.ticker.updated' ? handlers.onTicker
        : msg.type === 'market.breadth.updated' ? handlers.onBreadth
        : msg.type === 'snapshot' ? handlers.onSnapshot : null;
      if (fn) fn(msg.payload || [], msg);
    };
    ws.onclose = () => { if (handlers.onStatus) handlers.onStatus('closed'); schedule(); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  };
  open();
  return { close() { closed = true; clearTimeout(timer); try { ws && ws.close(); } catch (e) {} } };
}

// ── 通用导航页头(两页复用;active: 'global' | 'market') ──
function renderNav(active) {
  const items = [
    ['global', '全球热点', './global.html'],
    ['market', '全景市场', './index.html'],
  ];
  const links = items.map(([k, label, href]) =>
    `<a class="nav-btn ${active === k ? 'active' : ''}" href="${href}">${label}</a>`).join('');
  const disabled = ['实时监控', '概念图谱', '未来趋势', '全量数据', '消息中心']
    .map(t => `<button class="nav-btn" disabled title="暂未开放">${t}</button>`).join('');
  const host = document.getElementById('navHost');
  if (host) setHtmlIfChanged(host, links + disabled);
}

// ── 通用新闻详情弹层(按需创建;#newsModal 若页面已存在则复用) ──
function ensureNewsModal() {
  let m = document.getElementById('newsModal');
  if (m) return m;
  m = document.createElement('div');
  m.id = 'newsModal';
  m.style.cssText = 'position:fixed;inset:0;z-index:60;background:rgba(2,7,14,.72);display:none;align-items:center;justify-content:center;padding:40px;';
  m.innerHTML = '<div style="position:relative;max-width:720px;width:100%;max-height:78vh;overflow:auto;border-radius:12px;border:1px solid rgba(78,134,165,.3);background:linear-gradient(180deg,rgba(12,31,49,.98),rgba(6,20,34,.99));padding:22px 24px;box-shadow:0 24px 60px rgba(0,0,0,.5)">'
    + '<button id="newsModalClose" style="position:absolute;right:12px;top:10px;background:transparent;border:0;color:#7391a4;font-size:16px;cursor:pointer">✕</button>'
    + '<div id="newsModalBody"></div></div>';
  document.body.appendChild(m);
  m.addEventListener('click', e => { if (e.target === m) m.style.display = 'none'; });
  m.querySelector('#newsModalClose').addEventListener('click', () => { m.style.display = 'none'; });
  return m;
}

async function openNewsDetail(id) {
  if (!id) return;
  const m = ensureNewsModal();
  const body = m.querySelector('#newsModalBody');
  body.innerHTML = '<div style="color:#4d687a;text-align:center;padding:18px">加载中…</div>';
  m.style.display = 'flex';
  try {
    const n = await fetchJson('/api/news/' + encodeURIComponent(id));
    const t = (n && n.title) || '', src = (n && n.source) || '', pt = (n && n.publish_time) || '';
    const content = (n && (n.content || n.summary)) || '';
    body.innerHTML = `<h3 style="margin:0 0 8px;font-size:16px;color:#eaf4fb;line-height:1.5">${escapeHtml(t)}</h3>`
      + `<div style="color:#58788b;font-size:10px;margin-bottom:12px;font-family:var(--font-mono)">${escapeHtml(src)} · ${escapeHtml(fmtTimeText(pt, true))}</div>`
      + `<div style="color:#d0dfe7;font-size:12px;line-height:1.9;white-space:pre-wrap">${escapeHtml(String(content).slice(0, 4000))}</div>`;
  } catch (e) {
    body.innerHTML = '<div style="color:#4d687a;text-align:center;padding:18px">详情加载失败</div>';
  }
}

// ── 时间显示:把 API 的 UTC/带时区时间转成本地(当天 HH:MM,否则 MM-DD HH:MM) ──
function fmtTimeText(pt, forceDate) {
  if (!pt) return '';
  const s = String(pt);
  // 兼容 "2026-09-11T03:39:22+00:00"(ISO)与 "2026-09-11 11:39:22+08"(PG ::text)
  const iso = s.replace(' ', 'T').replace(/([+-]\d{2})$/, '$1:00');
  const d = new Date(iso);
  if (isNaN(d.getTime())) return s.slice(0, 16).replace('T', ' ');  // 回退:原样截取
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  const p2 = n => String(n).padStart(2, '0');
  const hm = `${p2(d.getHours())}:${p2(d.getMinutes())}`;
  return (forceDate || !sameDay) ? `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${hm}` : hm;
}
