"""
tests/test_cli_extra.py — FTS CLI 补充测试（覆盖 cli.py 未覆盖分支）。

HARNESS §测试随重构: 覆盖 data/catalog/seed/backtest/feature/gp/feedback/bridge
等子命令的参数校验、错误分支与辅助函数，目标将 fts/cli.py 覆盖率提升至 90%+。
"""

from __future__ import annotations

import io
import json
import sys
from argparse import Namespace
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.bridge import BridgeError
from fts.cli import (
    _build_default_aggregator,
    _cmd_backtest_batch,
    _cmd_backtest_compare,
    _cmd_backtest_run,
    _cmd_bridge_publish,
    _cmd_bridge_serve,
    _cmd_bridge_status,
    _cmd_catalog_backup,
    _cmd_catalog_stats,
    _cmd_catalog_verify,
    _cmd_data_cross_check,
    _cmd_data_fuse,
    _cmd_data_status,
    _cmd_data_sync,
    _cmd_factor_cross_market,
    _cmd_factor_list,
    _cmd_factor_seeds,
    _cmd_feature_analyze,
    _cmd_feature_list,
    _cmd_feedback_process,
    _cmd_feedback_report,
    _cmd_feedback_stats,
    _cmd_feedback_trigger,
    _cmd_gp_evolve,
    _cmd_seed_dedup,
    _cmd_seed_report,
    _cmd_seed_validate,
    _get_catalog_db_path,
    _load_factor_by_id,
    _prepare_cross_section_data,
    _prepare_data,
    _print_backtest_ranking,
    main,
)


def _close_df(n: int = 10) -> pd.DataFrame:
    """构造仅含 close 列的小 DataFrame。"""
    return pd.DataFrame({"close": np.arange(n, dtype=float)})


def _parse_json(out: str) -> dict | list:
    """从 stdout 解析 JSON（跳过 trace_id/[data fuse] 等前缀，支持多行 indent 输出）。"""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(out):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(out[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"stdout 中未找到 JSON: {out!r}")


# ═══════════════════════════════════════════════════════════
# 数据准备辅助函数
# ═══════════════════════════════════════════════════════════

class TestPrepareData:
    """测试 _prepare_data（单标数据准备）。"""

    def test_prepare_data_forward_returns(self):
        """长样本计算 forward_returns（5 日平移）。"""
        with patch("fts.cli.FTSDataProvider") as m_provider:
            m_provider.return_value.get_ohlcv.return_value = _close_df(10)
            df, fwd = _prepare_data("000001", days=500)
        assert len(df) == 10
        assert len(fwd) == 10
        assert fwd[-5] == 0  # 末尾 5 个为 0
        assert fwd[0] > 0    # (close[5]-close[0])/close[0] > 0

    def test_prepare_data_short_sample(self):
        """样本过短（≤5）时 forward_returns 全零。"""
        with patch("fts.cli.FTSDataProvider") as m_provider:
            m_provider.return_value.get_ohlcv.return_value = _close_df(5)
            df, fwd = _prepare_data("000001", days=500)
        assert len(fwd) == 5
        assert (fwd == 0).all()


class TestPrepareCrossSectionData:
    """测试 _prepare_cross_section_data（基本面注入分支）。"""

    def _setup(self, m_provider, m_fp):
        panel = {"000001": _close_df(10)}
        dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        m_provider.return_value.get_csi300_panel.return_value = (panel, dates)
        return panel

    def test_fundamental_inject_success(self, capsys):
        """基本面注入成功分支打印提示。"""
        with patch("fts.cli.FTSDataProvider") as m_provider, \
                patch("fts.data_fundamental.get_fundamental_provider") as m_fp:
            panel = self._setup(m_provider, m_fp)
            m_fp.return_value.enrich_panel.return_value = panel
            result_panel, dates, fwd = _prepare_cross_section_data()
        assert "000001" in result_panel
        assert len(fwd) == 10
        assert "基本面数据注入完成" in capsys.readouterr().out

    def test_fundamental_inject_fails(self, capsys):
        """基本面注入失败时降级为合成数据。"""
        with patch("fts.cli.FTSDataProvider") as m_provider, \
                patch("fts.data_fundamental.get_fundamental_provider") as m_fp:
            panel = self._setup(m_provider, m_fp)
            m_fp.return_value.enrich_panel.side_effect = RuntimeError("mcp down")
            result_panel, dates, fwd = _prepare_cross_section_data()
        assert "000001" in result_panel
        assert "基本面注入失败" in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════
# evolution run — 单标模式
# ═══════════════════════════════════════════════════════════

class TestCmdEvolutionRunSingle:
    """测试 _cmd_evolution_run 单标模式成功路径。"""

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.FactorVerifier")
    @patch("fts.cli.get_default_seed_pool")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli._prepare_data")
    @patch("fts.cli.generate_trace_id", return_value="l2_s_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_s_20260718T000000")
    def test_single_mode_success(
        self, mock_run_id, mock_trace_id, mock_prep,
        mock_llm, mock_seed, mock_verifier, mock_evoloop, capsys,
    ):
        """--universe single 走单标分支并成功完成。"""
        mock_prep.return_value = (_close_df(10), np.zeros(10))
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", generations_completed=1,
            elite_factor_ids=[], circuit_breaker_reason="",
        )
        rc = main(["evolution", "run", "--universe", "single", "--symbol", "600000"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "symbol=600000" in out
        mock_prep.assert_called_once_with(symbol="600000", days=500)


# ═══════════════════════════════════════════════════════════
# meta-loop run — 自定义 symbols
# ═══════════════════════════════════════════════════════════

class TestCmdMetaLoopSymbols:
    """测试 _cmd_meta_loop_run --symbols 解析分支。"""

    @patch("fts.cli.MetaLoop")
    @patch("fts.factor_engine.meta_loop._make_web_collector")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli.generate_trace_id", return_value="l1_sym_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_sym_20260718T000000")
    def test_custom_symbols_parsed(
        self, mock_run_id, mock_trace_id, mock_llm,
        mock_collector, mock_metal, capsys,
    ):
        """逗号分隔 symbols 被解析并传入 MetaLoop。"""
        mock_loop = mock_metal.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", injected_candidate_ids=["c1"],
        )
        rc = main(["meta-loop", "run", "--symbols", "rb,i,au, ,sc"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "自定义感知品种" in out
        # MetaLoop 收到的 sample_symbols 已去空白并小写化
        kwargs = mock_metal.call_args.kwargs
        assert kwargs["sample_symbols"] == ["rb", "i", "au", "sc"]


# ═══════════════════════════════════════════════════════════
# _build_default_aggregator
# ═══════════════════════════════════════════════════════════

class TestBuildDefaultAggregator:
    """测试 _build_default_aggregator（TQ 源 + DuckDB 路径探测）。"""

    def test_with_tq_source_and_db(self, tmp_path):
        """TQ 源可用 + DuckDB 存在时聚合器携带 db_path。"""
        duck = tmp_path / "f.duckdb"
        duck.write_bytes(b"")
        with patch("fts.data_futures._DUCKDB_PATH", duck), \
                patch("fts.data_sources.tq_source.TQLocalSource") as m_tq, \
                patch("fts.data_sources.aggregator.FuturesDataAggregator") as m_agg:
            agg = _build_default_aggregator()
        m_agg.assert_called_once()
        kwargs = m_agg.call_args.kwargs
        assert kwargs["db_path"] == duck
        assert kwargs["cache_max_age_days"] == 30
        assert len(kwargs["sources"]) == 1

    def test_tq_source_init_fails(self, tmp_path):
        """TQ 源初始化失败时静默跳过（sources 为空）。"""
        with patch("fts.data_futures._DUCKDB_PATH", tmp_path / "none.duckdb"), \
                patch("fts.data_sources.tq_source.TQLocalSource",
                      side_effect=RuntimeError("tq unavailable")), \
                patch("fts.data_sources.aggregator.FuturesDataAggregator") as m_agg:
            _build_default_aggregator()
        assert m_agg.call_args.kwargs["sources"] == []
        assert m_agg.call_args.kwargs["db_path"] is None


# ═══════════════════════════════════════════════════════════
# fts data 子命令组
# ═══════════════════════════════════════════════════════════

def _status_dict() -> dict:
    return {"tq": {"total_success": 5, "total_failure": 1,
                   "consecutive_failures": 2, "circuit_open": False}}


class TestCmdDataStatus:
    """测试 _cmd_data_status 各分支。"""

    def test_success_text(self, capsys):
        """状态非空文本输出。"""
        agg = MagicMock()
        agg.get_source_status.return_value = _status_dict()
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_status(Namespace(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "success=5" in out and "circuit_open=False" in out

    def test_success_json(self, capsys):
        """--json 输出 JSON。"""
        agg = MagicMock()
        agg.get_source_status.return_value = _status_dict()
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_status(Namespace(json=True))
        assert rc == 0
        payload = _parse_json(capsys.readouterr().out)
        assert payload["sources"]["tq"]["total_success"] == 5

    def test_empty_status(self, capsys):
        """无活动记录时提示。"""
        agg = MagicMock()
        agg.get_source_status.return_value = {}
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_status(Namespace(json=False))
        assert rc == 0
        assert "暂无源活动记录" in capsys.readouterr().out

    def test_aggregator_error(self, capsys):
        """聚合器异常返回 1。"""
        with patch("fts.cli._build_default_aggregator",
                   side_effect=RuntimeError("boom")):
            rc = _cmd_data_status(Namespace(json=False))
        assert rc == 1
        assert "获取状态失败" in capsys.readouterr().err


class TestCmdDataSync:
    """测试 _cmd_data_sync。"""

    def test_with_symbol(self):
        """指定 symbol 时传入单元素列表。"""
        with patch("fts.scheduler.jobs.sync_futures_data_job") as m_job:
            rc = _cmd_data_sync(Namespace(symbol="RB0", days=120))
        assert rc == 0
        m_job.assert_called_once_with(symbols=["RB0"], days=120)

    def test_without_symbol(self):
        """未指定 symbol 时传 None（全部核心品种）。"""
        with patch("fts.scheduler.jobs.sync_futures_data_job") as m_job:
            rc = _cmd_data_sync(Namespace(symbol=None, days=90))
        assert rc == 0
        m_job.assert_called_once_with(symbols=None, days=90)


class TestCmdDataCrossCheck:
    """测试 _cmd_data_cross_check 各分支。"""

    def _disagreements(self) -> list:
        return [{"symbol": "RB0", "date": "2026-08-04",
                 "max_diff_pct": 0.0123, "outliers": [1, 2]}]

    def test_error_returns_2(self, capsys):
        """交叉验证异常返回 2。"""
        agg = MagicMock()
        agg.cross_check.side_effect = RuntimeError("boom")
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_cross_check(Namespace(symbol="RB0", date="2026-08-04", json=False))
        assert rc == 2
        assert "交叉验证失败" in capsys.readouterr().err

    def test_json_with_disagreements(self, capsys):
        """json + 有分歧返回 1。"""
        agg = MagicMock()
        agg.cross_check.return_value = self._disagreements()
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_cross_check(Namespace(symbol="RB0", date="2026-08-04", json=True))
        assert rc == 1
        payload = _parse_json(capsys.readouterr().out)
        assert len(payload["disagreements"]) == 1

    def test_json_no_disagreements(self, capsys):
        """json + 无分歧返回 0。"""
        agg = MagicMock()
        agg.cross_check.return_value = []
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_cross_check(Namespace(symbol="RB0", date="2026-08-04", json=True))
        assert rc == 0

    def test_text_no_disagreements(self, capsys):
        """文本模式无分歧输出'无分歧'。"""
        agg = MagicMock()
        agg.cross_check.return_value = []
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_cross_check(Namespace(symbol="RB0", date="2026-08-04", json=False))
        assert rc == 0
        assert "无分歧" in capsys.readouterr().out

    def test_text_with_disagreements(self, capsys):
        """文本模式有分歧逐条打印并返回 1。"""
        agg = MagicMock()
        agg.cross_check.return_value = self._disagreements()
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_cross_check(Namespace(symbol="RB0", date="2026-08-04", json=False))
        assert rc == 1
        out = capsys.readouterr().out
        assert "max_diff_pct=0.0123" in out


class TestCmdDataFuse:
    """测试 _cmd_data_fuse 各分支（策略校验/聚合器/无源/成功/异常）。"""

    def _agg(self, n_sources: int = 1, fail_fetch: bool = False) -> MagicMock:
        srcs = []
        for i in range(n_sources):
            s = MagicMock(source_name=f"src{i}")
            if fail_fetch:
                s.fetch_ohlcv_or_none.side_effect = RuntimeError("fetch fail")
            else:
                s.fetch_ohlcv_or_none.return_value = pd.DataFrame(
                    {"date": ["2026-08-04"], "close": [1.0]},
                )
            srcs.append(s)
        agg = MagicMock(sources=srcs, enhancers=[])
        agg._is_circuit_open.return_value = False
        agg.cross_check.return_value = []
        return agg

    def test_invalid_strategy(self, capsys):
        """未知融合策略返回 2。"""
        args = Namespace(symbol="RB0", strategy="FOO", days=30, json=False, output=None)
        rc = _cmd_data_fuse(args)
        assert rc == 2
        assert "未知策略" in capsys.readouterr().err

    def test_aggregator_init_fails(self, capsys):
        """聚合器初始化失败返回 2。"""
        with patch("fts.cli._build_default_aggregator",
                   side_effect=RuntimeError("no aggregator")):
            rc = _cmd_data_fuse(Namespace(symbol="RB0", strategy="MEDIAN",
                                          days=30, json=False, output=None))
        assert rc == 2
        assert "聚合器初始化失败" in capsys.readouterr().err

    def test_no_source_provides_data(self, capsys):
        """所有源无数据（熔断跳过 + 拉取失败）返回 1。"""
        agg = self._agg(n_sources=2, fail_fetch=True)
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_fuse(Namespace(symbol="RB0", strategy="MEDIAN",
                                          days=30, json=False, output=None))
        assert rc == 1
        assert "没有任何源提供数据" in capsys.readouterr().err
        agg._record_failure.assert_called()

    def test_all_sources_circuit_open(self, capsys):
        """熔断器全部打开时跳过所有源。"""
        agg = self._agg(n_sources=1)
        agg._is_circuit_open.return_value = True
        with patch("fts.cli._build_default_aggregator", return_value=agg):
            rc = _cmd_data_fuse(Namespace(symbol="RB0", strategy="MEDIAN",
                                          days=30, json=False, output=None))
        assert rc == 1

    def test_success_json_with_output(self, tmp_path, capsys):
        """成功路径：融合 + 交叉验证 + JSON 输出 + 落盘。"""
        agg = self._agg(n_sources=1)
        mock_fuser = MagicMock()
        mock_fuser.fuse_dataframe.return_value = pd.DataFrame(
            {"date": ["2026-08-04"], "close": [1.0]},
        )
        out_file = tmp_path / "fuse_report.json"
        with patch("fts.cli._build_default_aggregator", return_value=agg), \
                patch("fts.data_sources.fusion.OHLCVFusion", return_value=mock_fuser):
            rc = _cmd_data_fuse(Namespace(symbol="RB0", strategy="MEDIAN",
                                          days=30, json=True, output=str(out_file)))
        assert rc == 0
        assert out_file.exists()
        payload = _parse_json(capsys.readouterr().out)
        assert payload["strategy"] == "MEDIAN"
        assert payload["sources_used"] == ["src0"]
        agg._record_success.assert_called_once()
        agg.cross_check.assert_called_once()

    def test_success_text_no_output(self, capsys):
        """成功路径文本摘要（无落盘无 JSON）。"""
        agg = self._agg(n_sources=1)
        mock_fuser = MagicMock()
        mock_fuser.fuse_dataframe.return_value = pd.DataFrame(
            {"date": ["2026-08-04"], "close": [1.0]},
        )
        with patch("fts.cli._build_default_aggregator", return_value=agg), \
                patch("fts.data_sources.fusion.OHLCVFusion", return_value=mock_fuser):
            rc = _cmd_data_fuse(Namespace(symbol="RB0", strategy="MEAN",
                                          days=30, json=False, output=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "strategy=MEAN" in out

    def test_cross_check_raises(self, tmp_path, capsys):
        """交叉验证异常时 disagreements 降级为空。"""
        agg = self._agg(n_sources=1)
        agg.cross_check.side_effect = RuntimeError("boom")
        mock_fuser = MagicMock()
        mock_fuser.fuse_dataframe.return_value = pd.DataFrame(
            {"date": ["2026-08-04"], "close": [1.0]},
        )
        with patch("fts.cli._build_default_aggregator", return_value=agg), \
                patch("fts.data_sources.fusion.OHLCVFusion", return_value=mock_fuser):
            rc = _cmd_data_fuse(Namespace(symbol="RB0", strategy="WEIGHTED",
                                          days=30, json=True, output=None))
        assert rc == 0
        payload = _parse_json(capsys.readouterr().out)
        assert payload["disagreements"] == []


# ═══════════════════════════════════════════════════════════
# portfolio run — futures 分支 + 信号管道
# ═══════════════════════════════════════════════════════════

class TestCmdPortfolioRunFutures:
    """测试 _cmd_portfolio_run futures 分支及信号管道触发。"""

    def _setup(self, mock_cfg, mock_port, status: str = "passed"):
        cfg = mock_cfg.return_value
        cfg.get_elite_dir.return_value = "/tmp/elite"
        cfg.memory_dir = "/tmp/memory"
        cfg.verifier = {"max_correlation": 0.4}
        mock_loop = mock_port.return_value
        mock_loop.run.return_value = MagicMock(
            status=status, n_factors_retained=3, combo_sharpe=1.2,
        )

    @patch("scripts.futures_signal_pipeline.main", return_value=0)
    @patch("fts.cli.PortfolioLoop")
    @patch("fts.cli.get_config")
    def test_futures_triggers_signal_pipeline(self, mock_cfg, mock_port, mock_signal, capsys):
        """futures + passed 触发期货信号管道。"""
        self._setup(mock_cfg, mock_port)
        rc = main(["portfolio", "run", "--universe", "futures"])
        assert rc == 0
        mock_signal.assert_called_once_with(max_symbols=82, days=120, universe="all")
        assert "触发期货信号生成管道" in capsys.readouterr().out

    @patch("scripts.futures_signal_pipeline.main", return_value=3)
    @patch("fts.cli.PortfolioLoop")
    @patch("fts.cli.get_config")
    def test_futures_signal_pipeline_nonzero_rc(self, mock_cfg, mock_port, mock_signal, capsys):
        """信号管道非零退出时打印告警但仍返回 0。"""
        self._setup(mock_cfg, mock_port)
        rc = main(["portfolio", "run", "--universe", "futures"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "信号管道异常退出: rc=3" in captured.err

    @patch("scripts.futures_signal_pipeline.main")
    @patch("fts.cli.PortfolioLoop")
    @patch("fts.cli.get_config")
    def test_futures_status_not_triggering(self, mock_cfg, mock_port, mock_signal, capsys):
        """状态不在触发集时不调用信号管道。"""
        self._setup(mock_cfg, mock_port, status="failed")
        rc = main(["portfolio", "run", "--universe", "futures"])
        assert rc == 0
        mock_signal.assert_not_called()


# ═══════════════════════════════════════════════════════════
# fts catalog 子命令组
# ═══════════════════════════════════════════════════════════

class TestCmdCatalogStats:
    """测试 _cmd_catalog_stats 各分支。"""

    def _cfg(self, elite_stock: str, elite_futures: str) -> MagicMock:
        cfg = MagicMock()
        cfg.get_elite_dir.side_effect = lambda m: (
            elite_futures if m == "futures" else elite_stock
        )
        return cfg

    def test_db_missing_text(self, tmp_path, capsys):
        """DuckDB 不存在 + JSON 目录不存在（文本模式）。"""
        cfg = self._cfg(str(tmp_path / "s"), str(tmp_path / "f"))
        with patch("fts.cli._get_catalog_db_path",
                   return_value=tmp_path / "none.duckdb"), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_stats(Namespace(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "不存在" in out
        assert "FUTURES JSON 文件" in out

    def test_db_exists_repo_ok(self, tmp_path, capsys):
        """DuckDB 存在且统计正常。"""
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x" * 1024)
        repo = MagicMock()
        repo.get_stats.return_value = {
            "total_factors": 10, "active_factors": 8, "elite_factors": 3,
            "avg_sharpe": 0.8, "avg_ic": 0.05,
        }
        cfg = self._cfg(str(tmp_path / "s"), str(tmp_path / "f"))
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", return_value=repo), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_stats(Namespace(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "总因子: 10" in out

    def test_db_exists_repo_error_json(self, tmp_path, capsys):
        """DuckDB 读取失败时记录 duckdb_error（JSON 模式不崩）。"""
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        repo = MagicMock()
        repo.get_stats.side_effect = RuntimeError("conn broken")
        cfg = self._cfg(str(tmp_path / "s"), str(tmp_path / "f"))
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", return_value=repo), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_stats(Namespace(json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert "duckdb_error" in payload

    def test_db_exists_repo_error_text_bug(self, tmp_path, capsys):
        """[已修复] 文本模式 + DuckDB 读取失败 → size 兜底为 0 不崩溃。

        修复前：database_size_mb 缺失时 f-string ':'。1f 抛 ValueError；
        修复后：兜底为 0.0，命令正常返回。
        """
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        repo = MagicMock()
        repo.get_stats.side_effect = RuntimeError("conn broken")
        cfg = self._cfg(str(tmp_path / "s"), str(tmp_path / "f"))
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", return_value=repo), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_stats(Namespace(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "大小: 0.0 MB" in out

    def test_json_dir_with_retired(self, tmp_path, capsys):
        """JSON 目录含普通文件与 _retired 子目录被正确统计（内部文件跳过）。"""
        futures_dir = tmp_path / "elite_futures"
        futures_dir.mkdir()
        (futures_dir / "F1.json").write_text("{}", encoding="utf-8")
        (futures_dir / "_index.json").write_text("{}", encoding="utf-8")
        retired = futures_dir / "_retired"
        retired.mkdir()
        (retired / "R1.json").write_text("{}", encoding="utf-8")
        cfg = self._cfg(str(tmp_path / "elite_stock"), str(futures_dir))
        with patch("fts.cli._get_catalog_db_path",
                   return_value=tmp_path / "none.duckdb"), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_stats(Namespace(json=True))
        assert rc == 0
        payload = _parse_json(capsys.readouterr().out)
        assert payload["futures_json_files"] == 1
        assert payload["futures_retired_files"] == 1


class TestGetCatalogDbPath:
    """测试 _get_catalog_db_path。"""

    def test_returns_schema_path(self, tmp_path):
        """返回 factor_db.schema.DATABASE_PATH 的 Path。"""
        fake = tmp_path / "db.duckdb"
        with patch("fts.factor_engine.factor_db.schema.DATABASE_PATH", fake):
            p = _get_catalog_db_path()
        assert p == fake


class TestCmdCatalogVerify:
    """测试 _cmd_catalog_verify 各分支。"""

    def _repo(self, duck_ids: list[tuple], query_fail: bool = False) -> MagicMock:
        conn = MagicMock()
        if query_fail:
            conn.execute.side_effect = RuntimeError("sql fail")
        else:
            conn.execute.return_value.fetchall.return_value = duck_ids
        repo = MagicMock()
        repo._get_conn.return_value = conn
        return repo

    def _cfg(self, elite_dir: Path) -> MagicMock:
        cfg = MagicMock()
        cfg.get_elite_dir.side_effect = lambda m: str(elite_dir)
        return cfg

    def test_db_missing(self, tmp_path, capsys):
        """DuckDB 不存在返回 1。"""
        cfg = self._cfg(tmp_path / "elite")
        with patch("fts.cli._get_catalog_db_path",
                   return_value=tmp_path / "none.duckdb"), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_verify(Namespace(json=False))
        assert rc == 1
        assert "数据库不存在" in capsys.readouterr().err

    def test_repo_load_fails(self, tmp_path, capsys):
        """仓储连接失败返回 1。"""
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        cfg = self._cfg(tmp_path / "elite")
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", side_effect=RuntimeError("conn")), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_verify(Namespace(json=False))
        assert rc == 1
        assert "DuckDB 连接失败" in capsys.readouterr().err

    def test_query_fails(self, tmp_path, capsys):
        """SQL 查询失败返回 1。"""
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        cfg = self._cfg(tmp_path / "elite")
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", return_value=self._repo([], query_fail=True)), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_verify(Namespace(json=False))
        assert rc == 1
        assert "DuckDB 查询失败" in capsys.readouterr().err

    def _write_json_factor(self, elite_dir: Path, fid: str, content: str | None = None) -> None:
        elite_dir.mkdir(parents=True, exist_ok=True)
        (elite_dir / f"{fid}.json").write_text(
            content if content is not None else json.dumps({"factor_id": fid, "market": "futures"}),
            encoding="utf-8",
        )

    def test_consistent_json_mode(self, tmp_path, capsys):
        """JSON 与 DuckDB 一致 → json 模式返回 0。"""
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        elite_dir = tmp_path / "elite"
        self._write_json_factor(elite_dir, "F1")
        repo = self._repo([("F1", "n", "futures", 1, "active")])
        cfg = self._cfg(elite_dir)
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", return_value=repo), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_verify(Namespace(json=True))
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["consistent"] is True

    def test_inconsistent_text_mode(self, tmp_path, capsys):
        """DuckDB 独有 + JSON 独有 → 文本模式返回 1 并打印明细。

        覆盖内部文件跳过（_index.json）、factor_id 缺失回退文件名（NOID.json）。
        """
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        elite_dir = tmp_path / "elite"
        # JSON 独有 F1；坏 JSON 文件被跳过；内部索引文件被跳过；无 factor_id 回退文件名
        self._write_json_factor(elite_dir, "F1")
        self._write_json_factor(elite_dir, "BAD", content="not-json{{{")
        (elite_dir / "_index.json").write_text("{}", encoding="utf-8")
        (elite_dir / "NOID.json").write_text(json.dumps({"market": "futures"}),
                                             encoding="utf-8")
        repo = self._repo([("D1", "n", "futures", 1, "active")])
        cfg = self._cfg(elite_dir)
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", return_value=repo), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_verify(Namespace(json=False))
        assert rc == 1
        out = capsys.readouterr().out
        assert "⚠️ 不一致" in out
        assert "DuckDB 独有" in out
        assert "JSON 独有: F1" in out
        assert "JSON 独有: NOID" in out

    def test_consistent_text_mode(self, tmp_path, capsys):
        """JSON 与 DuckDB 一致 → 文本模式打印 ✅ 一致并返回 0。"""
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        elite_dir = tmp_path / "elite"
        self._write_json_factor(elite_dir, "F1")
        repo = self._repo([("F1", "n", "futures", 1, "active")])
        cfg = self._cfg(elite_dir)
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli._load_factor_repo", return_value=repo), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_verify(Namespace(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "✅ 一致" in out


class TestCmdCatalogBackup:
    """测试 _cmd_catalog_backup 各分支。"""

    def _cfg(self, elite_futures: Path, elite_stock: Path) -> MagicMock:
        cfg = MagicMock()
        cfg.get_elite_dir.side_effect = lambda m: (
            str(elite_futures) if m == "futures" else str(elite_stock)
        )
        return cfg

    def test_no_db_no_json(self, tmp_path, monkeypatch, capsys):
        """无 DuckDB + 无 JSON 目录（json 模式）。"""
        monkeypatch.chdir(tmp_path)
        cfg = self._cfg(tmp_path / "ef", tmp_path / "es")
        with patch("fts.cli._get_catalog_db_path",
                   return_value=tmp_path / "none.duckdb"), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_backup(Namespace(json=True))
        assert rc == 0
        payload = _parse_json(capsys.readouterr().out)
        assert payload["timestamp"]
        assert (tmp_path / "data" / "backups").is_dir()

    def test_full_backup_success(self, tmp_path, monkeypatch, capsys):
        """DuckDB + JSON（含 _retired）全量备份。"""
        monkeypatch.chdir(tmp_path)
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"db-data")
        futures_dir = tmp_path / "ef"
        futures_dir.mkdir()
        (futures_dir / "F1.json").write_text("{}", encoding="utf-8")
        (futures_dir / "_index.json").write_text("{}", encoding="utf-8")  # 跳过
        retired = futures_dir / "_retired"
        retired.mkdir()
        (retired / "R1.json").write_text("{}", encoding="utf-8")
        cfg = self._cfg(futures_dir, tmp_path / "es")
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_catalog_backup(Namespace(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "DuckDB" in out and "FUTURES JSON" in out
        backup_dir = tmp_path / "data" / "backups"
        backups = list(backup_dir.glob("futures_elite_*"))
        assert len(backups) == 1
        assert (backups[0] / "_retired" / "R1.json").exists()

    def test_backup_copy_error(self, tmp_path, monkeypatch, capsys):
        """复制失败打印错误但仍返回 0。"""
        monkeypatch.chdir(tmp_path)
        db = tmp_path / "f.duckdb"
        db.write_bytes(b"x")
        futures_dir = tmp_path / "ef"
        futures_dir.mkdir()
        (futures_dir / "F1.json").write_text("{}", encoding="utf-8")
        cfg = self._cfg(futures_dir, tmp_path / "es")
        with patch("fts.cli._get_catalog_db_path", return_value=db), \
                patch("fts.cli.get_config", return_value=cfg), \
                patch("shutil.copy2", side_effect=OSError("disk full")):
            rc = _cmd_catalog_backup(Namespace(json=True))
        assert rc == 0
        payload = _parse_json(capsys.readouterr().out)
        assert "duckdb_error" in payload
        assert "futures_json_error" in payload


# ═══════════════════════════════════════════════════════════
# factor list — get_eligible / JSON 模式
# ═══════════════════════════════════════════════════════════

class TestCmdFactorListExtra:
    """测试 _cmd_factor_list 的 get_eligible 与 JSON 分支。"""

    def test_get_eligible(self, capsys):
        """min_ic 设置且无 family/diverse → get_eligible。"""
        repo = MagicMock()
        repo.get_eligible.return_value = [
            {"factor_id": "F1", "name": "trend_a", "family": "trend", "market": "futures"},
        ]
        args = Namespace(elite_dir=None, market="futures", family=None, min_ic=0.1,
                         min_sharpe=None, diverse=False, total_count=10,
                         max_per_family=3, limit=50, json=False)
        with patch("fts.cli._load_factor_repo", return_value=repo):
            rc = _cmd_factor_list(args)
        assert rc == 0
        repo.get_eligible.assert_called_once()
        assert "F1" in capsys.readouterr().out

    def test_json_mode(self, tmp_path, capsys):
        """目录模式 + --json 输出 JSON 数组。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "F1.json").write_text(json.dumps({
            "factor_id": "F1", "name": "trend_a",
        }), encoding="utf-8")
        args = Namespace(elite_dir=str(elite_dir), market="futures", family=None,
                         min_ic=None, min_sharpe=None, diverse=False, total_count=10,
                         max_per_family=3, limit=50, json=True)
        rc = _cmd_factor_list(args)
        assert rc == 0
        payload = _parse_json(capsys.readouterr().out)
        assert payload[0]["factor_id"] == "F1"


# ═══════════════════════════════════════════════════════════
# factor seeds / cross-market
# ═══════════════════════════════════════════════════════════

class TestCmdFactorSeeds:
    """测试 _cmd_factor_seeds 期货/股票两条路径。

    注: 产品代码 `from fts.factor_engine.seed_data import load_stock_seeds`
    引用了不存在的属性（真实 bug），测试通过 monkeypatch 注入以覆盖分支。
    """

    @patch("fts.factor_engine.seed_data_futures_full.load_futures_seeds_full")
    def test_futures(self, mock_load, capsys):
        """期货种子因子列表。"""
        mock_load.return_value = [
            {"name": "momentum_10", "signature": {"input_fields": ["close"]}, "params": {}},
        ]
        rc = _cmd_factor_seeds(Namespace(market="futures"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "期货种子因子" in out
        assert "momentum_10" in out

    def test_stock(self, monkeypatch, capsys):
        """股票种子因子列表（WQ101/Qlib158/GTJA191 外部种子）。"""
        from fts.factor_engine import seed_data as seed_data_mod

        monkeypatch.setattr(seed_data_mod, "load_all_external_seeds",
                            lambda trace_id: [
                                {"name": "reversal", "signature": {"input_fields": ["close"]},
                                 "params": {}},
                            ], raising=False)
        rc = _cmd_factor_seeds(Namespace(market="stock"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "股票种子因子" in out
        assert "reversal" in out


class TestCmdFactorCrossMarket:
    """测试 _cmd_factor_cross_market 各方向与异常分支。"""

    def _engine(self, report=None) -> MagicMock:
        engine = MagicMock()
        engine.run_futures_to_stock.return_value = report
        engine.run_futures_to_etf.return_value = report
        engine.run_stock_to_futures.return_value = report
        engine.generate_report.return_value = "/tmp/report.md"
        return engine

    def _report(self) -> MagicMock:
        return MagicMock(n_universal=5, n_market_specific=3, n_failed=1, n_deprecated=1)

    def test_futures_to_stock_with_max_stocks(self, capsys):
        """futures-to-stock + max_stocks>0 打印成分股信息。"""
        engine = self._engine(self._report())
        with patch("fts.cross_market.CrossMarketDataAdapter"), \
                patch("fts.cross_market.CrossMarketEngine", return_value=engine):
            rc = _cmd_factor_cross_market(Namespace(
                direction="futures-to-stock", days=120, max_factors=10,
                max_stocks=50, output_dir=None,
            ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "期货→A股" in out
        assert "最大成分股: 50" in out
        engine.generate_report.assert_called_once()

    def test_futures_to_stock_zero_stocks(self, capsys):
        """max_stocks=0 显示'全量'。"""
        engine = self._engine(self._report())
        with patch("fts.cross_market.CrossMarketDataAdapter"), \
                patch("fts.cross_market.CrossMarketEngine", return_value=engine):
            rc = _cmd_factor_cross_market(Namespace(
                direction="futures-to-stock", days=120, max_factors=0,
                max_stocks=0, output_dir=None,
            ))
        assert rc == 0
        assert "最大成分股: 全量" in capsys.readouterr().out

    @pytest.mark.parametrize("direction,attr", [
        ("futures-to-etf", "run_futures_to_etf"),
        ("stock-to-futures", "run_stock_to_futures"),
    ])
    def test_other_directions(self, direction, attr, capsys):
        """另外两个方向正常执行。"""
        engine = self._engine(self._report())
        with patch("fts.cross_market.CrossMarketDataAdapter"), \
                patch("fts.cross_market.CrossMarketEngine", return_value=engine):
            rc = _cmd_factor_cross_market(Namespace(
                direction=direction, days=120, max_factors=5,
                max_stocks=0, output_dir=None,
            ))
        assert rc == 0
        getattr(engine, attr).assert_called_once()

    def test_output_dir(self, tmp_path, capsys):
        """指定 output_dir 时生成带日期的报告路径。"""
        engine = self._engine(self._report())
        with patch("fts.cross_market.CrossMarketDataAdapter"), \
                patch("fts.cross_market.CrossMarketEngine", return_value=engine):
            rc = _cmd_factor_cross_market(Namespace(
                direction="futures-to-stock", days=120, max_factors=5,
                max_stocks=0, output_dir=str(tmp_path),
            ))
        assert rc == 0
        out_path = engine.generate_report.call_args.kwargs["output_path"]
        assert str(out_path).endswith(".md")

    def test_import_error(self, capsys):
        """CrossMarketDataAdapter 构造抛 ImportError 时返回 1。

        注: `from fts.cross_market import ...` 在函数 try 块之外执行，
        模块整体缺失时 ModuleNotFoundError 无法被 except ImportError 捕获（真实 bug）。
        """
        with patch("fts.cross_market.CrossMarketDataAdapter",
                   side_effect=ImportError("missing dep")):
            rc = _cmd_factor_cross_market(Namespace(
                direction="futures-to-stock", days=120, max_factors=5,
                max_stocks=0, output_dir=None,
            ))
        assert rc == 1
        assert "导入失败" in capsys.readouterr().err

    def test_generic_error(self, capsys):
        """执行异常返回 1。"""
        engine = self._engine()
        engine.run_futures_to_stock.side_effect = RuntimeError("boom")
        with patch("fts.cross_market.CrossMarketDataAdapter"), \
                patch("fts.cross_market.CrossMarketEngine", return_value=engine):
            rc = _cmd_factor_cross_market(Namespace(
                direction="futures-to-stock", days=120, max_factors=5,
                max_stocks=0, output_dir=None,
            ))
        assert rc == 1
        assert "执行失败" in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════
# fts seed 子命令组
# ═══════════════════════════════════════════════════════════

class TestCmdSeedValidate:
    """测试 _cmd_seed_validate 校验/去重组合。"""

    @patch("scripts.unified_factor_converter.check_duplicates")
    @patch("scripts.unified_factor_converter.validate_all")
    @patch("scripts.unified_factor_converter.load_all_factors")
    def test_all_pass(self, mock_load, mock_validate, mock_dup, capsys):
        """校验与去重均通过返回 0。"""
        mock_load.return_value = [{"name": "a"}]
        mock_validate.return_value = []
        mock_dup.return_value = []
        rc = _cmd_seed_validate(Namespace(market="futures"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "所有因子验证通过" in out
        assert "无跨文件重复" in out

    @patch("scripts.unified_factor_converter.check_duplicates")
    @patch("scripts.unified_factor_converter.validate_all")
    @patch("scripts.unified_factor_converter.load_all_factors")
    def test_validation_errors(self, mock_load, mock_validate, mock_dup, capsys):
        """校验错误 + 无重复返回 1。"""
        mock_load.return_value = [{"name": "a"}]
        mock_validate.return_value = {"F1": ["missing code"]}
        mock_dup.return_value = []
        rc = _cmd_seed_validate(Namespace(market="futures"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "发现 1 个因子存在问题" in out
        assert "无跨文件重复" in out

    @patch("scripts.unified_factor_converter.check_duplicates")
    @patch("scripts.unified_factor_converter.validate_all")
    @patch("scripts.unified_factor_converter.load_all_factors")
    def test_duplicates_found(self, mock_load, mock_validate, mock_dup, capsys):
        """存在跨文件重复返回 1。"""
        mock_load.return_value = [{"name": "a"}]
        mock_validate.return_value = []
        mock_dup.return_value = ["F1 duplicated"]
        rc = _cmd_seed_validate(Namespace(market="stock"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "F1 duplicated" in out


class TestCmdSeedReport:
    """测试 _cmd_seed_report。"""

    @patch("scripts.unified_factor_converter.generate_report", return_value="== report ==")
    @patch("scripts.unified_factor_converter.load_all_factors")
    def test_report(self, mock_load, mock_gen, capsys):
        """生成统计报告并打印。"""
        mock_load.return_value = [{"name": "a"}]
        rc = _cmd_seed_report(Namespace(market="futures"))
        assert rc == 0
        assert "== report ==" in capsys.readouterr().out


class TestCmdSeedDedup:
    """测试 _cmd_seed_dedup。"""

    @patch("scripts.unified_factor_converter.check_duplicates")
    @patch("scripts.unified_factor_converter.load_all_factors")
    def test_no_duplicates(self, mock_load, mock_dup, capsys):
        """无重复返回 0。"""
        mock_load.return_value = []
        mock_dup.return_value = []
        rc = _cmd_seed_dedup(Namespace(market="futures"))
        assert rc == 0
        assert "无跨文件重复" in capsys.readouterr().out

    @patch("scripts.unified_factor_converter.check_duplicates")
    @patch("scripts.unified_factor_converter.load_all_factors")
    def test_duplicates(self, mock_load, mock_dup, capsys):
        """有重复返回 1。"""
        mock_load.return_value = []
        mock_dup.return_value = ["dup: F1"]
        rc = _cmd_seed_dedup(Namespace(market="futures"))
        assert rc == 1
        assert "dup: F1" in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════
# backtest 子命令组
# ═══════════════════════════════════════════════════════════

class TestLoadFactorById:
    """测试 _load_factor_by_id（JSON 目录 + DuckDB 回退）。"""

    def test_from_json_file(self, tmp_path):
        """优先从 elite 目录 JSON 读取。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "F1.json").write_text(json.dumps({"factor_id": "F1", "name": "n"}),
                                          encoding="utf-8")
        cfg = MagicMock()
        cfg.get_elite_dir.return_value = str(elite_dir)
        with patch("fts.cli.get_config", return_value=cfg):
            f = _load_factor_by_id("F1", "futures")
        assert f["factor_id"] == "F1"

    def test_json_read_fails_falls_back_to_repo(self, tmp_path):
        """JSON 读取失败回退 DuckDB。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "F1.json").write_text("not-json", encoding="utf-8")
        repo = MagicMock()
        repo.get_by_id.return_value = {"factor_id": "F1", "source": "db"}
        cfg = MagicMock()
        cfg.get_elite_dir.return_value = str(elite_dir)
        with patch("fts.cli.get_config", return_value=cfg), \
                patch("fts.cli._load_factor_repo", return_value=repo):
            f = _load_factor_by_id("F1", "futures")
        assert f["source"] == "db"

    def test_repo_fails_returns_none(self, tmp_path, capsys):
        """目录不存在且 DuckDB 失败返回 None。"""
        repo = MagicMock()
        repo.get_by_id.side_effect = RuntimeError("db down")
        cfg = MagicMock()
        cfg.get_elite_dir.return_value = str(tmp_path / "none")
        with patch("fts.cli.get_config", return_value=cfg), \
                patch("fts.cli._load_factor_repo", return_value=repo):
            f = _load_factor_by_id("F1", "futures")
        assert f is None
        assert "因子加载失败" in capsys.readouterr().err


def _backtest_report() -> SimpleNamespace:
    """构造带完整指标的回测报告。"""
    m = SimpleNamespace(
        total_return=0.1, annual_return=0.2, sharpe_ratio=1.5, max_drawdown=0.05,
        calmar_ratio=2.0, win_rate=0.55, payoff_ratio=1.3, profit_factor=1.8,
        ic_mean=0.04, ic_ir=0.9, turnover=0.3,
    )
    return SimpleNamespace(
        factor_id="F1", start_date="2026-01-01", end_date="2026-08-01", metrics=m,
    )


class TestCmdBacktestRun:
    """测试 _cmd_backtest_run 各分支。"""

    def _factor(self) -> dict:
        return {"factor_id": "F1", "code": "close", "name": "n"}

    def test_factor_not_found(self, capsys):
        """因子不存在返回 1。"""
        with patch("fts.cli._load_factor_by_id", return_value=None):
            rc = _cmd_backtest_run(Namespace(factor_id="NOPE", market="futures",
                                             frequency="daily", start=None, end=None,
                                             symbol="RB0", days=500, capital=1e6, output=None))
        assert rc == 1
        assert "未找到因子" in capsys.readouterr().out

    def test_daily_success(self, capsys):
        """日频回测成功打印指标。"""
        report = _backtest_report()
        with patch("fts.cli._load_factor_by_id", return_value=self._factor()), \
                patch("fts.cli._prepare_data", return_value=(_close_df(10), np.zeros(10))), \
                patch("fts.factor_engine.backtest_pipeline.BacktestPipeline") as m_pipe:
            m_pipe.return_value.run.return_value = SimpleNamespace(
                success=True, error="", output=report,
            )
            rc = _cmd_backtest_run(Namespace(factor_id="F1", market="futures",
                                             frequency="daily", start="2026-01-01",
                                             end="2026-02-01", symbol="RB0", days=500,
                                             capital=1e6, output=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "回测结果: F1" in out
        assert "Sharpe: 1.500" in out
        # date_range 被传入 BacktestInput
        bt_input = m_pipe.return_value.run.call_args.args[0]
        assert bt_input.date_range == ("2026-01-01", "2026-02-01")

    def test_daily_failure(self, capsys):
        """回测失败返回 1。"""
        with patch("fts.cli._load_factor_by_id", return_value=self._factor()), \
                patch("fts.cli._prepare_data", return_value=(_close_df(10), np.zeros(10))), \
                patch("fts.factor_engine.backtest_pipeline.BacktestPipeline") as m_pipe:
            m_pipe.return_value.run.return_value = SimpleNamespace(
                success=False, error="backtest crashed", output=None,
            )
            rc = _cmd_backtest_run(Namespace(factor_id="F1", market="futures",
                                             frequency="daily", start=None, end=None,
                                             symbol="RB0", days=500, capital=1e6, output=None))
        assert rc == 1
        assert "回测失败" in capsys.readouterr().err

    def test_minute_freq_empty_data(self, capsys):
        """分钟级数据获取失败返回 1。"""
        with patch("fts.cli._load_factor_by_id", return_value=self._factor()), \
                patch("fts.data_futures.FuturesDataProvider") as m_provider:
            m_provider.return_value.get_minute_ohlcv.return_value = pd.DataFrame()
            rc = _cmd_backtest_run(Namespace(factor_id="F1", market="futures",
                                             frequency="5m", start=None, end=None,
                                             symbol="RB0", days=500, capital=1e6, output=None))
        assert rc == 1
        assert "分钟数据获取失败" in capsys.readouterr().out

    def test_minute_freq_success_with_output(self, tmp_path, capsys):
        """分钟级回测成功 + 生成报告。"""
        report = _backtest_report()
        minute_df = pd.DataFrame({"close": np.arange(10, dtype=float)})
        with patch("fts.cli._load_factor_by_id", return_value=self._factor()), \
                patch("fts.data_futures.FuturesDataProvider") as m_provider, \
                patch("fts.factor_engine.backtest_pipeline.BacktestPipeline") as m_pipe, \
                patch("fts.factor_engine.report_generator.ReportGenerator") as m_gen:
            m_provider.return_value.get_minute_ohlcv.return_value = minute_df
            m_pipe.return_value.run.return_value = SimpleNamespace(
                success=True, error="", output=report,
            )
            m_gen.return_value.generate.return_value = "/tmp/out/report.html"
            rc = _cmd_backtest_run(Namespace(factor_id="F1", market="futures",
                                             frequency="1m", start=None, end=None,
                                             symbol="RB0", days=500, capital=1e6,
                                             output=str(tmp_path / "out")))
        assert rc == 0
        out = capsys.readouterr().out
        assert "频率: 1m" in out
        assert "报告已生成" in out
        # 分钟路径不经过 _prepare_data
        m_provider.return_value.get_minute_ohlcv.assert_called_once()


class TestCmdBacktestBatchEdge:
    """测试 _cmd_backtest_batch 边界分支。"""

    def test_elite_dir_missing(self, tmp_path, capsys):
        """elite 目录不存在返回 1。"""
        cfg = MagicMock()
        cfg.get_elite_dir.return_value = str(tmp_path / "none")
        with patch("fts.cli.get_config", return_value=cfg):
            rc = _cmd_backtest_batch(Namespace(market="futures", grade="B",
                                               min_score=None, limit=20,
                                               symbol="RB0", days=300, capital=1e6))
        assert rc == 1
        assert "elite 目录不存在" in capsys.readouterr().err

    def test_bad_json_skipped_and_empty_screened(self, tmp_path, capsys):
        """损坏 JSON 跳过 + screener 无结果返回 0。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "BROKEN.json").write_text("not-json", encoding="utf-8")
        cfg = MagicMock()
        cfg.get_elite_dir.return_value = str(elite_dir)
        with patch("fts.cli.get_config", return_value=cfg), \
                patch("fts.factor_engine.factor_screener.FactorScreener") as m_screen:
            m_screen.return_value.screen.return_value = []
            rc = _cmd_backtest_batch(Namespace(market="futures", grade="B",
                                               min_score=None, limit=20,
                                               symbol="RB0", days=300, capital=1e6))
        assert rc == 0
        assert "无符合条件的因子" in capsys.readouterr().out

    def test_bad_json_skipped_and_screen_ok(self, tmp_path, capsys):
        """损坏 JSON 跳过 + 正常因子参与回测。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "BROKEN.json").write_text("not-json", encoding="utf-8")
        (elite_dir / "F1.json").write_text(json.dumps({"factor_id": "F1"}),
                                           encoding="utf-8")
        cfg = MagicMock()
        cfg.get_elite_dir.return_value = str(elite_dir)
        with patch("fts.cli.get_config", return_value=cfg), \
                patch("fts.factor_engine.factor_screener.FactorScreener") as m_screen, \
                patch("fts.cli._prepare_data", return_value=(_close_df(10), np.zeros(10))), \
                patch("fts.factor_engine.backtest_pipeline.BacktestPipeline") as m_pipe:
            m_screen.return_value.screen.return_value = [{"factor_id": "F1"}]
            m_pipe.return_value.run_batch.return_value = []
            rc = _cmd_backtest_batch(Namespace(market="futures", grade="B",
                                               min_score=None, limit=20,
                                               symbol="RB0", days=300, capital=1e6))
        assert rc == 0
        assert "回测对比排名" in capsys.readouterr().out


class TestCmdBacktestCompare:
    """测试 _cmd_backtest_compare。"""

    def test_empty_factor_ids(self, capsys):
        """factor_ids 为空返回 1。"""
        rc = _cmd_backtest_compare(Namespace(factor_ids=" , ", market="futures",
                                             symbol="RB0", days=300, capital=1e6))
        assert rc == 1
        assert "请提供 --factor-ids" in capsys.readouterr().out

    def test_all_factors_fail_to_load(self, capsys):
        """全部因子加载失败返回 1。"""
        with patch("fts.cli._load_factor_by_id", return_value=None):
            rc = _cmd_backtest_compare(Namespace(factor_ids="A,B", market="futures",
                                                 symbol="RB0", days=300, capital=1e6))
        assert rc == 1
        assert "所有因子加载失败" in capsys.readouterr().out

    def test_success(self, capsys):
        """部分因子成功加载并回测排名。"""
        with patch("fts.cli._load_factor_by_id",
                   side_effect=lambda fid, market: {"factor_id": fid}), \
                patch("fts.cli._prepare_data", return_value=(_close_df(10), np.zeros(10))), \
                patch("fts.factor_engine.backtest_pipeline.BacktestPipeline") as m_pipe:
            m_pipe.return_value.run_batch.return_value = []
            rc = _cmd_backtest_compare(Namespace(factor_ids="A,B", market="futures",
                                                 symbol="RB0", days=300, capital=1e6))
        assert rc == 0
        assert "回测对比排名 (0 因子)" in capsys.readouterr().out


class TestPrintBacktestRanking:
    """测试 _print_backtest_ranking 排名表渲染。"""

    def test_with_and_without_report(self, capsys):
        """有 report 打印指标，无 report 打印失败原因。"""
        r1 = SimpleNamespace(
            rank=1, factor_id="F1",
            report=SimpleNamespace(metrics=SimpleNamespace(
                sharpe_ratio=1.5, ic_mean=0.04, max_drawdown=0.05, total_return=0.2,
            )),
            error=None,
        )
        r2 = SimpleNamespace(rank=2, factor_id="F2", report=None, error="boom")
        rc = _print_backtest_ranking([r2, r1])  # 乱序验证排序
        assert rc == 0
        out = capsys.readouterr().out
        assert "F1" in out
        assert "失败: boom" in out


# ═══════════════════════════════════════════════════════════
# feature / gp 子命令组
# ═══════════════════════════════════════════════════════════

class TestCmdFeatureList:
    """测试 _cmd_feature_list 各分支。"""

    def _op(self, name: str = "sma") -> SimpleNamespace:
        return SimpleNamespace(name=name, category="rolling", signature="x -> y")

    def test_empty(self, capsys):
        """无算子提示。"""
        engine = MagicMock()
        engine.registry.list_operators.return_value = []
        with patch("fts.factor_engine.feature_ops.FeatureOpsEngine", return_value=engine):
            rc = _cmd_feature_list(Namespace(category="rolling", json=False))
        assert rc == 0
        assert "无算子" in capsys.readouterr().out

    def test_text_mode(self, capsys):
        """文本表格输出。"""
        engine = MagicMock()
        engine.registry.list_operators.return_value = [self._op()]
        with patch("fts.factor_engine.feature_ops.FeatureOpsEngine", return_value=engine):
            rc = _cmd_feature_list(Namespace(category=None, json=False))
        assert rc == 0
        assert "sma" in capsys.readouterr().out

    def test_json_mode(self, capsys):
        """JSON 模式输出算子字典。"""
        engine = MagicMock()
        engine.registry.list_operators.return_value = [self._op()]
        with patch("fts.factor_engine.feature_ops.FeatureOpsEngine", return_value=engine):
            rc = _cmd_feature_list(Namespace(category=None, json=True))
        assert rc == 0
        payload = _parse_json(capsys.readouterr().out)
        assert payload[0]["name"] == "sma"


class TestCmdFeatureAnalyze:
    """测试 _cmd_feature_analyze 各分支。"""

    def _panel(self, n: int = 30) -> dict:
        return {"RB0": _close_df(n)}

    def test_factor_not_found(self, capsys):
        """因子不存在返回 1。"""
        with patch("fts.cli._load_factor_by_id", return_value=None):
            rc = _cmd_feature_analyze(Namespace(factor_id="NOPE", market="futures",
                                                days=500, output=None))
        assert rc == 1
        assert "未找到因子" in capsys.readouterr().out

    def test_panel_empty(self, capsys):
        """面板数据为空返回 1。"""
        with patch("fts.cli._load_factor_by_id", return_value={"factor_id": "F1"}), \
                patch("fts.cli._prepare_futures_data", return_value=({}, None, np.array([]))):
            rc = _cmd_feature_analyze(Namespace(factor_id="F1", market="futures",
                                                days=500, output=None))
        assert rc == 1
        assert "数据准备失败" in capsys.readouterr().err

    def test_values_none(self, capsys):
        """因子计算失败返回 1。"""
        with patch("fts.cli._load_factor_by_id", return_value={"factor_id": "F1"}), \
                patch("fts.cli._prepare_futures_data",
                      return_value=(self._panel(), pd.DatetimeIndex([]), np.zeros(30))), \
                patch("fts.factor_engine.signal_generator.SignalGenerator._compute_factor_values",
                      return_value=None):
            rc = _cmd_feature_analyze(Namespace(factor_id="F1", market="futures",
                                                days=500, output=None))
        assert rc == 1
        assert "因子计算失败" in capsys.readouterr().err

    def test_success_with_output(self, tmp_path, capsys):
        """成功路径 + 输出文件。"""
        result = SimpleNamespace(
            factor_id="F1", analysis_method="shap", baseline_ic=0.05,
            feature_importance={"close": 0.8, "volume": 0.2},
        )
        with patch("fts.cli._load_factor_by_id", return_value={"factor_id": "F1"}), \
                patch("fts.cli._prepare_futures_data",
                      return_value=(self._panel(), pd.DatetimeIndex([]), np.zeros(30))), \
                patch("fts.factor_engine.signal_generator.SignalGenerator._compute_factor_values",
                      return_value=np.ones(30)), \
                patch("fts.factor_engine.feature_importance.FeatureImportanceAnalyzer") as m_ana:
            m_ana.return_value.analyze.return_value = result
            out_dir = tmp_path / "out"
            rc = _cmd_feature_analyze(Namespace(factor_id="F1", market="futures",
                                                days=500, output=str(out_dir)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "特征重要性: F1" in out
        assert "结果已保存" in out
        assert (out_dir / "feature_importance_F1.json").exists()

    def test_success_no_output(self, capsys):
        """成功路径不落盘。"""
        result = SimpleNamespace(
            factor_id=None, analysis_method="permutation", baseline_ic=0.03,
            feature_importance={"close": 1.0},
        )
        with patch("fts.cli._load_factor_by_id", return_value={"factor_id": "F1"}), \
                patch("fts.cli._prepare_futures_data",
                      return_value=(self._panel(), pd.DatetimeIndex([]), np.zeros(30))), \
                patch("fts.factor_engine.signal_generator.SignalGenerator._compute_factor_values",
                      return_value=np.ones(30)), \
                patch("fts.factor_engine.feature_importance.FeatureImportanceAnalyzer") as m_ana:
            m_ana.return_value.analyze.return_value = result
            rc = _cmd_feature_analyze(Namespace(factor_id="F1", market="futures",
                                                days=500, output=None))
        assert rc == 0
        assert "特征重要性: F1" in capsys.readouterr().out


class TestCmdGpEvolve:
    """测试 _cmd_gp_evolve 各分支。"""

    def _result(self) -> SimpleNamespace:
        return SimpleNamespace(
            best_expression="close > sma(close,5)",
            best_fitness=0.5, best_ic=0.1, best_sharpe=1.5,
            generations_completed=3, total_evaluations=100,
        )

    def test_panel_empty(self, capsys):
        """面板为空返回 1。"""
        with patch("fts.cli._prepare_futures_data", return_value=({}, None, np.array([]))):
            rc = _cmd_gp_evolve(Namespace(universe="futures", population=50, generations=5,
                                          days=500, max_stocks=30, max_symbols=0,
                                          forward=20, output=None))
        assert rc == 1
        assert "数据准备失败" in capsys.readouterr().err

    def test_futures_success(self, capsys):
        """期货 universe 成功演化。"""
        gp = MagicMock()
        gp.evolve.return_value = self._result()
        with patch("fts.cli._prepare_futures_data",
                   return_value=({"RB0": _close_df(30)}, pd.DatetimeIndex([]), np.zeros(30))), \
                patch("fts.factor_engine.gp_evolver.GPEvolver", return_value=gp):
            rc = _cmd_gp_evolve(Namespace(universe="futures", population=50, generations=5,
                                          days=500, max_stocks=30, max_symbols=0,
                                          forward=20, output=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "GP 演化结果" in out
        assert "适应度: 0.5000" in out

    def test_csi300_success_with_output(self, tmp_path, capsys):
        """csi300 universe + 输出落盘。"""
        gp = MagicMock()
        gp.evolve.return_value = self._result()
        with patch("fts.cli._prepare_cross_section_data",
                   return_value=({"000001": _close_df(30)}, pd.DatetimeIndex([]), np.zeros(30))), \
                patch("fts.factor_engine.gp_evolver.GPEvolver", return_value=gp):
            out_dir = tmp_path / "gp_out"
            rc = _cmd_gp_evolve(Namespace(universe="csi300", population=50, generations=5,
                                          days=500, max_stocks=30, max_symbols=0,
                                          forward=20, output=str(out_dir)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "最优因子已保存" in out
        assert (out_dir / "gp_best_factor.json").exists()


# ═══════════════════════════════════════════════════════════
# feedback 子命令组
# ═══════════════════════════════════════════════════════════

class TestCmdFeedback:
    """测试 feedback 各子命令。"""

    @patch("fts.factor_engine.feedback_loop.FeedbackLoop")
    def test_trigger(self, mock_loop, capsys):
        """手动触发反馈事件。"""
        mock_loop.return_value.trigger_manual_feedback.return_value = {
            "event_id": "e1", "factor_id": "F1",
        }
        rc = _cmd_feedback_trigger(Namespace(factor_id="F1", reason="manual review"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "反馈事件已触发" in out
        assert "e1" in out

    @patch("fts.factor_engine.feedback_loop.FeedbackLoop")
    def test_process_empty(self, mock_loop, capsys):
        """无待处理事件。"""
        mock_loop.return_value.process_feedback.return_value = []
        rc = _cmd_feedback_process(Namespace())
        assert rc == 0
        assert "无待处理反馈事件" in capsys.readouterr().out

    @patch("fts.factor_engine.feedback_loop.FeedbackLoop")
    def test_process_with_results(self, mock_loop, capsys):
        """处理结果列表打印。"""
        mock_loop.return_value.process_feedback.return_value = [
            {"event_id": "e1", "root_cause": "overfit",
             "action_taken": "demote", "success": True},
        ]
        rc = _cmd_feedback_process(Namespace())
        assert rc == 0
        out = capsys.readouterr().out
        assert "反馈处理结果" in out
        assert "overfit" in out

    @patch("fts.factor_engine.feedback_loop.FeedbackLoop")
    def test_report(self, mock_loop, capsys):
        """月度报告输出。"""
        mock_loop.return_value.generate_monthly_report.return_value = {
            "period": "2026-07", "new_factors": 5, "effective_rate": 0.4,
            "feedback_events_handled": 8, "recommendations_accepted": 3,
            "recommendations_total": 6, "summary_text": "本月小结",
        }
        rc = _cmd_feedback_report(Namespace(month="2026-07"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "迭代效果月报" in out
        assert "本月小结" in out

    @patch("fts.factor_engine.feedback_loop.FeedbackLoop")
    def test_stats(self, mock_loop, capsys):
        """统计输出。"""
        mock_loop.return_value.get_statistics.return_value = {
            "total_events": 10, "effective_rate": 0.5,
        }
        rc = _cmd_feedback_stats(Namespace())
        assert rc == 0
        assert '"total_events": 10' in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════
# bridge 子命令组
# ═══════════════════════════════════════════════════════════

def _bridge_args(**overrides) -> Namespace:
    defaults = dict(protocol="json", output_dir="signals",
                    redis_url="redis://localhost:6379/0",
                    redis_key="fts:signals:latest", rest_url="")
    defaults.update(overrides)
    return Namespace(**defaults)


class TestCmdBridgePublish:
    """测试 _cmd_bridge_publish。"""

    @patch("fts.factor_engine.state.generate_trace_id", return_value="l2_demo_1")
    @patch("fts.bridge.SignalBridge")
    def test_demo_signal(self, mock_bridge, mock_trace, capsys):
        """缺省输入生成演示信号并发布。"""
        mock_bridge.return_value.publish.return_value = "sig1"
        rc = _cmd_bridge_publish(_bridge_args(input=""))
        assert rc == 0
        out = capsys.readouterr().out
        assert "json 协议发布成功: signal_id=sig1" in out
        published = mock_bridge.return_value.publish.call_args.args[0]
        assert published["signal_id"] == "l2_demo_1"
        assert published["portfolio_id"] == "demo"

    @patch("fts.bridge.SignalBridge")
    def test_input_read_fails(self, mock_bridge, tmp_path, capsys):
        """信号文件读取失败返回 1。"""
        rc = _cmd_bridge_publish(_bridge_args(input=str(tmp_path / "nope.json")))
        assert rc == 1
        assert "读取信号文件失败" in capsys.readouterr().out

    @patch("fts.bridge.SignalBridge")
    def test_input_read_success(self, mock_bridge, tmp_path, capsys):
        """读取 JSON 信号文件并发布。"""
        sig_file = tmp_path / "sig.json"
        sig_file.write_text(json.dumps({"signal_id": "s9", "signals": []}),
                            encoding="utf-8")
        mock_bridge.return_value.publish.return_value = "s9"
        rc = _cmd_bridge_publish(_bridge_args(input=str(sig_file)))
        assert rc == 0
        published = mock_bridge.return_value.publish.call_args.args[0]
        assert published["signal_id"] == "s9"

    @patch("fts.bridge.SignalBridge")
    def test_bridge_error(self, mock_bridge, capsys):
        """发布抛 BridgeError 返回 1。"""
        mock_bridge.return_value.publish.side_effect = BridgeError("redis down")
        rc = _cmd_bridge_publish(_bridge_args(input=""))
        assert rc == 1
        assert "发布失败" in capsys.readouterr().out


class TestCmdBridgeStatus:
    """测试 _cmd_bridge_status。"""

    @patch("fts.bridge.SignalBridge")
    def test_available_with_latest(self, mock_bridge, capsys):
        """可用且有最近信号。"""
        mock_bridge.return_value.status.return_value = SimpleNamespace(
            protocol="json", available=True, detail="ok",
            latest_signal_id="s1", latest_timestamp="2026-08-04T00:00:00",
        )
        rc = _cmd_bridge_status(_bridge_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "可用: YES" in out
        assert "最近信号: s1" in out

    @patch("fts.bridge.SignalBridge")
    def test_not_available(self, mock_bridge, capsys):
        """不可用返回 1。"""
        mock_bridge.return_value.status.return_value = SimpleNamespace(
            protocol="json", available=False, detail="no backend",
            latest_signal_id="", latest_timestamp="",
        )
        rc = _cmd_bridge_status(_bridge_args())
        assert rc == 1
        assert "可用: NO" in capsys.readouterr().out

    @patch("fts.bridge.SignalBridge")
    def test_bridge_error(self, mock_bridge, capsys):
        """状态查询失败返回 1。"""
        mock_bridge.return_value.status.side_effect = BridgeError("boom")
        rc = _cmd_bridge_status(_bridge_args())
        assert rc == 1
        assert "状态查询失败" in capsys.readouterr().out


class TestCmdBridgeServe:
    """测试 _cmd_bridge_serve 的 serve 生命周期与 HTTP handler 方法。"""

    @patch("http.server.ThreadingHTTPServer")
    @patch("fts.bridge.SignalBridge")
    def test_serve_keyboard_interrupt(self, mock_bridge, mock_server, capsys):
        """KeyboardInterrupt 正常关闭服务。"""
        server = mock_server.return_value
        server.serve_forever.side_effect = KeyboardInterrupt
        rc = _cmd_bridge_serve(Namespace(host="127.0.0.1", port=8765))
        assert rc == 0
        out = capsys.readouterr().out
        assert "服务已停止" in out
        server.server_close.assert_called_once()

    @staticmethod
    def _make_handler(handler_cls, body: bytes = b"", path: str = "/signal") -> object:
        """手工构造 handler 实例（绕过 BaseHTTPRequestHandler.__init__）。"""
        inst = handler_cls.__new__(handler_cls)
        inst.path = path
        inst.headers = Message()
        inst.headers["Content-Length"] = str(len(body))
        inst.rfile = io.BytesIO(body)
        inst.wfile = io.BytesIO()
        inst.send_response = lambda code: None
        inst.send_header = lambda k, v: None
        inst.end_headers = lambda: None
        inst.address_string = lambda: "127.0.0.1"
        return inst

    @patch("http.server.ThreadingHTTPServer")
    @patch("fts.bridge.SignalBridge")
    def test_do_post_success_and_errors(self, mock_bridge, mock_server, capsys):
        """POST /signal 成功发布 + 404/400 分支。"""
        mock_server.return_value.serve_forever.side_effect = KeyboardInterrupt
        _cmd_bridge_serve(Namespace(host="127.0.0.1", port=8765))
        handler_cls = mock_server.call_args.args[1]

        # 成功发布
        body = json.dumps({"signal_id": "s1"}).encode()
        inst = self._make_handler(handler_cls, body=body, path="/signal")
        inst.do_POST()
        mock_bridge.return_value.publish.assert_called_once_with({"signal_id": "s1"})
        assert b'"ok"' in inst.wfile.getvalue()

        # 路径不匹配 → 404
        inst = self._make_handler(handler_cls, body=body, path="/other")
        inst.do_POST()
        assert b'not found' in inst.wfile.getvalue()

        # 非法 JSON → 400
        inst = self._make_handler(handler_cls, body=b"not-json", path="/signal")
        inst.do_POST()
        assert b'"error"' in inst.wfile.getvalue()

        # log_message 格式化访问日志
        inst = self._make_handler(handler_cls, body=b"{}", path="/signal")
        inst.log_message("%s %s", "GET", 200)
        assert "[bridge] 127.0.0.1 GET 200" in capsys.readouterr().out

    @patch("http.server.ThreadingHTTPServer")
    @patch("fts.bridge.SignalBridge")
    def test_do_get(self, mock_bridge, mock_server, capsys):
        """GET /health 与 GET 最近信号。"""
        mock_server.return_value.serve_forever.side_effect = KeyboardInterrupt
        _cmd_bridge_serve(Namespace(host="127.0.0.1", port=8765))
        handler_cls = mock_server.call_args.args[1]

        # /health
        inst = self._make_handler(handler_cls, path="/health")
        inst.do_GET()
        assert b'"status": "ok"' in inst.wfile.getvalue()

        # 最近信号为空
        mock_bridge.return_value.latest.return_value = {}
        inst = self._make_handler(handler_cls, path="/signal")
        inst.do_GET()
        assert b"{}" in inst.wfile.getvalue()

        # 最近信号有数据
        mock_bridge.return_value.latest.return_value = {"signal_id": "s1"}
        inst = self._make_handler(handler_cls, path="/signal")
        inst.do_GET()
        assert b'"signal_id"' in inst.wfile.getvalue()
