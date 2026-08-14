"""test_qa_report_template — 9 部分质检报告生成测试（CTA 手册 6.4）。"""

from __future__ import annotations

from fts.factor_engine.qa.report_template import REPORT_SECTIONS, generate_qa_report


def test_report_has_nine_sections() -> None:
    """报告包含 9 个部分章节。"""
    assert len(REPORT_SECTIONS) == 9
    assert "准入结论" in REPORT_SECTIONS


def test_report_full_factor() -> None:
    """完整因子信息生成报告。"""
    factor = {
        "name": "fut_mom_20",
        "category": "量价技术",
        "researcher": "张三",
        "date": "2026-08-14",
        "formula": "ret(close,20)",
        "logic": "动量延续",
        "environment": "趋势市",
        "ir": 0.45,
        "ir_gate": 0.3,
        "perm_p": 0.001,
        "decay_ratio": "12%",
        "approver": "李四",
    }
    adm = {"score": 4.2, "label": "核心库", "max_weight": 0.30}
    text = generate_qa_report(factor, admission=adm)
    assert "因子名称：fut_mom_20" in text
    assert "核心库" in text
    assert "一、因子基本信息" in text
    assert "九、准入结论" in text


def test_report_with_qa_detail() -> None:
    """Q1-Q10 明细嵌入报告。"""
    qa = {
        "items": [
            {"qid": "Q1", "name": "未来函数检测", "passed": True, "detail": "shift 校验通过"},
            {"qid": "Q5", "name": "IR 分类门槛", "passed": False, "detail": "IR 0.2 < 0.3"},
        ]
    }
    text = generate_qa_report({}, qa_result=qa)
    assert "[Q1] 未来函数检测 PASS" in text
    assert "[Q5] IR 分类门槛 FAIL" in text


def test_report_empty_defaults() -> None:
    """缺省字段用占位符兜底，不崩溃。"""
    text = generate_qa_report({})
    assert "因子名称：____" in text
    assert "准入等级：____" in text


def test_report_params_block() -> None:
    """参数遍历结果章节渲染。"""
    p = {"grid": "N=5/10/20", "best": "N=10", "decay": "8%", "conclusion": "稳定"}
    text = generate_qa_report({}, params=p)
    assert "N=10" in text
    assert "稳定" in text
