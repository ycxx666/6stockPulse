# -*- coding: utf-8 -*-
"""rsshub 源批量实测(轮换逻辑), 结果即时写入文件"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from news_gateway.adapters import RssHubAdapter

G = {"timeout": 5, "retries": 0, "request_interval": 0.3}
cfg = json.load(open("config/sources_config.json", encoding="utf-8"))
rh = [s for s in cfg["sources"] if s["type"] == "rsshub_rss" and s.get("enabled", True)]

out = []
for i, sc in enumerate(rh, 1):
    try:
        a = RssHubAdapter(sc, G)
        arts = a.fetch()
        out.append({"name": sc["name"], "ok": bool(arts), "count": len(arts)})
        print(f"[{i}/{len(rh)}] {'✅' if arts else '❌'} {sc['name']:<16} {len(arts)}条", flush=True)
    except Exception as e:
        out.append({"name": sc["name"], "ok": False, "count": 0, "err": str(e)[:60]})
        print(f"[{i}/{len(rh)}] ⚠️ {sc['name']}: {type(e).__name__}", flush=True)

Path("docs/rsshub_recheck.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
ok = [r for r in out if r["ok"]]
print(f"\n汇总: 可采集 {len(ok)} / 总 {len(out)}")
