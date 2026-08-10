"""GAP-F15 极值扰动一票否决测试。

覆盖:
    - _compute_extreme_perturbation_ic 纯函数（极值依赖因子大降幅 / 稳健因子小降幅 / 边界）
    - EvaluationChain.evaluate 输出注入 extreme_perturbation
    - HighICScreener V2 一票否决（ic_drop > 25% 拦截 / 稳健因子放行）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.evaluation_chain import (
    _compute_extreme_perturbation_ic,
    EvaluationChain,
)
from fts.factor_engine.contracts import (
    FactorProgram,
    EconomicLogic,
    FactorSignature,
)
from fts.factor_engine.factor_program import create_factor_program
from fts.factor_engine.high_ic_screener import HighICScreener


def _rng() -> np.random.Generator:
    return np.random.default_rng(42)


def _extreme_dependent_signal(n: int = 500) -> tuple[np.ndarray, np.ndarray]:
    """构造极值依赖样本：仅上下 2% 信号极值样本的秩与收益强相关，其余为噪声。

    用 linspace 保证极值分位内秩单调对齐，使 IC 主要由少数极端样本支撑。
    """
    rng = _rng()
    sig = rng.normal(0, 1, n)
    ret = rng.normal(0, 1, n)
    idx = np.argsort(sig)
    k = max(int(n * 0.02), 5)
    amp = 4.0
    ret[idx[-k:]] = np.linspace(amp * 0.5, amp, k)
    ret[idx[:k]] = np.linspace(-amp, -amp * 0.5, k)
    return sig, ret


def _robust_signal(n: int = 500) -> tuple[np.ndarray, np.ndarray]:
    """构造稳健因子：信号与收益全样本线性相关，无极端样本依赖。"""
    rng = _rng()
    sig = rng.normal(0, 1, n)
    ret = sig * 0.3 + rng.normal(0, 1, n)
    return sig, ret


# ─── 纯函数测试 ────────────────────────────────────────────


class TestComputeExtremePerturbationIc:
    def test_extreme_dependent_triggers_large_drop(self):
        sig, ret = _extreme_dependent_signal()
        r = _compute_extreme_perturbation_ic(sig, ret, pct=0.01)
        assert r is not None
        assert abs(r["ic_before"]) > abs(r["ic_after"])
        assert r["ic_drop"] > 0.3, f"极值依赖因子应大幅降幅: {r}"
        assert r["n_removed"] > 0

    def test_robust_factor_small_drop(self):
        sig, ret = _robust_signal()
        r = _compute_extreme_perturbation_ic(sig, ret, pct=0.01)
        assert r is not None
        assert r["ic_drop"] < 0.2, f"稳健因子降幅应小: {r}"

    def test_insufficient_samples_returns_none(self):
        sig = np.array([1.0, 2.0, 3.0])
        ret = np.array([0.1, 0.2, 0.3])
        assert _compute_extreme_perturbation_ic(sig, ret) is None

    def test_constant_signal_returns_none(self):
        sig = np.ones(100)
        ret = np.arange(100, dtype=float)
        assert _compute_extreme_perturbation_ic(sig, ret) is None

    def test_nan_handling(self):
        sig, ret = _robust_signal()
        sig[::7] = np.nan
        ret[::11] = np.nan
        r = _compute_extreme_perturbation_ic(sig, ret)
        assert r is not None

    def test_custom_pct(self):
        sig, ret = _extreme_dependent_signal(n=1000)
        r1 = _compute_extreme_perturbation_ic(sig, ret, pct=0.005)
        r5 = _compute_extreme_perturbation_ic(sig, ret, pct=0.05)
        assert r1 is not None and r5 is not None
        # 剔除范围越大，扰动越强
        assert r5["ic_drop"] >= r1["ic_drop"]


# ─── EvaluationChain 注入 ──────────────────────────────────


def _make_factor(code_body: str) -> FactorProgram:
    """构造语法合法的测试因子（code_body 为 factor_program 函数体）。

    注意：沙箱 execute 会对信号做 np.clip(-10, 10)，故测试因子需返回
    标准化信号（避免原始价格 ~100 被钳成常数 10 致 std=0）。
    """
    code = f"def factor_program(data, params):\n    import numpy as np\n    close = data['close']\n    {code_body}\n"
    return create_factor_program(
        name="test_extreme_perturb",
        code=code,
        params={},
        signature=FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=10,
        ),
        economic_logic=EconomicLogic(
            theory=4,
            behavioral=3,
            microstructure=3,
            institutional=3,
            narrative="测试极值扰动因子",
        ),
        source="seed",
        generation=0,
    )


class TestEvaluationChainInjection:
    def _data(self) -> pd.DataFrame:
        n = 300
        rng = _rng()
        close = 100 + np.cumsum(rng.normal(0, 0.5, n))
        return pd.DataFrame(
            {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0},
            index=pd.date_range("2024-01-01", periods=n, freq="D"),
        )

    def test_evaluate_injects_extreme_perturbation(self):
        data = self._data()
        ret = data["close"].pct_change().fillna(0.0).values
        factor = _make_factor("return (close - np.mean(close)) / (np.std(close) + 1e-10)")
        ev = EvaluationChain().evaluate(factor, data, ret)
        perturb = ev.get("extreme_perturbation")
        assert perturb is not None, "evaluation 应注入 extreme_perturbation"
        assert {"ic_before", "ic_after", "ic_drop"} <= set(perturb.keys())


# ─── Screener V2 一票否决端到端 ────────────────────────────


class TestScreenerVeto:
    def _screen_eval(self, sig: np.ndarray, ret: np.ndarray) -> dict:
        """构造带 extreme_perturbation 的 evaluation 字典。"""
        perturb = _compute_extreme_perturbation_ic(sig, ret, pct=0.01)
        return {"level_1_backtest": {"ic": 0.3}, "extreme_perturbation": perturb}

    def test_extreme_dependent_veto(self):
        """极值依赖因子（ic_drop > 25%）触发 V2 一票否决。"""
        sig, ret = _extreme_dependent_signal()
        perturb = _compute_extreme_perturbation_ic(sig, ret, pct=0.01)
        assert perturb is not None and perturb["ic_drop"] > 0.25

        screener = HighICScreener()
        result = screener.screen(
            factor=_make_factor("return (close - np.mean(close)) / (np.std(close) + 1e-10)"),
            evaluation=self._screen_eval(sig, ret),
        )
        assert result.grade == "C"
        assert any("极值" in v for v in result.veto_reasons)

    def test_robust_factor_passes_no_veto(self):
        """稳健因子（ic_drop <= 25%）不触发极值否决。"""
        sig, ret = _robust_signal()
        perturb = _compute_extreme_perturbation_ic(sig, ret, pct=0.01)
        assert perturb is not None and perturb["ic_drop"] <= 0.25

        screener = HighICScreener()
        result = screener.screen(
            factor=_make_factor("return (close - np.mean(close)) / (np.std(close) + 1e-10)"),
            evaluation=self._screen_eval(sig, ret),
        )
        assert not any("极值" in v for v in result.veto_reasons)

    def test_missing_perturbation_not_vetoed(self):
        """扰动数据缺失仅降分（screener 既有行为），不触发一票否决。"""
        screener = HighICScreener()
        result = screener.screen(
            factor=_make_factor("return (close - np.mean(close)) / (np.std(close) + 1e-10)"),
            evaluation={"level_1_backtest": {"ic": 0.3}},
        )
        assert not any("极值" in v for v in result.veto_reasons)
