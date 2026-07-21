"""tests/test_http_server.py — MetricsHTTPServer 测试。

HARNESS §测试随重构: 覆盖 http_server.py 核心路径。
"""

from __future__ import annotations

import json
import time
from http.server import HTTPServer
from io import BytesIO
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from fts.monitor.http_server import (
    MetricsHTTPServer,
    _metrics,
    _MetricsHandler,
    get_metric,
    set_metric,
)


# ─── set_metric / get_metric ────────────────────────────


class TestMetricsAPI:
    """全局指标 API 测试。"""

    def test_set_and_get_metric(self):
        """set_metric 和 get_metric 正常读写。"""
        set_metric("test_metric", 42)
        assert get_metric("test_metric") == 42

    def test_get_metric_default(self):
        """get_metric 返回默认值。"""
        assert get_metric("nonexistent", default=-1) == -1
        assert get_metric("nonexistent") == 0

    def test_get_metric_existing_default_ignored(self):
        """已有指标忽略 default 参数。"""
        set_metric("my_metric", 100)
        assert get_metric("my_metric", default=999) == 100

    def test_set_metric_overwrite(self):
        """set_metric 覆盖已有值。"""
        set_metric("dynamic", 1)
        set_metric("dynamic", 2)
        assert get_metric("dynamic") == 2


# ─── MetricsHTTPServer ──────────────────────────────────


class TestMetricsHTTPServerInit:
    """初始化测试。"""

    def test_default_host_port(self):
        """默认 host='127.0.0.1', port=9100。"""
        server = MetricsHTTPServer()
        assert server.host == "127.0.0.1"
        assert server.port == 9100

    def test_custom_host_port(self):
        """自定义 host 和 port。"""
        server = MetricsHTTPServer(host="0.0.0.0", port=8080)
        assert server.host == "0.0.0.0"
        assert server.port == 8080

    def test_initial_state(self):
        """初始状态。"""
        server = MetricsHTTPServer()
        assert server._server is None
        assert server._thread is None
        assert server.running is False


class TestMetricsHTTPServerStartStop:
    """启动/停止测试。"""

    @patch("fts.monitor.http_server.HTTPServer")
    def test_start_creates_server(self, mock_httpserver):
        """start 创建 HTTPServer 实例。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = MetricsHTTPServer()
        server.start()

        mock_httpserver.assert_called_once_with(("127.0.0.1", 9100), _MetricsHandler)
        assert server.running is True

    @patch("fts.monitor.http_server.HTTPServer")
    def test_start_idempotent(self, mock_httpserver):
        """多次 start 不重复创建。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = MetricsHTTPServer()
        server.start()
        server.start()  # 第二次应跳过

        mock_httpserver.assert_called_once()
        mock_server_instance.serve_forever.assert_called_once()

    @patch("fts.monitor.http_server.HTTPServer")
    def test_stop_after_start(self, mock_httpserver):
        """start 后 stop 应正确关闭。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = MetricsHTTPServer()
        server.start()
        server.stop()

        mock_server_instance.shutdown.assert_called_once()
        assert server.running is False
        assert server._server is None

    @patch("fts.monitor.http_server.HTTPServer")
    def test_stop_idempotent(self, mock_httpserver):
        """stop 多次调用不抛异常。"""
        server = MetricsHTTPServer()
        server.stop()  # _server 为 None
        server.stop()  # 再次调用

    @patch("fts.monitor.http_server.HTTPServer")
    def test_running_property(self, mock_httpserver):
        """running 属性反映状态。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = MetricsHTTPServer()
        assert server.running is False

        server.start()
        assert server.running is True

        server.stop()
        assert server.running is False

    @patch("fts.monitor.http_server.HTTPServer", side_effect=OSError("port in use"))
    def test_start_failure_on_port(self, mock_httpserver):
        """端口被占用时 start 不抛出异常。"""
        server = MetricsHTTPServer(port=9999)
        server.start()  # 不应抛出
        assert server.running is False


# ─── _MetricsHandler ────────────────────────────────────


class MockRequestHandler:
    """模拟 _MetricsHandler 所需环境。"""

    @staticmethod
    def make_handler(method="GET", path="/health"):
        """创建 mock handler 实例。"""
        handler = MagicMock(spec=_MetricsHandler)
        handler.command = method
        handler.path = path
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = BytesIO()
        handler._respond_json = _MetricsHandler._respond_json.__get__(handler, _MetricsHandler)
        handler._respond_text = _MetricsHandler._respond_text.__get__(handler, _MetricsHandler)
        handler._respond_html = _MetricsHandler._respond_html.__get__(handler, _MetricsHandler)
        handler.do_GET = _MetricsHandler.do_GET.__get__(handler, _MetricsHandler)
        return handler


class TestMetricsHandler:
    """_MetricsHandler HTTP 端点测试。"""

    def test_health_endpoint_json(self):
        """GET /health 返回 JSON 含 status=ok。"""
        handler = MockRequestHandler.make_handler(path="/health")
        with (
            patch("fts.monitor.http_server.time.time", return_value=1000),
            patch("fts.monitor.http_server.time.strftime", return_value="2026-07-19T12:00:00"),
            patch("fts.monitor.http_server._metrics", {"fts_started_at": 500}),
        ):
            handler.do_GET()

        # 验证 send_response 被调用
        handler.send_response.assert_called_once_with(200)
        # 验证 Content-Type
        handler.send_header.assert_any_call("Content-Type", "application/json")
        # 验证 body 内容
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["status"] == "ok"
        assert data["uptime_seconds"] == 500

    def test_metrics_endpoint_text(self):
        """GET /metrics 返回 Prometheus 文本格式。"""
        set_metric("test_counter", 123)
        handler = MockRequestHandler.make_handler(path="/metrics")
        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/plain; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        assert "# HELP test_counter FTS metric" in body
        assert "# TYPE test_counter gauge" in body
        assert "test_counter 123" in body

    def test_root_endpoint_html(self):
        """GET / 返回 HTML 仪表盘。"""
        handler = MockRequestHandler.make_handler(path="/")
        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/html; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        assert "<html>" in body
        assert "FTS System Monitor" in body
        assert "<table" in body

    def test_unknown_endpoint_404(self):
        """未知路径返回 404 JSON。"""
        handler = MockRequestHandler.make_handler(path="/unknown")
        handler.do_GET()

        handler.send_response.assert_called_once_with(404)
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["error"] == "not found"

    def test_metrics_shows_only_numeric(self):
        """metrics 端点只输出 int/float 类型的指标。"""
        set_metric("string_metric", "hello")
        set_metric("int_metric", 42)
        handler = MockRequestHandler.make_handler(path="/metrics")
        handler.do_GET()

        body = handler.wfile.getvalue().decode()
        assert "int_metric 42" in body
        assert "string_metric" not in body

    def test_root_shows_all_metrics_sorted(self):
        """根端点显示所有指标（含非数值），按 key 排序。"""
        set_metric("z_last", 999)
        set_metric("a_first", 1)
        handler = MockRequestHandler.make_handler(path="/")
        handler.do_GET()

        body = handler.wfile.getvalue().decode()
        # 确保两个指标都存在
        assert "a_first" in body
        assert "z_last" in body
        # 确保按字母序（a_first 在 z_last 之前）
        a_pos = body.index("a_first")
        z_pos = body.index("z_last")
        assert a_pos < z_pos
