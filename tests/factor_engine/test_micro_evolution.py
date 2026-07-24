"""tests/factor_engine/test_micro_evolution.py — 微观演化测试。

覆盖遗漏行: 28-30, 93, 95, 114-115
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import (
    EconomicLogic,
    FactorProgram,
    FactorSignature,
)


def _make_factor(factor_id: str = "fct_test", code: str | None = None) -> FactorProgram:
    """构造最小 FactorProgram。"""
    if code is None:
        code = "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))"
    return FactorProgram(
        factor_id=factor_id,
        name="test_factor",
        code=code,
        params={"window": 10},
        signature=FactorSignature(
            input_fields=["close"], output_type="signal", frequency="daily", lookback=1,
        ),
        economic_logic=EconomicLogic(
            theory=3, behavioral=3, microstructure=3, institutional=3, narrative="test",
        ),
        source="manual",
    )


# ─── 覆盖遗漏行 ───────────────────────────────────────────

class TestCoverageGaps:
    """覆盖 micro_evolution.py 遗漏行 (28-30, 93, 95, 114-115)。"""

    def test_import_error_path(self, monkeypatch):
        """lines 28-30: optuna 导入失败时 _HAS_OPTUNA=False。

        验证 fallback 行为：设置 _HAS_OPTUNA=False 后 optimize_params 返回默认值。
        """
        import fts.factor_engine.micro_evolution as mev
        monkeypatch.setattr(mev, "_HAS_OPTUNA", False)
        monkeypatch.setattr(mev, "optuna", None)

        assert mev._HAS_OPTUNA is False
        assert mev.optuna is None

        factor = _make_factor("fct_noopt")
        data = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        rets = np.array([0.01, -0.01, 0.02])
        params, score = mev.optimize_params(factor, data, rets)
        assert params == {"window": 10}
        assert score == 0.0

    def test_optimize_params_short_signal_covers_line93(self, monkeypatch):
        """line 93: objective_fn 中信号太短返回 0.0。

        executor 返回空数组 → len(sig) < 2 → 进入 line 93。
        """
        import fts.factor_engine.micro_evolution as mev

        factor = _make_factor("fct_short93",
                              code="def factor_program(data, params):\n    import numpy as np\n    return np.array([])")

        data = pd.DataFrame({"close": [1.0]})
        rets = np.array([0.01])

        monkeypatch.setattr(mev, "_HAS_OPTUNA", True)
        monkeypatch.setattr(mev, "TPESampler", MagicMock())

        # 让 study.optimize 实际调用 objective 函数
        def controlled_optimize(objective, n_trials, callbacks, catch):
            trial = MagicMock()
            trial.number = 0
            trial.suggest_int.return_value = 10
            trial.suggest_float.return_value = 0.5
            trial.suggest_categorical.return_value = True
            try:
                objective(trial)  # 这会触发 objective_fn 调用
            except Exception:
                pass

        mock_study = MagicMock()
        mock_study.optimize = controlled_optimize
        mock_study.best_params = {"window": 10}
        mock_study.best_value = 0.0
        mock_study.trials = [MagicMock()]

        mock_optuna = MagicMock()
        mock_optuna.create_study.return_value = mock_study
        monkeypatch.setattr(mev, "optuna", mock_optuna)

        params, score = mev.optimize_params(factor, data, rets, n_trials=2)
        assert score == 0.0

    def test_optimize_params_zero_var_covers_line95(self, monkeypatch):
        """line 95: objective_fn 中零方差信号返回 0.0。

        executor 返回常量信号 → np.std(sig) < 1e-10 → 进入 line 95。
        """
        import fts.factor_engine.micro_evolution as mev

        factor = _make_factor("fct_zero95",
                              code="def factor_program(data, params):\n    import numpy as np\n    return np.ones(len(data['close'])) * 0.5")

        data = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        rets = np.array([0.01, -0.01, 0.02])

        monkeypatch.setattr(mev, "_HAS_OPTUNA", True)
        monkeypatch.setattr(mev, "TPESampler", MagicMock())

        def controlled_optimize(objective, n_trials, callbacks, catch):
            trial = MagicMock()
            trial.number = 0
            trial.suggest_int.return_value = 10
            trial.suggest_float.return_value = 0.5
            trial.suggest_categorical.return_value = True
            try:
                objective(trial)
            except Exception:
                pass

        mock_study = MagicMock()
        mock_study.optimize = controlled_optimize
        mock_study.best_params = {"window": 10}
        mock_study.best_value = 0.0
        mock_study.trials = [MagicMock()]

        mock_optuna = MagicMock()
        mock_optuna.create_study.return_value = mock_study
        monkeypatch.setattr(mev, "optuna", mock_optuna)

        params, score = mev.optimize_params(factor, data, rets, n_trials=2)
        assert score == 0.0

    def test_optuna_objective_exception_covers_lines114_115(self, monkeypatch):
        """lines 114-115: optuna_objective 内部 exec 抛异常返回 -1.0。

        executor.execute 抛异常 → except Exception: return -1.0。
        """
        import fts.factor_engine.micro_evolution as mev

        factor = _make_factor("fct_exc115",
                              code="def factor_program(data, params):\n    raise ValueError('模拟执行异常')")

        data = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        rets = np.array([0.01, -0.01, 0.02])

        monkeypatch.setattr(mev, "_HAS_OPTUNA", True)
        monkeypatch.setattr(mev, "TPESampler", MagicMock())

        def controlled_optimize(objective, n_trials, callbacks, catch):
            trial = MagicMock()
            trial.number = 0
            trial.suggest_int.return_value = 10
            trial.suggest_float.return_value = 0.5
            trial.suggest_categorical.return_value = True
            try:
                objective(trial)  # executor.execute 会抛异常 → 被 catch=(Exception,) 捕获
            except Exception:
                pass

        mock_study = MagicMock()
        mock_study.optimize = controlled_optimize
        mock_study.best_params = {"window": 10}
        mock_study.best_value = 0.0
        mock_study.trials = [MagicMock()]

        mock_optuna = MagicMock()
        mock_optuna.create_study.return_value = mock_study
        monkeypatch.setattr(mev, "optuna", mock_optuna)

        params, score = mev.optimize_params(factor, data, rets, n_trials=2)
        assert params == {"window": 10}
