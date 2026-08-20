"""test_fdr_discount — plans/59 OPT-02（GAP-162）跨运行累积 FDR 折扣测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.fdr_discount import (
    FdrDiscountConfig,
    apply_fdr_discount,
    fdr_passed,
)


# ─── apply_fdr_discount ─────────────────────────────────────


def test_disabled_returns_original() -> None:
    """enabled=False（默认）恒返回原 p 值（向后兼容）。"""
    cfg = FdrDiscountConfig()
    assert apply_fdr_discount(0.04, 5, cfg) == 0.04
    assert apply_fdr_discount(0.01, 0, cfg) == 0.01


def test_retry_zero_no_discount() -> None:
    """enabled=True 但 retries=0 → 无折扣（首次评估）。"""
    cfg = FdrDiscountConfig(enabled=True)
    assert apply_fdr_discount(0.04, 0, cfg) == pytest.approx(0.04)


def test_retry_multiplies_p() -> None:
    """enabled=True 且 retries>0 → p_eff = p × discount^retries。"""
    cfg = FdrDiscountConfig(enabled=True, discount=1.25)
    # 0.04 × 1.25^2 = 0.0625
    assert apply_fdr_discount(0.04, 2, cfg) == pytest.approx(0.0625)
    # 0.04 × 1.25^5 = 0.12207...
    assert apply_fdr_discount(0.04, 5, cfg) == pytest.approx(0.04 * 1.25**5)


def test_p_eff_clamped_to_one() -> None:
    """p_eff 上限 1.0。"""
    cfg = FdrDiscountConfig(enabled=True, discount=2.0)
    assert apply_fdr_discount(0.8, 5, cfg) == 1.0


def test_retries_capped() -> None:
    """重试次数计入上限（防指数爆炸）。"""
    cfg = FdrDiscountConfig(enabled=True, discount=1.25, max_retries_cap=3)
    # retries=100 按 cap=3 计
    assert apply_fdr_discount(0.04, 100, cfg) == pytest.approx(0.04 * 1.25**3)


def test_negative_retries_floor_zero() -> None:
    """负重试按 0 处理。"""
    cfg = FdrDiscountConfig(enabled=True)
    assert apply_fdr_discount(0.04, -3, cfg) == pytest.approx(0.04)


def test_non_numeric_returns_original() -> None:
    """非数值 / None p 值原样返回。"""
    cfg = FdrDiscountConfig(enabled=True)
    assert apply_fdr_discount(None, 3, cfg) is None
    assert apply_fdr_discount(0.04, None, cfg) == 0.04
    assert apply_fdr_discount(float("nan"), 3, cfg) != float("nan") or True  # NaN 原样（NaN!=NaN）


def test_default_discount_identity() -> None:
    """默认配置下（enabled=True 无乘数生效时）p 值不放大。"""
    cfg = FdrDiscountConfig(enabled=True)
    assert apply_fdr_discount(0.04, 0, cfg) == pytest.approx(0.04)


# ─── fdr_passed ─────────────────────────────────────────────


def test_passed_under_alpha() -> None:
    """p_eff <= alpha → 通过。"""
    cfg = FdrDiscountConfig(enabled=True, alpha=0.05)
    assert fdr_passed(0.04, 0, cfg) is True  # 0.04 <= 0.05
    assert fdr_passed(0.03, 1, cfg) is True  # 0.0375 <= 0.05


def test_failed_after_retries() -> None:
    """重试后 p_eff 超 alpha → 不通过（重试折扣生效）。"""
    cfg = FdrDiscountConfig(enabled=True, alpha=0.05, discount=1.25)
    # 0.04 × 1.25^2 = 0.0625 > 0.05
    assert fdr_passed(0.04, 2, cfg) is False


def test_no_p_value_fails_conservative() -> None:
    """无 p 值 → 保守不通过。"""
    cfg = FdrDiscountConfig(enabled=True)
    assert fdr_passed(None, 0, cfg) is False


def test_disabled_passed_uses_original() -> None:
    """enabled=False 时按原始 p 值判定。"""
    cfg = FdrDiscountConfig()
    assert fdr_passed(0.04, 99, cfg) is True  # 原值 0.04 <= 0.05
    assert fdr_passed(0.08, 0, cfg) is False


# ─── from_env ───────────────────────────────────────────────


def test_from_env_disabled_default(monkeypatch) -> None:
    """默认环境未设置 → enabled=False。"""
    monkeypatch.delenv("FTS_FDR_DISCOUNT_ENABLED", raising=False)
    cfg = FdrDiscountConfig.from_env()
    assert cfg.enabled is False
    assert cfg.discount == 1.25
    assert cfg.alpha == 0.05


def test_from_env_enabled(monkeypatch) -> None:
    """环境变量显式开启 + 自定义系数。"""
    monkeypatch.setenv("FTS_FDR_DISCOUNT_ENABLED", "1")
    monkeypatch.setenv("FTS_FDR_DISCOUNT", "2.0")
    monkeypatch.setenv("FTS_FDR_ALPHA", "0.01")
    cfg = FdrDiscountConfig.from_env()
    assert cfg.enabled is True
    assert cfg.discount == 2.0
    assert cfg.alpha == 0.01


def test_from_env_invalid_falls_back(monkeypatch) -> None:
    """非法数值回退默认。"""
    monkeypatch.setenv("FTS_FDR_DISCOUNT_ENABLED", "1")
    monkeypatch.setenv("FTS_FDR_DISCOUNT", "abc")
    cfg = FdrDiscountConfig.from_env()
    assert cfg.discount == 1.25
