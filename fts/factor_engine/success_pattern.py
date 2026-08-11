"""
fts/factor_engine/success_pattern.py — 成功模式定向演化（Phase 1.2 P0-1，26 号计划 §6）

从经验链成功轨迹聚合近期成功模式（滚动窗口 + 时间衰减），注入 LLM prompt
作 **soft 偏向**（参考非硬性约束），提升演化命中率。

注入维度（仅结构信息，明确排除 family）:
    - by_method: 各演化方法晋升率（macro/gp/operator/deep）
    - top_operators: 高频成功算子（从 mutation_summary 提取 `identifier(` 模式）
    - top_window_bins: 高频窗口参数区间（从 mutation_summary 提取整数分箱）

防过拟合五重控制（计划 §6.4）:
    1. soft 偏向：仅注入 prompt 作参考，不硬编码采样概率
    2. 时间衰减：近期权重 > 远期（weight = decay ** days_ago）
    3. 滚动窗口：默认 14 天，窗口外模式不参与
    4. 配置开关：evolution_success_pattern_enabled 默认 true，可即时关闭
    5. 样本下限：min_sample=10，样本不足返回空报告（不注入）

降级: 数据缺失/解析异常 → 空报告，不阻断演化。

版本: v1.0.0（Phase 1.2，与 FTS 同步）
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

# ─── 契约 ─────────────────────────────────────────────────


@dataclass
class PromotionStat:
    """某演化方法的晋升统计。"""

    promoted: float  # 加权晋升数（时间衰减）
    evaluated: float  # 加权评估数（成功 + 失败）
    rate: float  # promoted / evaluated


@dataclass
class SuccessPatternConfig:
    """成功模式统计配置。"""

    enabled: bool = True  # 总开关（FTSConfig.evolution_success_pattern_enabled）
    window_days: int = 14
    decay: float = 0.9
    min_sample: int = 10  # 样本 < 阈值 → 空报告（不注入）
    max_operators: int = 5


@dataclass
class SuccessPatternReport:
    """成功模式统计报告（供 LLM prompt 注入）。"""

    window_days: int = 0
    decay: float = 0.9
    by_method: dict[str, PromotionStat] = field(default_factory=dict)
    top_operators: list[str] = field(default_factory=list)
    top_window_bins: list[str] = field(default_factory=list)
    sample_count: int = 0  # 统计样本数（< min_sample 返回空报告）


# ─── 窗口分箱 ─────────────────────────────────────────────

# 窗口参数区间（从 summary 提取的整数窗口值 → 区间标签）
_WINDOW_BINS: list[tuple[int, int, str]] = [
    (1, 5, "1-5"),
    (6, 10, "6-10"),
    (11, 20, "11-20"),
    (21, 40, "21-40"),
    (41, 80, "41-80"),
]

# 算子提取：`identifier(` 模式；过滤 Python 关键字与常见噪音
_OP_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(")
_OP_STOP_WORDS = {
    "if",
    "for",
    "while",
    "def",
    "return",
    "lambda",
    "print",
    "assert",
    "raise",
    "import",
    "np",
    "pd",
    "get",
    "set",
    "len",
    "range",
    "dict",
    "list",
    "str",
    "int",
    "float",
}


def _bin_window(value: int) -> Optional[str]:
    """窗口整数 → 区间标签；超界/非窗口值返回 None。"""
    if value <= 0 or value > 300:
        return None
    for lo, hi, label in _WINDOW_BINS:
        if lo <= value <= hi:
            return label
    return "81+"


def _extract_operators(summary: str) -> list[str]:
    """从 mutation_summary 提取算子名（`identifier(` 模式），过滤噪音。"""
    return [
        m.group(1)
        for m in _OP_RE.finditer(summary)
        if m.group(1) not in _OP_STOP_WORDS
    ]


def _extract_window_values(summary: str) -> list[int]:
    """从 mutation_summary 提取窗口整数参数。"""
    return [int(x) for x in re.findall(r"\b(\d{1,3})\b", summary)]


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """解析 recorded_at ISO 时间；非法返回 None（降级跳过）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ─── 核心聚合 ─────────────────────────────────────────────


def analyze_success_patterns(
    chain: object,
    config: SuccessPatternConfig | None = None,
) -> SuccessPatternReport:
    """从经验链聚合近期成功模式（滚动窗口 + 时间衰减）。

    Args:
        chain: ExperienceChain 实例（duck typing：需 read_all_success/read_all_failure）
        config: 统计配置；None 使用默认

    Returns:
        SuccessPatternReport；样本不足/无数据返回空报告（字段全空）
    """
    config = config or SuccessPatternConfig()
    report = SuccessPatternReport(window_days=config.window_days, decay=config.decay)
    if not config.enabled:
        return report

    try:
        success_traces = list(chain.read_all_success() or [])
        failure_traces = list(chain.read_all_failure() or [])
    except Exception:  # noqa: BLE001 — 数据读取异常降级为空报告
        return report

    now = datetime.now()
    cutoff = now - timedelta(days=config.window_days)

    # 按时间窗口过滤 + 时间衰减加权
    def _weighted(trace: dict) -> Optional[float]:
        ts = _parse_ts(trace.get("recorded_at"))
        if ts is None or ts < cutoff:
            return None
        days_ago = max(0.0, (now - ts).total_seconds() / 86400.0)
        return config.decay ** days_ago

    success_weighted: list[tuple[dict, float]] = []
    failure_weighted: list[tuple[dict, float]] = []

    for t in success_traces:
        w = _weighted(t)
        if w is not None:
            success_weighted.append((t, w))
    for t in failure_traces:
        w = _weighted(t)
        if w is not None:
            failure_weighted.append((t, w))

    report.sample_count = len(success_weighted)
    if report.sample_count < config.min_sample:
        # 样本不足 → 完全空报告（sample_count=0，prompt 层不注入）
        report.sample_count = 0
        return report

    # by_method 晋升率（加权）
    promoted_by_method: Counter = Counter()
    evaluated_by_method: Counter = Counter()
    for t, w in success_weighted:
        method = t.get("mutation_type") or "unknown"
        promoted_by_method[method] += w
        evaluated_by_method[method] += w
    for t, w in failure_weighted:
        method = t.get("mutation_type") or "unknown"
        evaluated_by_method[method] += w

    report.by_method = {
        method: PromotionStat(
            promoted=promoted_by_method[method],
            evaluated=evaluated_by_method[method],
            rate=(
                promoted_by_method[method] / evaluated_by_method[method]
                if evaluated_by_method[method] > 0
                else 0.0
            ),
        )
        for method in evaluated_by_method
    }

    # top_operators（加权计频）
    op_counter: Counter = Counter()
    for t, w in success_weighted:
        for op in _extract_operators(t.get("mutation_summary", "")):
            op_counter[op] += w
    report.top_operators = [op for op, _ in op_counter.most_common(config.max_operators)]

    # top_window_bins（加权计频）
    bin_counter: Counter = Counter()
    for t, w in success_weighted:
        for value in _extract_window_values(t.get("mutation_summary", "")):
            label = _bin_window(value)
            if label is not None:
                bin_counter[label] += w
    report.top_window_bins = [label for label, _ in bin_counter.most_common(5)]

    return report


def format_report_for_llm(report: SuccessPatternReport | None) -> str:
    """格式化成功模式报告为 LLM prompt 段落；空报告返回空串（不注入）。"""
    if report is None or report.sample_count == 0:
        return ""
    lines = [
        "\n=== 近期成功模式（参考，非硬性约束） ===",
        f"近 {report.window_days} 天成功轨迹样本: {report.sample_count}",
    ]
    if report.by_method:
        method_lines = []
        for method, stat in sorted(report.by_method.items(), key=lambda kv: kv[1].rate, reverse=True):
            method_lines.append(
                f"{method}(晋升率 {stat.rate:.0%}, 晋升 {stat.promoted:.1f}/评估 {stat.evaluated:.1f})"
            )
        lines.append("演化方法晋升率: " + "、".join(method_lines))
    if report.top_operators:
        lines.append("高频成功算子: " + ", ".join(report.top_operators))
    if report.top_window_bins:
        lines.append("高频窗口区间: " + ", ".join(report.top_window_bins))
    lines.append("仅作参考，逻辑改动须保持可解释性。")
    return "\n".join(lines)


__all__ = [
    "PromotionStat",
    "SuccessPatternConfig",
    "SuccessPatternReport",
    "analyze_success_patterns",
    "format_report_for_llm",
]
