"""
6StockPulse · RSS 源自动修复与补全工具
=======================================
处理"不完整"的新闻源信息，输出可用的 RSS 链接列表。
优先级：URL修复 → RSS自动发现 → Google News 备用 → 失败报告

典型输入：
  - 只有域名:        "reuters.com"
  - 破损链接:        "www.cnbc.com/id/10000664/device/rss/rss."
  - 缺协议头:        "feeds.bbci.co.uk/news/business/rss.xml"
  - 主页(需发现):    "https://www.economist.com/finance-and-economics/"
  - Google 截断:     "news.google.com/rss/search?q=site%3Amark"

功能:
  1. fix_rss_url()             — 修复常见 URL 格式错误
  2. discover_rss()            — 解析主页 <link> 自动发现 RSS
  3. enhance_full_text()       — summary <200字 时抓原文补全
  4. google_news_fallback()    — 生成 Google News site: 搜索 RSS
  5. complete_rss_source()     — 统一调度(修复→发现→Google备用)

依赖:
  pip install requests beautifulsoup4 lxml
  (newspaper3k 可选, 用于更强全文提取; 未装时用 BeautifulSoup 兜底)

与现有流程集成 (fetch_all):
  from scripts.rss_source_fixer import complete_rss_source, enhance_full_text
  for raw in RSS_SOURCES:
      urls = complete_rss_source(raw)          # 得到候选 RSS 列表
      for u in urls:                           # 逐一尝试直到成功
          items = fetch_feed(u)                # 你的抓取器
          if items:
              enhance_full_text(items)         # 补全全文后入库
              break
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse, urljoin, quote
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
TIMEOUT = 12            # 单次请求超时(秒)
REQUEST_INTERVAL = 1.0  # 请求间隔(秒), 避免触发反爬
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 6StockPulse/1.0"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/rss+xml,application/atom+xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

try:
    import newspaper  # 可选增强
    HAS_NEWSPAPER = True
except ImportError:
    HAS_NEWSPAPER = False

_last_request_at = 0.0  # 请求节流


# ── 通用工具 ──────────────────────────────────────────

def _throttle():
    """简单请求节流：保证相邻请求间隔 >= REQUEST_INTERVAL"""
    global _last_request_at
    gap = REQUEST_INTERVAL - (time.time() - _last_request_at)
    if gap > 0:
        time.sleep(gap)
    _last_request_at = time.time()


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _safe_get(url: str, timeout: int = TIMEOUT, retries: int = 1) -> requests.Response | None:
    """带节流/超时/有限重试的 GET，失败返回 None。
    超时类错误不再重试（网络黑洞重试无意义），仅连接错误重试 retries 次。
    """
    for attempt in range(retries + 1):
        _throttle()
        try:
            return _session().get(url, timeout=timeout, allow_redirects=True)
        except requests.Timeout:
            logger.debug("超时 %s", url)
            break  # 超时直接放弃
        except requests.ConnectionError as e:
            logger.debug("连接失败 %s: %s (attempt %d)", url, e, attempt + 1)
        except requests.RequestException as e:
            logger.debug("请求失败 %s: %s", url, e)
        if attempt < retries:
            time.sleep(0.5)
    return None


# ── 1. URL 自动修复 ───────────────────────────────────

def fix_rss_url(url: str) -> str | None:
    """
    修复常见 RSS URL 格式错误：
      - 补全 http(s):// 协议头
      - 去掉末尾多余点号 (rss. → rss.xml)
      - 站点名截断为裸域名时返回 None (交给 discover 处理)
    """
    if not url or not url.strip():
        return None
    u = url.strip()

    # 1) 去掉常见空白/引号包裹
    u = u.strip("\"'<> \t\n")

    # 2) 若没有协议头则补 https://
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u

    parsed = urlparse(u)
    host = parsed.netloc or parsed.path.split("/")[0]

    # 3) 纯域名/主页 → 交给自动发现
    if not parsed.path or parsed.path in ("/", ""):
        return None

    # 4) 去掉末尾多余点号(如 rss. / rss2.0.)
    #    www.cnbc.com/.../rss.  →  www.cnbc.com/.../rss  (后缀试探交给变体)
    u = re.sub(r"\.+$", "", u)

    # 5) 简单校验 host 非空
    if not host:
        return None
    return u


def _extract_domain(url: str) -> str:
    """从任意输入中提取裸域名 (reuters.com / www.cnbc.com)"""
    u = url.strip().strip("\"'")
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    parsed = urlparse(u)
    host = parsed.netloc or (parsed.path.split("/")[0] if parsed.path else "")
    return host.lower()


# ── 2. RSS 自动发现 ───────────────────────────────────

def discover_rss(url: str, timeout: int = TIMEOUT) -> list[str]:
    """
    给定网站主页/频道页，解析 HTML <link rel=alternate>
    返回发现的 application/rss+xml / application/atom+xml 地址列表。
    """
    resp = _safe_get(url, timeout=timeout)
    if resp is None:
        return []
    if resp.status_code >= 400:
        logger.info("主页 %s HTTP %s", url, resp.status_code)
        return []
    ctype = resp.headers.get("Content-Type", "")
    # 本身就是 XML feed
    if "xml" in ctype or "rss" in ctype or resp.text.lstrip().startswith(("<?xml", "<rss", "<feed")):
        return [resp.url or url]

    feeds: list[str] = []
    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        return []
    for link in soup.find_all("link", rel="alternate"):
        typ = (link.get("type") or "").lower()
        href = link.get("href") or ""
        if "rss" in typ or "atom" in typ or re.search(r"\.(xml|rss|atom)", href, re.I):
            feeds.append(urljoin(resp.url, href))
    # 兜底: 找明显的 /feed /rss 链接
    if not feeds:
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if re.search(r"(feed|rss|atom)", href) and re.search(r"\.(xml|rss)$|/feed/?$", href):
                feeds.append(urljoin(resp.url, a["href"]))
    return list(dict.fromkeys(feeds))


# ── 3. 全文内容增强 ───────────────────────────────────

def _extract_text_with_bs(html: str, url: str) -> str:
    """BeautifulSoup 提取正文：优先 article/main 容器, 否则最长 <p> 聚集"""
    soup = BeautifulSoup(html, "lxml")
    for tag in ("script", "style", "nav", "footer", "aside", "header", "form", "noscript"):
        for node in soup.find_all(tag):
            node.decompose()
    # 候选正文容器
    container = None
    for sel in ("article", "main", ".article-body", ".post-content",
                ".story-body", ".entry-content", '[itemprop="articleBody"]'):
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 200:
            container = node
            break
    if container is not None:
        text = "\n".join(p.strip() for p in container.find_all("p") if p.strip())
        if len(text) > 150:
            return text[:8000]
    # 兜底：取最长连续段落
    paras = [p.get_text(strip=True) for p in soup.find_all("p")]
    paras = [p for p in paras if len(p) > 40]
    if paras:
        return "\n".join(paras[:80])[:8000]
    return ""


def enhance_full_text(item: dict, timeout: int = TIMEOUT) -> str | None:
    """
    若 RSS 摘要过短(<200字)，抓原文页提取全文。
    item: 含 title/content/summary/source_url 的条目 dict。
    返回补全后的全文（写入 item["content"] 并返回）。
    """
    link = item.get("source_url") or item.get("link") or ""
    if not link or link.startswith("javascript"):
        return None
    cur = item.get("content") or item.get("summary") or ""
    if len(cur) >= 200:  # 已够长, 无需增强
        return None

    resp = _safe_get(link, timeout=timeout)
    if resp is None or resp.status_code >= 400:
        return None
    html = resp.text

    text = ""
    if HAS_NEWSPAPER:
        try:
            art = newspaper.Article(link, language="zh")
            art.download(input_html=html)
            art.parse()
            text = art.text or ""
        except Exception:
            text = ""
    if len(text) < 200:
        text = _extract_text_with_bs(html, link)

    if len(text) >= len(cur):
        item["content"] = text[:8000]
        item["_enhanced"] = True
        return item["content"]
    return None


# ── 4. Google News 备用源 ─────────────────────────────

def google_news_fallback(url_or_domain: str) -> str:
    """生成 Google News site: 搜索 RSS: https://news.google.com/rss/search?q=site:域名"""
    domain = _extract_domain(url_or_domain)
    if domain.startswith("news.google.com"):
        return url_or_domain  # 已是 Google News
    # 去 www. 前缀, 保留主域
    main = re.sub(r"^www\.", "", domain)
    return f"https://news.google.com/rss/search?q={quote('site:' + main)}"


# ── 5. 统一调度 ───────────────────────────────────────

def _candidate_variants(url: str) -> list[str]:
    """
    对已修复但仍探测失败的 URL 生成同级变体：
      .../rss    → 尝试 .../rss.xml / .../rss.html / .../feed
      .../feed   → 尝试 .../feed.xml / .../index.xml
      .../rss.xml→ 尝试 .../rss.html
    """
    base = re.sub(r"\.+$", "", url)
    variants: list[str] = []
    if re.search(r"\.xml$", base, re.I):
        stem = re.sub(r"\.xml$", "", base, flags=re.I)
        variants += [stem + ".html", stem, stem + "/"]
    elif re.search(r"/feed/?$", base, re.I):
        variants += [base + ".xml", base + "/index.xml", base.rstrip("/")]
    elif re.search(r"/rss/?$|/news/?$", base, re.I):
        variants += [base + ".xml", base + ".html", re.sub(r"/(rss|news)/?$", "/feed", base, flags=re.I)]
    return list(dict.fromkeys(v for v in variants if v != url))


def complete_rss_source(user_input: str, timeout: int = TIMEOUT) -> dict:
    """
    主入口：输入任意字符串（域名/破损链接/主页/未知源），
    按 修复→自动发现→Google备用 依次尝试，返回可用候选列表。

    返回结构:
      {
        "input":      原始输入,
        "candidates": [{"url": ..., "method": "fix|discover|fallback", "ok": bool}],
        "usable":     实际可抓取到 RSS 的 URL 列表,
        "best":       首选 URL 或 None,
      }
    """
    result = {"input": user_input, "candidates": [], "usable": [], "best": None}
    seen: set[str] = set()

    def _test(url: str, method: str):
        """探测候选是否真的返回 RSS/XML"""
        if url in seen:
            return
        seen.add(url)
        resp = _safe_get(url, timeout=timeout)
        ok = False
        if resp is not None and resp.status_code < 400:
            body = resp.text[:400].lstrip()
            ctype = resp.headers.get("Content-Type", "")
            ok = body.startswith("<?xml") or "<rss" in body or "<feed" in body or \
                 "rdf:RDF" in body or "xml" in ctype
        cand = {"url": url, "method": method, "ok": ok}
        result["candidates"].append(cand)
        if ok:
            result["usable"].append(url)
            if result["best"] is None:
                result["best"] = url

    u = user_input.strip().strip("\"'")

    # A. 已经是 news.google 查询 → 直接测试
    if "news.google.com/rss" in u:
        _test(u, "google-direct")
        return result

    # B. URL 修复
    fixed = fix_rss_url(u)
    if fixed:
        _test(fixed, "fix")

    # B2. 修复失败 → 探测同级变体 (rss.xml→rss.html 等)
    if not result["usable"] and fixed:
        for var in _candidate_variants(fixed):
            _test(var, "fix-variant")

    # C. RSS 自动发现（域名或主页）
    base = fixed or (u if re.match(r"^https?://", u, re.I) else "https://" + u)
    if not result["usable"]:
        # 逐级回退上级路径找主页 <link>（如 .../device/rss/ → .../device/ → 首页）
        probe = base
        for _depth in range(3):
            for feed_url in discover_rss(probe, timeout=timeout):
                _test(feed_url, "discover")
            if result["usable"]:
                break
            p = urlparse(probe)
            parent = p.path.rsplit("/", 1)[0]
            if parent in ("", "/", p.path):
                break
            probe = f"{p.scheme}://{p.netloc}{parent}"

    # D. Google News 备用
    if not result["usable"]:
        g = google_news_fallback(u)
        _test(g, "google-fallback")

    return result


# ── CLI ───────────────────────────────────────────────

def main():
    import argparse
    import json
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description="RSS 源自动修复与补全")
    parser.add_argument("inputs", nargs="*", help="要处理的源(域名/破损链接/主页)")
    parser.add_argument("--file", help="从文件读源(每行一个)")
    parser.add_argument("--json", help="输出 JSON 到文件")
    parser.add_argument("--timeout", type=int, default=6, help="单次请求超时秒数(默认6)")
    parser.add_argument("--verbose", action="store_true", help="显示全部候选(含失败的)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    inputs: list[str] = list(args.inputs)
    if args.file:
        p = Path(args.file)
        if p.exists():
            inputs += [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

    if not inputs:
        print("用法: python scripts/rss_source_fixer.py <源1> <源2> ... [--file f] [--json out.json]")
        return

    results = []
    for raw in inputs:
        r = complete_rss_source(raw, timeout=args.timeout)
        results.append(r)
        mark = "✅" if r["best"] else "❌"
        print(f"{mark} {raw}")
        print(f"    候选: {len(r['candidates'])} | 可用: {len(r['usable'])} | 最佳: {r['best']}")
        if args.verbose:
            for c in r["candidates"]:
                print(f"      [{c['method']:>8}] {'OK ' if c['ok'] else 'FAIL'} {c['url']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"结果已写入 {args.json}")


if __name__ == "__main__":
    main()
