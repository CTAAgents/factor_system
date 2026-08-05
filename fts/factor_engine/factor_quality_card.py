"""
fts.factor_engine.factor_quality_card — 因子质量评分卡

A.1 模块: 10 维度定量评分体系 (0-50 分)，替代 pass/fail 判定。
为分级准入 (A/B/C 级) 和因子排名提供依据。

用法:
    card = FactorQualityCard(config)
    score = card.evaluate(
        factor_id='fct_abc12345',
        ic=0.05, sharpe=2.1,
        walk_forward_result=wf_result,
        decay_rate=0.12,
        turnover=0.15,
        correlation_max=0.45,
        logic_score=4,
        data_frequency='daily',
        cross_symbol_coverage=0.85,
        capacity_estimate=100_000_000
    )
    print(score['total_score'], score['grade'])

版本: v1.0.0
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypedDict

from .walk_forward import WalkForwardResult


# ─── 契约: 类型定义 ──────────────────────────────────────────


class DimensionScore(TypedDict, total=False):
    """单个维度的评分。

    约束:
        - score 范围 0-5
        - raw_value 为原始指标值
    """
    name: str
    raw_value: float
    score: float
    description: str


class FactorQualityScore(TypedDict, total=False):
    """完整评分卡。

    约束:
        - total_score 范围 0-50
        - grade 为 'A'/'B'/'C'
    """
    score_id: str
    factor_id: str
    total_score: float
    dimension_scores: list[DimensionScore]
    evaluated_at: str
    score_version: str
    grade: Literal["A", "B", "C"]


class FactorQualityCardConfig(TypedDict, total=False):
    """评分卡配置。

    约束:
        - max_per_dimension 默认 5
        - total_max 默认 50
        - grade_A_threshold 默认 40
        - grade_B_min 默认 30
    """
    max_per_dimension: int
    total_max: int
    grade_A_threshold: float
    grade_B_min: float
    decay_discount_rate: float


# ─── 维度映射权重 ──────────────────────────────────────────

_DIMENSION_WEIGHTS: tuple[float, ...] = (
    1.0,   # ic_score
    1.0,   # sharpe_score
    0.8,   # stability_score
    0.8,   # robustness_score
    0.6,   # capacity_score
    0.6,   # tradability_score
    0.5,   # diversity_score
    0.5,   # logic_score
    0.4,   # timeliness_score
    0.4,   # compatibility_score
)
"""十维度权重，总和为 6.6。"""

_DIMENSION_NAMES: tuple[str, ...] = (
    "ic_score",
    "sharpe_score",
    "stability_score",
    "robustness_score",
    "capacity_score",
    "tradability_score",
    "diversity_score",
    "logic_score",
    "timeliness_score",
    "compatibility_score",
)
"""十维度名称，与权重一一对应。"""

# ─── 评分映射函数 ──────────────────────────────────────────


def _map_ic_to_score(ic: float) -> float:
    """IC → 有效性分 (0-5)。

    阈值: IC=0.08→5分, 0.03→3分, 0.01→1分, 0→0分
    """
    if ic <= 0:
        return 0.0
    if ic >= 0.08:
        return 5.0
    if ic >= 0.03:
        return 3.0
    if ic >= 0.01:
        return 1.0
    return (ic / 0.01) * 1.0


def _map_icir_to_score(icir: float) -> float:
    """ICIR → 有效性补充分 (0-5)。

    ICIR = IC 均值 / IC 标准差。ICIR 越高，因子越稳定。
    阈值: ICIR=3→5分, 2→3分, 1→1分
    """
    if icir <= 0:
        return 0.0
    if icir >= 3:
        return 5.0
    if icir >= 2:
        return 3.0
    if icir >= 1:
        return 1.0
    return (icir / 1.0) * 1.0


def _map_sharpe_to_score(sharpe: float) -> float:
    """Sharpe → 收益性分 (0-5)。

    阈值: Sharpe=3→5分, 1.5→3分, 0.5→1分
    """
    if sharpe <= 0:
        return 0.0
    if sharpe >= 3:
        return 5.0
    if sharpe >= 1.5:
        return 3.0
    if sharpe >= 0.5:
        return 1.0
    return (sharpe / 0.5) * 1.0


def _map_calmar_to_score(calmar: float) -> float:
    """Calmar → 收益性补充分 (0-5)。

    Calmar = 年化收益 / 最大回撤。
    阈值: Calmar=2→5分, 1→3分, 0.5→1分
    """
    if calmar <= 0:
        return 0.0
    if calmar >= 2:
        return 5.0
    if calmar >= 1:
        return 3.0
    if calmar >= 0.5:
        return 1.0
    return (calmar / 0.5) * 1.0


def _map_stability_to_score(wf_result: WalkForwardResult) -> float:
    """WalkForward 结果 → 稳定性分 (0-5)。

    综合 IC 一致性、IC 波动率、综合评分和窗口数量。
    四项独立评分：一致性 0-2.0, 波动率 0-1.5, 综合分 0-1.0, 窗口数 0-0.5。
    """
    consistency = wf_result.get("ic_consistency", 0.0)
    volatility = wf_result.get("ic_volatility", 1.0)
    n_windows = wf_result.get("n_windows_completed", 0)
    cons_score_raw = wf_result.get("consistency_score", 0.0)

    # IC 一致性分 (0-2.0): 窗口通过率越高越好
    consistency_score = min(consistency / 0.8, 1.0) * 2.0

    # IC 波动率分 (0-1.5): 波动率越低越好
    volatility_score = max(0.0, (1.0 - min(volatility, 1.0)) / 1.0) * 1.5

    # 综合分补充 (0-1.0): consistency_score 越高越好
    benchmarked = min(cons_score_raw / 100.0, 1.0)
    benchmark_score = benchmarked * 1.0

    # 窗口数量分 (0-0.5): 窗口越多越可靠
    window_score = min(n_windows / 4.0, 1.0) * 0.5

    return round(consistency_score + volatility_score + benchmark_score + window_score, 2)


def _map_decay_to_score(decay_rate: float) -> float:
    """衰减率 → 鲁棒性分 (0-5)。

    decay_rate: 月环比 IC 下降幅度 (正值表示衰减)。
    阈值: 0.1→5分, 0.3→3分, 0.5→1分
    """
    if decay_rate <= 0.1:
        return 5.0
    if decay_rate <= 0.3:
        return 3.0
    if decay_rate <= 0.5:
        return 1.0
    return 0.0


def _map_capacity_to_score(capacity_estimate: float) -> float:
    """容量估算 → 容量分 (0-5)。

    针对期货因子优化后的阈值:
    1亿+ → 5分, 5000万+ → 4分, 1000万+ → 3分, 100万+ → 2分, <100万 → 1分
    """
    if capacity_estimate <= 0:
        return 0.0
    if capacity_estimate >= 100_000_000:
        return 5.0
    if capacity_estimate >= 50_000_000:
        return 4.0
    if capacity_estimate >= 10_000_000:
        return 3.0
    if capacity_estimate >= 1_000_000:
        return 2.0
    return 1.0


def _map_turnover_to_score(turnover: float) -> float:
    """换手率 → 交易性分 (0-5)。

    自动检测格式:
    - 小数格式 (0-10): 5.72 表示 572%
    - 百分比格式 (>10): 572.34 表示 572.34%

    期货高频因子优化后阈值 (转换为百分比后):
    - 最优: 50%-500% → 5分
    - 可接受: 10%-1000% → 3分
    - 其他: 1分
    """
    if turnover <= 0:
        return 1.0

    # 自动检测格式: <= 10 视为小数, > 10 视为百分比
    if turnover <= 10:
        # 小数格式: 5.72 → 572%
        turnover_pct = turnover * 100
    else:
        # 百分比格式: 572.34 → 572.34%
        turnover_pct = turnover

    # 期货优化后的阈值 (百分比)
    if 50 <= turnover_pct <= 500:
        return 5.0
    if 10 <= turnover_pct <= 1000:
        return 3.0
    return 1.0


def _map_correlation_to_score(correlation_max: float) -> float:
    """最大相关性 → 多样性分 (0-5)。

    与已有因子相关性越低，多样性越好。
    阈值: 0.3→5分, 0.5→3分, 0.7→1分
    """
    if correlation_max <= 0.3:
        return 5.0
    if correlation_max <= 0.5:
        return 3.0
    if correlation_max <= 0.7:
        return 1.0
    return 0.0


def _map_logic_to_score(logic_score: int) -> float:
    """经济逻辑分 → 逻辑性分 (0-5)。

    直接使用 L2 经济逻辑评分 (0-5)。
    """
    return float(min(max(logic_score, 0), 5))


def _map_frequency_to_score(
    data_frequency: Literal["tick", "minute", "hour", "daily"],
) -> float:
    """数据频率 → 实时性分 (0-5)。

    期货因子优化: daily 频率在期货中是主力级别，应得 2 分。
    阈值: tick→5分, minute→4分, hour→3分, daily→2分
    """
    freq_map = {
        "tick": 5.0,
        "minute": 4.0,
        "hour": 3.0,
        "daily": 2.0,
    }
    return freq_map.get(data_frequency, 2.0)


def _map_coverage_to_score(cross_symbol_coverage: float) -> float:
    """跨品种覆盖率 → 兼容性分 (0-5)。

    期货因子优化: 单一品种覆盖也给基础分。
    阈值: 0.9→5分, 0.7→4分, 0.5→3分, 0.3→2分, <0.3→1分
    """
    if cross_symbol_coverage >= 0.9:
        return 5.0
    if cross_symbol_coverage >= 0.7:
        return 4.0
    if cross_symbol_coverage >= 0.5:
        return 3.0
    if cross_symbol_coverage >= 0.3:
        return 2.0
    return 1.0


# ─── 核心类: FactorQualityCard ──────────────────────────


class FactorQualityCard:
    """因子质量评分卡计算器。

    将 L1/L2/L3 评估结果映射到 10 维度评分 (0-50 分)，
    并输出分级准入 (A/B/C 级)。

    Usage:
        card = FactorQualityCard(config)
        score = card.evaluate(
            factor_id='...',
            ic=0.05, sharpe=2.1,
            walk_forward_result=wf_result,
            decay_rate=0.12,
            turnover=0.15,
            correlation_max=0.45,
            logic_score=4,
            data_frequency='daily',
            cross_symbol_coverage=0.85,
            capacity_estimate=100_000_000
        )
    """

    def __init__(self, config: FactorQualityCardConfig | None = None) -> None:
        self._config: FactorQualityCardConfig = config or {}

    # ─── 公有方法 ──────────────────────────────────

    def evaluate(
        self,
        *,
        factor_id: str,
        ic: float,
        sharpe: float,
        walk_forward_result: WalkForwardResult | None = None,
        decay_rate: float = 0.2,
        turnover: float = 0.3,
        correlation_max: float = 0.5,
        logic_score: int = 3,
        data_frequency: Literal["tick", "minute", "hour", "daily"] = "daily",
        cross_symbol_coverage: float = 0.6,
        capacity_estimate: float = 10_000_000,
        icir: float = 0.0,
        calmar: float = 0.0,
    ) -> FactorQualityScore:
        """执行评分卡计算。

        Args:
            factor_id: 因子 ID
            ic: IC 值
            sharpe: Sharpe 比率
            walk_forward_result: WalkForward 结果 (可选)
            decay_rate: 衰减率 (月环比 IC 下降幅度)
            turnover: 换手率
            correlation_max: 与已有因子的最大相关性
            logic_score: L2 经济逻辑评分 (0-5)
            data_frequency: 数据频率
            cross_symbol_coverage: 跨品种覆盖率
            capacity_estimate: 容量估算 (元)
            icir: ICIR (IC 均值/标准差)
            calmar: Calmar 比率

        Returns:
            FactorQualityScore: 完整评分卡
        """
        dimension_scores = self._compute_dimension_scores(
            ic=ic,
            sharpe=sharpe,
            walk_forward_result=walk_forward_result,
            decay_rate=decay_rate,
            turnover=turnover,
            correlation_max=correlation_max,
            logic_score=logic_score,
            data_frequency=data_frequency,
            cross_symbol_coverage=cross_symbol_coverage,
            capacity_estimate=capacity_estimate,
            icir=icir,
            calmar=calmar,
        )

        total_score = self._compute_total(dimension_scores)
        grade = self._determine_grade(total_score)

        return {
            "score_id": f"qsc_{factor_id}",
            "factor_id": factor_id,
            "total_score": total_score,
            "dimension_scores": dimension_scores,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "score_version": "v1",
            "grade": grade,
        }

    # ─── 私有方法 ──────────────────────────────────

    def _compute_dimension_scores(
        self,
        *,
        ic: float,
        sharpe: float,
        walk_forward_result: WalkForwardResult | None,
        decay_rate: float,
        turnover: float,
        correlation_max: float,
        logic_score: int,
        data_frequency: Literal["tick", "minute", "hour", "daily"],
        cross_symbol_coverage: float,
        capacity_estimate: float,
        icir: float,
        calmar: float,
    ) -> list[DimensionScore]:
        """计算所有 10 个维度的评分。"""
        scores: list[DimensionScore] = []

        # 1. 有效性: IC/ICIR
        ic_score = _map_ic_to_score(ic)
        icir_score = _map_icir_to_score(icir)
        validity_score = round((ic_score + icir_score) / 2, 2)
        scores.append({
            "name": "ic_score",
            "raw_value": ic,
            "score": validity_score,
            "description": f"IC={ic:.4f}, ICIR={icir:.2f}",
        })

        # 2. 收益性: Sharpe/Calmar
        sharpe_score = _map_sharpe_to_score(sharpe)
        calmar_score = _map_calmar_to_score(calmar)
        return_score = round((sharpe_score + calmar_score) / 2, 2)
        scores.append({
            "name": "sharpe_score",
            "raw_value": sharpe,
            "score": return_score,
            "description": f"Sharpe={sharpe:.2f}, Calmar={calmar:.2f}",
        })

        # 3. 稳定性: WalkForward 结果
        if walk_forward_result:
            stability_score = _map_stability_to_score(walk_forward_result)
            wf_desc = f"一致性={walk_forward_result.get('ic_consistency', 0):.2f}, 波动率={walk_forward_result.get('ic_volatility', 0):.2f}"
        else:
            stability_score = 2.5  # 默认中等分
            wf_desc = "无 WalkForward 结果，默认中等分"
        scores.append({
            "name": "stability_score",
            "raw_value": walk_forward_result.get("consistency_score", 0) if walk_forward_result else 0.0,
            "score": stability_score,
            "description": wf_desc,
        })

        # 4. 鲁棒性: 衰减率
        robustness_score = _map_decay_to_score(decay_rate)
        scores.append({
            "name": "robustness_score",
            "raw_value": decay_rate,
            "score": robustness_score,
            "description": f"衰减率={decay_rate:.2%}",
        })

        # 5. 容量: 容量估算
        capacity_score = _map_capacity_to_score(capacity_estimate)
        scores.append({
            "name": "capacity_score",
            "raw_value": capacity_estimate,
            "score": capacity_score,
            "description": f"容量={capacity_estimate:,.0f}",
        })

        # 6. 交易性: 换手率
        tradability_score = _map_turnover_to_score(turnover)
        scores.append({
            "name": "tradability_score",
            "raw_value": turnover,
            "score": tradability_score,
            "description": f"换手率={turnover:.2%}",
        })

        # 7. 多样性: 最大相关性
        diversity_score = _map_correlation_to_score(correlation_max)
        scores.append({
            "name": "diversity_score",
            "raw_value": correlation_max,
            "score": diversity_score,
            "description": f"最大相关性={correlation_max:.2f}",
        })

        # 8. 逻辑性: 经济逻辑分
        logic_dim_score = _map_logic_to_score(logic_score)
        scores.append({
            "name": "logic_score",
            "raw_value": logic_score,
            "score": logic_dim_score,
            "description": f"L2 评分={logic_score}/5",
        })

        # 9. 实时性: 数据频率
        timeliness_score = _map_frequency_to_score(data_frequency)
        scores.append({
            "name": "timeliness_score",
            "raw_value": 0.0,
            "score": timeliness_score,
            "description": f"数据频率={data_frequency}",
        })

        # 10. 兼容性: 跨品种覆盖率
        compatibility_score = _map_coverage_to_score(cross_symbol_coverage)
        scores.append({
            "name": "compatibility_score",
            "raw_value": cross_symbol_coverage,
            "score": compatibility_score,
            "description": f"覆盖率={cross_symbol_coverage:.1%}",
        })

        return scores

    def _compute_total(self, dims: list[DimensionScore]) -> float:
        """计算加权总分 (归一化到 0-50)。"""
        total_max = self._config.get("total_max", 50)
        weights = _DIMENSION_WEIGHTS

        # 加权求和
        raw_total = sum(
            d.get("score", 0.0) * w
            for d, w in zip(dims, weights)
        )
        weight_sum = sum(weights)

        # 归一化到 0-total_max
        normalized = (raw_total / (5.0 * weight_sum)) * total_max
        return round(normalized, 2)

    def _determine_grade(self, total: float) -> Literal["A", "B", "C"]:
        """根据总分判定等级。"""
        th_A = self._config.get("grade_A_threshold", 40.0)
        th_B_min = self._config.get("grade_B_min", 30.0)

        if total >= th_A:
            return "A"
        if total >= th_B_min:
            return "B"
        return "C"


# ─── 便捷函数 ──────────────────────────────────────────


def compute_total_score(
    dim_scores: list[DimensionScore],
    weights: list[float],
    total_max: int = 50,
) -> float:
    """计算加权总分 (归一化到 0-total_max)。

    便捷函数，等价于 FactorQualityCard._compute_total。
    """
    raw_total = sum(s["score"] * w for s, w in zip(dim_scores, weights))
    weight_sum = sum(weights)
    normalized = (raw_total / (5.0 * weight_sum)) * total_max
    return round(normalized, 2)


def determine_grade(
    total_score: float,
    th_A: float = 40.0,
    th_B_min: float = 30.0,
) -> Literal["A", "B", "C"]:
    """根据总分判定等级。

    便捷函数，等价于 FactorQualityCard._determine_grade。
    """
    if total_score >= th_A:
        return "A"
    if total_score >= th_B_min:
        return "B"
    return "C"