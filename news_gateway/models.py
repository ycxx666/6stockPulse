"""
news_gateway.models — 统一标准化输出结构
=========================================
所有适配器(无论 standard_rss / google_news_rss / ft_rss ...)
都必须输出本模块定义的 UnifiedArticle，保证下游处理一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class UnifiedArticle:
    """统一文章结构（网关内部标准格式）"""

    source: str                 # 源名称, 如 "FT市场"
    title: str                  # 标题
    link: str                   # 原文链接
    published: Optional[datetime] = None   # 发布时间
    cleaned_text: str = ""      # 去除 HTML 后的纯文本正文(优先全文, 其次 summary)
    raw_summary: str = ""       # 原始摘要备份(可能含 HTML, 供调试/再加工)
    region: str = "GLOBAL"      # GLOBAL/US/CN: 新闻源地理归属(区分来源, 便于抽出分析)
    category: str = "other"     # macro/policy/company/global/other
    extra: dict = field(default_factory=dict)  # 附加信息(地区/抓取方式等)

    # ── 供 SQLite 入库 ──────────────────────────────
    def to_db_row(self) -> dict[str, Any]:
        """转换为 infrastructure.database.sqlite_news.insert_news_batch 所需的 dict"""
        news_id = self.link or f"{self.source}:{self.title}"
        return {
            "news_id": news_id[:200],
            "title": self.title[:300],
            "content": self.cleaned_text[:5000] or self.raw_summary[:5000],
            "summary": self.cleaned_text[:500],
            "publish_time": self.published.isoformat() if self.published
                            else datetime.now().isoformat(),
            "source": self.source[:50],
            "region": self.region.upper(),
            "source_url": self.link[:500],
            "category": self.category,
            "keywords": [],
            "crawl_time": datetime.now().isoformat(),
            "extra": self.extra,
        }

    def __repr__(self) -> str:  # 便于调试打印
        return (f"<UnifiedArticle source={self.source!r} "
                f"title={self.title[:40]!r} link={self.link[:50]!r}>")
