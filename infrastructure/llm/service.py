"""
services/llm_service.py
=========================
LLM 摘要生成服务

职责: 将结构化信号/情绪/预测结果转化为人类可读的摘要文本。
LLM 不直接预测涨跌 — 只解释已有的结构化结论。

支持: OpenAI / Anthropic / 本地模型 (通过环境变量配置)
无 API Key 时降级为规则模板生成。
"""
from __future__ import annotations
import os
import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ────────── 配置 ──────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")          # template / openai / anthropic
LLM_MODEL    = os.getenv("LLM_MODEL",    "deepseek-v4-flash")
LLM_API_KEY  = os.getenv("LLM_API_KEY",  "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")


async def generate_market_summary(context: dict) -> str:
    """
    基于结构化数据生成市场观察摘要

    context = {
        "sentiment": {global: {score, label}, ashare: {score, label, factors}},
        "signals": [{title, summary, evidence}],
        "breadth": {up, down, limit_up, limit_down},
        "prediction": {up_probability, down_probability, confidence},
        "top_sectors": [{name, change_pct}],
    }
    """
    if LLM_PROVIDER == "template" or not LLM_API_KEY:
        return _template_summary(context)

    prompt = _build_prompt(context)
    try:
        if LLM_PROVIDER == "openai":
            return await _call_openai(prompt)
        elif LLM_PROVIDER == "anthropic":
            return await _call_anthropic(prompt)
    except Exception as e:
        logger.warning("LLM call failed, falling back to template: %s", e)
    return _template_summary(context)


def _build_prompt(context: dict) -> str:
    """构建 LLM prompt"""
    sentiment = context.get("sentiment", {})
    ashare = sentiment.get("ashare", {})
    global_ = sentiment.get("global", {})
    signals = context.get("signals", [])[:5]
    breadth = context.get("breadth", {})
    prediction = context.get("prediction", {})
    sectors = context.get("top_sectors", [])[:3]

    parts = [
        "你是一位专业的A股市场分析师。请基于以下结构化数据，"
        "用100-150字中文生成一段简洁的盘中市场观察摘要。"
        "必须引用至少2个具体数据点，不要给出投资建议。\n",
        "【市场情绪】",
        f"  A股情绪: {ashare.get('score', '?')}分 ({ashare.get('label', '?')})",
        f"  全球情绪: {global_.get('score', '?')}分 ({global_.get('label', '?')})",
        f"  上涨{breadth.get('up','?')}家 / 下跌{breadth.get('down','?')}家",
        f"  涨停{breadth.get('limit_up','?')}家 / 跌停{breadth.get('limit_down','?')}家",
    ]
    if sectors:
        parts.append("【领涨板块】")
        for s in sectors:
            parts.append(f"  {s.get('name','')}: {s.get('change_pct',0):+.2f}%")
    if signals:
        parts.append("【市场信号】")
        for s in signals:
            parts.append(f"  [{s.get('signal_type','')}] {s.get('title','')}")
    if prediction:
        parts.append(f"【次日预测】上行概率{prediction.get('up_probability',0):.0%}, "
                     f"下行概率{prediction.get('down_probability',0):.0%}, "
                     f"置信度{prediction.get('confidence',0):.0%}")

    return "\n".join(parts)


def _template_summary(context: dict) -> str:
    """模板规则生成 (无需 LLM)"""
    sentiment = context.get("sentiment", {})
    ashare = sentiment.get("ashare", {})
    breadth = context.get("breadth", {})
    prediction = context.get("prediction", {})

    label = ashare.get("label", "中性")
    score = ashare.get("score", 50)
    up = breadth.get("up", 0)
    down = breadth.get("down", 0)
    limit_up = breadth.get("limit_up", 0)
    limit_down = breadth.get("limit_down", 0)

    status = "偏强" if up > down else "偏弱" if down > up else "均衡"
    parts = [
        f"A股市场情绪{label}({score}分)，涨跌家数{up}:{down}，整体{status}。",
        f"涨停{limit_up}家，跌停{limit_down}家。",
    ]

    up_prob = prediction.get("up_probability", 0)
    if up_prob >= 0.5:
        parts.append(f"模型预期次日上行概率{up_prob:.0%}，建议关注量能变化。")
    elif up_prob <= 0.35:
        parts.append(f"模型预期次日下行风险偏高，注意仓位管理。")
    else:
        parts.append("市场方向不明确，短期或以震荡为主。")

    return "".join(parts)


async def _call_openai(prompt: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or None)
    resp = await client.chat.completions.create(
        model=LLM_MODEL, messages=[{"role": "user", "content": prompt}],
        max_tokens=800, temperature=0.3,
    )
    content = resp.choices[0].message.content
    # DeepSeek R1/V4 推理模型: content 可能为空，需重试或降级
    if not content or not content.strip():
        # 降级: 使用 reasoning_content 的最后一段作为摘要
        reasoning = getattr(resp.choices[0].message, 'reasoning_content', None)
        if reasoning:
            # 取推理结果的最后 150 字
            content = reasoning.strip().split('\n')[-1] if '\n' in reasoning else reasoning[-150:]
    return (content or "市场数据不足，无法生成摘要。").strip()


async def _call_anthropic(prompt: str) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=LLM_API_KEY)
    resp = await client.messages.create(
        model=LLM_MODEL, max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ────────── 便捷入口 ──────────
async def generate_daily_briefing() -> dict:
    """生成每日简报 (信号 + 情绪 + 预测 → LLM 摘要)"""
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

    summary = await generate_market_summary(context)
    return {
        "generated_at": datetime.now().isoformat(),
        "model": LLM_MODEL if LLM_API_KEY else "template",
        "summary": summary,
        "context": {
            "sentiment_score": context["sentiment"].get("ashare", {}).get("score"),
            "signal_count": len(context["signals"]),
            "prediction_up": context["prediction"].get("up_probability"),
        },
    }
