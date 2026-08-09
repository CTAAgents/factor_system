"""tests/factor_engine/expr_dsl/test_seed_analyzer.py — 种子表达式静态分析测试 (GAP-S09)。

覆盖:
    - max_lookback 静态提取（仅窗口算子常量参数，排除幂次/分支常量）
    - 字段集合提取
    - 算子使用分布提取
    - 解析错误处理
"""

from __future__ import annotations

import pytest

from fts.factor_engine.expr_dsl.seed_analyzer import (
    SeedExprParseError,
    analyze_seed_expression,
    estimate_lookback_static,
)


class TestEstimateLookbackStatic:
    """GAP-S09: 静态 lookback 估算（替代正则粗糙估计）。"""

    def test_window_op_lookback(self) -> None:
        """ts_stddev(returns, 20) → lookback 20。"""
        assert estimate_lookback_static(
            "rank(ts_argmax(signed_power(ifelse(returns<0, ts_stddev(returns,20), close), 2), 5)) - 0.5"
        ) == 20

    def test_delay_lookback(self) -> None:
        """delay(x, 10) → lookback 10。"""
        assert estimate_lookback_static("ts_mean(delay(close, 10), 5)") == 10

    def test_ts_corr_third_param(self) -> None:
        """ts_corr 第 3 参数为窗口。"""
        assert estimate_lookback_static("ts_corr(rank(close), rank(volume), 6)") == 6

    def test_non_window_constants_ignored(self) -> None:
        """幂次/分支常量不应计入 lookback。"""
        # signed_power 的幂次 2、ifelse 分支常量 0.5/1/0 均非窗口
        assert estimate_lookback_static("ifelse(close>0.5, 1, 0)") == 10  # 默认
        assert estimate_lookback_static("signed_power(close, 2)") == 10  # 默认

    def test_no_window_default(self) -> None:
        assert estimate_lookback_static("rank(close)") == 10

    def test_garbage_falls_back(self) -> None:
        """无法解析的表达式降级为默认 lookback。"""
        assert estimate_lookback_static("close @@@ garbage (") == 10


class TestAnalyzeSeedExpression:
    def test_fields_and_operators(self) -> None:
        a = analyze_seed_expression("rank(ts_mean(close, 5)) + volume")
        assert "close" in a.fields
        assert "volume" in a.fields
        assert "rank" in a.operators
        assert "ts_mean" in a.operators
        assert a.operator_count == 2

    def test_nested_call_depth(self) -> None:
        a = analyze_seed_expression("rank(ts_corr(rank(close), rank(volume), 5))")
        # rank → ts_corr → rank/rank 嵌套深度 ≥ 2
        assert a.depth >= 2

    def test_max_lookback_alignment(self) -> None:
        a = analyze_seed_expression("ts_mean(delay(close, 10), 5)")
        assert a.max_lookback == 10

    def test_np_compound_ident(self) -> None:
        a = analyze_seed_expression("np.tanh(northbound_flow / np.maximum(cap, 1e-6))")
        assert "np.tanh" in a.operators
        assert "northbound_flow" in a.fields

    def test_parse_error(self) -> None:
        with pytest.raises(SeedExprParseError):
            analyze_seed_expression("close ))")  # 多余右括号

    def test_paren_grouping(self) -> None:
        a = analyze_seed_expression("(close - open_) / open_")
        assert "close" in a.fields
        assert "open_" in a.fields

    def test_logical_ops(self) -> None:
        """gtja 表达式中的 & / | 二元逻辑运算可解析。"""
        a = analyze_seed_expression("(close > delay(close, 1)) & (volume > ts_mean(volume, 5))")
        assert a.max_lookback == 5
        assert "close" in a.fields

    def test_gtja_style_window_extraction(self) -> None:
        """gtja 常用形态（含 & 连接）的 lookback 提取。"""
        lb = estimate_lookback_static(
            "ts_rank(close, 10) & ts_stddev(returns, 20)"
        )
        assert lb == 20
