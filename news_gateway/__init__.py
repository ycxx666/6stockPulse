"""
6StockPulse · Unified News Source Gateway
===========================================
统一新闻源接入窗口：配置驱动 + 适配器 + 工厂。

特性:
  - 新增新闻源 → 只改 config/sources_config.json，不改代码
  - 适配器输出统一 UnifiedArticle
  - FT 自动降级 Google News；AP 域名自动纠错；CNBC 尾部点号自动清理
"""
from .adapters import (  # noqa: F401
    BaseSourceAdapter, DiscoverWebAdapter, FTAdapter,
    GoogleNewsAdapter, RssHubAdapter, StandardRSSAdapter,
)
from .factory import SourceFactory, get_available_types  # noqa: F401
from .fixes import FIX_REGISTRY, apply_fixes  # noqa: F401
from .models import UnifiedArticle  # noqa: F401
from .runner import fetch_all_sources, fetch_one  # noqa: F401

__version__ = "1.0.0"
__all__ = [
    "UnifiedArticle", "SourceFactory", "fetch_all_sources", "fetch_one",
    "BaseSourceAdapter", "StandardRSSAdapter", "GoogleNewsAdapter",
    "FTAdapter", "RssHubAdapter", "DiscoverWebAdapter",
    "get_available_types", "apply_fixes", "FIX_REGISTRY",
]
