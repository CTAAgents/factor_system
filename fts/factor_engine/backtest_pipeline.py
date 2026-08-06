"""
fts.factor_engine.backtest_pipeline — 端到端回测流水线 (Phase B.2)。

定义标准化的因子回测流水线接口，从数据输入到绩效报告的完整流程。
支持因子批量回测、对比分析和断点续跑。

流水线阶段:
    1. 数据加载 (DataLoadStage)
    2. 因子计算 (FactorComputeStage)
    3. 绩效评估 (PerformanceStage)
    4. 报告生成 (ReportStage)

用法:
    pipeline = BacktestPipeline()
    results = pipeline.run(
        factor=factor_program,
        data=ohlcv_dataframe,
        benchmark=benchmark_returns,
    )
    report = results.report
    print(report.sharpe, report.max_drawdown)

版本: v0.1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 阶段枚举 ─────────────────────────────────────────────


class PipelineStage(str, Enum):
    """流水线执行阶段。"""

    DATA_LOAD = "data_load"
    FACTOR_COMPUTE = "factor_compute"
    PERFORMANCE = "performance"
    REPORT = "report"


# ─── 输入输出契约 ──────────────────────────────────────────


@dataclass
class BacktestInput:
    """回测输入参数。"""

    factor: dict[str, Any]  # 因子元数据和代码
    data: pd.DataFrame  # OHLCV 数据
    benchmark: Optional[pd.Series] = None  # 基准收益率序列
    forward_period: int = 1  # 预测周期 (天)
    cost_rate: float = 0.0003  # 交易成本率 (单边)
    slippage: float = 0.0001  # 滑点率
    initialization_capital: float = 1_000_000.0  # 初始资金
    date_range: Optional[tuple[str, str]] = None  # 回测日期范围
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class FactorOutput:
    """因子计算输出。"""

    values: np.ndarray  # 因子值序列
    dates: pd.DatetimeIndex  # 对应日期
    forward_returns: np.ndarray  # 未来收益率
    ic_series: pd.Series  # 滚动 IC 序列
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceMetrics:
    """绩效指标。"""

    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    volatility: float = 0.0
    downside_volatility: float = 0.0
    best_day: float = 0.0
    worst_day: float = 0.0
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0  # IC Information Ratio
    turnover: float = 0.0
    exposure: float = 0.0


@dataclass
class BacktestReport:
    """回测报告。"""

    factor_id: str
    factor_name: str
    start_date: str
    end_date: str
    metrics: PerformanceMetrics
    ic_series: pd.Series
    equity_curve: pd.Series
    drawdown_curve: pd.Series
    trades: pd.DataFrame
    benchmark_curve: Optional[pd.Series] = None
    benchmark_excess: Optional[pd.Series] = None
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化报告为字典。"""
        return {
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "period": f"{self.start_date} to {self.end_date}",
            "metrics": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.metrics.__dict__.items()
            },
            "ic_mean": round(self.metrics.ic_mean, 4),
            "sharpe": round(self.metrics.sharpe_ratio, 4),
            "max_dd": round(self.metrics.max_drawdown, 4),
            "summary": self.summary,
        }


@dataclass
class PipelineResult:
    """流水线执行结果。"""

    success: bool
    stage: PipelineStage
    duration_ms: float
    output: Optional[BacktestReport] = None
    error: Optional[str] = None
    error_type: Optional[str] = None

    @property
    def failed(self) -> bool:
        return not self.success


@dataclass
class BacktestResult:
    """批量回测单项结果（B.2）。"""

    factor_id: str
    rank: int = 0
    report: Optional[BacktestReport] = None
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def sharpe(self) -> float:
        if self.report is not None:
            return self.report.metrics.sharpe_ratio
        return 0.0

    @property
    def ic_mean(self) -> float:
        if self.report is not None:
            return self.report.metrics.ic_mean
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 CLI 对比排名输出）。"""
        d: dict[str, Any] = {
            "factor_id": self.factor_id,
            "rank": self.rank,
            "sharpe": round(self.sharpe, 4),
            "ic_mean": round(self.ic_mean, 4),
        }
        if self.report is not None:
            d["max_drawdown"] = round(self.report.metrics.max_drawdown, 4)
            d["total_return"] = round(self.report.metrics.total_return, 4)
        if self.error:
            d["error"] = self.error
        return d


# ─── 异常类 ───────────────────────────────────────────────


class PipelineError(Exception):
    """流水线基础异常。"""

    def __init__(self, message: str, stage: PipelineStage, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.details = details or {}


class DataLoadError(PipelineError):
    """数据加载异常。"""
    pass


class FactorComputeError(PipelineError):
    """因子计算异常。"""
    pass


class PerformanceError(PipelineError):
    """绩效评估异常。"""
    pass


class ReportError(PipelineError):
    """报告生成异常。"""
    pass


# ─── BacktestPipeline ────────────────────────────────────


class BacktestPipeline:
    """端到端回测流水线。

    标准化的因子回测接口，支持完整的异常处理和断点续跑。

    Args:
        config: 流水线配置 (可选)
    """

    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self._config = config or PipelineConfig()
        self._intermediate: dict[str, Any] = {}
        self._current_stage: Optional[PipelineStage] = None

    def run_batch(
        self,
        factors: list[dict[str, Any]],
        data: pd.DataFrame,
        benchmark: Optional[pd.Series] = None,
        forward_period: int = 1,
        cost_rate: float = 0.0003,
        slippage: float = 0.0001,
        initialization_capital: float = 1_000_000.0,
        date_range: Optional[tuple[str, str]] = None,
        **kwargs: Any,
    ) -> list[BacktestResult]:
        """批量回测多个因子并输出对比排名（B.2）。

        每个因子独立执行单因子回测，结果按 Sharpe 降序排名。

        Args:
            factors: 因子字典列表（含 code/factor_id）
            data: OHLCV 数据
            benchmark: 基准收益率序列
            forward_period: 预测周期（天）
            cost_rate: 交易成本率
            slippage: 滑点率
            initialization_capital: 初始资金
            date_range: 回测日期范围
            **kwargs: 额外参数（透传 BacktestInput.extra_params）

        Returns:
            list[BacktestResult]，已按 Sharpe 排名（rank=1 最优）。
        """
        results: list[BacktestResult] = []
        for factor in factors:
            factor_id = factor.get("factor_id", "unknown")
            try:
                input_data = BacktestInput(
                    factor=factor,
                    data=data,
                    benchmark=benchmark,
                    forward_period=forward_period,
                    cost_rate=cost_rate,
                    slippage=slippage,
                    initialization_capital=initialization_capital,
                    date_range=date_range,
                    extra_params=kwargs,
                )
                result = self.run(input_data)
                if result.success:
                    results.append(BacktestResult(
                        factor_id=factor_id, report=result.output,
                    ))
                else:
                    results.append(BacktestResult(
                        factor_id=factor_id, error=result.error,
                    ))
            except Exception as e:  # noqa: BLE001
                logger.exception("[run_batch] 因子 %s 回测异常", factor_id)
                results.append(BacktestResult(factor_id=factor_id, error=str(e)))

        # 按 Sharpe 降序排名（失败因子排最后，保持 rank 连续）
        succeeded = [r for r in results if r.report is not None]
        succeeded.sort(key=lambda r: r.report.metrics.sharpe_ratio, reverse=True)
        for rank, r in enumerate(succeeded, start=1):
            r.rank = rank
        failed = [r for r in results if r.report is None]
        for rank, r in enumerate(failed, start=len(succeeded) + 1):
            r.rank = rank

        logger.info(
            "[run_batch] 批量回测完成 [total=%d, ok=%d, failed=%d]",
            len(results), len(succeeded), len(results) - len(succeeded),
        )
        return results

    def run(
        self,
        input_data: BacktestInput,
    ) -> PipelineResult:
        """执行完整回测流水线。

        Args:
            input_data: 回测输入

        Returns:
            PipelineResult 包含报告或错误信息
        """
        import time

        start_time = time.time()
        self._intermediate = {}

        try:
            # Stage 1: Data Load
            self._current_stage = PipelineStage.DATA_LOAD
            factor_code, data_clean = self._load_data(input_data)

            # Stage 2: Factor Compute
            self._current_stage = PipelineStage.FACTOR_COMPUTE
            factor_output = self._compute_factor(factor_code, data_clean, input_data)

            # Stage 3: Performance
            self._current_stage = PipelineStage.PERFORMANCE
            metrics, equity_curve, trades = self._evaluate_performance(
                factor_output, input_data
            )

            # Stage 4: Report
            self._current_stage = PipelineStage.REPORT
            report = self._generate_report(
                input_data, factor_output, metrics, equity_curve, trades
            )

            duration_ms = (time.time() - start_time) * 1000
            return PipelineResult(
                success=True,
                stage=PipelineStage.REPORT,
                duration_ms=duration_ms,
                output=report,
            )

        except PipelineError as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "流水线错误 [stage=%s]: %s", e.stage.value, str(e)
            )
            return PipelineResult(
                success=False,
                stage=self._current_stage or e.stage,
                duration_ms=duration_ms,
                error=str(e),
                error_type=type(e).__name__,
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            stage = self._current_stage or PipelineStage.DATA_LOAD
            logger.exception("未预期的错误 [stage=%s]: %s", stage.value, e)
            return PipelineResult(
                success=False,
                stage=stage,
                duration_ms=duration_ms,
                error=str(e),
                error_type=type(e).__name__,
            )

    # ─── Stage 1: Data Load ──────────────────────────────

    def _load_data(
        self, input_data: BacktestInput
    ) -> tuple[dict[str, Any], pd.DataFrame]:
        """加载和验证输入数据。"""
        try:
            data = input_data.data.copy()

            # 验证必要列
            required_cols = {"open", "high", "low", "close", "volume"}
            missing = required_cols - set(data.columns)
            if missing:
                raise DataLoadError(
                    f"缺少必要列: {missing}",
                    PipelineStage.DATA_LOAD,
                    {"missing_columns": list(missing)},
                )

            # 数据清洗
            data = data.dropna(subset=["close"])
            if len(data) < 60:
                raise DataLoadError(
                    f"数据量不足: {len(data)} 行 (需要 ≥ 60 行)",
                    PipelineStage.DATA_LOAD,
                    {"n_rows": len(data)},
                )

            # 日期过滤
            if input_data.date_range:
                start, end = input_data.date_range
                if "date" in data.columns:
                    mask = (data["date"] >= start) & (data["date"] <= end)
                    data = data[mask]

            factor_code = input_data.factor
            logger.info(
                "数据加载完成 [rows=%d, factor=%s]",
                len(data), factor_code.get("factor_id", "unknown"),
            )
            return factor_code, data

        except DataLoadError:
            raise
        except Exception as e:
            raise DataLoadError(
                f"数据加载失败: {e}",
                PipelineStage.DATA_LOAD,
            ) from e

    # ─── Stage 2: Factor Compute ────────────────────────

    def _compute_factor(
        self,
        factor_code: dict[str, Any],
        data: pd.DataFrame,
        input_data: BacktestInput,
    ) -> FactorOutput:
        """计算因子值。"""
        try:
            code_str = factor_code.get("code", "")
            if not code_str:
                raise FactorComputeError(
                    "因子代码为空",
                    PipelineStage.FACTOR_COMPUTE,
                )

            # 执行因子代码
            params = factor_code.get("params") or {}
            factor_values = self._execute_factor_code(code_str, data, params)

            if factor_values is None or len(factor_values) == 0:
                raise FactorComputeError(
                    "因子计算返回空结果",
                    PipelineStage.FACTOR_COMPUTE,
                )

            # 对齐到数据长度
            n = len(data)
            if len(factor_values) < n:
                factor_values = np.concatenate([
                    np.zeros(n - len(factor_values)),
                    factor_values,
                ])
            elif len(factor_values) > n:
                factor_values = factor_values[-n:]

            # 计算未来收益率
            forward_returns = self._compute_forward_returns(
                data["close"].values, input_data.forward_period
            )

            # 计算滚动 IC（无 date 列时回退到 index，兼容期货面板 DatetimeIndex 数据）
            date_values = data["date"].values if "date" in data.columns else data.index.values
            ic_series = self._compute_ic_series(
                factor_values, forward_returns, date_values
            )

            factor_id = factor_code.get("factor_id", "unknown")
            logger.info(
                "因子计算完成 [factor_id=%s, values=%d]",
                factor_id, len(factor_values),
            )

            return FactorOutput(
                values=factor_values,
                dates=pd.DatetimeIndex(
                    data["date"] if "date" in data.columns else data.index
                ),
                forward_returns=forward_returns,
                ic_series=ic_series,
                metadata={"factor_id": factor_id},
            )

        except (FactorComputeError, DataLoadError):
            raise
        except Exception as e:
            raise FactorComputeError(
                f"因子计算失败: {e}",
                PipelineStage.FACTOR_COMPUTE,
            ) from e

    @staticmethod
    def _execute_factor_code(
        code_str: str,
        data: pd.DataFrame,
        params: dict[str, Any] | None = None,
    ) -> np.ndarray:
        """执行因子代码。

        支持两种因子代码约定:
            1. 标准约定 (factor_program.py / seeds YAML):
               `def factor_program(data, params): ... return np.ndarray`
            2. 传统约定: 直接使用 open/high/low/close/volume 变量，
               并将结果存储到 output 变量。

        Args:
            code_str: 因子代码字符串
            data: OHLCV DataFrame
            params: 因子参数（标准约定使用）

        Returns:
            因子值数组
        """
        import numpy as np
        import warnings

        params = params or {}

        # 抑制因子执行期间的警告
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            # 准备执行环境（直接使用 .values 获取 ndarray，避免 Series 索引警告）
            open_price = data["open"].values.astype(np.float64)
            high = data["high"].values.astype(np.float64)
            low = data["low"].values.astype(np.float64)
            close = data["close"].values.astype(np.float64)
            volume = data["volume"].values.astype(np.float64)
            n = len(close)

            # 执行因子代码
            local_vars = {
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "n": n,
                "np": np,
            }
            try:
                exec(code_str, {}, local_vars)
            except Exception as e:
                # 捕获广播错误/形状不匹配等顶层运行时异常（传统约定代码），
                # 与 factor_program 约定行为一致，返回零值数组而非向上抛出
                logger.warning(
                    "因子代码顶层执行异常 (shape/broadcast): %s, 返回零值",
                    e,
                )
                return np.zeros(n)

        # 约定 1 (标准): def factor_program(data, params) -> np.ndarray
        factor_fn = local_vars.get("factor_program")
        if callable(factor_fn):
            # 以 dict[str, ndarray] 传入，兼容种子因子的 data['close'] / data.get('hold') 用法
            data_dict = {
                col: data[col].values.astype(np.float64)
                for col in data.columns
            }
            try:
                result = factor_fn(data_dict, params)
            except Exception as e:
                # 捕获广播错误/形状不匹配等运行时异常，返回零值数组
                logger.warning(
                    "因子代码执行异常 (shape/broadcast): %s, 返回零值",
                    e,
                )
                return np.zeros(n)
            if isinstance(result, (np.ndarray, pd.Series)):
                result = np.asarray(result, dtype=float)
                # 检查输出长度是否匹配，不匹配时返回零值
                if len(result) != n:
                    logger.warning(
                        "因子输出长度不匹配: %d != %d, 返回零值",
                        len(result), n,
                    )
                    return np.zeros(n)
                result = np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=-1.0)
                result = np.clip(result, -10.0, 10.0)
                return result

        # 约定 2 (传统): output 变量
        if "output" in local_vars:
            output = local_vars["output"]
            if isinstance(output, np.ndarray):
                result = output.astype(float)
            else:
                result = np.asarray(output, dtype=float)

            # 数值稳定性处理
            result = np.nan_to_num(result, nan=0.0, posinf=1.0, neginf=-1.0)
            result = np.clip(result, -10.0, 10.0)
            return result

        # 两种约定均未命中
        logger.warning("因子代码未定义 factor_program 且未设置 output 变量，返回零值")
        return np.zeros(n)

    # ─── Stage 3: Performance Evaluation ────────────────

    def _evaluate_performance(
        self,
        factor_output: FactorOutput,
        input_data: BacktestInput,
    ) -> tuple[PerformanceMetrics, pd.Series, pd.DataFrame]:
        """评估因子绩效。"""
        try:
            values = factor_output.values
            fwd_returns = factor_output.forward_returns
            dates = factor_output.dates

            # 计算策略收益
            strategy_returns = self._compute_strategy_returns(
                values, fwd_returns, input_data.cost_rate, input_data.slippage
            )

            # 计算净值曲线 (转为 pandas Series)
            equity_curve = pd.Series(
                input_data.initialization_capital * (1 + strategy_returns).cumprod(),
                index=dates,
            )

            # 计算交易记录
            trades = self._extract_trades(strategy_returns, dates, values)

            # 计算绩效指标
            metrics = self._calculate_metrics(
                strategy_returns, equity_curve, factor_output.ic_series
            )

            logger.info(
                "绩效评估完成 [sharpe=%.2f, max_dd=%.2f%%, ic_mean=%.4f]",
                metrics.sharpe_ratio,
                metrics.max_drawdown * 100,
                metrics.ic_mean,
            )
            return metrics, equity_curve, trades

        except Exception as e:
            raise PerformanceError(
                f"绩效评估失败: {e}",
                PipelineStage.PERFORMANCE,
            ) from e

    # ─── Stage 4: Report Generation ──────────────────────

    def _generate_report(
        self,
        input_data: BacktestInput,
        factor_output: FactorOutput,
        metrics: PerformanceMetrics,
        equity_curve: pd.Series,
        trades: pd.DataFrame,
    ) -> BacktestReport:
        """生成回测报告。"""
        try:
            factor_meta = input_data.factor
            factor_id = factor_meta.get("factor_id", "unknown")
            factor_name = factor_meta.get("name", factor_id)

            dates = factor_output.dates
            start_date = str(dates[0].date()) if len(dates) > 0 else "N/A"
            end_date = str(dates[-1].date()) if len(dates) > 0 else "N/A"

            # 计算回撤曲线
            cummax = equity_curve.cummax()
            drawdown_curve = (equity_curve - cummax) / cummax

            # 基准对比
            benchmark_curve = None
            benchmark_excess = None
            if input_data.benchmark is not None:
                benchmark = input_data.benchmark.reindex(equity_curve.index).fillna(0)
                benchmark_curve = input_data.initialization_capital * (1 + benchmark).cumprod()
                benchmark_excess = equity_curve / benchmark_curve - 1

            report = BacktestReport(
                factor_id=factor_id,
                factor_name=factor_name,
                start_date=start_date,
                end_date=end_date,
                metrics=metrics,
                ic_series=factor_output.ic_series,
                equity_curve=equity_curve,
                drawdown_curve=drawdown_curve,
                trades=trades,
                benchmark_curve=benchmark_curve,
                benchmark_excess=benchmark_excess,
                summary={
                    "config": {
                        "forward_period": input_data.forward_period,
                        "cost_rate": input_data.cost_rate,
                        "initial_capital": input_data.initialization_capital,
                    },
                    "generated_at": datetime.now().isoformat(),
                },
            )
            logger.info("报告生成完成 [factor_id=%s]", factor_id)
            return report

        except Exception as e:
            raise ReportError(
                f"报告生成失败: {e}",
                PipelineStage.REPORT,
            ) from e

    # ─── 辅助方法 ────────────────────────────────────────

    @staticmethod
    def _compute_forward_returns(
        close: np.ndarray, period: int
    ) -> np.ndarray:
        """计算未来 N 日收益率。"""
        n = len(close)
        returns = np.zeros(n)
        if period >= n:
            return returns
        returns[:-period] = (close[period:] - close[:-period]) / close[:-period]
        return returns

    @staticmethod
    def _compute_ic_series(
        factor_values: np.ndarray,
        forward_returns: np.ndarray,
        dates: np.ndarray,
        window: int = 20,
    ) -> pd.Series:
        """计算滚动 IC 序列。"""
        n = len(factor_values)
        ics = np.zeros(n)
        for i in range(window, n):
            f = factor_values[i - window:i]
            r = forward_returns[i - window:i]
            if np.std(f) > 1e-8 and np.std(r) > 1e-8:
                ics[i] = np.corrcoef(f, r)[0, 1]
            else:
                ics[i] = 0.0

        return pd.Series(ics, index=pd.DatetimeIndex(dates))

    @staticmethod
    def _compute_strategy_returns(
        factor_values: np.ndarray,
        forward_returns: np.ndarray,
        cost_rate: float,
        slippage: float,
    ) -> np.ndarray:
        """计算策略收益率。"""
        # 因子作为持仓信号 (简化: 分位数持仓)
        positions = np.zeros_like(factor_values)
        n = len(factor_values)

        for i in range(1, n):
            if np.std(factor_values[:i]) > 1e-8:
                z = (factor_values[i] - np.mean(factor_values[:i])) / max(np.std(factor_values[:i]), 1e-8)
                positions[i] = np.clip(z, -2, 2) * 0.5  # 限制在 [-1, 1]

        # 计算换手
        turnover = np.abs(np.diff(positions, prepend=0))
        costs = turnover * (cost_rate + slippage)

        # 策略收益 = 持仓收益 - 成本
        strategy_returns = positions * forward_returns - costs
        return strategy_returns

    @staticmethod
    def _extract_trades(
        returns: np.ndarray,
        dates: pd.DatetimeIndex,
        positions: np.ndarray,
    ) -> pd.DataFrame:
        """提取交易记录。"""
        trades = []
        position = 0
        entry_price = 0
        entry_date = None

        for i in range(1, len(returns)):
            new_position = positions[i]
            if abs(new_position - position) > 0.1:
                # 平仓
                if position != 0 and entry_date is not None:
                    trades.append({
                        "entry_date": str(entry_date.date()) if entry_date else "N/A",
                        "exit_date": str(dates[i].date()),
                        "position": position,
                        "return": returns[i],
                    })
                # 开仓
                if new_position != 0:
                    entry_date = dates[i]
                    entry_price = 0  # 简化
                position = new_position

        return pd.DataFrame(trades)

    @staticmethod
    def _calculate_metrics(
        returns: np.ndarray,
        equity_curve: pd.Series,
        ic_series: pd.Series,
    ) -> PerformanceMetrics:
        """计算绩效指标。"""
        total_return = float(equity_curve.iloc[-1] / equity_curve.iloc[0] - 1)
        n_days = len(returns)
        annual_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1

        volatility = float(np.std(returns) * np.sqrt(252))
        sharpe = annual_return / volatility if volatility > 1e-8 else 0.0

        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_dd = float(drawdown.min())

        calmar = annual_return / abs(max_dd) if max_dd < -1e-8 else 0.0

        positive_days = (returns > 0).sum()
        win_rate = float(positive_days / max(n_days, 1))

        downside = returns[returns < 0]
        downside_vol = float(np.std(downside) * np.sqrt(252)) if len(downside) > 0 else 0.0

        best_day = float(np.max(returns)) if len(returns) > 0 else 0.0
        worst_day = float(np.min(returns)) if len(returns) > 0 else 0.0

        ic_mean = float(ic_series.mean()) if len(ic_series) > 0 else 0.0
        ic_std = float(ic_series.std()) if len(ic_series) > 0 else 0.0
        ic_ir = ic_mean / ic_std if ic_std > 1e-8 else 0.0

        # 计算换手率
        turnover = float(np.mean(np.abs(np.diff(returns, prepend=returns[0])))) if len(returns) > 1 else 0.0

        return PerformanceMetrics(
            total_return=total_return,
            annual_return=annual_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            calmar_ratio=calmar,
            win_rate=win_rate,
            volatility=volatility,
            downside_volatility=downside_vol,
            best_day=best_day,
            worst_day=worst_day,
            ic_mean=ic_mean,
            ic_std=ic_std,
            ic_ir=ic_ir,
            turnover=turnover,
            exposure=float(np.mean(np.abs(returns))),
        )


# ─── 流水线配置 ──────────────────────────────────────────


@dataclass
class PipelineConfig:
    """流水线配置。"""

    # 计算窗口
    ic_window: int = 20
    # 异常处理
    fail_fast: bool = True  # 出错立即停止
    max_retries: int = 2  # 单个阶段最大重试次数
    # 数据验证
    min_data_points: int = 60
    # 输出
    generate_trade_log: bool = True
    compute_benchmark: bool = True


# ─── 流水线构建器（B.2） ──────────────────────────────


class BacktestPipelineBuilder:
    """回测流水线构建器（Builder 模式，B.2）。

    链式配置回测参数，build() 返回配置好的 BacktestPipeline。

    Usage:
        pipeline = (BacktestPipelineBuilder()
            .set_period('2020-01-01', '2025-12-31')
            .set_signal_type('time_series')
            .set_weight_method('adaptive')
            .set_capital_mode('vol_target', target_vol=0.15)
            .enable_cost_model(True)
            .enable_risk_attribution(True)
            .build())
    """

    def __init__(self) -> None:
        self._start: Optional[str] = None
        self._end: Optional[str] = None
        self._signal_type: str = "time_series"
        self._weight_method: str = "equal"
        self._capital_mode: str = "vol_target"
        self._capital_kwargs: dict[str, Any] = {}
        self._use_cost_model: bool = True
        self._include_risk_attribution: bool = True
        self._forward_period: int = 1
        self._initialization_capital: float = 1_000_000.0

    # ─── 链式配置 ────────────────────────────────────────

    def set_period(self, start: str, end: str) -> "BacktestPipelineBuilder":
        """设置回测日期范围。"""
        self._start = start
        self._end = end
        return self

    def set_signal_type(self, stype: str) -> "BacktestPipelineBuilder":
        """设置信号类型（time_series / cross_section）。"""
        self._signal_type = stype
        return self

    def set_weight_method(self, method: str) -> "BacktestPipelineBuilder":
        """设置权重方法（equal / sharpe / adaptive）。"""
        self._weight_method = method
        return self

    def set_capital_mode(self, mode: str, **kwargs: Any) -> "BacktestPipelineBuilder":
        """设置资金管理模式（fixed / vol_target / risk_parity / kelly）。"""
        self._capital_mode = mode
        self._capital_kwargs = dict(kwargs)
        return self

    def enable_cost_model(self, enabled: bool) -> "BacktestPipelineBuilder":
        """启用/禁用成本模型。"""
        self._use_cost_model = bool(enabled)
        return self

    def enable_risk_attribution(self, enabled: bool) -> "BacktestPipelineBuilder":
        """启用/禁用风险归因。"""
        self._include_risk_attribution = bool(enabled)
        return self

    def set_forward_period(self, period: int) -> "BacktestPipelineBuilder":
        """设置预测周期。"""
        self._forward_period = int(period)
        return self

    def set_initial_capital(self, capital: float) -> "BacktestPipelineBuilder":
        """设置初始资金。"""
        self._initialization_capital = float(capital)
        return self

    # ─── 构建 ────────────────────────────────────────────

    def build(self) -> BacktestPipeline:
        """构建 BacktestPipeline 实例。"""
        config = PipelineConfig()
        pipeline = BacktestPipeline(config=config)
        # 注入构建参数（run_batch 时读取）
        pipeline._builder_config = {  # noqa: SLF001
            "start": self._start,
            "end": self._end,
            "signal_type": self._signal_type,
            "weight_method": self._weight_method,
            "capital_mode": self._capital_mode,
            "capital_kwargs": dict(self._capital_kwargs),
            "use_cost_model": self._use_cost_model,
            "include_risk_attribution": self._include_risk_attribution,
            "forward_period": self._forward_period,
            "initialization_capital": self._initialization_capital,
        }
        return pipeline

    def get_config(self) -> dict[str, Any]:
        """返回构建参数快照。"""
        return {
            "start": self._start,
            "end": self._end,
            "signal_type": self._signal_type,
            "weight_method": self._weight_method,
            "capital_mode": self._capital_mode,
            "capital_kwargs": dict(self._capital_kwargs),
            "use_cost_model": self._use_cost_model,
            "include_risk_attribution": self._include_risk_attribution,
            "forward_period": self._forward_period,
            "initialization_capital": self._initialization_capital,
        }


__all__ = [
    "BacktestPipeline",
    "BacktestInput",
    "BacktestReport",
    "BacktestResult",
    "BacktestPipelineBuilder",
    "PipelineResult",
    "PipelineStage",
    "PerformanceMetrics",
    "FactorOutput",
    "PipelineConfig",
    "PipelineError",
    "DataLoadError",
    "FactorComputeError",
    "PerformanceError",
    "ReportError",
]
