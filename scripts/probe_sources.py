# -*- coding: utf-8 -*-
"""全源健康探测：对 sources_config.json 的每个源做可达性+内容检查，输出 JSON 报告"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "config" / "sources_config.json"
OUT = ROOT / "docs" / "source_health_report.json"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,*/*;q=0.8"}

SEM = asyncio.Semaphore(10)  # 并发 10
PROXY: str | None = None     # 由 main 注入


async def probe(name, url, stype, fixes=None, timeout=8):
    async with SEM:
        # 与网关适配器一致：先应用 fixes（ensure_scheme / strip_trailing_dot / correct_ap_domain...）
        from news_gateway.fixes import smart_fix
        url = smart_fix(url, fixes)
        rec = {"name": name, "type": stype, "url": url,
               "reachable": False, "http": None, "content_ok": False,
               "bytes": 0, "issue": None, "issue_type": None}
        # Google News 类: 主 URL 探测失败时无意义(需代理), 直接尝试一次
        try:
            async with aiohttp.ClientSession(headers=UA,
                                             trust_env=(PROXY is None)) as s:
                kw = dict(timeout=aiohttp.ClientTimeout(total=timeout),
                          ssl=False, allow_redirects=True)
                if PROXY:
                    kw["proxy"] = PROXY
                async with s.get(url, **kw) as r:
                    rec["reachable"] = True
                    rec["http"] = r.status
                    if r.status >= 400:
                        rec["issue"] = f"HTTP {r.status}"
                        rec["issue_type"] = "http-error"
                        return rec
                    body = await r.text(errors="ignore")
                    rec["bytes"] = len(body)
                    head = body[:600].lstrip()
                    # 判定内容是否为有效 feed/页面
                    is_xml = head.startswith("<?xml") or "<rss" in head or "<feed" in head or "rdf:RDF" in head
                    is_html = "<html" in head.lower() or "<!doctype" in head.lower()
                    if rec["bytes"] < 200:
                        rec["issue"] = "响应过短(疑似空/反爬占位)"
                        rec["issue_type"] = "empty"
                    elif "Access Denied" in body[:2000] or "cf-challenge" in body[:3000].lower() or "cf-ray" in body[:3000].lower():
                        rec["issue"] = "被WAF/反爬拦截(Access Denied/Cloudflare)"
                        rec["issue_type"] = "anti-bot"
                    elif ("阿里云" in body[:2000]) or ("aliyun" in body[:2000].lower() and "waf" in body[:2000].lower()):
                        rec["issue"] = "阿里云WAF拦截"
                        rec["issue_type"] = "anti-bot"
                    elif not (is_xml or is_html):
                        rec["issue"] = "非XML非HTML(可能是登录页/JSON/纯文本)"
                        rec["issue_type"] = "unexpected"
                    else:
                        rec["content_ok"] = True
        except asyncio.TimeoutError:
            rec["issue"] = "连接超时(8s)"
            rec["issue_type"] = "timeout"
        except aiohttp.ClientError as e:
            rec["issue"] = f"请求失败: {type(e).__name__}"
            rec["issue_type"] = "client-error"
        except Exception as e:
            rec["issue"] = f"异常: {type(e).__name__}: {e}"
            rec["issue_type"] = "exception"
        return rec


async def main():
    import argparse
    ap = argparse.ArgumentParser(description="全源健康探测")
    ap.add_argument("--proxy", help='HTTP/HTTPS 代理, 如 "http://127.0.0.1:7890"')
    ap.add_argument("--only", nargs="*", help="只探测指定源 name")
    ap.add_argument("--out", help="输出报告路径(默认 docs/source_health_report.json)")
    args = ap.parse_args()

    global PROXY
    PROXY = args.proxy
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    sources = cfg["sources"]
    if args.only:
        want = set(args.only)
        sources = [s for s in sources if s["name"] in want]
    out = Path(args.out) if args.out else OUT
    print(f"探测 {len(sources)} 个源, 并发10, 超时8s, proxy={PROXY or '直连'} ...")
    t0 = time.time()
    results = await asyncio.gather(*[probe(s["name"], s["url"], s["type"], s.get("fixes")) for s in sources])
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = [r for r in results if r["content_ok"]]
    bad = [r for r in results if not r["content_ok"]]
    print(f"完成 {time.time()-t0:.0f}s | 内容有效 {len(ok)} / 有问题 {len(bad)}")
    print(f"报告: {out}")

if __name__ == "__main__":
    asyncio.run(main())
