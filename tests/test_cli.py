"""
tests/test_cli.py — FTS CLI 全面测试。

HARNESS §测试随重构: 测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, mock_open, patch

import numpy as np
import pandas as pd
import pytest

from fts.cli import (
    _cmd_factor_list,
    _cmd_factor_show,
    _cmd_evolution_run,
    _cmd_meta_loop_run,
    _cmd_portfolio_run,
    build_parser,
    main,
)
from fts.monitor import LoopStatusReport, SystemStatusReport


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

    @pytest.mark.parametrize("subcmd", [
        "version", "monitor", "evolution", "meta-loop", "portfolio", "factor", "scheduler",
    ])
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
    @patch("fts.cli.generate_trace_id", return_value="l2_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_evolution_run_default_max_gen(
        self, mock_run_id, mock_trace_id, mock_evoloop, capsys,
    ):
        """evolution run 默认 max_generations=10。"""
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", generations_completed=1,
            elite_factor_ids=[], circuit_breaker_reason="",
        )
        rc = main(["evolution", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l2_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "max_generations=10" in captured.out
        mock_loop.run.assert_called_once()

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.generate_trace_id", return_value="l2_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_evolution_run_custom_max_gen(
        self, mock_run_id, mock_trace_id, mock_evoloop, capsys,
    ):
        """evolution run --max-generations 20 使用自定义值。"""
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", generations_completed=1,
            elite_factor_ids=[], circuit_breaker_reason="",
        )
        rc = main(["evolution", "run", "--max-generations", "20"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "max_generations=20" in captured.out

    @patch("fts.cli.MetaLoop")
    @patch("fts.cli.generate_trace_id", return_value="l1_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_meta_loop_run(
        self, mock_run_id, mock_trace_id, mock_metal, capsys,
    ):
        """meta-loop run 打印 trace_id 和 run_id。"""
        mock_loop = mock_metal.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", injected_candidate_ids=[],
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
        self, mock_run_id, mock_trace_id, mock_port, capsys,
    ):
        """portfolio run 打印 trace_id 和 run_id。"""
        mock_loop = mock_port.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", n_factors_retained=0,
            combo_sharpe=0.0,
        )
        rc = main(["portfolio", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l3_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "portfolio" in captured.out

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

    def test_non_existing_elite_dir(self, capsys):
        """elite 目录不存在时打印提示，返回 0。"""
        args = MagicMock(spec=[])
        args.elite_dir = "/tmp/nonexistent_elite_xyz"
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "不存在" in captured.out or "不存在" in captured.out

    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob", return_value=[])
    def test_empty_elite_dir(self, mock_glob, mock_exists, capsys):
        """elite 目录存在但无 JSON 文件。"""
        args = MagicMock(spec=[])
        args.elite_dir = "/tmp/elite"
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "无 elite 因子" in captured.out

    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob")
    def test_with_factors(self, mock_glob, mock_exists, capsys):
        """elite 目录有因子文件时正确列出。"""
        # 创建模拟的 Path 对象
        factor_data = json.dumps({
            "factor_id": "RB_001",
            "name": "Reversal Beta",
            "generation": 5,
        })
        mock_file = MagicMock(spec=Path)
        mock_file.stem = "RB_001"
        mock_file.read_text.return_value = factor_data
        mock_glob.return_value = [mock_file]

        args = MagicMock(spec=[])
        args.elite_dir = "/tmp/elite"
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "RB_001" in captured.out
        assert "Reversal Beta" in captured.out

    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.glob")
    def test_factor_read_error(self, mock_glob, mock_exists, capsys):
        """因子文件读取失败时优雅处理。"""
        mock_file = MagicMock(spec=Path)
        mock_file.stem = "BROKEN"
        mock_file.read_text.side_effect = ValueError("corrupt")
        mock_glob.return_value = [mock_file]

        args = MagicMock(spec=[])
        args.elite_dir = "/tmp/elite"
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "BROKEN" in captured.out
        assert "读取失败" in captured.out

    def test_default_elite_dir_none(self, capsys):
        """elite_dir 为 None 时使用默认路径。"""
        args = MagicMock(spec=[])
        args.elite_dir = None
        rc = _cmd_factor_list(args)
        assert rc == 0
        captured = capsys.readouterr()
        # 默认路径存在但无 JSON 文件
        assert "无 elite 因子" in captured.out


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
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "fts.cli", "--version"],
            capture_output=True, text=True,
            cwd="d:\\Programs\\factor_system",
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "FTS version" in result.stdout


# ═══════════════════════════════════════════════════════════
# _cmd_evolution_run — 横截面(csi300)模式
# ═══════════════════════════════════════════════════════════

class TestCmdEvolutionRunCrossSection:
    """测试 _cmd_evolution_run 横截面(csi300)模式（lines 142-158）。"""

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.FactorVerifier")
    @patch("fts.cli.get_default_seed_pool")
    @patch("fts.cli.get_default_llm_client")
    @patch("fts.cli._prepare_cross_section_data")
    @patch("fts.cli.generate_trace_id", return_value="l2_cs_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_cs_20260718T000000")
    def test_csi300_mode(
        self, mock_run_id, mock_trace_id, mock_prep_cs,
        mock_llm, mock_seed, mock_verifier, mock_evoloop, capsys,
    ):
        """--universe csi300 走横截面代码路径。"""
        panel = {"000001": pd.DataFrame({"close": range(10)})}
        common_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        fwd_ret = np.ones(10)
        mock_prep_cs.return_value = (panel, common_dates, fwd_ret)

        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", generations_completed=1,
            elite_factor_ids=[], circuit_breaker_reason="",
        )

        rc = main(["evolution", "run", "--universe", "csi300"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "csi300" in captured.out
        assert "panel symbols=1" in captured.out
        mock_prep_cs.assert_called_once()

    @patch("fts.cli.FTSDataProvider")
    def test_csi300_data_provider_fails(self, mock_provider, capsys):
        """数据提供者失败时传播异常（_prepare_cross_section_data 未在 try 内）。"""
        instance = mock_provider.return_value
        instance.get_csi300_panel.side_effect = RuntimeError("API unavailable")
        with pytest.raises(RuntimeError, match="API unavailable"):
            main(["evolution", "run", "--universe", "csi300"])

    @patch("fts.cli.FTSDataProvider")
    @patch("fts.cli.EvolutionLoop")
    def test_csi300_real_prepare_path(
        self, mock_evoloop, mock_provider, capsys,
    ):
        """不 mock _prepare_cross_section_data，验证其实际执行路径覆盖 lines 99-106。"""
        # 模拟数据提供者返回合理的面板数据
        mock_instance = mock_provider.return_value
        close_data = np.arange(10, dtype=float)
        df = pd.DataFrame({"close": close_data})
        panel = {"000001": df}
        common_dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10))
        mock_instance.get_csi300_panel.return_value = (panel, common_dates)

        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", generations_completed=1,
            elite_factor_ids=[], circuit_breaker_reason="",
        )

        rc = main(["evolution", "run", "--universe", "csi300"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "panel symbols=1" in captured.out


# ═══════════════════════════════════════════════════════════
# _cmd_evolution_run — 错误处理
# ═══════════════════════════════════════════════════════════

class TestCmdEvolutionRunErrors:
    """测试 _cmd_evolution_run 异常处理路径（lines 205/207-209）。"""

    @patch("fts.cli.EvolutionLoop")
    @patch("fts.cli.generate_trace_id", return_value="l2_err_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_err_20260718T000000")
    def test_loop_run_raises(self, mock_run_id, mock_trace_id, mock_evoloop, capsys):
        """loop.run() 抛出异常时返回 2。"""
        mock_loop = mock_evoloop.return_value
        mock_loop.run.side_effect = RuntimeError("evolution crashed")
        rc = main(["evolution", "run"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "运行失败" in captured.out or "运行失败" in captured.err

    @patch("fts.cli._prepare_data", side_effect=RuntimeError("prepare_data failed"))
    def test_prepare_data_raises(self, mock_prep, capsys):
        """_prepare_data 失败时传播异常（未在 try 内）。"""
        with pytest.raises(RuntimeError, match="prepare_data failed"):
            main(["evolution", "run"])


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
    @patch("fts.cli.generate_trace_id", return_value="l2_cb_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_cb_20260718T000000")
    def test_circuit_breaker_reason_printed(
        self, mock_run_id, mock_trace_id, mock_evoloop, capsys,
    ):
        """有 circuit_breaker_reason 时打印原因。"""
        mock_loop = mock_evoloop.return_value
        mock_loop.run.return_value = MagicMock(
            status="completed", generations_completed=1,
            elite_factor_ids=[], circuit_breaker_reason="token budget exceeded",
        )
        rc = main(["evolution", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "token budget exceeded" in captured.out


