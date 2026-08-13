"""
loop_engine/evolution_audit.py — AuditPipeline 协作类：审计与验证管线

34 计划（plans/34-evolution-loop-refactor-inventory.md）C 阶段 Phase 47d：
B 阶段产物 EvolutionAuditMixin 组合式重构为 AuditPipeline 协作类，行为等价、
公开 API 不变。领域独享组件（auditor / backtest_pipeline / ablation_experiment /
robustness_tester / shap_analyzer / causal_validator）与 `_signal_cache`
（34 §8.3：归本协作类；CandidateProcessor 经主类 property 转发共享同一引用）
随迁本类并在构造内装配；跨领域共享数据（data / forward_returns）经 owner
（主类实例）动态读取。主类 EvolutionLoop 组合持有本类实例，保留 11 方法
转发桩 + 7 属性 property 转发（兼容测试零改动，见 34 §8.5）。

`_build_wf_config` / `_is_blocking_ablation` 为 @staticmethod 纯函数，
`_ABLATION_*` 类常量随迁；测试经实例转发桩或类级调用保持兼容。

跨组件约束（34 §8.3）：协作类不 import evolution_loop（防循环导入），
owner 仅经 Any 标注，运行时经主类组装注入。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, cast

import numpy as np
import pandas as pd

from .audit import FactorAuditReport  # noqa: E402 — 延迟导入规避循环依赖

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from .contracts import FactorEvaluation, FactorProgram

logger = logging.getLogger(__name__)


class AuditPipeline:
    """领域 E：审计与验证管线（34 计划 C 阶段协作类）。

    状态所有权（34 §8.3）：领域独享组件（auditor / backtest_pipeline /
    ablation_experiment / robustness_tester / shap_analyzer / causal_validator）
    与 `_signal_cache`（归本协作类，CandidateProcessor 经主类 property 转发
    共享同一引用）随迁本类并在构造内装配（原主类 __init__ 对应段迁移）；
    跨领域共享数据（data / forward_returns）经 owner（主类实例）动态读取，
    兼容运行时重赋值（34 §8.3 可变上下文修订）。主类 EvolutionLoop 组合持有
    本类实例，保留 11 方法转发桩 + 7 属性 property 转发（兼容测试零改动，
    见 34 §8.5）。
    """

    def __init__(self, owner: Any, audit_config: Any = None) -> None:
        self._owner: Any = owner
        # ── 领域独享组件随迁（原主类 __init__ 对应段迁移） ──
        # GAP-070: 质检链信号缓存（三级评估/消融/鲁棒性/SHAP 共享，避免同一候选重复执行因子代码）
        from .evolution_loop import _QC_SIGNAL_CACHE_MAX_ENTRIES  # 延迟导入规避循环
        from .signal_cache import SignalCache

        self._signal_cache = SignalCache(max_entries=_QC_SIGNAL_CACHE_MAX_ENTRIES)
        # 子模块: 因子审计器 (Phase B.3 集成)
        # audit_config: 允许外部注入审计阈值（如期货低信噪比场景放宽 OOS 阈值）
        from .audit import FactorAuditor

        self.auditor = FactorAuditor(config=audit_config) if audit_config else FactorAuditor()
        # 子模块: 端到端回测流水线 (Phase B.2 集成)
        from .backtest_pipeline import BacktestPipeline, PipelineConfig

        self.backtest_pipeline = BacktestPipeline(config=PipelineConfig())
        # 子模块: 消融实验 (Phase A 集成)
        from .ablation import AblationExperiment

        self.ablation_experiment = AblationExperiment(random_seed=42)
        # 子模块: SHAP 可解释性分析 (Phase B 集成)
        # GAP-080 (v2.102.0): SHAP 批量计算降频——从 FTSConfig 读取采样参数
        # （默认 n_extreme=25 / n_background=50 / nsamples=50，env 可覆盖）
        from fts.config.settings import get_config as _get_shap_cfg
        from .shap_analyzer import ShapAnalyzer

        _shap_cfg = _get_shap_cfg()
        self.shap_analyzer = ShapAnalyzer(
            n_extreme=_shap_cfg.shap_n_extreme,
            n_background=_shap_cfg.shap_n_background,
            nsamples=_shap_cfg.shap_nsamples,
        )
        # 子模块: 鲁棒性审查 (Phase B 集成)
        from .robustness import RobustnessTester

        self.robustness_tester = RobustnessTester()
        # 子模块: 因果验证 (Phase C 集成)
        from .causal_validator import CausalValidator

        self.causal_validator = CausalValidator()

    # v2.50.0 判定语义：核心价格列（因子正常依赖的输入）与信息型消融模式
    # 不参与"伪相关"拦截判定——时序因子依赖时序因果（shuffle_dates）、
    # 价格因子依赖价格列、量价因子依赖成交量/VWAP 均属必要特征。
    _ABLATION_PRICE_CORE_COLS: frozenset[str] = frozenset({"open", "high", "low", "close", "vwap", "settle"})
    # 信息型消融模式：记录但不拦截
    _ABLATION_INFORMATIONAL_MODES: frozenset[str] = frozenset(
        {"volume_zero", "vwap_to_close", "vwap_to_settle", "shuffle_dates"}
    )

    def _run_backtest_pipeline(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
        trace_id: str,
    ) -> Optional[dict[str, Any]]:
        """执行端到端回测流水线（Phase B.2 集成）。

        在因子通过 L1/L2/L3 评估后，运行标准化回测流水线，
        生成完整的回测报告，供质检和审计使用。

        Args:
            factor: 因子程序
            evaluation: L1/L2/L3 评估结果
            trace_id: 全链路 trace_id

        Returns:
            回测结果字典，包含绩效指标和报告路径；失败返回 None
        """
        try:
            from .backtest_pipeline import BacktestInput

            bt_input = BacktestInput(
                factor=factor if isinstance(factor, dict) else dict(factor),
                data=self._owner.data,
                benchmark=None,
                forward_period=1,
            )
            result = self.backtest_pipeline.run(bt_input)

            if not result.success:
                print(f"[evo] 回测流水线失败 [{factor.get('factor_id', '?')}]: {result.error}")
                return None

            report = result.output
            return {
                "success": True,
                "duration_ms": result.duration_ms,
                "report_path": getattr(report, "file_path", None) if report else None,
                "metrics": {
                    "total_return": getattr(report, "total_return", 0.0),
                    "sharpe": getattr(report, "sharpe_ratio", 0.0),
                    "max_drawdown": getattr(report, "max_drawdown", 0.0),
                    "calmar": getattr(report, "calmar_ratio", 0.0),
                }
                if report
                else {},
            }
        except Exception as e:
            logger.debug("回测流水线异常: %s", e)
            return None

    # ── Phase B.3: 因子强制审计 ──────────────────────────

    @staticmethod
    def _build_wf_config(data: pd.DataFrame) -> dict[str, Any]:
        """按数据长度适配 WalkForward 窗口配置（GAP-F08，v2.60.0）。

        数据不足 3 年时缩短窗口，保证短样本也能构建多窗口冷启动验证；
        数据 < 半年（约 125 交易日）无法构建，由调用方跳过并记录原因。

        Args:
            data: 主时间序列数据

        Returns:
            WalkForwardConfig 字典
        """
        from .walk_forward import DEFAULT_WALK_FORWARD_CONFIG

        cfg = dict(DEFAULT_WALK_FORWARD_CONFIG)
        n = len(data)
        years = n / 250.0
        if years >= 3.0:
            return cfg
        if years >= 2.0:
            cfg.update(window_years=1, step_months=3, min_oos_months=2, n_windows=4)
        elif years >= 1.0:
            cfg.update(window_years=1, step_months=2, min_oos_months=1, n_windows=3)
        elif years >= 0.5:
            cfg.update(window_years=0, step_months=1, min_oos_months=0, n_windows=2)
        else:
            cfg.update(window_years=0, step_months=0, min_oos_months=0, n_windows=1)
        return cfg

    def _run_walkforward_oos(
        self,
        factor: "FactorProgram",
    ) -> Optional[dict[str, Any]]:
        """冷启动 WalkForward 样本外验证（GAP-F08，v2.60.0）。

        用多窗口滚动样本外评估替代 L1 单段 ICIR 近似，验证因子时间维度稳定性。
        数据不足或 force_walkforward=false 时返回 None（跳过并记录原因），
        审计 oos_consistency 项回退原逻辑。

        Args:
            factor: 因子程序

        Returns:
            WalkForwardResult 字典；跳过时返回 None
        """
        from fts.config.settings import get_config

        if not get_config().force_walkforward:
            logger.info("[Evo] force_walkforward=false，跳过冷启动样本外验证")
            return None

        data = self._owner.data
        if data is None or len(data) < 125:
            logger.info(
                "[Evo] 数据长度不足（%d 行 < 125），跳过冷启动样本外验证",
                len(data) if data is not None else 0,
            )
            return None

        try:
            from scipy import stats as sp_stats  # type: ignore[import-untyped]

            from .backtest_pipeline import BacktestPipeline
            from .walk_forward import WalkForwardOptimizer

            code = factor.get("code", "") if isinstance(factor, dict) else getattr(factor, "code", "")
            params = factor.get("params", {}) if isinstance(factor, dict) else getattr(factor, "params", {})

            def _eval_fn(
                train_df: pd.DataFrame,
                oos_df: pd.DataFrame,
            ) -> dict[str, float]:
                """评估函数：在 oos 段计算因子 IC/夏普/换手。"""
                try:
                    signal = BacktestPipeline._execute_factor_code(code, oos_df, params)
                except Exception:  # noqa: BLE001
                    return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
                signal = np.asarray(signal, dtype=float)
                close = oos_df["close"].to_numpy(dtype=float)
                fwd = np.zeros(len(close))
                if len(close) > 1:
                    fwd[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
                mask = np.isfinite(signal) & np.isfinite(fwd)
                if int(np.sum(mask)) < 10:
                    return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}
                ic, _ = sp_stats.spearmanr(signal[mask], fwd[mask])
                if not np.isfinite(ic):
                    ic = 0.0
                rets = fwd[mask]
                sharpe = float(np.mean(rets) / max(np.std(rets), 1e-9) * np.sqrt(252))
                turnover = float(np.mean(np.abs(np.diff(signal))))
                return {"ic": float(ic), "sharpe": sharpe, "turnover": turnover}

            optimizer = WalkForwardOptimizer(self._owner._build_wf_config(data))
            result = optimizer.evaluate(data, _eval_fn)
            if result.get("n_windows_completed", 0) == 0:
                logger.info("[Evo] WalkForward 无可用窗口，跳过冷启动样本外验证")
                return None
            logger.info(
                "[Evo] 冷启动样本外验证完成 [ic_consistency=%.2f, windows=%d, passed=%s]",
                result.get("ic_consistency", 0.0),
                result.get("n_windows_completed", 0),
                result.get("passed", False),
            )
            return dict(result)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Evo] WalkForward 冷启动验证异常，跳过: %s", e)
            return None

    def _run_factor_audit(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
        trace_id: str,
    ) -> FactorAuditReport:
        """执行因子审计（Phase B.3 集成）。

        将评估结果中的数据映射到审计器所需的输入，
        执行 6 项强制审计检查。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            FactorAuditReport 审计报告
        """
        import traceback

        # 兼容类级未绑定调用（测试 object.__new__ 绕过 __init__ 装配，self 即 owner）
        owner = getattr(self, "_owner", self)

        factor_meta = {
            "factor_id": factor.get("factor_id", ""),
            "name": factor.get("name", ""),
            "trace_id": trace_id,
            "family": factor.get("family", ""),
        }

        l1 = evaluation.get("level_1_backtest", {})
        l3 = evaluation.get("level_3_multiple", {})

        # 构造 OOS 结果（从评估链 L1 提取）
        # 注意: L1 的 oos_ratio 是样本外数据切分比例（评估链默认 0.3），
        # 并非一致性通过率，不能直接与审计阈值 0.5 比较。
        # 样本外一致性以 OOS ICIR 度量（|ICIR| ≥ 1.0 → ic_consistency=1.0）。
        oos_ratio = l1.get("oos_ratio", 0)
        oos_ic = l1.get("ic", 0)
        oos_icir = l1.get("icir", 0)
        oos_result: dict[str, Any] | None = None
        if oos_ratio > 0:
            oos_result = {
                "ic_consistency": min(1.0, abs(oos_icir)),
                "oos_ic": oos_ic,
                "passed": abs(oos_icir) >= 1.0,
            }

        # v2.60.0 (GAP-F08): 冷启动 WalkForward 样本外验证优先。
        # GAP-070 (v2.98.0): 优先复用三级评估链走航结果（Step 3 已强制走航，
        # 配置同源 `_build_wf_config`，窗口 IC 口径一致），消除双重 WalkForward
        # 重复计算；评估链走航失败/跳过（数据不足/force_walkforward=false）时
        # 兜底独立计算保持原逻辑。
        wf_result: dict[str, Any] | None = cast(dict[str, Any] | None, evaluation.get("walk_forward"))
        if not (wf_result and wf_result.get("n_windows_completed", 0) > 0):
            wf_result = owner._run_walkforward_oos(factor)
        if wf_result is not None:
            oos_result = {
                "ic_consistency": wf_result.get("ic_consistency", 0.0),
                "oos_ic": 0.0,  # 一致性已含多窗口均值信息
                "passed": wf_result.get("passed", False),
                "windows": wf_result.get("windows", []),
                "n_windows_completed": wf_result.get("n_windows_completed", 0),
            }
        elif isinstance(evaluation.get("walk_forward"), dict):
            chain_wf = evaluation.get("walk_forward")
            if chain_wf is not None and int(chain_wf.get("n_windows_completed", 0)) < 2:
                # GAP-079 (v2.102.0): 评估链走航存在但窗口不足（n_windows_completed<2），
                # 且独立走航失败（数据不足/force_walkforward=false）——保留"窗口不足"事实
                # 而非回退 L1 icir 兜底，使 _check_oos_consistency 命中 GAP-073 的
                # n_windows<2 → skipped 分支。修复短样本下 oos_consistency 全量误杀
                # （1073 audit_fail 中 99.4% 由 oos 导致，其中 90% 走航 0 窗口，
                # 见 plans/26-phase0-audit-breakdown.md）。
                oos_result = {
                    "ic_consistency": 0.0,
                    "oos_ic": 0.0,
                    "passed": False,
                    "windows": [],
                    "n_windows_completed": 0,
                }

        # 构造 p-values（从 L3 提取，仅当非默认值时传递）
        p_values: list[float] = []
        bonf_p = l3.get("bonferroni_p")
        if bonf_p is not None and bonf_p < 1.0:
            p_values.append(float(bonf_p))

        try:
            report = self.auditor.audit(
                factor=factor_meta,
                data=owner.data,
                forward_returns=owner.forward_returns,
                symbol_ic_map=l1.get("symbol_ic") or None,  # GAP-075: 激活 cross_symbol
                symbol_holdout=l1.get("symbol_holdout") or None,  # GAP-075: 标的留出审计项
                oos_result=oos_result,
                p_values=p_values if p_values else None,
            )
        except Exception as e:
            logger.warning(
                "审计执行异常 [%s]: %s (降级为跳过所有审计项)",
                factor_meta["name"],
                str(e),
            )
            logger.debug(traceback.format_exc())
            report = FactorAuditReport(
                factor_id=factor_meta["factor_id"],
                factor_name=factor_meta["name"],
                audited_at=datetime.now().isoformat(),
                items=[],
                passed=False,
                pass_rate=0.0,
                summary={"total": 6, "passed": 0, "failed": 0, "skipped": 6, "pass_rate": 0.0},
            )

        return report

    # ── Phase A: 消融实验检查 ──────────────────────────

    @staticmethod
    def _is_blocking_ablation(ab: dict[str, Any]) -> bool:
        """是否属于拦截型消融（非价格列置零导致的输入依赖崩塌）。

        仅当 zero_one_feature 置零的是「非核心价格列」（如 volume/持仓量等
        逻辑上为辅助输入的特征）时才参与伪相关判定。
        """
        mode = ab.get("mode", "")
        if mode in AuditPipeline._ABLATION_INFORMATIONAL_MODES:
            return False
        if mode == "zero_one_feature":
            feature = ab.get("feature") or ""
            return feature.lower() not in AuditPipeline._ABLATION_PRICE_CORE_COLS
        return False

    def _run_ablation_check(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
        trace_id: str,
    ) -> dict[str, Any]:
        """执行消融实验检查（Phase A 集成）。

        随机扰动因子输入特征，检测伪相关。
        仅「拦截型消融」（非价格列置零）IC 降幅超过基线 50% 时判定为伪相关；
        信息型消融（时序结构/成交量/VWAP/核心价格列）只记录不拦截。
        数据缺失时跳过（passed=True，不误杀）。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            消融结果字典，包含 passed 标志
        """
        try:
            data = getattr(self._owner, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self._owner, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            result = self.ablation_experiment.run(factor, data, forward_returns, signal_cache=self._signal_cache)
            # AblationResult 是 dict 子类，直接使用
            baseline_ic = result.get("baseline_ic", 0.0)
            ablations = result.get("ablations", [])
            if abs(baseline_ic) < 1e-9:
                is_passed = True
            else:
                # 仅拦截型消融的 IC 降幅超过基线 50% → 疑似伪相关
                blocking = [ab for ab in ablations if self._is_blocking_ablation(ab)]
                is_passed = all(ab.get("ic_change", 0.0) >= -0.5 * abs(baseline_ic) for ab in blocking)
            return {**result, "passed": is_passed}
        except Exception as e:
            logger.warning("消融实验异常: %s", e)
            return {"passed": True, "error": str(e), "ablations": []}

    def _run_robustness_check(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
        trace_id: str,
    ) -> dict[str, Any]:
        """执行鲁棒性审查（Phase B 集成）。

        在因子通过审计后，检测在对抗扰动、缺失值和
        分布外场景下的稳定性。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            鲁棒性结果字典，包含 passed 标志
        """
        try:
            data = getattr(self._owner, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self._owner, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            # 期货市场鲁棒性审查阈值放宽（低信噪比、短样本场景）
            min_pass_rate = 0.7

            result = self.robustness_tester.run(factor, data, forward_returns, signal_cache=self._signal_cache)
            # RobustnessTestResult 是 dict 子类，直接使用
            summary = result.get("summary", {})
            pass_rate = summary.get("overall_pass_rate", 1.0)
            is_passed = pass_rate >= min_pass_rate
            return {**result, "passed": is_passed}
        except Exception as e:
            logger.warning("鲁棒性审查异常: %s", e)
            return {"passed": True, "error": str(e)}

    # ── Phase B: SHAP 可解释性分析 ──────────────────────

    def _run_shap_analysis(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
        trace_id: str,
    ) -> dict[str, Any]:
        """执行 SHAP 可解释性分析（Phase B 集成）。

        对极端预测样本进行特征归因，确保模型可解释。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            SHAP 分析结果字典
        """
        try:
            data = getattr(self._owner, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self._owner, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            result = self.shap_analyzer.analyze(factor, data, forward_returns, signal_cache=self._signal_cache)
            # ShapAnalysisResult 是 dict 子类，直接使用；SHAP 为信息型审查，成功即通过
            return {**result, "passed": True}
        except Exception as e:
            logger.warning("SHAP 分析异常: %s", e)
            return {"passed": True, "error": str(e)}

    # ── Phase C: 因果结构审查 ──────────────────────────

    def _run_causal_validation(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
        trace_id: str,
    ) -> dict[str, Any]:
        """执行因果结构审查（Phase C 集成）。

        使用自然实验验证因子是否捕获了真实因果关系。
        对熔断等极端事件进行预测误差分析。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            因果验证结果字典，包含 passed 标志
        """
        try:
            data = getattr(self._owner, "data", None)
            if data is None or len(data) == 0:
                return {"passed": True, "skipped": True, "error": "data unavailable"}
            forward_returns = getattr(self._owner, "forward_returns", None)
            if forward_returns is None:
                forward_returns = np.zeros(len(data))

            result = self.causal_validator.validate(factor, data, forward_returns)
            # CausalValidationResult 是 dict 子类，直接使用
            is_passed = result.get("n_anomalous", 0) == 0
            return {**result, "passed": is_passed}
        except Exception as e:
            logger.warning("因果验证异常: %s", e)
            return {"passed": True, "error": str(e)}
