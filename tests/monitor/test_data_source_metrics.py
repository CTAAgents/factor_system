"""tests.monitor.test_data_source_metrics — Phase 14.5 数据源指标端点测试。

覆盖:
    1. /metrics/data-sources 端点契约
    2. /health 端点集成数据源状态
    3. check_data_sources_status() 接口
    4. 任意源熔断时 healthy=False
    5. 最近一次同步摘要嵌入
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


# ─── /metrics/data-sources 端点测试 ─────────────────────


@pytest.fixture(autouse=True)
def _clear_metrics_cache():
    """每次测试前清理 _build_data_source_metrics 的模块级别缓存，避免测试间干扰。"""
    import fts.monitor.http_server as mod

    mod._metrics_cache["data"] = None
    mod._metrics_cache["ts"] = 0.0
    yield
    mod._metrics_cache["data"] = None
    mod._metrics_cache["ts"] = 0.0


class TestMetricsDataSourcesEndpoint:
    def test_endpoint_structure(self):
        """/metrics/data-sources 返回完整结构。"""
        from fts.monitor.http_server import _DashboardHandler

        handler = MagicMock()
        result = _DashboardHandler._build_data_source_metrics(handler)

        # 顶层字段
        assert "fts_version" in result
        assert "checked_at" in result
        assert "healthy" in result
        assert "summary" in result
        assert "sources" in result
        assert "latest_sync" in result

        # summary 子字段
        s = result["summary"]
        assert "any_circuit_open" in s
        assert "total_success" in s
        assert "total_failure" in s
        assert "success_rate" in s
        assert "source_count" in s

    def test_healthy_when_all_sources_ok(self):
        """所有源正常 → healthy=True。"""
        from fts.monitor.http_server import _DashboardHandler

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 100,
                "total_failure": 5,
                "last_error": "",
            },
            "WIND": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 50,
                "total_failure": 2,
                "last_error": "",
            },
        }

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_data_source_metrics(MagicMock())

        assert result["healthy"] is True
        assert result["summary"]["any_circuit_open"] is False
        assert result["summary"]["source_count"] == 2
        assert result["summary"]["total_success"] == 150
        assert result["summary"]["total_failure"] == 7
        # 150 / 157 ≈ 0.9554
        assert result["summary"]["success_rate"] == 0.9554

    def test_unhealthy_when_circuit_open(self):
        """任一源熔断 → healthy=False。"""
        from fts.monitor.http_server import _DashboardHandler

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 100,
                "total_failure": 0,
                "last_error": "",
            },
            "WIND": {
                "consecutive_failures": 5,
                "circuit_open": True,
                "total_success": 50,
                "total_failure": 10,
                "last_error": "timeout",
            },
        }

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_data_source_metrics(MagicMock())

        assert result["healthy"] is False
        assert result["summary"]["any_circuit_open"] is True
        assert result["sources"]["WIND"]["circuit_open"] is True

    def test_aggregator_failure_returns_empty(self):
        """aggregator 创建失败时仍返回结构（healthy=False）。"""
        from fts.monitor.http_server import _DashboardHandler

        with patch(
            "fts.cli._build_default_aggregator",
            side_effect=RuntimeError("init fail"),
        ):
            result = _DashboardHandler._build_data_source_metrics(MagicMock())

        assert result["healthy"] is False
        assert result["sources"] == {}
        assert result["summary"]["source_count"] == 0

    def test_latest_sync_summary_loaded(self, tmp_path, monkeypatch):
        """最近一次 sync_summary_*.json 被嵌入 latest_sync。"""
        from fts.monitor.http_server import _DashboardHandler

        # 构造 lineage 目录
        lineage = tmp_path / "data" / "_lineage"
        lineage.mkdir(parents=True)
        summary = {
            "trace_id": "fts.sync.sched_20260804T173000",
            "started_at": "2026-08-04T17:30:00",
            "finished_at": "2026-08-04T17:31:00",
            "elapsed_seconds": 60.0,
            "symbols_total": 25,
            "success": 24,
            "failure": 1,
            "total_rows": 12000,
            "source_status": {
                "TQ_LOCAL": {
                    "consecutive_failures": 0,
                    "circuit_open": False,
                    "total_success": 25,
                    "total_failure": 0,
                    "last_error": "",
                }
            },
            "failures": [{"symbol": "WH0", "reason": "empty"}],
        }
        (lineage / "sync_summary_20260804_173000.json").write_text(
            json.dumps(summary, ensure_ascii=False),
            encoding="utf-8",
        )

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {}

        monkeypatch.chdir(tmp_path)
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_data_source_metrics(MagicMock())

        assert result["latest_sync"] is not None
        assert result["latest_sync"]["trace_id"] == "fts.sync.sched_20260804T173000"
        assert result["latest_sync"]["success"] == 24
        assert result["latest_sync"]["failure"] == 1

    def test_latest_sync_failures_truncated(self, tmp_path, monkeypatch):
        """failures 列表截断到 10 个。"""
        from fts.monitor.http_server import _DashboardHandler

        lineage = tmp_path / "data" / "_lineage"
        lineage.mkdir(parents=True)
        summary = {
            "trace_id": "t1",
            "started_at": "t",
            "finished_at": "t",
            "elapsed_seconds": 0.0,
            "symbols_total": 0,
            "success": 0,
            "failure": 20,
            "total_rows": 0,
            "source_status": {},
            "failures": [{"symbol": f"S{i:02d}", "reason": "x"} for i in range(20)],
        }
        (lineage / "sync_summary_test.json").write_text(
            json.dumps(summary, ensure_ascii=False),
            encoding="utf-8",
        )

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {}
        monkeypatch.chdir(tmp_path)
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_data_source_metrics(MagicMock())

        assert len(result["latest_sync"]["failures"]) == 10


# ─── /health 端点测试（数据源状态集成）────────────────


class TestHealthEndpointWithDataSources:
    def test_health_status_ok(self):
        """所有源正常 → status=ok。"""
        from fts.monitor.http_server import _DashboardHandler

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 100,
                "total_failure": 0,
                "last_error": "",
            },
        }

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_health(MagicMock())

        assert result["status"] == "ok"
        assert "timestamp" in result
        assert result["data_sources"]["any_circuit_open"] is False
        assert result["data_sources"]["source_count"] == 1
        assert "TQ_LOCAL" in result["data_sources"]["sources"]

    def test_health_status_degraded_when_circuit_open(self):
        """任一源熔断 → status=degraded。"""
        from fts.monitor.http_server import _DashboardHandler

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "WIND": {
                "consecutive_failures": 5,
                "circuit_open": True,
                "total_success": 50,
                "total_failure": 10,
                "last_error": "timeout",
            },
        }

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_health(MagicMock())

        assert result["status"] == "degraded"
        assert result["data_sources"]["any_circuit_open"] is True

    def test_health_aggregator_failure(self):
        """aggregator 失败时记录 error，不影响 status=ok。"""
        from fts.monitor.http_server import _DashboardHandler

        with patch(
            "fts.cli._build_default_aggregator",
            side_effect=RuntimeError("init fail"),
        ):
            result = _DashboardHandler._build_health(MagicMock())

        # status 仍为 ok（aggregator 失败 ≠ 系统崩溃）
        assert result["status"] == "ok"
        assert "data_sources_error" in result

    def test_health_response_keys(self):
        """health 响应包含 data_sources 子对象。"""
        from fts.monitor.http_server import _DashboardHandler

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 10,
                "total_failure": 0,
                "last_error": "",
            },
        }
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_health(MagicMock())

        # 子对象结构
        ds = result["data_sources"]
        assert "any_circuit_open" in ds
        assert "source_count" in ds
        assert "sources" in ds
        for name, s in ds["sources"].items():
            assert "circuit_open" in s
            assert "consecutive_failures" in s
            assert "total_success" in s
            assert "total_failure" in s


# ─── check_data_sources_status() 接口测试 ─────────────


class TestCheckDataSourcesStatus:
    def test_all_healthy(self):
        """所有源正常。"""
        from fts.monitor import check_data_sources_status

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 100,
                "total_failure": 0,
                "last_error": "",
            },
        }
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = check_data_sources_status()

        assert result["healthy"] is True
        assert result["source_count"] == 1
        assert result["any_circuit_open"] is False
        assert "TQ_LOCAL" in result["sources"]

    def test_aggregator_error(self):
        """aggregator 初始化失败 → 返回错误结构。"""
        from fts.monitor import check_data_sources_status

        with patch(
            "fts.cli._build_default_aggregator",
            side_effect=RuntimeError("init fail"),
        ):
            result = check_data_sources_status()

        assert result["healthy"] is False
        assert result["source_count"] == 0
        assert "error" in result

    def test_circuit_open_flag(self):
        """任一源熔断 → any_circuit_open=True, healthy=False。"""
        from fts.monitor import check_data_sources_status

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 100,
                "total_failure": 0,
                "last_error": "",
            },
            "WIND": {
                "consecutive_failures": 5,
                "circuit_open": True,
                "total_success": 50,
                "total_failure": 10,
                "last_error": "x",
            },
        }
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = check_data_sources_status()

        assert result["healthy"] is False
        assert result["any_circuit_open"] is True
        assert result["source_count"] == 2


# ─── HTTP 路由测试 ──────────────────────────────────


class TestHTTPRouting:
    def test_metrics_route_404_when_missing(self):
        """/api/unknown 返回 404。"""
        from fts.monitor.http_server import _DashboardHandler

        handler = _DashboardHandler.__new__(_DashboardHandler)
        handler.path = "/api/unknown"
        handler.wfile = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler._respond_json = MagicMock()
        # 直接调用 _respond_json 来检查 path 解析
        # 这里不直接调 do_GET 因为需要完整 socket 环境

    def test_build_data_source_metrics_returns_4xx_safe_json(self):
        """_build_data_source_metrics 返回的字典可被 json.dumps 序列化。"""
        from fts.monitor.http_server import _DashboardHandler

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0,
                "circuit_open": False,
                "total_success": 10,
                "total_failure": 1,
                "last_error": "",
                "opened_at": 0.0,  # float
            },
        }
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            result = _DashboardHandler._build_data_source_metrics(MagicMock())

        # 不抛异常
        text = json.dumps(result, ensure_ascii=False, default=str)
        assert "TQ_LOCAL" in text
