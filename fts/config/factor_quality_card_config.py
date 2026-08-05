"""
fts/config/factor_quality_card_config.py — 因子质量评分卡配置

提取自 factor_quality_card.py 的所有可配置参数，
便于独立调整阈值和权重，无需修改核心计算逻辑。

配置项:
    1. 10 维度权重 (weights)
    2. 分级准入阈值 (grade thresholds)
    3. 各维度评分映射参数 (mapping parameters)
    4. 容量/换手率/相关性等默认值

版本: v1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Tuple


# ══════════════════════════════════════════════════════════
# 10 维度权重配置
# ══════════════════════════════════════════════════════════

@dataclass
class DimensionWeights:
    """10 维度评分权重 (总和建议约 6.6)。

    权重越高，该维度对总分影响越大。
    """

    ic_score: float = 1.0           # 有效性: IC/ICIR
    sharpe_score: float = 1.0       # 收益性: Sharpe/Calmar
    stability_score: float = 0.8   # 稳定性: WalkForward 结果
    robustness_score: float = 0.8   # 鲁棒性: 衰减率
    capacity_score: float = 0.6     # 容量: 容量估算
    tradability_score: float = 0.6  # 交易性: 换手率
    diversity_score: float = 0.5   # 多样性: 最大相关性
    logic_score: float = 0.5       # 逻辑性: 经济逻辑分
    timeliness_score: float = 0.4   # 实时性: 数据频率
    compatibility_score: float = 0.4  # 兼容性: 跨品种覆盖率

    def to_tuple(self) -> Tuple[float, ...]:
        """返回权重元组 (按固定顺序)。"""
        return (
            self.ic_score,
            self.sharpe_score,
            self.stability_score,
            self.robustness_score,
            self.capacity_score,
            self.tradability_score,
            self.diversity_score,
            self.logic_score,
            self.timeliness_score,
            self.compatibility_score,
        )

    def sum(self) -> float:
        """所有权重之和。"""
        return sum(self.to_tuple())


# ══════════════════════════════════════════════════════════
# 分级准入阈值
# ══════════════════════════════════════════════════════════

@dataclass
class GradeThresholds:
    """分级准入阈值配置。

    总分范围 0-total_max (默认 50)。
    - A 级: total >= grade_A_threshold (默认 40)
    - B 级: grade_B_min <= total < grade_A_threshold (默认 30-40)
    - C 级: total < grade_B_min (默认 < 30)
    """

    total_max: int = 50
    max_per_dimension: int = 5
    grade_A_threshold: float = 40.0   # A 级最低分
    grade_B_min: float = 30.0         # B 级最低分

    # 准入最低等级 ("A" = 仅 A 级通过, "B" = A/B 级通过, "C" = 全通过)
    min_grade: Literal["A", "B", "C"] = "B"


# ══════════════════════════════════════════════════════════
# 各维度评分映射参数
# ══════════════════════════════════════════════════════════

@dataclass
class ICMappingConfig:
    """IC → 有效性分 (0-5) 映射参数。

    阈值: IC=0.08→5分, 0.03→3分, 0.01→1分, 0→0分
    """

    ic_high: float = 0.08      # IC >= 此值给 5 分
    ic_mid: float = 0.03       # IC >= 此值给 3 分
    ic_low: float = 0.01       # IC >= 此值给 1 分


@dataclass
class ICIRMappingConfig:
    """ICIR → 有效性补充分 (0-5) 映射参数。

    ICIR = IC 均值 / IC 标准差。
    阈值: ICIR=3→5分, 2→3分, 1→1分
    """

    icir_high: float = 3.0    # ICIR >= 此值给 5 分
    icir_mid: float = 2.0     # ICIR >= 此值给 3 分
    icir_low: float = 1.0     # ICIR >= 此值给 1 分


@dataclass
class SharpeMappingConfig:
    """Sharpe → 收益性分 (0-5) 映射参数。

    阈值: Sharpe=3→5分, 1.5→3分, 0.5→1分
    """

    sharpe_high: float = 3.0    # Sharpe >= 此值给 5 分
    sharpe_mid: float = 1.5     # Sharpe >= 此值给 3 分
    sharpe_low: float = 0.5      # Sharpe >= 此值给 1 分


@dataclass
class CalmarMappingConfig:
    """Calmar → 收益性补充分 (0-5) 映射参数。

    Calmar = 年化收益 / 最大回撤。
    阈值: Calmar=2→5分, 1→3分, 0.5→1分
    """

    calmar_high: float = 2.0    # Calmar >= 此值给 5 分
    calmar_mid: float = 1.0     # Calmar >= 此值给 3 分
    calmar_low: float = 0.5     # Calmar >= 此值给 1 分


@dataclass
class DecayMappingConfig:
    """衰减率 → 鲁棒性分 (0-5) 映射参数。

    decay_rate: 月环比 IC 下降幅度 (正值表示衰减)。
    阈值: 0.1→5分, 0.3→3分, 0.5→1分
    """

    decay_good: float = 0.1    # 衰减率 <= 此值给 5 分
    decay_mid: float = 0.3     # 衰减率 <= 此值给 3 分
    decay_bad: float = 0.5     # 衰减率 <= 此值给 1 分


@dataclass
class CapacityMappingConfig:
    """容量估算 → 容量分 (0-5) 映射参数。

    针对期货因子优化: 10M-50M 容量是期货主力合约的常见规模。
    阈值: 1亿→5分, 5000万→4分, 1000万→3分, 100万→2分, <100万→1分
    """

    capacity_high: float = 100_000_000    # 容量 >= 此值给 5 分 (机构级)
    capacity_mid_high: float = 50_000_000  # 容量 >= 此值给 4 分 (大型期货)
    capacity_mid: float = 10_000_000       # 容量 >= 此值给 3 分 (中型期货)
    capacity_low: float = 1_000_000        # 容量 >= 此值给 2 分 (小型期货)
    capacity_min: float = 0.0               # 容量 > 此值给 1 分 (微型)


@dataclass
class TurnoverMappingConfig:
    """换手率 → 交易性分 (0-5) 映射参数。

    支持两种格式:
    - 小数格式: 0.1-0.5 (即 10%-50%)
    - 百分比格式: 50-500 (即 50%-500%)

    期货高频因子优化:
    - 最优区间: 50%-500% (0.5-5.0 或 50-500)
    - 可接受区间: 10%-1000% (0.1-10.0 或 10-1000)
    - 低于 10% 或超过 1000%: 1 分
    """

    # 最优区间 (百分比格式, 即 50-500 表示 50%-500%)
    turnover_opt_low: float = 50.0       # 最优区间下限 (%)
    turnover_opt_high: float = 500.0     # 最优区间上限 (%)
    # 可接受区间
    turnover_mid_low: float = 10.0       # 中等区间下限 (%)
    turnover_mid_high: float = 1000.0    # 中等区间上限 (%)
    # 单位: 'percent' (百分比) 或 'decimal' (小数)
    turnover_unit: str = "percent"


@dataclass
class CorrelationMappingConfig:
    """最大相关性 → 多样性分 (0-5) 映射参数。

    与已有因子相关性越低，多样性越好。
    阈值: 0.3→5分, 0.5→3分, 0.7→1分
    """

    corr_low: float = 0.3       # 相关性 <= 此值给 5 分
    corr_mid: float = 0.5       # 相关性 <= 此值给 3 分
    corr_high: float = 0.7       # 相关性 <= 此值给 1 分


@dataclass
class CoverageMappingConfig:
    """跨品种覆盖率 → 兼容性分 (0-5) 映射参数。

    阈值: 0.9→5分, 0.7→3分, 0.5→1分
    """

    coverage_high: float = 0.9   # 覆盖率 >= 此值给 5 分
    coverage_mid: float = 0.7    # 覆盖率 >= 此值给 3 分
    coverage_low: float = 0.5    # 覆盖率 >= 此值给 1 分


# ══════════════════════════════════════════════════════════
# 默认值配置
# ══════════════════════════════════════════════════════════

@dataclass
class DefaultValuesConfig:
    """评估时使用的默认参数 (当无法从评估结果提取时)。"""

    decay_rate: float = 0.2
    turnover: float = 0.3
    correlation_max: float = 0.5
    cross_symbol_coverage: float = 0.6
    capacity_estimate: float = 10_000_000
    logic_score: int = 3
    data_frequency: Literal["tick", "minute", "hour", "daily"] = "daily"
    icir: float = 0.0
    calmar: float = 0.0


# ══════════════════════════════════════════════════════════
# 主配置类
# ══════════════════════════════════════════════════════════

@dataclass
class FactorQualityCardFullConfig:
    """因子质量评分卡完整配置。

    整合所有权重、阈值和映射参数。
    """

    # 权重
    weights: DimensionWeights = field(default_factory=DimensionWeights)

    # 分级阈值
    grades: GradeThresholds = field(default_factory=GradeThresholds)

    # 各维度映射
    ic_mapping: ICMappingConfig = field(default_factory=ICMappingConfig)
    icir_mapping: ICIRMappingConfig = field(default_factory=ICIRMappingConfig)
    sharpe_mapping: SharpeMappingConfig = field(default_factory=SharpeMappingConfig)
    calmar_mapping: CalmarMappingConfig = field(default_factory=CalmarMappingConfig)
    decay_mapping: DecayMappingConfig = field(default_factory=DecayMappingConfig)
    capacity_mapping: CapacityMappingConfig = field(default_factory=CapacityMappingConfig)
    turnover_mapping: TurnoverMappingConfig = field(default_factory=TurnoverMappingConfig)
    correlation_mapping: CorrelationMappingConfig = field(default_factory=CorrelationMappingConfig)
    coverage_mapping: CoverageMappingConfig = field(default_factory=CoverageMappingConfig)

    # 默认值
    defaults: DefaultValuesConfig = field(default_factory=DefaultValuesConfig)

    def to_dict(self) -> dict:
        """转换为字典 (方便序列化)。"""
        return {
            "weights": self.weights.__dict__,
            "grades": self.grades.__dict__,
            "ic_mapping": self.ic_mapping.__dict__,
            "icir_mapping": self.icir_mapping.__dict__,
            "sharpe_mapping": self.sharpe_mapping.__dict__,
            "calmar_mapping": self.calmar_mapping.__dict__,
            "decay_mapping": self.decay_mapping.__dict__,
            "capacity_mapping": self.capacity_mapping.__dict__,
            "turnover_mapping": self.turnover_mapping.__dict__,
            "correlation_mapping": self.correlation_mapping.__dict__,
            "coverage_mapping": self.coverage_mapping.__dict__,
            "defaults": self.defaults.__dict__,
        }

    def to_factor_quality_card_config(self) -> dict:
        """转换为 FactorQualityCard 兼容的配置字典。"""
        return {
            "max_per_dimension": self.grades.max_per_dimension,
            "total_max": self.grades.total_max,
            "grade_A_threshold": self.grades.grade_A_threshold,
            "grade_B_min": self.grades.grade_B_min,
            "decay_discount_rate": self.decay_mapping.decay_mid,
        }


# ══════════════════════════════════════════════════════════
# 全局实例与便捷函数
# ══════════════════════════════════════════════════════════

_default_config: FactorQualityCardFullConfig | None = None


def get_quality_card_config() -> FactorQualityCardFullConfig:
    """获取全局默认配置 (单例模式)。"""
    global _default_config
    if _default_config is None:
        _default_config = FactorQualityCardFullConfig()
    return _default_config


def create_config(
    *,
    min_grade: Literal["A", "B", "C"] | None = None,
    grade_A_threshold: float | None = None,
    grade_B_min: float | None = None,
    total_max: int | None = None,
    **kwargs: object,
) -> FactorQualityCardFullConfig:
    """创建自定义配置实例。

    Args:
        min_grade: 最低准入等级
        grade_A_threshold: A 级阈值
        grade_B_min: B 级最低分
        total_max: 总分上限
        **kwargs: 其他配置覆盖 (如 weights.ic_score=1.2)

    Returns:
        新的配置实例
    """
    config = FactorQualityCardFullConfig()

    # 应用参数覆盖
    if min_grade is not None:
        config.grades.min_grade = min_grade
    if grade_A_threshold is not None:
        config.grades.grade_A_threshold = grade_A_threshold
    if grade_B_min is not None:
        config.grades.grade_B_min = grade_B_min
    if total_max is not None:
        config.grades.total_max = total_max

    # 处理 kwargs (支持点号分隔的键，如 "weights.ic_score")
    for key, value in kwargs.items():
        parts = key.split(".")
        obj = config
        for part in parts[:-1]:
            if hasattr(obj, part):
                obj = getattr(obj, part)
        if hasattr(obj, parts[-1]):
            setattr(obj, parts[-1], value)

    return config


# ─── 预设配置 ──────────────────────────────────────────

def get_conservative_config() -> FactorQualityCardFullConfig:
    """保守配置: 更严格的准入标准。

    A 级 >= 42 分, B 级 >= 32 分
    """
    config = FactorQualityCardFullConfig()
    config.grades.grade_A_threshold = 42.0
    config.grades.grade_B_min = 32.0
    config.grades.min_grade = "B"
    return config


def get_aggressive_config() -> FactorQualityCardFullConfig:
    """宽松配置: 更低的准入门槛。

    A 级 >= 38 分, B 级 >= 28 分
    """
    config = FactorQualityCardFullConfig()
    config.grades.grade_A_threshold = 38.0
    config.grades.grade_B_min = 28.0
    config.grades.min_grade = "B"
    return config


def get_permissive_config() -> FactorQualityCardFullConfig:
    """宽松配置: 允许 C 级通过 (仅做参考)。

    仅用于分析模式，不建议用于生产。
    """
    config = FactorQualityCardFullConfig()
    config.grades.min_grade = "C"
    return config


__all__ = [
    "DimensionWeights",
    "GradeThresholds",
    "ICMappingConfig",
    "ICIRMappingConfig",
    "SharpeMappingConfig",
    "CalmarMappingConfig",
    "DecayMappingConfig",
    "CapacityMappingConfig",
    "TurnoverMappingConfig",
    "CorrelationMappingConfig",
    "CoverageMappingConfig",
    "DefaultValuesConfig",
    "FactorQualityCardFullConfig",
    "get_quality_card_config",
    "create_config",
    "get_conservative_config",
    "get_aggressive_config",
    "get_permissive_config",
]