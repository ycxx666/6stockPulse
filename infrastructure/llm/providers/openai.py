"""
infrastructure/llm/providers/openai.py — OpenAI 兼容 Provider (DeepSeek 等)
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


async def generate(prompt: str, api_key: str, model: str,
                   base_url: str | None = None, max_tokens: int = 800,
                   temperature: float = 0.3) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
    resp = await client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens, temperature=temperature,
    )
    content = resp.choices[0].message.content
    if not content or not content.strip():
        reasoning = getattr(resp.choices[0].message, "reasoning_content", None)
        if reasoning:
            content = reasoning.strip().split("\n")[-1] if "\n" in reasoning else reasoning[-150:]
    return (content or "数据不足，无法生成摘要。").strip()
