"""
infrastructure/llm/prompts/market_summary.py — 市场观察摘要 Prompt 模板
"""


def build_market_summary_prompt(context: dict) -> str:
    """基于结构化数据构建市场摘要 prompt"""
    sentiment = context.get("sentiment", {})
    ashare = sentiment.get("ashare", {})
    g = sentiment.get("global", {})
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
        f"  全球情绪: {g.get('score', '?')}分 ({g.get('label', '?')})",
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
        parts.append(
            f"【次日预测】上行概率{prediction.get('up_probability',0):.0%}, "
            f"下行概率{prediction.get('down_probability',0):.0%}, "
            f"置信度{prediction.get('confidence',0):.0%}"
        )
    return "\n".join(parts)


def template_summary(context: dict) -> str:
    """模板规则生成（无需 LLM，降级方案）"""
    sentiment = context.get("sentiment", {})
    ashare = sentiment.get("ashare", {})
    breadth = context.get("breadth", {})
    prediction = context.get("prediction", {})

    up = breadth.get("up", 0)
    down = breadth.get("down", 0)
    status = "偏强" if up > down else "偏弱" if down > up else "均衡"

    parts = [
        f"A股市场情绪{ashare.get('label','中性')}({ashare.get('score',50)}分)，"
        f"涨跌家数{up}:{down}，整体{status}。",
        f"涨停{breadth.get('limit_up',0)}家，跌停{breadth.get('limit_down',0)}家。",
    ]
    up_prob = prediction.get("up_probability", 0)
    if up_prob >= 0.5:
        parts.append(f"模型预期次日上行概率{up_prob:.0%}，建议关注量能变化。")
    elif up_prob <= 0.35:
        parts.append("模型预期次日下行风险偏高，注意仓位管理。")
    else:
        parts.append("市场方向不明确，短期或以震荡为主。")
    return "".join(parts)
