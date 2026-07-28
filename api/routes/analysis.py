"""
api/routes/analysis.py — NLP分析 / LLM摘要 / 回测评估
"""
from __future__ import annotations

from fastapi import APIRouter

from api.schemas import APIResponse

router = APIRouter(tags=["analysis"])


@router.get("/api/analysis/news-report")
async def news_analysis_report(hours: int = 2):
    """NLP 新闻分析报告 (情感/关键词/主题/来源)"""
    from services.news_service import generate_analysis_report
    data = await generate_analysis_report(hours)
    return APIResponse.success(data)


@router.get("/api/analysis/briefing")
async def daily_briefing():
    """AI 市场简报 (结构化数据 → 摘要)"""
    from services.llm_service import generate_daily_briefing
    data = await generate_daily_briefing()
    return APIResponse.success(data)


@router.get("/api/predictions/evaluate")
async def evaluate_predictions(days: int = 30):
    """预测回测评估"""
    from services.prediction_service import evaluate_predictions
    data = await evaluate_predictions(days)
    return APIResponse.success(data)


@router.post("/api/analysis/process-news")
async def trigger_nlp_process(limit: int = 100):
    """手动触发 NLP 新闻处理"""
    from services.news_service import process_news_batch
    data = await process_news_batch(limit)
    return APIResponse.success(data)


@router.post("/api/analysis/generate-signals")
async def trigger_signal_generation():
    """手动触发信号生成"""
    from services.signal_service import generate_signals
    data = await generate_signals()
    return APIResponse.success({"count": len(data), "signals": data[:5]})
