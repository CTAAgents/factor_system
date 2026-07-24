"""tests/test_http_server.py — FTSDashboardServer 测试。

HARNESS §测试随重构: 覆盖 http_server.py 核心路径。
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from fts.monitor.http_server import (
    FTSDashboardServer,
    _metrics,
    _DashboardHandler,
    DASHBOARD_HTML,
    get_metric,
    set_metric,
)


# ─── DASHBOARD_HTML ──────────────────────────────────


class TestDashboardHTML:
    """仪表盘 HTML 内容测试。"""

    def test_contains_dashboard_title(self):
        """HTML 应包含 FTS Dashboard 标题。"""
        assert "FTS Dashboard" in DASHBOARD_HTML

    def test_contains_api_endpoints(self):
        """HTML 应引用正确的 API 端点。"""
        assert "/api/status" in DASHBOARD_HTML
        assert "/api/factors" in DASHBOARD_HTML

    def test_auto_refresh_interval(self):
        """应每 10 秒自动刷新。"""
        assert "setInterval(refresh, 10000)" in DASHBOARD_HTML


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


# ─── FTSDashboardServer ──────────────────────────────────


class TestFTSDashboardServerInit:
    """初始化测试。"""

    def test_default_host_port(self):
        """默认 host='127.0.0.1', port=9100。"""
        server = FTSDashboardServer()
        assert server.host == "127.0.0.1"
        assert server.port == 9100

    def test_custom_host_port(self):
        """自定义 host 和 port。"""
        server = FTSDashboardServer(host="0.0.0.0", port=8080)
        assert server.host == "0.0.0.0"
        assert server.port == 8080

    def test_initial_state(self):
        """初始状态。"""
        server = FTSDashboardServer()
        assert server._server is None
        assert server._thread is None
        assert server.running is False


class TestFTSDashboardServerStartStop:
    """启动/停止测试。"""

    @patch("fts.monitor.http_server.HTTPServer")
    def test_start_creates_server(self, mock_httpserver):
        """start 创建 HTTPServer 实例。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = FTSDashboardServer()
        server.start()

        mock_httpserver.assert_called_once_with(("127.0.0.1", 9100), _DashboardHandler)
        assert server.running is True

    @patch("fts.monitor.http_server.HTTPServer")
    def test_start_idempotent(self, mock_httpserver):
        """多次 start 不重复创建。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = FTSDashboardServer()
        server.start()
        server.start()  # 第二次应跳过

        mock_httpserver.assert_called_once()
        mock_server_instance.serve_forever.assert_called_once()

    @patch("fts.monitor.http_server.HTTPServer")
    def test_stop_after_start(self, mock_httpserver):
        """start 后 stop 应正确关闭。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = FTSDashboardServer()
        server.start()
        server.stop()

        mock_server_instance.shutdown.assert_called_once()
        assert server.running is False
        assert server._server is None

    @patch("fts.monitor.http_server.HTTPServer")
    def test_stop_idempotent(self, mock_httpserver):
        """stop 多次调用不抛异常。"""
        server = FTSDashboardServer()
        server.stop()  # _server 为 None
        server.stop()  # 再次调用

    @patch("fts.monitor.http_server.HTTPServer")
    def test_running_property(self, mock_httpserver):
        """running 属性反映状态。"""
        mock_server_instance = MagicMock()
        mock_httpserver.return_value = mock_server_instance

        server = FTSDashboardServer()
        assert server.running is False

        server.start()
        assert server.running is True

        server.stop()
        assert server.running is False

    @patch("fts.monitor.http_server.HTTPServer", side_effect=OSError("port in use"))
    def test_start_failure_on_port(self, mock_httpserver):
        """端口被占用时 start 不抛出异常。"""
        server = FTSDashboardServer(port=9999)
        server.start()  # 不应抛出
        assert server.running is False


# ─── _DashboardHandler ────────────────────────────────────


class MockRequestHandler:
    """模拟 _DashboardHandler 所需环境。"""

    @staticmethod
    def make_handler(method="GET", path="/health"):
        """创建 mock handler 实例。"""
        handler = MagicMock(spec=_DashboardHandler)
        handler.command = method
        handler.path = path
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = BytesIO()
        handler._respond_json = _DashboardHandler._respond_json.__get__(handler, _DashboardHandler)
        handler._respond_html = _DashboardHandler._respond_html.__get__(handler, _DashboardHandler)
        handler.do_GET = _DashboardHandler.do_GET.__get__(handler, _DashboardHandler)
        return handler


class TestDashboardHandler:
    """_DashboardHandler HTTP 端点测试。"""

    def test_health_endpoint_json(self):
        """GET /health 返回 JSON 含 status=ok。"""
        handler = MockRequestHandler.make_handler(path="/health")
        with (
            patch("fts.monitor.http_server.time.strftime", return_value="2026-07-19T12:00:00"),
        ):
            handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["status"] == "ok"

    def test_root_endpoint_html(self):
        """GET / 返回仪表盘 HTML。"""
        handler = MockRequestHandler.make_handler(path="/")
        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/html; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        assert "FTS Dashboard" in body
        assert "/api/status" in body

    def test_root_endpoint_empty_path(self):
        """GET '' 应等同于 /。"""
        handler = MockRequestHandler.make_handler(path="")
        handler.do_GET()
        handler.send_response.assert_called_once_with(200)

    def test_api_status_endpoint(self):
        """GET /api/status 返回 JSON。"""
        handler = MockRequestHandler.make_handler(path="/api/status")
        with patch.object(handler, "_build_status", return_value={"healthy": True, "loops": []}):
            handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["healthy"] is True

    def test_api_factors_endpoint(self):
        """GET /api/factors 返回 JSON。"""
        handler = MockRequestHandler.make_handler(path="/api/factors")
        with patch.object(handler, "_build_factor_list", return_value={"factors": [], "count": 0}):
            handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["count"] == 0

    def test_unknown_endpoint_404(self):
        """未知路径返回 404 JSON。"""
        handler = MockRequestHandler.make_handler(path="/unknown")
        handler.do_GET()

        handler.send_response.assert_called_once_with(404)
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        assert data["error"] == "not found"
