"""tests/factor_engine/test_factor_optimizer.py — factor_optimizer 分层优化框架测试。

覆盖:
    1. FactorCacheKey 缓存键（to_dict / cache_id 确定性）
    2. FactorSignalCache 信号缓存（内存/磁盘/命中率/失效/损坏容错）
    3. CorrelationCache 相关矩阵缓存（内存/磁盘/清理/命中率）
    4. FactorOptimizer 信号矩阵并行计算（顺序/并行/缓存命中/强制重算/短数据跳过）
    5. tiered_orthogonalize 两阶段正交化（L2 先验/Phase1 代码重复/家族裁剪/Phase2 高相关标记）
    6. compute_correlation_from_matrix（全量/采样/空信号零矩阵）

隔离性说明: 所有测试均使用 tmp_path + monkeypatch，不写全局状态，避免污染其他测试。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# 确保 fts 包可导入
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

import fts.factor_engine.factor_optimizer as fo  # noqa: E402
from fts.factor_engine.factor_optimizer import (  # noqa: E402
    CorrelationCache,
    FactorCacheKey,
    FactorOptimizer,
    FactorSignalCache,
    create_optimizer,
    set_panel_ref,
)

# 合法的沙箱因子代码
FACTOR_CODE_OK = """
def factor_program(data, params):
    return data['close']
"""

FACTOR_CODE_FAIL = """
import os
def factor_program(data, params):
    return data['close']
"""


# ─── 工具函数 ──────────────────────────────────────────────


def _make_df(n: int = 120, seed: int = 1, start: str = "2026-01-01") -> pd.DataFrame:
    """构造含 close 列的合成行情。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n, freq="D")
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.1, n),
            "high": close + np.abs(rng.normal(0, 0.3, n)),
            "low": close - np.abs(rng.normal(0, 0.3, n)),
            "close": close,
            "volume": rng.integers(1000, 10000, n).astype(float),
        },
        index=dates,
    )


def _make_factor(fid: str, name: str, code: str = FACTOR_CODE_OK, **extra) -> dict:
    factor = {"factor_id": fid, "name": name, "code": code}
    factor.update(extra)
    return factor


def _make_panel(n_symbols: int = 2, n: int = 120) -> dict[str, pd.DataFrame]:
    return {f"SYM{i}": _make_df(n=n, seed=i) for i in range(n_symbols)}


# ─── FactorCacheKey ────────────────────────────────────────


class TestFactorCacheKey:
    def test_to_dict_fields(self):
        key = FactorCacheKey(factor_id="f1", factor_code_hash="h1", symbol="RB0", data_version="v1")
        d = key.to_dict()
        assert set(d) == {"factor_id", "factor_code_hash", "symbol", "data_version"}
        assert d["factor_id"] == "f1"

    def test_cache_id_deterministic(self):
        k1 = FactorCacheKey("f1", "h1", "RB0", "v1")
        k2 = FactorCacheKey("f1", "h1", "RB0", "v1")
        assert k1.cache_id == k2.cache_id
        assert len(k1.cache_id) == 16
        assert int(k1.cache_id, 16) >= 0  # hex 可解析

    def test_cache_id_distinct_on_fields(self):
        base = FactorCacheKey("f1", "h1", "RB0", "v1")
        variants = [
            FactorCacheKey("f2", "h1", "RB0", "v1"),
            FactorCacheKey("f1", "h2", "RB0", "v1"),
            FactorCacheKey("f1", "h1", "CU0", "v1"),
            FactorCacheKey("f1", "h1", "RB0", "v2"),
        ]
        ids = {v.cache_id for v in variants}
        assert base.cache_id not in ids
        assert len(ids) == len(variants)  # 四个变体互不相同


# ─── FactorSignalCache ─────────────────────────────────────


class TestFactorSignalCache:
    def test_init_creates_dir(self, tmp_path):
        cache_dir = tmp_path / "sig"
        FactorSignalCache(cache_dir)
        assert cache_dir.exists()

    def test_get_miss(self, tmp_path):
        cache = FactorSignalCache(tmp_path / "sig")
        key = FactorCacheKey("f1", "h1", "RB0", "v1")
        assert cache.get(key) is None
        assert cache._stats["misses"] == 1
        assert cache._stats["hits"] == 0

    def test_put_then_get_memory_hit(self, tmp_path):
        cache = FactorSignalCache(tmp_path / "sig")
        key = FactorCacheKey("f1", "h1", "RB0", "v1")
        arr = np.array([1.0, 2.0, 3.0])
        cache.put(key, arr)
        result = cache.get(key)
        assert result is not None
        np.testing.assert_array_equal(result, arr)
        assert cache._stats["hits"] == 1
        assert cache._stats["misses"] == 0

    def test_disk_persistence(self, tmp_path):
        cache_dir = tmp_path / "sig"
        key = FactorCacheKey("f1", "h1", "RB0", "v1")
        cache1 = FactorSignalCache(cache_dir)
        cache1.put(key, np.array([1.0, 2.0, 3.0]))
        # 新实例从磁盘加载
        cache2 = FactorSignalCache(cache_dir)
        result = cache2.get(key)
        assert result is not None
        np.testing.assert_array_equal(result, np.array([1.0, 2.0, 3.0]))
        # 索引文件持久化
        index_fp = cache_dir / "signal_index.json"
        assert index_fp.exists()
        meta = json.loads(index_fp.read_text(encoding="utf-8"))
        assert key.cache_id in meta

    def test_corrupted_disk_file_is_miss(self, tmp_path):
        cache_dir = tmp_path / "sig"
        key = FactorCacheKey("f1", "h1", "RB0", "v1")
        cache1 = FactorSignalCache(cache_dir)
        cache1.put(key, np.array([1.0, 2.0]))
        # 破坏 npy 文件
        (cache_dir / f"{key.cache_id}.npy").write_text("not a npy", encoding="utf-8")
        cache2 = FactorSignalCache(cache_dir)
        assert cache2.get(key) is None
        assert cache2._stats["misses"] >= 1

    def test_corrupted_index_file_loaded_as_empty(self, tmp_path):
        cache_dir = tmp_path / "sig"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "signal_index.json").write_text("{broken json", encoding="utf-8")
        cache = FactorSignalCache(cache_dir)
        assert cache._meta == {}

    def test_clear_removes_files(self, tmp_path):
        cache_dir = tmp_path / "sig"
        cache = FactorSignalCache(cache_dir)
        for i in range(3):
            cache.put(FactorCacheKey(f"f{i}", "h", "RB0", "v1"), np.array([i]))
        assert len(list(cache_dir.glob("*.npy"))) == 3
        removed = cache.clear()
        assert removed == 3
        assert len(list(cache_dir.glob("*.npy"))) == 0
        assert cache._signals == {}
        assert cache._meta == {}

    def test_invalidate_factor_only_matches_id(self, tmp_path):
        cache = FactorSignalCache(tmp_path / "sig")
        k1 = FactorCacheKey("f1", "h1", "RB0", "v1")
        k2 = FactorCacheKey("f2", "h1", "RB0", "v1")
        cache.put(k1, np.array([1.0]))
        cache.put(k2, np.array([2.0]))
        removed = cache.invalidate_factor("f1")
        assert removed == 1
        assert cache.get(k1) is None
        assert cache.get(k2) is not None  # f2 保留

    def test_hit_rate(self, tmp_path):
        cache = FactorSignalCache(tmp_path / "sig")
        assert cache.hit_rate == 0.0  # 无访问
        key = FactorCacheKey("f1", "h1", "RB0", "v1")
        cache.get(key)  # miss
        cache.put(key, np.array([1.0]))
        cache.get(key)  # hit
        assert cache.hit_rate == pytest.approx(0.5)


# ─── CorrelationCache ──────────────────────────────────────


class TestCorrelationCache:
    def test_init_creates_dir(self, tmp_path):
        cache_dir = tmp_path / "corr"
        CorrelationCache(cache_dir)
        assert cache_dir.exists()

    def test_put_get_memory(self, tmp_path):
        cache = CorrelationCache(tmp_path / "corr")
        ids = ["b", "a"]
        matrix = np.array([[1.0, 0.5], [0.5, 1.0]])
        cache.put(ids, matrix)
        result, labels = cache.get(ids)
        np.testing.assert_array_equal(result, matrix)
        assert labels == sorted(ids)  # 标签按排序存储

    def test_matrix_id_order_independent(self, tmp_path):
        cache = CorrelationCache(tmp_path / "corr")
        assert cache._compute_matrix_id(["a", "b"]) == cache._compute_matrix_id(["b", "a"])
        assert cache._compute_matrix_id(["a", "b"], sample_size=10) != cache._compute_matrix_id(["a", "b"])

    def test_disk_persistence(self, tmp_path):
        cache_dir = tmp_path / "corr"
        ids = ["a", "b"]
        matrix = np.eye(2)
        cache1 = CorrelationCache(cache_dir)
        cache1.put(ids, matrix)
        cache2 = CorrelationCache(cache_dir)
        result, labels = cache2.get(ids)
        assert result is not None
        np.testing.assert_array_equal(result, matrix)
        assert labels == ["a", "b"]

    def test_corrupted_disk_file_is_miss(self, tmp_path):
        cache_dir = tmp_path / "corr"
        ids = ["a", "b"]
        cache1 = CorrelationCache(cache_dir)
        cache1.put(ids, np.eye(2))
        mid = cache1._compute_matrix_id(ids)
        (cache_dir / f"corr_{mid}.npz").write_bytes(b"garbage")
        cache2 = CorrelationCache(cache_dir)
        assert cache2.get(ids) is None
        assert cache2._stats["misses"] >= 1

    def test_clear_and_invalidate_all(self, tmp_path):
        cache = CorrelationCache(tmp_path / "corr")
        cache.put(["a"], np.array([[1.0]]))
        cache.put(["b"], np.array([[1.0]]))
        removed = cache.clear()
        assert removed == 2
        assert cache._matrices == {}
        cache.put(["c"], np.array([[1.0]]))
        cache.invalidate_all()
        assert cache._matrices == {}

    def test_hit_rate(self, tmp_path):
        cache = CorrelationCache(tmp_path / "corr")
        assert cache.hit_rate == 0.0
        cache.get(["a"])  # miss
        cache.put(["a"], np.array([[1.0]]))
        cache.get(["a"])  # hit
        assert cache.hit_rate == pytest.approx(0.5)


# ─── FactorOptimizer 基础 ──────────────────────────────────


class TestFactorOptimizerBasics:
    def test_init_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # 避免写入真实 memory/cache
        opt = FactorOptimizer()
        assert opt.parallel_threshold == fo.PARALLEL_THRESHOLD
        assert opt.max_workers >= 1
        assert opt._timings == {}

    def test_create_optimizer_helper(self, tmp_path):
        opt = create_optimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        assert isinstance(opt, FactorOptimizer)
        assert opt.factor_cache.cache_dir == tmp_path / "a"

    def test_timeit_accumulates(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        opt._timeit("op", lambda: 42)
        opt._timeit("op", lambda: 43)
        assert "op" in opt._timings
        assert opt._timings["op"] > 0

    def test_compute_data_version(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        panel = _make_panel()
        v1 = opt._compute_data_version(panel)
        v2 = opt._compute_data_version(panel)
        assert v1 == v2  # 稳定
        assert len(v1) == 12  # sha256 前 12 位
        # 数据变化 → 版本变化（data_version 基于日期/行数，追加行触发变化）
        panel2 = dict(panel)
        df = panel2["SYM0"].copy()
        df.loc[df.index[-1] + pd.Timedelta(days=1)] = df.iloc[-1]
        panel2["SYM0"] = df
        assert opt._compute_data_version(panel2) != v1

    def test_get_cache_stats_shape(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        stats = opt.get_cache_stats()
        assert "factor_cache" in stats and "correlation_cache" in stats and "timings" in stats
        assert stats["factor_cache"]["hits"] == 0

    def test_invalidate_factor_clears_corr_cache(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        key = FactorCacheKey("f1", "h1", "RB0", "v1")
        opt.factor_cache.put(key, np.array([1.0]))
        opt.corr_cache.put(["x"], np.array([[1.0]]))
        n = opt.invalidate_factor("f1")
        assert n == 1
        assert opt.corr_cache._matrices == {}


# ─── 信号矩阵计算 ──────────────────────────────────────────


class TestSignalMatrixCompute:
    def test_sequential_mode(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        panel = _make_panel(n_symbols=2, n=60)
        factors = [
            _make_factor("f1", "mom", code=FACTOR_CODE_OK),
            _make_factor("f2", "rev", code=FACTOR_CODE_OK),
        ]
        matrix = opt.compute_signal_matrix_parallel(panel, factors)
        assert set(matrix.keys()) == {"SYM0", "SYM1"}
        for sym, sigs in matrix.items():
            assert set(sigs.keys()) == {"mom", "rev"}
            assert len(sigs["mom"]) == 60

    def test_parallel_mode_uses_panel_ref(self, tmp_path, monkeypatch):
        opt = FactorOptimizer(
            factor_cache_dir=str(tmp_path / "a"),
            correlation_cache_dir=str(tmp_path / "b"),
            parallel_threshold=1,  # 强制并行
        )
        panel = _make_panel(n_symbols=2, n=60)
        monkeypatch.setattr(fo, "_optimize_panel_ref", panel)
        factors = [_make_factor("f1", "mom", code=FACTOR_CODE_OK)]
        matrix = opt.compute_signal_matrix_parallel(panel, factors)
        assert "SYM0" in matrix and "SYM1" in matrix
        assert matrix["SYM0"]["mom"].shape == (60,)

    def test_cache_hit_second_call(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        panel = _make_panel(n_symbols=1, n=60)
        factors = [_make_factor("f1", "mom", code=FACTOR_CODE_OK)]
        m1 = opt.compute_signal_matrix_parallel(panel, factors)
        hits_before = opt.factor_cache._stats["hits"]
        m2 = opt.compute_signal_matrix_parallel(panel, factors)
        assert opt.factor_cache._stats["hits"] > hits_before
        assert m1 == m2  # 缓存命中结果一致

    def test_force_recompute_skips_cache(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        panel = _make_panel(n_symbols=1, n=60)
        factors = [_make_factor("f1", "mom", code=FACTOR_CODE_OK)]
        opt.compute_signal_matrix_parallel(panel, factors)
        hits_before = opt.factor_cache._stats["hits"]
        opt.compute_signal_matrix_parallel(panel, factors, force_recompute=True)
        assert opt.factor_cache._stats["hits"] == hits_before  # 强制重算不命中缓存

    def test_short_data_skipped(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        panel = {"SYM0": _make_df(n=10)}  # 不足 20 行
        factors = [_make_factor("f1", "mom", code=FACTOR_CODE_OK)]
        matrix = opt.compute_signal_matrix_parallel(panel, factors)
        assert matrix == {}

    def test_failed_factor_skipped_gracefully(self, tmp_path):
        opt = FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))
        panel = _make_panel(n_symbols=1, n=60)
        factors = [_make_factor("f_bad", "bad", code=FACTOR_CODE_FAIL)]  # 非法 import → 编译失败
        matrix = opt.compute_signal_matrix_parallel(panel, factors)
        assert matrix == {}  # 失败因子被跳过，不抛异常


# ─── tiered_orthogonalize ──────────────────────────────────


class TestTieredOrthogonalize:
    def _make_opt(self, tmp_path) -> FactorOptimizer:
        return FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))

    def test_small_pool_skipped(self, tmp_path):
        opt = self._make_opt(tmp_path)
        factors = [_make_factor(f"f{i}", f"n{i}") for i in range(2)]
        result, summary = opt.tiered_orthogonalize(factors)
        assert result == factors  # 原样返回
        assert summary["input_count"] == 2
        assert summary["phase1_marked"] == 0

    def test_l2_prior_marks_high_corr(self, tmp_path):
        opt = self._make_opt(tmp_path)
        # 需 > max(2, family_threshold)=3 个因子才进入分层流程
        factors = [
            _make_factor("a", "f_a"), _make_factor("b", "f_b"),
            _make_factor("c", "f_c"), _make_factor("d", "f_d"),
        ]
        l2 = [
            {"factor_id_a": "a", "factor_id_b": "b", "pearson": 0.99, "spearman": 0.95},
            {"factor_id_a": "c", "factor_id_b": "x", "pearson": 0.1, "spearman": 0.1},
        ]
        result, summary = opt.tiered_orthogonalize(factors, l2_prior_correlations=l2)
        # a/b max_abs=0.99 >= 0.95 → 被标记；c/x max_abs=0.1 → 不标记
        flag_ids = {
            f["factor_id"]
            for f in result
            if any(fl.get("type") == "l2_seed_correlation" for fl in f.get("correlation_flags", []))
        }
        assert "a" in flag_ids and "b" in flag_ids
        assert "c" not in flag_ids
        assert summary["l2_prior_count"] == 2  # a 和 b 各一次

    def test_phase1_code_duplicate_marked(self, tmp_path):
        opt = self._make_opt(tmp_path)
        code = "def factor_program(data, params):\n    return data['close']"
        code2 = "def factor_program(data, params):\n    return data['close'] * 2"
        factors = [
            _make_factor("f1", "dup_a", code=code, sharpe=1.5),
            _make_factor("f2", "dup_b", code=code, sharpe=0.8),
            _make_factor("f3", "uniq", code=FACTOR_CODE_OK),
            _make_factor("f4", "uniq2", code=code2),  # 第 4 个因子避免小池子跳过
        ]
        result, summary = opt.tiered_orthogonalize(factors)
        dup_flags = [
            f for f in result
            if any(fl.get("type") == "code_duplicate" for fl in f.get("correlation_flags", []))
        ]
        assert len(dup_flags) == 1
        assert dup_flags[0]["factor_id"] == "f2"  # Sharpe 低者被标记
        assert summary["phase1_marked"] == 1

    def test_phase1_remove_mode_excludes_duplicate(self, tmp_path):
        opt = self._make_opt(tmp_path)
        code = "def factor_program(data, params):\n    return data['close']"
        code2 = "def factor_program(data, params):\n    return data['close'] * 2"
        factors = [
            _make_factor("f1", "dup_a", code=code, sharpe=1.5),
            _make_factor("f2", "dup_b", code=code, sharpe=0.8),
            _make_factor("f3", "uniq", code=FACTOR_CODE_OK),
            _make_factor("f4", "uniq2", code=code2),
        ]
        result, summary = opt.tiered_orthogonalize(factors, mode="remove")
        excluded = [f for f in result if f.get("exclude_from_portfolio")]
        assert [f["factor_id"] for f in excluded] == ["f2"]

    def test_phase1_family_prune_over_10(self, tmp_path):
        opt = self._make_opt(tmp_path)
        # 11 个同家族（name 含 mkt_trend）因子
        factors = [_make_factor(f"f{i}", f"mkt_trend_{i}", sharpe=float(i)) for i in range(11)]
        factors.append(_make_factor("g1", "other_factor"))
        result, summary = opt.tiered_orthogonalize(factors)
        family_flags = [
            f for f in result
            if any(fl.get("type") == "family_pruned" for fl in f.get("correlation_flags", []))
        ]
        assert len(family_flags) == 1  # 11 - 10 = 1 个被家族裁剪标记
        assert summary["phase1_marked"] >= 1  # 含家族标记（另有同 code 产生的重复标记）

    def test_phase2_marks_lower_sharpe_of_high_corr(self, tmp_path):
        opt = self._make_opt(tmp_path)
        rng = np.random.default_rng(7)
        base = rng.normal(size=100)
        noise = rng.normal(scale=0.05, size=100)
        # Phase 2 触发需 > max(2, parallel_threshold//2)=15 个因子
        factors = [_make_factor(f"f{i}", f"sig_{i}", sharpe=1.0) for i in range(16)]
        factors[0]["sharpe"] = 2.0
        factors[1]["sharpe"] = 1.0
        signal_matrix = {
            "SYM0": {
                "sig_0": base,
                "sig_1": base + noise,  # 与 sig_0 高相关
            }
        }
        for i in range(2, 16):
            signal_matrix["SYM0"][f"sig_{i}"] = rng.normal(size=100)
        result, summary = opt.tiered_orthogonalize(factors, signal_matrix=signal_matrix)
        marked = [
            f["factor_id"] for f in result
            if any(fl.get("type") == "high_correlation" for fl in f.get("correlation_flags", []))
        ]
        assert "f1" in marked  # Sharpe 更低的一方被标记
        assert "f0" not in marked
        assert summary["phase2_marked"] >= 1

    def test_phase2_skipped_when_few_factors(self, tmp_path):
        opt = self._make_opt(tmp_path, ) if False else self._make_opt(tmp_path)
        # parallel_threshold 默认 30 → threshold//2=15，4 个因子跳过 Phase 2
        factors = [_make_factor(f"f{i}", f"n{i}", sharpe=1.0) for i in range(4)]
        _, summary = opt.tiered_orthogonalize(factors)
        assert summary["phase2_marked"] == 0

    def test_classify_family(self, tmp_path):
        opt = self._make_opt(tmp_path)
        assert opt._classify_family({"name": "hf_price"}) == "hf_microstructure"
        assert opt._classify_family({"name": "mkt_trend_x"}) == "trend"
        assert opt._classify_family({"name": "basis_ratio"}) == "value_carry"
        assert opt._classify_family({"name": "macro_gdp"}) == "macro"
        assert opt._classify_family({"name": "upside_skewness"}) == "volatility"
        assert opt._classify_family({"name": "random_name"}) == "other"

    def test_get_or_compute_correlation_prefers_signal_matrix(self, tmp_path):
        opt = self._make_opt(tmp_path)
        rng = np.random.default_rng(3)
        factors = [_make_factor("f1", "sig_a"), _make_factor("f2", "sig_b")]
        sm = {"SYM0": {"sig_a": rng.normal(size=50), "sig_b": rng.normal(size=50)}}
        result = opt._get_or_compute_correlation(factors, signal_matrix=sm)
        assert result is not None
        matrix, labels = result
        assert matrix.shape == (2, 2)
        assert labels == ["sig_a", "sig_b"]

    def test_get_or_compute_correlation_falls_back_to_cache(self, tmp_path):
        opt = self._make_opt(tmp_path)
        factors = [_make_factor("f1", "n1"), _make_factor("f2", "n2")]
        ids = sorted(f["factor_id"] for f in factors)
        fake_matrix = np.eye(2)
        opt.corr_cache.put(ids, fake_matrix)
        result = opt._get_or_compute_correlation(factors)
        assert result is not None
        np.testing.assert_array_equal(result[0], fake_matrix)

    def test_get_or_compute_correlation_none_without_signal(self, tmp_path):
        opt = self._make_opt(tmp_path)
        factors = [_make_factor("f1", "n1")]
        assert opt._get_or_compute_correlation(factors) is None  # 无 signal_matrix 且缓存未命中


# ─── compute_correlation_from_matrix ───────────────────────


class TestComputeCorrelationFromMatrix:
    def _make_opt(self, tmp_path) -> FactorOptimizer:
        return FactorOptimizer(factor_cache_dir=str(tmp_path / "a"), correlation_cache_dir=str(tmp_path / "b"))

    def test_full_mode(self, tmp_path):
        opt = self._make_opt(tmp_path)
        rng = np.random.default_rng(5)
        sm = {
            "SYM0": {"a": rng.normal(size=40), "b": rng.normal(size=40)},
            "SYM1": {"a": rng.normal(size=40), "b": rng.normal(size=40)},
        }
        matrix, labels = opt.compute_correlation_from_matrix(sm, ["a", "b"])
        assert matrix.shape == (2, 2)
        assert labels == ["a", "b"]
        np.testing.assert_allclose(np.diag(matrix), np.ones(2), atol=1e-9)
        # 结果被缓存
        assert opt.corr_cache._matrices != {}

    def test_empty_signals_returns_zero_matrix(self, tmp_path):
        opt = self._make_opt(tmp_path)
        sm = {"SYM0": {}}
        matrix, labels = opt.compute_correlation_from_matrix(sm, ["a", "b"])
        assert matrix.shape == (2, 2)
        np.testing.assert_array_equal(matrix, np.zeros((2, 2)))

    def test_sampling_mode_over_100_factors(self, tmp_path):
        opt = self._make_opt(tmp_path)
        rng = np.random.default_rng(11)
        factor_names = [f"f{i:03d}" for i in range(101)]
        # 1001 个品种，每个品种给 3 个因子信号
        sm = {}
        for s in range(1001):
            sym = f"S{s}"
            sm[sym] = {f"f{s % 101:03d}": rng.normal(size=20), f"f{(s + 1) % 101:03d}": rng.normal(size=20)}
        matrix, labels = opt.compute_correlation_from_matrix(sm, factor_names)
        assert matrix.shape == (101, 101)
        assert labels == factor_names


# ─── 全局 Panel 引用 ───────────────────────────────────────


class TestPanelRef:
    def test_set_panel_ref_updates_global(self, monkeypatch):
        panel = _make_panel()
        set_panel_ref(panel)
        assert fo._optimize_panel_ref is panel
        set_panel_ref(None)
        assert fo._optimize_panel_ref is None
