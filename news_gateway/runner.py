"""
news_gateway.runner — 主入口
=============================
fetch_all_sources(config_path) 遍历配置中的每个源：
    工厂实例化适配器 → 抓取 → 标准化 UnifiedArticle → 存入 SQLite(或打印)

配置字段约定: 见 config/sources_config.json
  - 每个源可含: name / region / type / url / fixes / fallback / parser / auth
  - parser.fulltext_enrich=true 时, 对 cleaned_text<200 字的条目抓原文补全
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from .factory import SourceFactory, get_available_types
from .models import UnifiedArticle

logger = logging.getLogger(__name__)

# 可选全文增强（依赖 bs4 + requests，已装）
try:
    from bs4 import BeautifulSoup
    import requests as _rq
    _HAS_ENRICH = True
except Exception:  # pragma: no cover
    _HAS_ENRICH = False

# 可选入库
try:
    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from infrastructure.database import sqlite_news as _db
    _HAS_DB = True
except Exception:  # pragma: no cover
    _HAS_DB = False


def _load_config(config_path: str | Path) -> dict:
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _filter_sources(sources: list[dict], only: Optional[list[str]] = None,
                    healthy_only: bool = False,
                    group: Optional[str] = None) -> tuple[list[dict], list[str]]:
    """筛选可采集源。

    - only 显式指定(name)时: 视为手动调试, 不应用 enabled/_health/group 过滤;
    - 否则默认跳过 disabled / paused 源(排除需人工维护登录态等有风险源);
    - healthy_only=True(定时任务): 仅采 _health=verified 的零风险源;
    - group=非空(分档任务): 仅采 poll.group == group 的源(cn_fast/media/slow)。
    返回 (待采源, 被跳过源 name 列表)
    """
    if only:
        wanted = set(only)
        return ([s for s in sources if s.get("name") in wanted],
                [s.get("name") for s in sources if s.get("name") not in wanted])
    out, skipped = [], []
    for s in sources:
        if s.get("enabled") is False or s.get("_health") == "paused":
            skipped.append(s.get("name", "?")); continue
        if healthy_only and s.get("_health") != "verified":
            skipped.append(s.get("name", "?")); continue
        if group and (s.get("poll") or {}).get("group") != group:
            skipped.append(s.get("name", "?")); continue
        out.append(s)
    return out, skipped


def _enrich_fulltext(article: UnifiedArticle, timeout: int = 15) -> None:
    """cleaned_text 过短(<200字)时抓原文补全。失败静默，保留原 summary。"""
    if not _HAS_ENRICH or len(article.cleaned_text) >= 200:
        return
    link = article.link
    if not link or not link.startswith("http"):
        return
    try:
        resp = _rq.get(link, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"})
        if resp.status_code >= 400:
            return
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in ("script", "style", "nav", "footer", "aside", "header"):
            for node in soup.find_all(tag):
                node.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = "\n".join(p for p in paras if len(p) > 30)
        if len(text) >= len(article.cleaned_text):
            article.cleaned_text = text[:8000]
    except Exception as e:
        logger.debug("[%s] 全文增强失败: %s", article.source, e)


def fetch_one(source_cfg: dict, global_cfg: dict | None = None) -> list[UnifiedArticle]:
    """抓取单个源并标准化"""
    adapter = SourceFactory.create(source_cfg, global_cfg)
    articles = adapter.fetch()
    # 可选全文增强
    parser = source_cfg.get("parser") or {}
    if parser.get("fulltext_enrich"):
        for a in articles:
            _enrich_fulltext(a, timeout=global_cfg.get("timeout", 15) if global_cfg else 15)
    return articles


def _persist(articles: list[UnifiedArticle], db_ok: bool) -> int:
    """入库 SQLite（复用 sqlite_news）；未装依赖则仅记录日志"""
    if not db_ok or not _HAS_DB:
        return 0
    rows = [a.to_db_row() for a in articles]
    return _db.insert_news_batch(rows)


def fetch_all_sources(config_path: str | Path = "config/sources_config.json",
                      only: Optional[list[str]] = None,
                      persist: bool = True,
                      print_result: bool = True,
                      proxy: Optional[str] = None,
                      healthy_only: bool = False,
                      group: Optional[str] = None) -> dict[str, Any]:
    """
    遍历全部(或指定)源执行采集。

    参数:
      config_path   : 配置文件路径
      only          : 仅采集这些 name 的源（None=全部）
      persist       : 是否写入 SQLite news.db
      print_result  : 是否打印汇总
      proxy         : HTTP/HTTPS 代理地址, 如 "http://127.0.0.1:7890" (None=用配置)
    返回:
      {"by_source": {name: {"fetched": n, "inserted": m}}, "total": ..., "errors": [...]}
    """
    cfg = _load_config(config_path)
    global_cfg = dict(cfg.get("global", {}))
    if proxy:
        global_cfg["proxy"] = proxy
    sources, skipped = _filter_sources(
        cfg.get("sources", []), only, healthy_only, group)
    if skipped:
        shown = ", ".join(skipped[:10]) + ("…" if len(skipped) > 10 else "")
        logger.info("[gateway] 跳过 %d 个非采集源(disabled/paused/非verified): %s",
                    len(skipped), shown)
    if not sources:
        raise ValueError("配置中没有源 (或 only 过滤后为空)")

    summary: dict[str, Any] = {"by_source": {}, "total": 0, "inserted": 0, "errors": []}
    for sc in sources:
        name = sc.get("name", "?")
        try:
            arts = fetch_one(sc, global_cfg)
            inserted = _persist(arts, persist) if persist else 0
            summary["by_source"][name] = {"fetched": len(arts), "inserted": inserted}
            summary["total"] += len(arts)
            summary["inserted"] += inserted
            if print_result:
                print(f"  ✅ {name:<16} 抓取 {len(arts):>3} 条 | 入库 {inserted}")
        except Exception as e:
            summary["errors"].append({"name": name, "error": str(e)})
            if print_result:
                print(f"  ❌ {name:<16} 失败: {e}")
    return summary


# ── CLI ───────────────────────────────────────────────

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="统一新闻源接入窗口")
    ap.add_argument("--config", default="config/sources_config.json",
                    help="配置文件路径")
    ap.add_argument("--only", nargs="*", help="只采集指定源 name")
    ap.add_argument("--no-db", action="store_true", help="不入库，仅打印")
    ap.add_argument("--types", action="store_true", help="列出可用适配器类型")
    ap.add_argument("--proxy", help='HTTP/HTTPS 代理, 如 "http://127.0.0.1:7890"')
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S")

    if args.types:
        print("可用适配器类型:", get_available_types())
        return

    print(f"📡 统一新闻源接入窗口  (persist={'SQLite' if not args.no_db else '关闭'}"
          f"{' | proxy=' + args.proxy if args.proxy else ''})")
    print("=" * 60)
    summary = fetch_all_sources(
        config_path=args.config,
        only=args.only,
        persist=not args.no_db,
        print_result=True,
        proxy=args.proxy,
    )
    print("=" * 60)
    print(f"合计: 抓取 {summary['total']} 条 | 入库 {summary['inserted']} 条 | 错误 {len(summary['errors'])}")


if __name__ == "__main__":
    main()
