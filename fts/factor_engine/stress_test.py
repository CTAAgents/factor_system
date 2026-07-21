"""
fts.factor_engine.stress_test — 极端行情压力测试。

对当前因子组合执行预定义的压力场景回测，
评估在历史极端行情下的最大回撤和恢复能力。

用法:
    tester = StressTester()
    results = tester.run_all(portfolio_signals, ohlcv_data)

版本: v0.1.0
"""

from __future__ import annotations

from typing import TypedDict

import numpy as np
import pandas as pd


class StressScenario(TypedDict):
    """压力场景定义。"""
    name: str                      # 场景名称
    symbols: list[str]             # 涉及品种
    date_range: tuple[str, str]    # (start, end) ISO dates
    price_shock: float             # 最大价格冲击（%）
    vol_multiplier: float          # 波动率倍数


class StressTestResult(TypedDict, total=False):
    """单场景压力测试结果。"""
    scenario: str
    max_drawdown: float            # 该场景下最大回撤
    sharpe: float                  # 该场景下夏普
    recovery_days: int             # 恢复天数（0 = 未恢复）
    passed: bool                   # 是否通过（回撤 ≤ 40%）


# ─── 内置压力场景 ─────────────────────────────────────────

_BUILTIN_SCENARIOS: list[StressScenario] = [
    StressScenario(
        name="原油暴跌",
        symbols=["SC", "CL"],
        date_range=("2020-03-01", "2020-05-31"),
        price_shock=-300.0,
        vol_multiplier=3.0,
    ),
    StressScenario(
        name="双十一闪崩",
        symbols=["RB", "HC", "I", "J", "JM"],
        date_range=("2016-11-11", "2016-11-11"),
        price_shock=-5.0,
        vol_multiplier=5.0,
    ),
    StressScenario(
        name="股灾",
        symbols=["IF", "IH", "IC"],
        date_range=("2015-06-01", "2015-09-30"),
        price_shock=-45.0,
        vol_multiplier=2.0,
    ),
    StressScenario(
        name="疫情冲击",
        symbols=["*"],  # 全品种
        date_range=("2020-02-01", "2020-03-31"),
        price_shock=-30.0,
        vol_multiplier=3.0,
    ),
    StressScenario(
        name="供给侧改革",
        symbols=["RB", "HC", "I", "J", "JM"],
        date_range=("2016-01-01", "2016-12-31"),
        price_shock=50.0,
        vol_multiplier=1.0,
    ),
]

# 压力场景通过阈值（最大回撤 ≤ 40%）
_PASS_THRESHOLD = 0.40


class StressTester:
    """极端行情压力测试器。

    内置 5 个历史极端场景，对输入的组合信号
    逐一执行压力测试并输出结构化结果。
    """

    def __init__(self) -> None:
        """初始化压力测试器。

        自动加载内置场景列表。
        """
        self._scenarios = self.get_builtin_scenarios()

    @staticmethod
    def get_builtin_scenarios() -> list[StressScenario]:
        """返回预定义的内置压力场景。

        Returns:
            包含 5 个 StressScenario 的列表：
                - 原油暴跌: SC/CL, 2020-03~2020-05, -300%, 3.0x
                - 双十一闪崩: 商品期货, 2016-11-11, -5%, 5.0x
                - 股灾: 沪深300, 2015-06~2015-09, -45%, 2.0x
                - 疫情冲击: 全品种, 2020-02~2020-03, -30%, 3.0x
                - 供给侧改革: 黑色系, 2016, 50%, 1.0x
        """
        return list(_BUILTIN_SCENARIOS)

    def run_all(
        self,
        signals: dict[str, np.ndarray],
        ohlcv: dict[str, pd.DataFrame],
    ) -> list[StressTestResult]:
        """对所有内置场景执行压力测试。

        Args:
            signals: 品种 → 信号数组 的映射字典。
            ohlcv: 品种 → OHLCV DataFrame 的映射字典。

        Returns:
            每个场景的 StressTestResult 列表。
        """
        results: list[StressTestResult] = []
        for scenario in self._scenarios:
            result = self.run_scenario(scenario, signals, ohlcv)
            results.append(result)
        return results

    def run_scenario(
        self,
        scenario: StressScenario,
        signals: dict[str, np.ndarray],
        ohlcv: dict[str, pd.DataFrame],
    ) -> StressTestResult:
        """对单个压力场景执行测试。

        步骤:
            1. 筛选场景涉及品种的信号
            2. 将数据切片到场景日期范围
            3. 估算压力下最大回撤
            4. 计算场景夏普
            5. 估算恢复天数
            6. 判定是否通过

        Args:
            scenario: 压力场景定义。
            signals: 品种 → 信号数组。
            ohlcv: 品种 → OHLCV DataFrame。

        Returns:
            该场景的 StressTestResult。
        """
        # 收集场景涉及品种的信号
        scenario_signals: list[np.ndarray] = []
        scenario_returns: list[np.ndarray] = []

        for symbol, df in ohlcv.items():
            # 检查品种是否匹配场景（* 表示全品种）
            if scenario["symbols"] != ["*"] and symbol not in scenario["symbols"]:
                continue

            # 切片日期范围
            start_date = pd.Timestamp(scenario["date_range"][0])
            end_date = pd.Timestamp(scenario["date_range"][1])
            mask = (df.index >= start_date) & (df.index <= end_date)
            sliced = df.loc[mask]

            if len(sliced) < 2:
                continue

            # 计算日收益率
            closes = sliced["close"].values
            rets = (closes[1:] - closes[:-1]) / np.maximum(closes[:-1], 1e-10)
            scenario_returns.append(rets)

            # 获取对应品种的信号
            if symbol in signals:
                sig = signals[symbol]
                # 对齐信号长度
                sig_sliced = sig[-len(sliced):] if len(sig) >= len(sliced) else np.pad(
                    sig, (len(sliced) - len(sig), 0), mode="edge",
                )[:len(sliced)]
                scenario_signals.append(sig_sliced)
            else:
                scenario_signals.append(np.zeros(len(sliced)))

        if not scenario_returns or len(scenario_returns) == 0:
            # 无数据时返回空结果
            return StressTestResult(
                scenario=scenario["name"],
                max_drawdown=0.0,
                sharpe=0.0,
                recovery_days=0,
                passed=True,
            )

        # 合并所有品种的收益率
        all_returns = np.concatenate(scenario_returns)

        # 合并信号
        all_signals = (
            np.concatenate(scenario_signals)
            if scenario_signals
            else np.array([])
        )

        # 估算压力下最大回撤
        max_dd = self._estimate_drawdown_from_signals(
            all_signals, scenario["price_shock"],
        )

        # 计算夏普（场景收益率 * vol_multiplier 放大波动）
        if len(all_returns) > 1:
            daily_mean = float(np.mean(all_returns))
            daily_std = float(np.std(all_returns)) * scenario["vol_multiplier"]
            sharpe = daily_mean / max(daily_std, 1e-10) * np.sqrt(252)
        else:
            sharpe = 0.0

        # 恢复天数
        recovery_days = self._estimate_recovery_days(all_signals)

        # 通过判定（确保 Python bool 而非 numpy bool）
        passed = bool(max_dd <= _PASS_THRESHOLD)

        return StressTestResult(
            scenario=scenario["name"],
            max_drawdown=max_dd,
            sharpe=sharpe,
            recovery_days=recovery_days,
            passed=passed,
        )

    @staticmethod
    def _estimate_drawdown_from_signals(
        signals: np.ndarray,
        shock: float,
    ) -> float:
        """从信号行为估算压力下的最大回撤。

        逻辑:
            - 信号方向与价格冲击相反时，因子受损。
            - 信号绝对值越大（仓位越重），回撤越大。
            - shock 的符号与幅度决定损失方向。

        Args:
            signals: 因子信号数组。
            shock: 价格冲击百分比（如 -30 表示下跌 30%）。

        Returns:
            估算的最大回撤（0~1）。
        """
        if len(signals) == 0:
            return 0.0

        # 将 shock 从百分比转为小数
        shock_dec = abs(shock) / 100.0

        # 信号方向 vs 冲击方向:
        #   如果 shock < 0（下跌），信号 > 0（做多）= 损失
        #   如果 shock > 0（上涨），信号 < 0（做空）= 损失
        shock_direction = -1.0 if shock < 0 else 1.0
        # 方向不一致时受损
        aligned = signals * shock_direction
        # 只计受损部分（aligned < 0 表示方向错误）
        loss_fraction = np.mean(np.maximum(-aligned, 0))

        # 回撤 = 冲击幅度 * 方向错误比例 * 杠杆（通过平均信号绝对值估算）
        avg_abs_signal = float(np.mean(np.abs(signals)))
        leverage = max(avg_abs_signal, 0.1)

        drawdown = shock_dec * loss_fraction * leverage
        # 施加上限确保 0~1 范围
        return min(max(drawdown, 0.0), 1.0)

    @staticmethod
    def _estimate_recovery_days(signals: np.ndarray) -> int:
        """从信号自相关性估算恢复天数。

        逻辑:
            - 信号变化越快（换手率高），恢复速度越快。
            - 用信号的一阶差分评估。

        Args:
            signals: 因子信号数组。

        Returns:
            估算的恢复天数（非负整数）。
        """
        if len(signals) < 3:
            return 0

        # 计算信号的自相关性（滞后 1 期）
        sig = signals[~np.isnan(signals)]
        if len(sig) < 3:
            return 0

        autocorr = float(np.corrcoef(sig[:-1], sig[1:])[0, 1])
        # 高自相关 → 信号缓慢变化 → 恢复慢
        # 低自相关 → 信号快速变化 → 恢复快
        if autocorr > 0.8:
            recovery = 60
        elif autocorr > 0.5:
            recovery = 30
        elif autocorr > 0.2:
            recovery = 15
        else:
            recovery = 7

        # 结合信号波动调整
        vol = float(np.std(sig))
        if vol < 0.1:
            recovery = max(recovery, 10)

        return max(recovery, 0)
