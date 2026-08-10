"""tests/factor_engine/conftest.py — pytest 配置与 fixtures。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest

if TYPE_CHECKING:  # pragma: no cover
    from fts.factor_engine.evolution_loop import EvolutionLoop

# 把 FTS 根目录加入 sys.path（fts.factor_engine 是 FTS 包的子模块）
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))


# ─── 共享 fixtures ────────────────────────────────────────


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """500 天的合成 OHLCV 数据（用于因子评估测试）。"""
    np.random.seed(42)
    n = 500
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame(
        {
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": np.random.randint(1000, 10000, n).astype(float),
        },
        index=dates,
    )


@pytest.fixture
def forward_returns(sample_ohlcv) -> np.ndarray:
    """未来 1 日收益率数组（与 sample_ohlcv 等长）。"""
    close = sample_ohlcv["close"].values
    rets = np.zeros(len(close))
    rets[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    return rets


@pytest.fixture
def tmp_memory_dir(tmp_path) -> Path:
    """临时 memory 目录（每个测试独立，目录预先创建）。"""
    p = tmp_path / "evolution"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_elite_dir(tmp_path) -> Path:
    """临时 elite 池目录。"""
    return tmp_path / "elite"


# ─── Mock fixtures（减少 test_evolution_loop.py 中的重复 Mock 代码）───


@pytest.fixture
def mock_trial():
    """Mock optuna trial 对象。"""
    from unittest.mock import MagicMock

    return MagicMock()


@pytest.fixture
def mock_optuna_study(monkeypatch):
    """Mock optuna 完整环境（TPESampler + optuna 模块 + create_study）。

    返回 (mock_optuna, mock_study)，测试可各自设置 mock_study 的行为。
    """
    from unittest.mock import MagicMock
    import fts.factor_engine.micro_evolution as mev

    monkeypatch.setattr(mev, "TPESampler", MagicMock(), raising=False)
    monkeypatch.setattr(mev, "_HAS_OPTUNA", True)
    mock_optuna = MagicMock()
    monkeypatch.setattr(mev, "optuna", mock_optuna)
    mock_study = MagicMock()
    mock_optuna.create_study.return_value = mock_study
    return mock_optuna, mock_study


@pytest.fixture
def mock_evolve_micro():
    """Patch fts.factor_engine.evolution_loop.evolve_micro。"""
    from unittest.mock import patch

    with patch("fts.factor_engine.evolution_loop.evolve_micro") as m:
        yield m


# ─── EvolutionLoop 集成测试 fixtures ──────────────────────


@pytest.fixture
def sample_seed() -> dict:
    """示例种子因子数据。"""
    return {
        "factor_id": "seed_test_001",
        "name": "test_momentum",
        "code": "close - close.shift(1)",
        "factor_type": "momentum",
        "description": "测试动量因子",
    }


@pytest.fixture
def sample_evaluation() -> dict:
    """示例评估结果。"""
    return {
        "ic": 0.05,
        "icir": 1.2,
        "sharpe": 1.5,
        "max_drawdown": 0.08,
        "total_return": 0.25,
        "passed": True,
        "level_1_backtest": {
            "ic": 0.05,
            "sharpe": 1.5,
            "max_drawdown": 0.08,
        },
    }


@pytest.fixture
def sample_dataframe(sample_ohlcv) -> pd.DataFrame:
    """与 sample_ohlcv 相同的 DataFrame。"""
    return sample_ohlcv


@pytest.fixture
def sample_forward_returns(forward_returns) -> np.ndarray:
    """与 forward_returns 相同的数组。"""
    return forward_returns


@pytest.fixture
def minimal_loop(sample_dataframe, sample_forward_returns, tmp_path) -> "EvolutionLoop":
    """最小化配置的 EvolutionLoop 实例。"""
    from fts.factor_engine.evolution_loop import EvolutionLoop

    return EvolutionLoop(
        data=sample_dataframe,
        forward_returns=sample_forward_returns,
        elite_dir=str(tmp_path / "elite"),
        memory_dir=str(tmp_path / "memory"),
        n_trials_micro=5,
    )
