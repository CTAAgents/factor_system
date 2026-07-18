"""
tests/test_cli.py — FTS CLI 全面测试。

HARNESS §测试随重构: 测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from fts.cli import (
    _cmd_factor_list,
    _cmd_factor_show,
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
        "version", "monitor", "evolution", "meta-loop", "portfolio", "factor",
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

    @patch("fts.cli.generate_trace_id", return_value="l2_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_evolution_run_default_max_gen(
        self, mock_run_id, mock_trace_id, capsys,
    ):
        """evolution run 默认 max_generations=10。"""
        rc = main(["evolution", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l2_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "max_generations=10" in captured.out

    @patch("fts.cli.generate_trace_id", return_value="l2_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_evolution_run_custom_max_gen(
        self, mock_run_id, mock_trace_id, capsys,
    ):
        """evolution run --max-generations 20 使用自定义值。"""
        rc = main(["evolution", "run", "--max-generations", "20"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "max_generations=20" in captured.out

    @patch("fts.cli.generate_trace_id", return_value="l1_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_meta_loop_run(self, mock_run_id, mock_trace_id, capsys):
        """meta-loop run 打印 trace_id 和 run_id。"""
        rc = main(["meta-loop", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l1_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "Meta-Loop" in captured.out

    @patch("fts.cli.generate_trace_id", return_value="l3_abcd1234_20260718T000000")
    @patch("fts.cli.generate_run_id", return_value="run_ef567890_20260718T000000")
    def test_portfolio_run(self, mock_run_id, mock_trace_id, capsys):
        """portfolio run 打印 trace_id 和 run_id。"""
        rc = main(["portfolio", "run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "l3_abcd1234_20260718T000000" in captured.out
        assert "run_ef567890_20260718T000000" in captured.out
        assert "Portfolio" in captured.out

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
        # 默认路径不存在，应打印不存在提示
        assert "不存在" in captured.out or "不存在" in captured.out


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
