"""
tests/factor_engine/test_data_provider_panel.py — 面板数据规模参数化扩展测试（GAP-L309 / v2.67.1）。

覆盖:
    - PanelLoadingConfig 默认配置（全 CSI300 × 500 天）
    - _liquidity_stratified_sample 分层抽样（数量、高低流动性覆盖、无 volume 退化）
    - _load_panel_with_liquidity_sampling（默认全量 / 抽样触发 / 空面板回退）
    - _compute_elastic_net_weights / _compute_ml_ensemble_weights 默认参数接线
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.portfolio_loop import (
    PanelLoadingConfig,
    _compute_elastic_net_weights,
    _compute_ml_ensemble_weights,
    _liquidity_stratified_sample,
    _load_panel_with_liquidity_sampling,
)


def _make_panel(n_stocks: int = 10, n_rows: int = 40, with_volume: bool = False,
                liquidity_spread: bool = False) -> dict[str, pd.DataFrame]:
    """构造面板。liquidity_spread=True 时 volume 随 index 递增（流动性有区分度）。"""
    idx = pd.date_range("2023-12-20", periods=n_rows, freq="D")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_stocks):
        cols: dict[str, np.ndarray] = {"close": np.linspace(100 + i, 200 + i, n_rows)}
        if with_volume:
            if liquidity_spread:
                cols["volume"] = np.full(n_rows, 1000.0 + i * 1000.0)
            else:
                cols["volume"] = np.full(n_rows, 1000.0)
        panel[f"SYM{i}"] = pd.DataFrame(cols, index=idx)
    return panel


class TestPanelLoadingConfig:
    """GAP-L309 默认配置。"""

    def test_default_config_full_scale(self) -> None:
        """默认配置：全量股票 × 500 天（对齐 MIN_EVAL_DAYS）。"""
        cfg = PanelLoadingConfig()
        assert cfg.days == 500
        assert cfg.max_stocks == 0  # 0 = 全量
        assert cfg.liquidity_layers == 4
        assert cfg.min_common_dates == 20


class TestLiquidityStratifiedSample:
    """流动性分层抽样逻辑。"""

    def test_no_sample_when_below_limit(self) -> None:
        """股票数 ≤ 上限时不抽样。"""
        panel = _make_panel(n_stocks=5)
        out = _liquidity_stratified_sample(panel, max_stocks=10)
        assert set(out) == set(panel)

    def test_sample_reaches_limit(self) -> None:
        """抽样后数量 == max_stocks。"""
        panel = _make_panel(n_stocks=20, with_volume=True)
        out = _liquidity_stratified_sample(panel, max_stocks=8, n_layers=4)
        assert len(out) == 8

    def test_liquidity_coverage(self) -> None:
        """高低流动性层均有覆盖（非仅取头部）。"""
        panel = _make_panel(n_stocks=16, with_volume=True, liquidity_spread=True)
        # 流动性随 index 递增：SYM15 最高，SYM0 最低
        # 16 只分 4 层（每层 4 只）取 6 只 → 每层至少 1 只
        out = _liquidity_stratified_sample(panel, max_stocks=6, n_layers=4)
        picked = set(out)
        assert "SYM15" in picked or "SYM14" in picked    # 最高流动性层覆盖
        # 最低层桶 = [SYM3, SYM2, SYM1, SYM0]（桶内流动性降序），任一本层成员被选即覆盖
        assert any(s in picked for s in ("SYM3", "SYM2", "SYM1", "SYM0"))

    def test_no_volume_column_fallback(self) -> None:
        """无 volume 列时退化（不崩溃、数量正确）。"""
        panel = _make_panel(n_stocks=12, with_volume=False)
        out = _liquidity_stratified_sample(panel, max_stocks=5, n_layers=3)
        assert len(out) == 5

    def test_empty_panel(self) -> None:
        """空面板原样返回。"""
        assert _liquidity_stratified_sample({}, max_stocks=5) == {}

    def test_stability(self) -> None:
        """同输入两次抽样结果一致（确定性）。"""
        panel = _make_panel(n_stocks=15, with_volume=True, liquidity_spread=True)
        a = _liquidity_stratified_sample(panel, max_stocks=6, n_layers=3)
        b = _liquidity_stratified_sample(panel, max_stocks=6, n_layers=3)
        assert set(a) == set(b)


class TestLoadPanelWithLiquiditySampling:
    """面板加载接线。"""

    def test_default_full_load(self) -> None:
        """默认配置：max_stocks=0 → 全量加载不抽样。"""
        panel = _make_panel(n_stocks=10)
        dates = pd.date_range("2024-01-01", periods=25, freq="D")
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_csi300_panel.return_value = (panel, dates)
            out, out_dates = _load_panel_with_liquidity_sampling(
                m_prov.return_value, PanelLoadingConfig(),
            )
        assert set(out) == set(panel)
        # 全量加载 → 请求 max_stocks=0
        assert m_prov.return_value.get_csi300_panel.call_args.kwargs["max_stocks"] == 0
        assert m_prov.return_value.get_csi300_panel.call_args.kwargs["days"] == 500

    def test_sampling_triggered(self) -> None:
        """max_stocks>0 且股票数超限 → 抽样生效。"""
        panel = _make_panel(n_stocks=20, with_volume=True)
        dates = pd.date_range("2024-01-01", periods=25, freq="D")
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_csi300_panel.return_value = (panel, dates)
            out, _ = _load_panel_with_liquidity_sampling(
                m_prov.return_value, PanelLoadingConfig(max_stocks=6),
            )
        assert len(out) == 6

    def test_empty_panel_returns_early(self) -> None:
        """空面板直接返回不抽样。"""
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_csi300_panel.return_value = ({}, [])
            out, dates = _load_panel_with_liquidity_sampling(
                m_prov.return_value, PanelLoadingConfig(),
            )
        assert out == {}
        assert dates == []


class TestElasticNetDefaultParams:
    """默认参数提升接线（500 天 / 全量）。"""

    def test_elastic_net_default_days_full(self, tmp_path) -> None:
        """elastic_net 默认 days=500 / max_stocks=0 透传。"""
        panel = _make_panel(n_stocks=10, n_rows=40)
        dates = pd.date_range("2024-01-01", periods=25, freq="D")
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_csi300_panel.return_value = (panel, dates)
            _compute_elastic_net_weights([], tmp_path)  # 有效因子不足 → 回退，但已触发加载
            call = m_prov.return_value.get_csi300_panel.call_args
            assert call.kwargs["days"] == 500
            assert call.kwargs["max_stocks"] == 0

    def test_ml_ensemble_default_days_full(self, tmp_path) -> None:
        """ml_ensemble 默认 days=500 / max_stocks=0 透传。"""
        panel = _make_panel(n_stocks=10, n_rows=40)
        dates = pd.date_range("2024-01-01", periods=25, freq="D")
        with patch("fts.data.FTSDataProvider") as m_prov:
            m_prov.return_value.get_csi300_panel.return_value = (panel, dates)
            _compute_ml_ensemble_weights([], tmp_path)
            call = m_prov.return_value.get_csi300_panel.call_args
            assert call.kwargs["days"] == 500
            assert call.kwargs["max_stocks"] == 0
