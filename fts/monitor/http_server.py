"""
fts.monitor.http_server — 轻量监控 HTTP 服务器。

纯标准库实现，零额外依赖。
端点:
    GET /health    → 200 OK + JSON 状态
    GET /metrics   → Prometheus 文本格式指标
    GET /          → 简易 HTML 仪表盘

用法:
    server = MetricsHTTPServer()
    server.start()  # 非阻塞线程

版本: v0.1.0
"""

from __future__ import annotations

import json
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 全局指标注册表（简易，无 Prometheus client 依赖时使用）
_metrics: dict[str, Any] = {
    "fts_elite_factor_count": 0,
    "fts_loop_status_L1": 0,
    "fts_loop_status_L2": 0,
    "fts_loop_status_L3": 0,
    "fts_tokens_consumed": 0,
    "fts_combo_sharpe": 0.0,
    "fts_started_at": time.time(),
}
_metrics_lock = None  # 可在外部替换为 threading.Lock()


def set_metric(name: str, value: Any) -> None:
    """设置指标值。"""
    global _metrics  # noqa: PLW0603
    _metrics[name] = value


def get_metric(name: str, default: Any = 0) -> Any:
    """获取指标值。"""
    return _metrics.get(name, default)


class _MetricsHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。"""
    
    def log_message(self, fmt, *args):
        logger.debug("HTTP %s", fmt % args)
    
    def _respond_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())
    
    def _respond_text(self, text: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode())
    
    def _respond_html(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())
    
    def do_GET(self):  # noqa: N802
        path = self.path.rstrip("/")
        
        if path == "/health":
            self._respond_json({
                "status": "ok",
                "uptime_seconds": int(time.time() - _metrics.get("fts_started_at", time.time())),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        elif path == "/metrics":
            lines = []
            for key, value in _metrics.items():
                if isinstance(value, (int, float)):
                    lines.append(f"# HELP {key} FTS metric")
                    lines.append(f"# TYPE {key} gauge")
                    lines.append(f"{key} {value}")
            self._respond_text("\n".join(lines))
        elif path == "" or path == "/":
            html = "<html><head><title>FTS Monitor</title></head><body>"
            html += "<h1>FTS System Monitor</h1>"
            html += "<table border='1'><tr><th>Metric</th><th>Value</th></tr>"
            for key, value in sorted(_metrics.items()):
                html += f"<tr><td>{key}</td><td>{value}</td></tr>"
            html += "</table></body></html>"
            self._respond_html(html)
        else:
            self._respond_json({"error": "not found"}, 404)


class MetricsHTTPServer:
    """轻量监控 HTTP 服务器。"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 9100):
        self.host = host
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[Thread] = None
        self._running = False
    
    def start(self) -> None:
        """启动 HTTP 服务器（非阻塞线程）。"""
        if self._running:
            logger.warning("[monitor] HTTP server already running")
            return
        try:
            self._server = HTTPServer((self.host, self.port), _MetricsHandler)
            self._thread = Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            self._running = True
            logger.info("[monitor] HTTP server started: http://%s:%d/metrics", self.host, self.port)
        except OSError as e:
            logger.error("[monitor] HTTP server failed: %s", e)
    
    def stop(self) -> None:
        """停止 HTTP 服务器。"""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self._running = False
        logger.info("[monitor] HTTP server stopped")
    
    @property
    def running(self) -> bool:
        return self._running


__all__ = [
    "MetricsHTTPServer",
    "set_metric",
    "get_metric",
    "_metrics",
]
