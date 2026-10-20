"""
services/news_service.py
===========================
NLP 新闻处理流水线

功能:
  1. jieba 中文分词 + 金融停用词过滤
  2. 金融情感词典 (40+ 积极词 / 40+ 消极词)
  3. TF-IDF 关键词提取
  4. KMeans 主题聚类
  5. SimHash 新闻去重
  6. 实体映射 (股票代码/板块/概念)
  7. 重要度评分 (0-5)

从 OLD_backup/动态版看板/源代码/analysis.py 移植
"""
from __future__ import annotations
import re
import hashlib
import logging
from datetime import datetime
from typing import Any
from collections import Counter

logger = logging.getLogger(__name__)

# ────────── 可选依赖 ──────────
try:
    import jieba
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False
    logger.warning("jieba not installed; seg will use char-level fallback")

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logger.warning("scikit-learn not installed; TF-IDF/KMeans disabled")


# ═══════════════════════════════════════════════════════════════
# 1. 文本清洗
# ═══════════════════════════════════════════════════════════════
def clean_text(text: str) -> str:
    """去除 HTML 标签、实体、特殊字符"""
    if not text:
        return ""
    # HTML 标签
    text = re.sub(r"<[^>]+>", "", text)
    # HTML 实体
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#13;", ""))
    # 数字实体 &#13; &#10; 等
    text = re.sub(r"&#\d+;", "", text)
    # 特殊字符清理
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════
# 2. 金融停用词列表
# ═══════════════════════════════════════════════════════════════
STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "报道", "记者", "新闻", "来源", "编辑", "发布", "同比", "环比",
    "亿元", "万元", "美元", "人民", "币", "公司", "集团", "有限",
    "股份", "数据", "显示", "表示", "认为", "分析", "指出",
    "目前", "已经", "正在", "将", "可能", "预计", "预期",
    "相关", "情况", "方面", "其中", "进行", "通过", "根据",
    "以及", "包括", "主要", "同时", "此外", "另外",
    "近日", "近期", "今年", "去年", "今日", "昨日", "明日",
    "一个", "一种", "这个", "那个", "什么", "怎么", "如何",
    "可以", "能够", "需要", "应该", "必须", "一定",
    "大家", "市场", "行情", "走势", "板块", "概念",
}


# ═══════════════════════════════════════════════════════════════
# 3. 中文分词
# ═══════════════════════════════════════════════════════════════
def segment_and_filter(text: str) -> list[str]:
    """jieba 分词 + 停用词过滤"""
    if not text:
        return []
    if HAS_JIEBA:
        words = jieba.lcut(text)
    else:
        # 降级: 按字符+2-gram
        chars = list(text)
        words = chars + [chars[i] + chars[i+1] for i in range(len(chars)-1)
                         if chars[i].strip() and chars[i+1].strip()]

    # 过滤: 长度>=2, 非纯数字, 非停用词, 非纯标点
    result = []
    for w in words:
        w = w.strip()
        if len(w) < 2:
            continue
        if w in STOP_WORDS:
            continue
        if re.match(r"^[\d\.\%\-\+]+$", w):
            continue
        if re.match(r"^[^\w\u4e00-\u9fff]+$", w):
            continue
        result.append(w)
    return result


# ═══════════════════════════════════════════════════════════════
# 4. 金融情感词典
# ═══════════════════════════════════════════════════════════════
POSITIVE_WORDS = {
    "涨停", "拉升", "大涨", "暴涨", "突破", "新高", "反弹",
    "利好", "回购", "增持", "分红", "盈利", "增长", "超预期",
    "繁荣", "活跃", "复苏", "企稳", "走强", "强势", "领涨",
    "龙头", "热门", "追捧", "抢筹", "放量", "净流入",
    "政策", "支持", "扶持", "补贴", "降准", "降息",
    "创新", "突破", "领先", "优势", "签约", "中标",
    "扭亏", "预增", "业绩", "向好",
}

NEGATIVE_WORDS = {
    "跌停", "暴跌", "大跌", "下挫", "跳水", "走弱", "低迷",
    "利空", "亏损", "违约", "暴雷", "减持", "套现", "崩盘",
    "泡沫", "衰退", "萎缩", "下滑", "恶化", "承压", "拖累",
    "监管", "处罚", "调查", "诉讼", "退市", "警示",
    "谨慎", "悲观", "恐慌", "抛售", "出逃", "净流出",
    "贸易战", "制裁", "关税", "冲突", "危机", "风险",
    "ST", "退市风险", "业绩", "预亏",
    "跌幅", "跌超", "下跌", "回落", "重挫", "走低", "收跌",
}


def analyze_sentiment(text: str) -> tuple[float, str]:
    """
    金融情感分析
    返回: (score, label)  score ∈ [-1, 1], label ∈ {positive, neutral, negative}
    """
    if not text:
        return (0.0, "neutral")
    words = segment_and_filter(text)
    pos_count = sum(1 for w in words if w in POSITIVE_WORDS)
    neg_count = sum(1 for w in words if w in NEGATIVE_WORDS)
    total = pos_count + neg_count
    if total == 0:
        return (0.0, "neutral")
    score = round((pos_count - neg_count) / (total + 3), 4)  # 加平滑
    label = "positive" if score > 0.05 else "negative" if score < -0.05 else "neutral"
    return (score, label)


# ═══════════════════════════════════════════════════════════════
# 5. TF-IDF 关键词提取
# ═══════════════════════════════════════════════════════════════
def extract_keywords(texts: list[str], top_n: int = 30) -> list[tuple[str, float]]:
    """TF-IDF 批量关键词提取"""
    if not texts or not HAS_SKLEARN:
        return []
    try:
        cleaned = [clean_text(t) for t in texts if t]
        if not cleaned:
            return []
        # 中文需要分词后输入
        if HAS_JIEBA:
            cleaned = [" ".join(segment_and_filter(t)) for t in cleaned]
        vec = TfidfVectorizer(max_df=0.8, min_df=2, max_features=100)
        tfidf = vec.fit_transform(cleaned)
        # 取全局 TF-IDF 均值最高的词
        scores = tfidf.mean(axis=0).A1
        words = vec.get_feature_names_out()
        ranked = sorted(zip(words, scores), key=lambda x: x[1], reverse=True)
        return [(w, round(s, 4)) for w, s in ranked[:top_n]]
    except Exception as e:
        logger.warning("TF-IDF failed: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════
# 6. 主题聚类
# ═══════════════════════════════════════════════════════════════
def topic_cluster(news_texts: list[str], n_clusters: int = 5) -> dict:
    """KMeans 主题聚类，返回每个簇的 top 8 关键词"""
    if not news_texts or not HAS_SKLEARN or len(news_texts) < n_clusters:
        return {"labels": [], "cluster_keywords": {}, "cluster_counts": {}}
    try:
        if HAS_JIEBA:
            cleaned = [" ".join(segment_and_filter(clean_text(t))) for t in news_texts]
        else:
            cleaned = [clean_text(t) for t in news_texts]
        vec = TfidfVectorizer(max_df=0.8, min_df=2, max_features=100)
        X = vec.fit_transform(cleaned)
        words = vec.get_feature_names_out()

        km = KMeans(n_clusters=min(n_clusters, len(news_texts)), random_state=42, n_init=10)
        labels = km.fit_predict(X)

        result = {"labels": labels.tolist(), "cluster_keywords": {}, "cluster_counts": {}}
        for c in range(km.n_clusters):
            center = km.cluster_centers_[c]
            top_idx = center.argsort()[-8:][::-1]
            result["cluster_keywords"][str(c)] = [words[i] for i in top_idx]
            result["cluster_counts"][str(c)] = int(sum(labels == c))
        return result
    except Exception as e:
        logger.warning("Topic clustering failed: %s", e)
        return {"labels": [], "cluster_keywords": {}, "cluster_counts": {}}


# ═══════════════════════════════════════════════════════════════
# 7. SimHash 新闻去重
# ═══════════════════════════════════════════════════════════════
def simhash(text: str) -> str:
    """简化 SimHash: 用 sha256 前 16 位作为近似指纹"""
    if not text:
        return ""
    cleaned = clean_text(text)[:500]  # 只用前 500 字符
    return hashlib.sha256(cleaned.encode()).hexdigest()[:16]


def deduplicate_news(news_items: list[dict]) -> list[dict]:
    """按 SimHash 去重 (保留最新)"""
    seen: dict[str, dict] = {}
    for item in sorted(news_items, key=lambda x: x.get("publish_time", ""), reverse=True):
        h = simhash(item.get("title", "") + (item.get("content") or "")[:200])
        if h not in seen:
            seen[h] = item
    return list(seen.values())


# ═══════════════════════════════════════════════════════════════
# 8. 实体映射 (股票代码/板块/概念)
# ═══════════════════════════════════════════════════════════════
# 预加载股票名称 → 代码映射 (在运行时从 DB 加载)
_stock_name_map: dict[str, str] = {}
_sector_name_map: dict[str, str] = {}


async def load_entity_maps():
    """从数据库加载实体映射表"""
    from infrastructure.database import postgres as db
    global _stock_name_map, _sector_name_map
    try:
        rows = await db.fetch_all("SELECT stock_code, stock_name FROM stocks WHERE is_active = TRUE")
        _stock_name_map = {r["stock_name"]: r["stock_code"] for r in rows if r["stock_name"]}
    except Exception as e:
        logger.warning("load entity maps failed: %s", e)


def extract_entities(text: str) -> dict[str, list[str]]:
    """
    从文本中提取股票代码和板块实体
    返回: {stocks: [code], sectors: [name]}
    """
    result: dict[str, list[str]] = {"stocks": [], "sectors": []}
    if not text:
        return result

    # 股票代码匹配: 6位数字
    codes = re.findall(r"\b(\d{6})\b", text)
    result["stocks"] = list(set(codes))[:10]

    # 股票名称匹配
    for name, code in _stock_name_map.items():
        if name in text and code not in result["stocks"]:
            result["stocks"].append(code)

    # 板块名称匹配
    for name, code in _sector_name_map.items():
        if name in text:
            result["sectors"].append(name)

    return result


# ═══════════════════════════════════════════════════════════════
# 9. 重要度评分
# ═══════════════════════════════════════════════════════════════
def score_importance(news_item: dict) -> int:
    """
    新闻重要度 0-5
    - 涉及龙头股 +2
    - 涉及政策/宏观 +1
    - 负面情绪 +1
    - 有具体数字 +1
    - 来源权威 +1
    """
    title = news_item.get("title", "")
    content = news_item.get("content", "")
    text = title + (content or "")
    score = 0

    # 龙头股关键词
    if re.search(r"茅台|宁德|平安|腾讯|阿里|字节|华为", text):
        score += 2
    # 政策/宏观
    if re.search(r"央行|美联储|政治局|国务院|证监会|降准|降息|GDP|PMI", text):
        score += 1
    # 负面情绪
    sentiment, _ = analyze_sentiment(text)
    if sentiment < -0.1:
        score += 1
    # 具体数字
    if re.search(r"\d+\.?\d*\s*[亿万亿%倍]", text):
        score += 1
    # 权威来源
    source = news_item.get("source", "")
    if source in ("新华社", "人民日报", "央视", "证监会", "央行"):
        score += 1

    return min(5, score)


# ═══════════════════════════════════════════════════════════════
# 10. 批量处理入口
# ═══════════════════════════════════════════════════════════════
async def process_news_batch(limit: int = 100) -> dict:
    """
    批量 NLP 处理: 从未处理的新闻中取一批，执行全流程，更新 DB
    """
    from infrastructure.database import postgres as db

    # 确保实体映射已加载
    if not _stock_name_map:
        await load_entity_maps()

    rows = await db.fetch_all("""
        SELECT news_id, title, content, publish_time, source, category
        FROM news
        WHERE is_processed = FALSE
        ORDER BY publish_time DESC
        LIMIT $1
    """, limit)

    if not rows:
        return {"processed": 0, "message": "no unprocessed news"}

    processed = 0
    for r in rows:
        try:
            title = r.get("title") or ""
            content = r.get("content") or ""
            text = title + " " + (content[:2000] if content else "")

            # 情感
            sentiment, _ = analyze_sentiment(text)

            # 关键词 (单条简化版)
            words = segment_and_filter(text)
            word_freq = Counter(words).most_common(10)
            keywords = [w for w, _ in word_freq]

            # 实体
            entities = extract_entities(text)

            # 重要度
            importance = score_importance(dict(r))

            # 更新 DB
            await db.execute("""
                UPDATE news
                SET sentiment = $2, keywords = $3, stocks = $4,
                    importance = $5, is_processed = TRUE
                WHERE news_id = $1
            """,
                r["news_id"], sentiment, keywords,
                entities.get("stocks", []), importance,
            )
            processed += 1
        except Exception as e:
            logger.debug("process news %s: %s", r.get("news_id"), e)

    logger.info("NLP processed: %d/%d news", processed, len(rows))
    return {"processed": processed, "total": len(rows)}


# ═══════════════════════════════════════════════════════════════
# 11. 分析报告生成
# ═══════════════════════════════════════════════════════════════
async def generate_analysis_report(hours: int = 2) -> dict:
    """生成最近 N 小时的新闻分析报告"""
    from infrastructure.database import postgres as db

    rows = await db.fetch_all("""
        SELECT title, content, sentiment, category, publish_time, source
        FROM news
        WHERE publish_time > NOW() - $1::interval
        ORDER BY publish_time DESC
        LIMIT 200
    """, f"{hours} hours")

    if not rows:
        return {"error": "no news in window"}

    texts = [(r.get("title") or "") + " " + (r.get("content") or "")[:500] for r in rows]

    # 情感分布
    sentiments = [r.get("sentiment") or 0 for r in rows]
    pos = sum(1 for s in sentiments if s > 0.05)
    neg = sum(1 for s in sentiments if s < -0.05)
    neu = len(sentiments) - pos - neg

    # 关键词
    keywords = extract_keywords(texts, top_n=20)

    # 主题
    topics = topic_cluster(texts)

    # 来源分布
    sources = Counter(r.get("source") or "未知" for r in rows)

    return {
        "analysis_time": datetime.now().isoformat(),
        "total_news": len(rows),
        "sentiment": {
            "positive": pos,
            "negative": neg,
            "neutral": neu,
            "avg_score": round(sum(sentiments) / max(len(sentiments), 1), 4),
        },
        "keywords": keywords,
        "topics": topics,
        "source_distribution": sources.most_common(10),
        "hours": hours,
    }
