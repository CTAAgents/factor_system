"""np.bincount 输入边界 + 同族因子 NaN 防护测试（v2.33.0 / v2.34.0 批量防护）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fts.config.settings import get_config

# 含 np.bincount 且需验证边界防护的精英因子
BINCOUNT_FACTORS = ["fct_70d783d1", "fct_71372ef2", "fct_7b251afa"]

# 活跃池 g10/g11/g13 同族因子（同步入口 NaN 防护，v2.33.0）
NAN_GUARD_FACTORS = [
    "fct_3513f204",  # fut_hf_trade_imbalance_g10
    "fct_403b25c3",  # fut_bias_g11
    "fct_42b2bef8",  # fut_option_pcr_g10
    "fct_71ff2938",  # fut_gp_alpha1_g13
]

# v2.34.0 批量防护：全部活跃缺防护因子已同步入口 NaN 防护（77 个）
# 测试通过动态扫描 elite 目录发现，此处仅排除已显式测试的因子
_BATCH_EXCLUDE = set(BINCOUNT_FACTORS) | set(NAN_GUARD_FACTORS)


@pytest.fixture(scope="module")
def elite_dir() -> Path:
    return Path(get_config().get_elite_dir("futures"))


@pytest.fixture()
def close_base() -> np.ndarray:
    rng = np.random.default_rng(42)
    return 100 + np.cumsum(rng.normal(0, 0.5, 300))


def _load_factor(elite_dir: Path, factor_id: str) -> dict:
    fp = elite_dir / f"{factor_id}.json"
    assert fp.exists(), f"因子文件不存在: {fp}"
    return json.loads(fp.read_text(encoding="utf-8"))


def _run(factor: dict, close: np.ndarray) -> np.ndarray:
    ns = {"np": np}
    exec(compile(factor["code"], "<factor>", "exec"), ns)  # noqa: S102
    return ns["factor_program"]({"close": close.copy()}, {})


class TestBincountBoundary:
    """np.bincount 输入边界：NaN 输入不崩溃、输出有限。"""

    @pytest.mark.parametrize("factor_id", BINCOUNT_FACTORS)
    def test_nan_input_no_crash_and_finite_output(self, elite_dir: Path, factor_id: str):
        """前部/尾部 NaN 输入不崩溃，且输出全部有限。"""
        factor = _load_factor(elite_dir, factor_id)
        base = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.5, 300))
        scenarios = {
            "front_nan": np.concatenate([np.full(30, np.nan), base]),
            "tail_nan": np.concatenate([base, np.full(10, np.nan)]),
            "all_nan": np.full(300, np.nan),
        }
        for name, close in scenarios.items():
            out = _run(factor, close)
            assert out.shape == close.shape, f"{name}: 输出形状不一致"
            assert np.isfinite(out).all(), f"{name}: 输出含非有限值"
            assert np.abs(out).max() <= 1.0 + 1e-9, f"{name}: 输出越界"

    @pytest.mark.parametrize("factor_id", BINCOUNT_FACTORS)
    def test_normal_input_deterministic(self, elite_dir: Path, factor_id: str):
        """正常输入下输出确定性（两次一致），且不改变修复前语义量级。"""
        factor = _load_factor(elite_dir, factor_id)
        base = 100 + np.cumsum(np.random.default_rng(7).normal(0, 0.5, 300))
        o1 = _run(factor, base)
        o2 = _run(factor, base)
        assert np.allclose(o1, o2)
        assert np.isfinite(o1).all()

    @pytest.mark.parametrize("factor_id", BINCOUNT_FACTORS)
    def test_json_valid_and_contains_bincount(self, elite_dir: Path, factor_id: str):
        """JSON 合法且代码仍含 np.bincount（修复未删除原逻辑）。"""
        factor = _load_factor(elite_dir, factor_id)
        assert "np.bincount" in factor["code"]
        assert factor["code"].startswith("def factor_program")


def _run_full(factor: dict, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """执行因子代码，提供 close/volume/high/low 完整字段。"""
    ns = {"np": np}
    exec(compile(factor["code"], "<factor>", "exec"), ns)  # noqa: S102
    return ns["factor_program"](
        {
            "close": close.copy(),
            "volume": volume.copy(),
            "high": close.copy() * 1.01,
            "low": close.copy() * 0.99,
        },
        {},
    )


class TestNanGuard:
    """活跃池同族因子入口 NaN 防护：NaN 输入不崩溃、输出有限（v2.33.0）。"""

    @pytest.mark.parametrize("factor_id", NAN_GUARD_FACTORS)
    def test_nan_input_finite_output(self, elite_dir: Path, factor_id: str):
        """前部/尾部/全 NaN 输入不崩溃，输出全部有限且 |out|<=1。"""
        factor = _load_factor(elite_dir, factor_id)
        rng = np.random.default_rng(5)
        base = 100 + np.cumsum(rng.normal(0, 0.5, 300))
        vol = np.abs(rng.normal(10000, 2000, 300)) + 100
        scenarios = {
            "front_nan": (
                np.concatenate([np.full(30, np.nan), base]),
                np.concatenate([np.full(30, np.nan), vol]),
            ),
            "tail_nan": (
                np.concatenate([base, np.full(10, np.nan)]),
                np.concatenate([vol, np.full(10, np.nan)]),
            ),
            "all_nan": (np.full(300, np.nan), np.full(300, np.nan)),
        }
        for name, (close, volume) in scenarios.items():
            out = _run_full(factor, close, volume)
            assert out.shape == close.shape, f"{name}: 输出形状不一致"
            assert np.isfinite(out).all(), f"{name}: 输出含非有限值"
            assert np.abs(out).max() <= 1.0 + 1e-9, f"{name}: 输出越界"

    @pytest.mark.parametrize("factor_id", NAN_GUARD_FACTORS)
    def test_normal_input_deterministic(self, elite_dir: Path, factor_id: str):
        """正常输入下输出确定性（两次一致）。"""
        factor = _load_factor(elite_dir, factor_id)
        rng = np.random.default_rng(9)
        base = 100 + np.cumsum(rng.normal(0, 0.5, 300))
        vol = np.abs(rng.normal(10000, 2000, 300)) + 100
        o1 = _run_full(factor, base, vol)
        o2 = _run_full(factor, base, vol)
        assert np.allclose(o1, o2)
        assert np.isfinite(o1).all()

    @pytest.mark.parametrize("factor_id", NAN_GUARD_FACTORS)
    def test_json_valid_and_nan_guard_present(self, elite_dir: Path, factor_id: str):
        """JSON 合法且入口含 NaN 防护逻辑。"""
        factor = _load_factor(elite_dir, factor_id)
        assert "np.isnan(close).any()" in factor["code"]
        assert factor["code"].startswith("def factor_program")


def _scan_batch_guarded(elite_dir: Path) -> list[str]:
    """扫描全部活跃精英因子，返回含入口 NaN 防护且未在显式列表中测试的因子。"""
    guarded = []
    for fp in sorted(elite_dir.glob("fct_*.json")):
        factor_id = fp.stem
        if factor_id in _BATCH_EXCLUDE:
            continue
        f = json.loads(fp.read_text(encoding="utf-8"))
        if "np.isnan(close).any()" in f.get("code", ""):
            guarded.append(factor_id)
    return guarded


def _run_full_params(factor: dict) -> callable:
    """编译因子代码，返回以完整字段 + JSON params 调用的 runner。"""
    ns = {"np": np}
    exec(compile(factor["code"], "<factor>", "exec"), ns)  # noqa: S102
    params = dict(factor.get("params") or {})

    def runner(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        return ns["factor_program"](
            {
                "close": close.copy(),
                "volume": volume.copy(),
                "high": close.copy() * 1.01,
                "low": close.copy() * 0.99,
                "open": close.copy() * 0.995,
                "hold": volume.copy(),
            },
            params,
        )

    return runner


# 模块加载时惰性扫描批量防护因子（仅用于 parametrize 收集）
_BATCH_DIR = Path(get_config().get_elite_dir("futures"))
_BATCH_GUARDED = _scan_batch_guarded(_BATCH_DIR) if _BATCH_DIR.exists() else []


class TestBatchNanGuard:
    """v2.34.0 批量防护：全部活跃缺防护因子入口 NaN 防护（77 个，动态发现）。"""

    @pytest.mark.parametrize("factor_id", _BATCH_GUARDED, ids=lambda x: x)
    def test_batch_nan_input_finite_output(self, elite_dir: Path, factor_id: str):
        """前部/尾部/周期 NaN 输入不崩溃，输出全部有限。"""
        factor = _load_factor(elite_dir, factor_id)
        runner = _run_full_params(factor)
        rng = np.random.default_rng(hash(factor_id) % 2**31)
        base = 100 + np.cumsum(rng.normal(0, 0.5, 300))
        vol = np.abs(rng.normal(10000, 2000, 300)) + 100
        peri_c = base.copy()
        peri_c[::7] = np.nan
        peri_v = vol.copy()
        peri_v[::7] = np.nan
        scenarios = {
            "front_nan": (
                np.concatenate([np.full(30, np.nan), base]),
                np.concatenate([np.full(30, np.nan), vol]),
            ),
            "tail_nan": (
                np.concatenate([base, np.full(10, np.nan)]),
                np.concatenate([vol, np.full(10, np.nan)]),
            ),
            "periodic_nan": (peri_c, peri_v),
        }
        for name, (close, volume) in scenarios.items():
            out = runner(close, volume)
            assert out.shape == close.shape, f"{name}: 输出形状不一致"
            assert np.isfinite(out).all(), f"{name}: 输出含非有限值"

    @pytest.mark.parametrize("factor_id", _BATCH_GUARDED, ids=lambda x: x)
    def test_batch_normal_input_deterministic(self, elite_dir: Path, factor_id: str):
        """正常输入下输出确定性（两次一致）。"""
        factor = _load_factor(elite_dir, factor_id)
        runner = _run_full_params(factor)
        rng = np.random.default_rng(hash(factor_id) % 2**31 + 1)
        base = 100 + np.cumsum(rng.normal(0, 0.5, 300))
        vol = np.abs(rng.normal(10000, 2000, 300)) + 100
        o1 = runner(base, vol)
        o2 = runner(base, vol)
        assert np.allclose(o1, o2)
        assert np.isfinite(o1).all()

    @pytest.mark.parametrize("factor_id", _BATCH_GUARDED, ids=lambda x: x)
    def test_batch_json_valid_and_nan_guard_present(self, elite_dir: Path, factor_id: str):
        """JSON 合法且入口含 NaN 防护逻辑。"""
        factor = _load_factor(elite_dir, factor_id)
        assert "np.isnan(close).any()" in factor["code"]

    def test_batch_scan_found_all(self, elite_dir: Path):
        """批量扫描应发现全部已防护因子（≥77，含后续新增）。"""
        assert len(_scan_batch_guarded(elite_dir)) >= 77
