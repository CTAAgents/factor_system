"""tests.cli.test_data_cli — `fts data` 子命令组单元测试（Phase 14.4）。

覆盖:
    1. build_parser 注册 4 个子命令 + 参数解析
    2. `_cmd_data_status` / `_cmd_data_cross_check` / `_cmd_data_fuse` 输出格式
    3. trace_id 全链路
    4. FusionReport 契约必填字段
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# 确保 fts 包可导入
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fts.cli import build_parser  # noqa: E402


# ─── 测试夹具 ───────────────────────────────────────────────


def _make_kline_df(prices: list, base_date: str = "2026-08-04", source: str = "TQ_LOCAL") -> pd.DataFrame:
    """构造测试用 K 线 DataFrame。"""
    from datetime import date, timedelta

    n = len(prices)
    dates = [date.fromisoformat(base_date) - timedelta(days=n - 1 - i) for i in range(n)]
    return pd.DataFrame({
        "symbol": "RB0",
        "period": "daily",
        "date": dates,
        "open": [p - 1 for p in prices],
        "high": [p + 2 for p in prices],
        "low": [p - 2 for p in prices],
        "close": prices,
        "volume": [100000] * n,
        "source": source,
        "trace_id": "test-tid",
    })


class _StubSource:
    """Mock 数据源，is_available=True，返回固定 K 线。"""

    def __init__(self, source_name: str, df: pd.DataFrame, available: bool = True, fail: bool = False):
        self.source_name = source_name
        self._df = df
        self._available = available
        self._fail = fail

    def is_available(self) -> bool:
        return self._available

    def fetch_ohlcv(self, symbol, days, trace_id=""):
        if self._fail:
            raise RuntimeError(f"[{self.source_name}] mock failure")
        return self._df.copy()

    def fetch_ohlcv_or_none(self, symbol, days, trace_id=""):
        try:
            return self.fetch_ohlcv(symbol, days, trace_id)
        except Exception:
            return None

    def fetch_quote(self, symbol, trace_id=""):
        return None


# ─── build_parser 注册测试 ───────────────────────────────────


class TestDataSubcommandRegistration:
    def test_data_subcommand_registered(self):
        parser = build_parser()
        # 解析 `data` 子命令不应抛错
        args = parser.parse_args(["data", "status"])
        assert args.command == "data"
        assert args.subcommand == "status"

    def test_data_subcommands_all_registered(self):
        """4 个子命令都能解析（--help 会 SystemExit(0)，但本测试不传 --help）。"""
        parser = build_parser()
        # 用 --help 但抑制 stdout
        for sub, extra in [
            ("status", []),
            ("sync-futures", ["--symbol", "RB0"]),
            ("cross-check", ["--symbol", "RB0", "--date", "2026-08-04"]),
            ("fuse", ["--symbol", "RB0"]),
        ]:
            args = parser.parse_args(["data", sub, *extra])
            assert args.command == "data"
            assert args.subcommand == sub

    def test_data_fuse_required_symbol(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["data", "fuse"])  # 缺 --symbol

    def test_data_fuse_strategy_choices(self):
        parser = build_parser()
        for s in ("MEDIAN", "MEAN", "WEIGHTED", "HIERARCHICAL", "TRIMMED_MEAN"):
            args = parser.parse_args(["data", "fuse", "--symbol", "RB0", "--strategy", s])
            assert args.strategy == s

    def test_data_fuse_invalid_strategy_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["data", "fuse", "--symbol", "RB0", "--strategy", "INVALID"])


# ─── _cmd_data_status 测试 ──────────────────────────────────


class TestDataStatus:
    def test_status_empty(self, capsys):
        """空源状态输出。"""
        from fts.cli import _cmd_data_status

        # Mock aggregator with no breakers
        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {}

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            args = MagicMock(json=False)
            rc = _cmd_data_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "trace_id=" in out
        assert "暂无源活动记录" in out

    def test_status_with_data(self, capsys):
        """有源状态时的输出。"""
        from fts.cli import _cmd_data_status

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {
                "consecutive_failures": 0, "circuit_open": False,
                "total_success": 100, "total_failure": 2, "last_error": "",
            },
            "WIND": {
                "consecutive_failures": 3, "circuit_open": False,
                "total_success": 50, "total_failure": 5, "last_error": "timeout",
            },
        }
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            args = MagicMock(json=False)
            rc = _cmd_data_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "TQ_LOCAL" in out
        assert "WIND" in out

    def test_status_json_format(self, capsys):
        """JSON 模式输出。"""
        from fts.cli import _cmd_data_status

        mock_agg = MagicMock()
        mock_agg.get_source_status.return_value = {
            "TQ_LOCAL": {"consecutive_failures": 0, "circuit_open": False,
                         "total_success": 5, "total_failure": 0, "last_error": ""},
        }
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            args = MagicMock(json=True)
            rc = _cmd_data_status(args)
        assert rc == 0
        out = capsys.readouterr().out
        # 输出包含 trace_id 行 + JSON 对象，提取 JSON 部分
        json_start = out.find("{")
        assert json_start >= 0
        parsed = json.loads(out[json_start:])
        assert "trace_id" in parsed
        assert "TQ_LOCAL" in parsed["sources"]


# ─── _cmd_data_cross_check 测试 ────────────────────────────


class TestDataCrossCheck:
    def test_cross_check_no_disagreement(self, capsys):
        """无分歧时返回 0。"""
        from fts.cli import _cmd_data_cross_check

        mock_agg = MagicMock()
        mock_agg.cross_check.return_value = []

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            args = MagicMock(symbol="RB0", date="2026-08-04", json=False)
            rc = _cmd_data_cross_check(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "无分歧" in out

    def test_cross_check_with_disagreement(self, capsys):
        """有分歧时返回 1。"""
        from fts.cli import _cmd_data_cross_check

        mock_agg = MagicMock()
        mock_agg.cross_check.return_value = [{
            "symbol": "RB0", "date": "2026-08-04",
            "prices": {"TQ_LOCAL": 3500.0, "WIND": 3800.0},
            "median": 3650.0, "outliers": ["WIND"],
            "max_diff_pct": 0.041, "threshold": 0.005, "trace_id": "tid",
            "detected_at": "2026-08-04T17:00:00",
        }]
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            args = MagicMock(symbol="RB0", date="2026-08-04", json=False)
            rc = _cmd_data_cross_check(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "⚠️" in out
        assert "outliers" in out


# ─── _cmd_data_fuse 测试 ───────────────────────────────────


class TestDataFuse:
    def test_fuse_no_data(self, capsys):
        """所有源失败时返回 1。"""
        from fts.cli import _cmd_data_fuse

        # Mock aggregator with no real sources
        mock_agg = MagicMock()
        mock_agg.sources = []
        mock_agg.enhancers = []
        mock_agg._is_circuit_open.return_value = False
        mock_agg.cross_check.return_value = []

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            args = MagicMock(
                symbol="RB0", strategy="MEDIAN", days=30,
                json=False, output=None,
            )
            rc = _cmd_data_fuse(args)
        assert rc == 1
        captured = capsys.readouterr()
        # 错误信息走 stderr
        assert "没有任何源提供数据" in captured.err

    def test_fuse_with_mock_data(self, capsys, tmp_path):
        """多源 mock 数据成功融合。"""
        from fts.cli import _cmd_data_fuse

        # 构造两组 K 线（源 1 与源 2 价格差 0.5%）
        df1 = _make_kline_df([3500.0, 3510.0, 3505.0], source="TQ_LOCAL")
        df2 = _make_kline_df([3517.5, 3527.5, 3522.5], source="WIND")
        df3 = _make_kline_df([3482.5, 3492.5, 3487.5], source="IFIND")

        src1 = _StubSource("TQ_LOCAL", df1)
        src2 = _StubSource("WIND", df2)
        src3 = _StubSource("IFIND", df3)

        mock_agg = MagicMock()
        mock_agg.sources = [src1]
        mock_agg.enhancers = [src2, src3]
        mock_agg._is_circuit_open.return_value = False
        mock_agg.cross_check.return_value = []

        out_path = tmp_path / "report.json"
        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            args = MagicMock(
                symbol="RB0", strategy="MEDIAN", days=3,
                json=True, output=str(out_path),
            )
            rc = _cmd_data_fuse(args)
        assert rc == 0
        # 检查报告落盘
        assert out_path.exists()
        report = json.loads(out_path.read_text(encoding="utf-8"))
        # FusionReport 必填字段
        for key in ("trace_id", "symbol", "strategy", "rows", "sources_used", "rows_count"):
            assert key in report, f"missing {key}"
        assert report["strategy"] == "MEDIAN"
        assert report["rows_count"] == 3
        assert set(report["sources_used"]) == {"TQ_LOCAL", "WIND", "IFIND"}

    def test_fuse_invalid_strategy(self, capsys):
        """无效策略时返回 2。"""
        from fts.cli import _cmd_data_fuse

        args = MagicMock(symbol="RB0", strategy="INVALID", days=30, json=False, output=None)
        rc = _cmd_data_fuse(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "未知策略" in err


# ─── FusionReport 契约测试 ────────────────────────────────


class TestFusionReportContract:
    def test_required_fields(self):
        """FusionReport 必填字段。"""
        from fts.core.contracts import FusionReport
        import typing
        hints = typing.get_type_hints(FusionReport)
        required = {
            "trace_id", "symbol", "strategy",
            "rows", "sources_used", "rows_count",
        }
        for field in required:
            assert field in hints, f"FusionReport missing required field: {field}"

    def test_optional_fields(self):
        """FusionReport 可选字段。"""
        from fts.core.contracts import FusionReport
        import typing
        try:
            hints = typing.get_type_hints(FusionReport, include_extras=True)
        except TypeError:
            hints = typing.get_type_hints(FusionReport)
        optional = {"started_at", "finished_at", "disagreements", "avg_disagreement_pct"}
        # 至少部分可选字段存在（可能 type-hints 看不到 NotRequired）
        assert "disagreements" in FusionReport.__annotations__ or "disagreements" in hints


# ─── CLI 端到端冒烟 ────────────────────────────────────────


class TestDataCliE2E:
    def test_data_status_help(self, capsys):
        """`fts data status --help` 通过 main() 调用。"""
        from fts.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["data", "status", "--help"])
        # argparse 退出码 0 表示 help 成功
        assert exc_info.value.code == 0

    def test_data_fuse_help(self, capsys):
        """`fts data fuse --help` 显示所有参数。"""
        from fts.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["data", "fuse", "--help"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--symbol" in out
        assert "--strategy" in out
        assert "--days" in out
        assert "--json" in out
        assert "--output" in out
