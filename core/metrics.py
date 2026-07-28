"""
core/metrics.py — Prometheus 指标暴露
=====================================
在 FastAPI app 中通过 /metrics 端点暴露指标。
需要 prometheus_client 依赖。
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest

    crawler_requests = Counter(
        "crawler_requests_total", "Total crawler requests",
        ["source", "type", "status"]
    )
    api_latency = Histogram(
        "api_latency_seconds", "API request latency",
        ["endpoint"]
    )
    ws_clients = Gauge(
        "ws_clients_connected", "Active WebSocket clients"
    )
    db_connections = Gauge(
        "db_connections_active", "Active database connections"
    )
    METRICS_AVAILABLE = True

except ImportError:
    METRICS_AVAILABLE = False

    def generate_latest():
        return b"# prometheus_client not installed\n"
