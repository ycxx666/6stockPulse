"""
news_gateway.custom — 定制直连适配器 (SocialHotJson)
=====================================================
针对"无官方 RSS + 强反爬"的平台（雪球/微博/头条/知乎热榜等）：
绕开 RSSHub 中转，直接请求源站半公开 JSON/HTML 端点，解析为
UnifiedArticle —— 与 standard_rss 走同一条入库管线。

设计要点（对应 docs/news_source_solution_plan.md §二-路径C/§四）：
  - 继承 BaseSourceAdapter：节流/重试/双路代理/cookie 槽位全部复用；
  - 风控识别：响应是 WAF/登录挑战页(HTML/JS)时抛 SourceBlocked，
    让上层明确降级到 rsshub/第三方聚合，而不是产出脏数据；
  - cookie 外置：auth.cookies_file 从文件加载(绝不写进 config 进 git)。

配置示例(sources_config.json)：
{
  "name": "雪球热门-直连",
  "type": "social_hot_json",
  "region": "CN",
  "url": "https://xueqiu.com/statuses/hot/listV2.json?since_id=-1&max_id=-1&size=15",
  "parser": {
    "list_path": "data.items",          # JSON 中列表的 dotted path
    "fields": {"id": "id", "title": "text", "time": "created_at"},
    "title_len": 60,
    "link_prefix": "https://xueqiu.com/"
  },
  "auth": {"cookies_file": "config/cookies/xueqiu.txt"}   # 可选
}
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from .adapters import BaseSourceAdapter
from .models import UnifiedArticle

logger = logging.getLogger(__name__)

# 风控/挑战页特征（命中即判定被拦截，返回 HTML/JS 而非数据）
_BLOCK_MARKERS = (
    "aliyun_waf", "waf_", "renderData", "getRende",
    "<!doctype html", "<!DOCTYPE html", "captcha", "验证码", "登录后查看",
)


class SourceBlocked(RuntimeError):
    """源站风控/登录墙拦截 —— 上层应降级而非当失败处理"""


class SocialHotJsonAdapter(BaseSourceAdapter):
    """直连源站 JSON 热榜/帖子端点 → UnifiedArticle"""

    type_name = "social_hot_json"

    def __init__(self, source_cfg: dict, global_cfg: Optional[dict] = None):
        super().__init__(source_cfg, global_cfg)
        parser = source_cfg.get("parser") or {}
        self._parser = parser
        # cookie 文件槽位（可选）：每行 name=value 或 name: value / JSON
        cf = (source_cfg.get("auth") or {}).get("cookies_file")
        if cf:
            self._load_cookies_file(cf)

    def _load_cookies_file(self, path: str) -> None:
        try:
            from pathlib import Path
            raw = Path(path).read_text(encoding="utf-8").strip()
            cookies: dict[str, str] = {}
            if raw.startswith("{"):
                cookies = json.loads(raw)
            else:
                for line in raw.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    k, _, v = line.partition("=")
                    if not v:
                        k, _, v = line.partition(":")
                    if v:
                        cookies[k.strip()] = v.strip()
            self.cookies.update(cookies)
            self.session.cookies.update(cookies)
            if self._proxy_session is not None:
                self._proxy_session.cookies.update(cookies)
            logger.info("[%s] 已加载 cookie 文件 %s (%d 项)",
                        self.name, path, len(cookies))
        except Exception as e:
            logger.warning("[%s] cookie 文件加载失败(%s)，继续无 cookie 尝试",
                           self.name, e)

    # ── JSON 定位助手 ──────────────────────────────
    @staticmethod
    def _get_path(obj: Any, path: Optional[str]) -> Any:
        """按 dotted path 取值: 'data.items' → obj['data']['items']"""
        if not path:
            return obj
        cur = obj
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return None
        return cur

    def _is_blocked(self, text: str) -> bool:
        head = text[:800].lower()
        return any(m in text or m.lower() in head for m in _BLOCK_MARKERS)

    def _parse_time(self, v: Any) -> Optional[datetime]:
        if not v:
            return None
        try:
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(v / 1000 if v > 10**12 else v)
            s = str(v)
            return datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
        except Exception:
            return None

    def _make_from_item(self, item: dict) -> Optional[UnifiedArticle]:
        fields = (self._parser.get("fields") or {})
        title_f = fields.get("title", "title")
        id_f = fields.get("id", "id")
        time_f = fields.get("time") or ""
        # 标题允许 "a.b" 取嵌套字段；取不到则跳过该条
        title = self._get_path(item, title_f)
        if title is None and not isinstance(item.get(title_f), str):
            return None
        title = str(title).strip()
        if not title:
            return None
        link = self._get_path(item, fields.get("link")) if fields.get("link") else None
        if not link:
            oid = self._get_path(item, id_f)
            prefix = self._parser.get("link_prefix", "")
            link = f"{prefix}{oid}" if oid is not None else None
        if not link and self._parser.get("link_tpl"):
            # 无 id 的榜单条目(如微博热搜): link_tpl 形如 "https://s.weibo.com/weibo?q=%s"
            from urllib.parse import quote
            link = self._parser["link_tpl"] % quote(title)
        if not link:
            return None
        tlen = int(self._parser.get("title_len", 80))
        short_title = title[:tlen]
        return UnifiedArticle(
            source=self.name,
            title=short_title,
            link=str(link),
            published=self._parse_time(self._get_path(item, time_f)) if time_f else None,
            cleaned_text=title[:8000],
            raw_summary=title[:2000],
            region=self.region or "CN",
            extra={"region": self.region or "CN", "via": self.type_name},
        )

    def fetch(self) -> list[UnifiedArticle]:
        from .fixes import smart_fix
        url = smart_fix(self.cfg.get("url", ""), self.cfg.get("fixes"))
        if not url:
            return []
        try:
            text = self._get_text(url)
        except Exception as e:
            logger.warning("[%s] 直连请求失败: %s", self.name, e)
            return []
        if self._is_blocked(text):
            logger.warning("[%s] 命中风控/登录墙(%s 字符挑战页)，判定 SourceBlocked",
                           self.name, len(text))
            raise SourceBlocked(f"{self.name} 被源站风控/登录墙拦截")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("[%s] 响应非 JSON(前 120 字符: %r)，丢弃",
                           self.name, text[:120])
            return []
        items = self._get_path(data, self._parser.get("list_path")) or []
        if not isinstance(items, list):
            logger.warning("[%s] list_path=%r 未定位到列表", self.name,
                           self._parser.get("list_path"))
            return []
        out = []
        for it in items:
            if isinstance(it, dict):
                a = self._make_from_item(it)
                if a:
                    out.append(a)
        logger.info("[%s] 直连抓取 %d 条 (blocked=%s)", self.name, len(out),
                    self._is_blocked(text))
        return out
