"""tests.scheduler.test_sync_stock_task — 股票数据缓存同步任务测试。

覆盖:
    1. sync_stock_data 调度任务注册（cron 工作日 17:00）
    2. sync_stock_data_job 端到端（mock FTSDataProvider 面板）
    3. 写入 DuckDB stock_kline_cache + 同步摘要落盘
    4. 部分失败时仍能继续 + 正确统计
"""

from __future__ import annotations

import gzip
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fts.scheduler.jobs import sync_stock_data_job  # noqa: E402
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


def _make_stock_df(n: int = 5, price: float = 100.0) -> pd.DataFrame:
    """构造测试股票 OHLCV DataFrame（index 为 DatetimeIndex）。"""
    idx = pd.DatetimeIndex([date.today() - timedelta(days=i) for i in range(n)][::-1])
    return pd.DataFrame(
        {
            "open": [price] * n,
            "high": [price + 1] * n,
            "low": [price - 1] * n,
            "close": [price] * n,
            "volume": [1_000_000.0] * n,
            "amount": [5e8] * n,
        },
        index=idx,
    )


def _make_panel(symbols: list[str], n: int = 5) -> dict[str, pd.DataFrame]:
    return {sym: _make_stock_df(n=n, price=100.0) for sym in symbols}


@pytest.fixture
def _patch_db_path(tmp_path, monkeypatch):
    """将 _DUCKDB_PATH 指向临时目录，避免污染真实 data/fts_history.duckdb。"""
    db_path = tmp_path / "fts_history.duckdb"
    monkeypatch.setattr("fts.data_futures._DUCKDB_PATH", db_path)
    return db_path


# ─── 调度任务注册测试 ──────────────────────────────────


class TestSyncStockDataTaskRegistration:
    def test_registered_in_default_tasks(self):
        """register_default_tasks() 注册 sync_stock_data。"""
        from fts.scheduler.tasks import register_default_tasks

        register_default_tasks()
        assert "sync_stock_data" in REGISTRY
        spec = REGISTRY.get("sync_stock_data")
        assert spec is not None
        assert spec.cron_expression == "0 17 * * 1-5"  # 工作日 17:00
        assert spec.callable_path == "fts.scheduler.jobs.sync_stock_data_job"
        assert spec.trace_id_prefix == "fts.sync.stock"

    def test_in_list_tasks(self):
        """list_tasks() 含 sync_stock_data。"""
        tasks = list_tasks()
        names = [t.name for t in tasks]
        assert "sync_stock_data" in names

    def test_get_task_returns_spec(self):
        """get_task('sync_stock_data') 返回正确 spec。"""
        spec = get_task("sync_stock_data")
        assert spec is not None
        assert spec.cron_expression == "0 17 * * 1-5"


# ─── sync_stock_data_job 端到端测试 ──────────────────


def _mock_mcp(subset: list[str], data: dict[str, pd.DataFrame], raising: set[str] | None = None) -> MagicMock:
    """构造 mock MCPDataProvider：按 CSI300_SUBSET 顺序返回数据。

    raising 中的标的抛 MCPDataError（模拟拉取失败，严格模式不写入）。
    """
    from fts.data_mcp import MCPDataError

    raising = raising or set()
    provider = MagicMock()

    def _get_ohlcv(sym: str, days: int = 500, adjust: str = "qfq", trace_id: str = "", strict: bool = False):
        if sym in raising:
            raise MCPDataError(f"mock failure [{sym}]")
        return data.get(sym, pd.DataFrame())

    provider.get_ohlcv.side_effect = _get_ohlcv
    return provider


class TestSyncStockDataJob:
    def test_all_success(self, tmp_path, monkeypatch, _patch_db_path):
        """全部标成功 + 写入 DuckDB + 摘要落盘。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        panel = _make_panel(["000001", "000002", "600000"])
        mock_provider = _mock_mcp(["000001", "000002", "600000"], panel)

        # TQ 拉取失败 → 降级腾讯 API 严格模式成功
        with patch("fts.scheduler.jobs._fetch_stock_ohlcv_from_tdx", return_value=None):
            with patch("fts.data_mcp.CSI300_SUBSET", ["000001", "000002", "600000"]):
                with patch("fts.data_mcp.MCPDataProvider", return_value=mock_provider):
                    sync_stock_data_job(max_stocks=3, days=5)

        # 摘要落盘
        lineage = tmp_path / "data" / "_lineage"
        assert lineage.exists()
        files = list(lineage.glob("sync_stock_summary_*.json.gz"))
        assert len(files) == 1
        summary = json.loads(gzip.decompress(files[0].read_bytes()))
        assert summary["symbols_total"] == 3
        assert summary["success"] == 3
        assert summary["failure"] == 0
        assert summary["total_rows"] == 15
        assert summary["source"] == "TDX_LOCAL|TENCENT"
        assert summary["trace_id"].startswith("fts.sync.stock.sched_")

        # DuckDB 写入验证
        import duckdb

        db_path = _patch_db_path
        assert db_path.exists()
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute("SELECT symbol, COUNT(*) FROM stock_kline_cache GROUP BY symbol").fetchall()
            symbols = {r[0]: r[1] for r in rows}
            assert set(symbols.keys()) == {"000001", "000002", "600000"}
            assert all(v == 5 for v in symbols.values())
        finally:
            con.close()

    def test_tq_preferred_source(self, tmp_path, monkeypatch, _patch_db_path):
        """TQ 可用时优先使用 TDX_LOCAL 源，不调用腾讯 API。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        panel = _make_panel(["000001", "000002", "600000"])
        mock_provider = _mock_mcp(["000001", "000002", "600000"], panel)
        mock_provider.get_ohlcv.side_effect = AssertionError("TQ 可用时不应调用腾讯 API")

        # TQ 成功返回数据
        with patch("fts.scheduler.jobs._fetch_stock_ohlcv_from_tdx", side_effect=lambda sym, days, tid: panel[sym]):
            with patch("fts.data_mcp.CSI300_SUBSET", ["000001", "000002", "600000"]):
                with patch("fts.data_mcp.MCPDataProvider", return_value=mock_provider):
                    sync_stock_data_job(max_stocks=3, days=5)

        import duckdb

        con = duckdb.connect(str(_patch_db_path), read_only=True)
        try:
            sources = con.execute("SELECT DISTINCT source FROM stock_kline_cache").fetchall()
            assert [s[0] for s in sources] == ["TDX_LOCAL"]
        finally:
            con.close()

    def test_partial_failure(self, tmp_path, monkeypatch, _patch_db_path):
        """部分标失败时仍能继续。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        panel = _make_panel(["000001", "000002", "600000"])
        mock_provider = _mock_mcp(["000001", "000002", "600000"], panel, raising={"000002"})

        # TQ 全失败 → 腾讯 API 降级，000002 降级也失败
        with patch("fts.scheduler.jobs._fetch_stock_ohlcv_from_tdx", return_value=None):
            with patch("fts.data_mcp.CSI300_SUBSET", ["000001", "000002", "600000"]):
                with patch("fts.data_mcp.MCPDataProvider", return_value=mock_provider):
                    sync_stock_data_job(max_stocks=3, days=5)

        lineage = tmp_path / "data" / "_lineage"
        files = list(lineage.glob("sync_stock_summary_*.json.gz"))
        summary = json.loads(gzip.decompress(files[0].read_bytes()))
        assert summary["success"] == 2
        assert summary["failure"] == 1
        assert len(summary["failures"]) == 1
        assert summary["failures"][0]["symbol"] == "000002"

        # 成功标的已写入
        import duckdb

        con = duckdb.connect(str(_patch_db_path), read_only=True)
        try:
            cnt = con.execute("SELECT COUNT(*) FROM stock_kline_cache").fetchone()[0]
            assert cnt == 10  # 2 个成功标 × 5 行
        finally:
            con.close()

    def test_panel_fetch_failure(self, tmp_path, monkeypatch, _patch_db_path, caplog):
        """面板拉取抛异常时，job 不崩溃（仅记日志、无摘要）。"""
        import logging

        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        mock_provider = MagicMock()
        mock_provider.get_ohlcv.side_effect = RuntimeError("mock failure")

        with caplog.at_level(logging.ERROR, logger="fts.scheduler.jobs"):
            with patch("fts.scheduler.jobs._fetch_stock_ohlcv_from_tdx", return_value=None):
                with patch("fts.data_mcp.CSI300_SUBSET", ["000001", "000002", "600000"]):
                    with patch("fts.data_mcp.MCPDataProvider", return_value=mock_provider):
                        sync_stock_data_job(max_stocks=3, days=5)  # 不应抛

        lineage = tmp_path / "data" / "_lineage"
        files = list(lineage.glob("sync_stock_summary_*.json.gz")) if lineage.exists() else []
        assert len(files) == 0

    def test_empty_panel_skips(self, tmp_path, monkeypatch, _patch_db_path):
        """空面板时跳过，无摘要。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        mock_provider = MagicMock()
        mock_provider.get_ohlcv.side_effect = RuntimeError("all fail")

        with patch("fts.scheduler.jobs._fetch_stock_ohlcv_from_tdx", return_value=None):
            with patch("fts.data_mcp.CSI300_SUBSET", ["000001", "000002", "600000"]):
                with patch("fts.data_mcp.MCPDataProvider", return_value=mock_provider):
                    sync_stock_data_job(max_stocks=3, days=5)

        lineage = tmp_path / "data" / "_lineage"
        files = list(lineage.glob("sync_stock_summary_*.json.gz")) if lineage.exists() else []
        assert len(files) == 0

    def test_upsert_replaces_duplicates(self, tmp_path, monkeypatch, _patch_db_path):
        """重复同步同一标的不产生重复行（主键 upsert）。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()

        panel = _make_panel(["000001"], n=5)
        mock_provider = _mock_mcp(["000001"], panel)

        with patch("fts.scheduler.jobs._fetch_stock_ohlcv_from_tdx", return_value=None):
            with patch("fts.data_mcp.CSI300_SUBSET", ["000001"]):
                with patch("fts.data_mcp.MCPDataProvider", return_value=mock_provider):
                    sync_stock_data_job(max_stocks=1, days=5)
                    sync_stock_data_job(max_stocks=1, days=5)  # 第二次同步

        import duckdb

        con = duckdb.connect(str(_patch_db_path), read_only=True)
        try:
            cnt = con.execute(
                "SELECT COUNT(*) FROM stock_kline_cache WHERE symbol='000001'"
            ).fetchone()[0]
            assert cnt == 5  # 不膨胀
        finally:
            con.close()
