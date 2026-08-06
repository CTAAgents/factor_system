"""
loop_engine/portfolio_loop.py — L3 Portfolio Loop 主循环

HARNESS §11-loop-engineering.md §16:
    L3 Portfolio Loop — 组合构建（因子筛选 + 信号合成 + Verifier 校验）

流程:
    Step 1: 加载 elite 因子 → 从 futures_elite 目录读取因子 JSON
    Step 2: 信号合成 → 三种权重模式:
        - equal_weight: 等权 1/N
        - sharpe_weight: 按 Sharpe 比率归一化加权（期货默认）
        - elastic_net: Elastic Net 截面回归（CSI300 面板，L1+L2）
    Step 3: Verifier 校验 → 组合夏普、因子相关性、换手率
    Step 4: 输出 PortfolioCombo → 期货场景自动触发信号管道

信号管道（期货专用，由 CLI 在 L3 完成后触发）:
    scripts/futures_signal_pipeline.py — 独立于本模块的 Ridge 回归加权:
        - 方向校正: 截面 IC 法（Spearman 秩相关 vs 未来 5 日收益）
        - 权重学习: Ridge 回归（L2 正则化，弱因子保留不丢弃）
        - 输出: 多空双向信号排名 → reports/{date}/futures_signals_*.md

Verifier 阈值:
    - 组合夏普 > 2.0
    - 因子间最大相关性 < 0.3
    - 组合换手率 < 50%/月

版本: v1.2.0（与 FTS 同步）
"""
# pylint: disable=broad-exception-caught,too-few-public-methods,too-many-instance-attributes,too-many-locals

from __future__ import annotations

import argparse
import json
import logging
import secrets
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .contracts import (
    EVOLUTION_VERSION,
    STATE_SCHEMA_VERSION,
    DEFAULT_L3_VERIFIER_CONFIG,
    DEFAULT_L3_BUDGET,
    DEFAULT_VERIFIER_CONFIG,
    DEFAULT_STICKY_CONFIG,
    AgentOptimizationProposal,
    DriftMetrics,
    FactorCorrelation,
    L3MetaLoopState,
    L3VerifierConfig,
    PortfolioCombo,
    PortfolioSignal,
    StickyConfig,
)
from .state import generate_run_id, generate_trace_id

logger = logging.getLogger(__name__)


# ─── 异常 ──────────────────────────────────────────────────

class L3Error(Exception):
    """L3 Portfolio Loop 操作失败。"""


# ─── 常量 ──────────────────────────────────────────────────

STATE_FILE_NAME: str = "state.json"
BACKUP_FILE_NAME: str = "state.json.backup"
COMBO_FILE_NAME: str = "current_combo.json"
PROPOSALS_DIR: str = "agent_proposals"
COMBO_HISTORY_DIR: str = "combo_history"
DRIFT_HISTORY_DIR: str = "drift_history"

# 影子池观察期（交易日数）— L2 新晋升因子先进影子池观察，期满后才进正式组合
SHADOW_OBSERVE_TRADING_DAYS: int = 5


# ─── Verifier ──────────────────────────────────────────────

class L3Verifier:
    """L3 组合构建 Verifier — 一旦初始化不可修改。

    判定维度:
        1. combo_sharpe >= config.min_sharpe
        2. max_correlation <= config.max_correlation
        3. combo_turnover <= config.max_turnover
        4. 每个信号 decay_6m <= config.max_decay_rate
        5. n_factors >= config.min_n_factors
    """

    def __init__(self, config: L3VerifierConfig):
        self._locked = True
        self._config = config

    def check(self, combo: PortfolioCombo) -> tuple[bool, list[str]]:
        """执行 Verifier 判定。"""
        if not self._locked:
            raise RuntimeError("L3 Verifier 未锁定")
        reasons: list[str] = []

        # 维度 1: 组合夏普
        if combo.get("combo_sharpe", 0) < self._config.get("min_sharpe", 2.0):
            reasons.append(
                f"组合夏普 {combo.get('combo_sharpe', 0):.2f} < {self._config['min_sharpe']}"
            )

        # 维度 2: 最大相关性
        if combo.get("max_correlation", 1.0) > self._config.get("max_correlation", 0.3):
            reasons.append(
                f"最大相关性 {combo.get('max_correlation', 1.0):.2f} > {self._config['max_correlation']}"
            )

        # 维度 3: 组合换手率
        if combo.get("combo_turnover", 1.0) > self._config.get("max_turnover", 0.5):
            reasons.append(
                f"组合换手率 {combo.get('combo_turnover', 1.0):.2f} > {self._config['max_turnover']}"
            )

        # 维度 4: 衰减率（各信号逐一检查）
        for sig in combo.get("signals", []):
            if sig.get("retained", True) and sig.get("decay_6m", 0) > self._config.get("max_decay_rate", 0.3):
                reasons.append(
                    f"因子 {sig.get('name', '?')} 衰减率 {sig.get('decay_6m', 0):.2f} > {self._config['max_decay_rate']}"
                )

        # 维度 5: 最少因子数
        retained = sum(1 for s in combo.get("signals", []) if s.get("retained", True))
        if retained < self._config.get("min_n_factors", 3):
            reasons.append(f"保留因子数 {retained} < {self._config['min_n_factors']}")

        return (len(reasons) == 0), reasons


# ─── 组合状态管理器 ───────────────────────────────────────

class PortfolioStateManager:
    """L3 组合状态持久化 — 先写主文件再镜像 backup。"""

    def __init__(self, memory_dir: str | Path = "memory/portfolio"):
        self.memory_dir = Path(memory_dir)
        self.state_file = self.memory_dir / STATE_FILE_NAME
        self.backup_file = self.memory_dir / BACKUP_FILE_NAME
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load_or_init(self) -> L3MetaLoopState:
        state = self._try_load(self.state_file)
        if state is None:
            state = self._try_load(self.backup_file)
            if state is not None:
                self._write(state)
            else:
                state = self._init_state()
                self._write(state)
        return state

    def save(self, state: L3MetaLoopState) -> None:
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise L3Error(
                f"状态 schema 版本不匹配: {state.get('schema_version')} != {STATE_SCHEMA_VERSION}"
            )
        state["last_updated"] = datetime.now().isoformat()
        self._write(state)
        try:
            shutil.copy2(self.state_file, self.backup_file)
        except OSError as e:
            raise L3Error(f"备份失败: {e}") from e

    def mark_running(self, run_id: Optional[str] = None) -> L3MetaLoopState:
        state = self.load_or_init()
        state["run_id"] = run_id or generate_run_id()
        state["started_at"] = datetime.now().isoformat()
        state["status"] = "running"
        state["last_error"] = None
        self.save(state)
        return state

    def mark_completed(self, state: L3MetaLoopState) -> None:
        state["status"] = "completed"
        self.save(state)

    def mark_circuit_broken(self, state: L3MetaLoopState, reason: str) -> None:
        state["status"] = "circuit_broken"
        state["last_error"] = reason
        self.save(state)

    def _try_load(self, fp: Path) -> L3MetaLoopState | None:
        if not fp.exists():
            return None
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if data.get("schema_version") != STATE_SCHEMA_VERSION:
                return None
            return L3MetaLoopState(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def _write(self, state: L3MetaLoopState) -> None:
        self.state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    @staticmethod
    def _init_state() -> L3MetaLoopState:
        return L3MetaLoopState(
            run_id=generate_run_id(),
            started_at=datetime.now().isoformat(),
            last_synthesis_mode="",
            total_signals_processed=0,
            total_signals_retained=0,
            total_proposals_generated=0,
            tokens_consumed=0,
            budget_limit=DEFAULT_L3_BUDGET,
            status="running",
            last_error=None,
            combo_ref=[],
            last_updated=datetime.now().isoformat(),
            schema_version=STATE_SCHEMA_VERSION,
        )


# ─── 组合管理器 ───────────────────────────────────────────

class PortfolioManager:
    """管理组合文件（memory/portfolio/current_combo.json + 历史归档）。"""

    def __init__(self, portfolio_dir: str | Path = "memory/portfolio"):
        self.portfolio_dir = Path(portfolio_dir)
        self.combo_file = self.portfolio_dir / COMBO_FILE_NAME
        self.proposals_dir = self.portfolio_dir / PROPOSALS_DIR
        self.combo_history_dir = self.portfolio_dir / COMBO_HISTORY_DIR
        self.portfolio_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.combo_history_dir.mkdir(parents=True, exist_ok=True)
        self._cache: PortfolioCombo | None = None

    def load_or_init(self) -> PortfolioCombo:
        if self._cache is not None:
            return self._cache
        if self.combo_file.exists():
            try:
                data = json.loads(self.combo_file.read_text(encoding="utf-8"))
                self._cache = PortfolioCombo(**data)
                return self._cache
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        # 初始化空组合
        combo = PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at=datetime.now().isoformat(),
            combo_id=f"cmb_{secrets.token_hex(4)}",
            trace_id=generate_trace_id("l3"),
            synthesis_mode="equal_weight",
            signals=[],
            combo_sharpe=0.0,
            combo_turnover=0.0,
            max_correlation=0.0,
            n_factors=0,
            status="pending",
            created_at=datetime.now().isoformat(),
        )
        self._cache = combo
        return combo

    def save_combo(self, combo: PortfolioCombo) -> None:
        # 保存前归档旧组合，供漂移对比 / 粘性约束读取上次权重
        if self.combo_file.exists():
            try:
                old = json.loads(self.combo_file.read_text(encoding="utf-8"))
                old_id = old.get("combo_id") or "unknown"
                hist_fp = self.combo_history_dir / f"{old_id}.json"
                if not hist_fp.exists():
                    hist_fp.write_text(
                        json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8",
                    )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        self._cache = combo
        self.combo_file.write_text(
            json.dumps(combo, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def load_prev_combo(self) -> PortfolioCombo | None:
        """读取上一次组合（current_combo.json 覆盖前的历史归档）。

        Returns:
            最近一次历史组合；无历史返回 None（冷启动）。
        """
        # 优先读磁盘上当前 combo 覆盖前的最新归档（combo_history 中时间最新者）
        history_files = sorted(
            self.combo_history_dir.glob("*.json"),
            key=lambda fp: fp.stat().st_mtime, reverse=True,
        )
        if not history_files:
            return None
        try:
            data = json.loads(history_files[0].read_text(encoding="utf-8"))
            return PortfolioCombo(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def extract_prev_weights(self, prev_combo: PortfolioCombo | None) -> dict[str, float]:
        """从历史组合提取 {factor_id: weight}，供粘性约束使用。"""
        if not prev_combo:
            return {}
        return {
            s.get("factor_id"): s.get("weight", 0.0)
            for s in prev_combo.get("signals", [])
            if s.get("retained", True) and s.get("factor_id")
        }

    def save_proposal(self, proposal: AgentOptimizationProposal) -> str:
        """保存 Agent 优化建议，返回文件路径。"""
        pid = proposal.get("proposal_id", f"prop_{secrets.token_hex(4)}")
        fp = self.proposals_dir / f"{pid}.json"
        fp.write_text(
            json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return str(fp)

    def list_active_proposals(self) -> list[AgentOptimizationProposal]:
        """列出所有 draft 状态的 Agent 优化建议。"""
        proposals: list[AgentOptimizationProposal] = []
        if not self.proposals_dir.exists():
            return proposals
        for fp in sorted(self.proposals_dir.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                if data.get("status") == "draft":
                    proposals.append(AgentOptimizationProposal(**data))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return proposals


# ─── Regime 自适应权重 ────────────────────────────────────

# Regime → FactorFamily 权重倍率映射表
# 基于优化计划 A.3: 不同市场制度下因子家族的表现差异
REGIME_FAMILY_MULTIPLIERS: dict[str, dict[str, float]] = {
    "bull": {
        "trend": 1.3,           # 趋势因子 +30%
        "momentum": 1.3,        # 动量因子 +30%
        "breakout": 1.2,        # 突破因子 +20%
        "carry": 1.1,           # 跨期套利 +10%
        "cross_section": 1.1,   # 横截面因子 +10%
        "fundamental": 1.0,     # 基本面不变
        "mean_reversion": 0.7,  # 均值回归 -30%
        "volatility": 0.9,      # 波动率因子 -10%
    },
    "bear": {
        "trend": 1.1,           # 趋势因子 +10%（空头趋势仍有效）
        "momentum": 0.8,        # 动量因子 -20%（反转风险）
        "breakout": 0.7,        # 突破因子 -30%
        "carry": 1.0,           # 跨期套利不变
        "volatility": 1.3,      # 波动率因子 +30%（防御）
        "mean_reversion": 1.2,  # 均值回归 +20%
        "liquidity": 1.2,       # 流动性因子 +20%
        "fundamental": 1.1,     # 基本面 +10%
    },
    "oscillate": {
        "mean_reversion": 1.3,  # 均值回归 +30%（震荡市核心）
        "reversal": 1.3,        # 反转因子 +30%
        "trend": 0.8,           # 趋势因子 -20%
        "momentum": 0.8,        # 动量因子 -20%
        "volatility": 1.1,      # 波动率因子 +10%
        "volume": 1.1,          # 成交量因子 +10%
    },
    "high_vol": {
        "volatility": 1.3,      # 波动率因子 +30%
        "mean_reversion": 1.1,  # 均值回归 +10%
        "trend": 0.7,           # 趋势因子 -30%
        "momentum": 0.7,        # 动量因子 -30%
        "breakout": 0.5,        # 突破因子 -50%（高波动假突破多）
        "carry": 1.0,           # 跨期套利不变
    },
    "low_vol": {
        "trend": 1.2,           # 趋势因子 +20%
        "momentum": 1.2,        # 动量因子 +20%
        "mean_reversion": 1.0,  # 均值回归不变
        "volatility": 0.7,      # 波动率因子 -30%
        "fundamental": 1.1,     # 基本面 +10%
    },
}


def _infer_factor_family_from_name(name: str) -> str:
    """从因子名称推断其家族分类。

    Args:
        name: 因子名称

    Returns:
        推断的家族名称（"trend", "mean_reversion" 等）
    """
    name_lower = name.lower()

    if any(kw in name_lower for kw in ("trend", "momentum", "breakout", "follow")):
        return "trend"
    if any(kw in name_lower for kw in ("reversion", "mean", "reversal", "regression")):
        return "mean_reversion"
    if any(kw in name_lower for kw in ("carry", "spread", "arbitrage")):
        return "carry"
    # 先检查 volume 家族（避免被 vol 前缀误判为 volatility）
    if any(kw in name_lower for kw in ("volume", "volume_ratio")):
        return "volume"
    if any(kw in name_lower for kw in ("volatility", "vol", "atr", "bollinger")):
        return "volatility"
    if any(kw in name_lower for kw in ("fundamental", "pe", "pb", "roe")):
        return "fundamental"
    if any(kw in name_lower for kw in ("liquidity", "illiquidity")):
        return "liquidity"
    if any(kw in name_lower for kw in ("cross_section", "cs_", "rank")):
        return "cross_section"

    return "other"


def regime_adaptive_weight_adjustment(
    signals: list[PortfolioSignal],
    regime: dict[str, Any],
    factors: list[dict[str, Any]],
    min_weight: float = 0.01,
) -> list[PortfolioSignal]:
    """根据市场制度自适应调整因子权重。

    核心逻辑:
    1. 从 MarketRegime 获取当前制度名（bull/bear/oscillate/high_vol/low_vol）
    2. 遍历每个 signal，根据其 factor_id 在 factors 中查找 family 字段
    3. 根据 REGIME_FAMILY_MULTIPLIERS 查表获取倍率
    4. 将原始权重 × 倍率 → 调整后权重
    5. 对高波动期（high_vol）额外缩减衰减过快因子

    Args:
        signals: 合成后的信号列表
        regime: 市场制度检测结果 (from RegimeAwareSelector.detect())
        factors: 原始因子列表（含 family 字段）
        min_weight: 最小权重下限（避免完全归零）

    Returns:
        调整后的 signals 列表（权重已更新，retained 可能变化）
    """
    if not signals or not regime:
        return signals

    regime_name = regime.get("regime", "oscillate")
    multipliers = REGIME_FAMILY_MULTIPLIERS.get(regime_name, {})

    if not multipliers:
        logger.info("[L3-Regime] 无制度倍率配置，跳过自适应调整 [regime=%s]", regime_name)
        return signals

    # 构建 factor_id → family 映射
    factor_family_map: dict[str, str] = {}
    for f in factors:
        fid = f.get("factor_id", "")
        family = f.get("family", "")
        if fid:
            if family:
                factor_family_map[fid] = family
            else:
                # 无 family 字段，从名称推断
                factor_family_map[fid] = _infer_factor_family_from_name(
                    f.get("name", "")
                )

    # 应用倍率调整
    adjustment_log: list[str] = []
    for s in signals:
        fid = s.get("factor_id", "")
        family = factor_family_map.get(fid, "other")

        # 获取该家族的倍率（默认 1.0）
        multiplier = multipliers.get(family, 1.0)

        # 高波动期额外缩减衰减因子
        if regime_name == "high_vol":
            decay = s.get("decay_6m", 0.0)
            if decay > 0.20:
                multiplier *= 0.8  # 衰减快的因子再减 20%

        # 应用倍率（但不低于 min_weight 比例）
        original_weight = s.get("weight", 0.0)
        adjusted_weight = max(original_weight * multiplier, original_weight * min_weight)

        if abs(adjusted_weight - original_weight) > 1e-6:
            adjustment_log.append(
                f"  {s.get('name', fid)} [{family}]: {original_weight:.4f} → {adjusted_weight:.4f} (×{multiplier:.2f})"
            )

        s["weight"] = adjusted_weight

    # 日志
    if adjustment_log:
        logger.info(
            "[L3-Regime] 自适应权重调整完成 [regime=%s, adjusted=%d/%d]:\n%s",
            regime_name,
            len(adjustment_log),
            len(signals),
            "\n".join(adjustment_log),
        )
    else:
        logger.info("[L3-Regime] 自适应权重调整完成 [regime=%s, 无需调整]", regime_name)

    return signals


def synthesize_signals(
    factors: list[dict[str, Any]],
    mode: str = "equal_weight",
    elite_dir: str | Path | None = None,
) -> tuple[list[PortfolioSignal], float, float]:
    """信号合成。

    Args:
        factors: 每个 dict 必须含 factor_id, name, sharpe, ic, turnover, decay_6m
        mode: "equal_weight" | "sharpe_weight" | "elastic_net"
        elite_dir: 精英因子目录（elastic_net 模式需要，用于加载因子代码）

    Returns:
        (signals, max_correlation, combo_turnover)
    """
    if not factors:
        return [], 0.0, 0.0

    n = len(factors)

    if mode == "elastic_net" and elite_dir is not None:
        elastic_weights = _compute_elastic_net_weights(factors, Path(elite_dir))
        if not elastic_weights:
            logger.warning("[L3] Elastic Net 权重计算失败，回退到 sharpe_weight")
            return synthesize_signals(factors, "sharpe_weight")

        signals: list[PortfolioSignal] = []
        for f in factors:
            w = elastic_weights.get(f["factor_id"], 0.0)
            signals.append(PortfolioSignal(
                factor_id=f["factor_id"],
                name=f["name"],
                weight=w,
                sharpe=f.get("sharpe", 0.0),
                ic=f.get("ic", 0.0),
                turnover=f.get("turnover", 0.0),
                decay_6m=f.get("decay_6m", 0.0),
                orthogonalized=True,   # Elastic Net L1 已做变量选择
                retained=w > 0.0,
            ))
        logger.info("[L3] Elastic Net 完成: %d/%d 因子获得非零权重",
                    sum(1 for s in signals if s["retained"]), len(signals))
    elif mode == "equal_weight":
        w = 1.0 / n
        signals = []
        for f in factors:
            signals.append(PortfolioSignal(
                factor_id=f["factor_id"],
                name=f["name"],
                weight=w,
                sharpe=f.get("sharpe", 0.0),
                ic=f.get("ic", 0.0),
                turnover=f.get("turnover", 0.0),
                decay_6m=f.get("decay_6m", 0.0),
                orthogonalized=False,
                retained=True,
            ))
    elif mode == "sharpe_weight":
        total_sharpe = sum(max(f.get("sharpe", 0), 0.01) for f in factors)
        signals = []
        for f in factors:
            w = max(f.get("sharpe", 0), 0.01) / total_sharpe if total_sharpe > 0 else 1.0 / n
            signals.append(PortfolioSignal(
                factor_id=f["factor_id"],
                name=f["name"],
                weight=w,
                sharpe=f.get("sharpe", 0.0),
                ic=f.get("ic", 0.0),
                turnover=f.get("turnover", 0.0),
                decay_6m=f.get("decay_6m", 0.0),
                orthogonalized=False,
                retained=True,
            ))
        # [WEIGHT-LOG] 权重计算详情
        logger.info("[L3-WEIGHT] sharpe_weight 模式: total_sharpe=%.2f, n_factors=%d", total_sharpe, n)
        for idx, s in enumerate(sorted(signals, key=lambda x: -x["weight"])):
            logger.info("[L3-WEIGHT]   [%d] %s | sharpe=%.2f | raw_weight=%.4f",
                        idx + 1, s["name"], s["sharpe"], s["weight"])
    else:
        # lightgbm 等未实现模式暂回退等权
        signals = []
        for f in factors:
            signals.append(PortfolioSignal(
                factor_id=f["factor_id"],
                name=f["name"],
                weight=1.0 / n,
                sharpe=f.get("sharpe", 0.0),
                ic=f.get("ic", 0.0),
                turnover=f.get("turnover", 0.0),
                decay_6m=f.get("decay_6m", 0.0),
                orthogonalized=False,
                retained=True,
            ))

    # 估算最大相关性和组合换手率
    max_corr = 0.0
    total_turnover = sum(s.get("turnover", 0) for s in signals) / len(signals) if signals else 0.0

    return signals, max_corr, total_turnover


def _compute_elastic_net_weights(
    factors: list[dict[str, Any]],
    elite_dir: Path,
    days: int = 120,
    max_stocks: int = 50,
    l1_ratio: float = 0.5,
    cv_folds: int = 5,
) -> dict[str, float]:
    """Elastic Net 截面回归确定因子权重。

    步骤:
        1. 加载 CSI300 面板数据 + 基本面字段
        2. 对每个因子，逐股票执行因子代码获取信号序列
        3. 逐日截面回归: 因子信号[t] → 5 日前向收益
        4. 平均各日回归系数绝对值 → 归一化 → 权重

    Args:
        factors: 因子列表
        elite_dir: 精英因子目录（含因子代码 JSON）
        days: 回溯天数
        max_stocks: 最大股票数
        l1_ratio: ElasticNet L1 比例（0=Ridge, 1=Lasso）
        cv_folds: 交叉验证折数

    Returns:
        {factor_id: weight} 映射（权重和为 1.0）
    """
    try:
        from sklearn.linear_model import ElasticNetCV
    except ImportError:
        logger.warning("[L3] scikit-learn 未安装，无法使用 Elastic Net")
        return {}

    import numpy as np
    from ..data import FTSDataProvider
    from .factor_program import FactorExecutor

    # ── 1. 加载 CSI300 面板数据 ──
    provider = FTSDataProvider()
    panel, common_dates = provider.get_csi300_panel(
        days=days, max_stocks=max_stocks, fundamental=True,
    )
    if not panel or len(common_dates) < 20:
        logger.warning("[L3] 面板数据不足（需 ≥20 个交易日），回退")
        return {}

    n_dates = len(common_dates)
    stocks = sorted(panel.keys())
    n_stocks = len(stocks)
    logger.info("[L3] Elastic Net 数据: %d 只股票 × %d 个交易日", n_stocks, n_dates)

    # ── 2. 加载因子代码 ──
    factor_codes: dict[str, dict[str, Any]] = {}
    for fp in sorted(elite_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            fid = data.get("factor_id", "")
            if fid and data.get("code"):
                factor_codes[fid] = data
        except Exception:
            continue

    valid_factors = [f for f in factors if f["factor_id"] in factor_codes]
    if len(valid_factors) < 2:
        logger.warning("[L3] 有效因子不足（需 ≥2），回退")
        return {}

    n_factors = len(valid_factors)
    logger.info("[L3] Elastic Net 因子: %d 个（含代码）", n_factors)

    # ── 3. 计算因子信号矩阵: [n_dates, n_stocks, n_factors] ──
    signal_matrix = np.full((n_dates, n_stocks, n_factors), np.nan)
    for j, f in enumerate(valid_factors):
        fid = f["factor_id"]
        fdata = factor_codes[fid]
        try:
            executor = FactorExecutor(fdata)
        except Exception:
            continue
        for i, sym in enumerate(stocks):
            df = panel[sym]
            try:
                sig = executor.execute(df, fdata.get("params", {}))
                aligned = np.full(n_dates, np.nan)
                for t, d in enumerate(common_dates):
                    if d in df.index:
                        idx = list(df.index).index(d)
                        aligned[t] = float(sig[idx]) if idx < len(sig) else np.nan
                signal_matrix[:, i, j] = aligned
            except Exception:
                continue

    # ── 4. 计算 5 日前向收益 ──
    forward_returns = np.full((n_dates, n_stocks), np.nan)
    horizon = 5
    for i, sym in enumerate(stocks):
        df = panel[sym]
        closes = df["close"].values
        fwd = np.full(len(closes), np.nan)
        fwd[:-horizon] = (closes[horizon:] - closes[:-horizon]) / np.maximum(closes[:-horizon], 1e-10)
        for t, d in enumerate(common_dates):
            if d in df.index:
                idx = list(df.index).index(d)
                forward_returns[t, i] = fwd[idx] if idx < len(fwd) else np.nan

    # ── 5. 逐日 Elastic Net 截面回归 ──
    all_coefs = np.zeros((n_dates, n_factors))
    valid_dates = 0

    for t in range(n_dates):
        X = signal_matrix[t]  # (n_stocks, n_factors)
        y = forward_returns[t]  # (n_stocks,)

        valid = ~np.isnan(X).any(axis=1) & ~np.isnan(y)
        n_valid = valid.sum()
        if n_valid < 10:
            continue

        X_valid = X[valid]
        y_valid = y[valid]

        # 标准化
        X_mean = X_valid.mean(axis=0)
        X_std = X_valid.std(axis=0) + 1e-10
        X_scaled = (X_valid - X_mean) / X_std

        model = ElasticNetCV(
            l1_ratio=[l1_ratio],
            cv=min(cv_folds, n_valid),
            max_iter=5000,
            random_state=42,
        )
        model.fit(X_scaled, y_valid)
        all_coefs[t] = model.coef_ / X_std  # 还原到原始尺度
        valid_dates += 1

    if valid_dates < 5:
        logger.warning("[L3] 有效回归日不足（%d < 5），回退", valid_dates)
        return {}

    logger.info("[L3] Elastic Net 完成: %d 个有效截面回归日", valid_dates)

    # ── 6. 平均系数 → 取绝对值 → 归一化为权重 ──
    mean_coefs = np.nanmean(all_coefs, axis=0)
    mean_coefs = np.nan_to_num(mean_coefs, 0.0)

    abs_coefs = np.abs(mean_coefs)
    total = abs_coefs.sum()
    if total <= 0:
        return {}

    weights = abs_coefs / total

    result = {valid_factors[j]["factor_id"]: float(weights[j]) for j in range(n_factors)}
    n_nonzero = sum(1 for w in result.values() if w > 0.001)
    logger.info("[L3] Elastic Net 权重: %d 个因子获非零权重（共 %d 个）", n_nonzero, len(result))
    return result


def orthogonalize_factors(
    signals: list[PortfolioSignal],
    correlation_matrix: list[FactorCorrelation] | None = None,
    max_corr_threshold: float = 0.7,
    factors: list[dict[str, Any]] | None = None,
    use_tiered: bool = False,
    l2_prior_correlations: list[dict[str, Any]] | None = None,
) -> list[PortfolioSignal]:
    """因子正交化 — 剔除相关性 > threshold 的因子 + 代码去重 + L2 先验注入。

    保留夏普更高的因子。支持四种模式：
    1. 有 correlation_matrix: 基于相关性矩阵剔除高相关因子对
    2. 无 correlation_matrix + use_tiered=True: 分层正交化（预筛→家族→统计）
    3. 无 correlation_matrix: 基于因子代码哈希去重（相同代码的因子只保留夏普更高的）
    4. 所有模式均注入 l2_prior_correlations 作为先验标记

    Args:
        signals: 组合信号列表
        correlation_matrix: 因子相关性矩阵（可选）
        max_corr_threshold: 最大相关性阈值
        factors: 因子元数据列表
        use_tiered: 是否使用分层正交化（因子数>=30时推荐）
        l2_prior_correlations: L2 种子因子相关性预检结果（先验数据）
    """
    # ── 注入 L2 先验标记（所有模式通用）──
    if l2_prior_correlations:
        logger.info("[L3] 注入 L2 相关性先验: %d 对高相关因子", len(l2_prior_correlations))
        # 将 L2 先验标记附加到对应 signal
        for pair in l2_prior_correlations:
            fid_a = pair.get("factor_id_a", "")
            fid_b = pair.get("factor_id_b", "")
            max_abs = max(abs(pair.get("pearson", 0)), abs(pair.get("spearman", 0)))
            if max_abs >= 0.95:
                for s in signals:
                    if s.get("factor_id") in (fid_a, fid_b):
                        flags = s.setdefault("correlation_flags", [])
                        flags.append({
                            "type": "l2_seed_correlation",
                            "reason": f"L2 种子预检: 与 {fid_b if s.get('factor_id') == fid_a else fid_a} 相关 {max_abs:.3f}",
                        })

    # ── 模式 0: 分层正交化（使用 FactorOptimizer — 标记模式）──
    if use_tiered and factors is not None and len(factors) >= 30:
        try:
            from .factor_optimizer import FactorOptimizer
            logger.info("[L3] 触发分层正交化（标记模式）: 因子数=%d, 阈值=%.1f",
                        len(factors), max_corr_threshold)
            optimizer = FactorOptimizer()
            result_factors, summary = optimizer.tiered_orthogonalize(
                factors, max_corr_threshold=max_corr_threshold, mode="mark",
                l2_prior_correlations=l2_prior_correlations,
            )

            # 只根据 exclude_from_portfolio 硬排除（仅限代码重复场景）
            # 其他相关性标记仅作为诊断信息，不改变 retain 状态
            for rf in result_factors:
                fid = rf.get("factor_id", "")
                flags = rf.get("correlation_flags", [])
                excluded = rf.get("exclude_from_portfolio", False)
                
                for s in signals:
                    if s.get("factor_id") == fid:
                        s["orthogonalized"] = True
                        # 添加标记到 signal（用于诊断）
                        if flags:
                            s["correlation_flags"] = flags
                        # 仅在明确排除时标记 retained=False
                        if excluded:
                            s["retained"] = False

            # ── Phase 1 详细日志 ──
            phase1_details = summary.get("phase1_details", [])
            phase1_code_dup = [d for d in phase1_details if d["type"] == "code_duplicate"]
            phase1_family = [d for d in phase1_details if d["type"] == "family_prune"]

            logger.info("[L3] Phase 1 标记完成: 标记 %d 个 (代码重复=%d, 家族标记=%d)",
                        summary.get("phase1_marked", 0),
                        len(phase1_code_dup), len(phase1_family))

            if phase1_code_dup:
                dup_msg = "; ".join(
                    f"{d['removed']} (因:{d['reason']})"
                    for d in phase1_code_dup
                )
                logger.info("[L3] Phase 1-代码重复标记: %s", dup_msg)

            if phase1_family:
                fam_msg = "; ".join(
                    f"{d['removed']} (因:{d['reason']})"
                    for d in phase1_family
                )
                logger.info("[L3] Phase 1-家族标记: %s", fam_msg)

            # ── Phase 2 详细日志（含 L2 先验合并）──
            phase2_details = summary.get("phase2_details", [])
            l2_prior_count = summary.get("l2_prior_count", 0)
            phase2_new = summary.get("phase2_new_count", 0)
            phase2_overlap = summary.get("phase2_overlap_count", 0)
            
            logger.info(
                "[L3] Phase 2 相关性标记完成: 标记 %d 个高相关因子 "
                "(新增 %d 个, 与 L2 先验重叠 %d 个)",
                summary.get("phase2_marked", 0),
                phase2_new, phase2_overlap,
            )
            
            if l2_prior_count > 0:
                logger.info("[L3] Phase 2 与 L2 先验合并: L2 先验标记 %d 个, "
                            "Phase 2 新增 %d 个, 重叠 %d 个",
                            l2_prior_count, phase2_new, phase2_overlap)

            if phase2_details:
                corr_msg = "; ".join(
                    f"{d['removed']} (因:{d['reason']})"
                    for d in phase2_details
                )
                logger.info("[L3] Phase 2-高相关标记详情: %s", corr_msg)

            # ── L2 先验 × Phase 2 合并详情 ──
            for s in signals:
                flags = s.get("correlation_flags", [])
                l2_flags = [f for f in flags if f.get("source") == "l2_prior"]
                phase2_flags = [f for f in flags if f.get("source") == "phase2_full_correlation"]
                if l2_flags or phase2_flags:
                    logger.info(
                        "[L3] 因子 %s 标记汇总: L2先验=%d, Phase2全量=%d",
                        s.get("name", s.get("factor_id", "?")),
                        len(l2_flags), len(phase2_flags),
                    )
                    for f in l2_flags:
                        logger.info("[L3]   L2: %s", f["reason"])
                    for f in phase2_flags:
                        logger.info("[L3]   P2: %s", f["reason"])

            # ── 汇总 ──
            logger.info(
                "[L3] 分层正交化汇总: 输入 %d → 输出 %d | "
                "L2 先验 %d, Phase1 %d, Phase2 %d | "
                "Phase2 新增 %d, Phase2 与 L2 重叠 %d | "
                "耗时 %.2fs | 模式=mark (只标记不删除)",
                summary.get("input_count", 0),
                summary.get("output_count", 0),
                l2_prior_count,
                summary.get("phase1_marked", 0),
                summary.get("phase2_marked", 0),
                phase2_new, phase2_overlap,
                summary.get("elapsed_seconds", 0),
            )
            return signals
        except Exception as e:
            logger.warning("[L3] 分层正交化失败，回退到代码去重: %s", e)

    # ── 模式 1: 基于相关性矩阵的正交化 ──
    if correlation_matrix is not None:
        high_corr_pairs: dict[str, set[str]] = {}
        for edge in correlation_matrix:
            if abs(edge.get("pearson", 0)) > max_corr_threshold:
                a, b = edge["factor_id_a"], edge["factor_id_b"]
                high_corr_pairs.setdefault(a, set()).add(b)
                high_corr_pairs.setdefault(b, set()).add(a)

        factor_map = {s["factor_id"]: s for s in signals}
        removed: set[str] = set()
        for fid in sorted(factor_map.keys(), key=lambda x: factor_map[x].get("sharpe", 0), reverse=True):
            if fid in removed:
                continue
            for neighbor in high_corr_pairs.get(fid, set()):
                if neighbor not in removed and neighbor in factor_map:
                    removed.add(neighbor)

        for s in signals:
            s["orthogonalized"] = True
            if s["factor_id"] in removed:
                s["retained"] = False

        return signals
    
    # ── 模式 2: 基于代码哈希的去重（无相关性矩阵时的后备方案）──
    if factors is not None:
        # 构建 factor_id -> code_hash 映射
        factor_code_map: dict[str, str] = {}
        for f in factors:
            fid = f.get("factor_id", "")
            code_hash = f.get("code_hash", "")
            if fid and code_hash:
                factor_code_map[fid] = code_hash
        
        # 按代码哈希分组，相同代码只保留夏普更高的
        hash_to_factors: dict[str, list[PortfolioSignal]] = {}
        for s in signals:
            fid = s.get("factor_id", "")
            code_hash = factor_code_map.get(fid, "")
            if code_hash:
                hash_to_factors.setdefault(code_hash, []).append(s)
        
        removed: set[str] = set()
        for code_hash, group in hash_to_factors.items():
            if len(group) > 1:
                # 按夏普排序，保留最高的
                group_sorted = sorted(group, key=lambda x: x.get("sharpe", 0), reverse=True)
                for s in group_sorted[1:]:
                    removed.add(s["factor_id"])
                    logger.info("[L3] 代码去重: 剔除 %s (与 %s 代码相同)", 
                                s.get("name", "?"), group_sorted[0].get("name", "?"))
        
        for s in signals:
            s["orthogonalized"] = True
            if s["factor_id"] in removed:
                s["retained"] = False
        
        if removed:
            logger.info("[L3] 正交化完成: 基于代码去重剔除 %d 个因子", len(removed))
        else:
            logger.info("[L3] 正交化完成: 无代码重复因子")
        
        return signals
    
    # ── 模式 3: 无任何去重依据，全部标记为已正交化 ──
    for s in signals:
        s["orthogonalized"] = True
    return signals


def decay_test(
    signals: list[PortfolioSignal],
    max_decay_rate: float = 0.30,
) -> list[PortfolioSignal]:
    """衰减检验 — 6 个月滚动衰减 > threshold 的因子标记为不保留。"""
    removed = []
    for s in signals:
        decay = s.get("decay_6m", 0)
        if decay > max_decay_rate:
            s["retained"] = False
            removed.append((s["name"], decay))
    if removed:
        logger.info("[L3-WEIGHT] 衰减检验移除 %d 个因子 (decay>%.2f): %s",
                    len(removed), max_decay_rate,
                    "; ".join(f"{n}(decay={d:.4f})" for n, d in removed))
    return signals


# ─── 组合构建 ─────────────────────────────────────────────

def _apply_sticky_constraints(
    signals: list[PortfolioSignal],
    prev_weights: dict[str, float],
    config: StickyConfig,
) -> list[PortfolioSignal]:
    """组合粘性约束 — 平滑换血，防止策略漂移。

    在权重归一化之前执行:
        - 存量因子: 权重相对上次组合变动 clamp 在 ±max_delta
        - 新因子: 首日权重封顶 new_factor_cap

    Args:
        signals: 待构建信号（权重尚未归一化）
        prev_weights: {factor_id: weight} 上次组合权重
        config: 粘性配置

    Returns:
        施加约束后的信号列表（就地修改 weight 字段）
    """
    if not config.get("enabled", True) or not prev_weights:
        return signals

    max_delta = config.get("max_delta", 0.30)
    new_factor_cap = config.get("new_factor_cap", 0.10)

    for s in signals:
        if not s.get("retained", True):
            continue
        fid = s.get("factor_id")
        prev_w = prev_weights.get(fid)
        if prev_w is not None and prev_w > 0:
            # 存量因子: 相对上次变动 clamp 在 ±max_delta
            low = max(0.0, prev_w * (1.0 - max_delta))
            high = prev_w * (1.0 + max_delta)
            s["weight"] = min(max(s.get("weight", 0.0), low), high)
        else:
            # 新因子: 首日权重封顶
            s["weight"] = min(s.get("weight", 0.0), new_factor_cap)

    return signals


def build_combo(
    signals: list[PortfolioSignal],
    mode: str = "equal_weight",
    trace_id: Optional[str] = None,
    prev_weights: Optional[dict[str, float]] = None,
    sticky_config: Optional[StickyConfig] = None,
) -> PortfolioCombo:
    """构建组合 — 归一化权重 + 计算组合指标。

    Args:
        signals: 信号列表
        mode: 合成模式
        trace_id: 追踪 ID
        prev_weights: 上次组合权重（粘性约束输入，可选）
        sticky_config: 粘性约束配置（可选，默认关闭）

    Returns:
        PortfolioCombo
    """
    retained = [s for s in signals if s.get("retained", True)]
    if not retained:
        return PortfolioCombo(
            version=EVOLUTION_VERSION,
            updated_at=datetime.now().isoformat(),
            combo_id=f"cmb_{secrets.token_hex(4)}",
            trace_id=trace_id or generate_trace_id("l3"),
            synthesis_mode=mode,
            signals=signals,
            combo_sharpe=0.0,
            combo_turnover=0.0,
            max_correlation=0.0,
            n_factors=0,
            status="pending",
            created_at=datetime.now().isoformat(),
        )

    # 粘性约束（归一化前，保证约束后的权重和为 1 的归一化语义一致）
    if sticky_config and prev_weights:
        _apply_sticky_constraints(retained, prev_weights, sticky_config)
        logger.info(
            "[L3] Step 5: 粘性约束已应用 (max_delta=%.2f, new_cap=%.2f, prev=%d 因子)",
            sticky_config.get("max_delta", 0.30),
            sticky_config.get("new_factor_cap", 0.10),
            len(prev_weights),
        )

    # 权重归一化
    total_w = sum(s.get("weight", 0) for s in retained)
    if total_w > 0:
        for s in retained:
            s["weight"] = s.get("weight", 0) / total_w

    # [WEIGHT-LOG] 归一化后权重分布
    sorted_retained = sorted(retained, key=lambda x: -x["weight"])
    effective_n = 1.0 / sum((s["weight"] ** 2) for s in sorted_retained)
    logger.info("[L3-WEIGHT] 最终权重分布: %d 因子, effective_n=%.2f, HHI=%.4f",
                len(sorted_retained), effective_n,
                sum(s["weight"] ** 2 for s in sorted_retained))
    for idx, s in enumerate(sorted_retained):
        logger.info("[L3-WEIGHT]   [%d] %s | weight=%.4f | sharpe=%.2f | ic=%.4f",
                    idx + 1, s["name"], s["weight"], s["sharpe"], s.get("ic", 0))

    # 组合指标（简化为算术平均）
    combo_sharpe = sum(s.get("sharpe", 0) for s in retained) / len(retained)
    combo_turnover = sum(s.get("turnover", 0) for s in retained) / len(retained)
    
    # 更准确的相关性估算: 基于因子权重集中度 + Ridge 惩罚后的相关性衰减
    n_ret = len(retained)
    if n_ret > 1:
        total_w = sum(s.get("weight", 0) for s in retained)
        if total_w > 0:
            weighted_concentration = sum(
                (s.get("weight", 0) / total_w) ** 2 
                for s in retained
            )
            effective_n = 1.0 / weighted_concentration if weighted_concentration > 0 else float(n_ret)
            diversity = min(1.0, effective_n / n_ret)
            avg_sharpe = sum(s.get("sharpe", 0) for s in retained) / n_ret
            max_corr = min(
                0.7,
                (1.0 - diversity) * 0.35 + avg_sharpe * 0.015
            )
        else:
            max_corr = 0.15
    else:
        max_corr = 0.0

    return PortfolioCombo(
        version=EVOLUTION_VERSION,
        updated_at=datetime.now().isoformat(),
        combo_id=f"cmb_{secrets.token_hex(4)}",
        trace_id=trace_id or generate_trace_id("l3"),
        synthesis_mode=mode,
        signals=signals,
        combo_sharpe=combo_sharpe,
        combo_turnover=combo_turnover,
        max_correlation=max_corr,
        n_factors=len(retained),
        status="active",
        created_at=datetime.now().isoformat(),
    )


# ─── 组合漂移监控 ─────────────────────────────────────────

class DriftMonitor:
    """L3 组合漂移监控 — 记录成员重合率 + 权重 L1 变化率。

    每次组合构建后对比上次组合（combo_history 归档），
    指标持久化到 memory/portfolio/drift_history/YYYY-MM-DD.json。
    """

    def __init__(self, portfolio_dir: str | Path = "memory/portfolio"):
        self.portfolio_dir = Path(portfolio_dir)
        self.drift_history_dir = self.portfolio_dir / DRIFT_HISTORY_DIR
        self.drift_history_dir.mkdir(parents=True, exist_ok=True)

    def compute(
        self,
        prev_combo: PortfolioCombo | None,
        new_combo: PortfolioCombo,
        trace_id: Optional[str] = None,
    ) -> DriftMetrics:
        """计算漂移指标。

        Args:
            prev_combo: 上次组合（可为 None = 冷启动）
            new_combo: 本次组合
            trace_id: 追踪 ID

        Returns:
            DriftMetrics 指标字典
        """
        prev_members: dict[str, str] = {}   # factor_id -> name
        prev_weights: dict[str, float] = {}
        if prev_combo:
            for s in prev_combo.get("signals", []):
                if s.get("retained", True) and s.get("factor_id"):
                    prev_members[s["factor_id"]] = s.get("name", s["factor_id"])
                    prev_weights[s["factor_id"]] = s.get("weight", 0.0)

        new_members: dict[str, str] = {}
        new_weights: dict[str, float] = {}
        for s in new_combo.get("signals", []):
            if s.get("retained", True) and s.get("factor_id"):
                new_members[s["factor_id"]] = s.get("name", s["factor_id"])
                new_weights[s["factor_id"]] = s.get("weight", 0.0)

        prev_ids = set(prev_members)
        new_ids = set(new_members)

        # 冷启动（无上次组合）：无漂移对比基准，仅记录新增成员
        if not prev_ids:
            return DriftMetrics(
                date=datetime.now().strftime("%Y-%m-%d"),
                combo_id=new_combo.get("combo_id", ""),
                prev_combo_id="",
                trace_id=trace_id or new_combo.get("trace_id", ""),
                member_overlap_rate=0.0,
                weight_l1_change=0.0,
                n_prev_members=0,
                n_new_members=len(new_ids),
                n_common_members=0,
                added=sorted(new_members[fid] for fid in new_ids),
                removed=[],
            )

        common = prev_ids & new_ids
        union = prev_ids | new_ids

        # 成员重合率 Jaccard: |A∩B| / |A∪B|（0~1，1=完全重合）
        overlap_rate = len(common) / len(union) if union else 0.0

        # 权重 L1 变化率: Σ|w_new - w_prev| / 2（除以 2 归一化到 0~1）
        l1_total = 0.0
        for fid in union:
            l1_total += abs(new_weights.get(fid, 0.0) - prev_weights.get(fid, 0.0))
        weight_l1 = l1_total / 2.0 if union else 0.0

        added = sorted(new_members[fid] for fid in (new_ids - prev_ids))
        removed = sorted(prev_members[fid] for fid in (prev_ids - new_ids))

        return DriftMetrics(
            date=datetime.now().strftime("%Y-%m-%d"),
            combo_id=new_combo.get("combo_id", ""),
            prev_combo_id=prev_combo.get("combo_id", "") if prev_combo else "",
            trace_id=trace_id or new_combo.get("trace_id", ""),
            member_overlap_rate=round(overlap_rate, 4),
            weight_l1_change=round(weight_l1, 4),
            n_prev_members=len(prev_ids),
            n_new_members=len(new_ids),
            n_common_members=len(common),
            added=added,
            removed=removed,
        )

    def record(self, metrics: DriftMetrics) -> Path:
        """持久化漂移指标到 drift_history/YYYY-MM-DD.json（当日多条追加）。"""
        date = metrics.get("date") or datetime.now().strftime("%Y-%m-%d")
        fp = self.drift_history_dir / f"{date}.json"

        records: list[DriftMetrics] = []
        if fp.exists():
            try:
                loaded = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    records = [DriftMetrics(**r) for r in loaded if isinstance(r, dict)]
            except (json.JSONDecodeError, TypeError, ValueError):
                records = []

        records.append(metrics)
        fp.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info(
            "[L3] 漂移记录已写入 %s: 重合率=%.4f L1变化=%.4f (prev=%d→new=%d)",
            fp.name, metrics.get("member_overlap_rate", 0),
            metrics.get("weight_l1_change", 0),
            metrics.get("n_prev_members", 0), metrics.get("n_new_members", 0),
        )
        return fp

    def load_history(self, date: str) -> list[DriftMetrics]:
        """读取指定日期的漂移记录列表。"""
        fp = self.drift_history_dir / f"{date}.json"
        if not fp.exists():
            return []
        try:
            loaded = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                return [DriftMetrics(**r) for r in loaded if isinstance(r, dict)]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return []


# ─── Agent 优化建议生成 ──────────────────────────────────

def generate_agent_proposals(
    combo: PortfolioCombo,
    trace_id: Optional[str] = None,
) -> list[AgentOptimizationProposal]:
    """基于组合结果生成 Agent 优化建议。"""
    proposals: list[AgentOptimizationProposal] = []
    tid = trace_id or combo.get("trace_id", generate_trace_id("l3"))

    retained = [s for s in combo.get("signals", []) if s.get("retained", True)]
    if not retained:
        return proposals

    # 提炼多空信号建议
    weighted_signals = [
        f"{s['name']}(w={s['weight']:.2f},sharpe={s['sharpe']:.2f})"
        for s in sorted(retained, key=lambda x: x.get("weight", 0), reverse=True)[:5]
    ]

    proposals.append(AgentOptimizationProposal(
        proposal_id=f"prop_{secrets.token_hex(4)}",
        trace_id=tid,
        created_at=datetime.now().isoformat(),
        agent_name="闫判官",
        current_prompt_summary="基于扫描信号+辩论数据的裁决",
        suggested_changes=(
            f"考虑增加以下因子的权重分配：{'；'.join(weighted_signals)}。"
            f"组合夏普{combo.get('combo_sharpe', 0):.2f}，换手率{combo.get('combo_turnover', 0):.2f}。"
        ),
        debate_round_ref=None,
        rationale=f"L3 组合构建输出（{combo.get('synthesis_mode', 'equal_weight')}模式），{len(retained)}个保留因子。",
        priority="medium",
        status="draft",
    ))

    return proposals


# ─── 精英因子读取 ────────────────────────────────────────

# ── 运行时质量门槛（与 DEFAULT_VERIFIER_CONFIG 对齐） ──

_RUNTIME_MIN_IC = DEFAULT_VERIFIER_CONFIG["min_ic"]       # 0.03
_RUNTIME_MIN_SHARPE = DEFAULT_VERIFIER_CONFIG["min_sharpe"]  # 1.5


def _filter_by_quality_gate(factors: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """按 Verifier 质量门槛过滤因子 — 防御性检查。

    即使上游筛选已通过，仍在 L3 入口处再次验证 IC/Sharpe，
    防止数据漂移或错误标记导致低质量因子进入组合。

    Args:
        factors: 待过滤因子列表
        source: 数据来源标识（用于日志）

    Returns:
        通过门槛的因子列表
    """
    if not factors:
        return factors

    passed: list[dict[str, Any]] = []
    failed: list[str] = []

    for f in factors:
        ic = f.get("ic", 0)
        sharpe = f.get("sharpe", 0)
        name = f.get("name", f.get("factor_id", "?"))

        if abs(ic) < _RUNTIME_MIN_IC:
            failed.append(f"{name}(ic={ic:.4f})")
            continue
        if sharpe < _RUNTIME_MIN_SHARPE:
            failed.append(f"{name}(sharpe={sharpe:.2f})")
            continue
        passed.append(f)

    if failed:
        logger.warning(
            "[L3] 质量门槛过滤 [%s]: 剔除 %d 个低质量因子 (IC<%.2f 或 Sharpe<%.1f):\n  %s",
            source, len(failed), _RUNTIME_MIN_IC, _RUNTIME_MIN_SHARPE,
            "\n  ".join(failed[:10]) + ("\n  ..." if len(failed) > 10 else ""),
        )

    logger.info(
        "[L3] 质量门槛通过 [%s]: %d/%d 因子 [min_ic=%.2f, min_sharpe=%.1f]",
        source, len(passed), len(factors), _RUNTIME_MIN_IC, _RUNTIME_MIN_SHARPE,
    )
    return passed


import re

_GEN_SUFFIX_RE = re.compile(r"_g\d+$")


def _normalize_base_name(name: str) -> str:
    """去掉因子名中的世代后缀 '_gXX'，返回基础因子名。

    示例:
        fut_bias_g18 → fut_bias
        fut_bias → fut_bias
        seed_spread_g16 → seed_spread
    """
    return _GEN_SUFFIX_RE.sub("", name)


def _compute_signal_correlations(
    factors: list[dict[str, Any]],
    panel_data: dict[str, Any],
    min_valid_points: int = 10,
) -> dict[tuple[str, str], float]:
    """计算一组因子在参考品种上的信号相关系数矩阵。

    Args:
        factors: 因子列表（需含 code, params）
        panel_data: {symbol: DataFrame} 市场数据
        min_valid_points: 最少有效数据点

    Returns:
        {(factor_id_a, factor_id_b): pearson_corr} 字典
    """
    import numpy as np

    from .factor_program import FactorExecutor, FactorCompileError

    # Pick reference symbol (first available)
    if not panel_data:
        return {}
    ref_symbol = next(iter(panel_data))
    ref_df = panel_data[ref_symbol]

    signals: dict[str, np.ndarray] = {}
    errors: list[str] = []
    for f in factors:
        fid = f.get("factor_id", f.get("name", "?"))
        code = f.get("code", "")
        if not code or not isinstance(code, str):
            errors.append(f"{fid}: 代码为空或类型异常 ({type(code).__name__})")
            continue
        try:
            executor = FactorExecutor(f)
            sig = executor.execute(ref_df, f.get("params", {}))
            if sig is not None and len(sig) > 0 and not np.all(np.isnan(sig)):
                signals[fid] = sig
            else:
                errors.append(f"{fid}: 信号为空或全 NaN")
        except (FactorCompileError, Exception) as e:
            errors.append(f"{fid}: {type(e).__name__}: {str(e)[:80]}")

    if errors:
        for e in errors[:5]:
            logger.debug("[L3] 信号计算跳过: %s", e)
        if len(errors) > 5:
            logger.debug("[L3] ... 还有 %d 个错误", len(errors) - 5)

    if len(signals) < 2:
        return {}

    fids = list(signals.keys())
    corr: dict[tuple[str, str], float] = {}
    for i in range(len(fids)):
        for j in range(i + 1, len(fids)):
            s1, s2 = signals[fids[i]], signals[fids[j]]
            valid = ~(np.isnan(s1) | np.isnan(s2))
            if valid.sum() > min_valid_points:
                c = float(np.corrcoef(s1[valid], s2[valid])[0, 1])
                corr[(fids[i], fids[j])] = c
    return corr


def _greedy_select_by_correlation(
    group: list[dict[str, Any]],
    corr: dict[tuple[str, str], float],
    threshold: float = 0.8,
) -> list[dict[str, Any]]:
    """贪心选择：按 IC 从高到低，若与已选因子的最大相关 < threshold，则保留。

    Args:
        group: 同一基础因子名的多个世代
        corr: 因子间相关系数字典
        threshold: 相关性阈值（超过则剔除）

    Returns:
        选中的因子列表
    """
    sorted_group = sorted(group, key=lambda x: abs(x.get("ic", 0)), reverse=True)
    selected: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for f in sorted_group:
        fid = f.get("factor_id", f.get("name", "?"))
        if not selected:
            selected.append(f)
            continue

        max_corr = 0.0
        for s in selected:
            sid = s.get("factor_id", s.get("name", "?"))
            # Look up correlation in both directions
            c = corr.get((fid, sid), corr.get((sid, fid), 0.0))
            max_corr = max(max_corr, abs(c))

        if max_corr < threshold:
            selected.append(f)
        else:
            f["_removed_reason"] = f"与已选因子最大相关={max_corr:.4f} ≥ {threshold}"
            removed.append(f)

    return selected


def _deduplicate_by_base_name(
    factors: list[dict[str, Any]],
    source: str,
    panel_data: dict[str, Any] | None = None,
    corr_threshold: float = 0.8,
) -> list[dict[str, Any]]:
    """按基础因子名去重，支持两种模式：

    1. 相关性模式（panel_data 可用）：
       对每组世代按 IC 排序，贪心选择保留与已选因子相关性 < threshold 的因子。
       这样低相关的革新性世代可以共存，高相关的克隆世代被合并。

    2. IC-only 模式（panel_data 不可用）：
       退化为原行为：每组只保留 IC 最高的版本。

    Args:
        factors: 已通过质量门槛的因子列表
        source: 数据来源标识（用于日志）
        panel_data: {symbol: DataFrame} 市场数据（可选）
        corr_threshold: 相关性阈值（默认 0.8）

    Returns:
        去重后的因子列表
    """
    if not factors:
        return factors

    use_corr = panel_data is not None

    groups: dict[str, list[dict[str, Any]]] = {}
    for f in factors:
        base = _normalize_base_name(f.get("name", f.get("factor_id", "?")))
        groups.setdefault(base, []).append(f)

    if len(groups) == len(factors):
        mode = "相关性模式" if use_corr else "IC-only 模式"
        logger.info("[L3] 基础因子名去重 [%s] (%s): 无需去重 (%d 个唯一因子)", source, mode, len(factors))
        return factors

    result: list[dict[str, Any]] = []
    merges: list[str] = []
    selections: list[str] = []

    for base, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
        elif not use_corr:
            # IC-only 模式：保留 IC 最高
            best = max(group, key=lambda x: abs(x.get("ic", 0)))
            result.append(best)
            all_names = [f.get("name", "?") for f in group]
            merges.append(
                f"{base}: [{', '.join(all_names)}] → {best.get('name')} (IC={best.get('ic', 0):.4f})"
            )
        else:
            # 相关性模式：先计算相关性，再贪心选择
            corr = _compute_signal_correlations(group, panel_data)

            if not corr:
                # 无法计算相关性，回退到 IC-only
                best = max(group, key=lambda x: abs(x.get("ic", 0)))
                result.append(best)
                all_names = [f.get("name", "?") for f in group]
                merges.append(
                    f"{base}: [{', '.join(all_names)}] → {best.get('name')} "
                    f"(IC={best.get('ic', 0):.4f}, 无代码回退)"
                )
                continue

            selected = _greedy_select_by_correlation(group, corr, corr_threshold)
            result.extend(selected)

            sel_names = [f.get("name", "?") for f in selected]
            removed_factors = [f for f in group if f not in selected]
            rem_info = []
            for f in removed_factors:
                rem_info.append(
                    f"{f.get('name')}(IC={f.get('ic', 0):.4f}, "
                    f"reason={f.get('_removed_reason', '?')})"
                )

            if len(sel_names) < len(group):
                selections.append(
                    f"{base}: 保留 [{', '.join(sel_names)}] "
                    f"合并 [{', '.join(rem_info)}]"
                )
            else:
                selections.append(
                    f"{base}: 全部保留 [{', '.join(sel_names)}] (互相关<{corr_threshold})"
                )

    mode = "相关性模式" if use_corr else "IC-only 模式"
    removed_count = len(factors) - len(result)
    logger.info(
        "[L3] 基础因子名去重 [%s] (%s, threshold=%.2f): %d → %d 因子 "
        "(移除 %d 个冗余世代)",
        source, mode, corr_threshold, len(factors), len(result), removed_count,
    )
    if merges:
        for m in merges[:10]:
            logger.info("[L3]   [IC-only合并] %s", m)
        if len(merges) > 10:
            logger.info("[L3]   ... 还有 %d 组合并", len(merges) - 10)
    if selections:
        for s in selections:
            logger.info("[L3]   [贪心选择] %s", s)

    return result


def _add_trading_days(start: datetime, days: int) -> datetime:
    """计算 N 个交易日后的日期时间（跳过周末，近似交易日）。"""
    import numpy as np

    end = np.busday_offset(start.date(), days, roll="forward")
    return datetime.combine(end.astype(object), datetime.min.time())


def _is_shadow_pending(factor: dict[str, Any], today: datetime | None = None) -> bool:
    """判断因子是否处于 L2 影子池观察期（观察期内不进正式组合）。

    因子 JSON/元数据含 shadow_pool 标记:
        {"promoted_at": iso, "observe_trading_days": 5, "observe_until": iso}

    Returns:
        True = 仍在影子池观察期，应排除出正式组合
        False = 无标记或观察期已过
    """
    sp = factor.get("shadow_pool")
    if not sp or not isinstance(sp, dict):
        return False
    observe_until = sp.get("observe_until")
    if not observe_until:
        return False
    today = today or datetime.now()
    try:
        until = datetime.fromisoformat(str(observe_until))
    except ValueError:
        return False
    return today < until


def _filter_shadow_pending(factors: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """过滤影子池观察期内的因子 — 观察期内不进正式组合。

    Args:
        factors: 待过滤因子列表
        source: 数据来源标识（用于日志）

    Returns:
        通过观察期的因子列表（保留 shadow_pool 标记供日志追踪）
    """
    if not factors:
        return factors
    pending = [f for f in factors if _is_shadow_pending(f)]
    if pending:
        names = [f.get("name", f.get("factor_id", "?")) for f in pending]
        logger.info(
            "[L3] 影子池过滤 [%s]: %d 个因子仍在观察期，暂不进组合: %s",
            source, len(pending), ", ".join(names[:10]) + ("..." if len(names) > 10 else ""),
        )
    return [f for f in factors if not _is_shadow_pending(f)]


def load_elite_factors(
    elite_dir: str | Path,
    use_duckdb: bool = True,
    market: str = "stock",
    panel_data: dict[str, Any] | None = None,
    corr_threshold: float = 0.8,
) -> list[dict[str, Any]]:
    """加载精英因子。优先从 DuckDB 加载，失败时回退到 JSON 文件。

    加载后按 Verifier 质量门槛（IC>=0.03, Sharpe>=1.5）过滤，
    确保进入组合的因子均满足最低质量标准。

    可选 panel_data 用于基于信号相关性的智能去重。

    Args:
        elite_dir: 精英因子 JSON 目录
        use_duckdb: 是否优先从 DuckDB 加载（测试时可设为 False）
        market: 市场类型过滤（futures/stock/etf 等）
        panel_data: {symbol: DataFrame} 市场数据（用于相关性去重）
        corr_threshold: 相关性阈值（默认 0.8）

    精英因子文件结构 (JSON 兜底):
        {
            "factor_id": "...",
            "name": "...",
            "code": "...",  # 因子代码，用于哈希去重
            "evaluation": {
                "level_1_backtest": {
                    "sharpe": ...,
                    "ic": ...,
                    "turnover_monthly": ...,
                    ...
                }
            },
            "correlation_metadata": {  # L2 写入的相关性先验
                "l2_seed_flags": [...],
                "flag_count": ...,
                "max_corr_detected": ...,
            },
            ...
        }
    """
    import hashlib
    
    # ── 路径 1: 从 DuckDB 加载（优先） ──
    if use_duckdb:
        try:
            logger.info("[L3] DuckDB 查询: market=%s, status=active, is_elite=True", market)
            from .factor_db import FactorRepository
            repo = FactorRepository()
            try:
                # 先统计总数
                total_count = repo._execute(
                    "SELECT count(*) FROM factor_catalog WHERE market=? AND status='active' AND is_elite=true",
                    [market]
                ).fetchone()[0]
                logger.info("[L3] DuckDB 匹配因子总数: %d [market=%s]", total_count, market)

                # 执行查询
                db_factors = repo.list_factors(
                    market=market,
                    status="active",
                    is_elite=True,
                    limit=10000,
                )
                logger.info("[L3] DuckDB 查询返回: %d 行 [market=%s]", len(db_factors), market)

                if db_factors:
                    logger.info("[L3] DuckDB 因子字段样例: %s", list(db_factors[0].keys())[:10])
                    factors = []
                    for f in db_factors:
                        code = f.get("code", "")
                        code_hash = f.get("code_hash", "")
                        if not code_hash and code:
                            code_hash = hashlib.sha256(code.encode()).hexdigest()
                        metadata = f.get("metadata", {}) or {}
                        corr_meta = metadata.get("correlation_metadata", {})
                        factors.append({
                            "factor_id": f.get("factor_id"),
                            "name": f.get("name"),
                            "sharpe": f.get("sharpe", 0.5),
                            "ic": f.get("ic", 0.02),
                            "turnover": f.get("turnover_monthly", 0.3),
                            "decay_6m": f.get("decay_6m", 0.05),
                            "code": code,
                            "params": f.get("params", {}) or {},
                            "economic_logic": f.get("economic_logic", {}) or {},
                            "code_hash": code_hash,
                            "correlation_metadata": corr_meta,
                            "source_file": f.get("factor_id"),
                            "market": f.get("market", market),
                            "shadow_pool": metadata.get("shadow_pool"),
                        })
                    logger.info("[L3] ✅ 从 DuckDB 加载 %d 个 elite 因子 [market=%s]", len(factors), market)
                    passed = _filter_by_quality_gate(factors, "DuckDB")
                    passed = _filter_shadow_pending(passed, "DuckDB")
                    try:
                        result = _deduplicate_by_base_name(passed, "DuckDB", panel_data=panel_data, corr_threshold=corr_threshold)
                        return result
                    except Exception as dedup_err:
                        logger.warning("[L3] DuckDB 相关性去重失败: %s，回退到 IC-only", dedup_err)
                        import traceback
                        logger.exception("[L3] 去重异常堆栈:")
                        # 回退: 不使用 panel_data
                        return _deduplicate_by_base_name(passed, "DuckDB", panel_data=None, corr_threshold=corr_threshold)
                else:
                    logger.warning("[L3] ⚠️ DuckDB 查询返回 0 行 [market=%s]，回退到 JSON 加载", market)
                    # 额外诊断：检查该市场的所有因子（不限 is_elite）
                    all_count = repo._execute(
                        "SELECT count(*) FROM factor_catalog WHERE market=?",
                        [market]
                    ).fetchone()[0]
                    logger.info("[L3] 诊断: market=%s 全部因子数=%d", market, all_count)
                    if all_count > 0:
                        sample = repo._execute(
                            "SELECT factor_id, name, market, is_elite, status FROM factor_catalog WHERE market=? LIMIT 3",
                            [market]
                        ).fetchall()
                        logger.info("[L3] 诊断样例: %s", sample)
            finally:
                repo.close()
        except Exception as e:
            logger.warning("[L3] DuckDB 加载失败: %s，回退到 JSON 加载", e)
            import traceback
            logger.debug("[L3] DuckDB 错误详情:\n%s", traceback.format_exc())

    # ── 路径 2: 从 JSON 文件加载（兜底） ──
    elite_path = Path(elite_dir)
    factors: list[dict[str, Any]] = []
    if not elite_path.exists():
        logger.warning("[L3] JSON 兜底路径不存在: %s", elite_path)
        return factors
    
    json_files = sorted(elite_path.glob("*.json"))
    valid_files = [f for f in json_files if not f.name.startswith("_")]
    logger.info("[L3] JSON 兜底: 扫描 %d 个文件 (有效=%d) [路径=%s, market=%s]",
                len(json_files), len(valid_files), elite_path, market)

    skipped_market = 0
    parse_errors = 0
    for fp in valid_files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            bt = data.get("evaluation", {}).get("level_1_backtest", {})
            code = data.get("code", "")
            code_hash = hashlib.sha256(code.encode()).hexdigest() if code else ""
            corr_meta = data.get("correlation_metadata", {})
            
            # 兼容新旧格式：优先使用 evaluation.level_1_backtest，缺失时回退到顶层字段
            sharpe = bt.get("sharpe")
            if sharpe is None:
                sharpe = data.get("sharpe", 0.5)
            ic = bt.get("ic")
            if ic is None:
                ic = data.get("ic", 0.02)
            turnover = bt.get("turnover_monthly")
            if turnover is None:
                turnover = data.get("turnover", 0.3)
            
            # 根据 market 过滤 JSON 因子
            factor_market = data.get("market", "stock")
            if factor_market != market:
                skipped_market += 1
                continue
            
            factors.append({
                "factor_id": data.get("factor_id", fp.stem),
                "name": data.get("name", fp.stem),
                "sharpe": sharpe,
                "ic": ic,
                "turnover": turnover,
                "decay_6m": data.get("decay_6m", 0.05),
                "code": code,
                "params": data.get("params", {}) or {},
                "economic_logic": data.get("economic_logic", {}) or {},
                "code_hash": code_hash,
                "correlation_metadata": corr_meta,
                "source_file": fp.name,
                "market": factor_market,
                "shadow_pool": data.get("shadow_pool"),
            })
        except (json.JSONDecodeError, TypeError) as e:
            parse_errors += 1
            logger.debug("[L3] JSON 解析错误: %s - %s", fp.name, e)
            continue
    logger.info("[L3] 从 JSON 文件加载 %d 个 elite 因子 [market=%s] (跳过市场不匹配=%d, 解析错误=%d)",
                len(factors), market, skipped_market, parse_errors)
    passed = _filter_by_quality_gate(factors, "JSON")
    passed = _filter_shadow_pending(passed, "JSON")
    return _deduplicate_by_base_name(passed, "JSON", panel_data=panel_data, corr_threshold=corr_threshold)


def load_l2_correlation_index(elite_dir: str | Path) -> list[dict[str, Any]]:
    """加载 L2 种子因子相关性索引（由 evolution_loop._write_seed_correlation_index 写入）。

    Returns:
        list[dict] — L2 高相关因子对列表:
            [{"factor_id_a": ..., "factor_id_b": ..., "pearson": ..., "spearman": ...}, ...]
    """
    elite_path = Path(elite_dir)
    index_path = elite_path / "_l2_seed_correlation_index.json"
    if not index_path.exists():
        logger.info("[L3] 未找到 L2 相关性索引文件，跳过先验加载")
        return []
    
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        correlations = data.get("correlations", [])
        logger.info("[L3] 加载 L2 相关性索引: %d 对高相关因子 (source=%s, created_at=%s)",
                    len(correlations),
                    data.get("source", "?"),
                    data.get("created_at", "?"))
        return correlations
    except (json.JSONDecodeError, TypeError):
        logger.warning("[L3] L2 相关性索引文件损坏，跳过加载")
        return []


# ─── 注入 FDT ────────────────────────────────────────────

def inject_to_fdt(
    combo: PortfolioCombo,
    proposals: list[AgentOptimizationProposal],
    output_dir: str | Path,
) -> dict[str, str]:
    """将组合 + 建议注入 FDT 可消费的配置目录。

    Args:
        combo: 组合
        proposals: Agent 优化建议列表
        output_dir: 输出目录（如 memory/portfolio）

    Returns:
        {file_type: absolute_path} 的映射
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: dict[str, str] = {}

    # 写入组合配置
    combo_fp = out / COMBO_FILE_NAME
    combo_fp.write_text(
        json.dumps(combo, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    paths["combo"] = str(combo_fp.resolve())

    # 写入权重配置（可直接被 multi_factor_strategy.py 加载的 JSON）
    weights = {}
    for s in combo.get("signals", []):
        if s.get("retained", True):
            weights[s["name"]] = s["weight"]
    weights_fp = out / "factor_weights.json"
    weights_fp.write_text(
        json.dumps({
            "version": EVOLUTION_VERSION,
            "updated_at": combo.get("updated_at", datetime.now().isoformat()),
            "synthesis_mode": combo.get("synthesis_mode", "equal_weight"),
            "weights": weights,
            "combo_sharpe": combo.get("combo_sharpe", 0),
            "n_factors": combo.get("n_factors", 0),
        }, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    paths["weights"] = str(weights_fp.resolve())

    # 写入 Agent 建议
    props_dir = out / PROPOSALS_DIR
    props_dir.mkdir(parents=True, exist_ok=True)
    for p in proposals:
        pp = props_dir / f"{p['proposal_id']}.json"
        pp.write_text(
            json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    paths["proposals_dir"] = str(props_dir.resolve())

    return paths


# ─── 主循环 ──────────────────────────────────────────────

@dataclass
class PortfolioRunResult:
    """L3 运行结果。"""
    run_id: str
    trace_id: str
    n_factors_input: int
    n_factors_retained: int
    combo_sharpe: float
    max_correlation: float
    n_proposals: int
    status: str
    error: Optional[str]
    output_paths: dict[str, str]


class PortfolioLoop:
    """L3 Portfolio Loop 主循环。

    流程:
        Step 1: 加载 elite 因子
        Step 2: 信号合成
        Step 2.5: Regime 自适应权重调整 (可选)
        Step 3: 因子正交化
        Step 4: 衰减检验
        Step 5: 组合构建
        Step 6: Verifier 判定
        Step 7: 注入 FDT

    Regime 自适应:
        当传入 market_ohlcv 时，自动检测市场制度并调整因子权重。
        支持 bull/bear/oscillate/high_vol/low_vol 五种制度的差异化权重。
    """

    def __init__(
        self,
        memory_dir: str | Path = "memory/portfolio",
        elite_dir: str | Path = "memory/knowledge/factors/elite",
        verifier_config: Optional[L3VerifierConfig] = None,
        synthesis_mode: str = "equal_weight",
        use_duckdb: bool = True,
        enable_regime_adaptation: bool = True,
        market: str = "stock",
        sticky_config: Optional[StickyConfig] = None,
    ):
        self.memory_dir = Path(memory_dir)
        self.elite_dir = Path(elite_dir)
        self.verifier = L3Verifier(verifier_config or DEFAULT_L3_VERIFIER_CONFIG)
        self.synthesis_mode = synthesis_mode
        self.use_duckdb = use_duckdb
        self.enable_regime_adaptation = enable_regime_adaptation
        self.market = market
        # 粘性约束默认启用（DEFAULT_STICKY_CONFIG: ±30% 变动 / 新因子首日封顶 0.10）
        self.sticky_config = sticky_config or DEFAULT_STICKY_CONFIG
        self.state_manager = PortfolioStateManager(memory_dir)
        self.portfolio_manager = PortfolioManager(memory_dir)
        self.drift_monitor = DriftMonitor(memory_dir)
        self._regime_selector: Optional[Any] = None

    def _generate_quality_report(self) -> None:
        """从 DuckDB 查询精英因子，生成最终质量报告 JSON。

        报告保存到 memory/portfolio/elite_final_quality_YYYY-MM-DD.json，
        与初始种子评测 (quality_ranking.json) 区分，记录实际进入组合的因子质量。
        """
        from datetime import datetime as _dt

        try:
            from .factor_db import FactorRepository
            repo = FactorRepository()
            try:
                rows = repo._execute("""
                    SELECT factor_id, name, ic, sharpe, turnover_monthly, decay_6m,
                           market, is_elite, status
                    FROM factor_catalog
                    WHERE status='active' AND is_elite=TRUE AND market=?
                    ORDER BY ic DESC
                """, [self.market]).fetchall()

                if not rows:
                    logger.info("[L3] Step 7.5: 无 active elite 因子，跳过质量报告")
                    return

                factors = []
                for r in rows:
                    factors.append({
                        "factor_id": r[0], "name": r[1],
                        "ic": round(r[2], 4), "sharpe": round(r[3], 4),
                        "turnover_monthly": round(r[4], 4),
                        "decay_6m": round(r[5], 4),
                        "market": r[6], "is_elite": r[7], "status": r[8],
                    })

                ics = [f["ic"] for f in factors]
                sharpes = [f["sharpe"] for f in factors]
                below_ic = sum(1 for ic in ics if abs(ic) < _RUNTIME_MIN_IC)
                below_sharpe = sum(1 for s in sharpes if s < _RUNTIME_MIN_SHARPE)

                # 加载当前状态获取 trace_id
                state = self.state_manager.load_or_init()
                report = {
                    "report_type": "elite_final_quality",
                    "generated_at": _dt.now().isoformat(),
                    "trace_id": state.get("current_trace_id"),
                    "thresholds": {"min_ic": _RUNTIME_MIN_IC, "min_sharpe": _RUNTIME_MIN_SHARPE},
                    "summary": {
                        "count": len(factors),
                        "ic_range": [round(min(ics), 4), round(max(ics), 4)],
                        "ic_mean": round(sum(ics) / len(ics), 4),
                        "sharpe_range": [round(min(sharpes), 4), round(max(sharpes), 4)],
                        "sharpe_mean": round(sum(sharpes) / len(sharpes), 4),
                        "below_ic_threshold": below_ic,
                        "below_sharpe_threshold": below_sharpe,
                    },
                    "factors": factors,
                }

                ts = _dt.now().strftime("%Y-%m-%d")
                out_file = self.memory_dir / f"elite_final_quality_{ts}.json"
                out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                logger.info(
                    "[L3] Step 7.5: 质量报告已生成 [%s]: %d 因子, IC=[%.4f, %.4f], "
                    "IC<%.2f: %d, Sharpe<%.1f: %d",
                    out_file.name, len(factors), min(ics), max(ics),
                    _RUNTIME_MIN_IC, below_ic, _RUNTIME_MIN_SHARPE, below_sharpe,
                )
            finally:
                repo.close()
        except Exception as e:
            logger.warning("[L3] Step 7.5: 质量报告生成失败 (非致命): %s", e)

    def run(
        self,
        market_ohlcv: Optional[Any] = None,
    ) -> PortfolioRunResult:
        """执行一次完整的 L3 Portfolio Loop。

        Args:
            market_ohlcv: 市场 OHLCV 数据（pd.DataFrame），用于 Regime 检测。
                         若为 None 或 enable_regime_adaptation=False，跳过自适应调整。
        """
        trace_id = generate_trace_id("l3")
        state = self.state_manager.mark_running()
        logger.info("[L3] ========== Portfolio Loop 启动 ==========")
        logger.info("[L3] trace_id=%s run_id=%s", trace_id, state.get("run_id"))
        logger.info("[L3] market=%s elite_dir=%s synthesis_mode=%s",
                    self.market, self.elite_dir, self.synthesis_mode)
        logger.info("[L3] use_duckdb=%s enable_regime=%s memory_dir=%s",
                    self.use_duckdb, self.enable_regime_adaptation, self.memory_dir)

        try:
            # Step 0.5: 加载市场数据（用于相关性去重）
            panel_data = None
            if self.market == "futures":
                try:
                    from ..data import FTSDataProvider
                    provider = FTSDataProvider()
                    panel_data, _cdates = provider.get_futures_panel(days=120)
                    logger.info("[L3] Step 0.5: 期货面板数据加载完成 (%d 品种, %d 交易日)",
                                len(panel_data), len(_cdates))
                except Exception as e:
                    logger.warning("[L3] Step 0.5: 期货面板数据加载失败 (%s)，使用 IC-only 去重", e)
                    panel_data = None

            # Step 1: 加载 elite 因子
            logger.info("[L3] Step 1a: 开始加载 elite 因子 [market=%s, use_duckdb=%s]",
                        self.market, self.use_duckdb)
            factors = load_elite_factors(
                self.elite_dir,
                use_duckdb=self.use_duckdb,
                market=self.market,
                panel_data=panel_data,
                corr_threshold=0.8,
            )
            logger.info("[L3] Step 1b: 因子加载完成, 共 %d 个 [market=%s]", len(factors), self.market)

            # 因子概要日志
            if factors:
                sharpe_values = [f.get("sharpe", 0) for f in factors]
                ic_values = [f.get("ic", 0) for f in factors]
                turnover_values = [f.get("turnover", 0) for f in factors]
                logger.info("[L3] 因子统计: sharpe=[%.2f, %.2f] ic=[%.4f, %.4f] turnover=[%.2f, %.2f]",
                            min(sharpe_values), max(sharpe_values),
                            min(ic_values), max(ic_values),
                            min(turnover_values), max(turnover_values))
                for i, f in enumerate(factors[:5]):
                    logger.info("[L3]   Top%d: %s | sharpe=%.2f | ic=%.4f | turnover=%.2f | src=%s",
                                i+1, f.get("name", "?"), f.get("sharpe", 0),
                                f.get("ic", 0), f.get("turnover", 0), f.get("source_file", "?"))
                if len(factors) > 5:
                    logger.info("[L3]   ... 还有 %d 个因子", len(factors) - 5)
            n_input = len(factors)

            if not factors:
                logger.warning("[L3] 无 elite 因子，跳过组合构建")
                result = PortfolioRunResult(
                    run_id=state["run_id"],
                    trace_id=trace_id,
                    n_factors_input=0,
                    n_factors_retained=0,
                    combo_sharpe=0.0,
                    max_correlation=0.0,
                    n_proposals=0,
                    status="completed",
                    error=None,
                    output_paths={},
                )
                state["total_signals_processed"] = 0
                state["total_signals_retained"] = 0
                self.state_manager.mark_completed(state)
                return result

            # Step 2: 信号合成
            signals, _max_corr, _combo_turn = synthesize_signals(
                factors, self.synthesis_mode, elite_dir=self.elite_dir,
            )
            logger.info("[L3] Step 2: 信号合成完成, mode=%s, 信号数=%d", self.synthesis_mode, len(signals))
            # [WEIGHT-LOG] Step 2 合成后权重摘要
            if signals:
                sorted_by_w = sorted(signals, key=lambda x: -x.get("weight", 0))
                w_sum = sum(s.get("weight", 0) for s in sorted_by_w)
                logger.info("[L3-WEIGHT] Step 2 权重摘要: sum=%.4f, top3=[%s], bottom3=[%s]",
                            w_sum,
                            ", ".join(f"{s['name']}={s['weight']:.4f}" for s in sorted_by_w[:3]),
                            ", ".join(f"{s['name']}={s['weight']:.4f}" for s in sorted_by_w[-3:]))
            state["total_signals_processed"] = len(signals)

            # Step 2.5: Regime 自适应权重调整
            regime_info: dict[str, Any] = {}
            if self.enable_regime_adaptation and market_ohlcv is not None:
                try:
                    if self._regime_selector is None:
                        from .regime import RegimeAwareSelector
                        self._regime_selector = RegimeAwareSelector()

                    regime = self._regime_selector.detect(market_ohlcv)
                    regime_info = regime

                    signals = regime_adaptive_weight_adjustment(
                        signals, regime, factors,
                    )
                    logger.info(
                        "[L3] Step 2.5: Regime=%s (confidence=%.2f), 自适应调整完成",
                        regime.get("regime", "unknown"),
                        regime.get("confidence", 0.0),
                    )
                except Exception as e:
                    logger.warning("[L3] Step 2.5: Regime 检测失败，跳过自适应调整: %s", e)
            elif self.enable_regime_adaptation and market_ohlcv is None:
                logger.info("[L3] Step 2.5: 无市场数据，跳过 Regime 自适应调整")

            # Step 3: 因子正交化（elastic_net 模式跳过，L1 已做变量选择）
            if self.synthesis_mode != "elastic_net":
                # 加载 L2 相关性索引作为先验
                l2_prior = load_l2_correlation_index(self.elite_dir)
                pre_retained = [s["name"] for s in signals if s.get("retained", True)]
                signals = orthogonalize_factors(
                    signals, max_corr_threshold=0.7, factors=factors,
                    use_tiered=(len(factors) >= 30),
                    l2_prior_correlations=l2_prior,
                )
                post_retained = [s["name"] for s in signals if s.get("retained", True)]
                removed_in_ortho = set(pre_retained) - set(post_retained)
                if removed_in_ortho:
                    logger.info("[L3-WEIGHT] 正交化移除 %d 个因子: %s",
                                len(removed_in_ortho), ", ".join(sorted(removed_in_ortho)))
                logger.info("[L3] Step 3: 正交化完成, 保留 %d/%d",
                            sum(1 for s in signals if s.get("retained", True)), len(signals))
            else:
                logger.info("[L3] Step 3: 跳过正交化（elastic_net L1 已做变量选择）")

            # Step 4: 衰减检验
            signals = decay_test(signals, max_decay_rate=0.30)
            n_retained = sum(1 for s in signals if s.get("retained", True))
            logger.info("[L3] Step 4: 衰减检验完成, 保留 %d 个因子", n_retained)

            # Step 5: 组合构建（含粘性约束 + 漂移监控）
            prev_combo = self.portfolio_manager.load_prev_combo()
            prev_weights = self.portfolio_manager.extract_prev_weights(prev_combo)
            if self.sticky_config and prev_weights:
                logger.info("[L3] Step 5: 读取上次组合 %s 共 %d 个因子权重 (粘性约束)",
                            prev_combo.get("combo_id", "?"), len(prev_weights))
            combo = build_combo(
                signals, self.synthesis_mode, trace_id,
                prev_weights=prev_weights or None,
                sticky_config=self.sticky_config,
            )
            logger.info("[L3] Step 5: 组合构建完成, 夏普=%.2f, 换手率=%.2f",
                        combo.get("combo_sharpe", 0), combo.get("combo_turnover", 0))

            # Step 5.5: 漂移监控 — 记录成员重合率 + 权重 L1 变化率
            try:
                drift = self.drift_monitor.compute(prev_combo, combo, trace_id)
                self.drift_monitor.record(drift)
            except Exception as e:
                logger.warning("[L3] Step 5.5: 漂移监控记录失败 (非致命): %s", e)

            # Step 6: Verifier 判定
            passed, reasons = self.verifier.check(combo)
            if not passed:
                logger.warning("[L3] Step 6: Verifier 未通过: %s", "; ".join(reasons))
                state["last_error"] = "; ".join(reasons)

            # Step 7: 注入 FDT
            proposals = generate_agent_proposals(combo, trace_id)
            paths = inject_to_fdt(combo, proposals, self.memory_dir)
            logger.info("[L3] Step 7: 注入完成, 路径=%s", paths)

            # 保存组合
            self.portfolio_manager.save_combo(combo)
            for p in proposals:
                self.portfolio_manager.save_proposal(p)

            # Step 7.5: 生成精英因子最终质量报告
            try:
                self._generate_quality_report()
            except Exception as e:
                logger.warning("[L3] Step 7.5: 质量报告生成失败 (非致命): %s", e)

            # 更新状态
            state["total_signals_retained"] = n_retained
            state["total_proposals_generated"] = len(proposals)
            combo_refs = state.get("combo_ref", [])
            if combo.get("combo_id") and combo["combo_id"] not in combo_refs:
                combo_refs.append(combo["combo_id"])
            state["combo_ref"] = combo_refs
            state["last_synthesis_mode"] = self.synthesis_mode
            self.state_manager.mark_completed(state)

            return PortfolioRunResult(
                run_id=state["run_id"],
                trace_id=trace_id,
                n_factors_input=n_input,
                n_factors_retained=n_retained,
                combo_sharpe=combo.get("combo_sharpe", 0),
                max_correlation=combo.get("max_correlation", 0),
                n_proposals=len(proposals),
                status="passed" if passed else "verifier_warning",
                error="; ".join(reasons) if not passed else None,
                output_paths=paths,
            )

        except Exception as e:
            logger.error("[L3] 运行失败: %s", e)
            self.state_manager.mark_circuit_broken(state, str(e))
            return PortfolioRunResult(
                run_id=state["run_id"],
                trace_id=trace_id,
                n_factors_input=0,
                n_factors_retained=0,
                combo_sharpe=0.0,
                max_correlation=0.0,
                n_proposals=0,
                status="circuit_broken",
                error=str(e),
                output_paths={},
            )


# ─── CLI ──────────────────────────────────────────────────

def main() -> None:
    """CLI 入口: python -m loop_engine.portfolio_loop [--once] [--mode equal_weight|sharpe_weight]"""
    parser = argparse.ArgumentParser(description="L3 Portfolio Loop")
    parser.add_argument("--once", action="store_true", help="单次运行模式")
    parser.add_argument("--mode", default="equal_weight",
                        choices=["equal_weight", "sharpe_weight", "elastic_net"],
                        help="信号合成模式")
    parser.add_argument("--memory-dir", default="memory/portfolio", help="状态/组合存储目录")
    parser.add_argument("--elite-dir", default="memory/knowledge/factors/elite", help="精英因子目录")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    loop = PortfolioLoop(
        memory_dir=args.memory_dir,
        elite_dir=args.elite_dir,
        synthesis_mode=args.mode,
    )
    result = loop.run()

    print(f"[L3] run_id={result.run_id} status={result.status} "
          f"input_factors={result.n_factors_input} retained={result.n_factors_retained} "
          f"sharpe={result.combo_sharpe:.2f} proposals={result.n_proposals}")
    if result.error:
        print(f"[L3] 警告/错误: {result.error}")
    sys.exit(0 if result.status in ("passed", "verifier_warning", "completed") else 1)


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "L3Error",
    "L3Verifier",
    "PortfolioStateManager",
    "PortfolioManager",
    "synthesize_signals",
    "orthogonalize_factors",
    "decay_test",
    "build_combo",
    "generate_agent_proposals",
    "load_elite_factors",
    "inject_to_fdt",
    "PortfolioRunResult",
    "PortfolioLoop",
    "main",
]
