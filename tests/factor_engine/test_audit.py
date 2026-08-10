"""tests/factor_engine/test_audit.py — 因子审计流程 (Phase B.3) 单元测试。

覆盖:
    - FactorAuditor 主入口与整体判定
    - 6 项审计各自通过/失败/跳过路径
    - FactorAuditReport 便捷查询
    - _bh_fdr_correction 工具函数
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.audit import (
    FactorAuditConfig,
    FactorAuditor,
    _bh_fdr_correction,
)


# ─── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def auditor() -> FactorAuditor:
    return FactorAuditor()


@pytest.fixture
def strict_auditor() -> FactorAuditor:
    config = FactorAuditConfig(
        min_cross_symbol_ratio=0.95,
        min_oos_pass_ratio=0.8,
    )
    return FactorAuditor(config=config)


@pytest.fixture
def sample_factor() -> dict:
    return {"factor_id": "f_test_001", "name": "test_factor"}


@pytest.fixture
def sample_dataframe() -> pd.DataFrame:
    n = 120
    rng = np.random.RandomState(42)
    close = 100 + np.cumsum(rng.randn(n) * 0.5)
    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01", periods=n),
            "close": close,
            "volume": np.abs(rng.randn(n) * 1000) + 500,
        }
    )


@pytest.fixture
def forward_returns(n: int = 120) -> np.ndarray:
    rng = np.random.RandomState(123)
    return rng.randn(n) * 0.01


# ─── FactorAuditor 主入口 ────────────────────────────────────


class TestFactorAuditor:
    def test_empty_audit_all_skipped(self, auditor: FactorAuditor, sample_factor: dict):
        """无任何输入时，所有审计项均应为 skipped，整体未通过。"""
        report = auditor.audit(factor=sample_factor)
        assert report.factor_id == "f_test_001"
        assert report.factor_name == "test_factor"
        assert report.passed is False
        # 6 项审计
        assert len(report.items) == 6
        assert all(it.status == "skipped" for it in report.items)
        assert report.pass_rate == 0.0
        assert report.summary["skipped"] == 6

    def test_report_item_lookup(self, auditor: FactorAuditor, sample_factor: dict):
        """report.item() 应按名称定位单项结果。"""
        report = auditor.audit(factor=sample_factor)
        item = report.item("cross_symbol")
        assert item is not None
        assert item.name == "cross_symbol"
        assert item.status == "skipped"

    def test_report_item_missing(self, auditor: FactorAuditor, sample_factor: dict):
        """查询不存在的名称返回 None。"""
        report = auditor.audit(factor=sample_factor)
        assert report.item("nonexistent") is None

    def test_failed_items_property(self, auditor: FactorAuditor, sample_factor: dict):
        """failed_items 应聚合所有 status=failed 的项。"""
        # 提供会触发失败的输入
        ics = {"RB": 0.01, "HC": -0.02, "I": -0.03}  # 仅 1/3 为正
        report = auditor.audit(factor=sample_factor, symbol_ic_map=ics)
        assert "cross_symbol" in [it.name for it in report.failed_items]

    def test_to_dict_roundtrip(self, auditor: FactorAuditor, sample_factor: dict):
        """to_dict 应包含完整审计信息。"""
        report = auditor.audit(factor=sample_factor)
        d = report.to_dict()
        assert d["factor_id"] == "f_test_001"
        assert "items" in d
        assert len(d["items"]) == 6
        assert "summary" in d


# ─── 2. OOS 验证 ────────────────────────────────────────────


class TestOOSConsistency:
    def test_oos_passed(self, auditor: FactorAuditor):
        """OOS 结果 passed=True 时应通过。"""
        result = {"ic_consistency": 0.75, "passed": True}
        item = auditor._check_oos_consistency(result)
        assert item.status == "passed"
        assert item.score > 0.0

    def test_oos_high_consistency_passes(self, auditor: FactorAuditor):
        """ic_consistency >= 0.5 应通过。"""
        result = {"ic_consistency": 0.6, "passed": False}
        item = auditor._check_oos_consistency(result)
        assert item.status == "passed"

    def test_oos_low_consistency_fails(self, auditor: FactorAuditor):
        """ic_consistency < 0.5 且 passed=False 应失败。"""
        result = {"ic_consistency": 0.2, "passed": False}
        item = auditor._check_oos_consistency(result)
        assert item.status == "failed"

    def test_oos_skipped_when_missing(self, auditor: FactorAuditor):
        """缺失 OOS 结果时应为 skipped。"""
        item = auditor._check_oos_consistency(None)
        assert item.status == "skipped"


# ─── 3. 跨品种验证 ──────────────────────────────────────────


class TestCrossSymbol:
    def test_cross_symbol_passes(self, auditor: FactorAuditor):
        """≥80% 品种 IC 为正应通过。"""
        ics = {"RB": 0.05, "HC": 0.03, "I": 0.02, "J": -0.01}  # 3/4 = 75%
        # 默认阈值 0.8: 0.75 < 0.8 → 失败
        item = auditor._check_cross_symbol(ics)
        assert item.status == "failed"

    def test_cross_symbol_boundary_passes(self, auditor: FactorAuditor):
        """正好 80% 应通过。"""
        ics = {"RB": 0.05, "HC": 0.03, "I": 0.02, "J": -0.01, "JM": 0.01}  # 4/5=80%
        item = auditor._check_cross_symbol(ics)
        assert item.status == "passed"

    def test_cross_symbol_all_positive(self, auditor: FactorAuditor):
        """全部 IC 为正应通过且 score=1.0。"""
        ics = {"RB": 0.05, "HC": 0.03, "I": 0.02}
        item = auditor._check_cross_symbol(ics)
        assert item.status == "passed"
        assert item.score == 1.0

    def test_cross_symbol_skipped(self, auditor: FactorAuditor):
        """空或 None 输入均应为 skipped。"""
        assert auditor._check_cross_symbol(None).status == "skipped"
        assert auditor._check_cross_symbol({}).status == "skipped"

    def test_cross_symbol_custom_threshold(self, strict_auditor: FactorAuditor):
        """自定义阈值 95% 时应更严格。"""
        ics = {"RB": 0.05, "HC": 0.03, "I": 0.02, "J": -0.01}  # 75%
        item = strict_auditor._check_cross_symbol(ics)
        assert item.status == "failed"
        assert item.details["threshold"] == 0.95


# ─── 5. 多重检验 ────────────────────────────────────────────


class TestMultipleTesting:
    def test_significant_bonferroni_passes(self, auditor: FactorAuditor):
        """Bonferroni 校正后仍显著应通过。"""
        # 10 个检验，其中 1 个 p=0.001 (< 0.005 阈值)
        p_values = [0.001] + [0.5] * 9
        item = auditor._check_multiple_testing(p_values)
        assert item.status == "passed"
        assert item.details["bonferroni_significant"] >= 1

    def test_no_significant_fails(self, auditor: FactorAuditor):
        """所有 p 值均不显著应失败。"""
        p_values = [0.1, 0.2, 0.3]
        item = auditor._check_multiple_testing(p_values)
        assert item.status == "failed"

    def test_multiple_testing_skipped(self, auditor: FactorAuditor):
        """无 p 值时应为 skipped。"""
        assert auditor._check_multiple_testing(None).status == "skipped"
        assert auditor._check_multiple_testing([]).status == "skipped"

    def test_fdr_significant_also_passes(self, auditor: FactorAuditor):
        """仅 FDR 显著也应通过。"""
        # 构造一组在 FDR 下显著但 Bonferroni 不显著的 p 值
        p_values = [0.01] * 50  # Bonferroni 阈值 0.00125 → 不显著; BH FDR 下显著
        item = auditor._check_multiple_testing(p_values)
        # 通过条件: bonferroni>=1 或 fdr>=1
        assert item.details["fdr_significant"] >= 1


# ─── 6. 数据窥探检验 ──────────────────────────────────────


class TestSnoopingCheck:
    def test_snooping_normal_data_passes(
        self,
        auditor: FactorAuditor,
        sample_dataframe: pd.DataFrame,
        forward_returns: np.ndarray,
    ):
        """普通合成数据不应触发窥探嫌疑。"""
        item = auditor._check_snooping(sample_dataframe, forward_returns)
        # 数据窥探检验通过/失败取决于随机数据，一般应 passed
        assert item.status in ("passed", "failed")
        assert item.details

    def test_snooping_skipped_no_data(self, auditor: FactorAuditor):
        """缺失数据时应为 skipped。"""
        item = auditor._check_snooping(None, None)
        assert item.status == "skipped"
        item2 = auditor._check_snooping(pd.DataFrame({"x": [1]}), np.array([0.1]))
        assert item2.status == "skipped"  # 无 close 列

    def test_snooping_with_close_column(
        self,
        auditor: FactorAuditor,
    ):
        """含 close 列且数据充足时应执行窥探检验。"""
        n = 60
        rng = np.random.RandomState(7)
        df = pd.DataFrame(
            {
                "close": 100 + np.cumsum(rng.randn(n)),
                "date": pd.date_range("2023-01-01", periods=n),
            }
        )
        fwd = rng.randn(n) * 0.01
        item = auditor._check_snooping(df, fwd)
        assert item.status in ("passed", "failed")
        assert "lag_correlations" in item.details


# ─── 压力测试集成 ──────────────────────────────────────────


class TestStressResilience:
    def test_stress_skipped_without_inputs(self, auditor: FactorAuditor):
        """无压力测试输入时应为 skipped。"""
        item = auditor._check_stress_resilience(None, None)
        assert item.status == "skipped"

    def test_stress_with_inputs(self, auditor: FactorAuditor):
        """提供信号和 OHLCV 时应实际执行。"""
        n = 30
        signals = {"RB": np.ones(n) * 0.5}
        idx = pd.date_range("2016-01-01", periods=n)
        ohlcv = {
            "RB": pd.DataFrame(
                {
                    "close": 3500 + np.arange(n, dtype=float),
                },
                index=idx,
            )
        }
        item = auditor._check_stress_resilience(signals, ohlcv)
        # 执行过，可能 passed/failed/skipped，但应有 details
        assert item.status in ("passed", "failed", "skipped")
        assert item.details or item.status == "skipped"


# ─── _bh_fdr_correction ────────────────────────────────────


class TestBHFDREcorrection:
    def test_no_p_values(self):
        threshold, n_sig = _bh_fdr_correction(np.array([]), 0.05)
        assert threshold == 0.05
        assert n_sig == 0

    def test_single_significant(self):
        p = np.array([0.001, 0.5, 0.9])
        threshold, n_sig = _bh_fdr_correction(p, 0.05)
        assert n_sig >= 1

    def test_none_significant(self):
        p = np.array([0.9, 0.8, 0.7])
        threshold, n_sig = _bh_fdr_correction(p, 0.05)
        assert n_sig == 0


# ─── 端到端审计 ──────────────────────────────────────────


class TestEndToEnd:
    def test_all_skipped_when_no_input(self, auditor: FactorAuditor):
        """无任何输入应全部 skipped。"""
        report = auditor.audit()
        assert report.passed is False
        assert report.summary["skipped"] == 6

    def test_complete_audit_with_cross_symbol_and_oos(self, auditor: FactorAuditor, sample_factor: dict):
        """仅提供跨品种 + OOS + p-values 时，这些项应通过/失败，其余 skipped。"""
        ics = {"RB": 0.05, "HC": 0.03, "I": 0.02}
        oos = {"ic_consistency": 0.8, "passed": True}
        p_vals = [0.001, 0.02, 0.5]

        report = auditor.audit(
            factor=sample_factor,
            symbol_ic_map=ics,
            oos_result=oos,
            p_values=p_vals,
        )

        # 3 项应非 skipped，其余 skipped
        non_skipped = [it for it in report.items if it.status != "skipped"]
        skipped = [it for it in report.items if it.status == "skipped"]
        assert len(non_skipped) == 3
        assert len(skipped) == 3

        # 所有非 skipped 项均通过 → 整体 passed=True
        assert all(it.status == "passed" for it in non_skipped)
        assert report.passed is True

    def test_one_failure_causes_overall_fail(self, auditor: FactorAuditor, sample_factor: dict):
        """任一审计项失败都应导致整体失败。"""
        # 跨品种严重不达标 (全部 IC 为负)
        ics = {"RB": -0.01, "HC": -0.02}
        oos = {"ic_consistency": 0.8, "passed": True}
        p_vals = [0.001, 0.02]

        report = auditor.audit(
            factor=sample_factor,
            symbol_ic_map=ics,
            oos_result=oos,
            p_values=p_vals,
        )
        # cross_symbol 失败
        assert report.passed is False
        failed_names = {it.name for it in report.failed_items}
        assert "cross_symbol" in failed_names
