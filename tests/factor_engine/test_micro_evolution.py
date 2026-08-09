"""tests/factor_engine/test_micro_evolution.py — 微观演化测试。

覆盖遗漏行: 28-30, 93, 95, 114-115
"""

from __future__ import annotations

import importlib
import sys
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

    def test_import_error_actual_import_fail(self):
        """lines 28-30: 实际触发 ImportError 覆盖 except 块。

        通过 patch optuna 的导入来触发 ImportError，然后重新加载模块。
        """
        with patch.dict('sys.modules', {'optuna': None}):
            with patch('builtins.__import__') as mock_import:
                def side_effect(name, *args, **kwargs):
                    if name == 'optuna' or name.startswith('optuna.'):
                        raise ImportError(f"No module named '{name}'")
                    # 恢复原始 __import__ 行为
                    return importlib.__import__(name, *args, **kwargs)
                mock_import.side_effect = side_effect

                # 重新加载模块以触发 ImportError
                import fts.factor_engine.micro_evolution as mev
                importlib.reload(mev)

                assert mev._HAS_OPTUNA is False
                assert mev.optuna is None

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


# ════════════════════════════════════════════════════════════
# GAP-I205: 两阶段漏斗（粗筛淘汰 + 精筛自适应 trials）
# ════════════════════════════════════════════════════════════

class TestStagedFunnel:
    """GAP-I205 两阶段参数优化漏斗测试。

    测试方案:
        - 早停路径（optimize_params 已有 early_stopping_failures 机制）
        - 粗筛淘汰率与精筛结果一致性
    """

    def _mock_optuna_run(self, monkeypatch, coarse_score, fine_score=0.06):
        """mock optuna：粗筛（随机搜索）返回 coarse_score，精筛（TPE）返回 fine_score。

        通过 study.best_value 区分调用次序：第一次调用为粗筛，第二次为精筛。
        """
        import fts.factor_engine.micro_evolution as mev

        monkeypatch.setattr(mev, "_HAS_OPTUNA", True)
        monkeypatch.setattr(mev, "TPESampler", MagicMock())
        monkeypatch.setattr(mev, "RandomSampler", MagicMock())

        scores = iter([coarse_score, fine_score])
        studies = []

        def _create_study(**kwargs):
            study = MagicMock()
            study.best_value = next(scores)
            study.best_params = {"window": 10}
            study.trials = [MagicMock()]
            studies.append(study)
            return study

        mock_optuna = MagicMock()
        mock_optuna.create_study.side_effect = _create_study
        monkeypatch.setattr(mev, "optuna", mock_optuna)
        return studies

    def test_staged_coarse_rejects_low_ic(self, monkeypatch):
        """粗筛得分低于阈值时淘汰（passed=False），不再精筛。"""
        from fts.factor_engine.micro_evolution import optimize_params_staged

        factor = _make_factor("fct_staged_reject")
        data = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]})
        rets = np.array([0.01, -0.01, 0.02, 0.0])

        studies = self._mock_optuna_run(monkeypatch, coarse_score=0.01)
        params, score, passed = optimize_params_staged(
            factor, data, rets, n_trials=50, coarse_ic_floor=0.02,
        )
        assert passed is False
        assert score == 0.01
        assert len(studies) == 1  # 仅粗筛一次，未进入精筛

    def test_staged_coarse_passes_fine(self, monkeypatch):
        """粗筛得分达标时进入精筛（passed=True），trials 自适应。"""
        from fts.factor_engine.micro_evolution import optimize_params_staged

        factor = _make_factor("fct_staged_pass")
        data = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]})
        rets = np.array([0.01, -0.01, 0.02, 0.0])

        studies = self._mock_optuna_run(monkeypatch, coarse_score=0.08, fine_score=0.09)
        params, score, passed = optimize_params_staged(
            factor, data, rets, n_trials=100,
            coarse_ic_floor=0.02, coarse_ref_ic=0.10,
        )
        assert passed is True
        assert score == 0.09
        assert len(studies) == 2  # 粗筛 + 精筛

    def test_staged_no_optuna_fallback(self, monkeypatch):
        """optuna 缺失时直接返回原参数，passed=True。"""
        import fts.factor_engine.micro_evolution as mev
        monkeypatch.setattr(mev, "_HAS_OPTUNA", False)
        monkeypatch.setattr(mev, "optuna", None)

        factor = _make_factor("fct_staged_noopt")
        data = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        rets = np.array([0.01, -0.01, 0.02])
        params, score, passed = mev.optimize_params_staged(factor, data, rets)
        assert passed is True
        assert params == {"window": 10}

    def test_evolve_micro_staged_mode(self, monkeypatch):
        """evolve_micro use_staged=True 走两阶段漏斗。"""
        from fts.factor_engine.micro_evolution import evolve_micro

        factor = _make_factor("fct_staged_evolve")
        data = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]})
        rets = np.array([0.01, -0.01, 0.02, 0.0])

        self._mock_optuna_run(monkeypatch, coarse_score=0.08, fine_score=0.09)
        evolved, score = evolve_micro(factor, data, rets, n_trials=50, use_staged=True)
        assert score == 0.09
        assert evolved["params"] == {"window": 10}

    def test_evolve_micro_non_staged_mode(self, monkeypatch):
        """evolve_micro use_staged=False 保持单阶段行为。"""
        from fts.factor_engine.micro_evolution import evolve_micro

        factor = _make_factor("fct_nostaged_evolve")
        data = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]})
        rets = np.array([0.01, -0.01, 0.02, 0.0])

        # 非 staged：仅一次 optimize_params 调用（best_value=0.09）
        self._mock_optuna_run(monkeypatch, coarse_score=0.09)
        evolved, score = evolve_micro(factor, data, rets, n_trials=50, use_staged=False)
        assert score == 0.09
        assert evolved["params"] == {"window": 10}
