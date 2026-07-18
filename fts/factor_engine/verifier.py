"""
loop_engine/verifier.py — Verifier 协议（锁定的评估机制）

HARNESS §11-loop-engineering.md §6:
    Verifier 是 Loop Engineering 的核心原语：评估机制一旦锁定，
    任何 LLM 调用、参数演化、人类干预都不可修改 Verifier 的判定逻辑。

设计要点:
    1. 初始化后 _locked = True，任何配置修改尝试抛 RuntimeError
    2. check() 严格按 VerifierConfig 判定，不接受任何 override
    3. 判定结果含 checked_against 快照，可审计

版本: v8.10.0
"""
# pylint: disable=too-many-branches

from __future__ import annotations

from datetime import datetime

from .contracts import (
    DEFAULT_VERIFIER_CONFIG,
    FactorEvaluation,
    VerifierConfig,
    VerifierResult,
)


class VerifierNotLockedError(RuntimeError):
    """Verifier 未锁定时调用 check() 抛出。"""


class VerifierAlreadyLockedError(RuntimeError):
    """Verifier 已锁定时尝试修改配置抛出。"""


class FactorVerifier:
    """锁定的因子评估 Verifier — 一旦初始化不可修改。

    HARNESS 角色边界:
        - Verifier 只做评估判定
        - 禁止接受任何 override / 修改自身配置
        - 判定结果可被经验链追溯，但不可被覆盖

    Usage:
        verifier = FactorVerifier(DEFAULT_VERIFIER_CONFIG)
        result = verifier.check(evaluation)
        if not result["passed"]:
            print(result["failure_reasons"])
    """

    def __init__(self, config: VerifierConfig | None = None):
        """初始化 Verifier 并立即锁定。

        Args:
            config: Verifier 配置（None = 使用 DEFAULT_VERIFIER_CONFIG）
        """
        self._config: VerifierConfig = dict(config or DEFAULT_VERIFIER_CONFIG)  # type: ignore[assignment]
        self._locked: bool = True  # 立即锁定

    @property
    def config(self) -> VerifierConfig:
        """返回配置的只读副本。"""
        return dict(self._config)  # type: ignore[return-value]

    @property
    def locked(self) -> bool:
        return self._locked

    def update_config(self, new_config: VerifierConfig) -> None:
        """尝试更新配置 — 锁定后调用必抛 RuntimeError。"""
        if self._locked:
            raise VerifierAlreadyLockedError(
                "Verifier 已锁定，禁止修改配置。"
                "如需更改 Verifier 配置，必须 bump 版本号并更新 docs/harness/11-loop-engineering.md"
            )
        self._config = dict(new_config)  # type: ignore[assignment]

    def unlock(self) -> None:
        """解锁 Verifier — 仅用于测试，生产环境禁止调用。"""
        self._locked = False

    def check(self, evaluation: FactorEvaluation) -> VerifierResult:
        """严格按配置判定 FactorEvaluation 是否通过。

        Args:
            evaluation: 三级评估链的输出

        Returns:
            VerifierResult: {
                "passed": bool,
                "failure_reasons": list[str],
                "checked_against": VerifierConfig,
                "checked_at": str
            }

        Raises:
            VerifierNotLockedError: Verifier 未锁定时调用
        """
        if not self._locked:
            raise VerifierNotLockedError(
                "Verifier 未锁定，禁止判定。请调用 __init__ 后自动锁定。"
            )

        reasons: list[str] = []
        cfg = self._config

        # Level 1: 回测验证
        bt = evaluation.get("level_1_backtest", {})
        if bt:
            if bt.get("ic", 0.0) < cfg["min_ic"]:
                reasons.append(
                    f"Level 1 失败: IC={bt.get('ic', 0.0):.4f} < {cfg['min_ic']}"
                )
            if bt.get("icir", 0.0) < cfg["min_icir"]:
                reasons.append(
                    f"Level 1 失败: ICIR={bt.get('icir', 0.0):.4f} < {cfg['min_icir']}"
                )
            if bt.get("sharpe", 0.0) < cfg["min_sharpe"]:
                reasons.append(
                    f"Level 1 失败: 夏普={bt.get('sharpe', 0.0):.4f} < {cfg['min_sharpe']}"
                )
            if bt.get("max_drawdown", 1.0) > cfg["max_drawdown"]:
                reasons.append(
                    f"Level 1 失败: 最大回撤={bt.get('max_drawdown', 1.0):.4f} > {cfg['max_drawdown']}"
                )
            if bt.get("oos_ratio", 0.0) < cfg["min_oos_ratio"]:
                reasons.append(
                    f"Level 1 失败: 样本外比例={bt.get('oos_ratio', 0.0):.4f} < {cfg['min_oos_ratio']}"
                )
            if bt.get("turnover_monthly", 1.0) > cfg["max_turnover_monthly"]:
                reasons.append(
                    f"Level 1 失败: 月度换手率={bt.get('turnover_monthly', 1.0):.4f} > {cfg['max_turnover_monthly']}"
                )
            if not bt.get("monotonicity", False):
                reasons.append("Level 1 失败: 十分位组合非单调")

        # Level 2: 经济逻辑
        ec = evaluation.get("level_2_economic", {})
        if ec:
            dims_passed = ec.get("dimensions_passed", 0)
            if dims_passed < cfg["min_economic_score"]:
                reasons.append(
                    f"Level 2 失败: 经济逻辑维度达标数={dims_passed} < {cfg['min_economic_score']}"
                )

        # Level 3: 多重检验
        mt = evaluation.get("level_3_multiple", {})
        if mt:
            if not mt.get("passed", False):
                reasons.append("Level 3 失败: 多重检验未通过")
            if mt.get("adjusted_t", 0.0) < cfg["min_t_stat"]:
                reasons.append(
                    f"Level 3 失败: 调整后 t={mt.get('adjusted_t', 0.0):.4f} < {cfg['min_t_stat']}"
                )
            if mt.get("fdr_q", 1.0) > cfg["max_fdr"]:
                reasons.append(
                    f"Level 3 失败: FDR={mt.get('fdr_q', 1.0):.4f} > {cfg['max_fdr']}"
                )

        # 整体判定（不接受任何 override）
        passed = len(reasons) == 0
        return VerifierResult(
            passed=passed,
            failure_reasons=reasons,
            checked_against=dict(self._config),  # type: ignore[typeddict-item]
            checked_at=datetime.now().isoformat(),
        )


# ─── 全局单例（v8.10.0 锁定值） ────────────────────────────

_GLOBAL_VERIFIER: FactorVerifier | None = None


def get_global_verifier() -> FactorVerifier:
    """获取全局 Verifier 单例 — v8.10.0 锁定值。

    使用 DEFAULT_VERIFIER_CONFIG，全局共享一个实例。
    """
    global _GLOBAL_VERIFIER  # pylint: disable=global-statement
    if _GLOBAL_VERIFIER is None:
        _GLOBAL_VERIFIER = FactorVerifier(DEFAULT_VERIFIER_CONFIG)
    return _GLOBAL_VERIFIER


def reset_global_verifier() -> None:
    """重置全局 Verifier — 仅用于测试。"""
    global _GLOBAL_VERIFIER  # pylint: disable=global-statement
    _GLOBAL_VERIFIER = None


__all__ = [
    "FactorVerifier",
    "VerifierNotLockedError",
    "VerifierAlreadyLockedError",
    "get_global_verifier",
    "reset_global_verifier",
]
