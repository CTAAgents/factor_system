"""
tests/factor_engine/test_g1_config.py — L3 G1 同向敞口惩罚参数配置化测试。

覆盖：
  1. FTSConfig 默认 l3_g1_* 与历史硬编码一致（回归锚点）
  2. YAML 覆盖 l3_g1_*
  3. 环境变量 FTS_L3_G1_* 覆盖
  4. AlignedExposureConfig 契约校验（非法值快速失败）
  5. check_aligned_exposure 消费自定义参数（scale 输出正确）
  6. PortfolioLoop 从配置消费 G1 参数
  7. 默认配置下 build_combo 行为不变（向后兼容）

背景：G1 参数 v2.104.0+X 前硬编码于 portfolio_risk_controls.AlignedExposureConfig，
本次配置化（config/settings.yaml + FTS_L3_G1_* 环境变量），默认值保持 0.60/0.50/linear。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from fts.config.settings import FTSConfig, load_config
from fts.factor_engine.portfolio_risk_controls import AlignedExposureConfig, check_aligned_exposure


# ═══════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════


def _signals(ic_signs: list[float], weight: float = 1.0) -> list[dict]:
    """按 IC 符号构造信号列表（ic>0 看多 / ic<0 看空，check_aligned_exposure 语义）。"""
    return [{"factor_id": f"f{i}", "ic": s, "weight": weight} for i, s in enumerate(ic_signs)]


ALL_LONG = _signals([0.1, 0.1, 0.1, 0.1])          # long_ratio=1.0
PARTIAL = _signals([0.1, 0.1, 0.1, -0.1])          # long_ratio=0.75


# ═══════════════════════════════════════════════════════════
# 1. 默认值与历史硬编码一致
# ═══════════════════════════════════════════════════════════


class TestDefaults:
    """FTSConfig l3_g1_* 默认值 = 历史硬编码（回归锚点）。"""

    def test_defaults_match_hardcoded(self) -> None:
        cfg = FTSConfig()
        assert cfg.l3_g1_enabled is True
        assert cfg.l3_g1_align_threshold == pytest.approx(0.60)
        assert cfg.l3_g1_max_compress == pytest.approx(0.50)
        assert cfg.l3_g1_compress_curve == "linear"

    def test_aligned_exposure_default_config(self) -> None:
        """AlignedExposureConfig 默认参数与 FTSConfig 一致，且校验通过。"""
        c = AlignedExposureConfig()
        assert c.enabled is True
        assert c.align_threshold == pytest.approx(0.60)
        assert c.max_compress == pytest.approx(0.50)
        assert c.compress_curve == "linear"


# ═══════════════════════════════════════════════════════════
# 2. YAML 覆盖
# ═══════════════════════════════════════════════════════════


class TestYamlOverride:
    """YAML 配置 l3_g1_* 覆盖默认值。"""

    def test_yaml_override(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        p.write_text(
            "l3_g1_enabled: false\n"
            "l3_g1_align_threshold: 0.80\n"
            "l3_g1_max_compress: 0.70\n"
            "l3_g1_compress_curve: sqrt\n",
            encoding="utf-8",
        )
        cfg = load_config(config_path=str(p))
        assert cfg.l3_g1_enabled is False
        assert cfg.l3_g1_align_threshold == pytest.approx(0.80)
        assert cfg.l3_g1_max_compress == pytest.approx(0.70)
        assert cfg.l3_g1_compress_curve == "sqrt"

    def test_yaml_partial_keeps_default(self, tmp_path: Path) -> None:
        """YAML 仅覆盖部分字段，其余保留默认。"""
        p = tmp_path / "config.yaml"
        p.write_text("l3_g1_max_compress: 0.70\n", encoding="utf-8")
        cfg = load_config(config_path=str(p))
        assert cfg.l3_g1_max_compress == pytest.approx(0.70)
        assert cfg.l3_g1_align_threshold == pytest.approx(0.60)
        assert cfg.l3_g1_compress_curve == "linear"


# ═══════════════════════════════════════════════════════════
# 3. 环境变量覆盖
# ═══════════════════════════════════════════════════════════


class TestEnvOverride:
    """FTS_L3_G1_* 环境变量覆盖。"""

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FTS_L3_G1_ENABLED", "0")
        monkeypatch.setenv("FTS_L3_G1_ALIGN_THRESHOLD", "0.80")
        monkeypatch.setenv("FTS_L3_G1_MAX_COMPRESS", "0.70")
        monkeypatch.setenv("FTS_L3_G1_COMPRESS_CURVE", "exp")
        cfg = load_config(config_path=None)
        assert cfg.l3_g1_enabled is False
        assert cfg.l3_g1_align_threshold == pytest.approx(0.80)
        assert cfg.l3_g1_max_compress == pytest.approx(0.70)
        assert cfg.l3_g1_compress_curve == "exp"


# ═══════════════════════════════════════════════════════════
# 4. 契约校验
# ═══════════════════════════════════════════════════════════


class TestValidation:
    """AlignedExposureConfig 非法值快速失败。"""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"align_threshold": 1.5},
            {"align_threshold": 0.0},
            {"max_compress": -0.1},
            {"max_compress": 1.2},
            {"compress_curve": "cubic"},
        ],
    )
    def test_validation_raises(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            AlignedExposureConfig(**kwargs)

    def test_valid_values_pass(self) -> None:
        """边界合法值通过校验。"""
        assert AlignedExposureConfig(align_threshold=0.0 + 1e-6, max_compress=1.0).max_compress == 1.0
        assert AlignedExposureConfig(align_threshold=1.0).align_threshold == 1.0


# ═══════════════════════════════════════════════════════════
# 5. check_aligned_exposure 消费自定义参数
# ═══════════════════════════════════════════════════════════


class TestCustomParamsScale:
    """自定义 G1 参数下的 scale 输出正确性。"""

    def test_max_compress_070_full_long(self) -> None:
        """max_compress=0.7 + 全多（ratio=1.0）→ scale=0.7（linear 压至下限）。"""
        r = check_aligned_exposure(ALL_LONG, AlignedExposureConfig(max_compress=0.70))
        assert r["triggered"] is True
        assert r["long_ratio"] == pytest.approx(1.0)
        assert r["compress_scale"] == pytest.approx(0.70)

    def test_threshold_080_partial_not_triggered(self) -> None:
        """align_threshold=0.8 + ratio=0.75 → 不触发，scale=1.0。"""
        r = check_aligned_exposure(PARTIAL, AlignedExposureConfig(align_threshold=0.80))
        assert r["triggered"] is False
        assert r["compress_scale"] == pytest.approx(1.0)

    def test_threshold_060_partial_triggered(self) -> None:
        """默认 threshold=0.6 + ratio=0.75 → 触发，scale=0.8125（对照锚点）。"""
        r = check_aligned_exposure(PARTIAL, AlignedExposureConfig())
        assert r["triggered"] is True
        assert r["compress_scale"] == pytest.approx(0.8125)


# ═══════════════════════════════════════════════════════════
# 6. PortfolioLoop 从配置消费
# ═══════════════════════════════════════════════════════════


class TestLoopConsumesConfig:
    """PortfolioLoop.__init__ 从 FTSConfig 构建 _g1_config。"""

    def test_loop_reads_g1_config(self, tmp_path: Path) -> None:
        from fts.factor_engine.portfolio_loop import PortfolioLoop

        cfg = FTSConfig(l3_g1_enabled=False, l3_g1_max_compress=0.70)
        with patch("fts.config.settings.get_config", return_value=cfg):
            loop = PortfolioLoop(memory_dir=str(tmp_path))
        assert loop._g1_config.enabled is False
        assert loop._g1_config.max_compress == pytest.approx(0.70)
        assert loop._g1_config.align_threshold == pytest.approx(0.60)

    def test_loop_default_config(self, tmp_path: Path) -> None:
        """未配置时 _g1_config 与历史硬编码一致（回归锚点）。"""
        from fts.factor_engine.portfolio_loop import PortfolioLoop

        with patch("fts.config.settings.get_config", return_value=FTSConfig()):
            loop = PortfolioLoop(memory_dir=str(tmp_path))
        assert loop._g1_config.align_threshold == pytest.approx(0.60)
        assert loop._g1_config.max_compress == pytest.approx(0.50)
        assert loop._g1_config.compress_curve == "linear"


# ═══════════════════════════════════════════════════════════
# 7. 向后兼容（默认配置下行为不变）
# ═══════════════════════════════════════════════════════════


class TestBackwardCompat:
    """默认配置下 G1 行为与历史版本一致。"""

    def test_default_scale_full_long(self) -> None:
        """全多组合默认压缩至 0.5（历史行为）。"""
        r = check_aligned_exposure(ALL_LONG, AlignedExposureConfig())
        assert r["triggered"] is True
        assert r["compress_scale"] == pytest.approx(0.50)

    def test_disabled_returns_scale_one(self) -> None:
        """enabled=False 时不触发，scale=1.0。"""
        r = check_aligned_exposure(ALL_LONG, AlignedExposureConfig(enabled=False))
        assert r["triggered"] is False
        assert r["compress_scale"] == pytest.approx(1.0)
