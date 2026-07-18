"""tests/factor_engine/conftest.py — pytest 配置与 fixtures。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

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
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    }, index=dates)


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
