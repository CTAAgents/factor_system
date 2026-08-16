"""
fts/monitor/logic_monitor.py — 逻辑监控仪表盘（Phase C 逻辑审查）

HARNESS §11-logic-review-plan.md §C.2:
    建立一套逻辑监控仪表盘，每天自动运行检查。

检查项:
    1. 因子行为漂移检测 — 计算因子输出与经典逻辑基准的相关性变化
    2. 极端预测占比 — 连续信号：z-score 归一后 |z|>2 样本占比超阈值报警；
       离散信号（唯一值 ≤20，如突破因子 {-1,0,+1}）：主导档位占比 ≥95% 退化报警
       （v2.104.0+72 双口径，修复离散信号 z-score 系统性误报）
    3. 换月日信号异常报警 — 换月日前后信号均值/方差与历史对比，超 3σ 报警

版本: v1.1.0
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from ..factor_engine.contracts import FactorProgram
from ..factor_engine.factor_program import FactorExecutor


# ─── 基准因子定义 ──────────────────────────────────────────


class _SimpleMomentum:
    """简单动量基准（N 日收益率）。"""

    @staticmethod
    def compute(close: np.ndarray, lookback: int = 20) -> np.ndarray:
        signal = np.full(len(close), np.nan)
        if len(close) > lookback:
            signal[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
        return signal


class _MeanReversion:
    """均值回归基准（N 日价格偏离度）。"""

    @staticmethod
    def compute(close: np.ndarray, lookback: int = 20) -> np.ndarray:
        signal = np.full(len(close), np.nan)
        if len(close) > lookback:
            rolling_mean = pd.Series(close).rolling(lookback).mean().values
            signal[lookback:] = (rolling_mean[lookback:] - close[lookback:]) / np.maximum(close[lookback:], 1e-10)
        return signal


# ─── 监控检查结果契约 ──────────────────────────────────────


@dataclass
class DriftCheckResult:
    """因子行为漂移检测结果。

    Attributes:
        factor_id: 因子 ID
        momentum_correlation: 与简单动量的相关系数
        mean_reversion_correlation: 与均值回归的相关系数
        is_drifted: 是否发生漂移（相关性低于阈值）
        drift_threshold: 漂移判断阈值
        n_samples: 有效样本数
    """

    factor_id: str
    momentum_correlation: float
    mean_reversion_correlation: float
    is_drifted: bool
    drift_threshold: float = 0.3
    n_samples: int = 0


@dataclass
class ExtremePredictionResult:
    """极端预测占比检测结果。

    Attributes:
        factor_id: 因子 ID
        total_samples: 总样本数
        extreme_positive: 极端正向预测样本数
        extreme_negative: 极端负向预测样本数
        extreme_ratio: 极端预测占比
        threshold: 报警阈值
        is_alarmed: 是否触发报警
        method: 检测口径（"zscore" 连续信号 z-score / "discrete" 离散信号主导档位退化检测）
        dominant_ratio: 离散口径下主导档位占比（连续口径为 NaN）
        discrete_nunique: 离散口径下唯一值数量（连续口径为 None）
    """

    factor_id: str
    total_samples: int
    extreme_positive: int
    extreme_negative: int
    extreme_ratio: float
    threshold: float = 0.05
    is_alarmed: bool = False
    method: str = "zscore"
    dominant_ratio: float = float("nan")
    discrete_nunique: Optional[int] = None


@dataclass
class ContractSwitchResult:
    """换月日信号异常检测结果。

    Attributes:
        factor_id: 因子 ID
        switch_dates: 换月日列表
        mean_before: 换月前信号均值
        mean_after: 换月后信号均值
        mean_change: 均值变化
        std_before: 换月前信号标准差
        std_after: 换月后信号标准差
        std_change_ratio: 标准差变化比率
        is_anomalous: 是否异常
        sigma_threshold: sigma 阈值
        n_switches: 换月次数
    """

    factor_id: str
    switch_dates: list[str]
    mean_before: float
    mean_after: float
    mean_change: float
    std_before: float
    std_after: float
    std_change_ratio: float
    is_anomalous: bool
    sigma_threshold: float = 3.0
    n_switches: int = 0


@dataclass
class LogicMonitorResult:
    """逻辑监控完整结果。

    Attributes:
        factor_id: 因子 ID
        checked_at: 检查时间
        drift: 漂移检测结果
        extreme_prediction: 极端预测检测结果
        contract_switch: 换月日检测结果
        all_healthy: 所有检查项是否健康
    """

    factor_id: str
    checked_at: str
    drift: DriftCheckResult
    extreme_prediction: ExtremePredictionResult
    contract_switch: Optional[ContractSwitchResult]
    all_healthy: bool


# ─── 逻辑监控器 ────────────────────────────────────────────


class LogicMonitor:
    """逻辑监控仪表盘执行器。

    每日收盘后自动运行，检查因子行为是否健康。

    Usage:
        monitor = LogicMonitor()
        result = monitor.run(factor, data, switch_dates=[])
        print(monitor.format_report(result))
    """

    DRIFT_THRESHOLD: float = 0.3
    EXTREME_RATIO_THRESHOLD: float = 0.05
    CONTRACT_SWITCH_SIGMA: float = 3.0
    # 离散信号判定与退化告警（2026-08-16 v2.104.0+72，GAP-121 逻辑监控口径修正）：
    # 离散三态信号（如突破因子 {-1,0,+1}）经 z-score 归一后全部非零档位天然落入 |z|>2
    # 极端区，产生系统性误报。改为唯一值数量判定离散性，用"主导档位占比"检测退化
    # （单一档位占比 ≥ 阈值 → 信号退化为近常数，因子失效）而非 z-score 极端值。
    DISCRETE_NUNIQUE_THRESHOLD: int = 20
    DISCRETE_DOMINANT_THRESHOLD: float = 0.95

    def __init__(
        self,
        drift_threshold: float = 0.3,
        extreme_ratio_threshold: float = 0.05,
        contract_switch_sigma: float = 3.0,
        discrete_nunique_threshold: int = 20,
        discrete_dominant_threshold: float = 0.95,
    ):
        """
        Args:
            drift_threshold: 漂移判断阈值（与基准的相关性低于此值视为漂移，默认 0.3）
            extreme_ratio_threshold: 极端预测占比报警阈值（默认 5%）
            contract_switch_sigma: 换月日异常 sigma 阈值（默认 3.0）
            discrete_nunique_threshold: 信号唯一值数量 ≤ 此值判定为离散信号（默认 20）
            discrete_dominant_threshold: 离散信号主导档位占比告警阈值（默认 0.95）
        """
        self._drift_threshold = drift_threshold
        self._extreme_ratio_threshold = extreme_ratio_threshold
        self._contract_switch_sigma = contract_switch_sigma
        self._discrete_nunique_threshold = discrete_nunique_threshold
        self._discrete_dominant_threshold = discrete_dominant_threshold

    def run(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        switch_dates: Optional[list[str]] = None,
    ) -> LogicMonitorResult:
        """执行全部逻辑监控检查。

        Args:
            factor: 因子程序
            data: OHLCV 数据（必须包含 close 列）
            switch_dates: 换月日日期列表（字符串格式 "YYYY-MM-DD"）

        Returns:
            LogicMonitorResult
        """
        factor_id = factor.get("factor_id", "unknown")

        # 获取因子信号
        executor = FactorExecutor(factor)
        signals = executor.execute(data, {})
        if len(signals) != len(data):
            signals = np.full(len(data), np.nan)

        # 检查 1: 因子行为漂移
        drift = self._check_drift(factor_id, data, signals)

        # 检查 2: 极端预测占比
        extreme = self._check_extreme(factor_id, signals)

        # 检查 3: 换月日信号异常
        contract = self._check_contract_switch(
            factor_id,
            data,
            signals,
            switch_dates or [],
        )

        all_healthy = (
            not drift.is_drifted and not extreme.is_alarmed and (contract is None or not contract.is_anomalous)
        )

        return LogicMonitorResult(
            factor_id=factor_id,
            checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            drift=drift,
            extreme_prediction=extreme,
            contract_switch=contract,
            all_healthy=all_healthy,
        )

    def _check_drift(
        self,
        factor_id: str,
        data: pd.DataFrame,
        signals: np.ndarray,
    ) -> DriftCheckResult:
        """检查因子行为漂移。"""
        close = data["close"].values

        mom_signal = _SimpleMomentum.compute(close)
        mrev_signal = _MeanReversion.compute(close)

        valid = ~np.isnan(signals) & ~np.isnan(mom_signal) & ~np.isnan(mrev_signal)

        n_samples = int(np.sum(valid))

        if n_samples < 10:
            return DriftCheckResult(
                factor_id=factor_id,
                momentum_correlation=0.0,
                mean_reversion_correlation=0.0,
                is_drifted=True,
                drift_threshold=self._drift_threshold,
                n_samples=n_samples,
            )

        mom_corr = float(np.corrcoef(signals[valid], mom_signal[valid])[0, 1])
        mrev_corr = float(np.corrcoef(signals[valid], mrev_signal[valid])[0, 1])

        # 取最大相关性作为基准相关性
        max_corr = max(abs(mom_corr), abs(mrev_corr))
        is_drifted = max_corr < self._drift_threshold

        return DriftCheckResult(
            factor_id=factor_id,
            momentum_correlation=float(mom_corr),
            mean_reversion_correlation=float(mrev_corr),
            is_drifted=is_drifted,
            drift_threshold=self._drift_threshold,
            n_samples=n_samples,
        )

    def _check_extreme(
        self,
        factor_id: str,
        signals: np.ndarray,
    ) -> ExtremePredictionResult:
        """检查极端预测占比（连续信号 z-score / 离散信号主导档位退化双口径）。

        连续信号（唯一值数量 > discrete_nunique_threshold）沿用 z-score 口径：
        归一化后 |z|>2 的样本占比超过阈值报警。
        离散信号（唯一值数量 ≤ 阈值，如突破因子 {-1,0,+1}）改用主导档位退化
        检测：计算各档位占比，单一档位占比 ≥ discrete_dominant_threshold 视为
        信号退化为近常数（因子失效）报警；正常离散分布不报警——修复 z-score
        对离散信号全部非零档位落入极端区的系统性误报（2026-08-16 能源链 22.1%）。
        """
        valid = signals[~np.isnan(signals)]
        total = len(valid)

        if total == 0:
            return ExtremePredictionResult(
                factor_id=factor_id,
                total_samples=0,
                extreme_positive=0,
                extreme_negative=0,
                extreme_ratio=0.0,
                threshold=self._extreme_ratio_threshold,
                is_alarmed=False,
            )

        # 离散信号判定：唯一值数量少 → 分箱口径
        nunique = len(np.unique(valid))
        if nunique <= self._discrete_nunique_threshold:
            return self._check_extreme_discrete(factor_id, valid, total, nunique)

        # 连续信号：信号标准化后判断极端值
        sig_mean = np.mean(valid)
        sig_std = np.std(valid)
        if sig_std > 0:
            normalized = (valid - sig_mean) / sig_std
        else:
            normalized = valid

        extreme_positive = int(np.sum(normalized > 2.0))  # > 2σ
        extreme_negative = int(np.sum(normalized < -2.0))  # < -2σ
        extreme_ratio = (extreme_positive + extreme_negative) / total

        is_alarmed = extreme_ratio > self._extreme_ratio_threshold

        return ExtremePredictionResult(
            factor_id=factor_id,
            total_samples=total,
            extreme_positive=extreme_positive,
            extreme_negative=extreme_negative,
            extreme_ratio=float(extreme_ratio),
            threshold=self._extreme_ratio_threshold,
            is_alarmed=is_alarmed,
            method="zscore",
        )

    def _check_extreme_discrete(
        self,
        factor_id: str,
        valid: np.ndarray,
        total: int,
        nunique: int,
    ) -> ExtremePredictionResult:
        """离散信号极端检测（主导档位退化口径）。

        对离散信号（如 {-1,0,+1} 突破因子），z-score 归一无统计意义（全部非零
        档位天然 |z|>2）。改为统计各档位占比，检测主导档位占比是否 ≥ 阈值——
        仅当信号退化为近常数（单一档位占比异常高，因子失效）时报警。
        """
        _, counts = np.unique(valid, return_counts=True)
        dominant_ratio = float(np.max(counts)) / total
        is_alarmed = dominant_ratio >= self._discrete_dominant_threshold

        return ExtremePredictionResult(
            factor_id=factor_id,
            total_samples=total,
            extreme_positive=0,
            extreme_negative=0,
            extreme_ratio=dominant_ratio,
            threshold=self._discrete_dominant_threshold,
            is_alarmed=is_alarmed,
            method="discrete",
            dominant_ratio=dominant_ratio,
            discrete_nunique=nunique,
        )

    def _check_contract_switch(
        self,
        factor_id: str,
        data: pd.DataFrame,
        signals: np.ndarray,
        switch_dates: list[str],
    ) -> Optional[ContractSwitchResult]:
        """检查换月日信号异常。"""
        if not switch_dates or "date" not in data.columns:
            return None

        dates = pd.to_datetime(data["date"])
        date_strs = [d.strftime("%Y-%m-%d") for d in dates]

        window = 5  # 前后各 5 个交易日
        before_signals: list[float] = []
        after_signals: list[float] = []

        switch_found: list[str] = []

        for sw_date in switch_dates:
            if sw_date not in date_strs:
                continue

            idx = date_strs.index(sw_date)
            switch_found.append(sw_date)

            # 事件前信号
            pre_start = max(0, idx - window)
            for i in range(pre_start, idx):
                if not np.isnan(signals[i]):
                    before_signals.append(float(signals[i]))

            # 事件后信号
            post_end = min(len(signals), idx + window + 1)
            for i in range(idx + 1, post_end):
                if not np.isnan(signals[i]):
                    after_signals.append(float(signals[i]))

        if len(before_signals) < 3 or len(after_signals) < 3:
            return ContractSwitchResult(
                factor_id=factor_id,
                switch_dates=switch_found,
                mean_before=float(np.mean(before_signals)) if before_signals else 0.0,
                mean_after=float(np.mean(after_signals)) if after_signals else 0.0,
                mean_change=0.0,
                std_before=0.0,
                std_after=0.0,
                std_change_ratio=0.0,
                is_anomalous=False,
                sigma_threshold=self._contract_switch_sigma,
                n_switches=len(switch_found),
            )

        mean_before = float(np.mean(before_signals))
        mean_after = float(np.mean(after_signals))
        mean_change = mean_after - mean_before

        std_before = float(np.std(before_signals))
        std_after = float(np.std(after_signals))
        std_change_ratio = std_after / max(std_before, 1e-10)

        # 均值变化超过 3σ 视为异常
        signal_std = float(np.std(before_signals + after_signals))
        is_anomalous = abs(mean_change) > self._contract_switch_sigma * max(signal_std, 1e-10)

        return ContractSwitchResult(
            factor_id=factor_id,
            switch_dates=switch_found,
            mean_before=mean_before,
            mean_after=mean_after,
            mean_change=mean_change,
            std_before=std_before,
            std_after=std_after,
            std_change_ratio=float(std_change_ratio),
            is_anomalous=is_anomalous,
            sigma_threshold=self._contract_switch_sigma,
            n_switches=len(switch_found),
        )

    @staticmethod
    def format_report(result: LogicMonitorResult) -> str:
        """生成可读的监控报告。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f"逻辑监控报告 — {result.factor_id}")
        lines.append(f"检查时间: {result.checked_at}")
        lines.append(f"总体健康: {'✅ 是' if result.all_healthy else '❌ 否'}")
        lines.append("=" * 70)

        # 1. 漂移检测
        d = result.drift
        lines.append("\n1. 因子行为漂移检测")
        lines.append(f"   与动量相关性:   {d.momentum_correlation:>8.4f}")
        lines.append(f"   与均值回归相关性: {d.mean_reversion_correlation:>8.4f}")
        lines.append(f"   是否漂移:        {'是' if d.is_drifted else '否'}")
        lines.append(f"   漂移阈值:        {d.drift_threshold}")
        lines.append(f"   有效样本数:      {d.n_samples}")

        # 2. 极端预测
        e = result.extreme_prediction
        lines.append("\n2. 极端预测占比检测")
        lines.append(f"   总样本数:        {e.total_samples}")
        lines.append(f"   极端正向:        {e.extreme_positive}")
        lines.append(f"   极端负向:        {e.extreme_negative}")
        lines.append(f"   极端占比:        {e.extreme_ratio:.2%}")
        lines.append(f"   报警阈值:        {e.threshold:.2%}")
        lines.append(f"   检测口径:        {'离散-主导档位退化' if e.method == 'discrete' else '连续-zscore'}")
        if e.method == "discrete":
            lines.append(f"   主导档位占比:    {e.dominant_ratio:.2%}（唯一值 {e.discrete_nunique} 个）")
        lines.append(f"   是否报警:        {'⚠️ 是' if e.is_alarmed else '否'}")

        # 3. 换月日
        c = result.contract_switch
        if c is not None:
            lines.append("\n3. 换月日信号异常检测")
            lines.append(f"   换月次数:        {c.n_switches}")
            lines.append(f"   换月前均值:      {c.mean_before:>8.4f}")
            lines.append(f"   换月后均值:      {c.mean_after:>8.4f}")
            lines.append(f"   均值变化:        {c.mean_change:>8.4f}")
            lines.append(f"   标准差变化比:    {c.std_change_ratio:>8.2f}")
            lines.append(f"   是否异常:        {'⚠️ 是' if c.is_anomalous else '否'}")
            if c.switch_dates:
                lines.append(f"   换月日:          {', '.join(c.switch_dates[:5])}")
                if len(c.switch_dates) > 5:
                    lines.append(f"                    ... 共 {len(c.switch_dates)} 个")
        else:
            lines.append("\n3. 换月日信号异常检测: 未执行（无换月日数据）")

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


__all__ = [
    "DriftCheckResult",
    "ExtremePredictionResult",
    "ContractSwitchResult",
    "LogicMonitorResult",
    "LogicMonitor",
]
