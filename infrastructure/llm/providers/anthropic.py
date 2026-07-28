"""
infrastructure/llm/providers/anthropic.py — Anthropic Claude Provider
"""
from __future__ import annotations


async def generate(prompt: str, api_key: str, model: str,
                   max_tokens: int = 200, **kwargs) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()
