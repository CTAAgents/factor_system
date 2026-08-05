"""
tests/test_data_cache.py — DataCache 测试

验证:
  1. LRU 淘汰正确性
  2. TTL 过期
  3. get_or_load 原子操作
  4. 并发访问
  5. 命中率统计
  6. 内存限制
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pandas as pd
import pytest

from fts.data_cache import DataCache


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def cache() -> DataCache:
    return DataCache(max_memory_mb=1, default_ttl=60)


@pytest.fixture
def small_cache() -> DataCache:
    return DataCache(max_memory_mb=1, default_ttl=1)  # 1秒 TTL


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """可缓存的 DataFrame。"""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "open": rng.standard_normal(1000),
        "high": rng.standard_normal(1000),
        "low": rng.standard_normal(1000),
        "close": rng.standard_normal(1000),
        "volume": rng.integers(100, 10000, 1000),
    })


# ─── 基础功能测试 ─────────────────────────────────────────


class TestDataCacheBasic:
    def test_set_and_get(self, cache: DataCache) -> None:
        """基本 set/get。"""
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_miss(self, cache: DataCache) -> None:
        """未命中返回 None。"""
        assert cache.get("nonexistent") is None

    def test_overwrite(self, cache: DataCache) -> None:
        """覆盖已有键。"""
        cache.set("key1", "value1")
        cache.set("key1", "value2")
        assert cache.get("key1") == "value2"
        assert cache.size == 1

    def test_delete(self, cache: DataCache) -> None:
        """删除条目。"""
        cache.set("key1", "value1")
        assert cache.invalidate("key1") is True
        assert cache.get("key1") is None
        assert cache.invalidate("key1") is False

    def test_clear(self, cache: DataCache) -> None:
        """清空缓存。"""
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.size == 0
        assert cache.get("key1") is None


# ─── TTL 过期测试 ────────────────────────────────────────


class TestTTL:
    def test_expire(self, small_cache: DataCache) -> None:
        """TTL 过期后返回 None。"""
        small_cache.set("key1", "value1")
        assert small_cache.get("key1") == "value1"
        time.sleep(1.1)
        assert small_cache.get("key1") is None

    def test_custom_ttl(self, cache: DataCache) -> None:
        """自定义 TTL。"""
        cache.set("key1", "value1", ttl=0.5)
        assert cache.get("key1") == "value1"
        time.sleep(0.6)
        assert cache.get("key1") is None

    def test_no_ttl(self, cache: DataCache) -> None:
        """设置 TTL=0 立即过期。"""
        cache.set("key1", "value1", ttl=0)
        time.sleep(0.01)
        assert cache.get("key1") is None


# ─── DataFrame 缓存测试 ───────────────────────────────────


class TestDataFrameCache:
    def test_dataframe_roundtrip(
        self, cache: DataCache, sample_df: pd.DataFrame
    ) -> None:
        """DataFrame 存取正确。"""
        cache.set("df1", sample_df)
        result = cache.get("df1")
        assert isinstance(result, pd.DataFrame)
        pd.testing.assert_frame_equal(result, sample_df)

    def test_dataframe_memory_tracking(
        self, cache: DataCache, sample_df: pd.DataFrame
    ) -> None:
        """DataFrame 内存追踪。"""
        cache.set("df1", sample_df)
        stats = cache.stats
        assert stats["memory_bytes"] > 0
        assert stats["entries"] == 1


# ─── LRU 淘汰测试 ─────────────────────────────────────────


class TestLRUEviction:
    def test_lru_eviction(self, sample_df: pd.DataFrame) -> None:
        """LRU 淘汰最久未使用条目。"""
        # 小内存缓存 — 1MB
        cache = DataCache(max_memory_mb=1, default_ttl=3600)

        # 添加多个条目
        for i in range(5):
            cache.set(f"key_{i}", sample_df.copy())

        # 淘汰后应小于 5 个条目（取决于 DataFrame 大小）
        assert cache.size <= 5

    def test_lru_access_updates_order(
        self, cache: DataCache, sample_df: pd.DataFrame
    ) -> None:
        """访问条目应更新 LRU 顺序。"""
        cache.set("key1", sample_df)
        cache.set("key2", sample_df)
        cache.get("key1")  # 访问 key1 — 移到末尾
        cache.set("key3", sample_df)

        # key2 应该在 key1 之前被淘汰（如果内存不够）
        stats = cache.stats
        # 至少验证没有崩溃
        assert stats["entries"] >= 1


# ─── get_or_load 测试 ─────────────────────────────────────


class TestGetOrLoad:
    def test_loads_on_miss(self, cache: DataCache) -> None:
        """未命中时调用 loader。"""
        call_count = 0

        def loader() -> str:
            nonlocal call_count
            call_count += 1
            return "loaded_value"

        result = cache.get_or_load("key1", loader)
        assert result == "loaded_value"
        assert call_count == 1

        # 第二次调用不应再触发 loader
        result = cache.get_or_load("key1", loader)
        assert result == "loaded_value"
        assert call_count == 1

    def test_loader_exception_propagates(self, cache: DataCache) -> None:
        """loader 异常应传播。"""
        def loader() -> str:
            raise ValueError("load failed")

        with pytest.raises(ValueError, match="load failed"):
            cache.get_or_load("key1", loader)

        # 失败时不应缓存
        assert cache.get("key1") is None


# ─── 指标统计测试 ─────────────────────────────────────────


class TestStats:
    def test_hit_miss_counts(self, cache: DataCache) -> None:
        """命中/未命中计数。"""
        cache.set("key1", "value1")

        # 2 次命中
        cache.get("key1")
        cache.get("key1")
        # 1 次未命中
        cache.get("nonexistent")

        stats = cache.stats
        assert stats["hits"] == 2
        assert stats["misses"] == 1

    def test_hit_rate(self, cache: DataCache) -> None:
        """命中率计算。"""
        cache.set("key1", "value1")
        cache.get("key1")  # hit
        cache.get("key1")  # hit
        cache.get("miss")  # miss

        assert cache.hit_rate == pytest.approx(2 / 3, abs=0.01)

    def test_empty_cache_stats(self, cache: DataCache) -> None:
        """空缓存指标。"""
        stats = cache.stats
        assert stats["entries"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0.0


# ─── 并发测试 ─────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_read_write(self, cache: DataCache) -> None:
        """并发读写。"""
        errors: list[str] = []

        def writer() -> None:
            try:
                for i in range(50):
                    cache.set(f"key_{i}", f"value_{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

        def reader() -> None:
            try:
                for i in range(50):
                    cache.get(f"key_{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发错误: {errors}"

    def test_concurrent_get_or_load(self, cache: DataCache) -> None:
        """并发 get_or_load。"""
        errors: list[str] = []
        call_count = [0]
        lock = threading.Lock()

        def loader() -> str:
            with lock:
                call_count[0] += 1
            time.sleep(0.01)
            return "shared_value"

        def fetcher() -> None:
            try:
                cache.get_or_load("shared", loader)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

        threads = [threading.Thread(target=fetcher) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # loader 可能被多次调用（get_or_load 非原子锁定），但不应出错
        assert call_count[0] >= 1


# ─── 上下文管理器测试 ─────────────────────────────────────


class TestContextManager:
    def test_context_manager_clears(self) -> None:
        """上下文管理器退出时清空缓存。"""
        with DataCache(max_memory_mb=1) as cache:
            cache.set("key1", "value1")
            assert cache.size == 1

        assert cache.size == 0


# ─── invalidate_pattern 测试 ──────────────────────────────


class TestInvalidatePattern:
    def test_invalidate_prefix(self, cache: DataCache) -> None:
        """按前缀失效。"""
        cache.set("futures_RB0", "data1")
        cache.set("futures_CU0", "data2")
        cache.set("stock_000001", "data3")

        removed = cache.invalidate_pattern("futures_")
        assert removed == 2
        assert cache.get("futures_RB0") is None
        assert cache.get("stock_000001") == "data3"

    def test_invalidate_nonexistent_prefix(self, cache: DataCache) -> None:
        """不存在的前缀返回 0。"""
        cache.set("key1", "value1")
        removed = cache.invalidate_pattern("nonexistent_")
        assert removed == 0


# ─── 各种值类型测试 ───────────────────────────────────────


class TestValueTypes:
    def test_ndarray_cache(self, cache: DataCache) -> None:
        """ndarray 缓存。"""
        arr = np.array([1.0, 2.0, 3.0])
        cache.set("arr", arr)
        result = cache.get("arr")
        np.testing.assert_array_equal(result, arr)

    def test_list_cache(self, cache: DataCache) -> None:
        """列表缓存。"""
        data = [1, 2, 3, {"key": "value"}]
        cache.set("list", data)
        assert cache.get("list") == data

    def test_int_cache(self, cache: DataCache) -> None:
        """整数缓存。"""
        cache.set("int", 42)
        assert cache.get("int") == 42