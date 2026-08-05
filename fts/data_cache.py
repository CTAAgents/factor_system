"""
fts/data_cache — FTS 数据缓存层

提供 LRU + TTL 内存缓存，避免重复的数据加载和因子编译。

用法:
    cache = DataCache(max_memory_mb=512)
    df = cache.get_or_load("RB0", lambda: provider.get_ohlcv("RB0"))

    # 作为上下文管理器
    with DataCache() as cache:
        ...

版本: v1.0.0
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class DataCache:
    """FTS 数据缓存 — LRU + TTL 内存缓存。

    特性:
      - LRU 淘汰: 内存超限时自动淘汰最久未使用条目
      - TTL 过期: 条目超过生存时间自动失效
      - 线程安全: 支持多线程并发访问
      - 指标追踪: 命中/未命中统计

    Args:
        max_memory_mb: 最大内存 (MB)，默认 512
        default_ttl: 默认生存时间 (秒)，默认 3600
    """

    def __init__(self, max_memory_mb: int = 512, default_ttl: float = 3600):
        self._max_memory_bytes = max_memory_mb * 1024 * 1024
        self._default_ttl = default_ttl
        self._cache: OrderedDict[str, tuple[Any, float, int]] = OrderedDict()
        self._lock = threading.Lock()
        self._current_memory = 0
        # 指标
        self._hits = 0
        self._misses = 0

    # ─── 核心 API ──────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值。

        Args:
            key: 缓存键

        Returns:
            缓存值或 None（未命中/已过期）
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None

            value, expire_at, _ = entry
            if expire_at < time.time():
                # 过期
                self._remove_entry(key)
                self._misses += 1
                return None

            # 命中 — 移到末尾（LRU）
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """设置缓存值。

        Args:
            key: 缓存键
            value: 缓存值（支持 DataFrame/ndarray/任何 pickle 对象）
            ttl: 生存时间 (秒)，None 使用默认值
        """
        expire_at = time.time() + (ttl if ttl is not None else self._default_ttl)
        size = self._estimate_size(value)

        with self._lock:
            # 先移除旧条目（如果存在）
            if key in self._cache:
                self._remove_entry(key)

            # 检查内存限制
            while self._current_memory + size > self._max_memory_bytes and self._cache:
                # 淘汰最旧条目
                oldest_key = next(iter(self._cache))
                self._remove_entry(oldest_key)

            # 如果单个条目就超限，跳过
            if size > self._max_memory_bytes:
                logger.warning("缓存条目 %s 超限 (%.1f MB > %.1f MB)，跳过",
                               key, size / 1024 / 1024, self._max_memory_bytes / 1024 / 1024)
                return

            self._cache[key] = (value, expire_at, size)
            self._current_memory += size

    def get_or_load(
        self, key: str, loader: Callable[[], Any], ttl: Optional[float] = None
    ) -> Any:
        """获取或加载缓存值（原子操作）。

        Args:
            key: 缓存键
            loader: 加载函数（仅在缓存未命中时调用）
            ttl: 生存时间

        Returns:
            缓存值
        """
        cached = self.get(key)
        if cached is not None:
            return cached

        # 未命中 — 调用 loader
        value = loader()
        self.set(key, value, ttl)
        return value

    def invalidate(self, key: str) -> bool:
        """失效指定键。

        Returns:
            是否成功移除
        """
        with self._lock:
            if key in self._cache:
                self._remove_entry(key)
                return True
            return False

    def invalidate_pattern(self, pattern: str) -> int:
        """失效匹配前缀的所有键。

        Args:
            pattern: 键前缀

        Returns:
            移除的条目数
        """
        removed = 0
        with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(pattern)]
            for key in keys_to_remove:
                self._remove_entry(key)
                removed += 1
        return removed

    def clear(self) -> None:
        """清空所有缓存。"""
        with self._lock:
            self._cache.clear()
            self._current_memory = 0
            self._hits = 0
            self._misses = 0

    # ─── 指标 ──────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """缓存统计。"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / max(total, 1)
            return {
                "entries": len(self._cache),
                "memory_bytes": self._current_memory,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": hit_rate,
            }

    @property
    def hit_rate(self) -> float:
        """命中率。"""
        return self.stats["hit_rate"]

    @property
    def size(self) -> int:
        """当前条目数。"""
        with self._lock:
            return len(self._cache)

    # ─── 上下文管理 ────────────────────────────────────────

    def __enter__(self) -> DataCache:
        return self

    def __exit__(self, *args: Any) -> None:
        self.clear()

    # ─── 内部方法 ──────────────────────────────────────────

    def _remove_entry(self, key: str) -> None:
        """移除条目（调用方须持有锁）。"""
        if key in self._cache:
            _, _, size = self._cache.pop(key)
            self._current_memory -= size

    @staticmethod
    def _estimate_size(value: Any) -> int:
        """估算对象内存占用。"""
        if isinstance(value, pd.DataFrame):
            return int(value.memory_usage(deep=True).sum())
        elif hasattr(value, 'nbytes'):
            return int(value.nbytes)
        elif isinstance(value, (list, dict, str)):
            return len(str(value)) * 2  # 粗略估算
        else:
            return 1024  # 默认 1KB


__all__ = ["DataCache"]