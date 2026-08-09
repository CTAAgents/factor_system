"""
tests/factor_engine/test_regime_multipliers.py — 数据驱动 Regime 倍率估计器测试（GAP-L308 / v2.67.1）。

覆盖:
    - 倍率计算正确性（regime×family IC 均值 / 全局家族基准，跨 regime 归一）
    - 最小样本回退（不足 min_samples → 不产出倍率并记警告）
    - 钳制边界（clamp_lo / clamp_hi）
    - 空输入降级 / 非法记录跳过
    - export_yaml / load_yaml 往返
    - portfolio_loop.load_data_driven_multipliers 文件缺失回退硬编码
"""

from __future__ import annotations

import pytest

from fts.factor_engine.portfolio_loop import load_data_driven_multipliers
from fts.factor_engine.regime_multipliers import (
    RegimeMultiplierEstimator,
)


def _records(regime: str = "bull", family: str = "trend", ic: float = 0.05, n: int = 10) -> list[dict]:
    """合成因子 IC 记录。"""
    return [
        {"regime": regime, "family": family, "ic": ic, "factor_id": f"{family}_{i}"}
        for i in range(n)
    ]


class TestRegimeMultiplierEstimator:
    """RegimeMultiplierEstimator 核心逻辑。"""

    def test_estimate_cross_regime_basic(self) -> None:
        """同一家族跨 regime → 倍率 = 该 regime IC 均值 / 全局家族均值。"""
        records = _records("bull", "trend", ic=0.06, n=20) + _records("bear", "trend", ic=0.02, n=20)
        est = RegimeMultiplierEstimator()
        report = est.estimate(records)

        assert set(report.multipliers) == {"bull", "bear"}
        # 全局 trend 基准 = 0.04；bull=0.06/0.04=1.5, bear=0.02/0.04=0.5
        assert report.multipliers["bull"]["trend"] == pytest.approx(1.5, abs=0.01)
        assert report.multipliers["bear"]["trend"] == pytest.approx(0.5, abs=0.01)
        assert report.stats["n_records"] == 40

    def test_estimate_within_regime_differentiation(self) -> None:
        """同 regime 内不同家族相对全局基准差异化（bull 中 trend 强于 momentum）。"""
        records = (
            _records("bull", "trend", ic=0.06, n=20)
            + _records("bear", "trend", ic=0.02, n=20)     # trend 全局 0.04
            + _records("bull", "momentum", ic=0.03, n=20)
            + _records("bear", "momentum", ic=0.03, n=20)  # momentum 全局 0.03
        )
        est = RegimeMultiplierEstimator()
        report = est.estimate(records)

        assert report.multipliers["bull"]["trend"] == pytest.approx(1.5, abs=0.01)
        assert report.multipliers["bull"]["momentum"] == pytest.approx(1.0, abs=0.01)
        assert report.multipliers["bear"]["trend"] == pytest.approx(0.5, abs=0.01)

    def test_min_samples_fallback(self) -> None:
        """样本不足：不产出该桶倍率 + 警告。"""
        records = _records("bull", "trend", ic=0.05, n=3)  # 3 < min_samples=10
        est = RegimeMultiplierEstimator()
        report = est.estimate(records)

        assert report.multipliers == {}
        assert any("样本不足" in w for w in report.warnings)

    def test_clamp_bounds(self) -> None:
        """倍率钳制在 [clamp_lo, clamp_hi]。"""
        records = _records("bull", "trend", ic=0.30, n=20) + _records("bear", "trend", ic=0.02, n=20)
        est = RegimeMultiplierEstimator()
        report = est.estimate(records)

        # 全局基准 0.16：bull=1.875 → 钳 1.5；bear=0.125 → 钳 0.5
        assert report.multipliers["bull"]["trend"] == 1.5
        assert report.multipliers["bear"]["trend"] == 0.5

    def test_empty_records(self) -> None:
        """空输入降级返回空倍率表 + 警告。"""
        est = RegimeMultiplierEstimator()
        report = est.estimate([])

        assert report.multipliers == {}
        assert any("无输入记录" in w for w in report.warnings)

    def test_invalid_records_skipped(self) -> None:
        """缺 regime/family/ic 字段或 ic 非法 → 跳过不报错。"""
        records = [
            {"regime": "", "family": "trend", "ic": 0.05},
            {"regime": "bull", "family": "", "ic": 0.05},
            {"regime": "bull", "family": "trend"},
            {"regime": "bull", "family": "trend", "ic": "nan_text"},
            {"regime": "bull", "family": "trend", "ic": 0.05},
        ]
        est = RegimeMultiplierEstimator()
        report = est.estimate(records)

        # 仅最后一条有效 → 样本不足回退
        assert report.multipliers == {}
        assert len(report.stats["buckets"]) == 0

    def test_hardcoded_comparison(self) -> None:
        """硬编码 vs 数据驱动对比报告生成（含最大偏差）。"""
        records = _records("bull", "trend", ic=0.06, n=20) + _records("bear", "trend", ic=0.02, n=20)
        hardcoded = {"bull": {"trend": 1.3}, "bear": {"trend": 1.1}}
        est = RegimeMultiplierEstimator()
        report = est.estimate(records, hardcoded=hardcoded)

        assert report.comparison["n_entries"] == 2
        rows = {r["regime"]: r for r in report.comparison["rows"]}
        assert rows["bull"]["data_driven"] == pytest.approx(1.5, abs=0.01)
        assert rows["bull"]["hardcoded"] == 1.3
        assert report.comparison["max_deviation"] == pytest.approx(0.6, abs=0.01)  # bear: |0.5-1.1|


class TestRegimeMultiplierYaml:
    """YAML 落盘 / 加载往返。"""

    def test_export_load_roundtrip(self, tmp_path) -> None:
        """export_yaml → load_yaml 往返一致。"""
        records = _records("bull", "trend", ic=0.06, n=20) + _records("bear", "trend", ic=0.02, n=20)
        est = RegimeMultiplierEstimator()
        report = est.estimate(records)

        path = str(tmp_path / "l3_regime_multipliers.yaml")
        est.export_yaml(path, report)
        doc = est.load_yaml(path)

        assert doc["multipliers"]["bull"]["trend"] == pytest.approx(1.5, abs=0.01)
        assert doc["multipliers"]["bear"]["trend"] == pytest.approx(0.5, abs=0.01)
        assert doc["n_records"] == 40

    def test_load_missing_file_fallback(self, tmp_path) -> None:
        """加载不存在文件回退空字典。"""
        est = RegimeMultiplierEstimator()
        assert est.load_yaml(str(tmp_path / "missing.yaml")) == {}

    def test_load_corrupt_yaml_fallback(self, tmp_path) -> None:
        """损坏 YAML 回退空字典（不抛异常）。"""
        bad = tmp_path / "bad.yaml"
        bad.write_text(": not: [valid", encoding="utf-8")
        est = RegimeMultiplierEstimator()
        assert est.load_yaml(str(bad)) == {}


class TestLoadDataDrivenMultipliers:
    """portfolio_loop.load_data_driven_multipliers 接线。"""

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        """文件缺失 → 返回空 dict（回退硬编码）。"""
        assert load_data_driven_multipliers(str(tmp_path / "nope.yaml")) == {}

    def test_load_valid_yaml(self, tmp_path) -> None:
        """有效 YAML → 加载并转换为 regime→family→float。"""
        yaml_path = tmp_path / "l3_regime_multipliers.yaml"
        yaml_path.write_text(
            "multipliers:\n"
            "  bull:\n"
            "    trend: 1.5\n"
            "    momentum: 0.8\n",
            encoding="utf-8",
        )
        table = load_data_driven_multipliers(str(yaml_path))

        assert table["bull"]["trend"] == 1.5
        assert table["bull"]["momentum"] == 0.8

    def test_corrupt_yaml_returns_empty(self, tmp_path) -> None:
        """损坏 YAML → 空 dict 不抛异常。"""
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text(": not: [valid", encoding="utf-8")
        assert load_data_driven_multipliers(str(yaml_path)) == {}

    def test_default_path_constant_consistent(self) -> None:
        """默认路径与计划文档一致（_data 易变配置原则）。"""
        from fts.factor_engine import portfolio_loop

        assert portfolio_loop._DEFAULT_REGIME_MULTIPLIERS_PATH == (
            "docs/harness/_data/l3_regime_multipliers.yaml"
        )
