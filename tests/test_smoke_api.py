"""
最小 API 冒烟测试(标准库 unittest,无需 pytest/第三方依赖)。

用法(需 API 已在运行):
    .venv\\Scripts\\python.exe -m unittest tests.test_smoke_api -v
    或指定地址:set SMOKE_BASE=http://127.0.0.1:8900

校验:/health、快照聚合、行情带、预测端点、前端页面可访问。
"""
import json
import os
import unittest
import urllib.request

BASE = os.environ.get("SMOKE_BASE", "http://219.224.5.250:8900")


def http_get(path: str):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


class TestSmokeAPI(unittest.TestCase):
    def _json(self, path: str):
        status, body = http_get(path)
        self.assertEqual(status, 200, f"{path} 应返回 200")
        return json.loads(body)

    def test_health(self):
        d = self._json("/health")
        self.assertTrue(d.get("ok"), "health.ok 应为 true")
        self.assertIn("db", d)

    def test_frontend_page(self):
        status, body = http_get("/frontend/index.html")
        self.assertEqual(status, 200)
        self.assertIn("6STOCKPULSE", body)

    def test_dashboard_snapshot(self):
        d = self._json("/api/dashboard/snapshot")
        self.assertIn("data", d)
        self.assertIsInstance(d.get("data"), dict)

    def test_market_tickers(self):
        d = self._json("/api/market/tickers")
        self.assertIsInstance(d, dict)

    def test_predictions_next_session(self):
        d = self._json("/api/predictions/next-session")
        self.assertTrue(d.get("ok"), "predictions 包络 ok 应为 true")

    def test_hotspots(self):
        d = self._json("/api/hotspots?limit=3")
        self.assertTrue(d.get("ok"), "hotspots 包络 ok 应为 true")
        for k in ("topics", "hot_news", "keywords"):
            self.assertIn(k, d.get("data", {}), f"hotspots.data 应含 {k}")

    def test_frontend_global_page(self):
        status, body = http_get("/frontend/global.html")
        self.assertEqual(status, 200)
        self.assertIn("6StockPulse", body)


if __name__ == "__main__":
    unittest.main()
