"""
fts.factor_engine.qa — 因子质检工作流程（CTA 手册第六章，v1.3）。

对照《期货CTA多因子策略标准化作业手册》第六章「因子质检工作流程」:
四段闭环质检 SOP（入库前质检 → 准入评估 → 定期复检 → 退役判定），
对应手册模块化架构 ``cta_factor_system/qa/`` 的 8 个文件:
    - pre_entry       入库前质检 Q1-Q10 统一执行器（Q1-Q3 一票否决）
    - admission       三级准入分类（核心库/候选库/淘汰 + 权重上限）
    - report_template 9 部分标准《因子质检报告》生成
    - monthly_check   月度滚动复检 M1-M5 + 分级处置路径
    - quarterly_check 季度全量复检 F1-F6
    - semi_annual     半年度深度复检 D1-D4
    - retirement      退役判定 5 条红线 + 退役流程
    - status_board    因子 7 状态机 + 质检状态看板（可接入 factor_db 落库）

核心原则（手册 6.1）:
    - 不质检不入库 / 不复检不续役 / 不审批不准入

版本: v1.0.0
"""

from .admission import (
    ADMISSION_LEVELS,
    admission_summary,
    classify_admission,
    max_weight_for,
)
from .monthly_check import MONTHLY_INDICATORS, monthly_recheck
from .pre_entry import QA_ITEMS, QaItem, run_pre_entry_qa
from .quarterly_check import QUARTERLY_ITEMS, quarterly_recheck
from .report_template import REPORT_SECTIONS, generate_qa_report
from .retirement import RETIREMENT_REDLINES, check_retirement
from .semi_annual import SEMI_ANNUAL_ITEMS, semi_annual_recheck
from .status_board import (
    FactorStatus,
    STATUS_TRANSITIONS,
    apply_status_transition,
    can_transition,
    status_board,
)

__all__ = [
    # pre_entry
    "QaItem",
    "QA_ITEMS",
    "run_pre_entry_qa",
    # admission
    "ADMISSION_LEVELS",
    "classify_admission",
    "max_weight_for",
    "admission_summary",
    # report_template
    "REPORT_SECTIONS",
    "generate_qa_report",
    # monthly_check
    "MONTHLY_INDICATORS",
    "monthly_recheck",
    # quarterly_check
    "QUARTERLY_ITEMS",
    "quarterly_recheck",
    # semi_annual
    "SEMI_ANNUAL_ITEMS",
    "semi_annual_recheck",
    # retirement
    "RETIREMENT_REDLINES",
    "check_retirement",
    # status_board
    "FactorStatus",
    "STATUS_TRANSITIONS",
    "can_transition",
    "status_board",
    "apply_status_transition",
]
