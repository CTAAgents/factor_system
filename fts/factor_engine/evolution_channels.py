"""
loop_engine/evolution_channels.py — EvolutionLoop 领域 G Mixin：演化通道（GP/深度/算子 DSL）

34 计划（plans/34-evolution-loop-refactor-inventory.md）B 阶段第三步：从
evolution_loop.py 抽取领域 G（演化通道）为独立 Mixin，行为等价、公开 API
不变。原方法剪切迁移（不改逻辑）。本领域无独享状态；组件
（feature_ops_engine / feature_importance_analyzer）与跨领域共享数据
（data / forward_returns / market / cross_section_data / _is_cross_section）
均由主类 EvolutionLoop.__init__ 装配，mixin 仅做类型声明经 self 访问。

契约（见 01-architecture.md §5 EvolutionLoop Mixin 拆分契约）：
- Mixin 方法名全局唯一，不 import evolution_loop（单向依赖，防循环导入）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, cast

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from .feature_importance import FeatureImportanceAnalyzer
    from .feature_ops import FeatureOpsEngine
    from .contracts import FactorProgram

logger = logging.getLogger(__name__)


class EvolutionChannelsMixin:
    """领域 G：演化通道（GP 遗传规划 / 深度因子 / 算子 DSL）。

    实例属性由主类 EvolutionLoop.__init__ 装配；此处类型声明供 mypy
    跨文件识别（34 计划 Mixin 拆分契约第 5 条）。本领域无独享状态。
    """

    # ── 实例属性类型声明（装配在 evolution_loop.py EvolutionLoop.__init__） ──
    data: pd.DataFrame
    forward_returns: np.ndarray | None
    market: str
    cross_section_data: Optional[dict[str, pd.DataFrame]]
    _is_cross_section: bool
    feature_ops_engine: "FeatureOpsEngine"
    feature_importance_analyzer: "FeatureImportanceAnalyzer"

    def _run_gp_evolution(
        self,
        parent: "FactorProgram",
        generation: int,
        trace_id: str,
    ) -> tuple["FactorProgram", str]:
        """执行 GP 遗传规划演化 (Phase C.1 集成)。

        使用 FeatureOpsEngine 在算子空间搜索最优因子表达式，
        作为宏观演化的补充或备选。

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id

        Returns:
            (新因子程序, 演化摘要)
        """
        from .gp_evolver import tree_to_factor_program

        target_col = "forward_return"
        gp_data = self.data.copy()
        if self.forward_returns is not None and len(self.forward_returns) == len(gp_data):
            gp_data[target_col] = self.forward_returns
        else:
            gp_data[target_col] = 0.0

        # 数据泄露防护: 构建训练集掩码（前 60% 数据），
        # 确保 GP 搜索仅在训练集上计算适应度
        train_ratio = 0.6
        train_size = max(int(len(gp_data) * train_ratio), 1)
        train_mask = pd.Series(
            [True] * train_size + [False] * (len(gp_data) - train_size),
            index=gp_data.index,
        )

        gp_result = self.feature_ops_engine.run_gp_search(
            data=gp_data,
            target=target_col,
            config={
                "population_size": 100,
                "max_generations": 20,
                "tournament_size": 3,
                "crossover_rate": 0.7,
                "mutation_rate": 0.1,
                "max_tree_depth": 4,
            },
            train_mask=train_mask,
        )

        if gp_result.best_fitness <= 0:
            raise RuntimeError(f"GP 演化适应度无效: {gp_result.best_fitness:.4f}")

        factor_program = tree_to_factor_program(gp_result.best_tree)
        factor_program["parent_id"] = parent.get("factor_id")
        factor_program["generation"] = generation
        factor_program["trace_id"] = trace_id
        factor_program["market"] = self.market

        # ── Phase C.1: 特征重要性分析 (集成到 GP 管线) ──
        try:
            from .factor_program import FactorExecutor

            # 执行因子程序得到信号序列，作为特征重要性的输入
            executor = FactorExecutor(factor_program)
            signals = executor.execute(gp_data, {})
            if len(signals) != len(gp_data):
                signals = np.full(len(gp_data), np.nan)

            importance_result = self.feature_importance_analyzer.analyze(
                pd.Series(signals),
                gp_data,
                target_col,
            )
            # FeatureImportanceResult 是 dataclass，转 dict 存快照
            factor_program["feature_importance"] = {k: v for k, v in importance_result.__dict__.items()}
        except Exception as e:
            logger.debug("特征重要性分析跳过: %s", e)

        summary = (
            f"GP Gen={gp_result.generations_completed}, "
            f"Fitness={gp_result.best_fitness:.4f}, "
            f"IC={gp_result.best_ic:.4f}, Sharpe={gp_result.best_sharpe:.4f}, "
            f"Expression={gp_result.best_expression[:80]}"
        )

        logger.info("GP 演化完成 [%s]: %s", parent.get("name", "?"), summary)
        return cast("FactorProgram", factor_program), summary

    def _run_deep_evolution(
        self,
        parent: "FactorProgram",
        generation: int,
        trace_id: str,
        model_kind: str = "gru",
    ) -> tuple["FactorProgram", str]:
        """执行深度因子演化 (GAP-I203 v2.73.0 / C5 v2.100.1)。

        使用 DeepFactorGenerator 在历史行情序列上训练轻量纯 numpy 深度模型
        （GRU 或 Transformer，C5），将训练权重固化内嵌为可执行因子 code
        （零未来函数：每步只用截至 t 的特征窗口推理），产出可过全套审计链
        的 FactorProgram。

        Args:
            parent: 父因子（仅用于命名与血缘）
            generation: 当前代数
            trace_id: 全链路 trace_id
            model_kind: 深度模型类型 "gru"（默认）| "transformer"（C5）

        Returns:
            (新因子程序, 演化摘要)

        Raises:
            RuntimeError: 数据缺失、样本不足或深度模型训练失败（调用方降级回退）
        """
        from fts.ml.deep_factor import DeepFactorConfig, create_deep_factor

        if self.data is None or len(self.data) < 2:
            raise RuntimeError("深度演化: 无可用行情数据")

        data = self.data
        # forward_returns 缺失/长度不齐时由生成器内部降级（返回 None）
        forward_returns: np.ndarray | None = self.forward_returns
        if forward_returns is None or len(forward_returns) != len(data):
            forward_returns = None

        factor = create_deep_factor(
            data=data,
            forward_returns=forward_returns,
            market=self.market,
            parent_name=parent.get("name", "?"),
            trace_id=trace_id,
            config=DeepFactorConfig(model_kind=model_kind),
        )
        if factor is None:
            raise RuntimeError(f"深度演化({model_kind}): 样本不足或训练失败")
        factor["parent_id"] = parent.get("factor_id")
        factor["generation"] = generation
        factor["trace_id"] = trace_id

        dm = factor.get("deep_model", {})
        label = "Transformer" if model_kind == "transformer" else "GRU"
        summary = (
            f"Deep {label} lookback={dm.get('lookback', '?')} "
            f"hidden={dm.get('hidden', '?')} "
            f"val_ic={float(dm.get('val_ic', 0.0)):.4f}"
        )
        logger.info("深度演化完成 [%s]: %s", parent.get("name", "?"), summary)
        return cast("FactorProgram", factor), summary

    def _generate_operator_factor(
        self,
        parent: "FactorProgram",
        generation: int,
        trace_id: str,
    ) -> tuple["FactorProgram", str]:
        """使用 FTS-Expr DSL 算子生成因子表达式 (Phase C.2)。

        基于算子注册表随机组合合法因子表达式，通过:
        1. 从 L0 字段池采样
        2. 随机选择 L1 时序算子（带合理的窗口参数）
        3. 可选 L2 横截面或 L4 组合算子封装
        4. 全程校验通过（参数边界、最大 lookback）

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id

        Returns:
            (新因子程序, 演化摘要)
        """
        # ── Phase 3+ / C.4: 优先适应度导向进化搜索 ──
        engine_factor = self._try_operator_engine_evolution(
            parent,
            generation,
            trace_id,
        )
        if engine_factor is not None:
            new_factor = engine_factor
            summary = f"OpEvolve: {new_factor.get('expression', '?')}"
            logger.info(
                "算子演化引擎因子生成成功 [%s]: %s",
                new_factor.get("name", "?"),
                summary,
            )
            return new_factor, summary

        # ── fallback: 随机组合生成（无评估数据或引擎失败） ──
        import hashlib
        import random
        import time

        from .expr_dsl.factory import create_operator_factor
        from .expr_dsl.executor import evaluate
        from .expr_dsl.parser import parse_expression
        from .expr_dsl.registry import L0_FIELDS, build_registry
        from .expr_dsl.validator import validate_expr

        # 构建算子注册表
        registry = build_registry()

        # 按类别分组算子
        l1_ops = [
            name
            for name, meta in registry.items()
            if meta.category == "L1" and name not in ("ts_covariance", "ts_correlation")
        ]
        l2_ops = [name for name, meta in registry.items() if meta.category == "L2"]
        l4_ops = [name for name, meta in registry.items() if meta.category == "L4"]

        # 种子随机（基于父因子，保证可复现性）
        seed = int(
            hashlib.md5(f"{parent.get('factor_id', '?')}_{generation}_{time.time_ns()}".encode()).hexdigest()[:8], 16
        ) % (2**31)
        rng = random.Random(seed)

        # 尝试生成合法的表达式，最多 10 次
        for attempt in range(10):
            try:
                # Step 1: 选择 1-2 个 L0 字段
                n_fields = rng.randint(1, 2)
                fields = rng.sample(list(L0_FIELDS), n_fields)

                # Step 2: 随机选择 1 个 L1 时序算子
                l1_op = rng.choice(l1_ops)

                # 确定窗口参数（5 的倍数，看起来更"专业"）
                window = ((rng.randint(5, 60) + 4) // 5) * 5

                # 构建表达式
                expr_parts = [f"{l1_op}({f}, {window})" for f in fields]

                # Step 3: 可选 L4 组合算子
                if len(expr_parts) == 2 and rng.random() < 0.5:
                    l4_op = rng.choice(l4_ops)
                    expression = f"{l4_op}({expr_parts[0]}, {expr_parts[1]})"
                else:
                    expression = expr_parts[0]

                # Step 4: 可选 L2 横截面封装
                if rng.random() < 0.4:
                    l2_op = rng.choice(l2_ops)
                    expression = f"{l2_op}({expression})"

                # 校验
                node = parse_expression(expression)
                errors, max_lookback = validate_expr(node, registry)
                if errors:
                    continue

                # Step 4.5: 常数信号前置拦截（生成阶段即过滤非常数表达式，
                # 避免到运行时校验/预筛阶段才被淘汰，浪费下游资源）
                try:
                    probe_data = (
                        list(self.cross_section_data.values())[0]
                        if (self._is_cross_section and self.cross_section_data is not None)
                        else self.data
                    )
                    sig = evaluate(node, probe_data, registry)
                    sig_arr = sig.values if isinstance(sig, pd.Series) else np.asarray(sig, dtype=float)
                    sig_arr = np.asarray(sig_arr, dtype=float)
                except Exception:
                    continue
                finite = sig_arr[np.isfinite(sig_arr)]
                if finite.size == 0 or np.nanstd(sig_arr) < 1e-8:
                    logger.debug(
                        "算子表达式非常数信号被前置拦截: %s",
                        expression,
                    )
                    continue

                # 创建因子程序
                parent_id = parent.get("factor_id", "?")
                unique_key = f"op_{parent_id}_{generation}_{expression}_{time.time_ns()}"
                factor_id = "fct_" + hashlib.md5(unique_key.encode()).hexdigest()[:8]

                factor_name = f"op_{l1_op}_{generation}_{factor_id[:6]}"

                new_factor = create_operator_factor(
                    expression=expression,
                    name=factor_name,
                    market=self.market,
                    family=parent.get("family", "operator"),
                    narrative=(f"算子演化: {expression} (基于父因子 {parent.get('name', '?')})"),
                    params={},
                    trace_id=trace_id,
                    source="operator_evolution",
                )
                # 覆盖产生的 factor_id 确保唯一
                new_factor["factor_id"] = factor_id
                new_factor["parent_id"] = parent_id
                new_factor["generation"] = generation

                summary = f"OpGen: {expression}, lookback={max_lookback}, fields={fields}"

                logger.info("算子因子生成成功 [%s]: %s", factor_name, summary)
                return new_factor, summary

            except Exception as e:
                logger.debug("算子因子生成尝试 %d/10 失败: %s", attempt + 1, e)
                continue

        raise RuntimeError(f"无法生成合法算子因子 (10 次尝试均失败, parent={parent.get('name', '?')})")

    def _try_operator_engine_evolution(
        self,
        parent: "FactorProgram",
        generation: int,
        trace_id: str,
    ) -> Optional["FactorProgram"]:
        """算子演化引擎搜索（Phase 3+ / C.4）。

        在 DSL 算子空间做适应度导向进化搜索，产物为 kind=OPERATOR 因子。
        无评估数据或引擎失败时返回 None（由调用方回退随机组合生成）。

        Returns:
            引擎产出的 OPERATOR 因子，或 None
        """
        import hashlib

        try:
            from .operator_evolution import (
                OperatorEvolutionConfig,
                OperatorEvolutionEngine,
            )
        except Exception as e:
            logger.debug("算子演化引擎导入失败: %s", e)
            return None

        try:
            # 评估数据源: 横截面模式用代表序列（与 micro_evolution 一致）
            if self._is_cross_section and self.cross_section_data is not None:
                data = list(self.cross_section_data.values())[0].copy()
            else:
                data = self.data.copy()
            target_col = "forward_return"
            if self.forward_returns is None or len(self.forward_returns) != len(data):
                logger.debug("算子演化引擎跳过: 无 forward_returns 评估数据")
                return None
            data[target_col] = self.forward_returns

            # 种子由父因子 + 代际序号派生（GAP-074 P0-2）：同父因子不同代
            # 产生不同搜索轨迹（原仅父因子派生产生完全确定性空转）；同父同代仍可复现
            seed = int(
                hashlib.md5(
                    f"{parent.get('factor_id', '?')}::{generation}".encode(),
                ).hexdigest()[:8],
                16,
            ) % (2**31)

            # 数据泄露防护: 构建训练集掩码（前 60% 数据），
            # 确保算子演化仅在训练集上计算适应度
            train_ratio = 0.6
            train_size = max(int(len(data) * train_ratio), 1)
            train_mask = pd.Series(
                [True] * train_size + [False] * (len(data) - train_size),
                index=data.index,
            )

            engine = OperatorEvolutionEngine(
                data_panel=data,
                target_col=target_col,
                config=OperatorEvolutionConfig(
                    population_size=40,
                    max_generations=8,
                    random_seed=seed,
                ),
                train_mask=train_mask,
            )
            result = engine.evolve()
            if result.best_fitness <= 0:
                logger.info(
                    "算子演化引擎无正适应度因子 [%s]，回退随机生成",
                    parent.get("name", "?"),
                )
                return None

            factor = engine.best_factor_program(
                result,
                name=f"op_evolved_{generation}_{parent.get('factor_id', '?')[:6]}",
                market=self.market,
                family=parent.get("family", "operator"),
                narrative=(f"算子演化引擎: {result.best_expression} (基于父因子 {parent.get('name', '?')})"),
                trace_id=trace_id,
                parent_id=parent.get("factor_id", "?"),
                generation=generation,
            )
            logger.info(
                "算子演化引擎成功 [%s]: %s (fitness=%.4f)",
                parent.get("name", "?"),
                result.best_expression,
                result.best_fitness,
            )
            return factor
        except Exception as e:
            logger.debug("算子演化引擎失败，回退随机生成: %s", e)
            return None
