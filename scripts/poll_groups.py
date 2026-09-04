"""
scripts/poll_groups.py — 新闻源采集节奏分级(一次性归类工具)
=============================================================
按"网站风格 × 内容需求"把 verified 源归入三档 poll.group：

  cn_fast — CN 财经快讯/盘中情报 (交易时段 120s, 盘后 600s)
  media   — 常规媒体/全球聚合 (恒定 300s)
  slow    — 低频/深度/技术社区站 (600s)

规则(可解释、可复核):
  - name ∈ CN_FAST_NAMES : 财经快讯型(金十/见闻实时 + GNews CN 财经媒体查询)
  - name ∈ SLOW_NAMES    : 低频深度/社区/垂类(日更级即够)
  - 其余 verified 源      : media

用法:
  python scripts/poll_groups.py --dry-run   # 只打印归类表核对, 不写配置
  python scripts/poll_groups.py --write     # 回写 config/sources_config.json 每源 poll.group
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources_config.json"

# ── 财经快讯型: 盘中价值高, 加密采集 ──
CN_FAST_NAMES = {
    # rsshub 快讯流
    "金十数据", "金十重要", "见闻实时",
    # GNews CN 财经媒体查询(实时财经情报)
    "财联社·查询替代", "东方财富·查询替代", "证券时报·查询替代",
    "每日经济·查询替代", "澎湃财经·查询替代", "中国金融·查询替代",
    "和讯财经·查询替代", "界面新闻", "界面·查询替代",
    "凤凰财经·查询替代", "一财全球", "东财策略", "FT中国",
    "中国证券网", "36氪·查询替代", "虎嗅", "虎嗅·查询替代",
}

# ── 低频/深度/技术社区站: 600s 足够, 减少源站压力 ──
SLOW_NAMES = {
    "少数派", "Solidot", "人人都是产品", "汽车之家",
    "InfoQ", "开源中国", "雷锋网", "极客公园", "极客公园·查询替代",
}

DEFAULT_GROUP = "media"


def classify(name: str) -> str:
    if name in CN_FAST_NAMES:
        return "cn_fast"
    if name in SLOW_NAMES:
        return "slow"
    return DEFAULT_GROUP


def load_sources() -> list[dict]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))["sources"]


def group_verified(sources: list[dict]) -> dict[str, list[dict]]:
    """只对 healthy_only 会采集的源归类(verified & enabled!=False)"""
    out: dict[str, list[dict]] = {}
    for s in sources:
        if s.get("_health") != "verified" or s.get("enabled") is False:
            continue
        g = classify(s["name"])
        out.setdefault(g, []).append(s)
    return out


def dry_run(sources: list[dict]) -> int:
    groups = group_verified(sources)
    total = sum(len(v) for v in groups.values())
    print(f"verified 源总数: {total}")
    for g in ("cn_fast", "media", "slow"):
        lst = sorted(s["name"] for s in groups.get(g, []))
        print(f"\n[{g}] {len(lst)} 个")
        for n in lst:
            print(f"   - {n}")
    # 完整性/非空校验
    assert total == sum(1 for s in sources
                        if s.get("_health") == "verified" and s.get("enabled") is not False), \
        "分组覆盖不完整"
    for g in ("cn_fast", "media", "slow"):
        assert len(groups.get(g, [])) > 0, f"分组 {g} 为空"
    # 名单源必须全部存在且 verified
    names = {s["name"] for s in sources if s.get("_health") == "verified"}
    missing = (CN_FAST_NAMES | SLOW_NAMES) - names
    assert not missing, f"名单中不存在/未verified: {missing}"
    print("\nOK 分组校验通过 (覆盖完整, 三组非空, 名单源全部存在)")
    return total


def write_back(sources: list[dict]) -> None:
    n_poll = 0
    for s in sources:
        if s.get("_health") == "verified" and s.get("enabled") is not False:
            s.setdefault("poll", {})["group"] = classify(s["name"])
            n_poll += 1
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    doc["sources"] = sources
    CONFIG.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"已回写 poll.group 到 {n_poll} 个源 -> {CONFIG}")


def main() -> None:
    ap = argparse.ArgumentParser(description="新闻源采集节奏分级")
    ap.add_argument("--dry-run", action="store_true", help="仅打印归类表核对")
    ap.add_argument("--write", action="store_true", help="回写 poll.group 到 config")
    args = ap.parse_args()
    srcs = load_sources()
    dry_run(srcs)
    if args.write:
        write_back(srcs)


if __name__ == "__main__":
    main()
