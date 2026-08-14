"""
fts.factor_engine.qa.retirement — 退役判定与处置（CTA 手册 6.7）。

对照《期货CTA多因子策略标准化作业手册》6.7 退役红线（满足任一即触发退役流程）:
    红线1 连续 3 个月度复检预警（因子持续衰减，非短期波动）
    红线2 滚动 60 日 IC 均值较入库时下降 > 50%（预测能力实质性丧失）
    红线3 IR 跌破 0.15（所有因子类别统一底线）
    红线4 半年度深度复检经济学逻辑失效（底层假设已不成立）
    红线5 因子代码依赖的数据源永久中断（如品种退市、数据接口关闭）

退役流程: 生成退役报告 → 策略负责人签字 → 权重归零/移出生产组合/移入淘汰库
→ 更新因子注册表状态 RETIRED → 通知组合管理层风险平价重算。

纯函数 / NaN 兜底（关键指标缺失时对应红线不触发，避免误退役）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional


logger = logging.getLogger(__name__)

# 红线2: 60 日 IC 较入库时下降阈值
IC_DECAY_RETIRE = 0.50
# 红线3: IR 统一底线
IR_FLOOR_RETIRE = 0.15

RETIREMENT_REDLINES: list[dict] = [
    {"id": "R1", "name": "连续3个月度复检预警", "detail": "因子持续衰减，非短期波动"},
    {"id": "R2", "name": "60日IC较入库时下降>50%", "detail": "因子预测能力实质性丧失"},
    {"id": "R3", "name": "IR跌破0.15", "detail": "低于所有因子类别统一底线"},
    {"id": "R4", "name": "经济学逻辑失效", "detail": "因子底层假设已不成立"},
    {"id": "R5", "name": "数据源永久中断", "detail": "如品种退市、数据接口关闭"},
]


def check_retirement(
    consecutive_warn_months: int = 0,
    current_ic60: Optional[float] = None,
    entry_ic60: Optional[float] = None,
    ir60: Optional[float] = None,
    logic_valid: bool = True,
    data_source_alive: bool = True,
) -> dict:
    """退役红线判定（手册 6.7）。

    Args:
        consecutive_warn_months: 连续月度复检预警月数
        current_ic60: 当前滚动 60 日 IC 均值
        entry_ic60: 入库时 60 日 IC 均值
        ir60: 当前滚动 60 日 IR（年化）
        logic_valid: 半年度深度复检经济学逻辑是否成立
        data_source_alive: 因子依赖数据源是否仍可用

    Returns:
        dict: {
            redlines: [{id, name, triggered, detail}],
            triggered: bool,
            triggered_ids: [str],
            action: "retain" | "retire",
            report: str,
        }
    """
    redlines = []
    for spec in RETIREMENT_REDLINES:
        rid = spec["id"]
        if rid == "R1":
            trig = consecutive_warn_months >= 3
        elif rid == "R2":
            trig = (
                current_ic60 is not None
                and entry_ic60 is not None
                and abs(entry_ic60) > 1e-12
                and (entry_ic60 - current_ic60) / abs(entry_ic60) > IC_DECAY_RETIRE
            )
        elif rid == "R3":
            trig = ir60 is not None and ir60 < IR_FLOOR_RETIRE
        elif rid == "R4":
            trig = not logic_valid
        else:  # R5
            trig = not data_source_alive
        redlines.append({"id": rid, "name": spec["name"], "triggered": bool(trig), "detail": spec["detail"]})

    triggered = [r["id"] for r in redlines if r["triggered"]]
    action = "retire" if triggered else "retain"
    lines = ["退役判定（5 条红线）: " + ("触发退役" if triggered else "维持服役")]
    for r in redlines:
        mark = "TRIGGER" if r["triggered"] else "OK"
        lines.append(f"  [{r['id']}] {r['name']} {mark} — {r['detail']}")
    if triggered:
        lines.append("  退役流程: 生成退役报告 → 负责人签字 → 权重归零/移入淘汰库 → 状态 RETIRED → 组合权重重算")

    return {
        "redlines": redlines,
        "triggered": bool(triggered),
        "triggered_ids": triggered,
        "action": action,
        "report": "\n".join(lines),
    }


__all__ = [
    "RETIREMENT_REDLINES",
    "IC_DECAY_RETIRE",
    "IR_FLOOR_RETIRE",
    "check_retirement",
]
