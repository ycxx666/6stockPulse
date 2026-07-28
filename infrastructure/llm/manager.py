"""
infrastructure/llm/manager.py
===============================
LLM 管理器：根据配置路由到具体 Provider。
替代旧的 services/llm_service.py 中的 generate_market_summary 等函数。
"""
from __future__ import annotations
import logging

from infrastructure.llm.prompts.market_summary import (
    build_market_summary_prompt, template_summary
)

logger = logging.getLogger(__name__)


class LLMManager:
    """统一 LLM 调用入口"""

    def __init__(self, provider: str, api_key: str, model: str,
                 base_url: str | None = None):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    async def generate(self, prompt: str, **kwargs) -> str:
        """路由到具体 Provider 生成文本"""
        if self.provider == "template" or not self.api_key:
            return "[template 模式，无 LLM 调用]"

        if self.provider in ("openai", "deepseek"):
            from infrastructure.llm.providers.openai import generate
            return await generate(
                prompt, self.api_key, self.model,
                base_url=self.base_url, **kwargs
            )
        elif self.provider == "anthropic":
            from infrastructure.llm.providers.anthropic import generate
            return await generate(
                prompt, self.api_key, self.model, **kwargs
            )
        else:
            logger.warning("Unknown LLM provider: %s, fallback to template", self.provider)
            return ""

    async def market_summary(self, context: dict) -> str:
        """生成市场观察摘要"""
        prompt = build_market_summary_prompt(context)
        if self.provider == "template" or not self.api_key:
            return template_summary(context)
        try:
            result = await self.generate(prompt, max_tokens=800, temperature=0.3)
            return result or template_summary(context)
        except Exception as e:
            logger.warning("LLM call failed, fallback to template: %s", e)
            return template_summary(context)

    async def daily_briefing(self) -> dict:
        """生成每日简报"""
        from datetime import datetime
        from application.dashboard.aggregator import get_snapshot

        snapshot = await get_snapshot(use_cache=True)
        data = snapshot.get("data", {})
        context = {
            "sentiment": data.get("sentiment", {}),
            "signals": data.get("signals", []),
            "breadth": data.get("breadth", {}),
            "prediction": data.get("prediction", {}),
            "top_sectors": data.get("concept_leaders", []),
        }
        summary = await self.market_summary(context)
        return {
            "generated_at": datetime.now().isoformat(),
            "model": self.model if self.api_key else "template",
            "summary": summary,
            "context": {
                "sentiment_score": context["sentiment"].get("ashare", {}).get("score"),
                "signal_count": len(context["signals"]),
                "prediction_up": context["prediction"].get("up_probability"),
            },
        }
