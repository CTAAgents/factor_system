"""
fts.factor_engine.qa.quarterly_check — 季度全量复检 F1-F6（CTA 手册 6.6）。

对照《期货CTA多因子策略标准化作业手册》6.6 季度全量复检:
    F1 全样本 IC/IR 重算    （与入库时基准值对比）
    F2 分层收益重测         （与入库时基准对比）
    F3 参数最优性验证       （参数偏移 > 1 档则标记）
    F4 因子相关性矩阵更新   （新增高相关对 > 0.6 需正交化处理）
    F5 Regime 条件 IC 更新  （条件 IC 变化 > 50% 则标记）
    F6 板块拆解更新         （板块方向一致性变化则标记）

各复检项输入为"入库基准 vs 当前重算"的对比值，由调用方用 evaluation_chain 等
重算后传入。纯函数 / 缺省不判失败。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# F4: 新增高相关对阈值
CORR_HIGH_THRESHOLD = 0.6
# F5: 条件 IC 变化标记阈值
COND_IC_CHANGE_THRESHOLD = 0.50
# F3: 参数档位偏移标记阈值
PARAM_STEP_THRESHOLD = 1

QUARTERLY_ITEMS: list[str] = ["F1", "F2", "F3", "F4", "F5", "F6"]


def quarterly_recheck(
    ic_ir_ratio: Optional[float] = None,
    layered_ratio: Optional[float] = None,
    param_steps: Optional[int] = None,
    new_high_corr_pairs: Optional[int] = None,
    cond_ic_change: Optional[float] = None,
    sector_consistent: Optional[bool] = None,
) -> dict:
    """季度全量复检（手册 6.6 F1-F6）。

    Args:
        ic_ir_ratio: 当前全样本 IC/IR 与入库基准的比值（<1 表示衰减）
        layered_ratio: 当前分层多空收益与入库基准比值（<1 表示衰减）
        param_steps: 当前参数相对入库最优参数的档位偏移（>1 档标记）
        new_high_corr_pairs: 新增高相关（>0.6）因子对数量（>0 标记）
        cond_ic_change: Regime 条件 IC 相对入库的变化率（>50% 标记）
        sector_consistent: 板块 IC 方向是否仍一致（False 标记）

    Returns:
        dict: {
            indicators: {F1..F6: {passed, flagged, detail}},
            flagged_count, passed, reasons: [str],
        }
    """
    ind: dict[str, dict] = {}

    def _item(name: str, flagged: bool, detail: str) -> None:
        ind[name] = {"passed": not flagged, "flagged": flagged, "detail": detail}

    if ic_ir_ratio is None:
        _item("F1", False, "全样本 IC/IR 重算数据缺失，无法判定")
    else:
        _item("F1", ic_ir_ratio < 0.8, f"全样本 IC/IR 与入库基准比值={ic_ir_ratio:.2f}（<0.8 标记）")

    if layered_ratio is None:
        _item("F2", False, "分层收益重测数据缺失，无法判定")
    else:
        _item("F2", layered_ratio < 0.8, f"分层多空收益与入库基准比值={layered_ratio:.2f}（<0.8 标记）")

    if param_steps is None:
        _item("F3", False, "参数最优性验证数据缺失，无法判定")
    else:
        _item(
            "F3",
            param_steps > PARAM_STEP_THRESHOLD,
            f"参数档位偏移={param_steps} 档（> {PARAM_STEP_THRESHOLD} 档标记）",
        )

    if new_high_corr_pairs is None:
        _item("F4", False, "相关性矩阵更新数据缺失，无法判定")
    else:
        _item(
            "F4",
            new_high_corr_pairs > 0,
            f"新增高相关（>{CORR_HIGH_THRESHOLD:.0%}）对={new_high_corr_pairs}（>0 需正交化）",
        )

    if cond_ic_change is None:
        _item("F5", False, "Regime 条件 IC 更新数据缺失，无法判定")
    else:
        _item(
            "F5",
            abs(cond_ic_change) > COND_IC_CHANGE_THRESHOLD,
            f"条件 IC 变化率={cond_ic_change:.1%}（> {COND_IC_CHANGE_THRESHOLD:.0%} 标记）",
        )

    if sector_consistent is None:
        _item("F6", False, "板块拆解更新数据缺失，无法判定")
    else:
        _item("F6", not sector_consistent, f"板块 IC 方向一致性={'一致' if sector_consistent else '不一致'}")

    flagged = [k for k, v in ind.items() if v["flagged"]]
    return {
        "indicators": ind,
        "flagged_count": len(flagged),
        "passed": len(flagged) == 0,
        "flagged_items": flagged,
        "reasons": [f"{k}: {ind[k]['detail']}" for k in flagged],
    }


__all__ = [
    "QUARTERLY_ITEMS",
    "CORR_HIGH_THRESHOLD",
    "COND_IC_CHANGE_THRESHOLD",
    "PARAM_STEP_THRESHOLD",
    "quarterly_recheck",
]
