"""
fts.factor_engine.qa.report_template — 9 部分《因子质检报告》生成（CTA 手册 6.4）。

对照《期货CTA多因子策略标准化作业手册》6.4 因子质检报告模板:
    一、因子基本信息    二、经济学逻辑      三、参数遍历结果
    四、IC/IR 统计      五、分层收益测试    六、板块拆解
    七、Regime 条件 IC  八、过拟合检测      九、准入结论

生成 Markdown 格式报告文本，与因子代码一同归档。纯函数 / 缺省兜底。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 报告九部分章节定义
REPORT_SECTIONS: list[str] = [
    "因子基本信息",
    "经济学逻辑",
    "参数遍历结果",
    "IC/IR 统计",
    "分层收益测试",
    "板块拆解",
    "Regime 条件 IC",
    "过拟合检测",
    "准入结论",
]


def _kv(key: str, value: Any, default: str = "____") -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return f"  · {key}：{default}"
    return f"  · {key}：{value}"


def generate_qa_report(
    factor: dict[str, Any],
    qa_result: dict[str, Any] | None = None,
    admission: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """生成标准化《因子质检报告》（手册 6.4 模板，9 部分）。

    Args:
        factor: 因子基本信息（name/category/researcher/date/params 等）
        qa_result: run_pre_entry_qa 输出（Q1-Q10 汇总）
        admission: admission_summary 输出（准入结论）
        params: 参数遍历结果（最优参数/敏感度等）

    Returns:
        str: Markdown 报告文本
    """
    qa = qa_result or {}
    adm = admission or {}
    p = params or {}

    items_block = ""
    for it in qa.get("items", []):
        mark = "PASS" if it.get("passed") else "FAIL"
        items_block += f"    [{it.get('qid')}] {it.get('name')} {mark} — {it.get('detail', '')}\n"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "            《因子质检报告》",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "一、因子基本信息",
        _kv("因子名称", factor.get("name")),
        _kv("因子类别", factor.get("category")),
        _kv("研发人", factor.get("researcher")),
        _kv("提交日期", factor.get("date")),
        _kv("参数设置", factor.get("params")),
        "",
        "二、经济学逻辑",
        _kv("核心公式", factor.get("formula")),
        _kv("经济学解释", factor.get("logic")),
        _kv("适用行情环境", factor.get("environment")),
        _kv("预期信号周期", factor.get("signal_cycle")),
        "",
        "三、参数遍历结果",
        _kv("参数网格", p.get("grid")),
        _kv("最优参数", p.get("best")),
        _kv("参数敏感度（±20% 扰动后绩效衰减）", p.get("decay")),
        _kv("结论", p.get("conclusion")),
        "",
        "四、IC/IR 统计",
        _kv("IC 均值", factor.get("ic_mean")),
        _kv("IC 标准差", factor.get("ic_std")),
        _kv("IC 胜率", factor.get("ic_win_rate")),
        _kv("IR", factor.get("ir")),
        _kv("分类门槛", factor.get("ir_gate")),
        _kv("样本期", factor.get("sample_period")),
        _kv("样本量（日）", factor.get("sample_size")),
        _kv("品种池规模", factor.get("pool_size")),
        "",
        "五、分层收益测试",
        _kv("Top1 组年化收益", factor.get("top1_annual")),
        _kv("Bottom1 组年化收益", factor.get("bottom1_annual")),
        _kv("多空对冲年化收益", factor.get("hedge_annual")),
        _kv("多空对冲夏普", factor.get("hedge_sharpe")),
        _kv("最大回撤", factor.get("max_drawdown")),
        _kv("分层净值单调性", factor.get("monotonic")),
        "",
        "六、板块拆解",
        _kv("黑色 IC", factor.get("sector_black")),
        _kv("能化 IC", factor.get("sector_energy")),
        _kv("农产品 IC", factor.get("sector_agri")),
        _kv("有色 IC", factor.get("sector_metal")),
        _kv("方向一致性", factor.get("sector_consistency")),
        "",
        "七、Regime 条件 IC",
        _kv("IC|趋势市", factor.get("ic_trend")),
        _kv("IC|震荡市", factor.get("ic_oscillation")),
        _kv("IC|过渡期", factor.get("ic_transition")),
        _kv("最适 Regime", factor.get("best_regime")),
        "",
        "八、过拟合检测",
        _kv("置换检验 p 值", factor.get("perm_p")),
        _kv("样本内外夏普落差", factor.get("decay_ratio")),
        _kv("分时段绩效拆解", factor.get("period_consistency")),
        "",
        "九、准入结论",
        _kv("综合得分", adm.get("score")),
        _kv("准入等级", adm.get("label")),
        _kv("初始权重上限", adm.get("max_weight")),
        _kv("策略负责人签字", factor.get("approver")),
        _kv("日期", factor.get("approve_date")),
    ]
    if items_block:
        lines += ["", "入库前质检明细（Q1-Q10）", items_block.rstrip()]
    return "\n".join(lines)


__all__ = ["REPORT_SECTIONS", "generate_qa_report"]
