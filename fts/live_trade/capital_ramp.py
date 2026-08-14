"""
fts.live_trade.capital_ramp — 实盘资金三级爬坡（CTA 手册阶段11.1）。

对照《期货CTA多因子策略标准化作业手册》阶段11.1「三级上线制度」:
    | 阶段 | 资金比例 | 运行时长 | 观测重点 |
    | 小仓测试 | 10% | 30天 | 实盘冲击成本、柜台时延、节假日风控执行 |
    | 半仓运行 | 50% | 连续月度稳定 | 对比仿真偏差，验证月度稳定性 |
    | 全额上线 | 100% | 常态化 | 开启每日监控，持续迭代 |

关键纪律（阶段11 Checkpoint）:
    - 严格资金分档爬坡，不一次性满仓上线

设计约束:
    - 纯函数 / 参数配置化 / 升级判定只依赖可观测指标，不依赖未来数据

版本: v1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass

# 三级爬坡表: (阶段标识, 中文名, 资金比例, 最小运行天数)
RAMP_STAGES: list[tuple[str, str, float, int | None]] = [
    ("small", "小仓测试", 0.10, 30),
    ("half", "半仓运行", 0.50, None),
    ("full", "全额上线", 1.00, None),
]

# 半仓→全额所需的连续月度稳定次数
MIN_MONTHLY_STABLE = 1

_STAGE_INDEX: dict[str, int] = {name: i for i, (name, _, _, _) in enumerate(RAMP_STAGES)}


@dataclass
class RampStatus:
    """当前爬坡状态。"""

    stage: str
    stage_label: str
    capital_scale: float
    days_running: int
    monthly_stable: bool
    advance_ready: bool
    next_stage: str | None
    reason: str


def ramp_plan() -> list[dict]:
    """三级爬坡计划表（手册 11.1 表格的机器可读版本）。"""
    return [
        {
            "stage": name,
            "label": label,
            "capital_scale": scale,
            "min_days": days,
            "observation": (
                "冲击成本/柜台时延/节假日风控执行"
                if name == "small"
                else "对比仿真偏差/月度稳定性"
                if name == "half"
                else "每日监控/持续迭代"
            ),
        }
        for name, label, scale, days in RAMP_STAGES
    ]


def capital_scale(stage: str) -> float:
    """返回指定阶段的资金比例（未知阶段默认 0.0，安全兜底）。"""
    for name, _, scale, _ in RAMP_STAGES:
        if name == stage:
            return scale
    return 0.0


def can_advance(
    stage: str,
    days_running: int,
    monthly_stable: bool = False,
    min_days: int | None = None,
    min_monthly_stable: int = MIN_MONTHLY_STABLE,
) -> bool:
    """判断当前阶段是否满足升级条件。

    升级规则（手册 11.1）:
        - small → half: 运行天数 ≥ 30
        - half → full: 连续月度稳定 ≥ min_monthly_stable 次
        - full: 常态化，不再升级

    Args:
        stage: 当前阶段标识
        days_running: 当前阶段已运行天数
        monthly_stable: 是否已连续月度稳定（half→full 判定）
        min_days: 覆盖 small 阶段最小天数（None 用表内配置 30）
        min_monthly_stable: half→full 所需连续月度稳定次数

    Returns:
        bool: 是否可升级
    """
    idx = _STAGE_INDEX.get(stage)
    if idx is None or idx >= len(RAMP_STAGES) - 1:
        return False  # 未知阶段或已处于全额上线
    _, _, _, table_days = RAMP_STAGES[idx]
    if stage == "small":
        need = min_days if min_days is not None else (table_days or 30)
        return days_running >= need
    if stage == "half":
        return monthly_stable and min_monthly_stable >= 1
    return False


def ramp_status(stage: str, days_running: int, monthly_stable: bool = False) -> RampStatus:
    """汇总当前爬坡状态与升级建议。

    Args:
        stage: 当前阶段标识
        days_running: 当前阶段已运行天数
        monthly_stable: 是否已连续月度稳定

    Returns:
        RampStatus
    """
    idx = _STAGE_INDEX.get(stage)
    if idx is None:
        return RampStatus(stage, "未知", 0.0, days_running, monthly_stable, False, None, "未知阶段，禁止按全额运行")
    name, label, scale, _ = RAMP_STAGES[idx]
    advance = can_advance(stage, days_running, monthly_stable)
    if idx < len(RAMP_STAGES) - 1:
        next_stage = RAMP_STAGES[idx + 1][0]
        if not advance:
            if stage == "small":
                reason = f"小仓运行不满 30 天（当前 {days_running} 天），维持 {label}"
            else:
                reason = f"尚未满足连续月度稳定，维持 {label}"
        else:
            reason = f"满足升级条件，可进入 {RAMP_STAGES[idx + 1][1]}"
    else:
        next_stage = None
        reason = "已全额上线，进入常态化每日监控与迭代"
    return RampStatus(stage, label, scale, days_running, monthly_stable, advance, next_stage, reason)


__all__ = [
    "RAMP_STAGES",
    "MIN_MONTHLY_STABLE",
    "RampStatus",
    "ramp_plan",
    "capital_scale",
    "can_advance",
    "ramp_status",
]
