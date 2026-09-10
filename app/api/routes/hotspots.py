"""
app/api/routes/hotspots.py — 全球热点聚合(真热度)

GET /api/hotspots?limit=20&hours=24
  topics[]    专题:按 news.category 聚合 top 组(name/summary/latest_time/count)
  hot_news[]  热点排名:title/source/publish_time/sentiment/heat/trend_per_h/stocks
  keywords[]  关键词榜:keyword/count/heat/trend_per_h
热度口径:heat = 来源权重 × 时效衰减(24h 线性递减,下限 0.05);
trend_per_h = 近 1h 提及数 − 前 1h 提及数(按来源/关键词维度)。
关键词优先取 news.keywords,缺失时回退匹配 sectors.sector_name(概念/行业名)在标题中的出现。
无数据返回空数组,不报错。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter

from app.api.schemas import APIResponse
from infrastructure.database import postgres as db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hotspots"])

# 来源权重(命中即用,子串匹配;未命中为 1.0)
_SOURCE_WEIGHT = {
    "bloomberg": 1.30, "reuters": 1.30, "ft": 1.30, "wsj": 1.25,
    "cnbc": 1.20, "economist": 1.15, "bbc": 1.10,
    "财联社": 1.10, "华尔街见闻": 1.10, "金十": 1.05,
    "新浪": 1.00, "东方财富": 1.00, "证券时报": 1.05,
}


def _weight(source: str) -> float:
    s = (source or "").lower()
    for k, v in _SOURCE_WEIGHT.items():
        if k in s:
            return v
    return 1.0


def _decay(hours_ago: float) -> float:
    return max(0.05, 1.0 - (hours_ago or 0.0) / 24.0)


def _maybe_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


@router.get("/api/hotspots")
async def list_hotspots(limit: int = 20, hours: int = 24):
    limit = max(1, min(int(limit), 50))
    hours = max(1, min(int(hours), 72))
    rows = await db.fetch_all("""
        SELECT news_id, title, source, publish_time::text AS publish_time,
               category, sentiment, stocks, keywords,
               EXTRACT(EPOCH FROM (NOW() - publish_time)) / 3600.0 AS hours_ago
        FROM news
        WHERE publish_time >= NOW() - make_interval(hours => $1)
        ORDER BY publish_time DESC
        LIMIT 800
    """, hours)

    now = datetime.now()

    # 关键词词表(概念/行业名);用于 keywords 缺失时的标题匹配回退
    try:
        name_rows = await db.fetch_all(
            "SELECT DISTINCT sector_name FROM sectors WHERE sector_name IS NOT NULL AND char_length(sector_name) >= 2"
        )
        vocab = [r["sector_name"] for r in name_rows]
    except Exception:
        logger.warning("hotspots: load sector vocab failed", exc_info=True)
        vocab = []

    hot_news = []
    kw_stat: dict[str, dict] = {}
    cat_stat: dict[str, dict] = {}
    src_hour: dict[str, list] = {}  # source -> [近1h, 前1h] 计数

    for r in rows:
        ha = float(r.get("hours_ago") or 0.0)
        w = _weight(r.get("source"))
        heat = round(w * _decay(ha), 3)
        title = r.get("title") or ""
        src = r.get("source") or ""

        # 热点排名条目
        hot_news.append({
            "news_id": r.get("news_id"),
            "title": title,
            "source": src,
            "publish_time": r.get("publish_time"),
            "sentiment": r.get("sentiment"),
            "stocks": _maybe_json(r.get("stocks")),
            "heat": heat,
            "trend_per_h": 0,  # 稍后按来源回填
        })

        # 来源小时分布(用于 trend)
        b = src_hour.setdefault(src, [0, 0])
        if ha <= 1: b[0] += 1
        elif ha <= 2: b[1] += 1

        # 关键词(优先 keywords 字段,回退词表匹配标题)
        keys: list[str] = []
        kv = _maybe_json(r.get("keywords"))
        if isinstance(kv, list) and kv:
            keys = [str(x) for x in kv if x]
        if not keys and vocab:
            keys = [n for n in vocab if n and n in title][:5]
        for k in set(keys):
            st = kw_stat.setdefault(k, {"count": 0, "heat": 0.0, "recent": 0, "prev": 0})
            st["count"] += 1
            st["heat"] += heat
            if ha <= 1: st["recent"] += 1
            elif ha <= 2: st["prev"] += 1

        # 专题(按 category)
        cat = r.get("category") or "other"
        cs = cat_stat.setdefault(cat, {"count": 0, "titles": [], "latest": r.get("publish_time")})
        cs["count"] += 1
        if len(cs["titles"]) < 3:
            cs["titles"].append(title)

    # 回填 hot_news 的 trend(按来源)
    for h in hot_news:
        b = src_hour.get(h["source"], [0, 0])
        h["trend_per_h"] = b[0] - b[1]

    hot_news.sort(key=lambda x: x["heat"], reverse=True)

    keywords = [
        {"keyword": k, "count": v["count"], "heat": round(v["heat"], 2),
         "trend_per_h": v["recent"] - v["prev"]}
        for k, v in kw_stat.items()
    ]
    keywords.sort(key=lambda x: (x["heat"], x["count"]), reverse=True)

    topics = []
    for cat, v in sorted(cat_stat.items(), key=lambda kv: kv[1]["count"], reverse=True)[:6]:
        topics.append({
            "name": cat,
            "summary": " / ".join(t for t in v["titles"] if t)[:180],
            "latest_time": v["latest"],
            "count": v["count"],
        })

    return APIResponse.success({
        "topics": topics,
        "hot_news": hot_news[:limit],
        "keywords": keywords[:limit],
        "generated_at": now.isoformat(),
        "window_hours": hours,
    })
