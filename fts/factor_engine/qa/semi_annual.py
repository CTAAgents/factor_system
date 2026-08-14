"""
fts.factor_engine.qa.semi_annual — 半年度深度复检 D1-D4（CTA 手册 6.6）。

对照《期货CTA多因子策略标准化作业手册》6.6 半年度深度复检（与季度合并执行）:
    D1 因子经济学逻辑复审（经济学假设是否仍成立）
    D2 全样本回测重跑     （与入库时回测结果对比）
    D3 品种池重构评估     （新品种上市/老品种退市对因子表现影响）
    D4 淘汰库复审         （已淘汰因子是否因环境变化重新有效）

纯函数 / 缺省不判失败（无法判定项不强行给结论）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

SEMI_ANNUAL_ITEMS: list[str] = ["D1", "D2", "D3", "D4"]


def semi_annual_recheck(
    logic_valid: Optional[bool] = None,
    backtest_sharpe_ratio: Optional[float] = None,
    pool_reconstructed: Optional[bool] = None,
    retired_review: Optional[dict] = None,
) -> dict:
    """半年度深度复检（手册 6.6 D1-D4）。

    Args:
        logic_valid: D1 经济学逻辑是否仍成立（策略负责人人工复审结论）
        backtest_sharpe_ratio: D2 全样本回测重跑夏普与入库时比值（<0.8 标记）
        pool_reconstructed: D3 品种池是否发生重构（新品种/退市影响因子表现）
        retired_review: D4 淘汰库复审结果 {retired_factor_id: 重新有效 or None}

    Returns:
        dict: {
            indicators: {D1..D4: {passed, flagged, detail}},
            flagged_count, passed, reasons: [str],
        }
    """
    ind: dict[str, dict] = {}

    def _item(name: str, flagged: bool, detail: str) -> None:
        ind[name] = {"passed": not flagged, "flagged": flagged, "detail": detail}

    if logic_valid is None:
        _item("D1", False, "经济学逻辑复审结论缺失，等待策略负责人人工判定")
    else:
        _item("D1", not logic_valid, "经济学逻辑复审：" + ("成立" if logic_valid else "失效，触发退役红线"))

    if backtest_sharpe_ratio is None:
        _item("D2", False, "全样本回测重跑数据缺失，无法判定")
    else:
        _item(
            "D2", backtest_sharpe_ratio < 0.8, f"全样本回测夏普与入库基准比值={backtest_sharpe_ratio:.2f}（<0.8 标记）"
        )

    if pool_reconstructed is None:
        _item("D3", False, "品种池重构评估结论缺失，无法判定")
    else:
        _item("D3", pool_reconstructed, "品种池已重构（新品种/退市），需评估因子表现影响")

    revived = []
    if retired_review:
        revived = [k for k, v in retired_review.items() if v]
        _item(
            "D4",
            False,
            f"淘汰库复审：{len(revived)} 个因子重新有效（{', '.join(revived) or '无'}），需重新走入库前质检",
        )
    else:
        _item("D4", False, "淘汰库复审结论缺失，跳过")

    flagged = [k for k, v in ind.items() if v["flagged"]]
    return {
        "indicators": ind,
        "flagged_count": len(flagged),
        "passed": len(flagged) == 0,
        "flagged_items": flagged,
        "revived_factors": revived,
        "reasons": [f"{k}: {ind[k]['detail']}" for k in flagged],
    }


__all__ = ["SEMI_ANNUAL_ITEMS", "semi_annual_recheck"]
