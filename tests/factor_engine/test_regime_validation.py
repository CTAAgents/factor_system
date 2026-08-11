"""
tests/factor_engine/test_regime_validation.py — 制度有效性样本外验证测试（28 计划 T9）

对标:
  - 学术通则（VAE 市场画像一文局限性）: Regime 标签需样本外验证——制度标签须能
    区分前向收益/前向波动，否则不应驱动仓位。

覆盖范围:
    - validate_regime_predictive_power: 制度标签能区分前向收益
      （bull 均值 > bear 均值）+ Kruskal-Wallis 组间差异统计量
    - 每个制度分组的统计字段（count/mean_fwd_return/mean_fwd_vol/fwd_return_std）
    - 空数据兜底返回 {"n": 0, "error": "empty"}

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.regime_validation import validate_regime_predictive_power


def test_validate_detects_predictive_regimes() -> None:
    """制度标签应能区分前向收益（bull 均值 > bear 均值）且输出 Kruskal-Wallis 统计量。

    固定 seed rng 生成 300 样本（bull 均值 0.001 / bear 均值 -0.001 / std 0.01），
    bull/bear 条件均值差与组间分布差异在统计上成立。
    """
    rng = np.random.default_rng(0)
    n = 300
    regimes = ["bull"] * 150 + ["bear"] * 150
    fwd = np.concatenate([rng.normal(0.001, 0.01, 150), rng.normal(-0.001, 0.01, 150)])
    fwd_vol = np.abs(fwd)
    result = validate_regime_predictive_power(pd.Series(regimes), pd.Series(fwd), pd.Series(fwd_vol))
    assert result["n"] == n
    assert result["bull"]["mean_fwd_return"] > result["bear"]["mean_fwd_return"]
    assert "kruskal_p" in result  # 组间差异统计量（scipy 可用时）


def test_validate_per_regime_stats_shape() -> None:
    """每个制度分组应输出 count/mean_fwd_return/mean_fwd_vol/fwd_return_std 字段。"""
    rng = np.random.default_rng(1)
    n = 200
    regimes = ["bull"] * 100 + ["high_vol"] * 100
    fwd = rng.normal(0.0, 0.01, n)
    result = validate_regime_predictive_power(pd.Series(regimes), pd.Series(fwd), pd.Series(np.abs(fwd)))
    assert result["n"] == n
    for regime in ("bull", "high_vol"):
        stats = result[regime]
        assert stats["count"] == 100
        assert "mean_fwd_return" in stats
        assert "mean_fwd_vol" in stats
        assert "fwd_return_std" in stats
        assert stats["fwd_return_std"] > 0.0


def test_validate_empty_input() -> None:
    """空数据应返回 {"n": 0, "error": "empty"}。"""
    result = validate_regime_predictive_power(
        pd.Series(dtype=str),
        pd.Series(dtype=float),
        pd.Series(dtype=float),
    )
    assert result == {"n": 0, "error": "empty"}
