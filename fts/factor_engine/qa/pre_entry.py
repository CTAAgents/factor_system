"""
fts.factor_engine.qa.pre_entry — 入库前质检 Q1-Q10 统一执行器（CTA 手册 6.2）。

对照《期货CTA多因子策略标准化作业手册》6.2 入库前质检清单:
    Q1-Q3 为一票否决项（任一不合格禁止入库）:
        Q1 未来函数检测（Shift 错位校验通过）
        Q2 逻辑文档化（公式/经济学逻辑/适用行情环境均有书面文档）
        Q3 参数遍历网格（至少 3 组参数跑完，记录最优参数区间）
    Q4-Q10 为评分项（综合判定准入等级）:
        Q4 IC 均值（同向且 |IC| > 0.02 日频）
        Q5 IR 分类门槛（量价≥0.3 / 基本面≥0.4 / 期限结构≥0.35）
        Q6 分层收益单调性（Top1-Bottom1 多空净值单调向上）
        Q7 置换检验（随机打乱标签后 IC 显著降低，p < 0.05）
        Q8 极端行情 IC 验证（2020 原油负价格/2022 俄乌扰动 IC 未大幅失效）
        Q9 参数敏感度（参数 ±20% 扰动后绩效衰减 < 30%）
        Q10 板块拆解（黑色/能化/农产品/有色 IC 方向一致）

各单项的数值计算由既有模块提供（shift_leak_test/factor_document/ir_thresholds/
permutation_test/stress_ic/robustness/evaluation_chain 等），本执行器负责
按手册清单汇总判定与一票否决。纯函数 / 零未来函数 / 不判失败不崩溃。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class QaItem:
    """单项质检结果。"""

    qid: str
    name: str
    passed: bool
    detail: str = ""
    one_vote: bool = False
    extra: dict = field(default_factory=dict)


# Q1-Q10 清单定义（qid, 名称, 对应阶段, 合格标准, 是否一票否决）
QA_ITEMS: list[dict] = [
    {
        "qid": "Q1",
        "name": "未来函数检测",
        "stage": "阶段2",
        "one_vote": True,
        "criterion": "Shift 错位校验通过，因子值仅依赖 t 日及之前数据",
    },
    {
        "qid": "Q2",
        "name": "逻辑文档化",
        "stage": "阶段2",
        "one_vote": True,
        "criterion": "公式、经济学逻辑、适用行情环境（趋势/震荡）均有书面文档",
    },
    {
        "qid": "Q3",
        "name": "参数遍历网格",
        "stage": "阶段2",
        "one_vote": True,
        "criterion": "至少 3 组参数（如 N=5/10/20）跑完，记录最优参数区间",
    },
    {
        "qid": "Q4",
        "name": "IC 均值",
        "stage": "阶段4",
        "one_vote": False,
        "criterion": "IC 均值同向且 |IC| > 0.02（日频）",
    },
    {
        "qid": "Q5",
        "name": "IR 分类门槛",
        "stage": "阶段4",
        "one_vote": False,
        "criterion": "量价≥0.3 / 基本面≥0.4 / 期限结构≥0.35",
    },
    {
        "qid": "Q6",
        "name": "分层收益单调性",
        "stage": "阶段4",
        "one_vote": False,
        "criterion": "Top1-Bottom1 多空净值单调向上，无倒挂",
    },
    {
        "qid": "Q7",
        "name": "置换检验",
        "stage": "阶段4",
        "one_vote": False,
        "criterion": "随机打乱标签后 IC 显著降低（p < 0.05）",
    },
    {
        "qid": "Q8",
        "name": "极端行情 IC 验证",
        "stage": "阶段4",
        "one_vote": False,
        "criterion": "2020 原油负价格、2022 俄乌等极端期 IC 未大幅失效",
    },
    {
        "qid": "Q9",
        "name": "参数敏感度",
        "stage": "阶段9",
        "one_vote": False,
        "criterion": "参数 ±20% 扰动后绩效衰减 < 30%",
    },
    {
        "qid": "Q10",
        "name": "板块拆解",
        "stage": "阶段4",
        "one_vote": False,
        "criterion": "分板块（黑色/能化/农产品/有色）IC 方向一致",
    },
]


def run_pre_entry_qa(items: list[QaItem]) -> dict:
    """执行入库前质检判定（手册 6.2）。

    Args:
        items: Q1-Q10 各单项结果（调用方已用对应模块计算出 passed/detail）

    Returns:
        dict: {
            items: [{qid, name, passed, one_vote, detail, stage}],
            one_vote_failed: [Q 编号],
            scoring_failed: [Q 编号],
            passed_count, total, scoring_pass_ratio,
            passed: bool,   # Q1-Q3 全过且评分项通过率 ≥ 0.6
            conclusion: "禁止入库" | "待综合评定" | "可进入准入评估",
            report: str,    # 摘要文本
        }
    """
    by_qid = {it.qid: it for it in items}
    merged: list[dict] = []
    one_vote_failed: list[str] = []
    scoring_failed: list[str] = []
    for spec in QA_ITEMS:
        it = by_qid.get(spec["qid"])
        passed = it.passed if it is not None else False
        detail = it.detail if it is not None else "未执行"
        merged.append(
            {
                "qid": spec["qid"],
                "name": spec["name"],
                "stage": spec["stage"],
                "one_vote": spec["one_vote"],
                "criterion": spec["criterion"],
                "passed": bool(passed),
                "detail": detail,
            }
        )
        if spec["one_vote"] and not passed:
            one_vote_failed.append(spec["qid"])
        elif not spec["one_vote"] and not passed:
            scoring_failed.append(spec["qid"])

    total = len(merged)
    passed_count = sum(1 for m in merged if m["passed"])
    scoring_total = sum(1 for m in merged if not m["one_vote"])
    scoring_pass = sum(1 for m in merged if not m["one_vote"] and m["passed"])
    scoring_ratio = scoring_pass / scoring_total if scoring_total else 0.0

    if one_vote_failed:
        passed = False
        conclusion = "禁止入库"
    elif scoring_ratio < 0.6:
        passed = False
        conclusion = "待综合评定"
    else:
        passed = True
        conclusion = "可进入准入评估"

    lines = [f"入库前质检（{passed_count}/{total} 通过）: {conclusion}"]
    if one_vote_failed:
        lines.append(f"  一票否决未过: {', '.join(one_vote_failed)}")
    for m in merged:
        mark = "PASS" if m["passed"] else "FAIL"
        lines.append(f"  [{m['qid']}] {m['name']} {mark} — {m['detail']}")

    return {
        "items": merged,
        "one_vote_failed": one_vote_failed,
        "scoring_failed": scoring_failed,
        "passed_count": passed_count,
        "total": total,
        "scoring_pass_ratio": float(scoring_ratio),
        "passed": bool(passed),
        "conclusion": conclusion,
        "report": "\n".join(lines),
    }


__all__ = ["QaItem", "QA_ITEMS", "run_pre_entry_qa"]
