"""
6stockPulse · SQLite 新闻数据库
================================
轻量级嵌入式数据库，无需 PostgreSQL。
用于存储每次采集的新闻数据，支持增删查操作。

表结构:
  - news         : 7×24 财经快讯（主表）
  - stock_news   : 个股新闻
  - stock_ann    : 个股公告
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# 数据库文件路径（项目根目录下）
DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "news.db"

# 线程本地连接（SQLite 连接不可跨线程共享）
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """事务上下文"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ────────── 建表 ──────────

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS news (
    news_id       TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    content       TEXT,
    summary       TEXT,
    publish_time  TEXT NOT NULL,          -- ISO 格式时间戳
    source        TEXT,
    region        TEXT DEFAULT 'GLOBAL',  -- GLOBAL/US/CN: 新闻源地理归属
    source_url    TEXT,
    category      TEXT DEFAULT 'other',   -- macro/policy/company/global/other
    stocks        TEXT DEFAULT '[]',      -- JSON 数组 ["000001","600519"]
    keywords      TEXT DEFAULT '[]',      -- JSON 数组
    sentiment     REAL,                   -- -1 ~ +1
    importance    INTEGER DEFAULT 0,      -- 0-5
    crawl_time    TEXT DEFAULT (datetime('now','localtime')),
    is_processed  INTEGER DEFAULT 0       -- 0=未处理, 1=已处理
);

CREATE INDEX IF NOT EXISTS idx_news_publish ON news(publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);

CREATE TABLE IF NOT EXISTS stock_news (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code    TEXT NOT NULL,
    news_id       TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT,
    publish_time  TEXT NOT NULL,
    source        TEXT,
    source_url    TEXT,
    category      TEXT DEFAULT 'company',
    sentiment     REAL,
    read_count    INTEGER DEFAULT 0,
    crawl_time    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(stock_code, news_id)
);

CREATE INDEX IF NOT EXISTS idx_stock_news_code ON stock_news(stock_code, publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_stock_news_time ON stock_news(publish_time DESC);

CREATE TABLE IF NOT EXISTS stock_ann (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code    TEXT NOT NULL,
    ann_id        TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT,
    publish_time  TEXT NOT NULL,
    ann_type      TEXT,
    pdf_url       TEXT,
    source_url    TEXT,
    crawl_time    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(stock_code, ann_id)
);

CREATE INDEX IF NOT EXISTS idx_stock_ann_code ON stock_ann(stock_code, publish_time DESC);
"""


def init_db():
    """初始化数据库，创建表结构 + 幂等迁移(兼容旧库)"""
    conn = get_conn()
    conn.executescript(CREATE_TABLES_SQL)
    conn.commit()
    _migrate_legacy(conn)
    conn.commit()
    logger.info("SQLite 新闻数据库已初始化: %s", DB_PATH)


def _migrate_legacy(conn) -> None:
    """旧版库补 region 列 + region 索引（幂等）"""
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(news)").fetchall()}
        if "region" not in cols:
            conn.execute("ALTER TABLE news ADD COLUMN region TEXT DEFAULT 'GLOBAL'")
            logger.info("迁移: news 表新增 region 列")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_region ON news(region)")
    except Exception as e:
        logger.warning("news 表 region 迁移跳过: %s", e)


# ────────── 插入 / 更新 ──────────

def insert_news(row: dict) -> bool:
    """插入一条新闻，已存在则跳过"""
    try:
        with transaction() as conn:
            row = _prepare_row(row)
            cur = conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO news
                    (news_id, title, content, summary, publish_time,
                     source, region, source_url, category, stocks, keywords,
                     sentiment, importance, crawl_time)
                VALUES
                    (:news_id, :title, :content, :summary, :publish_time,
                     :source, :region, :source_url, :category, :stocks, :keywords,
                     :sentiment, :importance,
                     COALESCE(:crawl_time, datetime('now','localtime')))
            """, row)
            return cur.rowcount > 0
    except Exception as e:
        logger.error("插入新闻失败 %s: %s", row.get("news_id", "?"), e)
        return False


def insert_news_batch(rows: list[dict]) -> int:
    """批量插入新闻，返回成功插入条数"""
    if not rows:
        return 0
    count = 0
    try:
        with transaction() as conn:
            cur = conn.cursor()
            for row in rows:
                row = _prepare_row(row)
                cur.execute("""
                    INSERT OR IGNORE INTO news
                        (news_id, title, content, summary, publish_time,
                         source, region, source_url, category, stocks, keywords,
                         sentiment, importance, crawl_time)
                    VALUES
                        (:news_id, :title, :content, :summary, :publish_time,
                         :source, :region, :source_url, :category, :stocks, :keywords,
                         :sentiment, :importance,
                         COALESCE(:crawl_time, datetime('now','localtime')))
                """, row)
                if cur.rowcount > 0:
                    count += 1
        return count
    except Exception as e:
        logger.error("批量插入新闻失败: %s", e)
        return count


def insert_stock_news_batch(rows: list[dict]) -> int:
    """批量插入个股新闻"""
    if not rows:
        return 0
    count = 0
    try:
        with transaction() as conn:
            cur = conn.cursor()
            for row in rows:
                row = _prepare_row(row)
                cur.execute("""
                    INSERT OR IGNORE INTO stock_news
                        (stock_code, news_id, title, content, publish_time,
                         source, source_url, category, sentiment, read_count, crawl_time)
                    VALUES
                        (:stock_code, :news_id, :title, :content, :publish_time,
                         :source, :source_url, :category, :sentiment, :read_count,
                         COALESCE(:crawl_time, datetime('now','localtime')))
                """, row)
                if cur.rowcount > 0:
                    count += 1
        return count
    except Exception as e:
        logger.error("批量插入个股新闻失败: %s", e)
        return count


def upsert_news(row: dict) -> bool:
    """插入或更新新闻（以 news_id 为准）"""
    try:
        with transaction() as conn:
            row = _prepare_row(row)
            conn.execute("""
                INSERT INTO news
                    (news_id, title, content, summary, publish_time,
                     source, region, source_url, category, stocks, keywords,
                     sentiment, importance, crawl_time)
                VALUES
                    (:news_id, :title, :content, :summary, :publish_time,
                     :source, :region, :source_url, :category, :stocks, :keywords,
                     :sentiment, :importance,
                     COALESCE(:crawl_time, datetime('now','localtime')))
                ON CONFLICT(news_id) DO UPDATE SET
                    title    = COALESCE(EXCLUDED.title, news.title),
                    content  = COALESCE(EXCLUDED.content, news.content),
                    category = COALESCE(EXCLUDED.category, news.category),
                    source   = COALESCE(EXCLUDED.source, news.source),
                    region   = COALESCE(EXCLUDED.region, news.region),
                    source_url = COALESCE(EXCLUDED.source_url, news.source_url)
            """, row)
            return True
    except Exception as e:
        logger.error("更新新闻失败 %s: %s", row.get("news_id", "?"), e)
        return False


# ────────── 查询 ──────────

def query_news(
    category: Optional[str] = None,
    source: Optional[str] = None,
    region: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """查询新闻，支持按分类/来源/地区/关键词筛选，按时间倒序"""
    conditions = []
    params: list[Any] = []

    if category:
        conditions.append("category = ?")
        params.append(category)
    if source:
        conditions.append("source = ?")
        params.append(source)
    if region:
        conditions.append("region = ?")
        params.append(region.upper())
    if keyword:
        conditions.append("(title LIKE ? OR content LIKE ?)")
        params.append(f"%{keyword}%")
        params.append(f"%{keyword}%")
    # 排除过短内容
    conditions.append("length(COALESCE(content, title)) > 5")

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT * FROM news
        WHERE {where}
        ORDER BY publish_time DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    try:
        conn = get_conn()
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("查询新闻失败: %s", e)
        return []


def query_stock_news(
    stock_code: Optional[str] = None,
    limit: int = 30,
    offset: int = 0,
) -> list[dict]:
    """查询个股新闻"""
    if stock_code:
        sql = """
            SELECT * FROM stock_news
            WHERE stock_code = ?
            ORDER BY publish_time DESC
            LIMIT ? OFFSET ?
        """
        params = [stock_code, limit, offset]
    else:
        sql = """
            SELECT * FROM stock_news
            ORDER BY publish_time DESC
            LIMIT ? OFFSET ?
        """
        params = [limit, offset]
    try:
        conn = get_conn()
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("查询个股新闻失败: %s", e)
        return []


def get_news_stats() -> dict:
    """获取新闻统计数据（含来源/地区分布）"""
    try:
        conn = get_conn()
        total = conn.execute("SELECT COUNT(*) AS n FROM news").fetchone()["n"]
        by_category = {
            r["category"]: r["n"]
            for r in conn.execute(
                "SELECT category, COUNT(*) AS n FROM news GROUP BY category"
            ).fetchall()
        }
        by_region = {
            r["region"]: r["n"]
            for r in conn.execute(
                "SELECT region, COUNT(*) AS n FROM news GROUP BY region"
            ).fetchall()
        }
        by_source = {
            r["source"]: r["n"]
            for r in conn.execute(
                "SELECT source, COUNT(*) AS n FROM news GROUP BY source "
                "ORDER BY n DESC LIMIT 30"
            ).fetchall()
        }
        today_count = conn.execute(
            "SELECT COUNT(*) AS n FROM news WHERE date(publish_time) = date('now')"
        ).fetchone()["n"]
        return {
            "total_news": total,
            "by_category": by_category,
            "by_region": by_region,
            "by_source": by_source,
            "today": today_count,
            "db_path": str(DB_PATH),
        }
    except Exception as e:
        logger.error("获取新闻统计失败: %s", e)
        return {"error": str(e)}


def _prepare_row(row: dict) -> dict:
    """预处理行数据：将列表转为 JSON 字符串，补全所有可能缺失的 SQL 字段"""
    prepared = dict(row)
    for field in ("stocks", "keywords"):
        if field in prepared and isinstance(prepared[field], list):
            prepared[field] = json.dumps(prepared[field], ensure_ascii=False)
    # 时间字段转字符串
    for field in ("publish_time", "crawl_time"):
        if field in prepared and isinstance(prepared.get(field), datetime):
            prepared[field] = prepared[field].isoformat()
    # 补全所有可能缺失的字段（SQLite 命名参数要求全部存在）
    defaults = {
        "news_id": "",
        "title": "",
        "content": None,
        "summary": None,
        "publish_time": None,
        "source": None,
        "region": "GLOBAL",
        "source_url": None,
        "category": "other",
        "stocks": "[]",
        "keywords": "[]",
        "sentiment": None,
        "importance": 0,
        "crawl_time": None,
    }
    for k, v in defaults.items():
        prepared.setdefault(k, v)
    return prepared


# ────────── 工具：查看最近新闻 ──────────
def show_recent(limit: int = 10) -> None:
    """打印最近新闻（命令行工具用）"""
    rows = query_news(limit=limit)
    if not rows:
        print("暂无新闻数据。")
        return
    print(f"\n{'='*60}")
    print(f"最近 {limit} 条新闻 (共 {get_news_stats().get('total_news', 0)} 条)")
    print(f"{'='*60}")
    for i, r in enumerate(rows, 1):
        cat = r.get("category", "?").ljust(8)
        src = (r.get("source") or "?")[:12].ljust(12)
        title = (r.get("title") or "")[:60]
        print(f"  {i:2d}. [{cat}] {src} {title}")
    print(f"{'='*60}\n")
