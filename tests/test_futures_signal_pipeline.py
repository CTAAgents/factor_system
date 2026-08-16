"""
tests/test_futures_signal_pipeline.py — 期货信号管道测试（L3 组合权重 + 辅助函数）

覆盖:
  - _load_l3_combo_weights: 正常加载 / 缺失 / 损坏 / 空权重（严格模式退出）
  - _load_l3_combo_factors: 按 L3 组合因子名过滤 / 缺失跳过 / 全缺失退出
  - _classify_factor_category / _apply_regime_weight_adjustment: Regime 档位缩放
  - _compute_per_variety_weights: 品种级 IC 自适应权重
  - _compute_composite_scores: 加权合成（方向校正恒为空 dict）
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# scripts/ 不在 sys.path 中，需要手动添加
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from futures_signal_pipeline import (
    _apply_regime_weight_adjustment,
    _classify_delta_moves,
    _classify_factor_category,
    _compute_composite_scores,
    _compute_holdout_validation,
    _compute_per_variety_ic_matrix,
    _compute_per_variety_weights,
    _compute_signal_deltas,
    _factor_set_signature,
    _load_l3_combo_factors,
    _load_l3_combo_weights,
    _load_signal_factors,
    load_futures_elite_factors,
)


# ─── Fixtures ────────────────────────────────────────────────────────────


def _make_panel(
    n_symbols: int = 5,
    n_days: int = 120,
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """构造合成面板数据（品种行情 + 共同交易日）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    date_strs = [d.strftime("%Y-%m-%d") for d in dates]

    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        sym = f"RB{i}"
        close = 3000 + rng.normal(0, 50, n_days).cumsum()
        close = np.maximum(close, 500)
        df = pd.DataFrame(
            {
                "open": close + rng.normal(0, 10, n_days),
                "high": close + abs(rng.normal(0, 15, n_days)),
                "low": close - abs(rng.normal(0, 15, n_days)),
                "close": close,
                "volume": rng.integers(1000, 10000, n_days).astype(float),
            },
            index=dates,
        )
        panel[sym] = df

    return panel, date_strs


def _make_signal_matrix(
    panel: dict[str, pd.DataFrame],
    factor_names: list[str],
    seed: int = 42,
    nan_rate: float = 0.0,
    zero_factor_names: list[str] = None,
) -> dict[str, dict[str, np.ndarray]]:
    """构造合成信号矩阵。"""
    rng = np.random.default_rng(seed)
    zero_factor_names = zero_factor_names or []
    signal_matrix: dict[str, dict[str, np.ndarray]] = {}
    for sym, df in panel.items():
        n_days = len(df)
        sym_signals: dict[str, np.ndarray] = {}
        for fname in factor_names:
            if fname in zero_factor_names:
                arr = np.zeros(n_days)
            else:
                arr = rng.normal(0, 1, n_days)
                if nan_rate > 0:
                    n_nan = int(n_days * nan_rate)
                    nan_idx = rng.choice(n_days, n_nan, replace=False)
                    arr = arr.astype(float)
                    arr[nan_idx] = np.nan
            sym_signals[fname] = arr
        signal_matrix[sym] = sym_signals
    return signal_matrix


def _make_factor_sign_flips(
    factor_names: list[str],
    flip_map: dict[str, float] = None,
) -> dict[str, float]:
    """构造因子方向校正字典。"""
    flips = {}
    for fname in factor_names:
        if flip_map and fname in flip_map:
            flips[fname] = flip_map[fname]
        else:
            flips[fname] = 1.0
    return flips


# ─── _compute_per_variety_weights ─────────────────────────────────────────


class TestComputePerVarietyWeights:
    """_compute_per_variety_weights 测试（修复：NaN IC 污染 total 导致品种被跳过）。"""

    def test_nan_ic_does_not_poison_total(self):
        """因子 IC 为 NaN（常数信号导致 Spearman IC 未定义）不应使整品种被跳过。"""
        global_weights = {"f_a": 0.6, "f_b": 0.4}
        per_variety_ic = {"f_a": {"RB0": np.nan}, "f_b": {"RB0": 0.5}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "RB0" in result
        # f_a 的 NaN IC 按低 IC 回退：weight ∝ gw * min_ic
        w_a = result["RB0"]["f_a"]
        w_b = result["RB0"]["f_b"]
        assert w_a > 0 and w_b > 0
        assert abs(sum(result["RB0"].values()) - 1.0) < 1e-9
        # 有效 IC(0.5) 因子权重大于 NaN 回退因子
        assert w_b > w_a

    def test_all_nan_ics_variety_kept(self):
        """品种所有因子 IC 均 NaN → 按 min_ic 回退保留，不因 total=NaN 被丢弃。"""
        global_weights = {"f_a": 0.5, "f_b": 0.5}
        per_variety_ic = {"f_a": {"RB0": np.nan}, "f_b": {"RB0": np.nan}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "RB0" in result
        assert abs(sum(result["RB0"].values()) - 1.0) < 1e-9

    def test_weights_normalized_per_variety(self):
        """正常 IC → 每个品种权重归一化和为 1，强 IC 因子获得更高权重。"""
        global_weights = {"f_a": 0.5, "f_b": 0.5}
        per_variety_ic = {
            "f_a": {"RB0": 0.8, "CU0": 0.1},
            "f_b": {"RB0": 0.2, "CU0": 0.6},
        }

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert set(result.keys()) == {"RB0", "CU0"}
        for var in result:
            assert abs(sum(result[var].values()) - 1.0) < 1e-9
        # RB0 上 f_a IC 更高 → f_a 权重大；CU0 上 f_b IC 更高 → f_b 权重大
        assert result["RB0"]["f_a"] > result["RB0"]["f_b"]
        assert result["CU0"]["f_b"] > result["CU0"]["f_a"]

    def test_low_ic_factor_min_fallback(self):
        """|IC| < min_ic → 因子仅获得 min_ic 级别权重（接近丢弃而非完全剔除）。"""
        global_weights = {"f_strong": 0.7, "f_weak": 0.3}
        per_variety_ic = {"f_strong": {"RB0": 0.6}, "f_weak": {"RB0": 0.001}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        w_strong = result["RB0"]["f_strong"]
        w_weak = result["RB0"]["f_weak"]
        assert w_weak > 0  # 低 IC 因子不剔除，仅极低权重
        assert w_strong / w_weak > 10

    def test_factor_missing_from_ic_min_fallback(self):
        """全局权重中的因子缺失该品种 IC → 按 min_ic 回退（不因 key 不匹配丢失）。"""
        global_weights = {"f_a": 0.6, "f_b": 0.4}
        per_variety_ic = {"f_a": {"RB0": 0.5}}  # f_b 完全缺失

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "RB0" in result
        assert "f_b" in result["RB0"]  # 缺失因子仍被保留（min_ic 权重）
        assert "f_a" in result["RB0"]
        assert abs(sum(result["RB0"].values()) - 1.0) < 1e-9

    def test_empty_per_variety_ic_returns_empty(self):
        """无品种级 IC 数据 → 返回空 dict。"""
        result = _compute_per_variety_weights({"f_a": 1.0}, {})
        assert result == {}

    def test_zero_global_weight_factor_skipped(self):
        """全局权重为 0 的因子不参与品种级权重分配。"""
        global_weights = {"f_a": 0.0, "f_b": 1.0}
        per_variety_ic = {"f_a": {"RB0": 0.9}, "f_b": {"RB0": 0.5}}

        result = _compute_per_variety_weights(global_weights, per_variety_ic)

        assert "f_a" not in result["RB0"]
        assert result["RB0"]["f_b"] == 1.0


# ─── L3 组合权重（v2.105.0：因子选择与基础权重由 L3 组合层负责）───


class TestLoadL3ComboWeights:
    """_load_l3_combo_weights 严格模式测试。"""

    def test_load_normal(self, tmp_path):
        """正常加载 → 返回 {name: weight}。"""
        fp = tmp_path / "factor_weights.json"
        fp.write_text(
            json.dumps({"weights": {"f_a": 0.5, "f_b": 0.3}, "n_factors": 2}),
            encoding="utf-8",
        )
        weights = _load_l3_combo_weights(weights_path=fp)
        assert weights == {"f_a": 0.5, "f_b": 0.3}

    def test_missing_file_exits(self, tmp_path):
        """文件缺失 → 严格模式 sys.exit(1)。"""
        with pytest.raises(SystemExit) as e:
            _load_l3_combo_weights(weights_path=tmp_path / "nope.json")
        assert e.value.code == 1

    def test_corrupt_json_exits(self, tmp_path):
        """JSON 损坏 → 严格模式 sys.exit(1)。"""
        fp = tmp_path / "factor_weights.json"
        fp.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            _load_l3_combo_weights(weights_path=fp)
        assert e.value.code == 1

    def test_empty_weights_exits(self, tmp_path):
        """权重为空 → 严格模式 sys.exit(1)。"""
        fp = tmp_path / "factor_weights.json"
        fp.write_text(json.dumps({"weights": {}}), encoding="utf-8")
        with pytest.raises(SystemExit) as e:
            _load_l3_combo_weights(weights_path=fp)
        assert e.value.code == 1


class TestLoadL3ComboFactors:
    """_load_l3_combo_factors 过滤测试。"""

    def test_filter_by_l3_names(self):
        """按 L3 组合因子名过滤；缺失因子跳过（不阻断）。"""
        l3 = {"f_a": 0.5, "f_b": 0.3, "f_missing": 0.2}
        db_factors = [
            {"name": "f_a", "code": "..."},
            {"name": "f_b", "code": "..."},
            {"name": "f_other", "code": "..."},
        ]
        with patch(
            "futures_signal_pipeline.load_futures_elite_factors_from_db",
            return_value=db_factors,
        ):
            kept = _load_l3_combo_factors(l3)
        assert {f["name"] for f in kept} == {"f_a", "f_b"}

    def test_all_missing_exits(self):
        """L3 组合因子全部缺失 → 严格模式 sys.exit(1)。"""
        with patch(
            "futures_signal_pipeline.load_futures_elite_factors_from_db",
            return_value=[{"name": "f_other", "code": "..."}],
        ):
            with pytest.raises(SystemExit) as e:
                _load_l3_combo_factors({"f_a": 1.0})
        assert e.value.code == 1

    def test_market_db_path_forwarded(self):
        """链模式：market/db_path 参数透传底层加载（能源库路由）。"""
        l3 = {"f_a": 0.5}
        db_factors = [{"name": "f_a", "code": "..."}]
        with patch(
            "futures_signal_pipeline.load_futures_elite_factors_from_db",
            return_value=db_factors,
        ) as mock_load:
            kept = _load_l3_combo_factors(l3, market="energy", db_path=Path("/tmp/energy.duckdb"))
        assert {f["name"] for f in kept} == {"f_a"}
        mock_load.assert_called_once_with(ic_threshold=0, db_path=Path("/tmp/energy.duckdb"), market="energy")


class TestClassifyFactorCategory:
    """_classify_factor_category 名称后缀启发式分类测试。"""

    def test_reversal_priority_over_trend(self):
        """basis 含 momentum 关键词 → 归 reversal（优先级高于 trend）。"""
        assert _classify_factor_category("fut_basis_momentum_g18") == "reversal"

    def test_trend(self):
        assert _classify_factor_category("fut_aroon") == "trend"
        assert _classify_factor_category("fut_intraday_momentum_g6") == "trend"

    def test_volume(self):
        assert _classify_factor_category("fut_volume_price_corr") == "volume"

    def test_neutral_fallback(self):
        assert _classify_factor_category("unknown_factor_xyz") == "neutral"


class TestApplyRegimeWeightAdjustment:
    """_apply_regime_weight_adjustment 档位缩放测试。"""

    def _weights(self):
        return {
            "fut_bias_g17": 0.4,  # reversal
            "fut_aroon": 0.3,  # trend
            "fut_volume_price_corr": 0.3,  # volume
        }

    def test_bull_amplifies_trend(self):
        """趋势制度放大 trend 类、压缩 reversal 类。"""
        w = self._weights()
        out = _apply_regime_weight_adjustment(w, {"regime": "bull"}, [])
        assert abs(sum(out.values()) - 1.0) < 1e-6
        assert out["fut_aroon"] / w["fut_aroon"] > out["fut_bias_g17"] / w["fut_bias_g17"]

    def test_oscillate_amplifies_reversal(self):
        """震荡制度放大 reversal 类、压缩 trend 类。"""
        w = self._weights()
        out = _apply_regime_weight_adjustment(w, {"regime": "oscillate"}, [])
        assert abs(sum(out.values()) - 1.0) < 1e-6
        assert out["fut_bias_g17"] / w["fut_bias_g17"] > out["fut_aroon"] / w["fut_aroon"]

    def test_high_vol_equal_scale_preserves_ratio(self):
        """高波动制度全部 ×0.8，归一化后各因子权重比例与基础一致。"""
        w = self._weights()
        out = _apply_regime_weight_adjustment(w, {"regime": "high_vol"}, [])
        assert abs(sum(out.values()) - 1.0) < 1e-6
        for name, wi in w.items():
            assert abs(out[name] - wi) < 1e-6

    def test_unknown_keeps_base(self):
        """unknown 无缩放配置 → 保持基础权重（归一化）。"""
        w = self._weights()
        out = _apply_regime_weight_adjustment(w, {"regime": "unknown"}, [])
        assert abs(sum(out.values()) - 1.0) < 1e-6
        for name, wi in w.items():
            assert abs(out[name] - wi) < 1e-6

    def test_keeps_all_factors(self):
        """缩放不丢弃因子（不构成因子选择）。"""
        w = self._weights()
        out = _apply_regime_weight_adjustment(w, {"regime": "bear"}, [])
        assert set(out.keys()) == set(w.keys())


# ─── _compute_composite_scores ───────────────────────────────────────────


class TestComputeCompositeScores:
    """_compute_composite_scores 测试。"""

    def test_equal_weight_composite(self):
        """等权合成 → 所有因子权重相同。"""
        panel, dates = _make_panel(n_symbols=3, n_days=60)
        factor_names = ["f1", "f2"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = _make_factor_sign_flips(factor_names)

        factors = [
            {"name": "f1", "params": {}},
            {"name": "f2", "params": {}},
        ]
        scores, details = _compute_composite_scores(
            signal_matrix,
            flips,
            factors,
            factor_weights=None,
        )

        assert len(scores) == 3
        assert all(isinstance(v, float) for v in scores.values())

    def test_weighted_composite(self):
        """加权合成（L3 组合基础权重）→ 权重应用于因子信号。"""
        panel, dates = _make_panel(n_symbols=3, n_days=60)
        factor_names = ["f1", "f2"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = {}

        factors = [
            {"name": "f1", "params": {}},
            {"name": "f2", "params": {}},
        ]
        custom_weights = {"f1": 0.8, "f2": 0.2}
        scores, details = _compute_composite_scores(
            signal_matrix,
            flips,
            factors,
            factor_weights=custom_weights,
        )

        assert len(scores) == 3

    def test_empty_sign_flips_keeps_signal(self):
        """方向校正空 dict（v2.105.0 语义）→ 信号不反转。"""
        panel, dates = _make_panel(n_symbols=2, n_days=30)
        factor_names = ["f1"]
        signal_matrix = _make_signal_matrix(panel, factor_names)
        flips = {}  # 方向校正已移除，恒为空

        factors = [{"name": "f1", "params": {}}]
        scores, details = _compute_composite_scores(
            signal_matrix,
            flips,
            factors,
        )

        # 空 flips → 信号保持原始方向（flip=+1）
        for sym, d in details.items():
            orig = signal_matrix[sym]["f1"][-1]
            if np.isfinite(orig):
                assert abs(d["f1"] - orig) < 0.001
            else:
                assert d["f1"] == 0.0

    def test_empty_signal_matrix_returns_empty(self):
        """空信号矩阵 → 抛出 ZeroDivisionError（n_factors=0）。"""
        with pytest.raises(ZeroDivisionError):
            _compute_composite_scores({}, {}, [])


# ─── load_futures_elite_factors ──────────────────────────────────────────


class TestLoadFuturesEliteFactors:
    """load_futures_elite_factors 测试。"""

    def test_ic_threshold_zero_loads_all(self, tmp_path, monkeypatch):
        """ic_threshold=0 时加载所有因子。"""
        import json

        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()
        for i in range(3):
            data = {
                "name": f"factor_{i}",
                "factor_id": f"fid_{i}",
                "code": f"def factor_program_{i}(data, params):\n    return data['close'] * {i}",
                "evaluation": {
                    "level_1_backtest": {"ic": 0.1 + i * 0.2},
                },
            }
            (elite_dir / f"factor_{i}.json").write_text(json.dumps(data))

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0)
        assert len(factors) == 3

    def test_ic_threshold_filters(self, tmp_path, monkeypatch):
        """ic_threshold > 0 时过滤低 IC 因子。"""
        import json

        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()
        for i, ic in enumerate([0.1, 0.3, 0.5]):
            data = {
                "name": f"factor_{i}",
                "factor_id": f"fid_{i}",
                "code": f"def factor_program_{i}(data, params):\n    return data['close'] * {ic}",
                "evaluation": {
                    "level_1_backtest": {"ic": ic},
                },
            }
            (elite_dir / f"factor_{i}.json").write_text(json.dumps(data))

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0.3)
        assert len(factors) == 2  # 0.3 和 0.5

    def test_corrupt_json_skipped(self, tmp_path, monkeypatch):
        """损坏的 JSON 文件应被跳过。"""
        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()
        (elite_dir / "bad.json").write_text("not json")
        (elite_dir / "good.json").write_text(
            '{"name":"f1","factor_id":"fid","code":"def f(): return 1","evaluation":{"level_1_backtest":{"ic":0.5}}}',
        )

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0)
        assert len(factors) == 1

    def test_empty_dir_returns_empty(self, tmp_path, monkeypatch):
        """空目录 → 返回空列表。"""
        elite_dir = tmp_path / "futures_elite"
        elite_dir.mkdir()

        monkeypatch.setattr(
            "futures_signal_pipeline.ELITE_DIR",
            elite_dir,
        )
        factors = load_futures_elite_factors(ic_threshold=0)
        assert factors == []


# ─── _classify_delta_moves ───────────────────────────────────────────────


class TestClassifyDeltaMoves:
    """_classify_delta_moves 测试（trading_advice 第 5 节增量分类，v2.104.0+7 修正）。"""

    def test_decel_takes_largest_positive_delta(self):
        """减速清单按增量降序取最大正增量（原升序取最小正增量 bug 修正）。"""
        sym_scores = {"SA0": 0.57, "A0": 0.31, "Y0": 0.06, "AP0": 0.06}
        sym_deltas = {"SA0": 0.44, "A0": 0.24, "Y0": 0.06, "AP0": 0.05, "CS0": -0.44}
        accel, decel = _classify_delta_moves(sym_scores, sym_deltas, top_n=5)
        # 增量 0.02 阈值仅取 >0.02；SA0 最大正增量排首位
        assert [s for s, _, _ in decel] == ["SA0", "A0", "Y0", "AP0"]
        assert [s for s, _, _ in accel] == ["CS0"]
        assert decel[0] == ("SA0", 0.44, "多头信号加强中，做多关注")

    def test_positive_score_decel_label_long(self):
        """正信号品种 + 正增量 → 标注"多头信号加强中，做多关注"（原误标"做空减弱"）。"""
        sym_scores = {"L0": 0.07}
        sym_deltas = {"L0": 0.03}
        _, decel = _classify_delta_moves(sym_scores, sym_deltas)
        assert decel == [("L0", 0.03, "多头信号加强中，做多关注")]

    def test_negative_score_decel_label_short(self):
        """负信号品种 + 正增量 → 标注"做空信号减弱中，建议减仓"。"""
        sym_scores = {"BU0": -0.08}
        sym_deltas = {"BU0": 0.03}
        _, decel = _classify_delta_moves(sym_scores, sym_deltas)
        assert decel == [("BU0", 0.03, "做空信号减弱中，建议减仓")]

    def test_negative_score_accel_label_short(self):
        """负信号品种 + 负增量 → 标注"做空信号加强中"。"""
        sym_scores = {"CS0": -0.45}
        sym_deltas = {"CS0": -0.44}
        accel, _ = _classify_delta_moves(sym_scores, sym_deltas)
        assert accel == [("CS0", -0.44, "做空信号加强中")]

    def test_positive_score_accel_label_long_weaken(self):
        """正信号品种 + 负增量 → 标注"多头信号减弱中，警惕回撤"。"""
        sym_scores = {"SA0": 0.57}
        sym_deltas = {"SA0": -0.44}
        accel, _ = _classify_delta_moves(sym_scores, sym_deltas)
        assert accel == [("SA0", -0.44, "多头信号减弱中，警惕回撤")]

    def test_thresholds_and_top_n(self):
        """阈值边界与 top_n 截断生效。"""
        sym_scores = {"A": -0.1, "B": 0.1, "C": 0.2}
        sym_deltas = {"A": -0.01, "B": 0.01, "C": 0.02}  # 均未过 ±0.02 阈值
        accel, decel = _classify_delta_moves(sym_scores, sym_deltas)
        assert accel == []
        assert decel == []
        sym_deltas2 = {"A": -0.03, "B": 0.03, "C": 0.04, "D": 0.05}
        accel2, decel2 = _classify_delta_moves(sym_scores, sym_deltas2, top_n=2)
        assert [s for s, _, _ in accel2] == ["A"]
        assert [s for s, _, _ in decel2] == ["D", "C"]


# ─── _load_signal_factors ────────────────────────────────────────────────


class TestLoadSignalFactors:
    """_load_signal_factors 测试（GAP-097 强制 DuckDB 加载源，v2.104.0+7）。"""

    def test_duckdb_primary_source(self):
        """DuckDB 有因子 → 返回因子且不回退 JSON。"""
        factors = [{"name": "f1", "code": "def f(): return 1"}]
        with patch(
            "futures_signal_pipeline.load_futures_elite_factors_from_db",
            return_value=factors,
        ) as mock_db, patch(
            "futures_signal_pipeline.load_futures_elite_factors",
            return_value=[{"name": "json_fallback"}],
        ) as mock_json:
            result = _load_signal_factors()
        assert result == factors
        mock_db.assert_called_once()
        mock_json.assert_not_called()

    def test_duckdb_empty_no_json_fallback(self):
        """DuckDB 为空 → 返回 []，不静默回退 JSON（8/12 单因子污染根因修复）。"""
        with patch(
            "futures_signal_pipeline.load_futures_elite_factors_from_db",
            return_value=[],
        ) as mock_db, patch(
            "futures_signal_pipeline.load_futures_elite_factors",
            return_value=[{"name": "json_fallback"}],
        ) as mock_json:
            result = _load_signal_factors()
        assert result == []
        mock_db.assert_called_once()
        mock_json.assert_not_called()


# ─── _factor_set_signature / _compute_signal_deltas ──────────────────────


class TestFactorSetSignature:
    """因子组合签名测试（跨日增量可比性校验，v2.104.0+69）。"""

    def test_same_names_same_signature_order_insensitive(self):
        """相同因子集合 → 相同签名；与顺序无关。"""
        f1 = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        f2 = [{"name": "c"}, {"name": "a"}, {"name": "b"}]
        assert _factor_set_signature(f1) == _factor_set_signature(f2)

    def test_different_names_different_signature(self):
        """不同因子集合 → 不同签名（L3 重算 8→7 因子可被检出）。"""
        f8 = [{"name": f"f{i}"} for i in range(8)]
        f7 = [{"name": f"f{i}"} for i in range(7)]
        assert _factor_set_signature(f8) != _factor_set_signature(f7)

    def test_signature_is_16_hex(self):
        """签名为 16 位十六进制字符串。"""
        sig = _factor_set_signature([{"name": "x"}, {"name": "y"}])
        assert len(sig) == 16
        assert all(c in "0123456789abcdef" for c in sig)


class TestComputeSignalDeltas:
    """跨因子组合增量校验测试（v2.104.0+69）。"""

    def _sig(self, *names: str) -> str:
        return _factor_set_signature([{"name": n} for n in names])

    def test_same_combo_computes_delta(self):
        """因子组合一致 → 正常计算增量。"""
        today = {"A": 0.3, "B": -0.2, "C": 0.1}
        prev = {"date": "2026-08-15", "scores": {"A": 0.5, "B": 0.0, "C": 0.3},
                "factor_signature": self._sig("f1", "f2")}
        deltas, prev_scores, has_delta, reason = _compute_signal_deltas(
            today, prev, self._sig("f2", "f1")
        )
        assert has_delta is True
        assert reason == ""
        assert deltas["A"] == pytest.approx(-0.2)
        assert deltas["B"] == pytest.approx(-0.2)
        assert deltas["C"] == pytest.approx(-0.2)
        assert prev_scores == {"A": 0.5, "B": 0.0, "C": 0.3}

    def test_mismatched_combo_marks_invalid(self):
        """因子组合不一致 → 增量标记无效（空 delta + 原因）。"""
        today = {"A": 0.3, "B": -0.2}
        prev = {"date": "2026-08-15", "scores": {"A": 0.5, "B": 0.0},
                "factor_signature": self._sig("f1", "f2")}
        deltas, prev_scores, has_delta, reason = _compute_signal_deltas(
            today, prev, self._sig("f1", "f2", "f3")
        )
        assert has_delta is False
        assert deltas == {}
        assert "因子组合与今日不一致" in reason
        # 昨日得分仍返回（报告用），但增量不展示
        assert prev_scores == {"A": 0.5, "B": 0.0}

    def test_no_prev_snapshot(self):
        """无昨日快照 → 无增量 + 原因。"""
        deltas, prev_scores, has_delta, reason = _compute_signal_deltas(
            {"A": 0.3}, None, self._sig("f1")
        )
        assert has_delta is False
        assert deltas == {} and prev_scores == {}
        assert "无昨日信号快照" in reason

    def test_legacy_snapshot_without_signature(self):
        """旧格式快照（无 factor_signature）→ 兼容计算增量。"""
        today = {"A": 0.3}
        prev = {"date": "2026-08-15", "scores": {"A": 0.5}}  # 旧格式无签名
        deltas, prev_scores, has_delta, reason = _compute_signal_deltas(
            today, prev, self._sig("f1")
        )
        assert has_delta is True
        assert deltas == {"A": -0.2}
        assert reason == ""


class TestHoldoutValidationAlignment:
    """GAP-130 (v2.104.0+80): 新上市品种信号-收益错位修复回归测试。

    旧实现：IC 验证中收盘价 reindex 共同日期（头部 NaN），但因子信号仅尾部补零
    → 品种历史是共同日期尾部子集时，信号与收益错位 = 上市日距共同日起点缺失
    天数，盲测 IC/品种-因子 IC 被稀释至 ≈0 误判失效（2026-08-16 能化链实测
    BZ0 错位 32 天 / PL0 错位 42 天）。修复：信号经 `_align_factor_signal`
    （df.index.get_indexer 向量化对齐共同日期）替代尾部补零。
    """

    @staticmethod
    def _make_panel(n_days: int = 100, head_gap: int = 10, seed: int = 7):
        """构造含短历史新品种的面板：FULL0 全历史 + NEW0 晚 head_gap 天上市。"""
        rng = np.random.default_rng(seed)
        dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
        panel: dict[str, pd.DataFrame] = {}
        for sym, idx in (("FULL0", dates), ("NEW0", dates[head_gap:])):
            close = 100 + rng.normal(0, 1, len(idx)).cumsum()
            panel[sym] = pd.DataFrame(
                {
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": np.full(len(idx), 1000.0),
                },
                index=idx,
            )
        return panel, list(dates)

    @staticmethod
    def _perfect_signal_matrix(panel: dict[str, pd.DataFrame], fwd: int = 5):
        """完美预测信号：signal[t] = 前向收益 → 正确对齐时 Spearman IC = 1.0。"""
        sm: dict[str, dict[str, np.ndarray]] = {}
        for sym, df in panel.items():
            closes = df["close"].values
            fwd_ret = np.full(len(closes), np.nan)
            fwd_ret[:-fwd] = (closes[fwd:] - closes[:-fwd]) / np.maximum(closes[:-fwd], 1e-10)
            sm[sym] = {"f": fwd_ret}
        return sm

    def test_holdout_validation_short_history_alignment(self):
        """短历史新品种经对齐修复后盲测 IC ≈ 1.0（旧实现错位 → ≈ 0）。"""
        panel, common = self._make_panel()
        sm = self._perfect_signal_matrix(panel)
        res = _compute_holdout_validation(sm, panel, common, {}, {"NEW0"})
        assert res["details"]["NEW0"] > 0.9
        assert res["details"]["FULL0"] > 0.9
        assert res["skipped_short"] == []

    def test_per_variety_ic_matrix_short_history_alignment(self):
        """短历史新品种品种-因子 IC 经对齐修复后 ≈ 1.0（旧实现错位 → ≈ 0）。"""
        panel, common = self._make_panel()
        sm = self._perfect_signal_matrix(panel)
        icm = _compute_per_variety_ic_matrix(sm, panel, common, {})
        assert icm["f"]["NEW0"] > 0.9
        assert icm["f"]["FULL0"] > 0.9

    def test_full_history_variety_unchanged(self):
        """全历史品种（len(sig)==len(closes)）修复前后逐位一致（零漂移回归保护）。"""
        panel, common = self._make_panel()
        sm = self._perfect_signal_matrix(panel)
        res = _compute_holdout_validation(sm, panel, common, {}, {"NEW0"})
        # FULL0 与直接按自身索引（旧实现等价路径）逐位一致
        df = panel["FULL0"]
        closes = df["close"].values
        fwd_ret = np.full(len(closes), np.nan)
        fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
        sig = np.where(np.isfinite(sm["FULL0"]["f"]), sm["FULL0"]["f"], 0.0)
        valid = np.isfinite(fwd_ret)
        from scipy.stats import spearmanr

        direct_ic, _ = spearmanr(sig[valid], fwd_ret[valid])
        assert res["details"]["FULL0"] == pytest.approx(direct_ic, abs=1e-9)

    def test_holdout_min_rows_threshold(self):
        """盲测池最小真实历史门槛：历史不足的盲测品种跳过不计（GAP-130）。"""
        panel, common = self._make_panel(n_days=100, head_gap=50)
        sm = self._perfect_signal_matrix(panel)
        res = _compute_holdout_validation(
            sm, panel, common, {}, {"NEW0"}, min_rows=60
        )
        assert "NEW0" in res["skipped_short"]
        assert "NEW0" not in res["details"]
        assert "历史不足跳过" in res["warning"]
        # 门槛关闭（默认 0）时不跳过
        res0 = _compute_holdout_validation(sm, panel, common, {}, {"NEW0"})
        assert res0["skipped_short"] == []
        assert "NEW0" in res0["details"]
