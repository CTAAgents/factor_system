"""tests/test_http_server.py — FTSDashboardServer 测试。

HARNESS §测试随重构: 覆盖 http_server.py 核心路径。
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
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


# ─── _build_status ──────────────────────────────────────


class TestDashboardHandlerBuildStatus:
    """_build_status 方法测试。"""

    def test_log_message_debug(self):
        """log_message 应调用 logger.debug。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.monitor.http_server.logger") as mock_logger:
            _DashboardHandler.log_message(handler, "GET /health %s %s", 200, "0.1")
            mock_logger.debug.assert_called_once_with("HTTP %s", "GET /health 200 0.1")

    def _make_loop_report(self, **kwargs):
        """创建 LoopStatusReport mock。"""
        from fts.monitor import LoopStatusReport
        return LoopStatusReport(
            loop_name=kwargs.get("loop_name", "L1"),
            healthy=kwargs.get("healthy", True),
            status=kwargs.get("status", "completed"),
            run_id=kwargs.get("run_id", "run-001"),
            last_run_at=kwargs.get("last_run_at", "2026-07-24T12:00:00"),
            last_error=kwargs.get("last_error"),
            tokens_consumed=kwargs.get("tokens_consumed", 500),
            age_hours=kwargs.get("age_hours", 1.5),
            version=kwargs.get("version", "v1.1.0"),
        )

    def test_build_status_returns_correct_structure(self):
        """_build_status 返回正确的 JSON 结构。"""
        handler = MockRequestHandler.make_handler()
        mock_loops = [
            self._make_loop_report(loop_name="L1", status="completed", tokens_consumed=500),
            self._make_loop_report(loop_name="L2", status="running", tokens_consumed=300),
        ]
        mock_report = MagicMock(spec=object)
        mock_report.healthy = True
        mock_report.fts_version = "v1.1.0"
        mock_report.any_circuit_broken = False
        mock_report.any_stale = False
        mock_report.total_tokens_today = 800
        mock_report.checked_at = "2026-07-24T12:00:00"
        mock_report.loops = mock_loops

        with (
            patch("fts.monitor.check_all_status", return_value=mock_report),
            patch("pathlib.Path.cwd", return_value=Path("/tmp")),
            patch("pathlib.Path.exists", return_value=False),
        ):
            result = _DashboardHandler._build_status(handler)

        assert result["healthy"] is True
        assert result["fts_version"] == "v1.1.0"
        assert result["any_circuit_broken"] is False
        assert result["any_stale"] is False
        assert result["total_tokens_today"] == 800
        assert result["checked_at"] == "2026-07-24T12:00:00"
        assert result["elite_factor_count"] == 0
        assert result["overloaded_count"] == 0
        assert result["retired_count"] == 0
        assert len(result["loops"]) == 2
        assert result["loops"][0]["loop_name"] == "L1"
        assert result["loops"][0]["healthy"] is True
        assert result["loops"][0]["status"] == "completed"
        assert result["loops"][0]["tokens_consumed"] == 500

    def test_build_status_error_handling(self):
        """check_all_status 抛出异常时 _build_status 返回降级报告。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.monitor.check_all_status", side_effect=RuntimeError("test error")):
            result = _DashboardHandler._build_status(handler)

        assert result["healthy"] is False
        assert result["loops"] == []
        assert result["any_circuit_broken"] is False
        assert result["any_stale"] is False
        assert result["total_tokens_today"] == 0

    def test_build_status_counts_factor_files(self):
        """_build_status 正确统计 elite/overloaded/retired 因子文件数。"""
        import tempfile
        import os

        handler = MockRequestHandler.make_handler()
        mock_report = MagicMock(spec=object)
        mock_report.healthy = True
        mock_report.fts_version = "v1.1.0"
        mock_report.any_circuit_broken = False
        mock_report.any_stale = False
        mock_report.total_tokens_today = 0
        mock_report.checked_at = ""
        mock_report.loops = []

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # 创建 elite/overloaded/retired 目录，放入占位文件
            for subdir, count in [("elite", 2), ("overloaded", 1), ("retired", 3)]:
                d = root / "memory" / "knowledge" / "factors" / subdir
                d.mkdir(parents=True)
                for i in range(count):
                    (d / f"factor_{i}.json").write_text("{}", encoding="utf-8")

            with patch("fts.monitor.check_all_status", return_value=mock_report):
                with patch("pathlib.Path.cwd", return_value=root):
                    result = _DashboardHandler._build_status(handler)

        assert result["elite_factor_count"] == 2
        assert result["overloaded_count"] == 1
        assert result["retired_count"] == 3


# ─── _build_factor_list ─────────────────────────────────


class TestDashboardHandlerBuildFactorList:
    """_build_factor_list 方法测试。"""

    def test_build_factor_list_empty_when_no_dir(self):
        """elite 目录不存在时返回空列表。"""
        handler = MockRequestHandler.make_handler()
        with patch("pathlib.Path.cwd", return_value=Path("/nonexistent")):
            with patch("pathlib.Path.exists", return_value=False):
                result = _DashboardHandler._build_factor_list(handler)

        assert result["factors"] == []
        assert result["count"] == 0

    def test_build_factor_list_empty_dir(self):
        """elite 目录存在但无 JSON 文件时返回空列表。"""
        handler = MockRequestHandler.make_handler()
        mock_elite_dir = MagicMock(spec=Path)
        mock_elite_dir.exists.return_value = True
        mock_elite_dir.glob.return_value = []

        mock_cwd = MagicMock()
        mock_cwd.__truediv__.return_value = mock_elite_dir

        with patch("pathlib.Path.cwd", return_value=mock_cwd):
            result = _DashboardHandler._build_factor_list(handler)

        assert result["factors"] == []
        assert result["count"] == 0

    def test_build_factor_list_reads_files(self):
        """_build_factor_list 读取 elite 因子文件并正确解析。"""
        import tempfile

        handler = MockRequestHandler.make_handler()

        factor1 = {"factor_id": "F001", "name": "测试因子1", "generation": 5,
                    "source": "evolution",
                    "evaluation": {"level_1_backtest": {"ic": 0.0523, "sharpe": 1.25}}}
        factor2 = {"factor_id": "F002", "name": "测试因子2", "generation": 3,
                    "source": "seed",
                    "evaluation": {"level_1_backtest": {"ic": 0.0310, "sharpe": 0.95}}}

        with tempfile.TemporaryDirectory() as tmpdir:
            elite_dir = Path(tmpdir) / "memory" / "knowledge" / "factors" / "elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "F001.json").write_text(json.dumps(factor1), encoding="utf-8")
            (elite_dir / "F002.json").write_text(json.dumps(factor2), encoding="utf-8")

            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                result = _DashboardHandler._build_factor_list(handler)

        assert result["count"] == 2
        assert len(result["factors"]) == 2
        # 按 reversed 排序，F002 在前
        assert result["factors"][0]["factor_id"] == "F002"
        assert result["factors"][0]["name"] == "测试因子2"
        assert result["factors"][0]["generation"] == 3
        assert result["factors"][0]["ic"] == "0.0310"
        assert result["factors"][0]["sharpe"] == "0.95"
        assert result["factors"][0]["source"] == "seed"
        assert result["factors"][1]["factor_id"] == "F001"

    def test_build_factor_list_skips_bad_files(self):
        """损坏的 JSON 文件被跳过不中断。"""
        import tempfile

        handler = MockRequestHandler.make_handler()

        good_factor = {"factor_id": "G001", "name": "good", "generation": 1,
                        "source": "seed",
                        "evaluation": {"level_1_backtest": {"ic": 0.01, "sharpe": 0.5}}}

        with tempfile.TemporaryDirectory() as tmpdir:
            elite_dir = Path(tmpdir) / "memory" / "knowledge" / "factors" / "elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "good.json").write_text(json.dumps(good_factor), encoding="utf-8")
            (elite_dir / "bad.json").write_text("{invalid json", encoding="utf-8")

            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                result = _DashboardHandler._build_factor_list(handler)

        assert result["count"] == 1
        assert len(result["factors"]) == 1
        assert result["factors"][0]["factor_id"] == "G001"

    def test_build_factor_list_limited_to_50(self):
        """_build_factor_list 最多返回 50 个因子。"""
        import tempfile

        handler = MockRequestHandler.make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            elite_dir = Path(tmpdir) / "memory" / "knowledge" / "factors" / "elite"
            elite_dir.mkdir(parents=True)
            for i in range(60):
                factor = {"factor_id": f"F{i:03d}", "name": f"f{i}", "generation": 1,
                          "source": "seed",
                          "evaluation": {"level_1_backtest": {"ic": 0.01, "sharpe": 0.5}}}
                (elite_dir / f"F{i:03d}.json").write_text(json.dumps(factor), encoding="utf-8")

            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                result = _DashboardHandler._build_factor_list(handler)

        # sorted(..., reverse=True)[:50] - sorted by stem, reversed
        assert result["count"] == 50
        assert len(result["factors"]) == 50
