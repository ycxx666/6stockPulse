"""
news_gateway.fixes — URL 预处理规则 (fixes)
============================================
配置中每个 source 可声明 fixes: ["ensure_scheme", "strip_trailing_dot", ...]
本模块提供规则函数注册表 fix_registry，供工厂在请求前对 url 应用。
新增规则只需在此注册，不改适配器代码。
"""
from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit, urlunsplit

FIX_REGISTRY: dict[str, callable] = {}


def register(name: str):
    """装饰器: 注册一个 URL 修复规则"""
    def deco(fn):
        FIX_REGISTRY[name] = fn
        return fn
    return deco


def apply_fixes(url: str, fixes: list) -> str:
    """按顺序应用配置中的 fixes 规则（兼容字符串名或 dict 形式）"""
    result = url
    for fix in fixes or []:
        if isinstance(fix, str):
            fn = FIX_REGISTRY.get(fix)
            if fn:
                result = fn(result)
        elif isinstance(fix, dict):  # 带参数形式 {"_key": "append_query", "q": ...}
            name = fix.get("_key")
            fn = FIX_REGISTRY.get(name)
            if fn:
                params = {k: v for k, v in fix.items() if k != "_key"}
                result = fn(result, **params)
    return result


# ── 内建规则 ──────────────────────────────────────────

@register("ensure_scheme")
def ensure_scheme(url: str) -> str:
    """缺少协议头时补 https://"""
    if url and not re.match(r"^https?://", url, re.I):
        return "https://" + url
    return url


@register("strip_trailing_dot")
def strip_trailing_dot(url: str) -> str:
    """去掉 URL 末尾多余点号: .../rss. → .../rss"""
    if not url:
        return url
    # 先去掉 query
    parts = urlsplit(url)
    cleaned_path = re.sub(r"\.+$", "", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, cleaned_path, parts.query, parts.fragment))


@register("strip_dot_keep_xml")
def strip_dot_keep_xml(url: str) -> str:
    """CNBC 类: .../rss. → .../rss.xml（保留.xml扩展，用于 rss. 结尾）"""
    if not url:
        return url
    parts = urlsplit(url)
    path = parts.path
    m = re.match(r"^(.*?rss)\.$", path)
    if m:
        path = m.group(1) + ".xml"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


@register("correct_ap_domain")
def correct_ap_domain(url: str) -> str:
    """AP 拼写纠错: q=site%3Aapne... → site:apnews.com"""
    return re.sub(r"site%3Aapne[^&]*", "site%3Aapnews.com", url, flags=re.I)


@register("append_query")
def append_query(url: str, **_kw) -> str:
    """追加 Google News 搜索关键词（如商业/科技/头条细分）"""
    return url  # 占位: 具体关键词由调用方在 url 中配置


# 便捷: 统一的"链式入口"，供外部调用
def smart_fix(url: str, fixes: list | None = None) -> str:
    """默认修复栈：ensure_scheme → strip_trailing_dot。fixes 为空时也保证协议头。"""
    if fixes:
        return apply_fixes(url, fixes)
    return ensure_scheme(strip_trailing_dot(url))
