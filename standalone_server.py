"""
standalone_server.py — 零依赖实时行情服务器
=============================================
无需 PostgreSQL。直接拉取东方财富/新浪真实数据，
内建异动检测，同时托管前端静态文件。

启动: python standalone_server.py
访问: http://localhost:8900
"""
from __future__ import annotations
import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("standalone")

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"

# ────────── 外部 API URL ──────────
# 东方财富: A 股行情 (按涨跌幅排序，取前 30)
EM_STOCKS_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f3&po=1&pz=30&pn=1&np=1&fltt=2&invt=2"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f12,f14,f2,f3,f4,f8,f10,f15,f16,f17,f18,f62,f184"
)
# 东方财富: 指数
EM_INDEX_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f3&po=1&pz=20&pn=1&np=1&fltt=2&invt=2"
    "&fs=b:1+t:2,m:0+t:3,m:0+t:4"
    "&fields=f12,f14,f2,f3,f4,f15,f16,f17,f18"
)
# 新浪: A 股指数
SINA_INDEX_URL = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006,s_sh000688"
# 新浪: 全球品种
SINA_GLOBAL_URL = (
    "https://hq.sinajs.cn/list="
    "gb_$dji,gb_$inx,gb_$ixic,gb_vix,"
    "rt_hkHSI,rt_hkN225,"
    "hf_CL,hf_GC,hf_SI,"
    "fx_seurcny,fx_susdcny,fx_susdjpy"
)
# 新浪: 国内商品期货 (上期所/大商所/郑商所主力合约 — nf_ 前缀, "0"=连续合约)
SINA_FUTURES_URL = (
    "https://hq.sinajs.cn/list="
    "nf_AU0,nf_AG0,nf_CU0,nf_AL0,nf_ZN0,nf_NI0,nf_SN0,"
    "nf_RB0,nf_HC0,nf_RU0,nf_BU0,nf_FU0,nf_SC0,"
    "nf_MA0,nf_TA0,nf_SR0,nf_CF0,nf_CY0,"
    "nf_Y0,nf_M0,nf_A0,nf_P0,nf_OI0,"
    "nf_I0,nf_J0,nf_JM0,nf_FG0,nf_SA0,nf_EG0,nf_PP0"
)

# 东方财富: 板块资金流向 (概念)
EM_SECTOR_CONCEPT = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f3&po=1&pz=50&pn=1&np=1&fltt=2&invt=2"
    "&fs=m:90+t:3&fields=f12,f14,f3,f62,f184,f8,f100"
)

# 东方财富: 板块资金流向 (行业)
EM_SECTOR_INDUSTRY = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f3&po=1&pz=50&pn=1&np=1&fltt=2&invt=2"
    "&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f8,f100"
)


# ────────── 数据缓存 ──────────
_cache: dict = {}
_cache_ts: dict[str, float] = {}
_cache_lock = asyncio.Lock()

CACHE_TTL = {"tickers": 5.0, "anomalies": 8.0, "sector_flow": 120.0}

# ────────── 期货 → A 股板块映射 ──────────
# 每个期货代码映射到 (品种中文名, 关联板块名, 分类标签)
FUTURES_PROFILE: dict[str, tuple[str, str, str]] = {
    # 上期所
    "nf_AU0": ("沪金",   "贵金属",     "贵金属"),
    "nf_AG0": ("沪银",   "贵金属",     "贵金属"),
    "nf_CU0": ("沪铜",   "有色金属",   "工业金属"),
    "nf_AL0": ("沪铝",   "有色金属",   "工业金属"),
    "nf_ZN0": ("沪锌",   "有色金属",   "工业金属"),
    "nf_NI0": ("沪镍",   "有色金属",   "工业金属"),
    "nf_SN0": ("沪锡",   "有色金属",   "工业金属"),
    "nf_RB0": ("螺纹钢", "钢铁",       "黑色系"),
    "nf_HC0": ("热卷",   "钢铁",       "黑色系"),
    "nf_RU0": ("橡胶",   "橡胶制品",   "化工"),
    "nf_BU0": ("沥青",   "石油石化",   "能源"),
    "nf_FU0": ("燃油",   "石油石化",   "能源"),
    "nf_SC0": ("原油",   "石油石化",   "能源"),
    # 大商所
    "nf_I0":  ("铁矿石", "钢铁",       "黑色系"),
    "nf_J0":  ("焦炭",   "煤炭",       "黑色系"),
    "nf_JM0": ("焦煤",   "煤炭",       "黑色系"),
    "nf_FG0": ("玻璃",   "建材",       "建材"),
    "nf_Y0":  ("豆油",   "农产品加工", "农业"),
    "nf_M0":  ("豆粕",   "农产品加工", "农业"),
    "nf_A0":  ("豆一",   "农产品加工", "农业"),
    "nf_P0":  ("棕榈油", "农产品加工", "农业"),
    "nf_OI0": ("菜油",   "农产品加工", "农业"),
    "nf_EG0": ("乙二醇", "化工",       "化工"),
    "nf_PP0": ("聚丙烯", "化工",       "化工"),
    # 郑商所
    "nf_MA0": ("甲醇",   "化工",       "化工"),
    "nf_TA0": ("PTA",    "化工",       "化工"),
    "nf_SR0": ("白糖",   "农产品加工", "农业"),
    "nf_CF0": ("棉花",   "纺织服装",   "农业"),
    "nf_CY0": ("棉纱",   "纺织服装",   "农业"),
    "nf_SA0": ("纯碱",   "化工",       "化工"),
}

# 3 分钟涨速缓存: {code: (price_3min_ago, ts)}
_futures_speed_cache: dict[str, tuple[float, float]] = {}
SPEED_WINDOW = 180  # 3 分钟
SPEED_INTERVAL = 30  # 每 30s 采样一次
# 涨速滚动基线: {code: [speed_3m, ...]} 最近 10 次
_speed_baseline: dict[str, list[float]] = {}
BASELINE_LEN = 10  # 基线样本数
BASELINE_MIN = 0.1  # 最小基线值，避免除以接近 0


async def cached(key: str, ttl: float, fetcher):
    """带 TTL 的异步缓存"""
    now = time.monotonic()
    if key in _cache and (now - _cache_ts.get(key, 0)) < ttl:
        return _cache[key]
    async with _cache_lock:
        now2 = time.monotonic()
        if key in _cache and (now2 - _cache_ts.get(key, 0)) < ttl:
            return _cache[key]
        data = await fetcher()
        _cache[key] = data
        _cache_ts[key] = time.monotonic()
        return data


# ────────── 数据获取 ──────────
async def _fetch_json(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        headers = {"Referer": "https://quote.eastmoney.com/"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            text = await resp.text()
            return json.loads(text)
    except Exception as e:
        logger.warning("fetch failed: %s — %s", url[:80], e)
        return None


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        headers = {"Referer": "https://finance.sina.com.cn/"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return await resp.text()
    except Exception as e:
        logger.warning("sina fetch failed: %s", e)
        return None


# ────────── 核心 A 股股票池 ──────────
_WATCHLIST = [
    # 沪市 (sh)
    ("sh600519", "贵州茅台"), ("sh601318", "中国平安"), ("sh600036", "招商银行"),
    ("sh600900", "长江电力"), ("sh601166", "兴业银行"), ("sh600030", "中信证券"),
    ("sh600276", "恒瑞医药"), ("sh601398", "工商银行"), ("sh600887", "伊利股份"),
    ("sh601857", "中国石油"), ("sh601012", "隆基绿能"), ("sh600809", "山西汾酒"),
    ("sh603259", "药明康德"), ("sh688981", "中芯国际"), ("sh601899", "紫金矿业"),
    ("sh601088", "中国神华"), ("sh600585", "海螺水泥"), ("sh601628", "中国人寿"),
    # 深市 (sz)
    ("sz000858", "五粮液"), ("sz000333", "美的集团"), ("sz002594", "比亚迪"),
    ("sz300750", "宁德时代"), ("sz000001", "平安银行"), ("sz002415", "海康威视"),
    ("sz300059", "东方财富"), ("sz000651", "格力电器"), ("sz002230", "科大讯飞"),
    ("sz300124", "汇川技术"), ("sz000568", "泸州老窖"), ("sz002475", "立讯精密"),
    ("sz300274", "阳光电源"), ("sz000725", "京东方A"), ("sz002714", "牧原股份"),
]
_WATCHLIST_URL = "https://hq.sinajs.cn/list=" + ",".join(c for c, _ in _WATCHLIST)

# 股票 → 板块映射（用于板块资金聚合）
_STOCK_SECTOR: dict[str, str] = {
    "贵州茅台": "白酒", "五粮液": "白酒", "山西汾酒": "白酒", "泸州老窖": "白酒",
    "宁德时代": "新能源", "比亚迪": "新能源", "隆基绿能": "新能源", "阳光电源": "新能源", "汇川技术": "新能源",
    "中芯国际": "半导体", "海康威视": "半导体", "立讯精密": "消费电子", "京东方A": "面板",
    "东方财富": "券商", "中信证券": "券商",
    "招商银行": "银行", "兴业银行": "银行", "平安银行": "银行", "工商银行": "银行",
    "中国平安": "保险", "中国人寿": "保险",
    "恒瑞医药": "医药", "药明康德": "医药",
    "中国石油": "石油石化", "中国神华": "煤炭",
    "美的集团": "家电", "格力电器": "家电",
    "伊利股份": "食品饮料", "牧原股份": "养殖",
    "科大讯飞": "人工智能", "紫金矿业": "有色金属",
    "长江电力": "电力", "海螺水泥": "建材", "中国石油": "石油石化",
    "京东方A": "面板", "立讯精密": "消费电子",
}


def _parse_sina_stock(body: str) -> dict | None:
    """解析新浪个股行情: 名称,今开,昨收,当前价,最高,最低,日期,时间,成交量,成交额,..."""
    parts = body.split(",")
    if len(parts) < 9:
        return None
    try:
        name = parts[0]
        open_p = float(parts[1]) if parts[1] else 0
        prev_close = float(parts[2]) if parts[2] else 0
        price = float(parts[3]) if parts[3] else 0
        high = float(parts[4]) if parts[4] else 0
        low = float(parts[5]) if parts[5] else 0
        volume = int(float(parts[8]) if len(parts) > 8 and parts[8] else 0)
        amount = float(parts[9]) if len(parts) > 9 and parts[9] else 0
        change = price - prev_close if prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0
        return {
            "name": name, "price": price, "prev_close": prev_close,
            "open": open_p, "high": high, "low": low,
            "volume": volume, "amount": amount,
            "change": round(change, 2), "change_pct": round(change_pct, 2),
        }
    except (ValueError, IndexError):
        return None


def _parse_sina_global(parts: list, default_pct_idx: int = 2) -> dict | None:
    """解析新浪全球品种"""
    try:
        price = float(parts[1]) if len(parts) > 1 and parts[1] else 0
        pct_str = (parts[default_pct_idx] if len(parts) > default_pct_idx else "0") or "0"
        change_pct = float(pct_str.replace("%", "")) if pct_str else 0
        prev = price / (1 + change_pct / 100) if change_pct != -100 and change_pct else price
        change = price - prev
        return {
            "price": round(price, 2), "prev_close": round(prev, 2),
            "change": round(change, 2), "change_pct": round(change_pct, 2),
        }
    except (ValueError, IndexError):
        return None


# ────────── Tickers API ──────────
async def fetch_tickers() -> list[dict]:
    """拉取 A 股指数 + 全球品种 + 热门股票"""
    results = []
    async with aiohttp.ClientSession() as session:
        # 1) 新浪 A 股指数 (格式: 名称,当前价,涨跌额,涨跌幅,成交量,成交额)
        sina_text = await _fetch_text(session, SINA_INDEX_URL)
        if sina_text:
            index_map = {
                "s_sh000001": ("000001.SH", "上证指数", "SH", "index"),
                "s_sz399001": ("399001.SZ", "深证成指", "SZ", "index"),
                "s_sz399006": ("399006.SZ", "创业板指", "SZ", "index"),
                "s_sh000688": ("000688.SH", "科创50", "SH", "index"),
            }
            for line in sina_text.splitlines():
                m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
                if not m:
                    continue
                code, body = m.group(1), m.group(2)
                if code not in index_map:
                    continue
                parts = body.split(",")
                if len(parts) < 4:
                    continue
                try:
                    price = float(parts[1]) if parts[1] else 0       # 当前点数
                    chg = float(parts[2]) if parts[2] else 0          # 涨跌额
                    pct = float(parts[3]) if parts[3] else 0          # 涨跌幅%
                    prev = price - chg if chg else price
                    sym, name, mkt, atype = index_map[code]
                    results.append({
                        "symbol": sym, "name": name, "asset_type": atype,
                        "market": mkt, "price": round(price, 2),
                        "prev_close": round(prev, 2), "change": round(chg, 2),
                        "change_pct": round(pct, 2),
                        "source": "sina", "is_stale": False,
                    })
                except (ValueError, IndexError):
                    continue

        # 2) 新浪全球品种 (不同品类格式不同)
        global_text = await _fetch_text(session, SINA_GLOBAL_URL)
        if global_text:
            for line in global_text.splitlines():
                m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
                if not m:
                    continue
                code, body = m.group(1), m.group(2)
                if not body or body == '""':  # 美股休市时返回空引号
                    continue
                parts = body.split(",")
                try:
                    if code.startswith("hf_"):  # 商品期货: 当前价,,结算价,昨收,最高,最低,时间,...
                        sym_map = {"hf_CL": ("CL", "WTI原油", "COMM", "commodity"),
                                   "hf_GC": ("GC", "黄金", "COMM", "commodity"),
                                   "hf_SI": ("SI", "白银", "COMM", "commodity")}
                        if code not in sym_map:
                            continue
                        price = float(parts[0]) if parts[0] else 0
                        prev = float(parts[3]) if len(parts) > 3 and parts[3] else price
                        chg = price - prev
                        pct = (chg / prev * 100) if prev else 0
                    elif code.startswith("fx_"):  # 外汇: 时间,当前价,昨收,...
                        sym_map = {"fx_seurcny": ("EURCNY", "欧元/人民币", "FX", "forex"),
                                   "fx_susdcny": ("USDCNY", "美元/离岸人民币", "FX", "forex"),
                                   "fx_susdjpy": ("USDJPY", "美元/日元", "FX", "forex")}
                        if code not in sym_map:
                            continue
                        price = float(parts[1]) if len(parts) > 1 and parts[1] else 0
                        prev = float(parts[2]) if len(parts) > 2 and parts[2] else price
                        chg = price - prev
                        pct = (chg / prev * 100) if prev else 0
                    elif code.startswith("rt_"):  # 港股/日股: 英文名,中文名,当前价,昨收,最高,最低,...
                        sym_map = {"rt_hkHSI": ("HSI", "恒生指数", "HK", "index"),
                                   "rt_hkN225": ("N225", "日经225", "JP", "index")}
                        if code not in sym_map:
                            continue
                        # rt_ 格式: 英文名,中文名,当前价,昨收?,最高,最低,?,涨跌额,涨跌幅,...
                        price = float(parts[2]) if len(parts) > 2 and parts[2] else 0
                        chg = float(parts[7]) if len(parts) > 7 and parts[7] else 0
                        pct = float(parts[8]) if len(parts) > 8 and parts[8] else 0
                        prev = price - chg if chg else price
                    elif code.startswith("gb_"):  # 美股指数: 当前价,涨跌额,涨跌幅%,...
                        sym_map = {"gb_$dji": ("DJI", "道琼斯", "US", "index"),
                                   "gb_$inx": ("INX", "标普500", "US", "index"),
                                   "gb_$ixic": ("IXIC", "纳斯达克", "US", "index"),
                                   "gb_vix": ("VIX", "VIX恐慌指数", "US", "index")}
                        if code not in sym_map:
                            continue
                        price = float(parts[0]) if parts[0] else 0
                        pct = float(parts[2]) if len(parts) > 2 and parts[2] else 0
                        prev = price / (1 + pct / 100) if pct != -100 and pct else price
                        chg = price - prev
                    else:
                        continue

                    sym, name, mkt, atype = sym_map[code]
                    results.append({
                        "symbol": sym, "name": name, "asset_type": atype,
                        "market": mkt, "price": round(price, 2),
                        "prev_close": round(prev, 2) if prev else None,
                        "change": round(chg, 2),
                        "change_pct": round(pct, 2),
                        "source": "sina", "is_stale": False,
                    })
                except (ValueError, IndexError):
                    continue

        # 3) 新浪核心股票 (取涨跌幅最大的 8 只)
        stock_text = await _fetch_text(session, _WATCHLIST_URL)
        if stock_text:
            stock_data = []
            code_map = {c: n for c, n in _WATCHLIST}
            for line in stock_text.splitlines():
                m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
                if not m:
                    continue
                code = m.group(1)
                parsed = _parse_sina_stock(m.group(2))
                if not parsed or code not in code_map:
                    continue
                sym = code[2:]  # sh600519 → 600519
                mkt = "SH" if code.startswith("sh") else "SZ"
                stock_data.append({
                    "symbol": sym, "name": code_map[code],
                    "asset_type": "stock", "market": mkt,
                    "price": parsed["price"],
                    "prev_close": parsed["prev_close"],
                    "change": parsed["change"],
                    "change_pct": parsed["change_pct"],
                    "source": "sina", "is_stale": False,
                })
            # 取涨跌幅绝对值最大的 8 只
            stock_data.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
            results.extend(stock_data[:8])

    # 去重
    seen = set()
    deduped = []
    for r in results:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            deduped.append(r)
    return deduped


# ────────── 期货行情获取 ──────────
async def _fetch_futures_prices(session: aiohttp.ClientSession) -> list[dict]:
    """拉取国内期货主力合约行情，附带 3 分钟涨速计算"""
    text = await _fetch_text(session, SINA_FUTURES_URL)
    if not text:
        return []

    now_ts = time.time()
    futures = []

    for line in text.splitlines():
        m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
        if not m:
            continue
        code, body = m.group(1), m.group(2)
        profile = FUTURES_PROFILE.get(code)
        if not profile or not body:
            continue

        parts = body.split(",")
        try:
            # nf_ 格式: 名称,时间,今开,最高,最低,_,买价,卖价,当前价,_,昨结算,...
            name_raw = parts[0].strip() if parts[0] else ""
            price = float(parts[8]) if len(parts) > 8 and parts[8] else 0
            prev_settle = float(parts[10]) if len(parts) > 10 and parts[10] else price
            high = float(parts[3]) if len(parts) > 3 and parts[3] else 0
            low = float(parts[4]) if len(parts) > 4 and parts[4] else 0
        except (ValueError, IndexError):
            continue
        if price <= 0 or prev_settle <= 0:
            continue

        name, sector, tag = profile
        chg = price - prev_settle
        pct = (chg / prev_settle * 100)

        # 3 分钟涨速 + 滚动基线
        speed_3m = 0.0
        speed_multiplier = 0
        prev_entry = _futures_speed_cache.get(code)
        if prev_entry:
            prev_price, prev_ts = prev_entry
            if now_ts - prev_ts >= 30:  # 每 30 秒更新
                if prev_price > 0:
                    speed_3m = (price - prev_price) / prev_price * 100
                _futures_speed_cache[code] = (price, now_ts)
        else:
            _futures_speed_cache[code] = (price, now_ts)

        # 更新涨速基线
        if abs(speed_3m) > 0.001:
            bl = _speed_baseline.setdefault(code, [])
            bl.append(speed_3m)
            if len(bl) > BASELINE_LEN:
                bl.pop(0)

        futures.append({
            "code": code,
            "name": name,
            "sector": sector,
            "tag": tag,
            "price": round(price, 2),
            "prev_close": round(prev_settle, 2),
            "high": high,
            "low": low,
            "change": round(chg, 2),
            "change_pct": round(pct, 2),
            "speed_3m": round(speed_3m, 2),
        })

    return futures


# ────────── Anomalies API ──────────
async def fetch_anomalies() -> list[dict]:
    """融合期货异动 + 个股异动 + 3 分钟涨速，按异常分降序排列"""
    anomalies: list[dict] = []
    async with aiohttp.ClientSession() as session:
        now = datetime.now()

        # ── 1) 期货异动 ──
        futures = await _fetch_futures_prices(session)
        for f in futures:
            pct = f["change_pct"]
            speed = f["speed_3m"]
            if abs(pct) < 0.5 and abs(speed) < 0.5:
                continue  # 过滤微幅波动

            direction = "up" if pct > 0 else "down"
            anomaly_score = round(abs(pct) * 10 + abs(speed) * 20, 1)
            anomaly_score = min(100, anomaly_score)

            # 标签判定: 涨速倍数 > 阈值突破
            event_type: str
            speed_mult = 0
            bl = _speed_baseline.get(f["code"], [])
            if bl and abs(speed) >= 0.5:
                avg_baseline = sum(abs(v) for v in bl) / len(bl)
                baseline = max(avg_baseline, BASELINE_MIN)
                speed_mult = round(abs(speed) / baseline)
                if speed_mult >= 2:
                    event_type = f"涨速 ×{speed_mult}"
                elif abs(pct) >= 5:
                    event_type = "破5%"
                elif abs(pct) >= 3:
                    event_type = "破3%"
                elif abs(pct) >= 1:
                    event_type = "异动"
                else:
                    event_type = "波动"
            elif abs(pct) >= 5:
                event_type = "破5%"
            elif abs(pct) >= 3:
                event_type = "破3%"
            elif abs(pct) >= 1:
                event_type = "异动"
            else:
                event_type = "波动"

            anomalies.append({
                "id": None,
                "symbol": f["code"].replace("nf_", "").rstrip("0"),
                "stock_name": f["name"],
                "sector_name": f["sector"],
                "event_type": event_type,
                "direction": direction,
                "window_seconds": 180,
                "change_pct": round(pct, 2),
                "speed_3m": round(speed, 2),
                "speed_multiplier": speed_mult,
                "volume_ratio": 0,
                "z_score": round(pct / 1.5, 2) if pct else 0,
                "anomaly_score": anomaly_score,
                "confidence": round(min(1.0, anomaly_score / 80), 2),
                "detected_at": now.isoformat(timespec="seconds"),
            })

        # ── 2) 个股异动 ──
        stock_text = await _fetch_text(session, _WATCHLIST_URL)
        if stock_text:
            code_map = {c: n for c, n in _WATCHLIST}
            stock_rows = []
            for line in stock_text.splitlines():
                m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
                if not m or m.group(1) not in code_map:
                    continue
                parsed = _parse_sina_stock(m.group(2))
                if not parsed:
                    continue
                sym = m.group(1)[2:]
                stock_rows.append({
                    "symbol": sym,
                    "stock_name": code_map[m.group(1)],
                    "change_pct": parsed["change_pct"],
                    "price": parsed["price"],
                    "volume": parsed["volume"],
                    "high": parsed["high"],
                    "low": parsed["low"],
                    "prev_close": parsed["prev_close"],
                })

            stock_rows.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
            for r in stock_rows[:10]:
                pct = r["change_pct"]
                if abs(pct) < 0.3:
                    continue
                direction = "up" if pct > 0 else "down"
                amplitude = (
                    abs(r["high"] - r["low"]) / max(r["prev_close"], 0.01) * 100
                    if r["prev_close"] and r["high"] and r["low"] else 0
                )
                anomaly_score = min(100, round(abs(pct) * 8 + amplitude * 2, 1))
                if anomaly_score < 30:
                    continue

                event_type = "surge" if pct > 0 else "drop" if abs(pct) >= 2 else "volatility_spike"

                anomalies.append({
                    "id": None,
                    "symbol": r["symbol"],
                    "stock_name": r["stock_name"],
                    "sector_name": event_type,
                    "event_type": event_type,
                    "direction": direction,
                    "window_seconds": 60,
                    "change_pct": round(pct, 2),
                    "speed_3m": 0.0,
                    "volume_ratio": round(r["volume"] / 1000000, 2) if r["volume"] else 0,
                    "z_score": round(pct / 1.5, 2) if pct else 0,
                    "anomaly_score": anomaly_score,
                    "confidence": round(min(1.0, anomaly_score / 80), 2),
                    "detected_at": now.isoformat(timespec="seconds"),
                })

    # 按 anomaly_score 降序
    anomalies.sort(key=lambda x: x["anomaly_score"], reverse=True)
    return anomalies[:15]


# ────────── 板块资金流向 ──────────
async def fetch_sector_flow() -> list[dict]:
    """从 Sina 个股数据按板块聚合资金流向"""
    async with aiohttp.ClientSession() as session:
        text = await _fetch_text(session, _WATCHLIST_URL)
        if not text:
            return []
        # 按板块聚合
        sector_data: dict[str, dict] = {}
        code_map = {c: n for c, n in _WATCHLIST}
        for line in text.splitlines():
            m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
            if not m or m.group(1) not in code_map:
                continue
            name = code_map[m.group(1)]
            sector = _STOCK_SECTOR.get(name)
            if not sector:
                continue
            parts = m.group(2).split(",")
            try:
                price = float(parts[3]) if len(parts) > 3 and parts[3] else 0  # 当前价
                prev_close = float(parts[2]) if len(parts) > 2 and parts[2] else price
                volume = float(parts[8]) if len(parts) > 8 and parts[8] else 0  # 成交量(手)
                amount = float(parts[9]) if len(parts) > 9 and parts[9] else 0  # 成交额
            except (ValueError, IndexError):
                continue
            pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
            if sector not in sector_data:
                sector_data[sector] = {"count": 0, "total_amount": 0, "pct_sum": 0, "stocks": []}
            sd = sector_data[sector]
            sd["count"] += 1
            sd["total_amount"] += amount
            sd["pct_sum"] += pct
            sd["stocks"].append((name, pct))

        results = []
        for sector, sd in sector_data.items():
            avg_pct = sd["pct_sum"] / max(sd["count"], 1)
            # 取涨跌幅最大的领涨股
            leader = max(sd["stocks"], key=lambda x: abs(x[1]))[0] if sd["stocks"] else ""
            results.append({
                "sector_name": sector,
                "main_net_inflow": round(sd["total_amount"]),
                "change_pct": round(avg_pct, 2),
                "leader_name": leader,
                "bar_pct": 0,
                "category": "concept",
            })
        # 按成交额降序
        results.sort(key=lambda x: x["main_net_inflow"], reverse=True)
        # 进度条 = 相对最大成交额的百分比
        max_amt = max((r["main_net_inflow"] for r in results), default=1)
        for r in results:
            r["bar_pct"] = round(r["main_net_inflow"] / max_amt * 100)
        return results


# ────────── 异动 AI 分析（规则引擎） ──────────
def generate_analysis(name: str, sector: str, tag: str, direction: str, change_pct: float, speed_3m: float) -> str:
    """根据异动参数生成财经分析文本"""
    parts = []
    arrow = "↑" if direction == "up" else "↓"
    abs_pct = abs(change_pct)

    # 1) 异动特征
    if (tag or "").startswith("涨速"):
        parts.append(f"{name}在短期内出现异常资金涌入，3分钟涨速达{speed_3m:.2f}%，超出历史均值水平，短线博弈情绪显著升温。")
    elif abs_pct >= 5:
        parts.append(f"{name}单日涨跌幅突破5%阈值，{arrow}{abs_pct:.2f}%，已进入超常波动区间，触发{tag}预警。")
    elif abs_pct >= 3:
        parts.append(f"{name}涨跌幅达{arrow}{abs_pct:.2f}%，突破3%关键阈值，行情异动特征明显。")
    else:
        parts.append(f"{name}出现{tag}信号，涨跌幅{arrow}{abs_pct:.2f}%，短期波动加剧。")

    # 2) 可能原因（基于板块推断）
    sector_reasons = {
        "贵金属": "避险情绪升温叠加国际金价波动，贵金属板块联动效应显著。",
        "石油石化": "国际原油价格持续走高，叠加OPEC+供给端收缩预期，推动国内石油石化板块跟涨。",
        "有色金属": "全球工业需求复苏叠加新能源产业链拉货，有色金属品种价格共振上行。",
        "钢铁": "地产政策边际放松叠加基建投资预期，黑色系品种获得资金青睐。",
        "黑色系": "钢厂复产补库叠加铁矿石发运量偏低，黑色系期货联动A股板块走强。",
        "煤炭": "季节性用煤高峰叠加供给端检修，煤炭板块情绪回暖。",
        "化工": "原油成本端抬升传导至化工品，加之下游补库周期启动，化工板块异动。",
        "能源": "全球能源供需格局变化，能源类品种获得配置型资金增持。",
        "农产品加工": "天气因素叠加USDA报告利好，农产品价格走高带动加工板块。",
        "农业": "极端天气预期叠加全球粮食安全关注度提升，农业板块获资金关注。",
        "建材": "地产竣工链回暖预期叠加原材料价格上行，建材板块出现联动。",
        "纺织服装": "海外订单回流叠加原料价格上涨，纺织板块异动传导。",
    }
    reason = sector_reasons.get(sector, f"{sector}板块受资金轮动及市场情绪推动，出现期股联动异动。")
    parts.append(reason)

    # 3) 关联影响
    parts.append(
        "关联A股板块或有跟涨/跟跌可能，建议关注该板块内弹性标的的短线机会，同时警惕冲高回落风险。"
        if direction == "up" else
        "关联A股板块或承压，建议控制相关仓位，关注后续是否有企稳迹象。"
    )

    return "".join(parts)


# ────────── HTTP Server ──────────
class StandaloneHandler(SimpleHTTPRequestHandler):
    """同时托管静态文件 + API 端点"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # WebSocket: 返回 200 让前端不报错（独立模式无 WS）
        if path == "/ws/realtime":
            return self._serve_json({"type": "standalone", "note": "WebSocket not available in standalone mode"})

        # API 路由
        if path == "/api/anomalies":
            return self._serve_api("anomalies", CACHE_TTL["anomalies"], fetch_anomalies)
        if path.startswith("/api/sector-flow"):
            return self._serve_api("sector_flow", CACHE_TTL["sector_flow"], fetch_sector_flow)
        if path.startswith("/api/anomaly/analyze"):
            return self._serve_analyze()
        if path == "/api/market/tickers":
            return self._serve_api("tickers", CACHE_TTL["tickers"], fetch_tickers)
        if path == "/health":
            return self._serve_json({"ok": True, "db": "standalone", "realtime": {"running": True}})

        # 静态文件
        if path == "/" or path == "":
            self.path = "/index.html"
        elif path.startswith("/frontend/"):
            self.path = path[len("/frontend/"):]
        return super().do_GET()

    def _serve_api(self, key, ttl, fetcher):
        try:
            loop = asyncio.new_event_loop()
            data = loop.run_until_complete(cached(key, ttl, fetcher))
            loop.close()
            self._serve_json({"ok": True, "data": data, "meta": {"source": key, "ts": datetime.now().isoformat()}})
        except Exception as e:
            logger.exception("API %s error", key)
            self._serve_json({"ok": False, "error": str(e)}, code=500)

    def _serve_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_analyze(self):
        """rule-based analysis endpoint"""
        qs = parse_qs(urlparse(self.path).query)
        name = qs.get("name", [""])[0]
        sector = qs.get("sector", [""])[0]
        tag = qs.get("tag", [""])[0]
        direction = qs.get("direction", ["up"])[0]
        try:
            change_pct = float(qs.get("change_pct", ["0"])[0])
            speed_3m = float(qs.get("speed_3m", ["0"])[0])
        except ValueError:
            change_pct = 0.0
            speed_3m = 0.0
        try:
            import time as _time
            _time.sleep(1.5)  # 模拟 AI 延迟
            text = generate_analysis(name, sector, tag, direction, change_pct, speed_3m)
            self._serve_json({
                "ok": True,
                "analysis_text": text,
                "disclaimer": "AI生成内容仅供参考，不构成投资建议",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            })
        except Exception as e:
            self._serve_json({"ok": False, "error": str(e)}, code=500)

    def log_message(self, fmt, *args):
        # 抑制静态文件日志，只显示 API 调用
        msg = fmt % args if args else fmt
        if "/api/" in str(msg):
            logger.info("API %s", msg)


def main():
    port = 8900
    # 预热缓存
    import asyncio as _asyncio
    try:
        loop = _asyncio.new_event_loop()
        loop.run_until_complete(cached("sector_flow", CACHE_TTL["sector_flow"], fetch_sector_flow))
        loop.close()
        logger.info("板块资金流向缓存已预热 (%d 条)", len(_cache.get("sector_flow", [])))
    except Exception:
        logger.warning("板块预热失败，首次请求时将重试")
    server = HTTPServer(("0.0.0.0", port), StandaloneHandler)
    logger.info("=" * 50)
    logger.info("6stockPulse 独立服务器已启动")
    logger.info("访问: http://localhost:%d", port)
    logger.info("API 端点:")
    logger.info("  GET /api/market/tickers  — 实时行情滚动带")
    logger.info("  GET /api/anomalies       — 异动监测")
    logger.info("=" * 50)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务器已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
