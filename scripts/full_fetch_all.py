# -*- coding: utf-8 -*-
"""全量采集入库：读取 sources_config.json 中 _health=verified 的源，逐源采集写入 news.db"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from news_gateway import fetch_one
from infrastructure.database import sqlite_news as db

CONFIG = ROOT / "config" / "sources_config.json"
LOG = ROOT / "docs" / "full_fetch_log.jsonl"


def main():
    db.init_db()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    gcfg = cfg.get("global", {})
    only = [s for s in cfg["sources"] if s.get("_health") == "verified"]
    print(f"待采集 verified 源: {len(only)} 个", flush=True)

    stats = {"ok": 0, "fail": 0, "fetched": 0, "inserted": 0, "failures": []}
    log_f = open(LOG, "a", encoding="utf-8")
    for i, sc in enumerate(only, 1):
        name = sc["name"]
        t0 = time.time()
        try:
            arts = fetch_one(sc, gcfg)
            inserted = db.insert_news_batch([a.to_db_row() for a in arts])
            stats["ok"] += 1
            stats["fetched"] += len(arts)
            stats["inserted"] += inserted
            print(f"[{i}/{len(only)}] ✅ {name:<20} 抓取{len(arts):>3} 新增{inserted:>3} "
                  f"{time.time()-t0:.0f}s", flush=True)
            log_f.write(json.dumps({"name": name, "ok": True, "fetched": len(arts),
                                    "inserted": inserted}) + "\n")
        except Exception as e:
            stats["fail"] += 1
            stats["failures"].append({"name": name, "error": str(e)})
            print(f"[{i}/{len(only)}] ❌ {name:<20} 失败: {str(e)[:80]}", flush=True)
            log_f.write(json.dumps({"name": name, "ok": False, "error": str(e)}) + "\n")
        log_f.flush()
        time.sleep(0.3)
    log_f.close()

    total = db.get_news_stats().get("total_news", 0)
    print(f"\n完成: 成功源{stats['ok']} 失败{stats['fail']} | "
          f"本轮抓取{stats['fetched']} 新增{stats['inserted']} | 库总数 {total}", flush=True)
    json.dump(stats, open(ROOT / "docs" / "full_fetch_summary.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
