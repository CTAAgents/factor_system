"""tests/test_http_server.py — FTSDashboardServer 测试。

HARNESS §测试随重构: 覆盖 http_server.py 核心路径。
"""

from __future__ import annotations

import hashlib
import json
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import fts.monitor.http_server as http_server
from fts.monitor.http_server import (
    FTSDashboardServer,
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
        handler._respond_metrics = _DashboardHandler._respond_metrics.__get__(handler, _DashboardHandler)
        handler._build_metrics = _DashboardHandler._build_metrics.__get__(handler, _DashboardHandler)
        handler._build_data_source_metrics = _DashboardHandler._build_data_source_metrics.__get__(
            handler, _DashboardHandler
        )
        handler._build_prometheus_metrics = _DashboardHandler._build_prometheus_metrics.__get__(
            handler, _DashboardHandler
        )
        handler._build_health = _DashboardHandler._build_health.__get__(handler, _DashboardHandler)
        handler._build_status = _DashboardHandler._build_status.__get__(handler, _DashboardHandler)
        handler._build_factor_list = _DashboardHandler._build_factor_list.__get__(handler, _DashboardHandler)
        handler._build_factor_list_from_duckdb = _DashboardHandler._build_factor_list_from_duckdb.__get__(
            handler, _DashboardHandler
        )
        handler._build_factor_list_json_fallback = _DashboardHandler._build_factor_list_json_fallback.__get__(
            handler, _DashboardHandler
        )
        handler._apply_cluster_groups = _DashboardHandler._apply_cluster_groups.__get__(handler, _DashboardHandler)
        handler._build_candidate_list = _DashboardHandler._build_candidate_list.__get__(handler, _DashboardHandler)
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
            # 创建 futures_elite/overloaded/retired 目录，放入占位文件
            for subdir, count in [("futures_elite", 2), ("overloaded", 1), ("retired", 3)]:
                d = root / "memory" / "knowledge" / "factors" / subdir
                d.mkdir(parents=True)
                for i in range(count):
                    (d / f"factor_{i}.json").write_text("{}", encoding="utf-8")

            with patch("fts.monitor.check_all_status", return_value=mock_report):
                with patch("pathlib.Path.cwd", return_value=root):
                    # 强制 DuckDB 不可用，走 JSON fallback 统计路径
                    with patch(
                        "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                        root / "nonexistent.duckdb",
                    ):
                        result = _DashboardHandler._build_status(handler)

        assert result["elite_factor_count"] == 2
        assert result["overloaded_count"] == 1
        assert result["retired_count"] == 3


# ─── _build_factor_list ─────────────────────────────────


class TestDashboardHandlerBuildFactorList:
    """_build_factor_list 方法测试。"""

    def test_build_factor_list_empty_when_no_dir(self):
        """elite 目录不存在时返回空列表。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("pathlib.Path.cwd", return_value=root):
                with patch(
                    "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                    root / "nonexistent.duckdb",
                ):
                    result = _DashboardHandler._build_factor_list(handler)

        assert result["factors"] == []
        assert result["count"] == 0

    def test_build_factor_list_empty_dir(self):
        """elite 目录存在但无 JSON 文件时返回空列表。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)

            with patch("pathlib.Path.cwd", return_value=root):
                with patch(
                    "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                    root / "nonexistent.duckdb",
                ):
                    result = _DashboardHandler._build_factor_list(handler)

        assert result["factors"] == []
        assert result["count"] == 0

    def test_build_factor_list_reads_files(self):
        """_build_factor_list 读取 elite 因子文件并正确解析。"""
        import tempfile

        handler = MockRequestHandler.make_handler()

        factor1 = {
            "factor_id": "F001",
            "name": "测试因子1",
            "generation": 5,
            "source": "evolution",
            "evaluation": {"level_1_backtest": {"ic": 0.0523, "sharpe": 1.25}},
        }
        factor2 = {
            "factor_id": "F002",
            "name": "测试因子2",
            "generation": 3,
            "source": "seed",
            "evaluation": {"level_1_backtest": {"ic": 0.0310, "sharpe": 0.95}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            elite_dir = Path(tmpdir) / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "F001.json").write_text(json.dumps(factor1), encoding="utf-8")
            (elite_dir / "F002.json").write_text(json.dumps(factor2), encoding="utf-8")

            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                with patch(
                    "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                    Path(tmpdir) / "nonexistent.duckdb",
                ):
                    with patch(
                        "fts.monitor.http_server._cluster_factors_by_signal",
                        return_value={
                            "assign": {"F001": 0, "F002": 0},
                            "cluster_order": [0],
                            "cluster_members": {0: ["F001", "F002"]},
                        },
                    ):
                        result = _DashboardHandler._build_factor_list(handler)

        assert result["count"] == 2
        assert len(result["factors"]) == 2
        # 单簇（size=2）→ applied，簇内按 sharpe 降序，F001 (sharpe=1.25) 在前
        assert result["factors"][0]["factor_id"] == "F001"
        assert result["factors"][0]["name"] == "测试因子1"
        assert result["factors"][0]["generation"] == 5
        assert result["factors"][0]["ic"] == "0.0523"
        assert result["factors"][0]["sharpe"] == "1.25"
        assert result["factors"][0]["source"] == "evolution"
        assert result["factors"][1]["factor_id"] == "F002"

    def test_build_factor_list_skips_bad_files(self):
        """损坏的 JSON 文件被跳过不中断。"""
        import tempfile

        handler = MockRequestHandler.make_handler()

        good_factor = {
            "factor_id": "G001",
            "name": "good",
            "generation": 1,
            "source": "seed",
            "evaluation": {"level_1_backtest": {"ic": 0.01, "sharpe": 0.5}},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            elite_dir = Path(tmpdir) / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "good.json").write_text(json.dumps(good_factor), encoding="utf-8")
            (elite_dir / "bad.json").write_text("{invalid json", encoding="utf-8")

            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                with patch(
                    "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                    Path(tmpdir) / "nonexistent.duckdb",
                ):
                    result = _DashboardHandler._build_factor_list(handler)

        assert result["count"] == 1
        assert len(result["factors"]) == 1
        assert result["factors"][0]["factor_id"] == "G001"

    def test_build_factor_list_limited_to_200(self):
        """_build_factor_list fallback 最多返回 200 个因子。"""
        import tempfile

        handler = MockRequestHandler.make_handler()

        with tempfile.TemporaryDirectory() as tmpdir:
            elite_dir = Path(tmpdir) / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            for i in range(210):
                factor = {
                    "factor_id": f"F{i:03d}",
                    "name": f"f{i}",
                    "generation": 1,
                    "source": "seed",
                    "evaluation": {"level_1_backtest": {"ic": 0.01, "sharpe": 0.5}},
                }
                (elite_dir / f"F{i:03d}.json").write_text(json.dumps(factor), encoding="utf-8")

            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                with patch(
                    "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                    Path(tmpdir) / "nonexistent.duckdb",
                ):
                    result = _DashboardHandler._build_factor_list(handler)

        # fallback 读取上限为 200
        assert result["count"] == 200
        assert len(result["factors"]) == 200

    def test_build_factor_list_fallback_marks_evaluation_status(self):
        """JSON fallback 按评估结果标注 evaluation_status（pending=未评估）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()

        evaluated = {
            "factor_id": "E001",
            "name": "已评估因子",
            "generation": 1,
            "source": "seed",
            "evaluation": {"level_1_backtest": {"ic": 0.05, "sharpe": 1.5}},
        }
        unevaluated = {"factor_id": "P001", "name": "未评估因子", "generation": 0, "source": "seed"}

        with tempfile.TemporaryDirectory() as tmpdir:
            elite_dir = Path(tmpdir) / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "E001.json").write_text(json.dumps(evaluated), encoding="utf-8")
            (elite_dir / "P001.json").write_text(json.dumps(unevaluated), encoding="utf-8")

            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                result = _DashboardHandler._build_factor_list_json_fallback(handler)

        by_id = {f["factor_id"]: f for f in result["factors"]}
        assert by_id["E001"]["evaluation_status"] == "evaluated"
        assert by_id["P001"]["evaluation_status"] == "pending"


# ─── /api/candidates（L1 候选池）────────────────────────


class TestDashboardHandlerBuildCandidates:
    """_build_candidate_list 方法测试。"""

    def _write_pool(self, tmpdir: str, factors: list[dict]) -> Path:
        pool_path = Path(tmpdir) / "memory" / "knowledge" / "factors" / "factor_pool.json"
        pool_path.parent.mkdir(parents=True, exist_ok=True)
        pool_path.write_text(
            json.dumps(
                {
                    "version": "8.10.0",
                    "updated_at": "2026-08-08T00:00:00",
                    "factors": factors,
                    "total_count": len(factors),
                    "pending_count": sum(1 for f in factors if f.get("status") == "pending"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return pool_path

    def test_candidates_empty_when_no_pool(self):
        """factor_pool.json 不存在时返回空。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                result = _DashboardHandler._build_candidate_list(handler)

        assert result["count"] == 0
        assert result["pending_count"] == 0
        assert result["factors"] == []

    def test_candidates_lists_pool_factors(self):
        """读取 factor_pool.json 并标注评估状态。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        factors = [
            {
                "factor_id": "cand_001",
                "name": "候选A",
                "source": "l2_evolution",
                "status": "pending",
                "evaluation_status": "pending",
                "priority": "high",
                "parent_topic": "螺纹钢",
                "trace_id": "t1",
                "created_at": "2026-08-08",
                "updated_at": "2026-08-08",
            },
            {
                "factor_id": "cand_002",
                "name": "候选B",
                "source": "l1_bootstrapping",
                "status": "injected",
                "evaluation_status": "pending",
                "priority": "medium",
                "parent_topic": "铁矿",
                "trace_id": "t2",
                "created_at": "2026-08-08",
                "updated_at": "2026-08-08",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_pool(tmpdir, factors)
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                result = _DashboardHandler._build_candidate_list(handler)

        assert result["count"] == 2
        assert result["pending_count"] == 1
        # pending 状态优先排序
        assert result["factors"][0]["factor_id"] == "cand_001"
        assert result["factors"][0]["evaluation_status"] == "pending"
        assert result["factors"][1]["evaluation_status"] == "pending"

    def test_candidates_defaults_evaluation_status(self):
        """旧记录缺 evaluation_status 时默认 pending（未评估）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        factors = [
            {
                "factor_id": "cand_003",
                "name": "旧候选",
                "source": "l1_extractor_pipeline",
                "status": "pending",
                "priority": "low",
                "parent_topic": None,
                "trace_id": "t3",
                "created_at": "2026-08-01",
                "updated_at": "2026-08-01",
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_pool(tmpdir, factors)
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                result = _DashboardHandler._build_candidate_list(handler)

        assert result["factors"][0]["evaluation_status"] == "pending"
        assert result["factors"][0]["parent_topic"] == "-"


# ─── /metrics 端点 ──────────────────────────────────────


class TestDashboardHandlerMetrics:
    """_DashboardHandler /metrics 端点测试。"""

    def test_metrics_endpoint_returns_text(self):
        """GET /metrics 应返回 Prometheus 文本。"""
        handler = MockRequestHandler.make_handler(path="/metrics")
        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        assert "# HELP" in body
        assert "# TYPE" in body

    def test_metrics_endpoint_contains_fts_up(self):
        """指标应包含 fts_up 在线指标。"""
        handler = MockRequestHandler.make_handler(path="/metrics")
        handler.do_GET()

        body = handler.wfile.getvalue().decode()
        assert "fts_up" in body
        assert "fts_up 1" in body

    def test_metrics_endpoint_contains_data_quality(self):
        """指标应包含数据质量指标。"""
        handler = MockRequestHandler.make_handler(path="/metrics")
        handler.do_GET()

        body = handler.wfile.getvalue().decode()
        assert "fts_data_quality_data_completeness_ratio" in body
        assert "fts_data_quality_market_data_valid" in body

    def test_metrics_endpoint_with_monitor_set(self):
        """设置 DataQualityMonitor 后指标应包含实时数据。"""
        from fts.monitor.data_quality_monitor import DataQualityMonitor
        from fts.monitor import set_data_quality_monitor

        monitor = DataQualityMonitor()
        from tests.monitor.test_data_quality_monitor import _make_good_data

        monitor.validate_market_data(_make_good_data())
        set_data_quality_monitor(monitor)

        handler = MockRequestHandler.make_handler(path="/metrics")
        handler.do_GET()

        body = handler.wfile.getvalue().decode()
        assert "fts_data_quality_total_checks 1" in body
        assert "fts_data_quality_market_data_valid 1.0" in body

        set_data_quality_monitor(None)  # cleanup

    def test_data_sources_metrics_endpoint(self):
        """GET /metrics/data-sources 应返回数据源指标 JSON。"""
        handler = MockRequestHandler.make_handler(path="/metrics/data-sources")
        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        data = json.loads(body)
        # Phase 14.5 契约: dict 包含 healthy/summary/sources
        assert "healthy" in data
        assert "summary" in data
        assert "sources" in data

    def test_set_and_get_data_quality_monitor(self):
        """set/get_data_quality_monitor 正常工作。"""
        from fts.monitor.data_quality_monitor import DataQualityMonitor
        from fts.monitor import set_data_quality_monitor, get_data_quality_monitor

        saved = get_data_quality_monitor()
        set_data_quality_monitor(None)
        assert get_data_quality_monitor() is None

        monitor = DataQualityMonitor()
        set_data_quality_monitor(monitor)
        assert get_data_quality_monitor() is monitor

        set_data_quality_monitor(saved)  # restore


# ═══════════════════════════════════════════════════════════
# 补充端点 / 路由：_respond_text、/api/candidates、/api/v1/*
# ═══════════════════════════════════════════════════════════


class TestDashboardHandlerExtendedRoutes:
    """_DashboardHandler 补充端点与 do_GET 路由测试。"""

    def test_respond_text_helper(self):
        """_respond_text 返回 text/plain 响应（line 574-577）。"""
        handler = MockRequestHandler.make_handler()
        _DashboardHandler._respond_text.__get__(handler, _DashboardHandler)("hello")
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/plain; charset=utf-8")
        assert handler.wfile.getvalue() == b"hello"

    def test_api_candidates_route(self):
        """GET /api/candidates 路由调用 _build_candidate_list（line 1330）。"""
        handler = MockRequestHandler.make_handler(path="/api/candidates")
        with patch.object(
            handler, "_build_candidate_list", return_value={"count": 0, "pending_count": 0, "factors": []}
        ):
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["count"] == 0

    def test_api_v1_risk_status_route(self):
        """GET /api/v1/risk/status 路由（line 1342）。"""
        handler = MockRequestHandler.make_handler(path="/api/v1/risk/status")
        with patch("fts.monitor.http_server._build_risk_status", return_value={"risk_level": "normal"}):
            handler.do_GET()
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["risk_level"] == "normal"

    def test_api_v1_live_factors_route(self):
        """GET /api/v1/live/factors 路由（line 1345）。"""
        handler = MockRequestHandler.make_handler(path="/api/v1/live/factors")
        with patch("fts.monitor.http_server._build_live_factors", return_value={"factors": [], "alerts": []}):
            handler.do_GET()
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["factors"] == []

    def test_api_v1_live_deviation_route(self):
        """GET /api/v1/live/factors/{id}/deviation 路由（line 1348-1349）。"""
        handler = MockRequestHandler.make_handler(path="/api/v1/live/factors/F001/deviation")
        with patch(
            "fts.monitor.http_server._build_live_deviation", return_value={"factor_id": "F001", "deviation": 0.1}
        ) as m:
            handler.do_GET()
        m.assert_called_once_with("F001")
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["factor_id"] == "F001"


# ═══════════════════════════════════════════════════════════
# _build_status — JSON fallback 细节 + DuckDB 查询失败
# ═══════════════════════════════════════════════════════════


class TestBuildStatusExtended:
    """_build_status 的 fallback 边界分支测试。"""

    def _mock_report(self):
        from fts.monitor import SystemStatusReport

        return SystemStatusReport(
            healthy=True,
            loops=[],
            fts_version="v1.1.0",
            any_circuit_broken=False,
            any_stale=False,
            total_tokens_today=0,
        )

    def test_build_status_fallback_skips_underscore_and_bad_json(self):
        """JSON fallback 跳过 _ 前缀文件 + 容忍坏 JSON（line 613, 618-619）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "_private.json").write_text("{}", encoding="utf-8")
            (elite_dir / "bad.json").write_text("{invalid", encoding="utf-8")
            (elite_dir / "F001.json").write_text(json.dumps({"factor_id": "F001", "name": "trend_factor"}), encoding="utf-8")

            with patch("fts.monitor.check_all_status", return_value=self._mock_report()):
                with patch("pathlib.Path.cwd", return_value=root):
                    with patch(
                        "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                        root / "nonexistent.duckdb",
                    ):
                        result = _DashboardHandler._build_status(handler)

        # elite_count 统计所有 *.json（含 _ 前缀与坏文件）
        assert result["elite_factor_count"] == 3

    def test_build_status_duckdb_query_failure_falls_back(self):
        """DuckDB 连接失败时回退 JSON 统计（line 662-664）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_file = root / "fts.duckdb"
            db_file.write_bytes(b"dummy")
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "F001.json").write_text(json.dumps({"factor_id": "F001", "name": "carry_factor"}), encoding="utf-8")

            with patch("fts.monitor.check_all_status", return_value=self._mock_report()):
                with patch("pathlib.Path.cwd", return_value=root):
                    with patch(
                        "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                        db_file,
                    ):
                        with patch("duckdb.connect", side_effect=RuntimeError("db broken")):
                            result = _DashboardHandler._build_status(handler)

        assert result["elite_factor_count"] == 1


# ═══════════════════════════════════════════════════════════
# _build_factor_list_from_duckdb — mock DuckDB 完整路径
# ═══════════════════════════════════════════════════════════


class TestBuildFactorListFromDuckDB:
    """_build_factor_list_from_duckdb 的完整路径（含 792 行列名获取修复）。

    修复前：fetchall() 返回 list，访问 result.description 必然 AttributeError，
    被 _build_factor_list 的 except 静默降级到 JSON fallback，DuckDB 分支从未生效。
    修复后：在 fetchall 之前通过 relation.description 取列名。
    """

    def test_duckdb_success_path(self):
        """真实 DuckDB 行为模拟：relation.description + fetchall 均可用 → 成功返回。

        tables 含 evaluatios/quality_scores 表以覆盖 JOIN 分支（734-739, 762-767）。
        """
        mock_conn = MagicMock()
        tables_resp = MagicMock()
        tables_resp.fetchall.return_value = [
            ("factor_catalog",),
            ("factor_evaluations",),
            ("factor_quality_scores",),
        ]
        main_resp = MagicMock()
        main_resp.description = [(f"c{i}",) for i in range(24)]
        main_resp.fetchall.return_value = [tuple(["x"] * 24)]
        cluster_resp = MagicMock()
        cluster_resp.fetchall.return_value = []
        mock_conn.execute.side_effect = [tables_resp, main_resp, cluster_resp]

        handler = MockRequestHandler.make_handler()
        with patch("duckdb.connect", return_value=mock_conn):
            result = _DashboardHandler._build_factor_list_from_duckdb(handler, Path("/tmp/x.duckdb"))
        assert isinstance(result, dict)
        assert result["count"] >= 1
        assert result["factors"][0]["factor_id"] == ""  # 列名未对齐时走默认值而非崩溃
        assert result["clustering_applied"] is False  # 无 code 因子 → 聚类降级

    def test_no_join_tables_null_select_branch(self):
        """无 evaluation/quality 表时走 NULL 列 else 分支（line 754, 778）。"""
        mock_conn = MagicMock()
        tables_resp = MagicMock()
        tables_resp.fetchall.return_value = [("factor_catalog",)]
        main_resp = MagicMock()
        main_resp.description = [(f"c{i}",) for i in range(24)]
        main_resp.fetchall.return_value = [tuple(["x"] * 24)]
        cluster_resp = MagicMock()
        cluster_resp.fetchall.return_value = []
        mock_conn.execute.side_effect = [tables_resp, main_resp, cluster_resp]

        handler = MockRequestHandler.make_handler()
        with patch("duckdb.connect", return_value=mock_conn):
            result = _DashboardHandler._build_factor_list_from_duckdb(handler, Path("/tmp/x.duckdb"))
        assert isinstance(result, dict)
        assert result["factors"][0]["quality_score"] is None  # 无 quality 表 → NULL 分支


# ═══════════════════════════════════════════════════════════
# _build_factor_list — DuckDB 失败降级 + _ 前缀文件
# ═══════════════════════════════════════════════════════════


class TestBuildFactorListExtended:
    """_build_factor_list 的降级与边界分支。"""

    def test_duckdb_connect_failure_falls_back(self):
        """DuckDB 连接失败时降级 JSON 文件（line 704-706）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_file = root / "fts.duckdb"
            db_file.write_bytes(b"dummy")
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "F001.json").write_text(
                json.dumps(
                    {
                        "factor_id": "F001",
                        "name": "f1",
                        "generation": 1,
                        "source": "seed",
                        "evaluation": {"level_1_backtest": {"ic": 0.01, "sharpe": 0.5}},
                    }
                ),
                encoding="utf-8",
            )

            with patch("pathlib.Path.cwd", return_value=root):
                with patch(
                    "fts.factor_engine.factor_db.schema.DATABASE_PATH",
                    db_file,
                ):
                    with patch("duckdb.connect", side_effect=RuntimeError("db broken")):
                        result = _DashboardHandler._build_factor_list(handler)

        assert result["source"] == "json_fallback"
        assert result["count"] == 1

    def test_json_fallback_skips_underscore_files(self):
        """JSON fallback 跳过 _ 前缀文件（line 923）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            elite_dir = root / "memory" / "knowledge" / "factors" / "futures_elite"
            elite_dir.mkdir(parents=True)
            (elite_dir / "_hidden.json").write_text("{}", encoding="utf-8")
            (elite_dir / "F001.json").write_text(
                json.dumps(
                    {
                        "factor_id": "F001",
                        "name": "f1",
                        "generation": 1,
                        "source": "seed",
                        "evaluation": {"level_1_backtest": {"ic": 0.01, "sharpe": 0.5}},
                    }
                ),
                encoding="utf-8",
            )

            with patch("pathlib.Path.cwd", return_value=root):
                result = _DashboardHandler._build_factor_list_json_fallback(handler)

        assert result["count"] == 1
        assert result["factors"][0]["factor_id"] == "F001"


# ═══════════════════════════════════════════════════════════
# _build_candidate_list — 损坏 pool 文件
# ═══════════════════════════════════════════════════════════


class TestBuildCandidatesExtended:
    """_build_candidate_list 的异常分支。"""

    def test_corrupt_pool_returns_empty(self):
        """factor_pool.json 内容损坏时返回空（line 1012-1013）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pool_path = root / "memory" / "knowledge" / "factors" / "factor_pool.json"
            pool_path.parent.mkdir(parents=True)
            pool_path.write_text("{corrupt json", encoding="utf-8")
            with patch("pathlib.Path.cwd", return_value=root):
                result = _DashboardHandler._build_candidate_list(handler)

        assert result["count"] == 0
        assert result["pending_count"] == 0
        assert result["factors"] == []


# ═══════════════════════════════════════════════════════════
# _build_metrics / 数据源指标 — 异常与缓存分支
# ═══════════════════════════════════════════════════════════


class TestBuildMetricsExtended:
    """_build_metrics 与 _build_data_source_metrics 的异常/缓存分支。"""

    @pytest.fixture(autouse=True)
    def _reset_metrics_cache(self):
        """重置模块级 _metrics_cache（避免测试间缓存污染）。"""
        from fts.monitor import http_server

        http_server._metrics_cache["data"] = None
        http_server._metrics_cache["ts"] = 0.0
        yield
        http_server._metrics_cache["data"] = None
        http_server._metrics_cache["ts"] = 0.0

    def test_metrics_registry_error_does_not_break(self):
        """metrics_registry.render 抛异常时基础指标仍在（line 1084-1085）。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.monitor.prometheus_metrics.metrics_registry") as mock_reg:
            mock_reg.render.side_effect = RuntimeError("registry broken")
            text = _DashboardHandler._build_metrics(handler)
        assert "# HELP fts_up" in text
        assert "fts_up 1" in text

    def test_metrics_dq_monitor_error_emits_error_line(self):
        """DataQualityMonitor 指标读取失败时输出 ERROR 行（line 1092-1094）。"""
        from fts.monitor import set_data_quality_monitor

        monitor = MagicMock()
        monitor.get_prometheus_metrics.side_effect = RuntimeError("monitor broken")
        set_data_quality_monitor(monitor)
        try:
            handler = MockRequestHandler.make_handler()
            text = _DashboardHandler._build_metrics(handler)
            assert "# ERROR" in text
        finally:
            set_data_quality_monitor(None)

    def test_data_source_metrics_cache_hit(self):
        """_metrics_cache 缓存命中直接返回（line 1160）。"""
        import time as _time
        from fts.monitor import http_server

        handler = MockRequestHandler.make_handler()
        http_server._metrics_cache["data"] = {"healthy": True, "sources": {}}
        http_server._metrics_cache["ts"] = _time.time()
        try:
            result = _DashboardHandler._build_data_source_metrics(handler)
        finally:
            http_server._metrics_cache["data"] = None
            http_server._metrics_cache["ts"] = 0.0
        assert result["healthy"] is True

    def test_data_source_metrics_aggregator_failure(self):
        """聚合器构建失败时降级为空状态（line 1166-1167）。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.cli._build_default_aggregator", side_effect=RuntimeError("agg broken")):
            result = _DashboardHandler._build_data_source_metrics(handler)
        assert result["healthy"] is False
        assert result["summary"]["success_rate"] == 0.0
        assert result["summary"]["source_count"] == 0

    def test_data_source_metrics_sync_read_failure(self):
        """sync_summary 损坏时 latest_sync 为 None（line 1196-1197）。"""
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lineage = root / "data" / "_lineage"
            lineage.mkdir(parents=True)
            (lineage / "sync_summary_20260809.json").write_text("{corrupt", encoding="utf-8")
            mock_agg = MagicMock()
            mock_agg.get_source_status.return_value = {}
            with patch("pathlib.Path.cwd", return_value=root):
                with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
                    result = _DashboardHandler._build_data_source_metrics(handler)
        assert result["latest_sync"] is None

    def test_data_source_metrics_sync_read_json_and_gzip(self):
        """sync_summary .json 与 .json.gz 均被解析（line 1185-1195）。"""
        import gzip
        import tempfile

        handler = MockRequestHandler.make_handler()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lineage = root / "data" / "_lineage"
            lineage.mkdir(parents=True)
            payload = json.dumps({"symbol": "RB0", "failures": list(range(20))})
            # .gz 文件更新时间更新 → 被优先选中
            (lineage / "sync_summary_20260809.json.gz").write_bytes(gzip.compress(payload.encode("utf-8")))
            mock_agg = MagicMock()
            mock_agg.get_source_status.return_value = {}
            with patch("pathlib.Path.cwd", return_value=root):
                with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
                    result = _DashboardHandler._build_data_source_metrics(handler)
        assert result["latest_sync"]["symbol"] == "RB0"
        # failures 截断到 10 个
        assert len(result["latest_sync"]["failures"]) == 10


# ═══════════════════════════════════════════════════════════
# _build_prometheus_metrics — Prometheus 文本指标
# ═══════════════════════════════════════════════════════════


class TestBuildPrometheusMetrics:
    """_build_prometheus_metrics 直接调用测试（line 1219-1285）。"""

    def _make_agg(self, status: dict | None = None):
        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = status or {}
        return mock_agg

    def test_returns_prometheus_text(self):
        """返回 Prometheus 文本格式，包含版本与数据源指标。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.cli._build_default_aggregator", return_value=self._make_agg()):
            text = _DashboardHandler._build_prometheus_metrics(handler)
        assert "# HELP fts_version" in text
        assert "fts_version{" in text
        assert "fts_data_source_success_rate 0.0000" in text
        assert "fts_circuit_open 0" in text
        assert "fts_elite_factor_count" in text

    def test_aggregator_failure_uses_empty_status(self):
        """聚合器构建失败时指标降级为空状态。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.cli._build_default_aggregator", side_effect=RuntimeError("agg broken")):
            text = _DashboardHandler._build_prometheus_metrics(handler)
        assert "fts_data_source_success_rate 0.0000" in text
        assert "fts_circuit_open 0" in text

    def test_source_detail_lines(self):
        """多源状态渲染 fts_source_info 行（含熔断标记）。"""
        handler = MockRequestHandler.make_handler()
        status = {
            "TQ_LOCAL": {"circuit_open": True, "consecutive_failures": 5},
            "AKSHARE": {"circuit_open": False, "consecutive_failures": 0},
        }
        with patch("fts.cli._build_default_aggregator", return_value=self._make_agg(status)):
            text = _DashboardHandler._build_prometheus_metrics(handler)
        assert 'fts_source_info{source="TQ_LOCAL",circuit_open="1",consecutive_failures="5"}' in text
        assert 'fts_source_info{source="AKSHARE",circuit_open="0",consecutive_failures="0"}' in text
        assert "fts_circuit_open 1" in text

    def test_elite_dir_glob_failure_defaults_zero(self):
        """elite 目录统计抛异常时 elite_count 默认 0（line 1279-1280）。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.cli._build_default_aggregator", return_value=self._make_agg()):
            with patch("pathlib.Path.cwd", side_effect=RuntimeError("cwd broken")):
                text = _DashboardHandler._build_prometheus_metrics(handler)
        assert "fts_elite_factor_count 0" in text


# ═══════════════════════════════════════════════════════════
# _build_health — 聚合器异常
# ═══════════════════════════════════════════════════════════


class TestBuildHealthExtended:
    """_build_health 的聚合器异常与降级分支（line 1312-1314）。"""

    def test_aggregator_failure_sets_error(self):
        """聚合器构建失败时写入 data_sources_error。"""
        handler = MockRequestHandler.make_handler()
        with patch("fts.monitor.http_server.time.strftime", return_value="2026-07-19T12:00:00"):
            with patch("fts.cli._build_default_aggregator", side_effect=RuntimeError("agg down")):
                data = _DashboardHandler._build_health(handler)
        assert data["status"] == "ok"
        assert "data_sources_error" in data

    def test_circuit_open_marks_degraded(self):
        """存在熔断开启的数据源时 status=degraded（line 1312）。"""
        handler = MockRequestHandler.make_handler()
        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {"circuit_open": True, "consecutive_failures": 5, "total_success": 1, "total_failure": 5},
        }
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            data = _DashboardHandler._build_health(handler)
        assert data["status"] == "degraded"
        assert data["data_sources"]["any_circuit_open"] is True
        assert data["data_sources"]["source_count"] == 1


# ═══════════════════════════════════════════════════════════
# do_POST — 信号提交全链路
# ═══════════════════════════════════════════════════════════


def _make_post_handler(path="/api/v1/signal/submit", body: bytes | None = None):
    """构造 POST 请求 handler。"""
    body = body if body is not None else b'{"signal_id": "s1", "symbol": "RB0"}'
    handler = MagicMock(spec=_DashboardHandler)
    handler.command = "POST"
    handler.path = path
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = BytesIO()
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    handler._respond_json = _DashboardHandler._respond_json.__get__(handler, _DashboardHandler)
    handler.do_POST = _DashboardHandler.do_POST.__get__(handler, _DashboardHandler)
    handler._handle_signal_submit = _DashboardHandler._handle_signal_submit.__get__(handler, _DashboardHandler)
    return handler


class TestDoPost:
    """POST 端点测试（line 1356-1408）。"""

    def test_post_unknown_path_404(self):
        """POST 未知路径返回 404。"""
        handler = _make_post_handler(path="/unknown")
        handler.do_POST()
        handler.send_response.assert_called_once_with(404)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["error"] == "not found"

    def test_post_signal_submit_invalid_json_400(self):
        """非法 JSON 返回 400。"""
        handler = _make_post_handler(body=b"{not json")
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        body = json.loads(handler.wfile.getvalue().decode())
        assert "error" in body  # 400 响应为 {"error": ...}

    def test_post_signal_submit_validation_errors_422(self):
        """SignalValidator 返回错误时返回 422。"""
        handler = _make_post_handler()
        with patch("fts.factor_engine.signal_contract.SignalValidator") as mock_cls:
            mock_cls.return_value.validate.return_value = ["missing symbol"]
            handler.do_POST()
        handler.send_response.assert_called_once_with(422)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["approved"] is False

    def test_post_signal_submit_risk_blocked_403(self):
        """风控拦截时返回 403。"""
        handler = _make_post_handler()
        mock_risk = MagicMock()
        mock_risk.check.return_value = {
            "approved": False,
            "blocking_violations": ["position_limit"],
        }
        with patch("fts.factor_engine.signal_contract.SignalValidator") as mock_cls:
            mock_cls.return_value.validate.return_value = []
            with patch("fts.monitor.http_server._get_risk_manager", return_value=mock_risk):
                with patch("fts.monitor.http_server._sim_account_status", return_value={"equity": 1}):
                    with patch("fts.monitor.http_server._sim_positions", return_value={}):
                        with patch("fts.monitor.http_server._record_risk_metrics") as rm:
                            handler.do_POST()
        handler.send_response.assert_called_once_with(403)
        rm.assert_called_once()
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["approved"] is False
        assert body["violations"] == ["position_limit"]

    def test_post_signal_submit_success_200(self):
        """信号全链路通过 → 200 approved。"""
        handler = _make_post_handler()
        mock_risk = MagicMock()
        mock_risk.check.return_value = {
            "approved": True,
            "blocking_violations": [],
            "checks": [],
        }
        mock_adapter = MagicMock()
        mock_adapter.is_connected.return_value = False
        mock_adapter.submit_signal.return_value = {"order_id": "o-1"}
        with patch("fts.factor_engine.signal_contract.SignalValidator") as mock_cls:
            mock_cls.return_value.validate.return_value = []
            with patch("fts.monitor.http_server._get_risk_manager", return_value=mock_risk):
                with patch("fts.monitor.http_server._sim_account_status", return_value={"equity": 1}):
                    with patch("fts.monitor.http_server._sim_positions", return_value={}):
                        with patch("fts.monitor.http_server._record_risk_metrics"):
                            with patch(
                                "fts.monitor.http_server._get_sim_adapter", return_value=mock_adapter
                            ) as get_adapter:
                                handler.do_POST()
        get_adapter.assert_called_once()
        handler.send_response.assert_called_once_with(200)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["approved"] is True
        assert body["order"]["order_id"] == "o-1"

    def test_post_signal_submit_trade_failure_500(self):
        """模拟成交失败返回 500。"""
        handler = _make_post_handler()
        mock_risk = MagicMock()
        mock_risk.check.return_value = {
            "approved": True,
            "blocking_violations": [],
            "checks": [],
        }
        mock_adapter = MagicMock()
        mock_adapter.is_connected.return_value = True
        mock_adapter.submit_signal.side_effect = RuntimeError("trade fail")
        with patch("fts.factor_engine.signal_contract.SignalValidator") as mock_cls:
            mock_cls.return_value.validate.return_value = []
            with patch("fts.monitor.http_server._get_risk_manager", return_value=mock_risk):
                with patch("fts.monitor.http_server._sim_account_status", return_value={"equity": 1}):
                    with patch("fts.monitor.http_server._sim_positions", return_value={}):
                        with patch("fts.monitor.http_server._record_risk_metrics"):
                            with patch("fts.monitor.http_server._get_sim_adapter", return_value=mock_adapter):
                                handler.do_POST()
        handler.send_response.assert_called_once_with(500)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["approved"] is False


# ═══════════════════════════════════════════════════════════
# 风控 / Live 因子构建函数（模块级辅助）
# ═══════════════════════════════════════════════════════════


class TestRiskAndLiveBuilders:
    """模块级辅助函数测试（line 1449-1561）。"""

    def test_get_risk_manager_lazy_init(self):
        """_get_risk_manager 首次调用初始化全局实例。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._risk_manager", None):
            with patch("fts.risk.RiskManager", return_value="RM") as mock_cls:
                assert http_server._get_risk_manager() == "RM"
                mock_cls.assert_called_once_with()

    def test_get_sim_adapter_lazy_init(self):
        """_get_sim_adapter 首次调用初始化全局实例。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._sim_adapter", None):
            with patch("fts.risk.SimulatedTradeAdapter", return_value="ADAPTER") as mock_cls:
                assert http_server._get_sim_adapter() == "ADAPTER"
                mock_cls.assert_called_once_with()

    def test_get_live_monitor_lazy_init(self):
        """_get_live_monitor 首次调用初始化全局实例。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._live_monitor", None):
            with patch("fts.monitor.live_factor_monitor.LiveFactorMonitor", return_value="LFM") as mock_cls:
                assert http_server._get_live_monitor() == "LFM"
                mock_cls.assert_called_once_with()

    def test_sim_account_status_fallback(self):
        """适配器异常时返回默认模拟账户（line 1478-1481）。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._get_sim_adapter", side_effect=RuntimeError("no adapter")):
            status = http_server._sim_account_status()
        assert status["total_equity"] == 1_000_000.0
        assert status["position_value"] == 0.0

    def test_sim_positions_reads_nonzero(self):
        """仅持仓非零的品种被保留（line 1488-1497）。"""
        from fts.monitor import http_server

        mock_adapter = MagicMock()
        mock_adapter.get_position.side_effect = lambda sym: {
            "market_value": 100 if sym == "RB0" else 0,
            "quantity": 1 if sym == "RB0" else 0,
        }
        with patch("fts.monitor.http_server._get_sim_adapter", return_value=mock_adapter):
            positions = http_server._sim_positions()
        assert list(positions.keys()) == ["RB0"]

    def test_sim_positions_failure_returns_empty(self):
        """适配器异常时返回空持仓。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._get_sim_adapter", side_effect=RuntimeError("down")):
            assert http_server._sim_positions() == {}

    def test_record_risk_metrics(self):
        """风控检查结果写入指标注册表（line 1502-1507）。"""
        from fts.monitor import http_server

        with patch("fts.monitor.prometheus_metrics.metrics_registry") as mock_reg:
            http_server._record_risk_metrics(
                {
                    "checks": [
                        {"check_name": "c1", "passed": True},
                        {"check_name": "c2", "passed": False},
                    ]
                }
            )
        assert mock_reg.record_risk_check.call_count == 2
        mock_reg.record_risk_check.assert_any_call("c1", "passed")
        mock_reg.record_risk_check.assert_any_call("c2", "blocked")

    def test_build_risk_status_normal(self):
        """全部检查通过 → risk_level=normal。"""
        from fts.monitor import http_server

        mock_risk = MagicMock()
        mock_risk.check.return_value = {"checks": [{"check_name": "x", "passed": True}]}
        with patch("fts.monitor.http_server._get_risk_manager", return_value=mock_risk):
            with patch("fts.monitor.http_server._sim_account_status", return_value={"equity": 1}):
                with patch("fts.monitor.http_server._sim_positions", return_value={}):
                    data = http_server._build_risk_status()
        assert data["risk_level"] == "normal"
        assert data["violations"] == []

    def test_build_risk_status_violations_critical(self):
        """存在违规检查 → risk_level=critical。"""
        from fts.monitor import http_server

        mock_risk = MagicMock()
        mock_risk.check.return_value = {
            "checks": [{"check_name": "pos", "passed": False}],
        }
        with patch("fts.monitor.http_server._get_risk_manager", return_value=mock_risk):
            with patch("fts.monitor.http_server._sim_account_status", return_value={"equity": 1}):
                with patch("fts.monitor.http_server._sim_positions", return_value={}):
                    data = http_server._build_risk_status()
        assert data["risk_level"] == "critical"
        assert len(data["violations"]) == 1

    def test_build_risk_status_failure_unknown(self):
        """风控管理器异常 → risk_level=unknown。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._get_risk_manager", side_effect=RuntimeError("risk down")):
            data = http_server._build_risk_status()
        assert data["risk_level"] == "unknown"
        assert "error" in data

    def test_build_live_factors(self):
        """返回实时因子列表与告警（line 1538-1551）。"""
        from fts.monitor import http_server

        monitor = MagicMock()
        monitor.check_deviation.return_value = [{"factor_id": "F1", "dev": 0.2}]
        monitor.get_factor_ids.return_value = ["F1"]
        monitor._live = {"F1": {"name": "live_factor"}}
        with patch("fts.monitor.http_server._get_live_monitor", return_value=monitor):
            data = http_server._build_live_factors()
        assert data["count"] == 1
        assert data["factors"][0]["live"]["name"] == "live_factor"
        assert data["alerts"] == [{"factor_id": "F1", "dev": 0.2}]

    def test_build_live_factors_failure(self):
        """LiveFactorMonitor 异常 → 空结果 + error。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._get_live_monitor", side_effect=RuntimeError("monitor down")):
            data = http_server._build_live_factors()
        assert data["factors"] == []
        assert data["alerts"] == []
        assert "error" in data

    def test_build_live_deviation(self):
        """返回单个因子偏离（line 1556-1561）。"""
        from fts.monitor import http_server

        monitor = MagicMock()
        monitor.get_factor_deviation.return_value = {"factor_id": "F1", "deviation": 0.2}
        with patch("fts.monitor.http_server._get_live_monitor", return_value=monitor):
            data = http_server._build_live_deviation("F1")
        assert data["factor_id"] == "F1"
        assert data["deviation"] == 0.2

    def test_build_live_deviation_failure(self):
        """偏离查询异常 → 错误响应。"""
        from fts.monitor import http_server

        with patch("fts.monitor.http_server._get_live_monitor", side_effect=RuntimeError("down")):
            data = http_server._build_live_deviation("F1")
        assert data["factor_id"] == "F1"
        assert "error" in data


# ═══════════════════════════════════════════════════════════
# _safe_version — 版本号获取异常回退
# ═══════════════════════════════════════════════════════════


class TestSafeVersion:
    """_safe_version 的异常回退分支（line 1636-1637）。"""

    def test_version_import_failure_returns_question_mark(self):
        """fts.__version__ 导入失败时返回 '?'。"""
        import builtins
        from fts.monitor import http_server

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fts":
                raise ImportError("boom")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            assert http_server._safe_version() == "?"


# ═══════════════════════════════════════════════════════════
# C8 人审工作台 — REVIEW_HTML + /review + /api/review/*
# ═══════════════════════════════════════════════════════════


def _make_review_get_handler(path="/review"):
    """构造 C8 审查 GET 请求 handler。"""
    handler = MagicMock(spec=_DashboardHandler)
    handler.command = "GET"
    handler.path = path
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = BytesIO()
    handler._respond_json = _DashboardHandler._respond_json.__get__(handler, _DashboardHandler)
    handler._respond_html = _DashboardHandler._respond_html.__get__(handler, _DashboardHandler)
    handler._build_review_pending = _DashboardHandler._build_review_pending.__get__(handler, _DashboardHandler)
    handler._build_review_history = _DashboardHandler._build_review_history.__get__(handler, _DashboardHandler)
    handler.do_GET = _DashboardHandler.do_GET.__get__(handler, _DashboardHandler)
    return handler


def _make_review_post_handler(path="/api/review/approve", body: bytes | None = None):
    """构造 C8 审查 POST 请求 handler。"""
    body = body if body is not None else b'{"factor_id": "fct_x", "comment": "ok", "reviewer": "tester"}'
    handler = MagicMock(spec=_DashboardHandler)
    handler.command = "POST"
    handler.path = path
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = BytesIO()
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    handler._respond_json = _DashboardHandler._respond_json.__get__(handler, _DashboardHandler)
    handler._handle_review_decision = _DashboardHandler._handle_review_decision.__get__(
        handler, _DashboardHandler
    )
    handler.do_POST = _DashboardHandler.do_POST.__get__(handler, _DashboardHandler)
    return handler


class TestReviewHTML:
    """C8 人审工作台页面内容测试。"""

    def test_contains_title(self):
        """HTML 包含审查工作台标题。"""
        from fts.monitor.http_server import REVIEW_HTML

        assert "审查工作台" in REVIEW_HTML

    def test_contains_api_endpoints(self):
        """HTML 引用待审/历史/决定端点（决定端点为 JS 动态拼接）。"""
        from fts.monitor.http_server import REVIEW_HTML

        assert "/api/review/pending" in REVIEW_HTML
        assert "/api/review/history" in REVIEW_HTML
        assert "/api/review/" in REVIEW_HTML
        assert "method: 'POST'" in REVIEW_HTML

    def test_auto_refresh_15s(self):
        """页面 15 秒自动刷新。"""
        from fts.monitor.http_server import REVIEW_HTML

        assert "setInterval(loadPending, 15000)" in REVIEW_HTML


class TestReviewEndpoints:
    """C8 审查端点 GET 路由测试。"""

    def test_get_review_page_html(self):
        """GET /review 返回审查页面 HTML。"""
        handler = _make_review_get_handler(path="/review")
        handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/html; charset=utf-8")
        body = handler.wfile.getvalue().decode()
        assert "审查工作台" in body

    def test_get_review_pending_json(self):
        """GET /api/review/pending 返回待审查队列 JSON。"""
        handler = _make_review_get_handler(path="/api/review/pending")
        items = [{"factor_id": "fct_p1", "name": "p1", "market": "futures", "ic": 0.05, "sharpe": 1.5}]
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value.list_pending.return_value = items
            handler.do_GET()
        mock_cls.return_value.list_pending.assert_called_once_with(limit=200)
        handler.send_response.assert_called_once_with(200)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["count"] == 1
        assert body["items"][0]["factor_id"] == "fct_p1"

    def test_get_review_pending_failure_degraded(self):
        """审查队列查询异常 → 空结果 + error。"""
        handler = _make_review_get_handler(path="/api/review/pending")
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow", side_effect=RuntimeError("db down")):
            handler.do_GET()
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["count"] == 0
        assert body["items"] == []
        assert "error" in body

    def test_get_review_history_json(self):
        """GET /api/review/history 返回最近审查记录 JSON。"""
        handler = _make_review_get_handler(path="/api/review/history")
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("fct_x", "approved", "ok", "tester", "2026-08-11T10:00:00", "fct_x"),
        ]
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value._conn.return_value = mock_conn
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["count"] == 1
        assert body["items"][0]["decision"] == "approved"
        assert body["items"][0]["reviewer"] == "tester"
        mock_conn.close.assert_called_once()


class TestReviewDecisionPost:
    """C8 审查决定 POST 端点测试。"""

    def test_post_review_approve(self):
        """POST /api/review/approve 调 workflow.approve 并回写。"""
        handler = _make_review_post_handler(path="/api/review/approve")
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value.approve.return_value = {
                "factor_id": "fct_x",
                "decision": "approved",
            }
            handler.do_POST()
        mock_cls.return_value.approve.assert_called_once_with("fct_x", "ok", "tester")
        handler.send_response.assert_called_once_with(200)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["decision"] == "approved"

    def test_post_review_reject(self):
        """POST /api/review/reject 调 workflow.reject 并回写。"""
        handler = _make_review_post_handler(path="/api/review/reject")
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value.reject.return_value = {
                "factor_id": "fct_x",
                "decision": "rejected",
            }
            handler.do_POST()
        mock_cls.return_value.reject.assert_called_once_with("fct_x", "ok", "tester")
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["decision"] == "rejected"

    def test_post_review_missing_factor_id_400(self):
        """factor_id 缺失 → 400。"""
        handler = _make_review_post_handler(body=b'{"comment": "no id"}')
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)
        body = json.loads(handler.wfile.getvalue().decode())
        assert "error" in body

    def test_post_review_unknown_path_404(self):
        """POST 未知审查路径 → 404。"""
        handler = _make_review_post_handler(path="/api/review/unknown")
        handler.do_POST()
        handler.send_response.assert_called_once_with(404)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["error"] == "not found"


# ═══════════════════════════════════════════════════════════
# C8-2 机审/人审可配置 — pending 标注 + POST /api/review/auto
# ═══════════════════════════════════════════════════════════


def _make_review_auto_handler(body: bytes | None = None):
    """构造 C8-2 机审 POST 请求 handler。"""
    body = body if body is not None else b"{}"
    handler = MagicMock(spec=_DashboardHandler)
    handler.command = "POST"
    handler.path = "/api/review/auto"
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.wfile = BytesIO()
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    handler._respond_json = _DashboardHandler._respond_json.__get__(handler, _DashboardHandler)
    handler._handle_review_auto = _DashboardHandler._handle_review_auto.__get__(handler, _DashboardHandler)
    handler.do_POST = _DashboardHandler.do_POST.__get__(handler, _DashboardHandler)
    return handler


class TestReviewAutoEndpoints:
    """C8-2 机审端点测试。"""

    def test_get_review_pending_includes_mode_and_flag(self):
        """GET /api/review/pending 返回 mode 字段 + needs_human 标注（不落库）。"""
        import json as _json

        handler = _make_review_get_handler(path="/api/review/pending")
        ok_qa = {
            "audit_passed": True, "quality_grade": "B", "high_ic_grade": "A",
            "multiple_passed": True, "walk_forward_windows": 4, "q1_q10_passed": True,
        }
        items = [
            {"factor_id": "fct_ok", "name": "ok", "market": "futures", "ic": 0.05, "sharpe": 2.0,
             "metadata": _json.dumps({"qa_review": ok_qa})},
            {"factor_id": "fct_high", "name": "high", "market": "futures", "ic": 0.9, "sharpe": 2.0,
             "metadata": _json.dumps({"qa_review": ok_qa})},
            {"factor_id": "fct_nan", "name": "nan", "market": "stock", "ic": None, "sharpe": None,
             "metadata": None},
        ]
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value.list_pending.return_value = items
            handler.do_GET()
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["mode"] == "auto"
        by_id = {f["factor_id"]: f for f in body["items"]}
        assert by_id["fct_ok"]["needs_human"] is False
        assert by_id["fct_high"]["needs_human"] is True
        assert "过拟合" in by_id["fct_high"]["review_reason"]
        assert by_id["fct_nan"]["needs_human"] is True

    def test_post_review_auto_success(self):
        """POST /api/review/auto → 机审执行并返回统计。"""
        handler = _make_review_auto_handler()
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value.auto_review.return_value = {
                "mode": "auto",
                "total_pending": 4,
                "auto_approved": 2,
                "auto_rejected": 1,
                "needs_human": [{"factor_id": "f", "reason": "超上限"}],
                "skipped": 0,
            }
            handler.do_POST()
        mock_cls.return_value.auto_review.assert_called_once_with(limit=200, force=False)
        handler.send_response.assert_called_once_with(200)
        body = json.loads(handler.wfile.getvalue().decode())
        assert body["auto_approved"] == 2

    def test_post_review_auto_manual_403(self):
        """manual 模式拒绝 → 403。"""
        handler = _make_review_auto_handler()
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value.auto_review.side_effect = ValueError("manual（纯人审）模式")
            handler.do_POST()
        handler.send_response.assert_called_once_with(403)
        body = json.loads(handler.wfile.getvalue().decode())
        assert "error" in body

    def test_post_review_auto_failure_500(self):
        """执行异常 → 500。"""
        handler = _make_review_auto_handler()
        with patch("fts.factor_engine.factor_inspector.FactorReviewWorkflow") as mock_cls:
            mock_cls.return_value.auto_review.side_effect = RuntimeError("db down")
            handler.do_POST()
        handler.send_response.assert_called_once_with(500)
        body = json.loads(handler.wfile.getvalue().decode())
        assert "error" in body

    def test_review_html_has_auto_controls(self):
        """工作台页面含模式徽标/运行机审按钮/需人工标记。"""
        from fts.monitor.http_server import REVIEW_HTML

        assert "modeBadge" in REVIEW_HTML
        assert "runAutoReview" in REVIEW_HTML
        assert "运行机审" in REVIEW_HTML
        assert "需人工" in REVIEW_HTML


# ═══════════════════════════════════════════════════════════
# 因子信号相关性聚类（UI 按聚类分组）
# ═══════════════════════════════════════════════════════════


class TestFactorClustering:
    """_compute_signal_clusters / _cluster_factors_by_signal / _apply_cluster_groups 测试。"""

    def _reset_cluster_cache(self):
        """重置模块级聚类缓存（避免测试间污染）。"""
        from fts.monitor import http_server

        http_server._cluster_cache["key"] = ""
        http_server._cluster_cache["data"] = None
        http_server._cluster_cache["ts"] = 0.0

    @pytest.fixture(autouse=True)
    def _clean_cluster_cache(self):
        self._reset_cluster_cache()
        yield
        self._reset_cluster_cache()

    def test_compute_signal_clusters_groups_by_correlation(self):
        """高相关信号同簇、低相关信号分簇。"""
        import numpy as np
        import pandas as pd
        from fts.monitor import http_server

        df = pd.DataFrame({"close": np.arange(100.0)})
        rng = np.random.RandomState(0)
        base = np.linspace(0, 1, 100)
        signals = {
            "f1": base + rng.normal(0, 0.01, 100),
            "f2": base + rng.normal(0, 0.01, 100),  # 与 f1 高相关
            "f3": rng.normal(0, 1, 100),  # 与 f1/f2 低相关
        }

        class FakeExecutor:
            def __init__(self, prog):
                self.prog = prog

            def execute(self, data, params):
                return signals[self.prog["factor_id"]]

        mock_provider = MagicMock()
        mock_provider.get_futures_ohlcv.return_value = df
        with (
            patch("fts.data.FTSDataProvider", return_value=mock_provider),
            patch("fts.factor_engine.factor_program.FactorExecutor", FakeExecutor),
        ):
            result = http_server._compute_signal_clusters(
                [
                    {"factor_id": "f1", "code": "x"},
                    {"factor_id": "f2", "code": "x"},
                    {"factor_id": "f3", "code": "x"},
                ]
            )

        assert result is not None
        assign = result["assign"]
        assert assign["f1"] == assign["f2"]  # 高相关同簇
        assert assign["f3"] != assign["f1"]  # 低相关分簇

    def test_compute_signal_clusters_no_signal_returns_none(self):
        """参考品种行情不可用（<2 信号）时返回 None 触发降级。"""
        from fts.monitor import http_server

        mock_provider = MagicMock()
        mock_provider.get_futures_ohlcv.side_effect = RuntimeError("no data source")
        with patch("fts.data.FTSDataProvider", return_value=mock_provider):
            result = http_server._compute_signal_clusters(
                [
                    {"factor_id": "f1", "code": "x"},
                    {"factor_id": "f2", "code": "x"},
                    {"factor_id": "f3", "code": "x"},
                ]
            )
        assert result is None

    def test_compute_signal_clusters_too_few_factors(self):
        """因子数 < 2 时直接返回 None。"""
        from fts.monitor import http_server

        result = http_server._compute_signal_clusters([{"factor_id": "f1", "code": "x"}])
        assert result is None

    def test_cluster_cache_hit_skips_recompute(self):
        """TTL 缓存命中时不再重复计算。"""
        from fts.monitor import http_server

        code_factors = [{"factor_id": f"f{i}", "code": "x"} for i in range(3)]
        data = {"assign": {"f0": 0, "f1": 0, "f2": 1}, "cluster_order": [0, 1], "cluster_members": {0: ["f0", "f1"], 1: ["f2"]}}
        key = hashlib.sha256("|".join(["f0", "f1", "f2"]).encode("utf-8")).hexdigest()[:16]
        http_server._cluster_cache["key"] = key
        http_server._cluster_cache["data"] = data
        http_server._cluster_cache["ts"] = time.time()

        with patch("fts.monitor.http_server._compute_signal_clusters", side_effect=AssertionError("不应重算")):
            result = http_server._cluster_factors_by_signal(code_factors)
        assert result == data

    def test_apply_cluster_groups_degrades_when_no_cluster(self):
        """聚类不可用时不标注 cluster_id，保持原列表。"""
        handler = MockRequestHandler.make_handler()
        factors = [
            {"factor_id": "F001", "name": "f1", "ic": "0.01", "sharpe": "0.5"},
            {"factor_id": "F002", "name": "f2", "ic": "0.02", "sharpe": "1.2"},
        ]
        with patch("fts.monitor.http_server._cluster_factors_by_signal", return_value=None):
            factors, meta = _DashboardHandler._apply_cluster_groups(handler, factors, [])

        assert meta["applied"] is False
        assert meta["distribution"] == {}
        assert "cluster_id" not in factors[0]

    def test_apply_cluster_groups_annotates_and_sorts(self):
        """聚类成功时标注 cluster_id、生成汇总并按簇排序。"""
        handler = MockRequestHandler.make_handler()
        factors = [
            {"factor_id": "A", "name": "a1", "ic": "0.01", "sharpe": "0.5"},
            {"factor_id": "B", "name": "b1", "ic": "0.02", "sharpe": "1.2"},
            {"factor_id": "C", "name": "c1", "ic": "0.03", "sharpe": "2.0"},
            {"factor_id": "D", "name": "d1", "ic": "0.01", "sharpe": "0.8"},
        ]
        cluster_result = {
            "assign": {"A": 0, "B": 0, "C": 1, "D": 1},
            "cluster_order": [0, 1],
            "cluster_members": {0: ["A", "B"], 1: ["C", "D"]},
        }
        with patch("fts.monitor.http_server._cluster_factors_by_signal", return_value=cluster_result):
            factors, meta = _DashboardHandler._apply_cluster_groups(handler, factors, [])

        assert meta["applied"] is True
        assert meta["distribution"] == {"0": 2, "1": 2}
        assert meta["summary"][0]["cluster_id"] == 0
        assert meta["summary"][0]["rep_name"] == "b1"  # 簇内 sharpe 最高
        assert meta["summary"][0]["avg_sharpe"] == 0.85
        # 簇 0 内按 sharpe 降序（b1=1.2 > a1=0.5），簇间按 size 降序
        assert [f["factor_id"] for f in factors] == ["B", "A", "C", "D"]
        assert factors[0]["cluster_id"] == 0

    def test_dashboard_html_uses_cluster_sections(self):
        """仪表盘 HTML 使用聚类区块与渲染函数。"""
        from fts.monitor.http_server import DASHBOARD_HTML

        assert "聚类分布" in DASHBOARD_HTML
        assert "clusterDistSection" in DASHBOARD_HTML
        assert "renderClusterDistribution" in DASHBOARD_HTML
        assert "renderClusterFilterChips" in DASHBOARD_HTML
        # 旧家族渲染函数不应残留
        assert "renderFamilyDistribution" not in DASHBOARD_HTML


class TestWorkflowStaticAssets:
    """_serve_workflow_static 静态资源托管（/workflow SPA + /assets 构建产物）。

    回归防护：http_server 必须同时路由 /workflow（SPA fallback）与 /assets/（Vite 构建产物），
    否则 WorkFlow UI 白屏（ERR_ABORTED assets 资源）。
    """

    @pytest.fixture(autouse=True)
    def _fake_dist(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """构造 fake 项目 dist 目录，并让 http_server 解析到该路径。"""
        dist = tmp_path / "web" / "workflow_ui" / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text('<div id="root">fake-app</div>', encoding="utf-8")
        (dist / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
        (dist / "assets" / "style-abc.css").write_text("body {}", encoding="utf-8")
        # __file__ 指向 tmp_path/fts/monitor/，则 dist = tmp_path/web/workflow_ui/dist
        monkeypatch.setattr(
            http_server, "__file__", str(tmp_path / "fts" / "monitor" / "http_server.py")
        )

    def _serve(self, handler: MockRequestHandler, path: str) -> None:
        """以真实方法绑定 handler 执行 _serve_workflow_static。"""
        _DashboardHandler._serve_workflow_static.__get__(handler, _DashboardHandler)(path)

    def _content_type(self, handler: MockRequestHandler) -> str:
        """提取最后一次 send_header Content-Type 值。"""
        for args, _kwargs in handler.send_header.call_args_list:
            if args[0] == "Content-Type":
                return args[1]
        return ""

    def test_workflow_index_serves_html(self):
        """GET /workflow 返回 SPA index.html。"""
        handler = MockRequestHandler.make_handler(path="/workflow")
        self._serve(handler, "/workflow")
        assert handler.send_response.call_args_list[-1].args[0] == 200
        assert self._content_type(handler) == "text/html"
        assert '<div id="root">fake-app</div>' in handler.wfile.getvalue().decode()

    def test_workflow_assets_js_served(self):
        """GET /assets/index-abc.js 返回构建产物 JS 与正确 Content-Type。"""
        handler = MockRequestHandler.make_handler(path="/assets/index-abc.js")
        self._serve(handler, "/assets/index-abc.js")
        assert handler.wfile.getvalue() == b"console.log(1)"
        assert self._content_type(handler) in ("text/javascript", "application/javascript")

    def test_workflow_assets_css_served(self):
        """GET /assets/style-abc.css 返回构建产物 CSS。"""
        handler = MockRequestHandler.make_handler(path="/assets/style-abc.css")
        self._serve(handler, "/assets/style-abc.css")
        assert handler.wfile.getvalue() == b"body {}"
        assert self._content_type(handler) == "text/css"

    def test_workflow_assets_unknown_falls_back_to_index(self):
        """未知 /assets 文件回退 index.html（SPA fallback）。"""
        handler = MockRequestHandler.make_handler(path="/assets/missing-xyz.js")
        self._serve(handler, "/assets/missing-xyz.js")
        assert '<div id="root">fake-app</div>' in handler.wfile.getvalue().decode()

    def test_workflow_assets_traversal_rejected(self):
        """路径越界（../）拒绝并回退 index.html。"""
        handler = MockRequestHandler.make_handler(path="/assets/../../secret.txt")
        self._serve(handler, "/assets/../../secret.txt")
        assert '<div id="root">fake-app</div>' in handler.wfile.getvalue().decode()
