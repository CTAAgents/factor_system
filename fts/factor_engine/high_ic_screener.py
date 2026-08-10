"""高IC因子筛查剔除执行器（Phase B.4 集成）。

将「高IC因子筛选打分表」（docs/Knowledge/高IC因子筛选打分表.xlsx）与
「高IC因子筛查剔除实操检查清单」固化为系统化自动筛查流程，作为 L2
精英因子入库质检的强制关卡（所有市场统一启用）。

核心能力:
- 16 项检查 × 6 大模块，总分归一化到 100 分
- 5 项一票否决（任意触发直接 C 级剔除）
- A/B/C 三级评级: A≥85 入库, B 60~84 暂缓优化, C<60 直接剔除
- 渐进式接口: 缺失数据标记 skipped，不误杀实际合格因子

版本: v1.0.0（与 FTS 同步，Phase B.4 集成）
"""
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-locals

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, cast


# ─── 配置 ───────────────────────────────────────────────────


@dataclass
class HighICScreenConfig:
    """高IC筛查配置（所有市场统一，不区分股票/期货）。

    阈值来源: docs/Knowledge/高IC因子筛选打分表.xlsx
    """

    # 一、基础指标校验（20 分）
    ic_min: float = 0.02  # IC 均值合理下限
    ic_max: float = 0.06  # IC 均值合理上限
    ic_alert: float = 0.07  # IC 极端偏高警戒线（过拟合嫌疑）
    icir_pass: float = 0.5  # ICIR 合格线
    icir_warn: float = 0.3  # ICIR 伪强因子线
    win_rate_pass: float = 0.55  # IC 正向胜率合格线

    # 二、过拟合排查（25 分）
    oos_decay_max: float = 0.30  # 外样本 IC 衰减上限（一票否决 V1）
    extreme_drop_max: float = 0.25  # 极值扰动 IC 降幅上限（一票否决 V2）
    param_sensitivity_vol_max: float = 0.5  # 参数敏感性 IC 波动上限

    # 三、冗余&风格（20 分）
    corr_max: float = 0.70  # 存量因子相关上限（一票否决 V3）
    corr_alert: float = 0.90  # 高度冗余警戒线
    industry_min_ratio: float = 0.80  # 全行业普适性合格线

    # 四、落地性（20 分）
    net_excess_min: float = 0.0  # 扣成本后超额下限（一票否决 V4）
    turnover_weekly_max: float = 0.80  # 周度换手率上限
    half_life_min_days: float = 3.0  # 信号半衰期下限（交易日）

    # 五、尾部风险（10 分）
    logic_min_score: float = 2.0  # 经济逻辑维度最低分（一票否决 V5 用 <2）

    # 六、综合稳定性（5 分）
    oos_positive_ratio_min: float = 0.5  # WalkForward 正 IC 窗口占比下限

    # 评级阈值
    grade_A_min: float = 85.0  # A 级入库下限
    grade_B_min: float = 60.0  # B 级暂缓下限


# ─── 报告契约 ──────────────────────────────────────────────


@dataclass
class HighICCheckItem:
    """单项检查结果。"""

    name: str  # 检查项名（英文 snake_case）
    label: str  # 检查项中文名
    module: str  # 所属模块
    full_score: float  # 该项满分
    score: float  # 实际得分
    raw_value: Optional[float]  # 原始指标值（None=缺失）
    passed: Optional[bool]  # None=skipped
    evidence: str = ""  # 判定依据文本


@dataclass
class HighICScreenReport:
    """筛查报告。"""

    factor_id: str
    factor_name: str
    market: str
    screened_at: str
    items: list[HighICCheckItem] = field(default_factory=list)
    module_scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    grade: str = "C"
    disposition: str = "直接剔除"
    veto_triggered: bool = False
    veto_reasons: list[str] = field(default_factory=list)
    improvement_suggestions: list[str] = field(default_factory=list)

    # 便捷查询
    def item(self, name: str) -> Optional[HighICCheckItem]:
        """按英文名查询单项结果。"""
        for it in self.items:
            if it.name == name:
                return it
        return None

    @property
    def veto_items(self) -> list[HighICCheckItem]:
        """触发一票否决的检查项。"""
        return [it for it in self.items if it.passed is False and it.name.startswith("veto_")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "market": self.market,
            "screened_at": self.screened_at,
            "items": [
                {
                    "name": it.name,
                    "label": it.label,
                    "module": it.module,
                    "full_score": it.full_score,
                    "score": it.score,
                    "raw_value": it.raw_value,
                    "passed": it.passed,
                    "evidence": it.evidence,
                }
                for it in self.items
            ],
            "module_scores": self.module_scores,
            "total_score": round(self.total_score, 2),
            "grade": self.grade,
            "disposition": self.disposition,
            "veto_triggered": self.veto_triggered,
            "veto_reasons": self.veto_reasons,
            "improvement_suggestions": self.improvement_suggestions,
        }


# ─── 模块与检查项注册 ──────────────────────────────────────

# (name, label, module, full_score)
_CHECK_ITEMS: tuple[tuple[str, str, str, float], ...] = (
    # 一、基础指标校验（20 分）
    ("ic_mean", "IC均值合理性", "基础指标校验", 8.0),
    ("icir", "ICIR信息比率", "基础指标校验", 8.0),
    ("ic_win_rate", "IC正向胜率", "基础指标校验", 4.0),
    # 二、过拟合&虚假相关性排查（25 分）
    ("oos_decay", "外样本IC衰减", "过拟合排查", 10.0),
    ("extreme_perturb", "极值样本扰动", "过拟合排查", 8.0),
    ("param_sensitivity", "参数敏感性", "过拟合排查", 7.0),
    # 三、因子冗余&风格风险排查（20 分）
    ("existing_corr", "与存量因子相关性", "冗余风格排查", 8.0),
    ("style_exposure", "风格敞口集中度", "冗余风格排查", 7.0),
    ("industry_coverage", "全行业普适性", "冗余风格排查", 5.0),
    # 四、实盘交易落地性排查（20 分）
    ("net_excess", "交易成本后超额", "落地性排查", 8.0),
    ("turnover", "换手率合理性", "落地性排查", 6.0),
    ("signal_halflife", "信号时效性", "落地性排查", 6.0),
    # 五、尾部黑天鹅风险排查（10 分）
    ("logic_reason", "因子逻辑合理性", "尾部风险排查", 6.0),
    ("event_stability", "事件冲击稳定性", "尾部风险排查", 4.0),
    # 六、综合稳定性（5 分）
    ("multi_regime", "多行情适应性", "综合稳定性", 5.0),
    # 综合加分项（5 分）
    ("monotonicity", "收益单调性", "综合稳定性", 5.0),
)


# ─── 工具函数 ──────────────────────────────────────────────


def _linear(ratio: float, floor: float = 0.0, cap: float = 1.0) -> float:
    """将比率钳制到 [floor, cap]。"""
    return max(floor, min(cap, ratio))


def _wf_ic_series(evaluation: dict) -> list[float]:
    """从评估结果提取 WalkForward 各窗口 IC 序列。"""
    wf = evaluation.get("walk_forward")
    if not isinstance(wf, dict):
        return []
    windows = wf.get("windows")
    if not isinstance(windows, list):
        return []
    ics = []
    for w in windows:
        if isinstance(w, dict):
            ic = w.get("ic")
            if isinstance(ic, (int, float)):
                ics.append(float(ic))
    return ics


def _level1(evaluation: dict) -> dict:
    """提取 level_1_backtest 子字典。"""
    l1 = evaluation.get("level_1_backtest")
    return l1 if isinstance(l1, dict) else {}


def _level2(evaluation: dict) -> dict:
    """提取 level_2_economic 子字典。"""
    l2 = evaluation.get("level_2_economic")
    return l2 if isinstance(l2, dict) else {}


# ─── 筛查执行器 ────────────────────────────────────────────


class HighICScreener:
    """高IC因子筛查执行器。

    用法:
        screener = HighICScreener()
        report = screener.screen(
            factor=factor_program,
            evaluation=eval_result,
            correlation_metadata={"max_corr_detected": 0.6},
            backtest_pipeline={"net_excess_return": 0.02},
            trace_id=trace_id,
        )
        # report.grade == "C" 且 report.veto_triggered 时阻止入库

    输入契约（缺失字段自动 skipped，不误杀）:
        factor: dict（含 market / family / name）
        evaluation: FactorEvaluation
            - level_1_backtest: ic/icir/sharpe/max_drawdown/monotonicity/
                                oos_ratio/turnover_monthly/decay_6m/ic_volatility
            - level_2_economic: theory/behavioral/microstructure/institutional
            - walk_forward: {windows: [{ic}], n_windows_completed, ...}
        correlation_metadata: dict（max_corr_detected 等，L2 相关性预检输出）
        backtest_pipeline: dict（net_excess_return 等，端到端回测流水线输出）
    """

    def __init__(self, config: Optional[HighICScreenConfig] = None) -> None:
        self._config = config or HighICScreenConfig()

    # ─── 主入口 ──────────────────────────────────────────

    def screen(
        self,
        factor: Optional[dict[str, Any]] = None,
        evaluation: Optional[dict[str, Any]] = None,
        correlation_metadata: Optional[dict[str, Any]] = None,
        backtest_pipeline: Optional[dict[str, Any]] = None,
        trace_id: str = "",
    ) -> HighICScreenReport:
        """执行完整高IC筛查。顺序: 一票否决检查 → 16 项打分 → 评级 → 生成报告。"""
        factor = factor or {}
        evaluation = evaluation or {}
        correlation_metadata = correlation_metadata or {}
        backtest_pipeline = backtest_pipeline or {}

        factor_id = factor.get("factor_id", "")
        factor_name = factor.get("name", factor_id or "unknown")
        market = factor.get("market", "unknown")

        items: list[HighICCheckItem] = []
        veto_reasons: list[str] = []

        # ── Step 1: 一票否决检查 V1~V5 ──
        self._run_veto_checks(
            items,
            veto_reasons,
            evaluation,
            correlation_metadata,
            backtest_pipeline,
        )

        # ── Step 2: 16 项打分 ──
        for name, label, module, full in _CHECK_ITEMS:
            method = getattr(self, f"_check_{name}", None)
            if method is None:
                continue
            item = method(evaluation, correlation_metadata, backtest_pipeline)
            item.name = name
            item.label = label
            item.module = module
            item.full_score = full
            items.append(item)

        # ── Step 3: 模块分汇总 + 总分归一化 ──
        module_scores: dict[str, float] = {}
        total_raw = 0.0
        total_full = 0.0
        for it in items:
            if it.passed is None:  # skipped 不计入分母
                continue
            module_scores[it.module] = module_scores.get(it.module, 0.0) + it.score
            total_raw += it.score
            total_full += it.full_score
        total_score = (total_raw / total_full * 100.0) if total_full > 0 else 0.0

        # ── Step 4: 评级与处置 ──
        veto_triggered = len(veto_reasons) > 0
        if veto_triggered:
            grade, disposition = "C", "直接剔除"
        elif total_full == 0:
            # 无可判定检查项（数据严重缺失）→ 放行不拦截（不误杀原则）
            grade, disposition = "PASS", "数据不足放行"
        elif total_score >= self._config.grade_A_min:
            grade, disposition = "A", "正常入库"
        elif total_score >= self._config.grade_B_min:
            grade, disposition = "B", "暂缓优化"
        else:
            grade, disposition = "C", "直接剔除"

        suggestions = self._build_suggestions(items, grade)

        report = HighICScreenReport(
            factor_id=factor_id,
            factor_name=factor_name,
            market=market,
            screened_at=datetime.now().isoformat(),
            items=items,
            module_scores=module_scores,
            total_score=round(total_score, 2),
            grade=grade,
            disposition=disposition,
            veto_triggered=veto_triggered,
            veto_reasons=veto_reasons,
            improvement_suggestions=suggestions,
        )
        return report

    # ─── 一票否决检查 ────────────────────────────────────

    def _run_veto_checks(
        self,
        items: list[HighICCheckItem],
        veto_reasons: list[str],
        evaluation: dict,
        correlation_metadata: dict,
        backtest_pipeline: dict,
    ) -> None:
        """执行 5 项一票否决检查。数据缺失时 skipped，不误杀。"""
        _level1(evaluation)

        # V1: 外样本 IC 衰减 > 30%
        oos_decay = self._estimate_oos_decay(evaluation)
        if oos_decay is not None:
            if oos_decay > self._config.oos_decay_max:
                veto_reasons.append(f"V1 外样本IC衰减 {oos_decay:.1%} > {self._config.oos_decay_max:.0%}")
            items.append(
                HighICCheckItem(
                    name="veto_oos_decay",
                    label="一票否决:外样本IC衰减",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=oos_decay,
                    passed=oos_decay <= self._config.oos_decay_max,
                    evidence=f"oos_decay={oos_decay:.1%}",
                )
            )
        else:
            items.append(
                HighICCheckItem(
                    name="veto_oos_decay",
                    label="一票否决:外样本IC衰减",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=None,
                    passed=None,
                    evidence="数据缺失, 跳过",
                )
            )

        # V2: 极值扰动 IC 降幅 > 25%（无扰动重算数据时跳过）
        perturb = evaluation.get("extreme_perturbation")
        if isinstance(perturb, dict) and isinstance(perturb.get("ic_drop"), (int, float)):
            drop = float(perturb["ic_drop"])
            if drop > self._config.extreme_drop_max:
                veto_reasons.append(f"V2 极值扰动IC降幅 {drop:.1%} > {self._config.extreme_drop_max:.0%}")
            items.append(
                HighICCheckItem(
                    name="veto_extreme_perturb",
                    label="一票否决:极值扰动",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=drop,
                    passed=drop <= self._config.extreme_drop_max,
                    evidence=f"ic_drop={drop:.1%}",
                )
            )
        else:
            items.append(
                HighICCheckItem(
                    name="veto_extreme_perturb",
                    label="一票否决:极值扰动",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=None,
                    passed=None,
                    evidence="数据缺失, 跳过",
                )
            )

        # V3: 与存量因子相关 > 0.7 无增量
        max_corr = correlation_metadata.get("max_corr_detected")
        if isinstance(max_corr, (int, float)):
            if max_corr > self._config.corr_max:
                veto_reasons.append(f"V3 存量因子相关 {max_corr:.2f} > {self._config.corr_max}")
            items.append(
                HighICCheckItem(
                    name="veto_existing_corr",
                    label="一票否决:存量相关",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=max_corr,
                    passed=max_corr <= self._config.corr_max,
                    evidence=f"max_corr={max_corr:.2f}",
                )
            )
        else:
            items.append(
                HighICCheckItem(
                    name="veto_existing_corr",
                    label="一票否决:存量相关",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=None,
                    passed=None,
                    evidence="数据缺失, 跳过",
                )
            )

        # V4: 扣双边成本后超额为负
        net_excess = backtest_pipeline.get("net_excess_return")
        if isinstance(net_excess, (int, float)):
            if net_excess <= self._config.net_excess_min:
                veto_reasons.append(f"V4 扣成本后超额 {net_excess:.4f} ≤ {self._config.net_excess_min}")
            items.append(
                HighICCheckItem(
                    name="veto_net_excess",
                    label="一票否决:成本后超额",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=net_excess,
                    passed=net_excess > self._config.net_excess_min,
                    evidence=f"net_excess_return={net_excess:.4f}",
                )
            )
        else:
            items.append(
                HighICCheckItem(
                    name="veto_net_excess",
                    label="一票否决:成本后超额",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=None,
                    passed=None,
                    evidence="数据缺失, 跳过",
                )
            )

        # V5: 纯统计高IC无业务逻辑
        l2 = _level2(evaluation)
        logic_dims = [
            l2.get("theory"),
            l2.get("behavioral"),
            l2.get("microstructure"),
            l2.get("institutional"),
        ]
        logic_dims = [d for d in logic_dims if isinstance(d, (int, float))]
        if logic_dims:
            min_logic = min(cast(list[float], logic_dims))
            if min_logic < self._config.logic_min_score:
                veto_reasons.append(f"V5 经济逻辑维度最低 {min_logic} < {self._config.logic_min_score}")
            items.append(
                HighICCheckItem(
                    name="veto_logic",
                    label="一票否决:逻辑缺失",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=min_logic,
                    passed=min_logic >= self._config.logic_min_score,
                    evidence=f"min_logic={min_logic}",
                )
            )
        else:
            items.append(
                HighICCheckItem(
                    name="veto_logic",
                    label="一票否决:逻辑缺失",
                    module="一票否决",
                    full_score=0.0,
                    score=0.0,
                    raw_value=None,
                    passed=None,
                    evidence="数据缺失, 跳过",
                )
            )

    # ─── 工具: 外样本 IC 衰减估计 ────────────────────────

    def _estimate_oos_decay(self, evaluation: dict) -> Optional[float]:
        """估计外样本 IC 衰减率。

        优先用 decay_6m（评估链已计算），否则用 walk_forward 首窗 vs 末窗 IC。
        """
        l1 = _level1(evaluation)
        decay_6m = l1.get("decay_6m")
        if isinstance(decay_6m, (int, float)) and not (isinstance(decay_6m, float) and decay_6m != decay_6m):
            return _linear(float(decay_6m), 0.0, 1.0)
        ics = _wf_ic_series(evaluation)
        if len(ics) >= 2:
            first, last = ics[0], ics[-1]
            if abs(first) > 1e-10:
                decay = 1.0 - last / first
                return _linear(decay, 0.0, 1.0)
        return None

    # ─── 16 项打分 ───────────────────────────────────────

    def _check_ic_mean(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        l1 = _level1(evaluation)
        ic = l1.get("ic")
        if not isinstance(ic, (int, float)):
            return HighICCheckItem("ic_mean", "", "", 8.0, 0.0, None, None, "IC 数据缺失")
        raw = abs(float(ic))
        if raw < cfg.ic_min:
            score = 8.0 * _linear(raw / cfg.ic_min)
        elif raw <= cfg.ic_max:
            score = 8.0
        else:
            score = 8.0 * max(0.0, 1.0 - (raw - cfg.ic_max) / (cfg.ic_alert * 2))
        return HighICCheckItem("ic_mean", "", "", 8.0, round(score, 2), raw, raw <= cfg.ic_alert, f"|ic|={raw:.4f}")

    def _check_icir(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        l1 = _level1(evaluation)
        icir = l1.get("icir")
        if not isinstance(icir, (int, float)):
            return HighICCheckItem("icir", "", "", 8.0, 0.0, None, None, "ICIR 数据缺失")
        raw = abs(float(icir))
        if raw >= cfg.icir_pass:
            score = 8.0
        elif raw >= cfg.icir_warn:
            score = 8.0 * (raw - cfg.icir_warn) / (cfg.icir_pass - cfg.icir_warn)
        else:
            score = 8.0 * max(0.0, raw / cfg.icir_warn) * 0.5  # 伪强因子惩罚
        return HighICCheckItem("icir", "", "", 8.0, round(score, 2), raw, raw >= cfg.icir_warn, f"|icir|={raw:.4f}")

    def _check_ic_win_rate(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        ics = _wf_ic_series(evaluation)
        if not ics:
            return HighICCheckItem("ic_win_rate", "", "", 4.0, 0.0, None, None, "窗口 IC 数据缺失")
        win_rate = sum(1 for ic in ics if ic > 0) / len(ics)
        if win_rate >= cfg.win_rate_pass:
            score = 4.0
        elif win_rate >= 0.5:
            score = 4.0 * (win_rate - 0.5) / (cfg.win_rate_pass - 0.5)
        else:
            score = 0.0
        return HighICCheckItem(
            "ic_win_rate", "", "", 4.0, round(score, 2), win_rate, win_rate >= 0.5, f"win_rate={win_rate:.1%}"
        )

    def _check_oos_decay(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        decay = self._estimate_oos_decay(evaluation)
        if decay is None:
            return HighICCheckItem("oos_decay", "", "", 10.0, 0.0, None, None, "OOS 数据缺失")
        if decay <= cfg.oos_decay_max:
            score = 10.0
        elif decay <= 0.5:
            score = 10.0 * (0.5 - decay) / (0.5 - cfg.oos_decay_max)
        else:
            score = 10.0 * max(0.0, 1.0 - (decay - 0.5) / 0.5)
        return HighICCheckItem(
            "oos_decay", "", "", 10.0, round(score, 2), decay, decay <= cfg.oos_decay_max, f"decay={decay:.1%}"
        )

    def _check_extreme_perturb(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        perturb = evaluation.get("extreme_perturbation")
        if not isinstance(perturb, dict):
            return HighICCheckItem("extreme_perturb", "", "", 8.0, 0.0, None, None, "扰动数据缺失")
        drop = perturb.get("ic_drop")
        if not isinstance(drop, (int, float)):
            return HighICCheckItem("extreme_perturb", "", "", 8.0, 0.0, None, None, "扰动数据缺失")
        drop = float(drop)
        if drop <= cfg.extreme_drop_max:
            score = 8.0
        else:
            score = 8.0 * max(0.0, 1.0 - (drop - cfg.extreme_drop_max))
        return HighICCheckItem(
            "extreme_perturb", "", "", 8.0, round(score, 2), drop, drop <= cfg.extreme_drop_max, f"ic_drop={drop:.1%}"
        )

    def _check_param_sensitivity(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        l1 = _level1(evaluation)
        vol = l1.get("ic_volatility")
        if not isinstance(vol, (int, float)):
            return HighICCheckItem("param_sensitivity", "", "", 7.0, 0.0, None, None, "IC 波动数据缺失")
        raw = float(vol)
        if raw <= 0.2:
            score = 7.0
        elif raw <= cfg.param_sensitivity_vol_max:
            score = 7.0 * (cfg.param_sensitivity_vol_max - raw) / (cfg.param_sensitivity_vol_max - 0.2)
        else:
            score = 7.0 * max(0.0, 1.0 - (raw - cfg.param_sensitivity_vol_max))
        return HighICCheckItem(
            "param_sensitivity",
            "",
            "",
            7.0,
            round(score, 2),
            raw,
            raw <= cfg.param_sensitivity_vol_max,
            f"ic_volatility={raw:.3f}",
        )

    def _check_existing_corr(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        max_corr = corr_meta.get("max_corr_detected")
        if not isinstance(max_corr, (int, float)):
            return HighICCheckItem("existing_corr", "", "", 8.0, 0.0, None, None, "相关性数据缺失")
        raw = abs(float(max_corr))
        if raw <= cfg.corr_max:
            score = 8.0
        elif raw <= cfg.corr_alert:
            score = 8.0 * (cfg.corr_alert - raw) / (cfg.corr_alert - cfg.corr_max)
        else:
            score = 8.0 * max(0.0, 1.0 - (raw - cfg.corr_alert) / (1.0 - cfg.corr_alert))
        return HighICCheckItem(
            "existing_corr", "", "", 8.0, round(score, 2), raw, raw <= cfg.corr_max, f"max_corr={raw:.2f}"
        )

    def _check_style_exposure(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        # 风格敞口: 以 cross_symbol IC 分化度近似（数据缺失时 skipped）
        ics = _wf_ic_series(evaluation)
        if len(ics) < 3:
            return HighICCheckItem("style_exposure", "", "", 7.0, 0.0, None, None, "跨窗口数据不足")
        import numpy as np

        spread = float(np.std(ics))
        if spread <= 0.15:
            score = 7.0
        elif spread <= 0.4:
            score = 7.0 * (0.4 - spread) / (0.4 - 0.15)
        else:
            score = 0.0
        return HighICCheckItem(
            "style_exposure", "", "", 7.0, round(score, 2), spread, spread <= 0.4, f"ic_spread={spread:.3f}"
        )

    def _check_industry_coverage(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        positive_ratio = evaluation.get("cross_symbol_positive_ratio")
        if not isinstance(positive_ratio, (int, float)):
            return HighICCheckItem("industry_coverage", "", "", 5.0, 0.0, None, None, "跨品种数据缺失")
        raw = float(positive_ratio)
        if raw >= cfg.industry_min_ratio:
            score = 5.0
        else:
            score = 5.0 * max(0.0, raw / cfg.industry_min_ratio)
        return HighICCheckItem(
            "industry_coverage",
            "",
            "",
            5.0,
            round(score, 2),
            raw,
            raw >= cfg.industry_min_ratio,
            f"positive_ratio={raw:.1%}",
        )

    def _check_net_excess(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        net_excess = bt_pipeline.get("net_excess_return")
        if not isinstance(net_excess, (int, float)):
            return HighICCheckItem("net_excess", "", "", 8.0, 0.0, None, None, "成本数据缺失")
        raw = float(net_excess)
        score = 8.0 if raw > cfg.net_excess_min else 0.0
        return HighICCheckItem(
            "net_excess", "", "", 8.0, score, raw, raw > cfg.net_excess_min, f"net_excess_return={raw:.4f}"
        )

    def _check_turnover(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        l1 = _level1(evaluation)
        monthly = l1.get("turnover_monthly")
        if not isinstance(monthly, (int, float)):
            return HighICCheckItem("turnover", "", "", 6.0, 0.0, None, None, "换手率数据缺失")
        weekly = float(monthly) * 12.0 / 52.0  # 月度 → 周度
        if weekly <= cfg.turnover_weekly_max:
            score = 6.0 * (1.0 - weekly / cfg.turnover_weekly_max)
            score = max(score, 1.0)  # 低换手保底 1 分
        else:
            score = 0.0
        return HighICCheckItem(
            "turnover",
            "",
            "",
            6.0,
            round(score, 2),
            weekly,
            weekly <= cfg.turnover_weekly_max,
            f"weekly_turnover={weekly:.1%}",
        )

    def _check_signal_halflife(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        l1 = _level1(evaluation)
        decay_6m = l1.get("decay_6m")
        if not isinstance(decay_6m, (int, float)):
            return HighICCheckItem("signal_halflife", "", "", 6.0, 0.0, None, None, "衰减数据缺失")
        raw = abs(float(decay_6m))
        # 半年衰减率 → 半衰期天数近似: half_life ≈ ln(0.5)/ln(1-decay) * 126
        import math

        if raw >= 0.999:
            halflife = 1.0
        else:
            halflife = math.log(0.5) / math.log(1.0 - raw) * 126.0 if raw < 1.0 else 1.0
        if halflife >= cfg.half_life_min_days:
            score = 6.0
        else:
            score = 6.0 * max(0.0, halflife / cfg.half_life_min_days)
        return HighICCheckItem(
            "signal_halflife",
            "",
            "",
            6.0,
            round(score, 2),
            halflife,
            halflife >= cfg.half_life_min_days,
            f"half_life={halflife:.1f}d",
        )

    def _check_logic_reason(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        l2 = _level2(evaluation)
        dims = [
            l2.get("theory"),
            l2.get("behavioral"),
            l2.get("microstructure"),
            l2.get("institutional"),
        ]
        dims = [d for d in dims if isinstance(d, (int, float))]
        if not dims:
            return HighICCheckItem("logic_reason", "", "", 6.0, 0.0, None, None, "经济逻辑数据缺失")
        avg = sum(dims) / len(dims)
        if avg >= 3.0:
            score = 6.0
        elif avg >= cfg.logic_min_score:
            score = 6.0 * (avg - cfg.logic_min_score) / (3.0 - cfg.logic_min_score)
        else:
            score = 0.0
        return HighICCheckItem(
            "logic_reason", "", "", 6.0, round(score, 2), avg, avg >= cfg.logic_min_score, f"avg_logic={avg:.2f}"
        )

    def _check_event_stability(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        l1 = _level1(evaluation)
        max_dd = l1.get("max_drawdown")
        if not isinstance(max_dd, (int, float)):
            return HighICCheckItem("event_stability", "", "", 4.0, 0.0, None, None, "回撤数据缺失")
        raw = abs(float(max_dd))
        if raw <= 0.15:
            score = 4.0
        elif raw <= 0.4:
            score = 4.0 * (0.4 - raw) / (0.4 - 0.15)
        else:
            score = 0.0
        return HighICCheckItem(
            "event_stability", "", "", 4.0, round(score, 2), raw, raw <= 0.4, f"max_drawdown={raw:.1%}"
        )

    def _check_multi_regime(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        cfg = self._config
        ics = _wf_ic_series(evaluation)
        if not ics:
            return HighICCheckItem("multi_regime", "", "", 5.0, 0.0, None, None, "窗口 IC 数据缺失")
        pos_ratio = sum(1 for ic in ics if ic > 0) / len(ics)
        if pos_ratio >= cfg.oos_positive_ratio_min:
            score = 5.0
        else:
            score = 5.0 * max(0.0, pos_ratio / cfg.oos_positive_ratio_min)
        return HighICCheckItem(
            "multi_regime",
            "",
            "",
            5.0,
            round(score, 2),
            pos_ratio,
            pos_ratio >= cfg.oos_positive_ratio_min,
            f"pos_ratio={pos_ratio:.1%}",
        )

    def _check_monotonicity(self, evaluation, corr_meta, bt_pipeline) -> HighICCheckItem:
        l1 = _level1(evaluation)
        mono = l1.get("monotonicity")
        if not isinstance(mono, bool):
            return HighICCheckItem("monotonicity", "", "", 5.0, 0.0, None, None, "单调性数据缺失")
        return HighICCheckItem(
            "monotonicity", "", "", 5.0, 5.0 if mono else 0.0, float(mono), mono, f"monotonicity={mono}"
        )

    # ─── 优化建议 ────────────────────────────────────────

    def _build_suggestions(self, items: list[HighICCheckItem], grade: str) -> list[str]:
        """根据低分项生成 B 级优化建议。"""
        if grade != "B":
            return []
        suggestions: list[str] = []
        low_items = [it for it in items if it.passed is False and it.score < it.full_score]
        for it in low_items:
            if it.name == "icir":
                suggestions.append("ICIR 偏低: 尝试对因子做时序正交化或平滑波动, 提升 IC 稳定性")
            elif it.name == "existing_corr":
                suggestions.append("与存量因子相关性偏高: 尝试风格中性化 / 正交化后再复测")
            elif it.name == "style_exposure":
                suggestions.append("风格敞口集中: 增加风格中性约束, 约束后收益达标再保留")
            elif it.name == "oos_decay":
                suggestions.append("外样本 IC 衰减偏高: 检查参数是否过度拟合训练段, 缩短回看窗口")
            elif it.name == "param_sensitivity":
                suggestions.append("参数敏感性偏高: IC 随参数断崖波动, 建议固定稳健参数区间")
            elif it.name == "turnover":
                suggestions.append("换手率偏高: 增加信号平滑或降低调仓频率, 控制交易成本")
            elif it.name == "signal_halflife":
                suggestions.append("信号半衰期过短: 因子预测时效差, 考虑低频重采样或更长窗口")
        if not suggestions:
            suggestions.append("核心指标达标但存在短板, 建议正交化/中性化优化后二次复测")
        return suggestions


# ─── 模块导出 ──────────────────────────────────────────────

__all__ = [
    "HighICScreener",
    "HighICScreenConfig",
    "HighICScreenReport",
    "HighICCheckItem",
]
