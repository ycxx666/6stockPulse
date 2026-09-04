"""
news_gateway.factory — 源工厂 (Factory Pattern)
================================================
根据配置 type 实例化对应适配器。新增 type 只需在此注册，不改业务代码。
"""
from __future__ import annotations

from typing import Type

from .adapters import (
    BaseSourceAdapter, DiscoverWebAdapter, FTAdapter,
    GoogleNewsAdapter, RssHubAdapter, StandardRSSAdapter,
)
from .custom import SocialHotJsonAdapter

# type → 适配器类 注册表
ADAPTER_REGISTRY: dict[str, Type[BaseSourceAdapter]] = {
    "standard_rss": StandardRSSAdapter,
    "google_news_rss": GoogleNewsAdapter,
    "ft_rss": FTAdapter,
    "rsshub_rss": RssHubAdapter,
    "discover_from_web": DiscoverWebAdapter,
    "social_hot_json": SocialHotJsonAdapter,  # 直连 JSON 热榜(雪球/微博等, 见 custom.py)
}


def register_adapter(type_name: str, cls: Type[BaseSourceAdapter]) -> None:
    """允许外部扩展注册自定义适配器"""
    ADAPTER_REGISTRY[type_name] = cls


class SourceFactory:
    """根据 source_cfg['type'] 创建适配器实例"""

    @staticmethod
    def create(source_cfg: dict, global_cfg: dict | None = None) -> BaseSourceAdapter:
        stype = source_cfg.get("type", "standard_rss")
        cls = ADAPTER_REGISTRY.get(stype)
        if cls is None:
            raise ValueError(
                f"未知源类型 {stype!r}，可用: {list(ADAPTER_REGISTRY)}")
        return cls(source_cfg, global_cfg)


def get_available_types() -> list[str]:
    return sorted(ADAPTER_REGISTRY)
