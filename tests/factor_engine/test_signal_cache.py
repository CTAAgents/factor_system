"""tests/factor_engine/test_signal_cache.py — SignalCache 信号缓存测试。

覆盖范围（GAP-070）:
    1. 同 factor + 同数据命中，返回相同信号
    2. 扰动数据（列值变化）不命中，正确重算
    3. 不同 factor_id / 不同 params 不命中
    4. LRU 容量淘汰 + clear 清空
    5. 缓存数组 copy 语义（下游修改不影响缓存）
    6. 边界：空数据 / 缺 factor_id 不缓存
    7. FactorExecutor 接入缓存（命中跳过沙箱执行）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.factor_program import FactorExecutor
from fts.factor_engine.signal_cache import SignalCache


def _make_factor(factor_id: str = "fct_a", params: dict | None = None) -> dict:
    """构造最小 FactorProgram 字典。"""
    return {
        "factor_id": factor_id,
        "name": f"factor_{factor_id}",
        "params": params or {},
        "code": (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = np.asarray(data['close'], dtype=float)\n"
            "    n = len(close)\n"
            "    ret = np.zeros(n)\n"
            "    ret[1:] = close[1:] - close[:-1]\n"
            "    return ret"
        ),
    }


def _make_data(n: int = 100, seed: int = 1) -> pd.DataFrame:
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame(
        {
            "open": close + 0.1,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": np.random.randint(1000, 10000, n).astype(float),
        },
        index=dates,
    )


# ═══ 1. 命中/未命中语义 ═══


class TestHitMiss:
    def test_same_factor_same_data_hits(self):
        cache = SignalCache()
        factor = _make_factor()
        data = _make_data()
        sig1 = np.array([1.0, 2.0, 3.0])
        cache.put(factor, data, sig1)
        got = cache.get(factor, data)
        assert got is not None
        np.testing.assert_allclose(got, sig1)
        assert cache.stats()["hits"] == 1
        assert cache.stats()["misses"] == 0

    def test_perturbed_data_misses(self):
        cache = SignalCache()
        factor = _make_factor()
        data = _make_data()
        cache.put(factor, data, np.array([1.0, 2.0, 3.0]))
        # 扰动 volume（模拟消融 volume_zero）→ 指纹变化 → miss
        perturbed = data.copy()
        perturbed["volume"] = 0.0
        assert cache.get(factor, perturbed) is None
        assert cache.stats()["misses"] == 1

    def test_zero_one_feature_perturbation_misses(self):
        """单特征归零（消融 zero_one_feature）同样触发 miss。"""
        cache = SignalCache()
        factor = _make_factor()
        data = _make_data()
        cache.put(factor, data, np.array([1.0]))
        for col in ["open", "high", "low", "volume"]:
            perturbed = data.copy()
            perturbed[col] = 0.0
            assert cache.get(factor, perturbed) is None, f"列 {col} 扰动未触发 miss"

    def test_different_factor_id_misses(self):
        cache = SignalCache()
        data = _make_data()
        cache.put(_make_factor("fct_a"), data, np.array([1.0]))
        assert cache.get(_make_factor("fct_b"), data) is None

    def test_different_params_misses(self):
        cache = SignalCache()
        data = _make_data()
        cache.put(_make_factor(params={"a": 1}), data, np.array([1.0]))
        assert cache.get(_make_factor(params={"a": 2}), data) is None


# ═══ 2. LRU / clear ═══


class TestLruAndClear:
    def test_max_entries_evicts_lru(self):
        cache = SignalCache(max_entries=2)
        data = _make_data()
        for i in range(3):
            cache.put(_make_factor(f"fct_{i}"), data, np.array([float(i)]))
        # 第 3 个写入后，第 1 个（最久未使用）被淘汰
        assert cache.get(_make_factor("fct_0"), data) is None
        assert cache.get(_make_factor("fct_2"), data) is not None
        assert cache.stats()["entries"] == 2

    def test_clear_removes_all(self):
        cache = SignalCache(max_entries=2)
        data = _make_data()
        cache.put(_make_factor("fct_a"), data, np.array([1.0]))
        cache.clear()
        assert cache.get(_make_factor("fct_a"), data) is None
        assert cache.stats()["entries"] == 0

    def test_put_copies_signal(self):
        """缓存存副本，下游 in-place 修改不污染缓存。"""
        cache = SignalCache()
        factor = _make_factor()
        data = _make_data()
        sig = np.array([1.0, 2.0, 3.0])
        cache.put(factor, data, sig)
        sig[0] = 999.0  # 修改原始数组
        got = cache.get(factor, data)
        assert got is not None
        assert got[0] == 1.0


# ═══ 3. 边界 ═══


class TestEdgeCases:
    def test_empty_data_not_cached(self):
        cache = SignalCache()
        factor = _make_factor()
        empty = pd.DataFrame()
        cache.put(factor, empty, np.array([]))
        assert cache.get(factor, empty) is None

    def test_missing_factor_id_not_cached(self):
        cache = SignalCache()
        factor = _make_factor()
        factor.pop("factor_id")
        data = _make_data()
        cache.put(factor, data, np.array([1.0]))
        assert cache.get(factor, data) is None

    def test_column_order_insensitive(self):
        """列顺序不同但值相同 → 指纹一致 → 命中（因子按列名取数）。"""
        cache = SignalCache()
        factor = _make_factor()
        data = _make_data()
        sig = np.array([1.0, 2.0, 3.0])
        cache.put(factor, data, sig)
        reordered = data[list(reversed(data.columns))]
        got = cache.get(factor, reordered)
        assert got is not None


# ═══ 4. FactorExecutor 集成 ═══


class TestFactorExecutorIntegration:
    def test_executor_hits_cache_skips_execution(self):
        cache = SignalCache()
        factor = _make_factor()
        data = _make_data(50)
        exe = FactorExecutor(factor, signal_cache=cache)
        sig1 = exe.execute(data, factor.get("params", {}))
        # 第二次执行走缓存（相同 factor + data + params）
        sig2 = exe.execute(data, factor.get("params", {}))
        np.testing.assert_allclose(sig1, sig2)
        assert cache.stats()["misses"] == 1  # 首次 miss，执行后写入
        assert cache.stats()["hits"] == 1  # 第二次命中

    def test_executor_perturbed_data_not_hit(self):
        cache = SignalCache()
        factor = _make_factor()
        data = _make_data(50)
        exe = FactorExecutor(factor, signal_cache=cache)
        _ = exe.execute(data, factor.get("params", {}))
        perturbed = data.copy()
        perturbed["volume"] = 0.0
        sig_p = exe.execute(perturbed, factor.get("params", {}))
        assert np.any(sig_p != 0)  # 正常重算，未被旧缓存污染
        assert cache.stats()["hits"] == 0

    def test_executor_no_cache_default_behavior(self):
        """不传 signal_cache 时行为不变（向后兼容）。"""
        factor = _make_factor()
        data = _make_data(50)
        exe = FactorExecutor(factor)
        sig = exe.execute(data, factor.get("params", {}))
        assert len(sig) == len(data)
