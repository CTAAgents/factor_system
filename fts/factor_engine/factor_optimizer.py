"""
fts/factor_engine/factor_optimizer.py — 可扩展因子优化框架

分层优化架构 (Factor-Optimization Tiers):
    Tier 1: 因子级并行化 — ThreadPoolExecutor 并行计算因子信号
    Tier 2: 两阶段正交化 — 廉价预筛 → 昂贵统计正交化
    Tier 3: 增量式缓存 — 因子信号/相关矩阵按版本哈希缓存
    Tier 4: 采样式估计 — 大因子池用样本估计代替全量计算

设计原则:
    - 对小因子池 (<100) 保持原有精确计算
    - 对大因子池 (100+) 自动切换到分层优化
    - 缓存命中时跳过所有计算

版本: v1.0.1
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 常量 ──────────────────────────────────────────────────

# 并行化阈值: 因子数超过此值启用并行
PARALLEL_THRESHOLD = 30

# 缓存版本号（缓存结构变更时 bump 以自动失效）
CACHE_VERSION = 1

# 因子家族分类（用于 Tier 2 预筛）
FACTOR_FAMILIES: dict[str, list[str]] = {
    "hf_microstructure": ["hf_", "option_", "bid_ask", "trade_imbalance"],
    "trend": ["mkt_trend", "basis_momentum", "gp_alpha"],
    "value_carry": ["basis", "mkt_concentration", "crowd_bias"],
    "macro": ["macro_", "mobile_big_data", "bias"],
    "volatility": ["upside_skewness", "ht_alpha", "historical_return"],
}


# ─── 数据类 ────────────────────────────────────────────────

@dataclass
class FactorCacheKey:
    """因子信号缓存键。"""
    factor_id: str
    factor_code_hash: str
    symbol: str
    data_version: str  # 数据版本哈希（日期范围 + 数据源）

    def to_dict(self) -> dict[str, str]:
        return {
            "factor_id": self.factor_id,
            "factor_code_hash": self.factor_code_hash,
            "symbol": self.symbol,
            "data_version": self.data_version,
        }

    @property
    def cache_id(self) -> str:
        raw = f"{self.factor_id}:{self.factor_code_hash}:{self.symbol}:{self.data_version}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class FactorSignalCache:
    """因子信号缓存（磁盘持久化）。"""
    cache_dir: Path
    _signals: dict[str, np.ndarray] = field(default_factory=dict)
    _meta: dict[str, Any] = field(default_factory=dict)
    _stats: dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0})

    def __post_init__(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    def _load_index(self) -> None:
        index_fp = self.cache_dir / "signal_index.json"
        if index_fp.exists():
            try:
                self._meta = json.loads(index_fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, TypeError):
                self._meta = {}

    def _save_index(self) -> None:
        index_fp = self.cache_dir / "signal_index.json"
        index_fp.write_text(json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, key: FactorCacheKey) -> Optional[np.ndarray]:
        """获取缓存信号。"""
        if key.cache_id in self._signals:
            self._stats["hits"] += 1
            return self._signals[key.cache_id]

        # 尝试从磁盘加载
        fp = self.cache_dir / f"{key.cache_id}.npy"
        if fp.exists():
            try:
                arr = np.load(fp)
                self._signals[key.cache_id] = arr
                self._stats["hits"] += 1
                return arr
            except (IOError, ValueError):
                pass

        self._stats["misses"] += 1
        return None

    def put(self, key: FactorCacheKey, signal: np.ndarray) -> None:
        """存入缓存。"""
        self._signals[key.cache_id] = signal
        fp = self.cache_dir / f"{key.cache_id}.npy"
        np.save(fp, signal)
        self._meta[key.cache_id] = key.to_dict()
        self._save_index()

    def clear(self) -> int:
        """清空所有缓存，返回删除的文件数。"""
        removed = 0
        for fp in self.cache_dir.glob("*.npy"):
            try:
                fp.unlink()
                removed += 1
            except OSError:
                pass
        self._signals.clear()
        self._meta.clear()
        self._save_index()
        return removed

    def invalidate_factor(self, factor_id: str) -> int:
        """失效指定因子的所有缓存。"""
        removed = 0
        to_remove = []
        for cid, meta in self._meta.items():
            if meta.get("factor_id") == factor_id:
                to_remove.append(cid)

        for cid in to_remove:
            self._meta.pop(cid, None)
            self._signals.pop(cid, None)
            fp = self.cache_dir / f"{cid}.npy"
            if fp.exists():
                fp.unlink()
            removed += 1

        self._save_index()
        return removed

    @property
    def hit_rate(self) -> float:
        total = self._stats["hits"] + self._stats["misses"]
        return self._stats["hits"] / total if total > 0 else 0.0


@dataclass
class CorrelationCache:
    """相关矩阵缓存。"""
    cache_dir: Path
    _matrices: dict[str, tuple[np.ndarray, list[str]]] = field(default_factory=dict)
    _stats: dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0})

    def __post_init__(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _compute_matrix_id(self, factor_ids: list[str], sample_size: int = 0) -> str:
        sorted_ids = sorted(factor_ids)
        raw = f"{'|'.join(sorted_ids)}:sample={sample_size}:v{CACHE_VERSION}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, factor_ids: list[str], sample_size: int = 0) -> Optional[tuple[np.ndarray, list[str]]]:
        """获取缓存的相关矩阵。"""
        mid = self._compute_matrix_id(factor_ids, sample_size)
        if mid in self._matrices:
            self._stats["hits"] += 1
            return self._matrices[mid]

        fp = self.cache_dir / f"corr_{mid}.npz"
        if fp.exists():
            try:
                data = np.load(fp, allow_pickle=True)
                matrix = data["matrix"]
                labels = list(data["labels"])
                result = (matrix, labels)
                self._matrices[mid] = result
                self._stats["hits"] += 1
                return result
            except (IOError, ValueError):
                pass

        self._stats["misses"] += 1
        return None

    def put(self, factor_ids: list[str], matrix: np.ndarray, sample_size: int = 0) -> None:
        """存入相关矩阵。"""
        mid = self._compute_matrix_id(factor_ids, sample_size)
        labels = sorted(factor_ids)
        self._matrices[mid] = (matrix, labels)

        fp = self.cache_dir / f"corr_{mid}.npz"
        np.savez_compressed(fp, matrix=matrix, labels=np.array(labels))

    def clear(self) -> int:
        """清空所有缓存，返回删除的文件数。"""
        removed = 0
        for fp in self.cache_dir.glob("corr_*.npz"):
            try:
                fp.unlink()
                removed += 1
            except OSError:
                pass
        self._matrices.clear()
        return removed

    def invalidate_all(self) -> None:
        """清空所有缓存。"""
        self.clear()

    @property
    def hit_rate(self) -> float:
        total = self._stats["hits"] + self._stats["misses"]
        return self._stats["hits"] / total if total > 0 else 0.0


# ─── 优化器 ────────────────────────────────────────────────

class FactorOptimizer:
    """可扩展因子优化框架。

    分层架构:
        Tier 1: 因子级并行信号计算
        Tier 2: 两阶段正交化（预筛 + 统计）
        Tier 3: 增量式缓存
        Tier 4: 采样式相关估计
    """

    def __init__(
        self,
        factor_cache_dir: str | Path = "memory/cache/factor_signals",
        correlation_cache_dir: str | Path = "memory/cache/correlation",
        max_workers: Optional[int] = None,
        parallel_threshold: int = PARALLEL_THRESHOLD,
    ):
        self.factor_cache = FactorSignalCache(Path(factor_cache_dir))
        self.corr_cache = CorrelationCache(Path(correlation_cache_dir))
        self.max_workers = max_workers or max(1, min(8, __import__("os").cpu_count() or 4))
        self.parallel_threshold = parallel_threshold
        self._timings: dict[str, float] = {}

    def _timeit(self, name: str, fn: Callable, *args, **kwargs):
        """计时装饰器。"""
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        self._timings[name] = self._timings.get(name, 0) + elapsed
        return result

    # ─── Tier 1: 并行因子信号计算 ────────────────────

    def compute_signal_matrix_parallel(
        self,
        panel: dict[str, pd.DataFrame],
        factors: list[dict[str, Any]],
        force_recompute: bool = False,
    ) -> dict[str, dict[str, np.ndarray]]:
        """并行计算因子信号矩阵。

        当因子数 >= parallel_threshold 时启用并行。

        Args:
            panel: 品种行情面板
            factors: 因子列表
            force_recompute: 强制重新计算（忽略缓存）

        Returns:
            signal_matrix[symbol][factor_name] = np.ndarray
        """
        from fts.factor_engine.factor_program import FactorExecutor

        n_factors = len(factors)
        n_symbols = len(panel)
        use_parallel = n_factors >= self.parallel_threshold

        print(f"      [优化器] 因子={n_factors} 品种={n_symbols} | "
              f"并行={'ON' if use_parallel else 'OFF'} (阈值={self.parallel_threshold})")

        # 计算数据版本哈希
        data_version = self._compute_data_version(panel)

        if use_parallel:
            return self._compute_parallel(panel, factors, data_version, force_recompute)
        else:
            return self._compute_sequential(panel, factors, data_version, force_recompute)

    def _compute_data_version(self, panel: dict[str, pd.DataFrame]) -> str:
        """计算数据版本哈希（基于每个品种最新日期）。"""
        parts = []
        for sym, df in sorted(panel.items()):
            if df is not None and not df.empty:
                last_date = str(df.index[-1])
                n_rows = len(df)
                parts.append(f"{sym}:{last_date}:{n_rows}")
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def _compute_sequential(
        self,
        panel: dict[str, pd.DataFrame],
        factors: list[dict[str, Any]],
        data_version: str,
        force_recompute: bool,
    ) -> dict[str, dict[str, np.ndarray]]:
        """顺序计算（小因子池）。"""
        from fts.factor_engine.factor_program import FactorExecutor

        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        cache_hits = 0
        cache_misses = 0

        for sym, df in panel.items():
            if df is None or df.empty or len(df) < 20:
                continue
            sym_signals: dict[str, np.ndarray] = {}

            for factor_data in factors:
                name = factor_data.get("name", "?")
                fid = factor_data.get("factor_id", "")
                code = factor_data.get("code", "")
                code_hash = hashlib.sha256(code.encode()).hexdigest()[:12] if code else ""

                # 检查缓存
                cache_key = FactorCacheKey(
                    factor_id=fid,
                    factor_code_hash=code_hash,
                    symbol=sym,
                    data_version=data_version,
                )

                if not force_recompute:
                    cached = self.factor_cache.get(cache_key)
                    if cached is not None:
                        sym_signals[name] = cached
                        cache_hits += 1
                        continue

                # 计算信号
                try:
                    executor = FactorExecutor(factor_data)
                    sig = executor.execute(df, factor_data.get("params", {}))
                    arr = np.array(sig, dtype=float)
                    arr = np.where(np.isfinite(arr), arr, np.nan)
                    sym_signals[name] = arr
                    self.factor_cache.put(cache_key, arr)
                    cache_misses += 1
                except Exception:
                    cache_misses += 1
                    continue

            if sym_signals:
                signal_matrix[sym] = sym_signals

        if cache_hits > 0 or cache_misses > 0:
            total = cache_hits + cache_misses
            print(f"      [缓存] 命中 {cache_hits}/{total} ({cache_hits/total:.0%})")

        return signal_matrix

    def _compute_parallel(
        self,
        panel: dict[str, pd.DataFrame],
        factors: list[dict[str, Any]],
        data_version: str,
        force_recompute: bool,
    ) -> dict[str, dict[str, np.ndarray]]:
        """并行计算（大因子池）。"""
        from fts.factor_engine.factor_program import FactorExecutor

        # 准备任务列表
        tasks: list[tuple[str, dict[str, Any], FactorCacheKey]] = []
        for sym, df in panel.items():
            if df is None or df.empty or len(df) < 20:
                continue
            for factor_data in factors:
                name = factor_data.get("name", "?")
                fid = factor_data.get("factor_id", "")
                code = factor_data.get("code", "")
                code_hash = hashlib.sha256(code.encode()).hexdigest()[:12] if code else ""

                cache_key = FactorCacheKey(
                    factor_id=fid,
                    factor_code_hash=code_hash,
                    symbol=sym,
                    data_version=data_version,
                )

                if not force_recompute:
                    cached = self.factor_cache.get(cache_key)
                    if cached is not None:
                        tasks.append((sym, factor_data, cache_key))  # 标记为缓存命中
                        continue
                    tasks.append((sym, factor_data, cache_key))

        # 分批并行执行
        batch_size = self.max_workers * 4
        signal_matrix: dict[str, dict[str, np.ndarray]] = {}
        n_workers = min(self.max_workers, len(tasks))

        cache_hits = 0
        cache_misses = 0

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {}
            for sym, factor_data, cache_key in tasks:
                # 再次检查缓存（多线程安全）
                if not force_recompute:
                    cached = self.factor_cache.get(cache_key)
                    if cached is not None:
                        if sym not in signal_matrix:
                            signal_matrix[sym] = {}
                        signal_matrix[sym][factor_data.get("name", "?")] = cached
                        cache_hits += 1
                        continue

                future = executor.submit(
                    self._compute_single_factor_signal,
                    sym, factor_data, cache_key, force_recompute,
                )
                futures[future] = (sym, factor_data, cache_key)

            for future in as_completed(futures):
                sym, factor_data, cache_key = futures[future]
                try:
                    name, arr = future.result()
                    if arr is not None:
                        if sym not in signal_matrix:
                            signal_matrix[sym] = {}
                        signal_matrix[sym][name] = arr
                        cache_misses += 1
                except Exception as e:
                    cache_misses += 1

        total = cache_hits + cache_misses
        if total > 0:
            print(f"      [并行] 完成 | 缓存命中 {cache_hits}/{total} ({cache_hits/total:.0%}) | "
                  f"workers={n_workers}")

        return signal_matrix

    def _compute_single_factor_signal(
        self,
        sym: str,
        factor_data: dict[str, Any],
        cache_key: FactorCacheKey,
        force_recompute: bool,
    ) -> tuple[str, Optional[np.ndarray]]:
        """计算单个因子在单个品种上的信号。"""
        from fts.factor_engine.factor_program import FactorExecutor

        name = factor_data.get("name", "?")

        if not force_recompute:
            cached = self.factor_cache.get(cache_key)
            if cached is not None:
                return (name, cached)

        # 需要获取 DataFrame（通过 panel，但在 worker 中不可序列化 panel）
        # 这里改为延迟加载：从 panel 全局引用获取
        # 为简化实现，使用全局 panel 引用
        global _optimize_panel_ref
        df = _optimize_panel_ref.get(sym) if _optimize_panel_ref else None
        if df is None or df.empty or len(df) < 20:
            return (name, None)

        try:
            executor = FactorExecutor(factor_data)
            sig = executor.execute(df, factor_data.get("params", {}))
            arr = np.array(sig, dtype=float)
            arr = np.where(np.isfinite(arr), arr, np.nan)
            self.factor_cache.put(cache_key, arr)
            return (name, arr)
        except Exception:
            return (name, None)

    # ─── Tier 2: 两阶段正交化 ────────────────────────

    def tiered_orthogonalize(
        self,
        factors: list[dict[str, Any]],
        max_corr_threshold: float = 0.7,
        family_threshold: int = 3,  # 同家族因子数超过此值才需要预筛
        mode: str = "mark",  # "mark"=只标记不删除, "remove"=硬删除（仅用于代码重复）
        l2_prior_correlations: Optional[list[dict[str, Any]]] = None,
        signal_matrix: Optional[dict[str, dict[str, np.ndarray]]] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """两阶段正交化。

        Phase 1: 廉价预筛（代码哈希 + 家族标记）
        Phase 2: 昂贵统计标记（仅对预筛后的候选集）
        L2 Prior: 注入 L2 种子因子相关性预检结果作为先验

        Args:
            factors: 因子列表
            max_corr_threshold: 最大相关性阈值
            family_threshold: 家族去重触发阈值
            mode: "mark"(默认) 只标记相关性高的因子, 不硬删除
                  "remove" 硬删除代码完全重复的因子（仅限 Phase 1 Step 1）
            l2_prior_correlations: L2 种子因子相关性预检结果（先验数据）
            signal_matrix: 因子信号矩阵（可选，Phase 2 需要时传入）
                          结构: {factor_name: {symbol: signal_array}}

        Returns:
            (标记后的因子列表, 过程摘要)
            
        设计原则:
            - 精英因子池只做标记，不做硬删除
            - 代码完全相同的重复因子 (same hash) 可以安全删除
            - 相关性高但代码不同的因子只标记，交由组合构建时决策
        """
        summary = {
            "input_count": len(factors),
            "phase1_marked": 0,
            "phase2_marked": 0,
            "phase1_details": [],
            "phase2_details": [],
            "elapsed_seconds": 0.0,
            "mode": mode,
        }

        t0 = time.perf_counter()

        if len(factors) <= max(2, family_threshold):
            # 小因子池：跳过分层，直接返回
            logger.info("[Optimizer] 跳过分层正交化: 因子数=%d <= 阈值=%d",
                        len(factors), max(2, family_threshold))
            summary["elapsed_seconds"] = time.perf_counter() - t0
            return factors, summary

        logger.info("[Optimizer] 启动分层正交化: 输入=%d 因子, 相关性阈值=%.2f, 模式=%s",
                    len(factors), max_corr_threshold, mode)

        # 复制因子列表，添加标记字段
        import copy
        marked_factors = copy.deepcopy(factors)
        for f in marked_factors:
            f.setdefault("correlation_flags", [])  # 相关性标记列表
            f.setdefault("exclude_from_portfolio", False)  # 是否从组合中排除

        # ── 注入 L2 相关性先验 ──
        l2_prior_count = 0
        l2_prior_factor_ids: set[str] = set()  # 记录哪些因子被 L2 标记过
        if l2_prior_correlations:
            logger.info("[Optimizer] === 注入 L2 相关性先验 ===")
            logger.info("[Optimizer] L2 先验输入: %d 对高相关因子 (threshold=0.95)", len(l2_prior_correlations))
            
            for idx, pair in enumerate(l2_prior_correlations):
                fid_a = pair.get("factor_id_a", "")
                fid_b = pair.get("factor_id_b", "")
                pearson = pair.get("pearson", 0)
                spearman = pair.get("spearman", 0)
                max_abs = max(abs(pearson), abs(spearman))
                
                logger.info("[Optimizer] L2 先验对 #%d: %s × %s | Pearson=%.4f, Spearman=%.4f, max=%.4f",
                            idx + 1, fid_a, fid_b, pearson, spearman, max_abs)
                
                if max_abs >= 0.95:
                    # 标记两个因子
                    for f in marked_factors:
                        fid = f.get("factor_id", "")
                        if fid in (fid_a, fid_b):
                            partner = fid_b if fid == fid_a else fid_a
                            flag = {
                                "type": "l2_seed_correlation",
                                "reason": f"L2 种子预检: 与 {partner} 相关 {max_abs:.3f}",
                                "source": "l2_prior",
                            }
                            f["correlation_flags"].append(flag)
                            l2_prior_count += 1
                            l2_prior_factor_ids.add(fid)
                            logger.info("[Optimizer] L2 标记 → %s: %s",
                                        f.get("name", fid), flag["reason"])
            
            if l2_prior_count > 0:
                logger.info("[Optimizer] L2 先验标记完成: %d 个因子被标记 (涉及 %d 个因子ID)",
                            l2_prior_count, len(l2_prior_factor_ids))
            else:
                logger.info("[Optimizer] L2 先验无超过阈值的因子对")

        # ── Phase 1: 廉价预筛 ──
        logger.info("[Optimizer] === Phase 1: 廉价预筛 ===")
        phase1_flags, phase1_details = self._phase1_prescreen(
            marked_factors, mode=mode
        )
        summary["phase1_marked"] = len(phase1_flags)
        summary["phase1_details"] = phase1_details

        # 应用 Phase 1 标记
        for flag_info in phase1_flags:
            fid = flag_info["factor_id"]
            flag_type = flag_info["type"]
            reason = flag_info.get("reason", "")
            for f in marked_factors:
                if f.get("factor_id") == fid:
                    flag_entry = {"type": flag_type, "reason": reason, "source": f"phase1_{flag_type}"}
                    f["correlation_flags"].append(flag_entry)
                    # 代码重复可以硬删除（mode="remove"）
                    if mode == "remove" and flag_type == "code_duplicate":
                        f["exclude_from_portfolio"] = True

        # 计算非排除因子数量
        non_excluded = [f for f in marked_factors if not f.get("exclude_from_portfolio", False)]
        logger.info("[Optimizer] Phase 1 完成: 标记 %d 个因子, 非排除=%d",
                    len(phase1_flags), len(non_excluded))

        # ── Phase 2: 统计标记（与 L2 先验合并）──
        if len(marked_factors) > max(2, self.parallel_threshold // 2):
            logger.info("[Optimizer] === Phase 2: 相关性标记（与 L2 先验合并）===")
            logger.info("[Optimizer] Phase 2 输入: %d 因子, 已标记 L2 先验的因子: %d",
                        len(marked_factors), len(l2_prior_factor_ids))
            
            phase2_flags, phase2_details = self._phase2_correlation_marking(
                marked_factors, max_corr_threshold, signal_matrix=signal_matrix
            )
            summary["phase2_marked"] = len(phase2_flags)
            summary["phase2_details"] = phase2_details

            # 应用 Phase 2 标记（只标记，不删除）
            phase2_new_count = 0
            phase2_already_marked = 0
            for flag_info in phase2_flags:
                fid = flag_info["factor_id"]
                flag_type = flag_info["type"]
                reason = flag_info.get("reason", "")
                f = next((f for f in marked_factors if f.get("factor_id") == fid), None)
                if f:
                    # 检查该因子是否已有 L2 先验标记
                    has_l2_prior = fid in l2_prior_factor_ids
                    flag_entry = {
                        "type": flag_type,
                        "reason": reason,
                        "source": "phase2_full_correlation",
                    }
                    f["correlation_flags"].append(flag_entry)
                    
                    if has_l2_prior:
                        phase2_already_marked += 1
                        logger.info("[Optimizer] Phase 2 → %s: 新增 %s 标记 (已有 L2 先验)",
                                    f.get("name", fid), flag_type)
                    else:
                        phase2_new_count += 1
                        logger.info("[Optimizer] Phase 2 → %s: 新增 %s 标记 (首次标记)",
                                    f.get("name", fid), flag_type)

            logger.info(
                "[Optimizer] Phase 2 完成: 标记 %d 个因子 "
                "(新增 %d 个, 与 L2 先验重叠 %d 个)",
                len(phase2_flags), phase2_new_count, phase2_already_marked,
            )
            
            # 合并汇总日志
            summary["phase2_new_count"] = phase2_new_count
            summary["phase2_overlap_count"] = phase2_already_marked
        else:
            logger.info("[Optimizer] 跳过 Phase 2: 因子数=%d <= 阈值=%d",
                        len(marked_factors), max(2, self.parallel_threshold // 2))

        summary["elapsed_seconds"] = time.perf_counter() - t0
        summary["output_count"] = len(marked_factors)
        summary["l2_prior_count"] = l2_prior_count  # 总是保存 L2 先验计数

        # 统计标记数量
        total_flags = sum(len(f.get("correlation_flags", [])) for f in marked_factors)
        excluded_count = sum(1 for f in marked_factors if f.get("exclude_from_portfolio", False))

        # 统计各类型标记来源
        source_counts: dict[str, int] = {}
        for f in marked_factors:
            for flag in f.get("correlation_flags", []):
                src = flag.get("source", "unknown")
                source_counts[src] = source_counts.get(src, 0) + 1
        
        source_str = ", ".join(f"{k}={v}" for k, v in source_counts.items())
        
        logger.info(
            "[Optimizer] 分层正交化汇总: 输入 %d → 输出 %d | "
            "L2 先验标记 %d, Phase1 标记 %d, Phase2 标记 %d | "
            "Phase2 新增 %d, Phase2 与 L2 重叠 %d | "
            "总标记数=%d, 硬排除=%d | 耗时 %.4fs",
            summary["input_count"],
            summary["output_count"],
            l2_prior_count,
            summary["phase1_marked"],
            summary["phase2_marked"],
            summary.get("phase2_new_count", 0),
            summary.get("phase2_overlap_count", 0),
            total_flags, excluded_count,
            summary["elapsed_seconds"],
        )
        logger.info("[Optimizer] 标记来源分布: %s", source_str)

        return marked_factors, summary

    def _phase1_prescreen(
        self,
        factors: list[dict[str, Any]],
        mode: str = "mark",
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Phase 1: 廉价预筛（标记模式）。

        1. 代码哈希标记（完全相同代码的因子标记为 code_duplicate）
        2. 家族裁剪标记（同家族因子数过多时，标记为 family_pruned）
        
        Returns:
            (标记列表: [{"factor_id", "type", "reason"}], 详情列表)
        """
        flags: list[dict[str, Any]] = []
        details: list[dict[str, str]] = []

        # Step 1: 代码哈希去重（只标记，不删除，除非 mode="remove"）
        hash_groups: dict[str, list[dict[str, Any]]] = {}
        for f in factors:
            code = f.get("code", "")
            code_hash = hashlib.sha256(code.encode()).hexdigest()[:12] if code else f.get("factor_id", "")
            hash_groups.setdefault(code_hash, []).append(f)

        code_dup_count = 0
        for code_hash, group in hash_groups.items():
            if len(group) > 1:
                group_sorted = sorted(group, key=lambda x: abs(x.get("sharpe", 0)), reverse=True)
                for f in group_sorted[1:]:
                    fid = f.get("factor_id", "")
                    flags.append({
                        "factor_id": fid,
                        "type": "code_duplicate",
                        "reason": f"代码重复，保留 {group_sorted[0].get('name', '?')} (Sharpe={group_sorted[0].get('sharpe', 0):.2f})",
                    })
                    code_dup_count += 1
                    details.append({
                        "type": "code_duplicate",
                        "reason": f"代码重复，保留 {group_sorted[0].get('name', '?')} (Sharpe={group_sorted[0].get('sharpe', 0):.2f})",
                        "removed": f.get("name", "?"),
                    })

        if code_dup_count > 0:
            logger.info("[Optimizer] Phase 1-代码重复: 标记 %d 个因子", code_dup_count)
            for d in details:
                if d["type"] == "code_duplicate":
                    logger.info("  ⚠ 标记 %s — 原因: %s", d["removed"], d["reason"])

        # Step 2: 家族标记（只标记，不删除）
        family_groups: dict[str, list[dict[str, Any]]] = {}
        for f in factors:
            family = self._classify_family(f)
            family_groups.setdefault(family, []).append(f)

        family_mark_count = 0
        for family, group in family_groups.items():
            if len(group) > 10:  # 家族超过 10 个因子时标记
                group_sorted = sorted(group, key=lambda x: abs(x.get("sharpe", 0)), reverse=True)
                keep_n = min(len(group), 10)
                logger.info("[Optimizer] 家族标记: %s 家族 %d 个因子 → 标记后 %d 个为家族冗余",
                            family, len(group), len(group) - keep_n)
                for f in group_sorted[keep_n:]:
                    fid = f.get("factor_id", "")
                    flags.append({
                        "factor_id": fid,
                        "type": "family_pruned",
                        "reason": f"家族 {family} 超过 10 个，仅保留 Top {keep_n}",
                    })
                    family_mark_count += 1
                    details.append({
                        "type": "family_prune",
                        "reason": f"家族 {family} 超过 10 个，保留 Top {keep_n}",
                        "removed": f.get("name", "?"),
                    })

        if family_mark_count > 0:
            logger.info("[Optimizer] Phase 1-家族标记: 标记 %d 个因子", family_mark_count)
            for d in details:
                if d["type"] == "family_prune":
                    logger.info("  ⚠ 标记 %s — 原因: %s", d["removed"], d["reason"])

        total_marked = code_dup_count + family_mark_count
        if total_marked > 0:
            logger.info("[Optimizer] Phase 1 汇总: 标记 %d 个因子 (代码重复=%d, 家族标记=%d)",
                        total_marked, code_dup_count, family_mark_count)

        return flags, details

    def _classify_family(self, factor: dict[str, Any]) -> str:
        """将因子分类到家族。"""
        name = factor.get("name", "")
        for family, keywords in FACTOR_FAMILIES.items():
            for kw in keywords:
                if kw in name:
                    return family
        return "other"

    def _phase2_correlation_marking(
        self,
        factors: list[dict[str, Any]],
        max_corr_threshold: float,
        signal_matrix: Optional[dict[str, dict[str, np.ndarray]]] = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Phase 2: 相关性标记（只标记高相关因子对，不删除）。

        扫描所有因子对的相关性，标记相关性超过阈值的因子。
        只添加 correlation_flags，不修改因子列表。
        
        Args:
            factors: 因子列表
            max_corr_threshold: 最大相关性阈值
            signal_matrix: 因子信号矩阵（可选），如果提供则使用它计算相关性
        """
        flags: list[dict[str, Any]] = []
        details: list[dict[str, str]] = []

        logger.info("[Optimizer] Phase 2 启动: 因子数=%d, 相关性阈值=%.2f",
                    len(factors), max_corr_threshold)

        # 获取或计算相关矩阵
        factor_ids = [f.get("factor_id", "") for f in factors]
        corr_result = self._get_or_compute_correlation(factors, signal_matrix=signal_matrix)

        if corr_result is None:
            logger.warning("[Optimizer] Phase 2 跳过: 无法获取相关矩阵"
                          + (" (未提供 signal_matrix)" if signal_matrix is None else ""))
            return flags, details

        corr_matrix, labels = corr_result

        logger.info("[Optimizer] Phase 2: 获取相关矩阵 %dx%d", corr_matrix.shape[0], corr_matrix.shape[1])

        # 扫描高相关对
        n = len(labels)
        high_corr_pairs: list[tuple[int, int, float]] = []
        for i in range(n):
            for j in range(i + 1, n):
                c = abs(corr_matrix[i, j])
                if c > max_corr_threshold:
                    high_corr_pairs.append((i, j, c))

        if high_corr_pairs:
            logger.info("[Optimizer] Phase 2: 发现 %d 个高相关因子对 (|corr|>%.2f)",
                        len(high_corr_pairs), max_corr_threshold)

        # 标记高相关对中 Sharpe 更低的因子
        sharpe_map = {f.get("factor_id", ""): f.get("sharpe", 0) for f in factors}
        label_to_id = {}
        for f in factors:
            name = f.get("name", "")
            fid = f.get("factor_id", "")
            if name in labels:
                label_to_id[name] = fid

        marked_count = 0
        for i, j, c in high_corr_pairs:
            name_i = labels[i]
            name_j = labels[j]
            fid_i = label_to_id.get(name_i, "")
            fid_j = label_to_id.get(name_j, "")
            sharpe_i = sharpe_map.get(fid_i, 0)
            sharpe_j = sharpe_map.get(fid_j, 0)

            if sharpe_i >= sharpe_j:
                # 标记 j
                flags.append({
                    "factor_id": fid_j,
                    "type": "high_correlation",
                    "reason": f"与 {name_i}(Sharpe={sharpe_i:.2f}) 相关性 {c:.3f}",
                })
                details.append({
                    "type": "high_correlation",
                    "reason": f"{name_i}(Sharpe={sharpe_i:.2f}) × {name_j}(Sharpe={sharpe_j:.2f}) = {c:.3f}",
                    "removed": name_j,
                })
                marked_count += 1
            else:
                # 标记 i
                flags.append({
                    "factor_id": fid_i,
                    "type": "high_correlation",
                    "reason": f"与 {name_j}(Sharpe={sharpe_j:.2f}) 相关性 {c:.3f}",
                })
                details.append({
                    "type": "high_correlation",
                    "reason": f"{name_i}(Sharpe={sharpe_i:.2f}) × {name_j}(Sharpe={sharpe_j:.2f}) = {c:.3f}",
                    "removed": name_i,
                })
                marked_count += 1

        if marked_count > 0:
            logger.info("[Optimizer] Phase 2 标记 %d 个高相关因子:", marked_count)
            for d in details:
                if d["type"] == "high_correlation":
                    logger.info("  ⚠ 标记 %s — 原因: %s", d["removed"], d["reason"])

        return flags, details

    # ─── Tier 3: 相关矩阵缓存 ────────────────────────

    def _get_or_compute_correlation(
        self,
        factors: list[dict[str, Any]],
        signal_matrix: Optional[dict[str, dict[str, np.ndarray]]] = None,
    ) -> Optional[tuple[np.ndarray, list[str]]]:
        """获取或计算因子相关矩阵。

        优先使用传入的 signal_matrix 计算真实相关矩阵，
        其次检查缓存，最后尝试从因子特征推断。
        """
        factor_ids = [f.get("factor_id", "") for f in factors]
        factor_names = [f.get("name", f.get("factor_id", "")) for f in factors]

        # ── 1. 使用传入的 signal_matrix 计算真实相关矩阵 ──
        if signal_matrix is not None:
            logger.info("[Optimizer] 使用传入的 signal_matrix 计算相关矩阵")
            return self.compute_correlation_from_matrix(
                signal_matrix, factor_names
            )

        # ── 2. 检查缓存 ──
        cached = self.corr_cache.get(factor_ids)
        if cached is not None:
            logger.info("[Optimizer] 使用缓存的相关矩阵")
            return cached

        # ── 3. 从因子特征计算（降级方案）──
        try:
            from scripts.futures_signal_pipeline import _compute_signal_matrix, _compute_factor_sign_flips
            logger.info("[Optimizer] 从因子特征计算相关矩阵")
            return None  # 需要真实信号数据，跳过
        except ImportError:
            logger.info("[Optimizer] 无法导入信号计算模块，跳过相关矩阵计算")
            return None

    def compute_correlation_from_matrix(
        self,
        signal_matrix: dict[str, dict[str, np.ndarray]],
        factor_names: list[str],
        max_samples: int = 1000,  # 大因子池时使用的最大样本数
    ) -> tuple[np.ndarray, list[str]]:
        """从信号矩阵计算因子相关矩阵（支持采样）。

        对大因子池自动采样：
        - 因子数 <= 100: 全量计算
        - 因子数 > 100: 随机采样 max_samples 个品种的信号
        """
        n_factors = len(factor_names)
        use_sampling = n_factors > 100

        # 收集所有品种的信号
        all_signals: dict[str, list[np.ndarray]] = {}
        for fname in factor_names:
            all_signals[fname] = []

        # 采样策略
        all_symbols = list(signal_matrix.keys())
        if use_sampling and len(all_symbols) > max_samples:
            rng = np.random.default_rng(seed=42)
            sampled_symbols = list(rng.choice(all_symbols, size=max_samples, replace=False))
        else:
            sampled_symbols = all_symbols

        for sym in sampled_symbols:
            sym_signals = signal_matrix.get(sym, {})
            for fname in factor_names:
                arr = sym_signals.get(fname)
                if arr is not None and len(arr) > 0:
                    all_signals[fname].append(arr)

        # 对齐长度
        min_len = min(
            min(len(a) for a in all_signals.values()) if all_signals else 0,
            5000,  # 限制最大长度
        )

        if min_len == 0:
            return np.zeros((n_factors, n_factors)), factor_names

        # 构建特征矩阵
        X = np.zeros((min_len, n_factors))
        for i, fname in enumerate(factor_names):
            sigs = all_signals[fname]
            if sigs:
                combined = np.concatenate(sigs)[:min_len]
                X[:, i] = np.nan_to_num(combined, nan=0.0)

        # 计算相关矩阵
        corr_matrix = np.corrcoef(X, rowvar=False)
        corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)

        # 缓存
        factor_ids = factor_names
        self.corr_cache.put(factor_ids, corr_matrix)

        mode = "SAMPLED" if use_sampling else "FULL"
        print(f"      [相关性] {mode} | 因子={n_factors} 样本={len(sampled_symbols)} | "
              f"矩阵={corr_matrix.shape}")

        return corr_matrix, factor_names

    # ─── 缓存管理 ──────────────────────────────────

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计。"""
        return {
            "factor_cache": {
                "hit_rate": f"{self.factor_cache.hit_rate:.1%}",
                "hits": self.factor_cache._stats["hits"],
                "misses": self.factor_cache._stats["misses"],
            },
            "correlation_cache": {
                "hit_rate": f"{self.corr_cache.hit_rate:.1%}",
                "hits": self.corr_cache._stats["hits"],
                "misses": self.corr_cache._stats["misses"],
            },
            "timings": dict(sorted(self._timings.items())),
        }

    def invalidate_factor(self, factor_id: str) -> int:
        """失效指定因子的缓存。"""
        n = self.factor_cache.invalidate_factor(factor_id)
        # 相关矩阵缓存也需要失效
        self.corr_cache.invalidate_all()
        return n


# ─── 全局 Panel 引用（用于并行计算） ─────────────────────────
# 注意: 这是一个简化实现，生产环境应使用更优雅的依赖注入
_optimize_panel_ref: Optional[dict[str, pd.DataFrame]] = None


def set_panel_ref(panel: dict[str, pd.DataFrame]) -> None:
    """设置全局 Panel 引用（供并行计算使用）。"""
    global _optimize_panel_ref
    _optimize_panel_ref = panel


# ─── 便捷函数 ──────────────────────────────────────────────

def create_optimizer(**kwargs) -> FactorOptimizer:
    """创建优化器实例。"""
    return FactorOptimizer(**kwargs)
