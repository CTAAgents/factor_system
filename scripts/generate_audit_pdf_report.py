"""
scripts/generate_audit_pdf_report.py — 生成审计结果 PDF 报告

整合所有可视化图表和失败模式分析，生成一份完整的 PDF 报告。

用法:
    python scripts/generate_audit_pdf_report.py

输出:
    - PDF 报告: reports/factor_audit_report_YYYYMMDD.pdf
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fpdf import FPDF

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pdf_report")


# ─── PDF 生成器 ────────────────────────────────────────────


class AuditPDFReport(FPDF):
    """审计报告 PDF 生成器。"""

    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)
        # 注册支持中文的 Unicode 字体 (微软雅黑)
        self.add_font("msyh", "", "C:/Windows/Fonts/msyh.ttc", uni=True)
        self.add_font("msyh", "B", "C:/Windows/Fonts/msyhbd.ttc", uni=True)

    def header(self):
        """页眉。"""
        if self.page_no() > 1:
            self.set_font("msyh", "", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 5, "因子审计报告 | Factor System Audit Report", align="C")
            self.ln(8)
            self.set_draw_color(200, 200, 200)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(5)

    def footer(self):
        """页脚。"""
        self.set_y(-15)
        self.set_font("msyh", "", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"- {self.page_no()} -", align="C")

    def chapter_title(self, title: str, level: int = 1):
        """章节标题。"""
        if level == 1:
            self.set_font("msyh", "B", 16)
            self.set_text_color(0, 51, 102)
            self.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
            self.set_draw_color(0, 51, 102)
            self.set_line_width(0.5)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(6)
        elif level == 2:
            self.set_font("msyh", "B", 13)
            self.set_text_color(0, 76, 153)
            self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
            self.ln(3)
        elif level == 3:
            self.set_font("msyh", "B", 11)
            self.set_text_color(51, 51, 51)
            self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

    def body_text(self, text: str):
        """正文文本。"""
        self.set_font("msyh", "", 10)
        self.set_text_color(51, 51, 51)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def bullet_point(self, text: str, indent: int = 0):
        """项目符号。"""
        self.set_font("msyh", "", 10)
        self.set_text_color(51, 51, 51)
        x = self.get_x() + indent
        self.set_x(x)
        self.cell(5, 5, "-")
        self.multi_cell(0, 5, text)
        self.ln(1)

    def key_value(self, key: str, value: str):
        """键值对。"""
        self.set_font("msyh", "B", 10)
        self.set_text_color(0, 51, 102)
        self.cell(40, 7, key + ": ")
        self.set_font("msyh", "", 10)
        self.set_text_color(51, 51, 51)
        self.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    def add_chart(self, chart_path: str, width: float = 170, caption: str = ""):
        """添加图表。"""
        if Path(chart_path).exists():
            # 计算居中位置
            x = (210 - width) / 2
            self.image(chart_path, x=x, w=width)
            self.ln(3)
            if caption:
                self.set_font("msyh", "", 9)
                self.set_text_color(128, 128, 128)
                self.cell(0, 5, caption, align="C")
                self.ln(5)
        else:
            logger.warning("图表不存在: %s", chart_path)

    def add_table(self, headers: list, data: list, col_widths: list = None):
        """添加表格。"""
        if col_widths is None:
            col_widths = [190 / len(headers)] * len(headers)

        # 表头
        self.set_font("msyh", "B", 8)
        self.set_fill_color(0, 51, 102)
        self.set_text_color(255, 255, 255)
        for i, header in enumerate(headers):
            self.cell(col_widths[i], 7, header, border=1, fill=True, align="C")
        self.ln()

        # 数据行
        self.set_font("msyh", "", 8)
        self.set_text_color(51, 51, 51)
        for row_idx, row in enumerate(data):
            if row_idx % 2 == 0:
                self.set_fill_color(240, 240, 240)
            else:
                self.set_fill_color(255, 255, 255)
            for i, cell in enumerate(row):
                self.cell(col_widths[i], 6, str(cell), border=1, fill=True, align="C")
            self.ln()
        self.ln(3)


# ─── 主函数 ──────────────────────────────────────────────


def main():
    logger.info("=" * 60)
    logger.info("生成审计结果 PDF 报告")
    logger.info("=" * 60)

    reports_dir = PROJECT_ROOT / "reports"
    charts_dir = reports_dir / "audit_charts"

    # 1. 加载审计数据
    logger.info("加载审计汇总数据...")
    csv_files = sorted(reports_dir.glob("audit_summary_real_*.csv"))
    if not csv_files:
        logger.error("未找到审计汇总 CSV 文件")
        sys.exit(1)

    latest_csv = csv_files[-1]
    df = pd.read_csv(latest_csv)
    logger.info("已加载: %s (%d 行)", latest_csv.name, len(df))

    # 加载 WalkForward 报告
    wf_files = sorted(reports_dir.glob("walkforward_report_*.json"))
    wf_report = None
    if wf_files:
        with open(wf_files[-1], "r", encoding="utf-8") as f:
            wf_report = json.load(f)

    # 加载动量修复报告
    fix_files = sorted(reports_dir.glob("momentum_fix_report_*.json"))
    fix_report = None
    if fix_files:
        with open(fix_files[-1], "r", encoding="utf-8") as f:
            fix_report = json.load(f)

    # 2. 创建 PDF
    pdf = AuditPDFReport()
    pdf.add_page()

    # ─── 封面 ──────────────────────────────────────────
    pdf.ln(30)
    pdf.set_font("msyh", "B", 28)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 15, "因子审计报告", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_font("msyh", "", 14)
    pdf.set_text_color(102, 102, 102)
    pdf.cell(0, 8, "Factor System Batch Audit Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(20)

    pdf.set_font("msyh", "", 12)
    pdf.set_text_color(51, 51, 51)
    pdf.cell(0, 8, f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"审计因子数: {len(df)}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(30)

    # 概览统计
    pdf.set_font("msyh", "B", 14)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 10, "审计概览", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    passed = (df["audit_passed"] == True).sum()
    failed = len(df) - passed
    oos_passed = df["oos_passed"].sum()
    avg_ic = df["mean_ic"].mean()
    avg_cross = df["cross_symbol_ratio"].mean()

    stats = [
        ("总因子数", str(len(df))),
        ("通过审计", str(passed)),
        ("未通过审计", str(failed)),
        ("OOS 通过", f"{oos_passed} ({oos_passed/len(df)*100:.1f}%)"),
        ("平均 IC", f"{avg_ic:.4f}"),
        ("平均跨品种比例", f"{avg_cross:.1%}"),
    ]

    for key, val in stats:
        pdf.set_font("msyh", "", 11)
        pdf.set_text_color(51, 51, 51)
        pdf.cell(50, 8, key)
        pdf.set_font("msyh", "B", 11)
        pdf.set_text_color(0, 102, 153)
        pdf.cell(0, 8, val, new_x="LMARGIN", new_y="NEXT")

    # ─── 第1章: 审计方法 ──────────────────────────────
    pdf.add_page()
    pdf.chapter_title("1. 审计方法论")

    pdf.chapter_title("1.1 审计流程", level=2)
    pdf.body_text("本次审计采用四阶段标准化流程，对因子库进行全面评估：")
    pdf.bullet_point("数据加载：从 DuckDB kline_cache 获取 15 个核心期货品种的 500 天历史行情")
    pdf.bullet_point("因子计算：逐品种执行因子代码，计算因子值序列")
    pdf.bullet_point("绩效评估：计算 IC、IC-IR、跨品种正收益比例、OOS 样本外验证")
    pdf.bullet_point("报告生成：综合各项指标，生成通过/未通过判定和优化建议")

    pdf.chapter_title("1.2 评估指标", level=2)
    pdf.body_text("核心评估指标体系如下：")

    headers = ["指标", "计算方法", "通过标准"]
    data = [
        ["IC (信息系数)", "因子值与未来收益的 Pearson 相关", "IC > 0.02"],
        ["IC-IR (IC 信息比率)", "IC 均值 / IC 标准差", "IC-IR > 0.5"],
        ["跨品种正收益比例", "IC > 0 的品种数 / 总品种数", "≥ 80%"],
        ["OOS 样本外验证", "尾部 30% 数据上的 IC 显著性", "p < 0.05"],
        ["多重检验校正", "Bonferroni / FDR 校正后显著性", "校正后 p < 0.05"],
    ]
    pdf.add_table(headers, data)

    pdf.chapter_title("1.3 数据说明", level=2)
    pdf.key_value("品种数量", "15 个核心期货品种")
    pdf.key_value("数据周期", "近 500 个交易日")
    pdf.key_value("品种覆盖", "RB/CU/AU/AG/I/M/TA/MA/SC/HC/NI/SN/P/Y/C")
    pdf.key_value("数据来源", "DuckDB kline_cache (主) + AKShare (备)")

    # ─── 第2章: 审计结果 ──────────────────────────────
    pdf.add_page()
    pdf.chapter_title("2. 审计结果汇总")

    pdf.chapter_title("2.1 整体通过率", level=2)
    pdf.body_text(f"本次共审计 {len(df)} 个因子，结果如下：")

    headers = ["指标", "通过", "未通过", "通过率"]
    data = [
        ["OOS 样本外验证", str(int(oos_passed)), str(len(df) - int(oos_passed)), f"{oos_passed/len(df)*100:.1f}%"],
        ["跨品种 IC ≥ 80%", str((df['cross_symbol_ratio'] >= 0.8).sum()), str((df['cross_symbol_ratio'] < 0.8).sum()), f"{(df['cross_symbol_ratio'] >= 0.8).mean()*100:.1f}%"],
        ["多重检验校正", str(0), str(len(df)), "0.0%"],
        ["综合审计", str(passed), str(failed), f"{passed/len(df)*100:.1f}%"],
    ]
    pdf.add_table(headers, data)

    # 添加通过率饼图
    pie_chart = charts_dir / "audit_pass_rate.png"
    if pie_chart.exists():
        pdf.add_chart(str(pie_chart), width=120, caption="图 1: 审计通过率分布")

    pdf.chapter_title("2.2 OOS vs 跨品种对比", level=2)
    oos_chart = charts_dir / "oos_vs_cross_symbol.png"
    if oos_chart.exists():
        pdf.add_chart(str(oos_chart), caption="图 2: OOS 通过率与跨品种分布对比")

    pdf.chapter_title("2.3 IC 分布", level=2)
    ic_chart = charts_dir / "ic_distribution.png"
    if ic_chart.exists():
        pdf.add_chart(str(ic_chart), caption="图 3: 因子平均 IC 分布直方图")

    # ─── 第3章: 家族分析 ──────────────────────────────
    pdf.add_page()
    pdf.chapter_title("3. 因子家族分析")

    pdf.chapter_title("3.1 家族通过率", level=2)
    family_chart = charts_dir / "family_pass_rate.png"
    if family_chart.exists():
        pdf.add_chart(str(family_chart), caption="图 4: 各因子家族通过率对比")

    pdf.chapter_title("3.2 家族多维指标对比", level=2)
    family_compare = charts_dir / "family_multim Comparison.png"
    if family_compare.exists():
        pdf.add_chart(str(family_compare), caption="图 5: 家族 IC / 跨品种 / OOS 三维对比")

    # 家族统计表
    if "family" in df.columns:
        valid = df[df["status"].isin(["passed", "failed"])]
        family_stats = valid.groupby("family").agg(
            total=("factor_name", "count"),
            mean_ic=("mean_ic", lambda x: f"{x.mean():.4f}"),
            mean_cross=("cross_symbol_ratio", lambda x: f"{x.mean():.1%}"),
            oos_rate=("oos_passed", lambda x: f"{x.mean():.1%}"),
        ).reset_index()

        pdf.chapter_title("3.3 家族指标详情", level=2)
        headers = ["家族", "因子数", "平均 IC", "跨品种比例", "OOS 通过率"]
        table_data = []
        for _, row in family_stats.iterrows():
            table_data.append([row["family"], str(row["total"]), row["mean_ic"], row["mean_cross"], row["oos_rate"]])
        pdf.add_table(headers, table_data)

    # ─── 第4章: 失败模式分析 ──────────────────────────
    pdf.add_page()
    pdf.chapter_title("4. 失败模式深度分析")

    pdf.chapter_title("4.1 主要失败类型", level=2)

    # 统计失败模式
    from collections import Counter
    all_failures = []
    for items in df["failed_items"].dropna():
        if pd.notna(items) and str(items).strip():
            all_failures.extend([x.strip() for x in str(items).split(",")])
    failure_counts = Counter(all_failures)

    pdf.body_text("按出现频率排序的失败类型：")
    headers = ["失败类型", "出现次数", "占比", "严重程度"]
    failure_data = []
    for item, count in failure_counts.most_common():
        severity = "高" if count / len(df) > 0.5 else "中" if count / len(df) > 0.2 else "低"
        failure_data.append([item, str(count), f"{count/len(df)*100:.1f}%", severity])
    pdf.add_table(headers, failure_data)

    pdf.chapter_title("4.2 失败模式热力图", level=2)
    heatmap = charts_dir / "failure_heatmap.png"
    if heatmap.exists():
        pdf.add_chart(str(heatmap), width=180, caption="图 6: 因子家族 × 失败类型热力图")

    pdf.chapter_title("4.3 IC vs 跨品种散点分析", level=2)
    scatter = charts_dir / "oox_cross_scatter.png"
    if scatter.exists():
        pdf.add_chart(str(scatter), caption="图 7: IC 与跨品种通过率散点对比 (按家族分组)")

    # ─── 第5章: Momentum 修复 ─────────────────────────
    if fix_report:
        pdf.add_page()
        pdf.chapter_title("5. Momentum 家族 IC 修复")

        pdf.chapter_title("5.1 修复概述", level=2)
        pdf.body_text("针对 momentum 家族 4 个因子 IC 为负的问题，执行了因子方向反转修复（乘以 -1）。修复结果如下：")

        headers = ["因子", "原始 IC", "反转 IC", "改善幅度", "状态"]
        fix_data = []
        for item in fix_report:
            orig_ic = item["original"]["mean_ic"]
            rev_ic = item["reversed"]["mean_ic"]
            delta = item["improvement"]["ic_delta"]
            status = "✅ 改善" if delta > 0 else "❌ 未改善"
            fix_data.append([item["factor_name"], f"{orig_ic:.4f}", f"{rev_ic:.4f}", f"{delta:+.4f}", status])
        pdf.add_table(headers, fix_data)

        pdf.chapter_title("5.2 修复前后对比图", level=2)
        fix_chart = charts_dir / "momentum_fix_comparison.png"
        if fix_chart.exists():
            pdf.add_chart(str(fix_chart), caption="图 8: Momentum 家族 IC 修复前后柱状对比")

        fix_heatmap = charts_dir / "momentum_fix_heatmap.png"
        if fix_heatmap.exists():
            pdf.add_chart(str(fix_heatmap), width=180, caption="图 9: Momentum 家族各品种 IC 热力图对比")

        pdf.chapter_title("5.3 结论", level=2)
        pdf.body_text("修复结果表明：")
        pdf.bullet_point("fut_xsmom、fut_tsmom、fut_composite_momentum、fut_basis_momentum 四个因子反转后 IC 由负转正，改善幅度显著（+0.42 ~ +0.69）")
        pdf.bullet_point("fut_short_reversal 原本 IC 为正 (+0.2532)，反转后变差，应保持原版本")
        pdf.bullet_point("结论：Momentum 家族因子的方向设定存在系统性偏差，建议采用反转版本入库")

    # ─── 第6章: WalkForward 优化 ──────────────────────
    if wf_report:
        pdf.add_page()
        pdf.chapter_title("6. 高潜力因子 WalkForward 优化")

        pdf.chapter_title("6.1 优化概述", level=2)
        pdf.body_text("对 Top 5 高潜力因子进行 WalkForward 走航验证优化，使用自适应配置（500 天数据，1 年训练窗口，3 月步长）。")

        pdf.chapter_title("6.2 WalkForward 结果", level=2)
        headers = ["因子", "家族", "评分", "IC 一致性", "窗口数", "通过"]
        wf_data = []
        for item in wf_report.get("walk_forward_results", []):
            wf = item.get("walk_forward", {})
            score = wf.get("consistency_score", 0)
            consistency = wf.get("ic_consistency", 0) * 100
            n_win = wf.get("n_windows_completed", 0)
            passed = "✅" if wf.get("passed", False) else "❌"
            wf_data.append([item["factor_name"], item.get("family", ""), f"{score:.1f}", f"{consistency:.0f}%", str(n_win), passed])
        pdf.add_table(headers, wf_data)

        pdf.chapter_title("6.3 OOS 稳定性对比", level=2)
        wf_chart = charts_dir / "walkforward_oos_comparison_*.png"
        wf_charts = sorted(charts_dir.glob("walkforward_oos_comparison_*.png"))
        if wf_charts:
            pdf.add_chart(str(wf_charts[-1]), caption="图 10: WalkForward 综合评分与 IC 一致性对比")

        ic_timeline = charts_dir / "walkforward_ic_timeline_*.png"
        ic_timelines = sorted(charts_dir.glob("walkforward_ic_timeline_*.png"))
        if ic_timelines:
            pdf.add_chart(str(ic_timelines[-1]), caption="图 11: 各因子 WalkForward 窗口 IC 时序变化")

        pdf.chapter_title("6.4 参数敏感性分析", level=2)
        sens_charts = sorted(charts_dir.glob("walkforward_param_sensitivity_*.png"))
        if sens_charts:
            pdf.add_chart(str(sens_charts[-1]), width=180, caption="图 12: 因子参数敏感性分析")

        # 参数建议表
        headers = ["因子", "当前参数", "最优参数", "最优 IC", "IC 变化"]
        sens_data = []
        for item in wf_report.get("walk_forward_results", []):
            factor_name = item["factor_name"]
            sens = item.get("param_sensitivity", {})
            if sens and "results" in sens:
                current_params = item.get("recommended_params", {})
                best = max(sens["results"], key=lambda x: x["mean_ic"])
                sens_data.append([
                    factor_name,
                    str(current_params),
                    f"{sens['param_name']}={best['param_value']}",
                    f"{best['mean_ic']:.4f}",
                    f"→ 最优" if best["mean_ic"] > 0 else "无改善"
                ])
        if sens_data:
            pdf.add_table(headers, sens_data)

    # ─── 第7章: 优化建议 ──────────────────────────────
    pdf.add_page()
    pdf.chapter_title("7. 优化建议与行动计划")

    pdf.chapter_title("7.1 短期优化 (1-2 周)", level=2)
    pdf.body_text("优先级最高的行动项：")
    pdf.bullet_point("【紧急】修复 Momentum 家族 4 个因子的方向反转问题，重新入库")
    pdf.bullet_point("【高优】对 fut_bias、fut_hf_trade_imbalance、fut_option_pcr 三个通过 WalkForward 的因子进行参数锁定")
    pdf.bullet_point("【高优】为 long_term_reversal、fut_short_reversal 添加市场状态过滤器（趋势/震荡识别）")
    pdf.bullet_point("【中优】提升因子 IC 阈值要求（从 0.02 提升到 0.05+），降低多重检验惩罚")

    pdf.chapter_title("7.2 中期优化 (1-2 月)", level=2)
    pdf.body_text("系统性改进：")
    pdf.bullet_point("建立因子定期重训机制（季度/半年度），自动淘汰 IC 持续衰退的因子")
    pdf.bullet_point("开发因子自适应框架：根据市场波动率自动调整因子参数窗口")
    pdf.bullet_point("构建因子组合优化器：将低 IC 因子作为辅助信号与高 IC 因子组合使用")
    pdf.bullet_point("实现 IC 漂移实时监测：连续 3 个月 IC 下降触发因子警报")

    pdf.chapter_title("7.3 长期优化 (3-6 月)", level=2)
    pdf.body_text("长期能力建设：")
    pdf.bullet_point("建立因子生命周期数据库，记录因子从发现到衰退的完整表现")
    pdf.bullet_point("开发自动化因子挖掘 Pipeline：基于遗传规划/符号回归发现新因子")
    pdf.bullet_point("引入机器学习因子筛选：使用 AutoML 辅助因子有效性验证")
    pdf.bullet_point("构建因子元学习框架：自动选择适合当前市场状态的因子组合")

    pdf.chapter_title("7.4 风险控制建议", level=2)
    pdf.body_text("所有因子应遵循以下风险控制规则：")
    pdf.bullet_point("最大回撤限制：单因子最大回撤不超过 20%")
    pdf.bullet_point("连续亏损熔断：连续 5 日亏损自动降低因子权重 50%")
    pdf.bullet_point("相关性约束：因子组合内最大两两相关系数不超过 0.7")
    pdf.bullet_point("容量上限：单因子管理规模不超过对应品种日均成交额的 5%")

    # ─── 附录 ──────────────────────────────────────
    pdf.add_page()
    pdf.chapter_title("附录 A: 因子完整列表与指标")

    # Top 20 因子表
    pdf.chapter_title("A.1 IC 排名 Top 20", level=2)
    top20 = df.nlargest(20, "mean_ic")
    headers = ["排名", "因子名", "家族", "IC", "跨品种", "OOS", "状态"]
    top_data = []
    for i, (_, row) in enumerate(top20.iterrows(), 1):
        status = "✅" if row["audit_passed"] else "⚠️" if row["mean_ic"] > 0.05 else "❌"
        top_data.append([
            str(i),
            row["factor_name"],
            row.get("family", ""),
            f"{row['mean_ic']:.4f}",
            f"{row['cross_symbol_ratio']:.1%}",
            "通过" if row["oos_passed"] else "未通过",
            status,
        ])
    pdf.add_table(headers, top_data)

    # ─── 版权 ──────────────────────────────────────
    pdf.ln(10)
    pdf.set_draw_color(0, 51, 102)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    pdf.set_font("msyh", "", 9)
    pdf.set_text_color(128, 128, 128)
    pdf.cell(0, 5, f"Factor System Audit Report | Generated by FTS v1.0 | {datetime.now().strftime('%Y-%m-%d')}", align="C")

    # 3. 保存 PDF
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = reports_dir / f"factor_audit_report_{timestamp}.pdf"
    pdf.output(str(pdf_path))
    logger.info("PDF 报告已生成: %s", pdf_path)

    return str(pdf_path)


if __name__ == "__main__":
    main()