/* ============================================================
   6StockPulse · 公共运行时(common.js)
   所有界面共享:API 端点表 / 工具函数 / 时钟 / 连接状态条 /
   防闪烁 DOM 写入 / WebSocket 实时通道。
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
    signals: '/api/signals'
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
