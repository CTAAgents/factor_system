"""
fts/factor_engine/signal_cache.py — 因子信号缓存（质检链全链路信号复用）。

HARNESS: L2 质检准入链中，同一候选因子在三级评估 / 极值扰动 / 消融 /
鲁棒性 / SHAP 等环节对「同一数据」重复执行因子代码。本模块按
(factor_id, params, 数据指纹) 缓存信号数组，命中后直接复用，
避免重复沙箱执行，显著降低 L2 单候选质检耗时。

正确性约束:
    - 数据指纹覆盖全部列值（消融会扰动 volume/vwap/各特征列），
      任何列值变化都会导致指纹变化 → 缓存 miss → 正确重算。
    - 因子代码视为确定性纯函数（沙箱内无副作用），同一
      (factor, params, data) 的信号结果可安全复用。
    - 并发安全（RLock），供 batch 并行粗筛与主循环共享。

用法:
    cache = SignalCache(max_entries=16)
    executor = FactorExecutor(factor, signal_cache=cache)
    signal = executor.execute(data, params)   # 命中缓存时不再执行沙箱

版本: v1.0.0（GAP-070）
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class SignalCache:
    """LRU 信号缓存（线程安全）。

    Args:
        max_entries: 缓存条目上限（超出后淘汰最久未使用项）。
    """

    def __init__(self, max_entries: int = 16) -> None:
        self._max_entries = max(1, int(max_entries))
        self._cache: "OrderedDict[tuple[str, str, str], np.ndarray]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ─── 查询/写入 ───────────────────────────────────────

    def get(self, factor: dict[str, Any], data: pd.DataFrame) -> Optional[np.ndarray]:
        """按 (factor_id, params, 数据指纹) 查询缓存。

        Args:
            factor: 因子程序（含 factor_id / params）
            data: OHLCV 数据

        Returns:
            命中返回信号数组；未命中返回 None
        """
        key = self._make_key(factor, data)
        if key is None:
            return None
        with self._lock:
            signal = self._cache.get(key)
            if signal is None:
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return signal

    def put(self, factor: dict[str, Any], data: pd.DataFrame, signal: np.ndarray) -> None:
        """写入缓存（复制信号，防下游 in-place 修改污染缓存）。"""
        key = self._make_key(factor, data)
        if key is None:
            return
        with self._lock:
            self._cache[key] = np.array(signal, dtype=float, copy=True)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
                self._evictions += 1
                # plans/51 C3：淘汰可观测（debug 级，高频场景不噪）
                logger.debug(
                    "SignalCache LRU 淘汰（累计 %d 条, max_entries=%d）",
                    self._evictions,
                    self._max_entries,
                )

    def clear(self) -> None:
        """清空缓存（命中/未命中计数保留）。"""
        with self._lock:
            self._cache.clear()

    # ─── 统计 ────────────────────────────────────────────

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def stats(self) -> dict[str, int]:
        """缓存命中统计（供观测/测试）。"""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "entries": len(self._cache),
                "evictions": self._evictions,  # plans/51 C3
            }

    # ─── 内部 ────────────────────────────────────────────

    def _make_key(
        self,
        factor: dict[str, Any],
        data: pd.DataFrame,
    ) -> Optional[tuple[str, str, str]]:
        """构造缓存 key；因子缺 factor_id 或数据为空时返回 None（不缓存）。"""
        factor_id = factor.get("factor_id")
        if not factor_id:
            return None
        if data is None or len(data) == 0:
            return None
        params = factor.get("params") or {}
        try:
            params_json = json.dumps(params, sort_keys=True, default=str)
        except (TypeError, ValueError):  # 参数不可序列化 → 不缓存
            return None
        return (str(factor_id), params_json, self._fingerprint(data))

    @staticmethod
    def _fingerprint(data: pd.DataFrame) -> str:
        """数据指纹 — 覆盖行数、索引范围与全部列值（含扰动）。

        消融/鲁棒性会复制数据并置零/替换列值，指纹必须能区分这些扰动，
        否则会错误命中缓存。列按名排序保证列顺序差异不影响指纹。
        """
        h = hashlib.blake2b(digest_size=16)
        h.update(str(len(data)).encode("utf-8"))
        idx = data.index
        try:
            h.update(str(idx[0]).encode("utf-8"))
            h.update(str(idx[-1]).encode("utf-8"))
        except (KeyError, IndexError):  # 空索引（len>0 防御）
            pass
        for col in sorted(str(c) for c in data.columns):
            h.update(col.encode("utf-8"))
            try:
                arr = data[col].to_numpy(dtype=np.float64)
                h.update(arr.tobytes())
            except (TypeError, ValueError):  # 非数值列（如日期 object）降级 str 摘要
                h.update(str(data[col].tolist()[:256]).encode("utf-8"))
        return h.hexdigest()


__all__ = ["SignalCache"]
