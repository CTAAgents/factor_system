"""
fts.pipeline.factor_combiner — 多因子加权/融合器。

HARNESS §契约优先：本文件定义多因子组合的核心契约。
- 输入: 多个因子得分（dict[symbol, float] 或 DataFrame）
- 输出: 组合因子得分（按权重加权 + 可选正交化）

边界:
    - 与 factor_engine.portfolio_loop.orthogonalize_factors 不同：
      - 本模块是管线 stage，处理"因子得分"层面的加权融合
      - portfolio_loop 处理"已评估因子"层面的正交化和组合构建
    - 本模块不涉及 LLM 调用，纯数值计算

版本: v0.1.0
"""
# pylint: disable=too-many-locals,too-many-branches

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd




# ─── 配置 ─────────────────────────────────────────────────

@dataclass
class CombinerConfig:
    """因子组合器配置。

    Attributes:
        weights: 因子名 → 权重（必须归一化，sum=1.0）
        normalize_inputs: 是否对每个因子得分做 z-score 归一化（默认 True）
        clip_sigma: 输入归一化时的裁剪标准差倍数（默认 3.0）
        orthogonalize: 是否对输入因子做正交化（默认 False）
        min_active_factors: 最少有效因子数（低于此数返回 0，默认 1）
        active_threshold: 因子得分绝对值阈值（>threshold 视为有效，默认 0.05）
    """
    weights: dict[str, float] = field(default_factory=dict)
    normalize_inputs: bool = True
    clip_sigma: float = 3.0
    orthogonalize: bool = False
    min_active_factors: int = 1
    active_threshold: float = 0.05


# ─── 加权因子条目 ─────────────────────────────────────────

@dataclass
class WeightedFactor:
    """加权因子条目 — 描述单个因子在组合中的贡献。

    Attributes:
        name: 因子名
        weight: 权重（0~1）
        raw_scores: 原始得分（symbol → score）
        normalized_scores: 归一化后的得分（None = 未归一化）
        contribution: 对组合得分的贡献（normalized * weight）
    """
    name: str
    weight: float
    raw_scores: dict[str, float] = field(default_factory=dict)
    normalized_scores: Optional[dict[str, float]] = None
    contribution: Optional[dict[str, float]] = None


# ─── 因子组合器 ───────────────────────────────────────────

class FactorCombiner:
    """多因子加权/融合器 — 纯数值计算（无 LLM 调用）。

    用法:
        cfg = CombinerConfig(weights={"momentum": 0.4, "basis": 0.3, "macro": 0.3})
        combiner = FactorCombiner(cfg)
        combined = combiner.combine({
            "momentum": {"RB": 0.5, "AU": -0.2, ...},
            "basis":    {"RB": -0.3, "AU": 0.1, ...},
            "macro":    {"RB": 0.2, "AU": 0.0, ...},
        })
        # combined.combined_scores: {"RB": 0.17, "AU": -0.05, ...}
    """

    def __init__(self, config: CombinerConfig):
        self._config = config
        # 权重归一化（容错：空权重 → 等权）
        total_w = sum(config.weights.values())
        if total_w > 0:
            self._weights = {k: v / total_w for k, v in config.weights.items()}
        else:
            self._weights = dict(config.weights)

    @property
    def config(self) -> CombinerConfig:
        return self._config

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def combine(self,
                factor_scores: dict[str, dict[str, float]],
                ) -> "CombineResult":
        """对多因子得分进行加权融合。

        Args:
            factor_scores: {factor_name: {symbol: score}}

        Returns:
            CombineResult: 含组合得分、各因子贡献、有效因子数
        """
        if not factor_scores:
            return CombineResult(
                combined_scores={},
                factors=[],
                active_counts={},
                trace_id=None,
                success=False,
                error="no factor scores provided",
            )

        # 仅保留配置中声明的因子（按权重）
        declared = {k: v for k, v in factor_scores.items() if k in self._weights}
        if not declared:
            return CombineResult(
                combined_scores={},
                factors=[],
                active_counts={},
                trace_id=None,
                success=False,
                error="no declared factors in input",
            )

        # 收集所有 symbol
        all_symbols: set[str] = set()
        for scores in declared.values():
            all_symbols.update(scores.keys())

        # 转 DataFrame（行=symbol, 列=factor）
        df = pd.DataFrame(
            {name: pd.Series(scores) for name, scores in declared.items()}
        ).reindex(sorted(all_symbols))

        # 输入归一化
        normalized_df = df.copy()
        if self._config.normalize_inputs:
            for col in df.columns:
                series = df[col]
                mu = series.mean()
                sigma = series.std()
                if sigma is not None and sigma > 1e-10:
                    z = (series - mu) / sigma
                    # 裁剪极端值
                    clip = self._config.clip_sigma
                    normalized_df[col] = z.clip(-clip, clip)
                else:
                    # 全相同或 NaN → 中心化到 0
                    normalized_df[col] = series - mu

        # 可选正交化（使用 QR 分解，按列顺序）
        if self._config.orthogonalize and normalized_df.shape[1] > 1:
            arr = normalized_df.fillna(0.0).to_numpy()
            try:
                q, _ = np.linalg.qr(arr)
                # 保留每列模长（投影到正交基）
                norms = np.linalg.norm(q, axis=0)
                norms[norms == 0] = 1.0
                arr = q / norms
                normalized_df = pd.DataFrame(
                    arr, index=normalized_df.index, columns=normalized_df.columns
                )
            except np.linalg.LinAlgError:
                pass  # 奇异矩阵 → 跳过正交化

        # 加权融合
        weights_series = pd.Series(
            {k: self._weights[k] for k in normalized_df.columns}
        )
        combined_series = normalized_df.mul(weights_series).sum(axis=1)

        # 有效因子数（每个 symbol）
        active_counts: dict[str, int] = {}
        threshold = self._config.active_threshold
        for sym in normalized_df.index:
            row = normalized_df.loc[sym]
            cnt = int((row.abs() > threshold).sum())
            active_counts[sym] = cnt

        # 应用 min_active_factors 过滤
        min_active = self._config.min_active_factors
        combined_scores: dict[str, float] = {}
        for sym in combined_series.index:
            if active_counts.get(sym, 0) >= min_active:
                val = combined_series.loc[sym]
                combined_scores[sym] = float(val) if val is not None and not np.isnan(val) else 0.0
            else:
                combined_scores[sym] = 0.0

        # 构造 WeightedFactor 明细
        factors: list[WeightedFactor] = []
        for name in normalized_df.columns:
            raw = {s: float(v) if not np.isnan(v) else 0.0
                   for s, v in df[name].items()}
            norm = {s: float(v) if not np.isnan(v) else 0.0
                    for s, v in normalized_df[name].items()}
            contrib = {s: norm.get(s, 0.0) * self._weights[name] for s in norm}
            factors.append(WeightedFactor(
                name=name,
                weight=self._weights[name],
                raw_scores=raw,
                normalized_scores=norm,
                contribution=contrib,
            ))

        return CombineResult(
            combined_scores=combined_scores,
            factors=factors,
            active_counts=active_counts,
            trace_id=None,
            success=True,
            error=None,
        )


# ─── 组合结果 ─────────────────────────────────────────────

@dataclass
class CombineResult:
    """因子组合结果。

    Attributes:
        combined_scores: symbol → 组合得分
        factors: 各因子的贡献明细
        active_counts: symbol → 有效因子数
        trace_id: 全链路 trace_id（None = 未注入）
        success: 是否成功
        error: 失败原因（success=True 时为 None）
    """
    combined_scores: dict[str, float]
    factors: list[WeightedFactor]
    active_counts: dict[str, int]
    trace_id: Optional[str]
    success: bool
    error: Optional[str]
