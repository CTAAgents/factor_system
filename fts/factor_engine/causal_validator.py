"""
fts/factor_engine/causal_validator.py — 因果结构审查（Phase C 逻辑审查）

HARNESS §11-logic-review-plan.md §C.1:
    通过自然实验和反事实测试，验证因子是否捕捉到真正的因果关系。

设计:
    - 在历史上标记"自然实验"事件（熔断、涨跌停板打开、主力合约切换日）
    - 在这些事件前后计算因子预测误差的异常程度
    - 若 |预测误差| > 3σ 且与事件方向一致，标记为"事件敏感"

版本: v1.0.0
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

from .contracts import FactorProgram
from .factor_program import FactorExecutor


# ─── 因果验证结果契约 ──────────────────────────────────────


class EventPredictionError(dict):
    """单个事件周围的预测误差分析。"""

    def __init__(
        self,
        event_id: str,
        event_name: str,
        event_type: str,
        event_date: str,
        expected_direction: str,
        pre_window: int,
        post_window: int,
        pre_mean_error: float,
        post_mean_error: float,
        error_change: float,
        error_std: float,
        is_anomalous: bool,
        anomaly_direction: str,
        n_pre_samples: int,
        n_post_samples: int,
    ) -> None:
        super().__init__()
        self["event_id"] = event_id
        self["event_name"] = event_name
        self["event_type"] = event_type
        self["event_date"] = event_date
        self["expected_direction"] = expected_direction
        self["pre_window"] = pre_window
        self["post_window"] = post_window
        self["pre_mean_error"] = pre_mean_error
        self["post_mean_error"] = post_mean_error
        self["error_change"] = error_change
        self["error_std"] = error_std
        self["is_anomalous"] = is_anomalous
        self["anomaly_direction"] = anomaly_direction
        self["n_pre_samples"] = n_pre_samples
        self["n_post_samples"] = n_post_samples


class CausalValidationResult(dict):
    """完整因果结构审查结果。"""

    def __init__(
        self,
        factor_id: str,
        factor_name: str,
        analysis_date: str,
        n_events: int,
        n_anomalous: int,
        anomalous_events: list[EventPredictionError],
        all_events: list[EventPredictionError],
        summary: dict[str, Any],
    ) -> None:
        super().__init__()
        self["factor_id"] = factor_id
        self["factor_name"] = factor_name
        self["analysis_date"] = analysis_date
        self["n_events"] = n_events
        self["n_anomalous"] = n_anomalous
        self["anomalous_events"] = anomalous_events
        self["all_events"] = all_events
        self["summary"] = summary


# ─── 自然实验事件导入 ──────────────────────────────────────


def _import_default_events() -> list[Any]:
    """延迟导入自然实验事件定义，避免循环依赖。"""
    import importlib

    try:
        mod = importlib.import_module("tests.scenarios.natural_experiments")
        return list(mod.DEFAULT_EVENTS)  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return []


# ─── 因果验证器 ────────────────────────────────────────────


class CausalValidator:
    """因果结构审查执行器。

    通过自然实验事件验证因子预测是否具有因果意义：
    - 在重大市场事件前后，因子预测误差应显著变化
    - 变化方向应与事件的经济学含义一致
    - 若因子完全不受事件影响，可能只是捕捉了截面相关性而非因果关系

    Usage:
        validator = CausalValidator()
        result = validator.validate(factor, data, forward_returns)
        print(validator.report(result))
    """

    ANOMALY_SIGMA_THRESHOLD: float = 3.0

    def __init__(
        self,
        events: Optional[list[Any]] = None,
        sigma_threshold: float = 3.0,
    ):
        """
        Args:
            events: 自然实验事件列表。None = 使用 DEFAULT_EVENTS。
            sigma_threshold: 异常判断的 sigma 阈值（默认 3.0）
        """
        self._events = events if events is not None else _import_default_events()
        self._sigma_threshold = sigma_threshold
        self._executor: Optional[FactorExecutor] = None

    def validate(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
    ) -> CausalValidationResult:
        """对因子执行因果结构审查。

        Args:
            factor: 因子程序
            data: OHLCV 数据（必须包含 date 列）
            forward_returns: 未来收益率

        Returns:
            CausalValidationResult
        """
        # 确保数据有日期索引
        if "date" not in data.columns and not isinstance(data.index, pd.DatetimeIndex):
            raise ValueError("data must have a 'date' column or DatetimeIndex")

        # 确保 date 列存在
        if "date" not in data.columns:
            data = data.copy()
            data["date"] = data.index

        # 获取因子信号（使用因子自身 params，与评估链 evaluation_chain 口径一致；
        # 传空 {} 会导致 params['window'] 直取型因子抛 KeyError，因果审查被静默跳过）
        executor = FactorExecutor(factor)
        signals = executor.execute(data, factor.get("params", {}))

        if len(signals) != len(data):
            signals = np.full(len(data), np.nan)

        # 计算预测误差 = 信号 - forward_returns（标准化后）
        fwd = np.asarray(forward_returns)
        error = np.full(len(data), np.nan)
        valid = ~np.isnan(signals) & ~np.isnan(fwd)
        if np.sum(valid) > 0:
            sig_norm = (signals - np.nanmean(signals)) / (np.nanstd(signals) + 1e-10)
            fwd_norm = (fwd - np.nanmean(fwd)) / (np.nanstd(fwd) + 1e-10)
            error[valid] = np.abs(sig_norm[valid] - fwd_norm[valid])

        # 全局误差标准差
        error_std_global = np.nanstd(error) if np.any(~np.isnan(error)) else 1.0

        # 获取日期数组
        dates = pd.to_datetime(data["date"]).values
        date_to_idx = {pd.Timestamp(d).date(): i for i, d in enumerate(dates)}

        # 对每个事件做分析
        all_event_results: list[EventPredictionError] = []
        anomalous_events: list[EventPredictionError] = []

        for event in self._events:
            event_date = event.event_date
            if isinstance(event_date, datetime):
                event_date = event_date.date()

            if event_date not in date_to_idx:
                # 事件日期不在数据范围内，跳过
                continue

            event_idx = date_to_idx[event_date]
            pre_start = max(0, event_idx - event.pre_window)
            post_end = min(len(data), event_idx + event.post_window + 1)

            # 事件前误差
            pre_errors = error[pre_start:event_idx]
            pre_valid = pre_errors[~np.isnan(pre_errors)]
            pre_mean = float(np.mean(pre_valid)) if len(pre_valid) > 0 else 0.0

            # 事件后误差
            post_errors = error[event_idx + 1 : post_end]
            post_valid = post_errors[~np.isnan(post_errors)]
            post_mean = float(np.mean(post_valid)) if len(post_valid) > 0 else 0.0

            error_change = post_mean - pre_mean

            # 判断是否异常
            is_anomalous = abs(error_change) > self._sigma_threshold * error_std_global
            anomaly_direction = "positive" if error_change > 0 else "negative"

            if is_anomalous:
                # 检查方向一致性
                if event.expected_direction != "unknown":
                    direction_matches = (event.expected_direction == "positive" and error_change > 0) or (
                        event.expected_direction == "negative" and error_change < 0
                    )
                    if not direction_matches:
                        anomaly_direction = f"unexpected_{anomaly_direction}"

            result = EventPredictionError(
                event_id=event.event_id,
                event_name=event.name,
                event_type=event.event_type,
                event_date=str(event_date),
                expected_direction=event.expected_direction,
                pre_window=event.pre_window,
                post_window=event.post_window,
                pre_mean_error=pre_mean,
                post_mean_error=post_mean,
                error_change=error_change,
                error_std=error_std_global,
                is_anomalous=is_anomalous,
                anomaly_direction=anomaly_direction,
                n_pre_samples=len(pre_valid),
                n_post_samples=len(post_valid),
            )

            all_event_results.append(result)
            if is_anomalous:
                anomalous_events.append(result)

        # 汇总
        n_events = len(all_event_results)
        n_anomalous = len(anomalous_events)
        n_anomalous_consistent = sum(1 for e in anomalous_events if "unexpected" not in e["anomaly_direction"])

        summary = {
            "n_events_in_data": n_events,
            "n_anomalous": n_anomalous,
            "n_anomalous_consistent": n_anomalous_consistent,
            "anomaly_rate": n_anomalous / n_events if n_events > 0 else 0.0,
            "sigma_threshold": self._sigma_threshold,
            "global_error_std": float(error_std_global),
            "event_types_covered": list(set(e["event_type"] for e in all_event_results)),
        }

        # 将 event 对象转为可序列化 dict
        [_event_to_dict(e) for e in self._events]
        summary["event_ids"] = [e["event_id"] for e in all_event_results]

        return CausalValidationResult(
            factor_id=factor.get("factor_id", "unknown"),
            factor_name=factor.get("name", "unknown"),
            analysis_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            n_events=n_events,
            n_anomalous=n_anomalous,
            anomalous_events=anomalous_events,
            all_events=all_event_results,
            summary=summary,
        )

    @staticmethod
    def report(result: CausalValidationResult) -> str:
        """生成可读的因果结构审查报告。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f"因果结构审查报告 — {result['factor_name']} ({result['factor_id']})")
        lines.append(f"分析日期: {result['analysis_date']}")
        lines.append("=" * 70)

        s = result["summary"]
        lines.append("\n总览:")
        lines.append(f"  数据中事件数: {s['n_events_in_data']}")
        lines.append(f"  异常事件数:   {s['n_anomalous']}")
        lines.append(f"  方向一致异常: {s['n_anomalous_consistent']}")
        lines.append(f"  异常率:       {s['anomaly_rate']:.1%}")
        lines.append(f"  Sigma 阈值:   {s['sigma_threshold']}")
        lines.append(f"  全局误差 STD: {s['global_error_std']:.4f}")

        if result["anomalous_events"]:
            lines.append("\n\n异常事件详情:")
            for e in result["anomalous_events"]:
                consistency = "✅" if "unexpected" not in e["anomaly_direction"] else "❌"
                lines.append(f"  [{consistency}] {e['event_name']} ({e['event_id']})")
                lines.append(f"      类型: {e['event_type']:20s} | 预期方向: {e['expected_direction']:>8s}")
                lines.append(
                    f"      事件前误差: {e['pre_mean_error']:>8.4f} | "
                    f"事件后误差: {e['post_mean_error']:>8.4f} | "
                    f"变化: {e['error_change']:>8.4f}"
                )
                lines.append(
                    f"      异常方向: {e['anomaly_direction']:20s} | "
                    f"前后样本: {e['n_pre_samples']}/{e['n_post_samples']}"
                )

        if result["all_events"]:
            lines.append("\n\n所有事件:")
            lines.append(f"  {'事件':<30} {'类型':<18} {'前误差':>8} {'后误差':>8} {'变化':>8} {'异常':>6}")
            for e in result["all_events"]:
                flag = "⚠️" if e["is_anomalous"] else "  "
                lines.append(
                    f"  {flag} {e['event_name']:<28} {e['event_type']:<18} "
                    f"{e['pre_mean_error']:>8.4f} {e['post_mean_error']:>8.4f} "
                    f"{e['error_change']:>8.4f} {'是' if e['is_anomalous'] else '否':>6}"
                )

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


# ─── 工具函数 ──────────────────────────────────────────────


def _event_to_dict(event: Any) -> dict[str, Any]:
    """将 NaturalExperiment 对象转为普通 dict。"""
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_date": str(event.event_date),
        "symbol": event.symbol,
        "name": event.name,
        "expected_direction": event.expected_direction,
    }


__all__ = [
    "EventPredictionError",
    "CausalValidationResult",
    "CausalValidator",
]
