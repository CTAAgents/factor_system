"""
fts.factor_engine.regime_multipliers — Regime 权重数据化估计器（GAP-L308，D 阶段）。

替代/校准 `portfolio_loop.py` 中人工硬编码的 REGIME_FAMILY_MULTIPLIERS /
REGIME_STYLE_MULTIPLIERS 查表：

    按 regime × family 分桶统计因子历史 IC 均值 / 胜率 → 生成数据驱动倍率表
    （倍率 = 该 regime×family 的 IC 均值相对全局基准的归一化值）。

产出:
    - 数据驱动倍率表（dict），可落盘 `docs/harness/_data/l3_regime_multipliers.yaml`
    - 硬编码 vs 数据驱动对比报告（供审计）

用法:
    est = RegimeMultiplierEstimator()
    table = est.estimate(records)   # records: [{date, regime, factor_id, family, ic}]
    est.export_yaml("docs/harness/_data/l3_regime_multipliers.yaml")

版本: v1.0.0
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import yaml

logger = logging.getLogger(__name__)


# ─── 契约 ───────────────────────────────────────────────────

@dataclass
class RegimeMultiplierConfig:
    """倍率估计配置。"""
    min_samples: int = 10          # 家族×regime 最小样本数（不足回退硬编码 1.0）
    min_ic_floor: float = 0.02     # IC 绝对下限（防噪音倍率膨胀）
    clamp_lo: float = 0.5          # 倍率下限（钳制）
    clamp_hi: float = 1.5          # 倍率上限（钳制）


@dataclass
class RegimeMultiplierReport:
    """倍率估计报告。"""
    multipliers: dict[str, dict[str, float]]   # regime → family → multiplier
    stats: dict[str, Any] = field(default_factory=dict)      # 分桶统计
    comparison: dict[str, Any] = field(default_factory=dict)  # 硬编码 vs 数据驱动
    warnings: list[str] = field(default_factory=list)


class RegimeMultiplierEstimator:
    """数据驱动 Regime 权重倍率估计器（GAP-L308）。

    Args:
        config: 估计配置（None 用默认）
    """

    def __init__(self, config: Optional[RegimeMultiplierConfig] = None) -> None:
        self._config = config or RegimeMultiplierConfig()

    def estimate(
        self,
        records: list[dict[str, Any]],
        hardcoded: Optional[dict[str, dict[str, float]]] = None,
    ) -> RegimeMultiplierReport:
        """估计数据驱动倍率表。

        Args:
            records: 因子历史 IC 记录列表，每项含:
                - regime: 市场制度（"bull"/"bear"/"oscillate"/...）
                - family: 因子家族（"trend"/"momentum"/...）
                - ic: 截面 IC 值
                - factor_id: 因子 ID（去重用，可选）
                - date: 日期（可选，用于按时间聚合）
            hardcoded: 硬编码倍率表（可选，用于对比报告）

        Returns:
            RegimeMultiplierReport（multipliers + stats + comparison）。
        """
        if not records:
            return RegimeMultiplierReport(
                multipliers={}, stats={}, comparison={},
                warnings=["无输入记录，返回空倍率表"],
            )

        # 1. 按 regime × family 聚合 IC
        buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
        for r in records:
            regime = str(r.get("regime", "")).strip()
            family = str(r.get("family", "")).strip()
            ic = r.get("ic")
            if not regime or not family or ic is None:
                continue
            try:
                buckets[(regime, family)].append(float(ic))
            except (TypeError, ValueError):
                continue

        # 2. 全局家族基准（该家族在全部 regime 的 IC 均值）
        family_global: dict[str, float] = {}
        family_ics: dict[str, list[float]] = defaultdict(list)
        for (_, family), ics in buckets.items():
            family_ics[family].extend(ics)
        for family, ics in family_ics.items():
            family_global[family] = float(np.mean(ics))

        # 3. 计算倍率: regime×family IC 均值 / 全局家族基准（钳制 + 最小样本）
        multipliers: dict[str, dict[str, float]] = {}
        stats: dict[str, Any] = {"buckets": {}, "n_records": len(records)}
        warnings: list[str] = []

        for (regime, family), ics in buckets.items():
            if len(ics) < self._config.min_samples:
                warnings.append(
                    f"{regime}/{family} 样本不足 ({len(ics)} < {self._config.min_samples})，回退 1.0"
                )
                continue
            mean_ic = float(np.mean(ics))
            base = abs(family_global.get(family, 0.0))
            if base < 1e-12:
                mult = 1.0
            else:
                mult = float(mean_ic / base)
            mult = float(np.clip(mult, self._config.clamp_lo, self._config.clamp_hi))
            win_rate = float(np.mean([1.0 if ic > 0 else 0.0 for ic in ics]))
            multipliers.setdefault(regime, {})[family] = round(mult, 3)
            stats["buckets"][f"{regime}/{family}"] = {
                "n": len(ics), "mean_ic": round(mean_ic, 4),
                "win_rate": round(win_rate, 3), "multiplier": round(mult, 3),
            }

        # 4. 硬编码 vs 数据驱动对比
        comparison: dict[str, Any] = {}
        if hardcoded:
            comp_rows = []
            for regime, fam_map in multipliers.items():
                hc = hardcoded.get(regime, {})
                for family, mult in fam_map.items():
                    comp_rows.append({
                        "regime": regime, "family": family,
                        "hardcoded": hc.get(family, 1.0),
                        "data_driven": mult,
                    })
            comparison = {
                "n_entries": len(comp_rows),
                "rows": comp_rows,
            }
            # 最大偏差
            if comp_rows:
                max_dev = max(abs(r["data_driven"] - r["hardcoded"]) for r in comp_rows)
                comparison["max_deviation"] = round(max_dev, 3)

        report = RegimeMultiplierReport(
            multipliers=multipliers, stats=stats,
            comparison=comparison, warnings=warnings,
        )
        logger.info(
            "[GAP-L308] Regime 倍率估计完成: %d regimes, %d 桶, %d 警告",
            len(multipliers), len(stats["buckets"]), len(warnings),
        )
        return report

    # ─── 导出 ────────────────────────────────────────────

    def export_yaml(self, path: str, report: RegimeMultiplierReport) -> None:
        """倍率表落盘 YAML（易变配置进 `docs/harness/_data/` 原则）。"""
        doc = {
            "generated_by": "RegimeMultiplierEstimator (GAP-L308)",
            "generated_at": _now_iso(),
            "note": "数据驱动倍率表，替代硬编码 REGIME_FAMILY_MULTIPLIERS 的校准基准",
            "n_records": report.stats.get("n_records", 0),
            "min_samples": self._config.min_samples,
            "multipliers": report.multipliers,
        }
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
        logger.info("[GAP-L308] 倍率表已落盘: %s", path)

    def load_yaml(self, path: str) -> dict[str, Any]:
        """从 YAML 加载倍率表（缺失/损坏回退空字典）。"""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.warning("[GAP-L308] 倍率表加载失败 (%s)，回退硬编码", e)
            return {}


def _now_iso() -> str:
    """当前 UTC 时间 ISO 格式。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "RegimeMultiplierConfig",
    "RegimeMultiplierReport",
    "RegimeMultiplierEstimator",
]
