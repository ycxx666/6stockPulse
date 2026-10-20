"""
news_gateway.adapters — 源适配器 (Adapter Pattern)
====================================================
BaseSourceAdapter 定义统一接口:
    fetch() -> list[UnifiedArticle]

子类:
    StandardRSSAdapter   — 标准 RSS/Atom (BBC/WSJ/卫报/新华社/CNBC 等)
    GoogleNewsAdapter    — Google News RSS (description 带 HTML, 需清洗+提取真实链接)
    RssHubAdapter        — RSSHub 中转源 (与 standard 同构, 独立类便于扩展)
    FTAdapter            — FT 专用: 携带 UA/Cookie, 失败或 Access Denied 降级 Google News
    DiscoverWebAdapter   — 仅域名/主页: 解析 <link> 自动发现 RSS

依赖: requests, feedparser, beautifulsoup4, tenacity
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from tenacity import (
    retry, stop_after_attempt, wait_fixed, retry_if_exception_type,
)

from .fixes import apply_fixes, smart_fix
from .models import UnifiedArticle

logger = logging.getLogger(__name__)

# Google News summary 中包装真实链接的格式
_GN_LINK_RE = re.compile(r'href="([^"]+)"')

# 需要跳过(常见垃圾)的条目标题开头
_SKIP_PREFIX = ("Live:", "Live ", "Reuters: ", "Video: ")

# ── host 级"直连失败熔断"记忆 ──
# 某 host(如 news.google.com) 直连超时一次后, 短期内同 host 请求直接走代理,
# 避免每组多源共享 host 时每源都空耗一次直连超时。
_DIRECT_BLACKLIST: dict[str, float] = {}   # host -> 过期时间戳
_DIRECT_BLACK_TTL = 900.0                   # 15 分钟


def _host_should_use_proxy(host: str) -> bool:
    return _DIRECT_BLACKLIST.get(host, 0.0) > time.time()


def _mark_host_direct_failed(host: str) -> None:
    _DIRECT_BLACKLIST[host] = time.time() + _DIRECT_BLACK_TTL


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_dt(v: str) -> Optional[datetime]:
    if not v:
        return None
    v = v.strip()
    try:
        return parsedate_to_datetime(v)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(int(v))
    except Exception:
        pass
    return None


class BaseSourceAdapter:
    """所有适配器的基类：统一会话、重试、节流与标准化输出骨架"""

    type_name = "base"

    def __init__(self, source_cfg: dict, global_cfg: Optional[dict] = None):
        self.cfg = source_cfg
        self.name: str = source_cfg.get("name", "unnamed")
        self.region: str = source_cfg.get("region", "")
        self.global_cfg = global_cfg or {}
        self.timeout: int = self.global_cfg.get("timeout", 15)
        self.retries: int = self.global_cfg.get("retries", 2)
        self.interval: float = self.global_cfg.get("request_interval", 1.0)
        default_ua = self.global_cfg.get("user_agent") or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        self.headers: dict = {"User-Agent": default_ua}
        self.cookies: dict = {}
        auth = source_cfg.get("auth") or {}
        if auth.get("headers"):
            self.headers.update(auth["headers"])
        if auth.get("cookies"):
            self.cookies.update(auth["cookies"])
        # 代理(双路策略): 源级 auth.proxy > 全局 global.proxy > 环境变量。
        # 会话先以"直连"发请求(短超时)，失败时 _request 自动改走显式代理重试：
        #   - 国内源(新浪/知乎/东财…)直连即通，不再被代理拖累/被代理 IP 风控；
        #   - 海外源(Google News/BBC…)直连超时后自动切代理，无需人工干预。
        self.proxy: Optional[str] = (
            auth.get("proxy") or self.global_cfg.get("proxy") or ""
        ) or None
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.cookies.update(self.cookies)
        self.session.trust_env = self.proxy is None  # 有显式代理时忽略系统代理
        self._proxy_session: Optional[requests.Session] = None  # 惰性创建
        self._direct_timeout: float = min(self.timeout, 2.5)  # 直连短超时（教育网访问境外受限，缩短等待）,代理用全量
        self._last_req = 0.0

    # ── HTTP 基础（带节流 + tenacity 重试 + 直连→代理双路）──
    def _throttle(self):
        gap = self.interval - (time.time() - self._last_req)
        if gap > 0:
            time.sleep(gap)
        self._last_req = time.time()

    def _do_request(self, session: requests.Session, url: str,
                    timeout: float) -> requests.Response:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp

    def _get_proxy_session(self) -> requests.Session:
        """惰性创建走显式代理的会话（忽略系统代理，直连失败时兜底用）"""
        if self._proxy_session is None:
            s = requests.Session()
            s.headers.update(self.headers)
            s.cookies.update(self.cookies)
            s.trust_env = False
            s.proxies = {"http": self.proxy, "https": self.proxy}
            self._proxy_session = s
        return self._proxy_session

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_fixed(1.0),
        retry=retry_if_exception_type(
            (requests.Timeout, requests.ConnectionError, requests.HTTPError)),
    )
    def _request(self, url: str) -> requests.Response:
        from urllib.parse import urlsplit
        self._throttle()
        host = urlsplit(url).netloc
        # 熔断命中: 该 host 近期直连失败过, 直接走代理(不再空耗直连超时)
        if self.proxy and _host_should_use_proxy(host):
            return self._do_request(self._get_proxy_session(),
                                    url, timeout=self.timeout)
        try:
            # 第一路: 直连(短超时) —— 国内源直接成功，不绕代理
            return self._do_request(self.session, url, timeout=self._direct_timeout)
        except (requests.Timeout, requests.ConnectionError,
                requests.HTTPError) as e:
            # 404 属服务器明确不存在，换代理无意义；无代理配置则直接抛出
            if isinstance(e, requests.HTTPError):
                code = e.response.status_code if e.response is not None else 0
                if code == 404 or not self.proxy:
                    raise
            if not self.proxy:
                raise
            # 第二路: 走显式代理(全量超时) —— 海外源/geo 403 在此兜底
            _mark_host_direct_failed(host)  # 熔断记忆, 同 host 后续源直走代理
            logger.info("[%s] 直连失败(%s)，自动改走代理并熔断 host=%s",
                        self.name, type(e).__name__, host)
            return self._do_request(self._get_proxy_session(),
                                    url, timeout=self.timeout)

    def _get_text(self, url: str) -> str:
        """GET 并返回文本（可被子类覆盖做降级）"""
        return self._request(url).text

    # ── 供子类调用的探测/降级助手 ───────────────────
    def _google_news_site_url(self) -> Optional[str]:
        fallback = self.cfg.get("fallback") or {}
        site = fallback.get("google_news_site_search")
        if not site:
            return None
        from urllib.parse import quote
        return f"https://news.google.com/rss/search?q={quote('site:' + site)}"

    def _fallback_candidates(self, base_url: str | None = None) -> list[str]:
        """按 fallback 配置生成备用候选 URL 列表（基于修复后的 url）"""
        cands: list[str] = []
        fb = self.cfg.get("fallback") or {}
        if fb.get("google_news_site_search"):
            g = self._google_news_site_url()
            if g:
                cands.append(g)
        # 变体探测: rss → rss.xml / rss.html / feed
        if fb.get("probe_variants"):
            base = base_url or smart_fix(self.cfg.get("url", ""), self.cfg.get("fixes"))
            for v in (base + ".xml", base + ".html",
                      re.sub(r"/(rss|news)/?$", "/feed", base, flags=re.I)):
                if v != base:
                    cands.append(v)
        return [c for c in cands if c]

    # ── 统一标准化输出 ───────────────────────────────
    def _make_article(self, *, title, link, published=None,
                      summary="", content="", source_override=None) -> Optional[UnifiedArticle]:
        t = (title or "").strip()
        if not t or not link or t.startswith(_SKIP_PREFIX):
            return None
        # 优先全文(content)，其次 summary；均去 HTML
        cleaned = _strip_html(content) if content else _strip_html(summary)
        return UnifiedArticle(
            source=source_override or self.name,
            title=t[:300],
            link=link,
            published=published,
            cleaned_text=cleaned[:8000],
            raw_summary=(summary or "")[:2000],
            region=self.region or "GLOBAL",
            extra={"region": self.region, "via": self.type_name},
        )

    def fetch(self) -> list[UnifiedArticle]:
        """子类必须实现。返回统一文章列表。"""
        raise NotImplementedError


# ── 1. 标准 RSS/Atom ─────────────────────────────────

class StandardRSSAdapter(BaseSourceAdapter):
    type_name = "standard_rss"

    def fetch(self) -> list[UnifiedArticle]:
        url = smart_fix(self.cfg.get("url", ""), self.cfg.get("fixes"))
        if not url:
            return []
        try:
            xml = self._get_text(url)
        except Exception as e:
            logger.info("[%s] 标准抓取失败(%s)，尝试变体", self.name, e)
            xml = self._try_variants(url)
        if not xml:
            logger.warning("[%s] 无可用 RSS 响应", self.name)
            return []
        feed = feedparser.parse(xml)
        if feed.bozo and not feed.entries:
            logger.warning("[%s] feedparser 解析异常: %s", self.name, feed.bozo_exception)
            return []
        return self._normalize_entries(feed.entries)

    def _try_variants(self, base_url: str) -> Optional[str]:
        """主 URL 失败时尝试 fallback 候选（google site 搜索等）"""
        for cand in self._fallback_candidates():
            try:
                return self._get_text(cand)
            except Exception:
                continue
        return None

    def _normalize_entries(self, entries) -> list[UnifiedArticle]:
        out: list[UnifiedArticle] = []
        for e in entries:
            link = (e.get("link") or "").strip()
            # RSS 2.0 summary→description; content:encoded 优先
            summary = e.get("summary", "")
            content = ""
            if e.get("content"):
                content = e["content"][0].get("value", "") if isinstance(e["content"], list) else str(e["content"])
            if not content:
                content = summary
            if not summary and e.get("description"):
                summary = e["description"]
            title = e.get("title", "")
            pub = _parse_dt(e.get("published") or e.get("updated") or "")
            art = self._make_article(title=title, link=link, published=pub,
                                     summary=summary, content=content)
            if art:
                out.append(art)
        return out


# ── 2. Google News RSS ───────────────────────────────

class GoogleNewsAdapter(BaseSourceAdapter):
    """Google News RSS：summary 是 HTML(<a><font>包裹真实链接与文本)，需清洗"""

    type_name = "google_news_rss"

    def fetch(self) -> list[UnifiedArticle]:
        # 先 apply fixes（含 correct_ap_domain 等），再 fallback 探测
        url = apply_fixes(self.cfg.get("url", ""), self.cfg.get("fixes"))
        url = smart_fix(url)
        if not url:
            return []
        xml = None
        try:
            xml = self._get_text(url)
        except Exception:
            pass
        if not xml:
            for cand in self._fallback_candidates():
                try:
                    xml = self._get_text(cand)
                    break
                except Exception:
                    continue
        if not xml:
            logger.warning("[%s] Google News 获取失败", self.name)
            return []
        # feedparser 对 Google News 非标准 XML 解析不可靠，改用 ElementTree
        return self._parse_gn_xml(xml)

    def _parse_gn_xml(self, xml: str) -> list[UnifiedArticle]:
        """ElementTree 解析 Google News RSS（标准 RSS2.0 结构）"""
        from xml.etree import ElementTree as ET
        out: list[UnifiedArticle] = []

        def _inner(el: ET.Element | None) -> str:
            """取元素完整 inner HTML（含子节点序列化），Google News title 内嵌 <a><font>"""
            if el is None:
                return ""
            parts = [el.text or ""]
            for child in el:
                parts.append(ET.tostring(child, encoding="unicode"))
                parts.append(child.tail or "")
            return "".join(parts)

        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            logger.warning("[%s] XML 解析失败: %s", self.name, e)
            return out
        for item in root.iter("item"):
            title_html = _inner(item.find("title"))
            summary = _inner(item.find("description"))
            link = (item.findtext("link") or "").strip()
            pub = _parse_dt(item.findtext("pubDate") or "")
            # 真实文章链接藏于 description 的 <a href>；title 亦常含
            real = None
            for hay in (summary, title_html):
                m = _GN_LINK_RE.search(hay or "")
                if m:
                    real = m.group(1)
                    break
            if real:
                link = real
            title = _strip_html(title_html)
            art = self._make_article(title=title, link=link, published=pub, summary=summary)
            if art:
                art.extra["needs_fulltext"] = len(art.cleaned_text) < 200
                out.append(art)
        return out


# ── 3. RSSHub 中转源 ─────────────────────────────────

# 公共 RSSHub 候选实例（主 url 的 host 会依次轮换尝试）
RSSHUB_FALLBACK_HOSTS = [
    "rss.injahow.cn",   # 实测可用(zhihu/见闻系等部分路由)
    "rsshub.app",       # 实测 403(可能需 UA/反爬), 轮换保留
]


class RssHubAdapter(StandardRSSAdapter):
    """RSSHub 实例源：与标准 RSS 同构，独立类型便于统一识别。
    支持主机级 fallback：主实例失败(503/超时)时自动把 url host 轮换到候选实例重试。"""

    type_name = "rsshub_rss"

    def _host_candidates(self, url: str) -> list[str]:
        """返回按优先级排列的候选 URL（主 url + 换 host 的 fallback）"""
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(url)
        cands = [url]
        fb_hosts = (self.cfg.get("fallback") or {}).get("hosts") or RSSHUB_FALLBACK_HOSTS
        for h in fb_hosts:
            if h != p.netloc:
                cands.append(urlunsplit((p.scheme, h, p.path, p.query, p.fragment)))
        return list(dict.fromkeys(cands))

    def fetch(self) -> list[UnifiedArticle]:
        from urllib.parse import urlsplit
        url = smart_fix(self.cfg.get("url", ""), self.cfg.get("fixes"))
        if not url:
            return []
        last_err = None
        for cand in self._host_candidates(url):
            try:
                xml = self._get_text(cand)
            except Exception as e:
                last_err = e
                if cand != url:  # 主实例失败才记日志；换 host 尝试
                    logger.info("[%s] host %s 失败(%s)，尝试下一实例",
                                self.name, urlsplit(cand).netloc,
                                type(e).__name__)
                continue
            if xml:
                arts = self._normalize_entries(feedparser.parse(xml).entries)
                if arts or len(xml) > 500:
                    return arts
            if cand != url:
                logger.info("[%s] host %s 无有效内容", self.name, urlsplit(cand).netloc)
        if last_err is not None:
            logger.warning("[%s] 所有 rsshub 实例均失败: %s", self.name, type(last_err).__name__)
        return []


# ── 4. FT 专用适配器 ─────────────────────────────────

class FTAdapter(StandardRSSAdapter):
    """
    FT: 需要模拟浏览器 + Cookie(ft-cookie-consent)；
    若响应含 "Access Denied" 或解析内容过短(<100字符)，自动降级到 Google News site:ft.com。
    """
    type_name = "ft_rss"

    def fetch(self) -> list[UnifiedArticle]:
        url = smart_fix(self.cfg.get("url", ""), self.cfg.get("fixes"))
        if not url:
            return []
        xml: Optional[str] = None
        via = "ft-direct"
        try:
            xml = self._get_text(url)
            # 判定被拦截
            if "Access Denied" in (xml or "") or (len(xml or "") < 100):
                logger.info("[%s] FT 直连被拦截(短响应/Access Denied)，降级 Google News", self.name)
                xml = None
        except Exception as e:
            logger.info("[%s] FT 直连异常(%s)，降级 Google News", self.name, e)
            xml = None

        if not xml:
            gurl = self._google_news_site_url()
            if gurl:
                try:
                    xml = self._get_text(gurl)
                    via = "google-news-fallback"
                except Exception as e:
                    logger.warning("[%s] FT Google 降级也失败: %s", self.name, e)
        if not xml:
            return []

        feed = feedparser.parse(xml)
        arts = self._normalize_entries(feed.entries)
        for a in arts:
            a.extra["via"] = via
            a.source = f"{self.name}({via})" if via != "ft-direct" else self.name
        logger.info("[%s] FT 抓取 %d 条 (via=%s)", self.name, len(arts), via)
        return arts


# ── 5. 自动发现(仅域名/主页) ─────────────────────────

class DiscoverWebAdapter(BaseSourceAdapter):
    """输入仅为域名/主页：解析 <link rel=alternate> 自动发现 RSS；失败则 Google 备用"""
    type_name = "discover_from_web"

    def fetch(self) -> list[UnifiedArticle]:
        url = smart_fix(self.cfg.get("url", ""), self.cfg.get("fixes"))
        feeds = self._discover(url)
        if not feeds:
            for cand in self._fallback_candidates():
                feeds.append(cand)
        all_arts: list[UnifiedArticle] = []
        for feed_url in feeds:
            try:
                xml = self._get_text(feed_url)
            except Exception:
                continue
            fp = feedparser.parse(xml)
            all_arts.extend(self._normalize_entries(fp.entries))
            if all_arts:
                break
        return all_arts

    def _discover(self, url: str) -> list[str]:
        try:
            html = self._get_text(url)
        except Exception:
            return []
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        feeds = []
        for link in soup.find_all("link", rel="alternate"):
            typ = (link.get("type") or "").lower()
            href = link.get("href")
            if href and ("rss" in typ or "atom" in typ):
                from urllib.parse import urljoin
                feeds.append(urljoin(url, href))
        return list(dict.fromkeys(feeds))
