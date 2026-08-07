"""
tests/factor_engine/test_sector_regime.py — SectorRegimeSelector 测试

覆盖范围:
    - detect_all() 对各产业链独立检测市场制度
    - 不同产业链可检测出不同 regime
    - 空 panel / 品种不足 / 数据不足的兜底
    - 返回格式: dict[str, MarketRegime]

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.regime import (
    SectorRegimeSelector,
    MarketRegime,
)


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def sector_selector() -> SectorRegimeSelector:
    return SectorRegimeSelector(lookback_days=60, use_hmm=False)


@pytest.fixture
def sector_map() -> dict[str, list[str]]:
    """测试用产业链映射。"""
    return {
        "向上板块": ["SYM1", "SYM2", "SYM3"],
        "向下板块": ["SYM4", "SYM5", "SYM6"],
        "震荡板块": ["SYM7", "SYM8", "SYM9"],
    }


def _make_sym_ohlcv(
    close_series: np.ndarray,
    dates: pd.DatetimeIndex,
    vol_scale: float = 0.002,
) -> pd.DataFrame:
    """从收盘价序列构造单个品种的 OHLCV DataFrame。"""
    n = len(close_series)
    return pd.DataFrame({
        "open": close_series * (1 + np.random.randn(n) * vol_scale),
        "high": close_series * (1 + np.abs(np.random.randn(n)) * vol_scale * 2),
        "low": close_series * (1 - np.abs(np.random.randn(n)) * vol_scale * 2),
        "close": close_series,
        "volume": np.random.randint(800, 1200, n).astype(float),
    }, index=dates)


def _make_panel(
    sector_map: dict[str, list[str]],
    prices: dict[str, np.ndarray],
    dates: pd.DatetimeIndex,
) -> dict[str, pd.DataFrame]:
    """从各品种价格序列构造面板数据。"""
    panel: dict[str, pd.DataFrame] = {}
    for sym, close in prices.items():
        panel[sym] = _make_sym_ohlcv(close, dates)
    return panel


# ─── 1. 不同产业链检测出不同 regime ──────────────────────

def test_detect_all_divergent_regimes(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """不同趋势的产业链检测出不同的 regime。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")

    # 向上板块: 强上涨趋势
    up_close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
    # 向下板块: 强下跌趋势（与 bear test 一致，drift -0.15）
    down_close = 100 + np.cumsum(np.random.randn(n) * 0.3 - 0.15)
    # 震荡板块: 无趋势、中等波动
    flat_close = 100 + np.random.randn(n) * 1.0

    prices = {
        "SYM1": up_close,
        "SYM2": up_close + np.random.randn(n) * 0.5,
        "SYM3": up_close + np.random.randn(n) * 0.5,
        "SYM4": down_close,
        "SYM5": down_close + np.random.randn(n) * 0.5,
        "SYM6": down_close + np.random.randn(n) * 0.5,
        "SYM7": flat_close,
        "SYM8": flat_close + np.random.randn(n) * 0.5,
        "SYM9": flat_close + np.random.randn(n) * 0.5,
    }
    panel = _make_panel(sector_map, prices, dates)

    result = sector_selector.detect_all(panel, sector_map=sector_map)

    # 三个板块的 regime 应不同
    assert "向上板块" in result
    assert "向下板块" in result
    assert "震荡板块" in result

    assert result["向上板块"]["regime"] == "bull", (
        f"向上板块预期 bull，实际 {result['向上板块']['regime']}"
    )
    assert result["向下板块"]["regime"] == "bear", (
        f"向下板块预期 bear，实际 {result['向下板块']['regime']}"
    )
    assert result["震荡板块"]["regime"] == "oscillate", (
        f"震荡板块预期 oscillate，实际 {result['震荡板块']['regime']}"
    )


# ─── 2. 返回格式验证 ─────────────────────────────────────

def test_detect_all_result_format(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """返回 dict[str, MarketRegime]，每项含 regime/confidence/features。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)

    prices = {sym: close + np.random.randn(n) * 0.5
              for syms in sector_map.values() for sym in syms}
    panel = _make_panel(sector_map, prices, dates)

    result = sector_selector.detect_all(panel, sector_map=sector_map)

    for sector_name, regime in result.items():
        assert isinstance(regime, dict), f"{sector_name}: 应为 dict"
        assert "regime" in regime, f"{sector_name}: 缺少 regime"
        assert "confidence" in regime, f"{sector_name}: 缺少 confidence"
        assert "features" in regime, f"{sector_name}: 缺少 features"
        assert 0 <= regime["confidence"] <= 1, (
            f"{sector_name}: confidence={regime['confidence']} 超出 [0,1]"
        )


# ─── 3. 空 panel ─────────────────────────────────────────

def test_detect_all_empty_panel(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """空 panel → 返回空 dict。"""
    result = sector_selector.detect_all({}, sector_map=sector_map)
    assert result == {}, f"预期空 dict，实际 {result}"


# ─── 4. 品种不足的产业链 ─────────────────────────────────

def test_detect_all_sector_too_few_symbols(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """产业链中只有 1 个品种 → 跳过该板块。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)

    # 只有 symbol1 有数据
    panel = {"SYM1": _make_sym_ohlcv(close, dates)}
    result = sector_selector.detect_all(panel, sector_map=sector_map)

    # 所有板块都无足够品种 → 空 dict
    assert result == {}, f"预期空 dict，实际 {result}"


# ─── 5. 部分品种在 panel 中缺失 ──────────────────────────

def test_detect_all_partial_symbols_in_panel(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """产业链部分品种在 panel 中 → 用可用品种检测。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)

    # 向上板块只提供 2 个品种（仍可检测）
    panel = {
        "SYM1": _make_sym_ohlcv(close, dates),
        "SYM2": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
    }
    # 使用简化的 sector_map，只包含向上板块的 2 个品种
    simple_map = {"向上板块": ["SYM1", "SYM2"]}
    result = sector_selector.detect_all(panel, sector_map=simple_map)

    assert "向上板块" in result
    assert isinstance(result["向上板块"]["regime"], str)


# ─── 6. 数据不足 20 行 ───────────────────────────────────

def test_detect_all_short_data(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """数据不足 20 行 → 跳过该板块。"""
    np.random.seed(42)
    n = 10
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)

    prices = {sym: close + np.random.randn(n) * 0.5
              for syms in sector_map.values() for sym in syms}
    panel = _make_panel(sector_map, prices, dates)

    result = sector_selector.detect_all(panel, sector_map=sector_map)
    assert result == {}, f"预期空 dict，实际 {result}"


# ─── 7. 默认 sector_map=FUTURES_SECTOR_MAP ───────────────

def test_detect_all_default_sector_map() -> None:
    """不传 sector_map 时使用默认的 FUTURES_SECTOR_MAP。"""
    selector = SectorRegimeSelector(lookback_days=60)
    # 构建一个涵盖多个产业链品种的简单面板
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.1)

    # 用少量真实期货品种代码
    panel = {
        "RB0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
        "I0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
        "CU0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
        "ZN0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
        "SC0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
        "MA0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
        "M0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
        "C0": _make_sym_ohlcv(close + np.random.randn(n) * 0.5, dates),
    }

    # 不传 sector_map，使用默认的 FUTURES_SECTOR_MAP
    result = selector.detect_all(panel)
    assert isinstance(result, dict)
    # 至少应该有 2 个以上产业链被检测到
    assert len(result) >= 2, f"预期至少 2 个产业链，实际 {len(result)}"


# ─── 8. 置信度合理性 ─────────────────────────────────────

def test_detect_all_confidence_sensible(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """各板块置信度与趋势强度合理对应。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")

    # 强趋势板块（低漂移 + 极低噪音，信噪比高，波动率低）
    strong_trend = 100 + np.cumsum(np.random.randn(n) * 0.05 + 0.2)
    # 弱趋势板块（微漂移 + 高噪音，信噪比低）
    weak_trend = 100 + np.cumsum(np.random.randn(n) * 0.25 + 0.05)

    prices = {
        "SYM1": strong_trend,
        "SYM2": strong_trend + np.random.randn(n) * 0.5,
        "SYM3": strong_trend + np.random.randn(n) * 0.5,
        "SYM4": weak_trend,
        "SYM5": weak_trend + np.random.randn(n) * 0.5,
        "SYM6": weak_trend + np.random.randn(n) * 0.5,
    }
    panel = _make_panel(
        {"强趋势": ["SYM1", "SYM2", "SYM3"], "弱趋势": ["SYM4", "SYM5", "SYM6"]},
        prices, dates,
    )

    result = sector_selector.detect_all(panel, sector_map={
        "强趋势": ["SYM1", "SYM2", "SYM3"],
        "弱趋势": ["SYM4", "SYM5", "SYM6"],
    })

    assert "强趋势" in result
    assert "弱趋势" in result
    # 强趋势板块置信度应更高
    assert result["强趋势"]["confidence"] >= result["弱趋势"]["confidence"], (
        f"强趋势({result['强趋势']['confidence']}) 置信度应 >= "
        f"弱趋势({result['弱趋势']['confidence']})"
    )


# ─── 9. 多次调用 detect_all 结果一致 ─────────────────────

def test_detect_all_deterministic(
    sector_selector: SectorRegimeSelector,
    sector_map: dict[str, list[str]],
) -> None:
    """相同输入多次调用，结果一致（无随机性）。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")

    # 使用固定种子，但给每个品种不同的随机序列
    base = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.3)
    rng = np.random.RandomState(42)

    prices = {
        "SYM1": base + rng.randn(n) * 0.5,
        "SYM2": base + rng.randn(n) * 0.5,
        "SYM3": base + rng.randn(n) * 0.5,
    }
    panel = _make_panel({"板块A": ["SYM1", "SYM2", "SYM3"]}, prices, dates)

    result1 = sector_selector.detect_all(panel, sector_map={"板块A": ["SYM1", "SYM2", "SYM3"]})
    result2 = sector_selector.detect_all(panel, sector_map={"板块A": ["SYM1", "SYM2", "SYM3"]})

    assert result1["板块A"]["regime"] == result2["板块A"]["regime"]
    assert result1["板块A"]["confidence"] == result2["板块A"]["confidence"]
    assert result1["板块A"]["features"] == result2["板块A"]["features"]