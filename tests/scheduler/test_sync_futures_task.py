"""tests.scheduler.test_sync_futures_task — Phase 14.5 同步任务测试。

覆盖:
    1. sync_futures_data_job 调度任务注册
    2. sync_futures_data_job 端到端（mock aggregator）
    3. 同步摘要落盘 data/_lineage/sync_summary_*.json
    4. 部分失败时仍能继续 + 正确统计
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fts.scheduler.jobs import sync_futures_data_job  # noqa: E402
from fts.scheduler.tasks import REGISTRY, get_task, list_tasks  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    keys = list(REGISTRY._tasks.keys())
    for k in keys:
        REGISTRY.unregister(k)
    yield
    keys = list(REGISTRY._tasks.keys())
    for k in keys:
        REGISTRY.unregister(k)


def _make_kline_df(n: int = 5, price: float = 3500.0) -> pd.DataFrame:
    """构造测试 K 线 DataFrame。"""
    from datetime import date, timedelta
    return pd.DataFrame({
        "symbol": "RB0",
        "period": "daily",
        "date": [date.today() - timedelta(days=i) for i in range(n)][::-1],
        "open": [price] * n,
        "high": [price + 1] * n,
        "low": [price - 1] * n,
        "close": [price] * n,
        "volume": [100000] * n,
        "source": "TQ_LOCAL",
        "trace_id": "test-tid",
    })


# ─── 调度任务注册测试 ──────────────────────────────────


class TestSyncFuturesDataTaskRegistration:
    def test_registered_in_default_tasks(self):
        """register_default_tasks() 注册 sync_futures_data。"""
        from fts.scheduler.tasks import register_default_tasks
        register_default_tasks()
        assert "sync_futures_data" in REGISTRY
        spec = REGISTRY.get("sync_futures_data")
        assert spec is not None
        assert spec.cron_expression == "30 17 * * 1-5"  # 工作日 17:30
        assert spec.callable_path == "fts.scheduler.jobs.sync_futures_data_job"
        assert spec.trace_id_prefix == "fts.sync"

    def test_in_list_tasks(self):
        """list_tasks() 含 sync_futures_data。"""
        tasks = list_tasks()
        names = [t.name for t in tasks]
        assert "sync_futures_data" in names

    def test_get_task_returns_spec(self):
        """get_task('sync_futures_data') 返回正确 spec。"""
        spec = get_task("sync_futures_data")
        assert spec is not None
        assert spec.cron_expression == "30 17 * * 1-5"


# ─── sync_futures_data_job 端到端测试 ──────────────────


class TestSyncFuturesDataJob:
    def test_all_success(self, tmp_path, monkeypatch):
        """全部品种成功。"""
        # 切换到 tmp_path 避免污染真实 data/
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        mock_agg = MagicMock()
        mock_agg.sources = [MagicMock(source_name="TQ_LOCAL")]
        mock_agg.enhancers = []
        # 每次返回有效 DataFrame
        mock_agg.get_ohlcv.return_value = _make_kline_df(n=5)
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0, "circuit_open": False,
                "total_success": 5, "total_failure": 0, "last_error": "",
            }
        }

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            sync_futures_data_job(symbols=["RB0", "CU0", "AU0"], days=5)

        # 验证 aggregator 被调用 3 次
        assert mock_agg.get_ohlcv.call_count == 3

        # 验证摘要落盘
        lineage = tmp_path / "data" / "_lineage"
        assert lineage.exists()
        files = list(lineage.glob("sync_summary_*.json.gz"))
        assert len(files) == 1
        summary = json.loads(gzip.decompress(files[0].read_bytes()))
        assert summary["symbols_total"] == 3
        assert summary["success"] == 3
        assert summary["failure"] == 0
        assert summary["total_rows"] == 15
        assert "trace_id" in summary
        assert summary["trace_id"].startswith("fts.sync.sched_")
        assert "TQ_LOCAL" in summary["source_status"]
        assert "elapsed_seconds" in summary

    def test_partial_failure(self, tmp_path, monkeypatch):
        """部分品种失败时仍能继续。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        mock_agg = MagicMock()
        mock_agg.sources = []
        mock_agg.enhancers = []

        # 第 1、3 成功，第 2 失败
        def side_effect(symbol, days, trace_id):
            if symbol == "CU0":
                return _make_kline_df(n=0)  # 空数据
            return _make_kline_df(n=5)

        mock_agg.get_ohlcv.side_effect = side_effect
        mock_agg.get_source_status.return_value = {}

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            sync_futures_data_job(symbols=["RB0", "CU0", "AU0"], days=5)

        lineage = tmp_path / "data" / "_lineage"
        files = list(lineage.glob("sync_summary_*.json.gz"))
        summary = json.loads(gzip.decompress(files[0].read_bytes()))
        assert summary["success"] == 2
        assert summary["failure"] == 1
        assert len(summary["failures"]) == 1
        assert summary["failures"][0]["symbol"] == "CU0"

    def test_all_exception_caught(self, tmp_path, monkeypatch):
        """所有品种抛异常时，job 不崩溃。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        mock_agg = MagicMock()
        mock_agg.sources = []
        mock_agg.enhancers = []
        mock_agg.get_ohlcv.side_effect = RuntimeError("mock failure")
        mock_agg.get_source_status.return_value = {}

        # 不应抛异常
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            sync_futures_data_job(symbols=["RB0", "CU0"], days=5)

        # 摘要仍落盘，全部失败
        lineage = tmp_path / "data" / "_lineage"
        files = list(lineage.glob("sync_summary_*.json.gz"))
        summary = json.loads(gzip.decompress(files[0].read_bytes()))
        assert summary["success"] == 0
        assert summary["failure"] == 2
        assert len(summary["failures"]) == 2

    def test_aggregator_creation_failure(self, tmp_path, monkeypatch, caplog):
        """aggregator 创建失败时，job 不崩溃（仅记日志）。"""
        import logging
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        with caplog.at_level(logging.ERROR, logger="fts.scheduler.jobs"):
            with patch("fts.cli._build_default_aggregator", side_effect=RuntimeError("init fail")):
                # 不应抛
                sync_futures_data_job(symbols=["RB0"], days=5)

        # 落盘失败（因为 aggregator 在 try 内构造）
        lineage = tmp_path / "data" / "_lineage"
        # 此时没有摘要文件（job 在构造 aggregator 之前就抛了）
        files = list(lineage.glob("sync_summary_*.json.gz")) if lineage.exists() else []
        assert len(files) == 0

    def test_default_symbols_is_core_subset(self, tmp_path, monkeypatch):
        """默认 symbols 来自 FUTURES_CORE_SUBSET。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        captured_symbols: list[list[str]] = []

        def side_effect(symbol, days, trace_id):
            captured_symbols.append(symbol) if False else None  # 不重要
            return _make_kline_df(n=3)

        mock_agg = MagicMock()
        mock_agg.sources = []
        mock_agg.enhancers = []
        mock_agg.get_ohlcv.side_effect = side_effect
        mock_agg.get_source_status.return_value = {}

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            sync_futures_data_job(symbols=None, days=5)  # 默认 core

        # 验证 call 数量 == FUTURES_CORE_SUBSET 长度
        from fts.data_futures import FUTURES_CORE_SUBSET
        assert mock_agg.get_ohlcv.call_count == len(FUTURES_CORE_SUBSET)
        # 默认应为 25 个核心品种
        assert len(FUTURES_CORE_SUBSET) >= 20
