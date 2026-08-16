"""
tests/test_cli.py — FTS CLI 全面测试。

HARNESS §测试随重构: 测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.cli import (
    _cmd_backtest_batch,
    _cmd_factor_list,
    _cmd_factor_show,
    _cmd_factor_stats,
    _cmd_factor_lineage,
    build_parser,
    main,
)
from fts.monitor import SystemStatusReport


# ═══════════════════════════════════════════════════════════
# build_parser()
# ═══════════════════════════════════════════════════════════


class TestBuildParser:
    """测试 CLI parser 构建。"""

    def test_parser_is_argument_parser(self):
        """返回 ArgumentParser 实例。"""
        parser = build_parser()
        assert parser is not None
        assert parser.prog == "fts"

    @pytest.mark.parametrize(
        "subcmd",
        [
            "version",
            "monitor",
            "evolution",
            "meta-loop",
            "portfolio",
            "factor",
            "scheduler",
        ],
    )
    def test_subcommands_exist(self, subcmd):
        """所有子命令都存在。"""
        parser = build_parser()
        # 尝试解析该子命令（不传额外参数）不抛异常即表示存在
        args = parser.parse_args([subcmd])
        assert args.command == subcmd

    def test_monitor_has_json_flag(self):
        """monitor 子命令有 --json 参数。"""
        parser = build_parser()
        args = parser.parse_args(["monitor", "--json"])
        assert args.json is True

    def test_monitor_json_default_false(self):
        """monitor 子命令 --json 默认为 False。"""
        parser = build_parser()
        args = parser.parse_args(["monitor"])
        assert args.json is False

    def test_evolution_run_has_max_generations(self):
        """evolution run 有 --max-generations 参数。"""
        parser = build_parser()
        args = parser.parse_args(["evolution", "run", "--max-generations", "20"])
        assert args.max_generations == 20

    def test_evolution_run_max_generations_default(self):
        """evolution run --max-generations 默认值为 10。"""
        parser = build_parser()
        args = parser.parse_args(["evolution", "run"])
        assert args.max_generations == 10

    def test_factor_list_has_elite_dir(self):
        """factor list 有 --elite-dir 参数。"""
        parser = build_parser()
        args = parser.parse_args(["factor", "list", "--elite-dir", "/tmp/elite"])
        assert args.elite_dir == "/tmp/elite"

    def test_factor_list_elite_dir_default(self):
        """factor list --elite-dir 默认为 None。"""
        parser = build_parser()
        args = parser.parse_args(["factor", "list"])
        assert args.elite_dir is None

    def test_factor_show_has_factor_id_and_elite_dir(self):
        """factor show 有 factor_id 和 --elite-dir 参数。"""
        parser = build_parser()
        args = parser.parse_args(["factor", "show", "RB", "--elite-dir", "/tmp/elite"])
        assert args.factor_id == "RB"
        assert args.elite_dir == "/tmp/elite"

    def test_portfolio_run_has_enable_pca(self):
        """portfolio run 有 --enable-pca 参数（v2.103.0+24）。"""
        parser = build_parser()
        args = parser.parse_args(["portfolio", "run", "--enable-pca"])
        assert args.enable_pca is True

    def test_portfolio_run_enable_pca_default(self):
        """portfolio run --enable-pca 默认为 False。"""
        parser = build_parser()
        args = parser.parse_args(["portfolio", "run"])
        assert args.enable_pca is False

    def test_parser_has_version_flag(self):
        """顶层 --version 标志存在。"""
        parser = build_parser()
        args = parser.parse_args(["--version"])
        assert args.version is True

    def test_invalid_command_shows_error(self):
        """无效命令通过解析器报错。"""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["nonexistent"])


# ═══════════════════════════════════════════════════════════
# main()
# ═══════════════════════════════════════════════════════════


class TestMain:
    """测试 CLI 主入口。"""

    def test_no_args_prints_help(self, capsys):
        """无参数时打印帮助信息，返回 0。"""
        rc = main([])
        captured = capsys.readouterr()
        assert rc == 0
        assert "usage:" in captured.out.lower() or "usage:" in captured.err.lower()

    @patch("fts.cli._cmd_version", return_value=0)
    def test_version_flag(self, mock_cmd_version, capsys):
        """--version 标志调用 _cmd_version。"""
        rc = main(["--version"])
        assert rc == 0
        mock_cmd_version.assert_called_once()

    @patch("fts.cli._cmd_version", return_value=0)
    def test_version_subcommand(self, mock_cmd_version, capsys):
        """version 子命令调用 _cmd_version。"""
        rc = main(["version"])
        assert rc == 0
        mock_cmd_version.assert_called_once()

    @patch("fts.cli._cmd_monitor", return_value=0)
    def test_main_attaches_session_id(self, mock_monitor, capsys):
        """main() 生成 session_id 并挂载到 args 传递到子命令。"""
        rc = main(["monitor"])
        assert rc == 0
        args = mock_monitor.call_args.args[0]
        assert args.session_id.startswith("session_")
        assert len(args.session_id) > len("session_")

    @patch("fts.cli.check_all_status")
    def test_monitor_healthy_returns_0(self, mock_check_all, capsys):
        """monitor 健康时返回 0。"""
        mock_check_all.return_value = SystemStatusReport(healthy=True)
        rc = main(["monitor"])
        assert rc == 0
        mock_check_all.assert_called_once()

    @patch("fts.cli.check_all_status")
    def test_monitor_unhealthy_returns_1(self, mock_check_all, capsys):
        """monitor 不健康时返回 1。"""
        mock_check_all.return_value = SystemStatusReport(healthy=False)
        rc = main(["monitor"])
        assert rc == 1

    @patch("fts.cli.check_all_status")
    def test_monitor_json_output(self, mock_check_all, capsys):
        """monitor --json 调用 status_report_to_json。"""
        mock_check_all.return_value = SystemStatusReport(
            healthy=True,
            checked_at="2026-07-18T00:00:00",
        )
        rc = main(["monitor", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        # 验证输出是有效的 JSON
        output = json.loads(captured.out)
        assert output["healthy"] is True

    @patch("fts.cli.check_all_status")
    def test_monitor_prints_formatted_when_not_json(self, mock_check_all, capsys):
        """monitor 无 --json 时打印格式化报告。"""
        mock_check_all.return_value = SystemStatusReport(
            healthy=True,
            checked_at="2026-07-18T00:00:00",
            fts_version="8.10.0",
        )
        rc = main(["monitor"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "FTS System Status" in captured.out

    @patch("fts.cli.check_all_status", side_effect=RuntimeError("boom"))
    def test_monitor_exception_returns_2(self, mock_check_all, capsys):
        """monitor 异常时返回 2 并打印错误。"""
        rc = main(["monitor"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "ERROR" in captured.err.upper() or "ERROR" in captured.out

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli._prepare_futures_data")
    @patch("fts.cli.generate_trace_id", return_value="l2_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_evolution_run_default_max_gen(
        self,
        mock_run_id,
        mock_trace_id,
        mock_prep_fut,
        mock_llm,
        mock_evoloop,
        capsys,
    ):
        """evolution run 默认 max_generations=10。"""
        df = pd.DataFrame({"close": np.arange(10, dtype=float)})
        panel = {"RB0": df}
        common_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        mock_prep_fut.return_value = (panel, common_dates, np.ones(10))
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed",
            generations_completed=1,
            elite_factor_ids=[],
            circuit_breaker_reason="",
        )
        rc = main(["evolution", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l2_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "max_generations=10" in captured.out
        mock_loop.run.assert_called_once()

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli._prepare_futures_data")
    @patch("fts.cli.generate_session_id", return_value="session_abcd1234_20260718T000000")
    @patch("fts.cli.generate_trace_id", return_value="l2_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_evolution_run_prints_session_id(
        self,
        mock_run_id,
        mock_trace_id,
        mock_session,
        mock_prep_fut,
        mock_llm,
        mock_evoloop,
        capsys,
    ):
        """evolution run 启动日志输出 session_id。"""
        df = pd.DataFrame({"close": np.arange(10, dtype=float)})
        panel = {"RB0": df}
        common_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        mock_prep_fut.return_value = (panel, common_dates, np.ones(10))
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed",
            generations_completed=1,
            elite_factor_ids=[],
            circuit_breaker_reason="",
        )
        rc = main(["evolution", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "session_abcd1234_20260718T000000" in captured.out

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli._prepare_futures_data")
    @patch("fts.cli.generate_trace_id", return_value="l2_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_evolution_run_custom_max_gen(
        self,
        mock_run_id,
        mock_trace_id,
        mock_prep_fut,
        mock_llm,
        mock_evoloop,
        capsys,
    ):
        """evolution run --max-generations 20 使用自定义值。"""
        df = pd.DataFrame({"close": np.arange(10, dtype=float)})
        panel = {"RB0": df}
        common_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        mock_prep_fut.return_value = (panel, common_dates, np.ones(10))
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed",
            generations_completed=1,
            elite_factor_ids=[],
            circuit_breaker_reason="",
        )
        rc = main(["evolution", "run", "--max-generations", "20"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "max_generations=20" in captured.out

    @patch("fts.cli.MetaLoop")
    @patch("fts.cli.generate_trace_id", return_value="l1_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_meta_loop_run(
        self,
        mock_run_id,
        mock_trace_id,
        mock_metal,
        capsys,
    ):
        """meta-loop run 打印 trace_id 和 run_id。"""
        mock_loop = mock_metal.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed",
            injected_candidate_ids=[],
        )
        rc = main(["meta-loop", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l1_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "meta-loop" in captured.out

    @patch("fts.cli.PortfolioLoop")
    @patch("fts.cli.generate_trace_id", return_value="l3_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_portfolio_run(
        self,
        mock_run_id,
        mock_trace_id,
        mock_port,
        capsys,
    ):
        """portfolio run 打印 trace_id 和 run_id（GAP-072 解绑后不再联动信号管道）。"""
        mock_loop = mock_port.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed",
            n_factors_retained=0,
            combo_sharpe=0.0,
        )
        rc = main(["portfolio", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l3_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "portfolio" in captured.out

    @patch("fts.cli.PortfolioLoop")
    @patch("fts.cli.get_config")
    def test_portfolio_run_follows_global_market(self, mock_cfg, mock_port, capsys):
        """portfolio run --universe 未指定时跟随全局 FTS_DEFAULT_MARKET（v2.104.0+101）。"""
        mock_cfg.return_value = SimpleNamespace(
            default_market="energy",
            memory_dir="/mem",
            get_elite_dir=lambda m: f"/elite/{m}",
            portfolio_optimizer_mode="risk_parity",
            l3={},
            verifier={},
        )
        mock_loop = mock_port.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed",
            n_factors_retained=0,
            combo_sharpe=0.0,
        )
        rc = main(["portfolio", "run"])
        assert rc == 0
        kwargs = mock_port.call_args.kwargs
        assert kwargs["market"] == "energy"
        assert kwargs["elite_dir"] == "/elite/energy"
        assert "[portfolio] universe=energy" in capsys.readouterr().out

    @patch("fts.cli.list_scheduler_tasks", return_value=[])
    def test_scheduler_list_empty(self, mock_tasks, capsys):
        """scheduler list 显示无任务。"""
        rc = main(["scheduler", "list"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "无已注册任务" in captured.out

    @patch("fts.cli._cmd_scheduler_list", return_value=0)
    def test_scheduler_list(self, mock_cmd, capsys):
        """scheduler list 调用 _cmd_scheduler_list。"""
        rc = main(["scheduler", "list"])
        assert rc == 0

    @patch("fts.cli._cmd_factor_list", return_value=0)
    def test_factor_list(self, mock_cmd, capsys):
        """factor list 调用 _cmd_factor_list。"""
        rc = main(["factor", "list"])
        assert rc == 0
        mock_cmd.assert_called_once()

    @patch("fts.cli._cmd_factor_list", return_value=0)
    def test_factor_list_with_elite_dir(self, mock_cmd, capsys):
        """factor list --elite-dir 传递自定义目录。"""
        rc = main(["factor", "list", "--elite-dir", "/tmp/elite"])
        assert rc == 0

    @patch("fts.cli._cmd_factor_show", return_value=0)
    def test_factor_show(self, mock_cmd, capsys):
        """factor show 调用 _cmd_factor_show。"""
        rc = main(["factor", "show", "RB"])
        assert rc == 0
        mock_cmd.assert_called_once()

    @patch("fts.cli._cmd_factor_show", return_value=1)
    def test_factor_show_missing_factor(self, mock_cmd, capsys):
        """factor show 找不到因子时返回 1。"""
        rc = main(["factor", "show", "NONEXISTENT"])
        assert rc == 1

    def test_invalid_subcommand(self):
        """无效子命令触发 SystemExit。"""
        with pytest.raises(SystemExit):
            main(["evolution", "invalid"])

    @patch("fts.cli._cmd_version", return_value=0)
    def test_version_flag_before_subcommand(self, mock_cmd_version):
        """--version 标志在子命令前也能工作。"""
        rc = main(["--version", "monitor"])
        assert rc == 0
        mock_cmd_version.assert_called_once()


# ═══════════════════════════════════════════════════════════
# _cmd_factor_list()
# ═══════════════════════════════════════════════════════════


class TestCmdFactorList:
    """测试 _cmd_factor_list。"""

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    def test_non_existing_elite_dir(self, mock_load, capsys):
        """elite 目录不存在时打印提示，返回 0。"""
        args = MagicMock(spec=[])
        args.elite_dir = "/tmp/nonexistent_elite_xyz"
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "不存在" in captured.out or "不存在" in captured.out

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob", return_value=[])
    def test_empty_elite_dir(self, mock_glob, mock_exists, mock_load, capsys):
        """elite 目录存在但无 JSON 文件。"""
        args = MagicMock(spec=[])
        args.elite_dir = "/tmp/elite"
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "无符合条件的因子" in captured.out

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob")
    def test_with_factors(self, mock_glob, mock_exists, mock_load, capsys):
        """elite 目录有因子文件时正确列出。"""
        # 创建模拟的 Path 对象
        factor_data = json.dumps(
            {
                "factor_id": "RB_001",
                "name": "Reversal Beta",
                "generation": 5,
            }
        )
        mock_file = MagicMock(spec=Path)
        mock_file.stem = "RB_001"
        mock_file.name = "RB_001.json"
        mock_file.read_text.return_value = factor_data
        mock_glob.return_value = [mock_file]

        args = MagicMock()
        args.elite_dir = "/tmp/elite"
        args.market = "futures"
        # 显式关闭筛选参数，确保走目录直读模式（而非 DuckDB）
        args.min_ic = None
        args.min_sharpe = None
        args.diverse = False
        args.total_count = 10
        args.json = False
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "RB_001" in captured.out
        assert "Reversal Beta" in captured.out

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    def test_unevaluated_factor_shows_not_evaluated(self, mock_load, capsys, tmp_path):
        """无评估指标的因子显示'未评估'而非空白。"""
        from argparse import Namespace

        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()
        (elite_dir / "CAND_001.json").write_text(
            json.dumps(
                {
                    "factor_id": "CAND_001",
                    "name": "candidate_factor",
                    "generation": 0,
                }
            ),
            encoding="utf-8",
        )

        args = Namespace(
            elite_dir=str(elite_dir),
            market="futures",
            min_ic=None,
            min_sharpe=None,
            diverse=False,
            total_count=10,
            limit=50,
            json=False,
        )
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "未评估" in captured.out
        assert "CAND_001" in captured.out

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob")
    def test_factor_read_error(self, mock_glob, mock_exists, mock_load, capsys):
        """因子文件读取失败时优雅处理。"""
        mock_file = MagicMock(spec=Path)
        mock_file.stem = "BROKEN"
        mock_file.name = "BROKEN.json"
        mock_file.read_text.side_effect = ValueError("corrupt")
        mock_glob.return_value = [mock_file]

        args = MagicMock()
        args.elite_dir = "/tmp/elite"
        args.market = "futures"
        # 显式关闭筛选参数，确保走目录直读模式（而非 DuckDB）
        args.min_ic = None
        args.min_sharpe = None
        args.diverse = False
        args.total_count = 10
        args.json = False
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "BROKEN" in captured.out
        assert "读取失败" in captured.out

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    def test_default_elite_dir_none(self, mock_load, capsys, tmp_path):
        """elite_dir 为 None 时使用默认路径。"""
        from unittest.mock import patch

        fake_elite = tmp_path / "elite"
        fake_elite.mkdir(parents=True)
        args = MagicMock()
        args.elite_dir = None
        args.market = "futures"
        with patch("fts.cli.Path", return_value=fake_elite):
            rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        # 默认路径存在但无 JSON 文件
        assert "无符合条件的因子" in captured.out


# ═══════════════════════════════════════════════════════════
# _cmd_factor_show()
# ═══════════════════════════════════════════════════════════


class TestCmdFactorShow:
    """测试 _cmd_factor_show。"""

    def test_missing_factor(self, capsys):
        """找不到因子时返回 1。"""
        args = MagicMock(spec=[])
        args.factor_id = "NONEXISTENT"
        args.elite_dir = "/tmp/nonexistent_elite_xyz"
        rc = _cmd_factor_show(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "未找到因子" in captured.out

    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob")
    def test_existing_factor(self, mock_glob, mock_exists, capsys):
        """找到因子时打印 JSON 详情，返回 0。"""
        factor_data = {"factor_id": "RB_001", "name": "Reversal Beta"}
        mock_file = MagicMock(spec=Path)
        mock_file.read_text.return_value = json.dumps(factor_data)
        mock_glob.return_value = [mock_file]

        args = MagicMock(spec=[])
        args.factor_id = "RB"
        args.elite_dir = "/tmp/elite"
        rc = _cmd_factor_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "RB_001" in captured.out
        assert "Reversal Beta" in captured.out

    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob")
    def test_read_error_returns_2(self, mock_glob, mock_exists, capsys):
        """因子文件读取异常时返回 2。"""
        mock_file = MagicMock(spec=Path)
        mock_file.read_text.side_effect = ValueError("corrupt")
        mock_glob.return_value = [mock_file]

        args = MagicMock(spec=[])
        args.factor_id = "BROKEN"
        args.elite_dir = "/tmp/elite"
        rc = _cmd_factor_show(args)
        assert rc == 2
        captured = capsys.readouterr()
        assert "读取失败" in captured.out or "读取失败" in captured.err

    def test_default_elite_dir_none(self, capsys):
        """elite_dir 为 None 时使用默认路径。"""
        args = MagicMock(spec=[])
        args.factor_id = "RB"
        args.elite_dir = None
        rc = _cmd_factor_show(args)
        assert rc == 1
        captured = capsys.readouterr()
        # 默认路径不存在，应打印未找到
        assert "未找到因子" in captured.out


# ═══════════════════════════════════════════════════════════
# _cmd_version 间接测试（通过 main）
# ═══════════════════════════════════════════════════════════


class TestCmdVersion:
    """测试版本命令（通过 main）。"""

    @patch("fts.cli.FTS_VERSION", "0.1.0")
    @patch("fts.cli.EVOLUTION_VERSION", "8.10.0")
    def test_version_output(self, capsys):
        """version 子命令打印版本信息。"""
        rc = main(["version"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "FTS version: 0.1.0" in captured.out
        assert "Factor engine version: 8.10.0" in captured.out

    @patch("fts.cli.FTS_VERSION", "0.1.0")
    @patch("fts.cli.EVOLUTION_VERSION", "8.10.0")
    def test_version_flag_output(self, capsys):
        """--version 标志打印版本信息。"""
        rc = main(["--version"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "FTS version: 0.1.0" in captured.out
        assert "Factor engine version: 8.10.0" in captured.out


class TestMainGuard:
    """覆盖 cli.py 中 if __name__ == '__main__' 守护线。"""

    def test_main_guard_via_subprocess(self):
        """通过子进程执行 python -m fts.cli --version 触发 __main__ 守护线。"""
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "fts.cli", "--version"],
            capture_output=True,
            text=True,
            cwd="d:\\Programs\\factor_system",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "FTS version" in result.stdout


# ═══════════════════════════════════════════════════════════
# _cmd_evolution_run — 错误处理
# ═══════════════════════════════════════════════════════════


class TestCmdEvolutionRunErrors:
    """测试 _cmd_evolution_run 异常处理路径（lines 205/207-209）。"""

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli._prepare_futures_data")
    @patch("fts.cli.generate_trace_id", return_value="l2_err_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_err_20260718T000000")
    def test_loop_run_raises(self, mock_run_id, mock_trace_id, mock_prep_fut, mock_llm, mock_evoloop, capsys):
        """loop.run() 抛出异常时返回 2。"""
        df = pd.DataFrame({"close": np.arange(10, dtype=float)})
        panel = {"RB0": df}
        common_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        mock_prep_fut.return_value = (panel, common_dates, np.ones(10))
        mock_loop = mock_evoloop.return_value
        mock_loop.run.side_effect = RuntimeError("evolution crashed")
        rc = main(["evolution", "run"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "运行失败" in captured.out or "运行失败" in captured.err


# ═══════════════════════════════════════════════════════════
# _cmd_factor_list / _cmd_backtest_batch — 目录直读修复
# ═══════════════════════════════════════════════════════════


def _write_factor_snapshot(elite_dir: Path, factor_id: str, name: str, ic: float = 0.1234, sharpe: float = 2.5) -> None:
    """写入一份完整因子快照（含嵌套 evaluation）。"""
    elite_dir.mkdir(parents=True, exist_ok=True)
    (elite_dir / f"{factor_id}.json").write_text(
        json.dumps(
            {
                "factor_id": factor_id,
                "name": name,
                "code": "close - close.shift(5)",
                "params": {},
                "signature": {"input_fields": ["close"], "output_type": "signal"},
                "economic_logic": {
                    "narrative": "test",
                    "theory": 4,
                    "behavioral": 3,
                    "microstructure": 2,
                    "institutional": 1,
                },
                "source": "seed",
                "parent_id": None,
                "generation": 0,
                "trace_id": "trace-test",
                "market": "futures",
                "evaluation": {
                    "level_1_backtest": {"ic": ic, "sharpe": sharpe, "max_drawdown": 0.05},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class TestFactorListDirectoryRead:
    """factor list 目录直读：跳过内部索引文件 + 提取嵌套评估。"""

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    def test_factor_list_skips_underscore_index_file(self, mock_load, tmp_path, capsys):
        """_l2_seed_correlation_index.json 等内部文件不计入因子。"""
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir(parents=True, exist_ok=True)
        (elite_dir / "_l2_seed_correlation_index.json").write_text(
            json.dumps({"pairs": []}),
            encoding="utf-8",
        )
        _write_factor_snapshot(elite_dir, "fct_abc12345", "test_factor")
        from argparse import Namespace

        args = Namespace(
            elite_dir=str(elite_dir),
            market="futures",
            min_ic=None,
            min_sharpe=None,
            diverse=False,
            total_count=10,
            limit=50,
            json=False,
        )
        rc = _cmd_factor_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Factors (1)" in out
        assert "test_factor" in out
        assert "fct_abc12345" in out

    @patch("fts.cli._load_factor_repo", side_effect=RuntimeError("db down"))
    def test_factor_list_extracts_evaluation_ic_sharpe(self, mock_load, tmp_path, capsys):
        """ic/sharpe 从 evaluation.level_1_backtest 提取（而非显示 -）。"""
        elite_dir = tmp_path / "elite"
        _write_factor_snapshot(elite_dir, "fct_abc12345", "test_factor", ic=0.1234, sharpe=2.5)
        from argparse import Namespace

        args = Namespace(
            elite_dir=str(elite_dir),
            market="futures",
            min_ic=None,
            min_sharpe=None,
            diverse=False,
            total_count=10,
            limit=50,
            json=False,
        )
        rc = _cmd_factor_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "0.1234" in out
        assert "2.5000" in out


class TestBacktestBatchDirectoryRead:
    """backtest batch 目录直读：跳过内部索引文件。"""

    @patch("fts.cli.get_config")
    @patch("fts.cli._prepare_data", return_value=(pd.DataFrame(), None))
    @patch("fts.factor_engine.backtest_pipeline.BacktestPipeline")
    def test_backtest_batch_skips_underscore_index_file(
        self, mock_pipeline_cls, mock_prep, mock_config, tmp_path, capsys
    ):
        """_l2_seed_correlation_index.json 不进入 screener。"""
        from argparse import Namespace

        elite_dir = tmp_path / "elite"
        elite_dir.mkdir(parents=True, exist_ok=True)
        (elite_dir / "_l2_seed_correlation_index.json").write_text(
            json.dumps({"pairs": []}),
            encoding="utf-8",
        )
        _write_factor_snapshot(elite_dir, "fct_abc12345", "test_factor")
        mock_cfg = mock_config.return_value
        mock_cfg.get_elite_dir.return_value = str(elite_dir)

        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.run_batch.return_value = []
        from fts.factor_engine.factor_screener import FactorScreener

        with patch.object(FactorScreener, "screen", wraps=None) as mock_screen:
            mock_screen.return_value = [{"factor_id": "fct_abc12345", "name": "test_factor"}]
            args = Namespace(
                market="futures", grade="C", min_score=None, limit=20, symbol="RB0", days=300, capital=1_000_000.0
            )
            rc = _cmd_backtest_batch(args)
        assert rc == 0
        # screener 收到的 factors 只含正常因子（内部索引文件被跳过）
        call_args = mock_screen.call_args
        received = call_args.kwargs.get("factors")
        assert received is not None
        assert len(received) == 1
        assert received[0]["factor_id"] == "fct_abc12345"


# ═══════════════════════════════════════════════════════════
# _cmd_meta_loop_run — 错误处理
# ═══════════════════════════════════════════════════════════


class TestCmdMetaLoopRunErrors:
    """测试 _cmd_meta_loop_run 异常处理路径（lines 231-233）。"""

    @patch("fts.cli.MetaLoop")
    @patch("fts.cli.generate_trace_id", return_value="l1_err_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_err_20260718T000000")
    def test_meta_loop_raises(self, mock_run_id, mock_trace_id, mock_metal, capsys):
        """MetaLoop.run() 抛出异常时返回 2。"""
        mock_loop = mock_metal.return_value
        mock_loop.run.side_effect = RuntimeError("meta loop crashed")
        rc = main(["meta-loop", "run"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "运行失败" in captured.out or "运行失败" in captured.err


# ═══════════════════════════════════════════════════════════
# _cmd_portfolio_run — 错误处理
# ═══════════════════════════════════════════════════════════


class TestCmdPortfolioRunErrors:
    """测试 _cmd_portfolio_run 异常处理路径（lines 253-255）。"""

    @patch("fts.cli.PortfolioLoop")
    @patch("fts.cli.generate_trace_id", return_value="l3_err_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_err_20260718T000000")
    def test_portfolio_raises(self, mock_run_id, mock_trace_id, mock_port, capsys):
        """PortfolioLoop.run() 抛出异常时返回 2。"""
        mock_loop = mock_port.return_value
        mock_loop.run.side_effect = RuntimeError("portfolio crashed")
        rc = main(["portfolio", "run"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "运行失败" in captured.out or "运行失败" in captured.err


# ═══════════════════════════════════════════════════════════
# _cmd_ui — Web UI 仪表盘
# ═══════════════════════════════════════════════════════════


class TestCmdUI:
    """测试 _cmd_ui 的启动/关闭/错误路径（lines 260-274）。"""

    @patch("time.sleep", side_effect=KeyboardInterrupt)
    @patch("fts.cli.FTSDashboardServer")
    def test_keyboard_interrupt_clean_shutdown(self, mock_server, mock_sleep, capsys):
        """KeyboardInterrupt 触发正常关闭（server.start 成功后 sleep 时中断）。"""
        instance = mock_server.return_value
        instance.start.return_value = None
        rc = main(["ui"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "正在关闭" in captured.out
        instance.stop.assert_called_once()

    @patch("fts.cli.FTSDashboardServer")
    def test_start_failure(self, mock_server, capsys):
        """启动失败时返回 2。"""
        instance = mock_server.return_value
        instance.start.side_effect = RuntimeError("port in use")
        rc = main(["ui"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "启动失败" in captured.out or "启动失败" in captured.err


# ═══════════════════════════════════════════════════════════
# _cmd_scheduler_run — 调度器运行
# ═══════════════════════════════════════════════════════════


class TestCmdSchedulerRun:
    """测试 _cmd_scheduler_run 成功/失败路径（lines 279-285）。"""

    @patch("fts.cli.SchedulerEngine")
    @patch("fts.cli.list_scheduler_tasks", return_value=[MagicMock()])
    def test_success(self, mock_tasks, mock_engine, capsys):
        """调度器成功启动返回 0。"""
        instance = mock_engine.return_value
        instance.start.return_value = True
        # _cmd_scheduler_run 成功路径以 threading.Event().wait() 常驻保活，
        # mock 使其立即返回，避免测试无限阻塞
        with patch("threading.Event.wait", return_value=None):
            rc = main(["scheduler", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "调度器已启动" in captured.out
        instance.start.assert_called_once_with(daemon=True)

    @patch("fts.cli.SchedulerEngine")
    def test_failure(self, mock_engine, capsys):
        """调度器启动失败返回 1（APScheduler 未安装）。"""
        instance = mock_engine.return_value
        instance.start.return_value = False
        rc = main(["scheduler", "run"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "启动失败" in captured.err


class TestCmdSchedulerStatus:
    """测试 _cmd_scheduler_status（一键启停状态查看）。"""

    def test_status_empty(self, capsys):
        """空任务时显示注册/调度计数与一键启停指引。"""
        with patch("fts.cli.list_scheduler_tasks", return_value=[]):
            rc = main(["scheduler", "status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "注册任务 0 个，实际调度 0 个" in out
        assert "一键启用" in out
        assert "一键停用" in out

    def test_status_with_tasks(self, capsys):
        """有任务时逐项显示启用状态。"""
        task = MagicMock()
        task.name = "test_task"
        task.enabled = False
        task.cron_expression = "0 4 * * *"
        task.description = "测试任务"
        with patch("fts.cli.list_scheduler_tasks", return_value=[task]):
            rc = main(["scheduler", "status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "注册任务 1 个，实际调度 0 个" in out
        assert "test_task" in out


# ═══════════════════════════════════════════════════════════
# _cmd_scheduler_list — 有任务
# ═══════════════════════════════════════════════════════════


class TestCmdSchedulerListWithTasks:
    """测试 _cmd_scheduler_list 有任务时（lines 294-298）。"""

    def test_with_tasks(self, capsys):
        """有任务时正确列出所有任务属性。"""
        task1 = MagicMock()
        task1.name = "evolution_run"
        task1.enabled = True
        task1.cron_expression = "0 9 * * 1-5"
        task1.description = "每日演化运行"

        task2 = MagicMock()
        task2.name = "daily_report"
        task2.enabled = False
        task2.cron_expression = "0 0 * * *"
        task2.description = "每日报告生成"

        with patch("fts.cli.list_scheduler_tasks", return_value=[task1, task2]):
            rc = main(["scheduler", "list"])
            assert rc == 0
            captured = capsys.readouterr()
            assert "Scheduler Tasks" in captured.out
            assert "evolution_run" in captured.out
            assert "daily_report" in captured.out
            assert "每日演化运行" in captured.out
            assert "每日报告生成" in captured.out


# ═══════════════════════════════════════════════════════════
# evolution run — 熔断器原因输出
# ═══════════════════════════════════════════════════════════


class TestCmdEvolutionRunCircuitBreaker:
    """测试 evolution run 熔断器输出路径（line 205）。"""

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli._prepare_futures_data")
    @patch("fts.cli.generate_trace_id", return_value="l2_cb_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_cb_20260718T000000")
    def test_circuit_breaker_reason_printed(
        self,
        mock_run_id,
        mock_trace_id,
        mock_prep_fut,
        mock_llm,
        mock_evoloop,
        capsys,
    ):
        """有 circuit_breaker_reason 时打印原因。"""
        df = pd.DataFrame({"close": np.arange(10, dtype=float)})
        panel = {"RB0": df}
        common_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        mock_prep_fut.return_value = (panel, common_dates, np.ones(10))
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed",
            generations_completed=1,
            elite_factor_ids=[],
            circuit_breaker_reason="token budget exceeded",
        )
        rc = main(["evolution", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "token budget exceeded" in captured.out


# ═══════════════════════════════════════════════════════════
# factor stats / lineage 子命令
# ═══════════════════════════════════════════════════════════


class TestFactorStatsParser:
    """factor stats / lineage 能被 parser 正确解析。"""

    def test_factor_stats_parser(self):
        parser = build_parser()
        args = parser.parse_args(["factor", "stats", "--min-sharpe", "0.5"])
        assert args.command == "factor"
        assert args.subcommand == "stats"
        assert args.min_sharpe == 0.5

    def test_factor_lineage_parser(self):
        parser = build_parser()
        args = parser.parse_args(["factor", "lineage", "F_001"])
        assert args.command == "factor"
        assert args.subcommand == "lineage"
        assert args.factor_id == "F_001"

    def test_factor_list_diverse_parser(self):
        parser = build_parser()
        args = parser.parse_args(["factor", "list", "--diverse", "--total-count", "8", "--max-per-cluster", "2"])
        assert args.diverse is True
        assert args.total_count == 8
        assert args.max_per_cluster == 2


class TestCmdFactorStats:
    """测试 _cmd_factor_stats（信号聚类分布）。"""

    @patch("fts.cli._load_factor_repo")
    @patch("fts.factor_engine.factor_clustering.cluster_factors_by_signal")
    def test_stats_with_data(self, mock_cluster, mock_load, capsys):
        """有数据时输出聚类分布。"""
        mock_repo = mock_load.return_value
        mock_repo.get_eligible.return_value = [
            {"factor_id": "f1", "name": "rep_a", "ic": 0.05, "sharpe": 1.5, "code": "x"},
            {"factor_id": "f2", "name": "b", "ic": 0.03, "sharpe": 0.8, "code": "x"},
            {"factor_id": "f3", "name": "c", "ic": 0.04, "sharpe": 1.2, "code": "x"},
        ]
        mock_cluster.return_value = {
            "assign": {"f1": 0, "f2": 0, "f3": 1},
            "cluster_order": [0, 1],
            "cluster_members": {0: ["f1", "f2"], 1: ["f3"]},
        }
        args = MagicMock(spec=[])
        args.market = "futures"
        args.min_sharpe = 0.0
        args.json = False
        rc = _cmd_factor_stats(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "因子聚类分布" in captured.out
        assert "rep_a" in captured.out  # 簇 0 代表因子（sharpe 最高）
        assert "3" in captured.out  # 合计

    @patch("fts.cli._load_factor_repo")
    def test_stats_empty(self, mock_load, capsys):
        """无数据时打印空提示。"""
        mock_repo = mock_load.return_value
        mock_repo.get_eligible.return_value = []
        args = MagicMock(spec=[])
        args.market = None
        args.min_sharpe = 0.0
        args.json = False
        rc = _cmd_factor_stats(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "无符合条件的因子" in captured.out

    @patch("fts.cli._load_factor_repo")
    @patch("fts.factor_engine.factor_clustering.cluster_factors_by_signal")
    def test_stats_json_mode(self, mock_cluster, mock_load, capsys):
        """--json 模式输出 JSON。"""
        mock_repo = mock_load.return_value
        mock_repo.get_eligible.return_value = [
            {"factor_id": "f1", "name": "rep_a", "ic": 0.05, "sharpe": 1.5, "code": "x"},
        ]
        mock_cluster.return_value = {
            "assign": {"f1": 0},
            "cluster_order": [0],
            "cluster_members": {0: ["f1"]},
        }
        args = MagicMock(spec=[])
        args.market = None
        args.min_sharpe = 0.0
        args.json = True
        rc = _cmd_factor_stats(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert '"cluster_distribution"' in captured.out
        assert '"rep_a"' in captured.out

    @patch("fts.cli._load_factor_repo")
    def test_stats_repo_error(self, mock_load, capsys):
        """仓储异常时返回 1。"""
        mock_load.side_effect = RuntimeError("db down")
        args = MagicMock(spec=[])
        args.market = None
        args.min_sharpe = 0.0
        args.json = False
        rc = _cmd_factor_stats(args)
        assert rc == 1


class TestCmdFactorLineage:
    """测试 _cmd_factor_lineage。"""

    @patch("fts.cli._load_factor_repo")
    def test_lineage_found(self, mock_load, capsys):
        """找到因子血缘时打印。"""
        mock_repo = mock_load.return_value
        mock_repo.get_factor_lineage.return_value = {
            "factor_id": "F_001",
            "parents": [{"factor_id": "F_000", "generation": 1}],
        }
        args = MagicMock(spec=[])
        args.factor_id = "F_001"
        rc = _cmd_factor_lineage(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "F_001" in captured.out
        assert "parents" in captured.out

    @patch("fts.cli._load_factor_repo")
    def test_lineage_not_found(self, mock_load, capsys):
        """未找到因子时返回 1。"""
        mock_repo = mock_load.return_value
        mock_repo.get_factor_lineage.return_value = None
        args = MagicMock(spec=[])
        args.factor_id = "UNKNOWN"
        rc = _cmd_factor_lineage(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "未找到因子" in captured.out

    @patch("fts.cli._load_factor_repo")
    def test_lineage_repo_error(self, mock_load, capsys):
        """仓储异常时返回 1。"""
        mock_load.side_effect = RuntimeError("db down")
        args = MagicMock(spec=[])
        args.factor_id = "F_001"
        rc = _cmd_factor_lineage(args)
        assert rc == 1


class TestCmdFactorListDuckDB:
    """测试 _cmd_factor_list 的 DuckDB 分支。"""

    @patch("fts.cli._load_factor_repo")
    def test_list_diverse(self, mock_load, capsys):
        """--diverse 走多样性选择。"""
        mock_repo = mock_load.return_value
        mock_repo.get_diverse_factors.return_value = [
            {"factor_id": "F_001", "name": "trend_a"},
            {"factor_id": "F_002", "name": "mr_b"},
        ]
        args = MagicMock(spec=[])
        args.elite_dir = None
        args.market = "futures"
        args.min_ic = None
        args.min_sharpe = None
        args.diverse = True
        args.total_count = 5
        args.max_per_cluster = 2
        args.limit = 50
        args.json = False
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "F_001" in captured.out
        mock_repo.get_diverse_factors.assert_called_once()

    @patch("fts.cli._load_factor_repo")
    def test_list_duckdb_fallback(self, mock_load, capsys):
        """DuckDB 查询失败时回退目录模式。"""
        mock_load.side_effect = RuntimeError("duckdb unavailable")
        # 目录模式会因为 elite_dir=None 走默认路径，不存在就返回 0
        args = MagicMock(spec=[])
        args.elite_dir = None
        args.market = "futures"
        args.min_ic = None
        args.min_sharpe = None
        args.diverse = False
        args.total_count = 10
        args.limit = 50
        args.json = False
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "回退目录模式" in captured.err or "回退目录模式" in captured.out
