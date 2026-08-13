"""
loop_engine/portfolio_loop.py — L3 Portfolio Loop 主循环

HARNESS §11-loop-engineering.md §16:
    L3 Portfolio Loop — 组合构建（因子筛选 + 信号合成 + Verifier 校验）

流程:
    Step 1: 加载 elite 因子 → 从 futures_elite 目录读取因子 JSON
    Step 1.7: 活跃因子数量上限（ACTIVE_FACTOR_CAP=20）
    Step 2: 信号合成 → 三种权重模式:
        - elastic_net: Elastic Net 截面回归（默认，L1+L2 自动变量选择）
        - equal_weight: 等权 1/N
        - sharpe_weight: 按 Sharpe 比率归一化加权
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

Sharpe 截断:
    - SHARPE_CAP = 2.0（因子 Sharpe > 2.0 截断，使用 _sharpe_raw 计算权重防均匀化）

版本: v1.2.0（与 FTS 同步）
"""
# pylint: disable=broad-exception-caught,too-few-public-methods,too-many-instance-attributes,too-many-locals

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional, cast

import numpy as np
import pandas as pd

from .contracts import (
    EVOLUTION_VERSION,
    STATE_SCHEMA_VERSION,
    DEFAULT_L3_VERIFIER_CONFIG,
    DEFAULT_L3_BUDGET,
    DEFAULT_VERIFIER_CONFIG,
    DEFAULT_STICKY_CONFIG,
    DEFAULT_ADAPTIVE_CONFIG,
    DEFAULT_DRIFT_ALERT_CONFIG,
    AdaptiveWeightConfig,
    AgentOptimizationProposal,
    DriftAlertConfig,
    DriftMetrics,
    FactorCorrelation,
    L3MetaLoopState,
    L3VerifierConfig,
    PortfolioCombo,
    PortfolioSignal,
    StickyConfig,
)
from .factor_returns import FactorReturnsBuilder
from .state import generate_run_id, generate_trace_id
from .weight_learning import WeightLearningConfig

logger = logging.getLogger(__name__)


# ─── 异常 ──────────────────────────────────────────────────


class L3Error(Exception):
    """L3 Portfolio Loop 操作失败。"""


# ─── 常量 ──────────────────────────────────────────────────

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
        6. combo_sharpe <= config.max_sharpe（过拟合保护）
    """

    def __init__(self, config: L3VerifierConfig):
        self._locked = True
        self._config = config

    def check(self, combo: PortfolioCombo) -> tuple[bool, list[str]]:
        """执行 Verifier 判定。"""
        if not self._locked:
            raise RuntimeError("L3 Verifier 未锁定")
        reasons: list[str] = []

        # 维度 1: 组合夏普（下限）
        if combo.get("combo_sharpe", 0) < self._config.get("min_sharpe", 2.0):
            reasons.append(f"组合夏普 {combo.get('combo_sharpe', 0):.2f} < {self._config['min_sharpe']}")

        # 维度 6: 组合夏普（上限 — P1 过拟合保护）
        max_sharpe = self._config.get("max_sharpe", 3.5)
        if combo.get("combo_sharpe", 0) > max_sharpe:
            reasons.append(
                f"组合夏普 {combo.get('combo_sharpe', 0):.2f} > {max_sharpe}（上限），强烈暗示过拟合，需人工复核"
            )

        # 维度 2: 最大相关性
        if combo.get("max_correlation", 1.0) > self._config.get("max_correlation", 0.3):
            reasons.append(f"最大相关性 {combo.get('max_correlation', 1.0):.2f} > {self._config['max_correlation']}")

        # 维度 3: 组合换手率
        if combo.get("combo_turnover", 1.0) > self._config.get("max_turnover", 0.5):
            reasons.append(f"组合换手率 {combo.get('combo_turnover', 1.0):.2f} > {self._config['max_turnover']}")

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
    """L3 组合状态持久化（SSOT 为 state.duckdb，plans/29 P4 读路径切换）。

    JSON（state.json/backup）退役为只读历史快照不再回写。
    """

    def __init__(self, memory_dir: str | Path = "memory/portfolio", state_store=None):
        # 保留 memory_dir 以派生 namespace/key（portfolio 根目录）
        self.memory_dir = Path(memory_dir)
        self._store = state_store  # None → 全局 SSOT（供测试注入临时 store）

    def _store_conn(self):
        """返回状态存储连接（注入的或全局 SSOT）。"""
        from fts.store.state_db import get_state_store

        return self._store if self._store is not None else get_state_store()

    def _ns_key(self) -> tuple[str, str]:
        """派生 state.duckdb 的 (namespace, key)（与 migrate 规则一致）。"""
        return "portfolio", "state"

    def load_or_init(self) -> L3MetaLoopState:
        ns, key = self._ns_key()
        data = self._store_conn().get(ns, key)
        if isinstance(data, dict) and data.get("schema_version") == STATE_SCHEMA_VERSION:
            return L3MetaLoopState(**data)
        state = self._init_state()
        self.save(state)
        return state

    def save(self, state: L3MetaLoopState) -> None:
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise L3Error(f"状态 schema 版本不匹配: {state.get('schema_version')} != {STATE_SCHEMA_VERSION}")
        state["last_updated"] = datetime.now().isoformat()
        ns, key = self._ns_key()
        self._store_conn().upsert(ns, key, state, run_id=state.get("run_id") or "")

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
            metrics_source="estimated",
            exposure_scale=None,
            regime_meta=None,
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
                        json.dumps(old, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        self._cache = combo
        self.combo_file.write_text(
            json.dumps(combo, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_prev_combo(self) -> PortfolioCombo | None:
        """读取上一次组合（current_combo.json 覆盖前的历史归档）。

        Returns:
            最近一次历史组合；无历史返回 None（冷启动）。
        """
        # 优先读磁盘上当前 combo 覆盖前的最新归档（combo_history 中时间最新者）
        history_files = sorted(
            self.combo_history_dir.glob("*.json"),
            key=lambda fp: fp.stat().st_mtime,
            reverse=True,
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
        result: dict[str, float] = {}
        for s in prev_combo.get("signals", []):
            if not s.get("retained", True):
                continue
            fid = s.get("factor_id")
            if fid:
                result[fid] = s.get("weight", 0.0)
        return result

    def save_proposal(self, proposal: AgentOptimizationProposal) -> str:
        """保存 Agent 优化建议，返回文件路径。"""
        pid = proposal.get("proposal_id", f"prop_{secrets.token_hex(4)}")
        fp = self.proposals_dir / f"{pid}.json"
        fp.write_text(
            json.dumps(proposal, ensure_ascii=False, indent=2),
            encoding="utf-8",
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
        "trend": 1.3,  # 趋势因子 +30%
        "momentum": 1.3,  # 动量因子 +30%
        "breakout": 1.2,  # 突破因子 +20%
        "carry": 1.1,  # 跨期套利 +10%
        "cross_section": 1.1,  # 横截面因子 +10%
        "fundamental": 1.0,  # 基本面不变
        "mean_reversion": 0.7,  # 均值回归 -30%
        "volatility": 0.9,  # 波动率因子 -10%
    },
    "bear": {
        "trend": 1.1,  # 趋势因子 +10%（空头趋势仍有效）
        "momentum": 0.8,  # 动量因子 -20%（反转风险）
        "breakout": 0.7,  # 突破因子 -30%
        "carry": 1.0,  # 跨期套利不变
        "volatility": 1.3,  # 波动率因子 +30%（防御）
        "mean_reversion": 1.2,  # 均值回归 +20%
        "liquidity": 1.2,  # 流动性因子 +20%
        "fundamental": 1.1,  # 基本面 +10%
    },
    "oscillate": {
        "mean_reversion": 1.3,  # 均值回归 +30%（震荡市核心）
        "reversal": 1.3,  # 反转因子 +30%
        "trend": 0.8,  # 趋势因子 -20%
        "momentum": 0.8,  # 动量因子 -20%
        "volatility": 1.1,  # 波动率因子 +10%
        "volume": 1.1,  # 成交量因子 +10%
    },
    "high_vol": {
        "volatility": 1.3,  # 波动率因子 +30%
        "mean_reversion": 1.1,  # 均值回归 +10%
        "trend": 0.7,  # 趋势因子 -30%
        "momentum": 0.7,  # 动量因子 -30%
        "breakout": 0.5,  # 突破因子 -50%（高波动假突破多）
        "carry": 1.0,  # 跨期套利不变
    },
    "low_vol": {
        "trend": 1.2,  # 趋势因子 +20%
        "momentum": 1.2,  # 动量因子 +20%
        "mean_reversion": 1.0,  # 均值回归不变
        "volatility": 0.7,  # 波动率因子 -30%
        "fundamental": 1.1,  # 基本面 +10%
    },
}

# Regime → FactorStyle 权重倍率映射表（A.3 style 维度，v2.56.0）
# 与 REGIME_FAMILY_MULTIPLIERS（family 维度）并行，未覆盖的 style 按 1.0 中性。
REGIME_STYLE_MULTIPLIERS: dict[str, dict[str, float]] = {
    "bull": {
        "momentum": 1.3,  # 趋势牛 → 动量 +30%
        "cross_section": 1.1,  # 横截面 +10%
        "carry": 1.1,  # Carry +10%
        "quality": 1.1,  # 质量 +10%
        "sentiment": 1.2,  # 情绪 +20%
        "mean_reversion": 0.7,  # 均值回归 -30%
        "value": 0.9,  # 价值 -10%
        "volatility": 0.9,  # 波动率 -10%
        "low_vol": 0.9,  # 低波 -10%
    },
    "bear": {
        "volatility": 1.3,  # 下跌 → 波动率 +30%（防御）
        "defensive": 1.3,  # 防御 +30%
        "mean_reversion": 1.2,  # 均值回归 +20%（超跌反弹）
        "value": 1.2,  # 价值 +20%
        "low_vol": 1.1,  # 低波 +10%
        "quality": 1.1,  # 质量 +10%
        "momentum": 0.8,  # 动量 -20%（反转风险）
        "sentiment": 0.8,  # 情绪 -20%
        "high_beta": 0.6,  # 高 beta -40%
        "cross_section": 0.9,  # 横截面 -10%
    },
    "oscillate": {
        "mean_reversion": 1.3,  # 震荡 → 均值回归 +30%
        "value": 1.2,  # 价值 +20%
        "carry": 1.1,  # Carry +10%
        "quality": 1.0,  # 质量中性
        "momentum": 0.8,  # 动量 -20%
        "high_beta": 0.7,  # 高 beta -30%
        "volatility": 1.1,  # 波动率 +10%
        "sentiment": 1.0,  # 情绪中性
    },
    "high_vol": {
        "volatility": 1.3,  # 高波 → 波动率 +30%
        "low_vol": 1.2,  # 低波 +20%（避险）
        "defensive": 1.3,  # 防御 +30%
        "mean_reversion": 1.1,  # 均值回归 +10%
        "momentum": 0.7,  # 动量 -30%
        "high_beta": 0.5,  # 高 beta -50%（高波假突破多）
        "sentiment": 0.8,  # 情绪 -20%
        "cross_section": 0.8,  # 横截面 -20%
    },
    "low_vol": {
        "momentum": 1.2,  # 低波 → 动量 +20%（趋势延续）
        "carry": 1.1,  # Carry +10%
        "quality": 1.1,  # 质量 +10%
        "value": 1.1,  # 价值 +10%
        "volatility": 0.7,  # 波动率 -30%
        "high_beta": 1.2,  # 高 beta +20%（低波下风险偏好回升）
        "mean_reversion": 1.0,  # 均值回归中性
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


# GAP-L308: 数据驱动 Regime 倍率表（进程级缓存，由 load_data_driven_multipliers 加载）
_DATA_DRIVEN_FAMILY_MULTIPLIERS: dict[str, dict[str, float]] = {}
_DEFAULT_REGIME_MULTIPLIERS_PATH = "docs/harness/_data/l3_regime_multipliers.yaml"


def load_data_driven_multipliers(path: Optional[str] = None) -> dict[str, dict[str, float]]:
    """加载数据驱动 Regime 倍率表（GAP-L308）。

    从 `docs/harness/_data/l3_regime_multipliers.yaml` 加载
    RegimeMultiplierEstimator.export_yaml 产出的倍率表并缓存到进程级全局。
    文件缺失/损坏时保留空表（回退硬编码）。

    Args:
        path: YAML 路径（None 用默认路径）

    Returns:
        倍率表 dict（regime → family → multiplier）；未加载时为空 dict。
    """
    global _DATA_DRIVEN_FAMILY_MULTIPLIERS
    p = Path(path) if path else Path(_DEFAULT_REGIME_MULTIPLIERS_PATH)
    if not p.exists():
        logger.info("[L3-Regime] 数据驱动倍率表不存在 (%s)，使用硬编码表", p)
        return {}
    try:
        import yaml

        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        mult = doc.get("multipliers") or {}
        _DATA_DRIVEN_FAMILY_MULTIPLIERS = {
            regime: {fam: float(v) for fam, v in fam_map.items()}
            for regime, fam_map in mult.items()
            if isinstance(fam_map, dict)
        }
        logger.info(
            "[L3-Regime] 数据驱动倍率表加载成功 [%s]: %d regimes",
            p,
            len(_DATA_DRIVEN_FAMILY_MULTIPLIERS),
        )
        return _DATA_DRIVEN_FAMILY_MULTIPLIERS
    except Exception as e:  # noqa: BLE001
        logger.warning("[L3-Regime] 数据驱动倍率表加载失败 (%s)，使用硬编码表", e)
        _DATA_DRIVEN_FAMILY_MULTIPLIERS = {}
        return {}


def regime_adaptive_weight_adjustment(
    signals: list[PortfolioSignal],
    regime: dict[str, Any],
    factors: list[dict[str, Any]],
    min_weight: float = 0.01,
    dimension: str = "family",
    min_clamp: float = 0.5,
    max_clamp: float = 1.5,
    probability_mix: bool = True,
) -> list[PortfolioSignal]:
    """根据市场制度自适应调整因子权重。

    核心逻辑:
    1. 从 MarketRegime 获取当前制度名（bull/bear/oscillate/high_vol/low_vol）
    2. 遍历每个 signal，根据其 factor_id 在 factors 中查找 family / style_tags
    3. 按维度查表获取倍率:
       - dimension="family": REGIME_FAMILY_MULTIPLIERS（FactorFamily 维度；
         GAP-L308 数据驱动表 DATA_DRIVEN_FAMILY_MULTIPLIERS 存在时优先）
       - dimension="style":  REGIME_STYLE_MULTIPLIERS（FactorStyle 维度，v2.56.0）
       - dimension="both":   family × style 双倍率乘积，乘积 clamp 到 [min_clamp, max_clamp]×base
    4. 将原始权重 × 倍率 → 调整后权重（不低于 min_weight 比例）
    5. 对高波动期（high_vol）额外缩减衰减过快因子

    制度概率混合（28-T3，regime blend，对标 Two Sigma / AQR）:
    - 启用 probability_mix 且 regime 含 regime_probs 时，对全部制度倍率表按概率
      加权混合: mult = Σ p_i × table_i，替代硬查表——制度误判只按概率摊薄而非全错。
    - 无 regime_probs 或 probability_mix=False 时回退旧硬查表逻辑（向后兼容）。

    Args:
        signals: 合成后的信号列表
        regime: 市场制度检测结果 (from RegimeAwareSelector.detect())
        factors: 原始因子列表（含 family/style_tags 字段）
        min_weight: 最小权重下限（避免完全归零）
        dimension: 调整维度 "family" | "style" | "both"
        min_clamp: 双维度乘积下限 clamp 倍率（dimension="both" 时生效）
        max_clamp: 双维度乘积上限 clamp 倍率（dimension="both" 时生效）
        probability_mix: 是否启用制度概率混合（regime blend，默认 True）

    Returns:
        调整后的 signals 列表（权重已更新，retained 可能变化）
    """
    if not signals or not regime:
        return signals

    regime_name = regime.get("regime", "oscillate")
    # GAP-L308: 数据驱动倍率优先（由 RegimeMultiplierEstimator 加载），缺失回退硬编码表
    family_multipliers = (
        _DATA_DRIVEN_FAMILY_MULTIPLIERS.get(regime_name, {})
        if _DATA_DRIVEN_FAMILY_MULTIPLIERS
        else REGIME_FAMILY_MULTIPLIERS.get(regime_name, {})
    )
    style_multipliers = REGIME_STYLE_MULTIPLIERS.get(regime_name, {})

    # 制度概率混合（28-T3）：按制度概率对各制度倍率表加权混合
    regime_probs: dict[str, float] | None = regime.get("regime_probs") if probability_mix else None
    # blend 需跨制度取表（mult = Σ p_i × table_i）：数据驱动全表优先，缺失回退硬编码全表
    family_tables: dict[str, dict[str, float]] = (
        _DATA_DRIVEN_FAMILY_MULTIPLIERS if _DATA_DRIVEN_FAMILY_MULTIPLIERS else REGIME_FAMILY_MULTIPLIERS
    )
    style_tables: dict[str, dict[str, float]] = REGIME_STYLE_MULTIPLIERS
    if not family_multipliers and not style_multipliers and regime_probs is None:
        logger.info("[L3-Regime] 无制度倍率配置，跳过自适应调整 [regime=%s]", regime_name)
        return signals

    # 构建 factor_id → family / style_tags 映射
    factor_family_map: dict[str, str] = {}
    factor_style_map: dict[str, str] = {}
    for f in factors:
        fid = f.get("factor_id", "")
        if not fid:
            continue
        family = f.get("family", "")
        factor_family_map[fid] = family or _infer_factor_family_from_name(f.get("name", ""))
        # style: 显式 style_tags 优先，其次名称推断
        style_tags = f.get("style_tags") or []
        if style_tags and isinstance(style_tags, list):
            factor_style_map[fid] = str(style_tags[0])
        else:
            factor_style_map[fid] = _infer_factor_style_from_name(f.get("name", ""))

    # 应用倍率调整
    adjustment_log: list[str] = []
    for s in signals:
        fid = s.get("factor_id", "")
        family = factor_family_map.get(fid, "other")
        style = factor_style_map.get(fid, "other")

        # 获取各维度倍率（默认 1.0）；启用概率混合时按制度概率跨制度加权（28-T3）
        if regime_probs:
            family_mult = sum(
                p * (family_tables.get(r, {}).get(family, 1.0) if family_tables else 1.0)
                for r, p in regime_probs.items()
            )
            style_mult = sum(
                p * (style_tables.get(r, {}).get(style, 1.0) if style_tables else 1.0)
                for r, p in regime_probs.items()
            )
        else:
            family_mult = family_multipliers.get(family, 1.0)
            style_mult = style_multipliers.get(style, 1.0)

        if dimension == "style":
            multiplier = style_mult
        elif dimension == "both":
            multiplier = family_mult * style_mult
            # 双维度乘积 clamp 到 [min_clamp, max_clamp]，防止过度调权
            multiplier = max(min_clamp, min(max_clamp, multiplier))
        else:  # "family"（默认，向后兼容）
            multiplier = family_mult

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
                f"  {s.get('name', fid)} [{family}/{style}]: "
                f"{original_weight:.4f} → {adjusted_weight:.4f} (×{multiplier:.2f})"
            )

        s["weight"] = adjusted_weight

    # 日志
    if adjustment_log:
        logger.info(
            "[L3-Regime] 自适应权重调整完成 [regime=%s, dim=%s, adjusted=%d/%d]:\n%s",
            regime_name,
            dimension,
            len(adjustment_log),
            len(signals),
            "\n".join(adjustment_log),
        )
    else:
        logger.info("[L3-Regime] 自适应权重调整完成 [regime=%s, dim=%s, 无需调整]", regime_name, dimension)

    return signals


def _compute_exposure_scale(
    regime: Optional[dict[str, Any]],
    enabled: bool = True,
    scale_min: float = 0.3,
    entropy_penalty: float = 0.5,
) -> float:
    """计算置信度仓位缩放因子（28-T4，T6 在 build_combo 消费）。

    对标机构 vol targeting 简化版：低确定性（高后验熵 / 低置信度）降低总暴露。
    未启用或 regime 缺失/异常时返回 1.0（不缩放），保证默认行为不变。

    Args:
        regime: 市场制度检测结果（含 confidence / regime_probs，可无 regime_probs）。
        enabled: 是否启用置信度缩放。
        scale_min: 熵标定后缩放下限。
        entropy_penalty: 熵标定惩罚系数。

    Returns:
        暴露缩放因子 ∈ [scale_min, 1.0]；未启用/异常时 1.0。
    """
    if not enabled or regime is None:
        return 1.0
    try:
        from .regime_calibration import RegimeConfidenceCalibrator

        calibrator = RegimeConfidenceCalibrator(
            entropy_penalty=float(entropy_penalty),
            scale_min=float(scale_min),
        )
        return float(
            calibrator.calibrate(
                regime.get("confidence", 0.5),
                regime.get("regime_probs"),
            )
        )
    except Exception as e:  # 容错兜底：任何异常不阻断主流程
        logger.warning("[L3-Regime] 置信度仓位缩放计算失败，回退 1.0: %s", e)
        return 1.0


def _infer_factor_style_from_name(name: str) -> str:
    """从因子名称推断其风格标签（FactorStyle 维度，v2.56.0）。

    Args:
        name: 因子名称

    Returns:
        推断的风格标签（"momentum", "mean_reversion" 等）
    """
    name_lower = (name or "").lower()

    # intraday / open_interest 是最具体的维度，优先于通用动量/价值等
    if any(kw in name_lower for kw in ("intraday", "minute", "tick")):
        return "intraday"
    if any(kw in name_lower for kw in ("open_interest", "oi_change", "position_change")):
        return "open_interest"
    if any(kw in name_lower for kw in ("momentum", "trend", "breakout", "follow", "roc")):
        return "momentum"
    if any(kw in name_lower for kw in ("reversion", "mean", "reversal", "bounce")):
        return "mean_reversion"
    if any(kw in name_lower for kw in ("carry", "spread", "arbitrage", "basis")):
        return "carry"
    if any(kw in name_lower for kw in ("pe_", "_pe", "pb_", "_pb", "value", "dividend")):
        return "value"
    if any(kw in name_lower for kw in ("lowvol", "low_vol")):
        return "low_vol"
    if any(kw in name_lower for kw in ("beta",)):
        return "high_beta"
    if any(kw in name_lower for kw in ("defensive", "defense")):
        return "defensive"
    if any(kw in name_lower for kw in ("growth", "earnings", "revenue")):
        return "growth"
    if any(kw in name_lower for kw in ("quality", "roe", "roa", "profit")):
        return "quality"
    if any(kw in name_lower for kw in ("sentiment", "analyst", "media", "news")):
        return "sentiment"
    if any(kw in name_lower for kw in ("volatility", "vol", "atr", "bollinger")):
        return "volatility"
    if any(kw in name_lower for kw in ("cross_section", "cs_", "rank")):
        return "cross_section"

    return "other"


def synthesize_signals(
    factors: list[dict[str, Any]],
    mode: str = "equal_weight",
    elite_dir: str | Path | None = None,
    returns_matrix: Optional[pd.DataFrame] = None,
    optimizer_mode: str = "risk_parity",
    optimizer_config: Optional[dict[str, Any]] = None,
    market: str = "futures",
    weight_config: Optional[Any] = None,
    ic_matrix: Optional[np.ndarray] = None,
) -> tuple[list[PortfolioSignal], float, float]:
    """信号合成。

    Args:
        factors: 每个 dict 必须含 factor_id, name, sharpe, ic, turnover, decay_6m
        mode: "equal_weight" | "sharpe_weight" | "elastic_net" | "ml_ensemble"
              | "optimizer"（GAP-F07/GAP-L303：PortfolioOptimizer 约束优化，需 returns_matrix）
              | "ic_weight"（GAP-064：IC 协方差加权，需 ic_matrix；否则回退 IC 均值加权）
        elite_dir: 精英因子目录（elastic_net/ml_ensemble 模式需要，用于加载因子代码）
        returns_matrix: 因子历史收益矩阵（行=样本，列=因子 factor_id；
            optimizer 模式需要，列将自动对齐 factors 顺序）
        optimizer_mode: optimizer 模式目标 "risk_parity" | "mvo" | "bl"（GAP-L303；
            "bl"=Black-Litterman 观点融合，C3：先验=风险平价，观点=IC 自动构建或
            optimizer_config["views_p"]/["views_q"] 显式传入）
        optimizer_config: PortfolioOptimizer 配置透传（OptimizerConfig 字段 dict，
            含 neutralization/exposure_tolerance，GAP-L304；可含 "exposure_matrix"/"target_exposure"/
            "capacity_limits"（GAP-L305））
        market: 目标交易市场（elastic_net 权重学习面板自动匹配，v2.74.0）
        weight_config: 机构级权重学习配置（WeightLearningConfig，None 用默认，v2.74.0）
        ic_matrix: T×N IC 观测矩阵（列序与 factors 一致；ic_weight 模式用，GAP-064）

    Returns:
        (signals, max_correlation, combo_turnover)
    """
    if not factors:
        return [], 0.0, 0.0

    n = len(factors)

    # IC 上限截断（P2 差距修复）：IC > 0.15 的因子按 0.15 计算权重，
    # 防止过拟合因子主导组合权重分配。IC 原始值保留在 _ic_raw 字段中供审计。
    IC_CAP = 0.15
    for f in factors:
        raw_ic = f.get("ic", 0.0)
        if abs(raw_ic) > IC_CAP:
            f["_ic_raw"] = raw_ic
            f["ic"] = IC_CAP * (1 if raw_ic > 0 else -1)

    # Sharpe 上限截断（P0 过拟合修复）：Sharpe > 2.0 的因子按 2.0 计算权重，
    # 防止过拟合因子主导组合权重分配。Sharpe 原始值保留在 _sharpe_raw 字段中供审计。
    for f in factors:
        raw_sharpe = f.get("sharpe", 0.0)
        if raw_sharpe > SHARPE_CAP:
            f["_sharpe_raw"] = raw_sharpe
            f["sharpe"] = SHARPE_CAP

    if mode == "elastic_net" and elite_dir is not None:
        elastic_weights = _compute_elastic_net_weights(factors, Path(elite_dir), config=weight_config, market=market)
        if not elastic_weights:
            logger.warning("[L3] Elastic Net 权重计算失败，回退到 sharpe_weight")
            return synthesize_signals(factors, "sharpe_weight")

        signals: list[PortfolioSignal] = []
        for f in factors:
            w = elastic_weights.get(f["factor_id"], 0.0)
            signals.append(
                PortfolioSignal(
                    factor_id=f["factor_id"],
                    name=f["name"],
                    weight=w,
                    sharpe=f.get("sharpe", 0.0),
                    ic=f.get("ic", 0.0),
                    turnover=f.get("turnover", 0.0),
                    decay_6m=f.get("decay_6m", 0.0),
                    orthogonalized=True,  # Elastic Net L1 已做变量选择
                    retained=w > 0.0,
                )
            )
        logger.info(
            "[L3] Elastic Net 完成: %d/%d 因子获得非零权重", sum(1 for s in signals if s["retained"]), len(signals)
        )
    elif mode == "ml_ensemble" and elite_dir is not None:
        ml_weights = _compute_ml_ensemble_weights(factors, Path(elite_dir), market=market)
        if not ml_weights:
            logger.warning("[L3] ML Ensemble 权重计算失败，回退到 sharpe_weight")
            return synthesize_signals(factors, "sharpe_weight")

        signals = []
        for f in factors:
            w = ml_weights.get(f["factor_id"], 0.0)
            signals.append(
                PortfolioSignal(
                    factor_id=f["factor_id"],
                    name=f["name"],
                    weight=w,
                    sharpe=f.get("sharpe", 0.0),
                    ic=f.get("ic", 0.0),
                    turnover=f.get("turnover", 0.0),
                    decay_6m=f.get("decay_6m", 0.0),
                    orthogonalized=True,  # ML 特征重要性已做变量选择
                    retained=w > 0.0,
                )
            )
        logger.info(
            "[L3] ML Ensemble 完成: %d/%d 因子获得非零权重", sum(1 for s in signals if s["retained"]), len(signals)
        )
    elif mode == "equal_weight":
        # P2 PCA 权重优先（v2.103.0+24）：enable_pca=True 时 Step 1.9 已写入
        # f["pca_weight"]（载荷×解释方差加权）；有 pca_weight 时替换均匀等权，否则回退 1/N。
        pca_ws = [float(f.get("pca_weight", 0.0) or 0.0) for f in factors]
        w_total = sum(pca_ws)
        if w_total > 0:
            weights = [pw / w_total for pw in pca_ws]
            logger.info("[L3] equal_weight 模式启用 PCA 权重（Step 1.9 输出，Σ=%.4f）", w_total)
        else:
            weights = [1.0 / n] * n
        signals = []
        for f, w in zip(factors, weights):
            signals.append(
                PortfolioSignal(
                    factor_id=f["factor_id"],
                    name=f["name"],
                    weight=w,
                    sharpe=f.get("sharpe", 0.0),
                    ic=f.get("ic", 0.0),
                    turnover=f.get("turnover", 0.0),
                    decay_6m=f.get("decay_6m", 0.0),
                    orthogonalized=bool(f.get("pca_orthogonalized", False)),
                    retained=True,
                )
            )
    elif mode == "sharpe_weight":
        # 使用截断前的原始 Sharpe 计算权重（_sharpe_raw 优先），
        # 保留截断后的 sharpe 字段用于 Verifier 校验和显示。
        # 避免所有因子因 Sharpe 上限截断而获得相同权重。
        weight_sharpes = [max(f.get("_sharpe_raw", f.get("sharpe", 0)), 0.01) for f in factors]
        total_sharpe = sum(weight_sharpes)
        signals = []
        for i, f in enumerate(factors):
            w = weight_sharpes[i] / total_sharpe if total_sharpe > 0 else 1.0 / n
            signal = PortfolioSignal(
                factor_id=f["factor_id"],
                name=f["name"],
                weight=w,
                sharpe=f.get("sharpe", 0.0),
                ic=f.get("ic", 0.0),
                turnover=f.get("turnover", 0.0),
                decay_6m=f.get("decay_6m", 0.0),
                orthogonalized=False,
                retained=True,
            )
            # 传递截断前的原始值（P0 过拟合修复）
            if "_sharpe_raw" in f:
                signal["_sharpe_raw"] = f["_sharpe_raw"]
            if "_ic_raw" in f:
                signal["_ic_raw"] = f["_ic_raw"]
            signals.append(signal)
        # [WEIGHT-LOG] 权重计算详情
        logger.info("[L3-WEIGHT] sharpe_weight 模式: total_sharpe=%.2f, n_factors=%d", total_sharpe, n)
        for idx, s in enumerate(sorted(signals, key=lambda x: -x["weight"])):
            logger.info(
                "[L3-WEIGHT]   [%d] %s | sharpe=%.2f | raw_weight=%.4f", idx + 1, s["name"], s["sharpe"], s["weight"]
            )
    elif mode == "ic_weight":
        # GAP-064 (v2.94.0): IC 加权。提供 ic_matrix（T×N，列序与 factors 一致）时
        # 升级为 IC 协方差加权 w=(Σ+λI)⁻¹μ（Ledoit-Wolf 收缩 + 正则）；
        # 未提供/样本不足时回退 IC 均值加权（w ∝ |ic|）。
        ic_weights: Optional[np.ndarray] = None
        if ic_matrix is not None:
            try:
                from .weight_learning import ic_covariance_weights

                ic_weights = ic_covariance_weights(np.asarray(ic_matrix, dtype=float))
            except Exception as e:  # noqa: BLE001
                logger.warning("[L3] IC 协方差加权失败，回退 IC 均值加权: %s", e)
                ic_weights = None
        signals = []
        if ic_weights is not None and len(ic_weights) == n:
            logger.info("[L3-WEIGHT] ic_weight 模式: IC 协方差加权 (Σ⁻¹μ, n=%d 因子)", n)
            for i, f in enumerate(factors):
                signals.append(
                    PortfolioSignal(
                        factor_id=f["factor_id"],
                        name=f["name"],
                        weight=float(ic_weights[i]),
                        sharpe=f.get("sharpe", 0.0),
                        ic=f.get("ic", 0.0),
                        turnover=f.get("turnover", 0.0),
                        decay_6m=f.get("decay_6m", 0.0),
                        orthogonalized=False,
                        retained=True,
                    )
                )
        else:
            mu_ics = [abs(f.get("ic", 0.0)) for f in factors]
            total_mu = sum(mu_ics)
            logger.info("[L3-WEIGHT] ic_weight 模式: IC 均值加权回退 (w ∝ |ic|, n=%d)", n)
            for i, f in enumerate(factors):
                w = mu_ics[i] / total_mu if total_mu > 0 else 1.0 / n
                signals.append(
                    PortfolioSignal(
                        factor_id=f["factor_id"],
                        name=f["name"],
                        weight=w,
                        sharpe=f.get("sharpe", 0.0),
                        ic=f.get("ic", 0.0),
                        turnover=f.get("turnover", 0.0),
                        decay_6m=f.get("decay_6m", 0.0),
                        orthogonalized=False,
                        retained=True,
                    )
                )
    elif mode == "adaptive":
        # 自适应模式（A.3 / v2.56.0）: 以 Sharpe 权重为基，
        # 后续由 Step 2.5 regime 双维度调整（family×style）+ RegimeSmoother 接管。
        # 回测路径 PortfolioConstructor(weight_method="adaptive") 语义与本分支一致。
        weight_sharpes = [max(f.get("_sharpe_raw", f.get("sharpe", 0)), 0.01) for f in factors]
        total_sharpe = sum(weight_sharpes)
        signals = []
        for i, f in enumerate(factors):
            w = weight_sharpes[i] / total_sharpe if total_sharpe > 0 else 1.0 / n
            signal = PortfolioSignal(
                factor_id=f["factor_id"],
                name=f["name"],
                weight=w,
                sharpe=f.get("sharpe", 0.0),
                ic=f.get("ic", 0.0),
                turnover=f.get("turnover", 0.0),
                decay_6m=f.get("decay_6m", 0.0),
                orthogonalized=False,
                retained=True,
            )
            if "_sharpe_raw" in f:
                signal["_sharpe_raw"] = f["_sharpe_raw"]
            if "_ic_raw" in f:
                signal["_ic_raw"] = f["_ic_raw"]
            signals.append(signal)
        logger.info("[L3] adaptive 模式: 基权重=sharpe_weight (Regime 调整在 Step 2.5)")
    elif mode == "optimizer":
        # GAP-F07/GAP-L303 (v2.61.0): PortfolioOptimizer 约束优化，需因子历史收益矩阵。
        if returns_matrix is None or len(returns_matrix) < 2:
            logger.warning("[L3] optimizer 模式需 returns_matrix，回退到 sharpe_weight")
            return synthesize_signals(factors, "sharpe_weight")

        # 列对齐到 factors 顺序（GAP-L303）
        rm = FactorReturnsBuilder.align_to_factors(returns_matrix, [f["factor_id"] for f in factors])
        if len(rm.columns) != n or len(rm) < 20:
            logger.warning(
                "[L3] optimizer 模式矩阵不可对齐（因子 %d/%d, 观测 %d < 20），回退到 sharpe_weight",
                len(rm.columns),
                n,
                len(rm),
            )
            return synthesize_signals(factors, "sharpe_weight")

        from fts.factor_engine.portfolio_optimizer import (
            OptimizerConfig,
            PortfolioOptimizer,
        )
        from fts.factor_engine.risk_model import RiskModelEstimator

        # 协方差用 Ledoit-Wolf 收缩估计（GAP-L302 联动）
        cov = RiskModelEstimator().estimate(rm).cov

        # 构造优化配置（GAP-L303/L304/L305）：exposure_matrix/target_exposure/capacity_limits 透传，不入 OptimizerConfig
        cfg_dict = dict(optimizer_config or {})
        # "mvo" 为 "mean_variance" 的 CLI 别名（GAP-L303）
        mode_internal = "mean_variance" if optimizer_mode == "mvo" else optimizer_mode
        exposure_matrix = cfg_dict.pop("exposure_matrix", None)
        target_exposure = cfg_dict.pop("target_exposure", None)
        capacity_limits = cfg_dict.pop("capacity_limits", None)

        # C3: Black-Litterman 观点融合（optimizer_mode="bl"）
        if mode_internal == "bl":
            try:
                bl_signals = _synthesize_bl_weights(cov, factors, cfg_dict)
            except Exception as e:  # noqa: BLE001
                logger.warning("[L3] Black-Litterman 融合异常 (%s)，回退 risk_parity", e)
                bl_signals = None
            if bl_signals is None:
                logger.warning("[L3] Black-Litterman 融合失败，回退 risk_parity")
                return synthesize_signals(
                    factors,
                    "optimizer",
                    elite_dir=elite_dir,
                    returns_matrix=returns_matrix,
                    optimizer_mode="risk_parity",
                    optimizer_config=optimizer_config,
                    market=market,
                    weight_config=weight_config,
                    ic_matrix=ic_matrix,
                )
            signals = bl_signals
        else:
            cfg_dict.setdefault("mode", mode_internal)
            opt_cfg = OptimizerConfig(**cfg_dict)
            opt = PortfolioOptimizer(opt_cfg)

            # 期望收益代理（mvo 模式需要）：截断后 Sharpe 作为 alpha 代理
            mu = np.array(
                [f.get("_sharpe_raw", f.get("sharpe", 0.0)) for f in factors],
                dtype=float,
            )
            weights = opt.optimize(
                cov=cov,
                expected_returns=mu,
                exposure_matrix=exposure_matrix,
                target_exposure=target_exposure,
                capacity_limits=capacity_limits,
            )
            signals = []
            for i, f in enumerate(factors):
                wi = float(weights[i])
                signals.append(
                    PortfolioSignal(
                        factor_id=f["factor_id"],
                        name=f["name"],
                        weight=wi,
                        sharpe=f.get("sharpe", 0.0),
                        ic=f.get("ic", 0.0),
                        turnover=f.get("turnover", 0.0),
                        decay_6m=f.get("decay_6m", 0.0),
                        orthogonalized=False,
                        retained=wi > 0.0,
                    )
                )
            logger.info(
                "[L3] PortfolioOptimizer(%s) 完成: %d/%d 因子获得非零权重",
                opt._config.mode,
                sum(1 for s in signals if s["retained"]),
                len(signals),
            )
    else:
        # lightgbm 等未实现模式暂回退等权
        signals = []
        for f in factors:
            signals.append(
                PortfolioSignal(
                    factor_id=f["factor_id"],
                    name=f["name"],
                    weight=1.0 / n,
                    sharpe=f.get("sharpe", 0.0),
                    ic=f.get("ic", 0.0),
                    turnover=f.get("turnover", 0.0),
                    decay_6m=f.get("decay_6m", 0.0),
                    orthogonalized=False,
                    retained=True,
                )
            )

    # 估算最大相关性和组合换手率
    max_corr = 0.0
    total_turnover = sum(s.get("turnover", 0) for s in signals) / len(signals) if signals else 0.0

    return signals, max_corr, total_turnover


def _synthesize_bl_weights(
    cov: np.ndarray,
    factors: list[dict[str, Any]],
    cfg_dict: dict[str, Any],
) -> Optional[list[PortfolioSignal]]:
    """C3: Black-Litterman 观点融合合成信号。

    先验权重 = 同协方差风险平价解；观点来源 = 显式（cfg_dict["views_p"]/["views_q"]）
    或 build_auto_views 自动构建（因子原始 IC × 先验隐含收益尺度）。

    Args:
        cov: Ledoit-Wolf 收缩协方差 (n, n)
        factors: 因子列表（含 factor_id/name/sharpe/ic/turnover/decay_6m）
        cfg_dict: optimizer_config 中 BL 专属字段（tau/omega_scale/risk_aversion/
            max_weight/max_leverage/views_p/views_q；已剔除 mode/exposure 等）

    Returns:
        signals 列表；融合失败返回 None（调用方回退 risk_parity）
    """
    try:
        from fts.factor_engine.black_litterman import (
            BlackLittermanConfig,
            black_litterman_weights,
            build_auto_views,
            implied_returns,
        )
        from fts.factor_engine.portfolio_optimizer import (
            OptimizerConfig,
            PortfolioOptimizer,
        )

        n = len(factors)
        bl_dict = {
            key: cfg_dict.pop(key)
            for key in ("tau", "omega_scale", "risk_aversion", "max_weight", "max_leverage")
            if key in cfg_dict
        }
        views_p = cfg_dict.pop("views_p", None)
        views_q = cfg_dict.pop("views_q", None)

        # 先验权重 = 风险平价（同协方差求解）
        prior_opt = PortfolioOptimizer(
            OptimizerConfig(
                mode="risk_parity",
                max_weight=bl_dict.get("max_weight", 0.3),
                max_leverage=bl_dict.get("max_leverage", 1.0),
            )
        )
        prior_w = prior_opt.optimize(cov=cov)
        pi = implied_returns(cov, prior_w, bl_dict.get("risk_aversion", 1.0))
        if views_q is None:
            views_p, views_q = build_auto_views(factors, pi)
        bl_cfg = BlackLittermanConfig(**bl_dict) if bl_dict else None
        bl_res = black_litterman_weights(cov, prior_w, views_q, views_p=views_p, config=bl_cfg)
        weights = bl_res.weights

        signals = []
        for i, f in enumerate(factors):
            wi = float(weights[i])
            signals.append(
                PortfolioSignal(
                    factor_id=f["factor_id"],
                    name=f["name"],
                    weight=wi,
                    sharpe=f.get("sharpe", 0.0),
                    ic=f.get("ic", 0.0),
                    turnover=f.get("turnover", 0.0),
                    decay_6m=f.get("decay_6m", 0.0),
                    orthogonalized=False,
                    retained=wi > 0.0,
                )
            )
        logger.info(
            "[L3] Black-Litterman 完成: %d/%d 因子非零权重（views=%d）",
            sum(1 for s in signals if s["retained"]),
            n,
            len(bl_res.view_q),
        )
        return signals
    except Exception as e:  # noqa: BLE001
        logger.warning("[L3] Black-Litterman 融合失败 (%s)", e)
        return None


def _build_factor_code_map(
    factors: list[dict[str, Any]],
    elite_dir: Path,
    market: str = "futures",
) -> dict[str, dict[str, Any]]:
    """构建 factor_id → 因子记录(含 code) 映射（SSOT 对齐）。

    优先级：内存 factors 自带 code（load_elite_factors 已从 DuckDB 读回，
    v2.73.0+ 主流程）→ DuckDB FactorRepository 按 factor_id 补拉 → JSON
    快照兜底（旧格式只读快照）。

    背景（v2.103.0 修复）：此前仅从 elite_dir/*.json 读因子代码，而
    futures_elite JSON 目录在存储迁移 DuckDB 后已退役（仅剩最近注入的
    少量快照），导致聚类后的因子大多匹配不到代码 → valid_factors<2 →
    回退 sharpe_weight。本函数绕开对 JSON 目录的全量依赖，重启后仍可
    从 DuckDB 读到 code。
    """
    factor_codes: dict[str, dict[str, Any]] = {}
    for f in factors:
        fid = f.get("factor_id")
        if fid and f.get("code"):
            factor_codes[fid] = f

    # DuckDB 补拉（内存缺 code 的因子）
    missing = [f["factor_id"] for f in factors if f["factor_id"] not in factor_codes]
    if missing:
        try:
            from .factor_db import FactorRepository

            repo = FactorRepository(market=market)
            try:
                for fid in missing:
                    rec = repo.get_factor(fid)
                    if rec and rec.get("code"):
                        factor_codes[fid] = rec
            finally:
                repo.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[L3] DuckDB 因子代码补拉失败 (%s)，回退 JSON 快照", e)

    # JSON 快照兜底（只读兼容，旧格式冻结期后退役）
    for fp in sorted(elite_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            fid = data.get("factor_id", "")
            if fid and data.get("code"):
                factor_codes.setdefault(fid, data)
        except Exception:
            continue
    return factor_codes


def _auto_build_factor_returns(
    panel: dict[str, Any],
    factors: list[dict[str, Any]],
    elite_dir: Path,
    market: str = "futures",
    horizon: int = 5,
    min_dates: int = 20,
) -> Optional[pd.DataFrame]:
    """自动构建因子收益矩阵（方案①：L3 实测化输入自动回退）。

    从市场面板 + 因子代码构建横截面信号矩阵与前向收益，经 FactorReturnsBuilder
    生成 T×N 因子收益矩阵，供 build_combo 走 measured 实测口径（w×R）。
    未显式传 --returns-matrix 时，调度场景自动获得实测化指标；任何失败/数据
    不足返回 None，由 build_combo 回退估算口径（不阻断主流程）。

    Args:
        panel: 市场面板 {symbol: DataFrame(OHLCV)}
        factors: 因子列表（含 factor_id）
        elite_dir: 精英因子目录（因子代码 SSOT）
        market: 目标市场（决定因子代码分库）
        horizon: 前向收益持有期（默认 5 日）
        min_dates: 最小共同交易日（不足回退）

    Returns:
        因子收益矩阵 DataFrame（index=dates, columns=factor_id）；失败/数据不足返回 None。
    """
    try:
        from .factor_program import FactorExecutor
        from .factor_returns import FactorReturnsBuilder

        if not panel or not factors:
            return None
        common_dates = sorted(set.intersection(*[set(df.index) for df in panel.values()]))
        if len(common_dates) < min_dates:
            logger.warning(
                "[L3-FR] 自动构建因子收益: 共同交易日不足 (%d < %d)，回退估算",
                len(common_dates),
                min_dates,
            )
            return None
        stocks = sorted(panel.keys())
        factor_codes = _build_factor_code_map(factors, elite_dir, market=market)
        valid_factors = [f for f in factors if f.get("factor_id") in factor_codes]
        if len(valid_factors) < 2:
            logger.warning("[L3-FR] 自动构建因子收益: 有效因子不足 (<2)，回退估算")
            return None

        n_dates, n_stocks, n_factors = len(common_dates), len(stocks), len(valid_factors)
        signal_matrix = np.full((n_dates, n_stocks, n_factors), np.nan)
        date_idx = {d: t for t, d in enumerate(common_dates)}
        for j, f in enumerate(valid_factors):
            fdata = factor_codes[f["factor_id"]]
            try:
                executor = FactorExecutor(fdata)
            except Exception:
                continue
            for i, sym in enumerate(stocks):
                df = panel[sym]
                try:
                    sig = executor.execute(df, fdata.get("params", {}))
                    for d, t in date_idx.items():
                        if d in df.index:
                            idx = list(df.index).index(d)
                            signal_matrix[t, i, j] = float(sig[idx]) if idx < len(sig) else np.nan
                except Exception:
                    continue

        forward_returns = np.full((n_dates, n_stocks), np.nan)
        for i, sym in enumerate(stocks):
            closes = panel[sym]["close"].to_numpy(dtype=float)
            fwd = np.full(len(closes), np.nan)
            fwd[:-horizon] = (closes[horizon:] - closes[:-horizon]) / np.maximum(closes[:-horizon], 1e-10)
            for d, t in date_idx.items():
                if d in panel[sym].index:
                    idx = list(panel[sym].index).index(d)
                    forward_returns[t, i] = fwd[idx] if idx < len(fwd) else np.nan

        builder = FactorReturnsBuilder()
        fr = builder.build_from_panel(
            signal_matrix=signal_matrix,
            forward_returns=forward_returns,
            dates=list(common_dates),
            factor_ids=[f["factor_id"] for f in valid_factors],
        )
        logger.info(
            "[L3-FR] 自动构建因子收益矩阵完成: %d 因子 × %d 交易日（measured 口径）",
            len(valid_factors),
            len(common_dates),
        )
        return fr.returns
    except Exception as e:  # noqa: BLE001
        logger.warning("[L3-FR] 自动构建因子收益矩阵失败，回退估算口径: %s", e)
        return None


def _compute_elastic_net_weights(
    factors: list[dict[str, Any]],
    elite_dir: Path,
    days: int = 500,
    max_stocks: int = 0,
    l1_ratio: float = 0.5,
    cv_folds: int = 5,
    config: Optional["WeightLearningConfig"] = None,
    market: str = "futures",
) -> dict[str, float]:
    """Elastic Net 截面回归确定因子权重（v2.74.0 机构级增强）。

    步骤:
        1. 加载期货核心面板（股票面板已剥离）
        2. 对每个因子，逐标的执行因子代码获取信号序列
        3. 逐日截面回归: 因子信号[t] → 5 日前向收益
        4. 平均各日回归系数绝对值 → 归一化 → 权重
        5. 风险调整: Ledoit-Wolf 收缩协方差 → 波动率缩放 / 风险平价
        6. 滚动样本外验证: 权重稳定性 / OOS IC / 权重衰减
        7. 跨市场迁移 IC 对比验证（学习面板 vs 对侧市场面板）

    Args:
        factors: 因子列表
        elite_dir: 精英因子目录（含因子代码 JSON）
        days: 回溯天数（默认 500，对齐 MIN_EVAL_DAYS）
        max_stocks: 最大标的数（0 = 全量；股票剥离后恒 0）
        l1_ratio: ElasticNet L1 比例（0=Ridge, 1=Lasso）
        cv_folds: 交叉验证折数
        config: 机构级权重学习配置（None 用默认，panel_market="auto" 跟随 market）
        market: 目标交易市场（决定学习面板，默认 futures）

    Returns:
        {factor_id: weight} 映射（权重和为 1.0）
    """
    if importlib.util.find_spec("sklearn") is None:
        logger.warning("[L3] scikit-learn 未安装，无法使用 Elastic Net")
        return {}

    import numpy as np
    from ..data import FTSDataProvider
    from .factor_program import FactorExecutor
    from .weight_learning import (
        WeightLearningConfig,
        _fit_elasticnet_coefs,
        cross_market_ic_check,
        resolve_panel_market,
        risk_adjust_from_panel,
        rolling_oos_validate,
    )

    wl_config = config if config is not None else WeightLearningConfig()
    panel_market = resolve_panel_market(wl_config.panel_market, market)

    # ── 1. 加载学习面板（期货核心面板；股票面板已剥离）──
    provider = FTSDataProvider()
    if panel_market == "futures":
        from ..data_futures import get_dynamic_core_subset

        panel, common_dates = provider.get_futures_panel(symbols=get_dynamic_core_subset(), days=days, trace_id="")
        logger.info(
            "[L3] 权重学习面板 [futures]: %d 品种 × %d 交易日（自动匹配目标市场）",
            len(panel),
            len(common_dates),
        )
    else:
        logger.warning("[L3] 权重学习面板 market=%s 不可用（股票面板已剥离），回退", panel_market)
        return {}
    if not panel or len(common_dates) < 20:
        logger.warning("[L3] 面板数据不足（需 ≥%d 个交易日），回退", 20)
        return {}

    n_dates = len(common_dates)
    stocks = sorted(panel.keys())
    n_stocks = len(stocks)
    logger.info("[L3] Elastic Net 数据: %d 只标的 × %d 个交易日", n_stocks, n_dates)

    # ── 2. 加载因子代码（SSOT 对齐：内存 code → DuckDB 补拉 → JSON 快照兜底）──
    factor_codes = _build_factor_code_map(factors, elite_dir, market=market)

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
    mean_coefs = _fit_elasticnet_coefs(signal_matrix, forward_returns, l1_ratio=l1_ratio, cv_folds=cv_folds)
    if mean_coefs is None:
        logger.warning("[L3] 有效回归日不足（<5），回退")
        return {}

    logger.info("[L3] Elastic Net 完成: %d 个有效截面回归日", n_dates)

    # ── 6. 平均系数 → 取绝对值 → 归一化为权重 ──
    mean_coefs = np.nan_to_num(mean_coefs, nan=0.0)

    abs_coefs = np.abs(mean_coefs)
    total = abs_coefs.sum()
    if total <= 0:
        return {}

    weights = abs_coefs / total
    result = {valid_factors[j]["factor_id"]: float(weights[j]) for j in range(n_factors)}

    # ── 6.5 风险调整权重（v2.74.0 机构级增强 ①）──
    if wl_config.risk_adjust != "none":
        adjusted, risk_meta = risk_adjust_from_panel(
            result,
            signal_matrix,
            forward_returns,
            list(common_dates),
            [f["factor_id"] for f in valid_factors],
            wl_config.risk_adjust,
        )
        if adjusted and risk_meta:
            result = adjusted
            logger.info("[L3-WEIGHT] 风险调整后权重分布: %s", {k: round(v, 4) for k, v in adjusted.items()})

    # ── 6.6 滚动样本外验证（v2.74.0 机构级增强 ②）──
    if wl_config.rolling_validation:
        rolling_oos_validate(
            signal_matrix,
            forward_returns,
            [f["factor_id"] for f in valid_factors],
            wl_config,
            l1_ratio=l1_ratio,
            cv_folds=cv_folds,
        )

    # ── 6.7 跨市场迁移 IC 对比验证（v2.74.0 机构级增强 ③）──
    if wl_config.cross_market_ic:
        cross_market_ic_check(
            provider,
            factor_codes,
            [f["factor_id"] for f in valid_factors],
            signal_matrix,
            forward_returns,
            list(common_dates),
            panel_market,
        )

    n_nonzero = sum(1 for w in result.values() if w > 0.001)
    logger.info("[L3] Elastic Net 权重: %d 个因子获非零权重（共 %d 个）", n_nonzero, len(result))
    return result


def _compute_ml_ensemble_weights(
    factors: list[dict[str, Any]],
    elite_dir: Path,
    days: int = 500,
    max_stocks: int = 0,
    model_kind: str = "lightgbm",
    market: str = "futures",
) -> dict[str, float]:
    """ML 集成融合确定因子权重（Phase 24，v2.38.0）。

    步骤:
        1. 加载期货核心面板（股票面板已剥离）
        2. 对每个因子，逐标的执行因子代码获取信号序列
        3. 训练横截面回归模型: 因子信号矩阵 → 5 日前向收益
        4. 特征重要性归一化 → 权重（ML 自动变量选择）

    Args:
        factors: 因子列表
        elite_dir: 精英因子目录（含因子代码 JSON 快照，仅兜底）
        days: 回溯天数（默认 500，对齐 MIN_EVAL_DAYS）
        max_stocks: 最大标的数（0 = 全量；股票剥离后恒 0）
        model_kind: 模型类型（lightgbm / xgboost / ensemble）
        market: 目标交易市场（DuckDB 补拉因子代码用，默认 futures）

    Returns:
        {factor_id: weight} 映射（权重和为 1.0）
    """
    from ..ml import SignalModelTrainer, TrainMode

    try:
        from sklearn.linear_model import ElasticNetCV  # noqa: F401 - 探活面板可用
    except ImportError:
        logger.warning("[L3] scikit-learn 未安装，无法使用 ML Ensemble")
        return {}

    import numpy as np
    from ..data import FTSDataProvider
    from .factor_program import FactorExecutor

    # ── 1. 加载期货核心面板数据 ──
    provider = FTSDataProvider()
    from ..data_futures import get_dynamic_core_subset

    panel, common_dates = provider.get_futures_panel(symbols=get_dynamic_core_subset(), days=days, trace_id="")
    if not panel or len(common_dates) < 20:
        logger.warning("[L3] ML Ensemble 面板数据不足（需 ≥%d 个交易日），回退", 20)
        return {}

    n_dates = len(common_dates)
    stocks = sorted(panel.keys())
    n_stocks = len(stocks)
    logger.info("[L3] ML Ensemble 数据: %d 只标的 × %d 个交易日", n_stocks, n_dates)

    # ── 2. 加载因子代码（SSOT 对齐：内存 code → DuckDB 补拉 → JSON 快照兜底）──
    factor_codes = _build_factor_code_map(factors, elite_dir, market=market)

    valid_factors = [f for f in factors if f["factor_id"] in factor_codes]
    if len(valid_factors) < 2:
        logger.warning("[L3] ML Ensemble 有效因子不足（需 ≥2），回退")
        return {}

    n_factors = len(valid_factors)

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

    # ── 5. 展平为样本矩阵训练 ML 模型 ──
    X_flat = signal_matrix.reshape(-1, n_factors)
    y_flat = forward_returns.reshape(-1)
    valid = ~np.isnan(X_flat).any(axis=1) & ~np.isnan(y_flat)
    if valid.sum() < 30:
        logger.warning("[L3] ML Ensemble 有效样本不足（%d < 30），回退", int(valid.sum()))
        return {}

    feature_names = [f["factor_id"] for f in valid_factors]
    trainer = SignalModelTrainer(kind=model_kind, mode=TrainMode.CROSS_SECTIONAL)
    result = trainer.train(X_flat[valid], y_flat[valid], feature_names=feature_names)
    if result.model is None:
        logger.warning("[L3] ML Ensemble 训练降级: %s", result.message)
        return {}

    # ── 6. 特征重要性归一化 → 权重 ──
    importance = result.feature_importance
    if not importance:
        logger.warning("[L3] ML Ensemble 无特征重要性，回退")
        return {}

    abs_imp = {k: abs(v) for k, v in importance.items()}
    total = sum(abs_imp.values())
    if total <= 0:
        return {}

    weights = {k: v / total for k, v in abs_imp.items()}
    n_nonzero = sum(1 for w in weights.values() if w > 0.001)
    logger.info(
        "[L3] ML Ensemble(%s) 权重: %d 个因子获非零权重（共 %d 个），R²=%.4f",
        model_kind,
        n_nonzero,
        len(weights),
        result.score,
    )
    return weights


def orthogonalize_factors(
    signals: list[PortfolioSignal],
    correlation_matrix: list[FactorCorrelation] | None = None,
    max_corr_threshold: float = 0.7,
    factors: list[dict[str, Any]] | None = None,
    use_tiered: bool = False,
    l2_prior_correlations: list[dict[str, Any]] | None = None,
    signal_matrix: dict[str, dict[str, np.ndarray]] | None = None,
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
        signal_matrix: 因子信号矩阵（可选，Phase 2 相关性分析需要时传入）
                      结构: {factor_name: {symbol: signal_array}}
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
                        flags.append(
                            {
                                "type": "l2_seed_correlation",
                                "reason": f"L2 种子预检: 与 {fid_b if s.get('factor_id') == fid_a else fid_a} 相关 {max_abs:.3f}",
                            }
                        )

    # ── 模式 0: 分层正交化（使用 FactorOptimizer — 标记模式）──
    if use_tiered and factors is not None and len(factors) >= 30:
        try:
            from .factor_optimizer import FactorOptimizer

            logger.info("[L3] 触发分层正交化（标记模式）: 因子数=%d, 阈值=%.1f", len(factors), max_corr_threshold)
            optimizer = FactorOptimizer()
            result_factors, summary = optimizer.tiered_orthogonalize(
                factors,
                max_corr_threshold=max_corr_threshold,
                mode="remove",
                l2_prior_correlations=l2_prior_correlations,
                signal_matrix=signal_matrix,
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

            logger.info(
                "[L3] Phase 1 标记完成: 标记 %d 个 (代码重复=%d, 家族标记=%d)",
                summary.get("phase1_marked", 0),
                len(phase1_code_dup),
                len(phase1_family),
            )

            if phase1_code_dup:
                dup_msg = "; ".join(f"{d['removed']} (因:{d['reason']})" for d in phase1_code_dup)
                logger.info("[L3] Phase 1-代码重复标记: %s", dup_msg)

            if phase1_family:
                fam_msg = "; ".join(f"{d['removed']} (因:{d['reason']})" for d in phase1_family)
                logger.info("[L3] Phase 1-家族标记: %s", fam_msg)

            # ── Phase 2 详细日志（含 L2 先验合并）──
            phase2_details = summary.get("phase2_details", [])
            l2_prior_count = summary.get("l2_prior_count", 0)
            phase2_new = summary.get("phase2_new_count", 0)
            phase2_overlap = summary.get("phase2_overlap_count", 0)

            logger.info(
                "[L3] Phase 2 相关性标记完成: 标记 %d 个高相关因子 (新增 %d 个, 与 L2 先验重叠 %d 个)",
                summary.get("phase2_marked", 0),
                phase2_new,
                phase2_overlap,
            )

            if l2_prior_count > 0:
                logger.info(
                    "[L3] Phase 2 与 L2 先验合并: L2 先验标记 %d 个, Phase 2 新增 %d 个, 重叠 %d 个",
                    l2_prior_count,
                    phase2_new,
                    phase2_overlap,
                )

            if phase2_details:
                corr_msg = "; ".join(f"{d['removed']} (因:{d['reason']})" for d in phase2_details)
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
                        len(l2_flags),
                        len(phase2_flags),
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
                phase2_new,
                phase2_overlap,
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
                    # 正交化闭环（GAP-I206 补充）：L2 已正交化入库的因子
                    # 与参照因子的相关成分已在 L2 剥离，此处不再重复剔除。
                    if factor_map[neighbor].get("orthogonalized"):
                        continue
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

        removed2: set[str] = set()
        for code_hash, group in hash_to_factors.items():
            if len(group) > 1:
                # 按夏普排序，保留最高的
                group_sorted = sorted(group, key=lambda x: x.get("sharpe", 0), reverse=True)
                for s in group_sorted[1:]:
                    removed2.add(s["factor_id"])
                    logger.info(
                        "[L3] 代码去重: 剔除 %s (与 %s 代码相同)", s.get("name", "?"), group_sorted[0].get("name", "?")
                    )

        for s in signals:
            s["orthogonalized"] = True
            if s["factor_id"] in removed2:
                s["retained"] = False

        if removed2:
            logger.info("[L3] 正交化完成: 基于代码去重剔除 %d 个因子", len(removed2))
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
        logger.info(
            "[L3-WEIGHT] 衰减检验移除 %d 个因子 (decay>%.2f): %s",
            len(removed),
            max_decay_rate,
            "; ".join(f"{n}(decay={d:.4f})" for n, d in removed),
        )
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


def apply_turnover_penalty(
    signals: list[PortfolioSignal],
    prev_weights: dict[str, float],
    turnover_penalty: float = 0.0,
) -> list[PortfolioSignal]:
    """组合目标函数换手惩罚项（GAP-I303，v2.85.0）。

    机构级标准：组合优化目标含换手惩罚项 λ·换手率。本函数在粘性约束之后、
    权重归一化之前执行，将"目标 = 原始目标 − λ·Σ|Δw|"的带惩罚优化近似为
    权重变动收缩：

        w_new' = w_old + (w_new − w_old) / (1 + λ)

    - λ = 0：无惩罚，权重保持原样（默认关闭，向后兼容）
    - λ > 0：变动幅度按 1/(1+λ) 收缩，λ 越大越接近上次组合（换手越低）
    新因子（prev 无权重）不惩罚，保留原权重。

    Args:
        signals: 待构建信号（权重未归一化，就地修改 weight 字段）
        prev_weights: {factor_id: weight} 上次组合权重
        turnover_penalty: 换手惩罚系数 λ（>= 0，0 关闭）

    Returns:
        施加换手惩罚后的信号列表
    """
    if turnover_penalty is None or turnover_penalty <= 0 or not prev_weights:
        return signals
    shrink = 1.0 / (1.0 + turnover_penalty)
    for s in signals:
        prev_w = prev_weights.get(s.get("factor_id"))
        if prev_w is not None and prev_w > 0:
            w_new = s.get("weight", 0.0)
            s["weight"] = prev_w + (w_new - prev_w) * shrink
    return signals


# ─── Sharpe 虚高验证 ──────────────────────────────────────

SHARPE_WARNING_THRESHOLD: float = 3.5
"""组合夏普警戒线：> 3.5 自动标记并触发独立验证。"""

SHARPE_CAP: float = 2.0
"""因子 Sharpe 上限截断：> 2.0 的因子按 2.0 计算权重，防止过拟合因子主导组合。"""

ACTIVE_FACTOR_CAP: int = 20
"""活跃因子数量上限：Elastic Net 信号合成前，按 Sharpe 排序保留前 N 个因子。
超过此数量的因子自动过滤，防止冗余因子稀释组合夏普。"""

MIN_EVAL_DAYS: int = 500
"""最小评价窗口（交易日数）：面板数据回溯天数，确保评价窗口足够长避免短窗口虚高。"""


def _validate_combo_sharpe(combo_sharpe: float) -> Optional[str]:
    """夏普比率警戒检查。

    行业经验范围:
        - 期货 CTA（中低频）: 1.0-2.5
        - 统计套利（中频）: 2-4
        - 高频做市: 4-8

    Args:
        combo_sharpe: 组合夏普比率

    Returns:
        None（正常）或 str（警戒原因）
    """
    if combo_sharpe > SHARPE_WARNING_THRESHOLD:
        return (
            f"Sharpe={combo_sharpe:.2f} > {SHARPE_WARNING_THRESHOLD}, "
            f"超出行业合理范围（期货 CTA 1.0-2.5），强烈暗示过拟合或数据泄露"
        )
    if combo_sharpe > 2.5:
        return f"Sharpe={combo_sharpe:.2f} > 2.5, 偏高，建议检查因子独立性"
    return None


def _run_sharpe_randomization_test(
    signals: list[PortfolioSignal],
    n_shuffle: int = 1000,
) -> bool:
    """夏普随机化测试：基于 Dirichlet 权重重采样，验证高夏普是否来自真实预测能力。

    原理：
        1. 计算组合的实际加权夏普（actual_weighted_sharpe）
        2. 从 Dirichlet(1,1,...,1) 生成 1000 组随机权重向量
        3. 对每组随机权重计算加权夏普，得到随机分布
        4. 如果实际夏普 > 随机分布的 95% 分位数，说明夏普显著优于随机权重配置
        5. 否则说明夏普可能来自权重集中而非真实预测能力

    Args:
        signals: 信号列表（含 sharpe/weight）
        n_shuffle: 随机化次数（默认 1000）

    Returns:
        True（随机化测试通过，高夏普可信）或
        False（随机化测试未通过，夏普可能虚高）
    """
    import numpy as np

    retained = [s for s in signals if s.get("retained", True)]
    if len(retained) < 3:
        return True  # 因子太少，跳过随机化测试

    # 实际组合加权夏普
    total_w = sum(s.get("weight", 0) for s in retained)
    if total_w <= 0:
        return True
    actual_sharpe = sum(s.get("weight", 0) * s.get("sharpe", 0) for s in retained) / total_w

    # 夏普正常，跳过随机化测试
    if actual_sharpe <= 2.5:
        return True

    # 提取因子 Sharpe 值
    sharpe_values = np.array([s.get("sharpe", 0.0) for s in retained])
    n = len(sharpe_values)

    # 从 Dirichlet 分布生成随机权重（均匀分布的先验）
    random_weights = np.random.dirichlet(np.ones(n), size=n_shuffle)
    random_sharpes = random_weights @ sharpe_values  # 矩阵乘法批量计算

    # 计算随机分布的 95% 分位数
    percentile_95 = float(np.percentile(random_sharpes, 95))

    # 如果实际夏普 > 95% 分位数，说明显著优于随机配置
    passed = actual_sharpe > percentile_95
    return passed


def build_combo(
    signals: list[PortfolioSignal],
    mode: str = "equal_weight",
    trace_id: Optional[str] = None,
    prev_weights: Optional[dict[str, float]] = None,
    sticky_config: Optional[StickyConfig] = None,
    factor_returns: Optional[pd.DataFrame] = None,
    annualize_factor: float = 252.0,
    market: str = "futures",
    cost_config: Optional[dict[str, Any]] = None,
    turnover_penalty: float = 0.0,
    exposure_scale: Optional[float] = None,
    regime_meta: Optional[dict] = None,
    aligned_exposure_config: Optional[Any] = None,
    turnover_budget_config: Optional[Any] = None,
) -> PortfolioCombo:
    """构建组合 — 归一化权重 + 计算组合指标。

    Args:
        signals: 信号列表
        mode: 合成模式
        trace_id: 追踪 ID
        prev_weights: 上次组合权重（粘性约束输入，可选）
        sticky_config: 粘性约束配置（可选，默认关闭）
        factor_returns: 因子收益矩阵（T×N，GAP-L301 实测化输入，可选）。
            提供且可对齐时，组合夏普/相关性由 w×R 实测；否则回退 diversity-adjusted 估算。
        annualize_factor: 年化因子（夏普年化，默认 252）
        market: 市场类型（"futures"，GAP-L305 net 指标成本参数）
        cost_config: 成本配置（CostConfig 字段 dict，GAP-L305；None=不启用成本模型，
            net_combo_sharpe 为 None）
        turnover_penalty: 换手惩罚系数 λ（GAP-I303，0=关闭；粘性约束后收缩权重变动）
        exposure_scale: 置信度仓位缩放因子（28-T6，None=未启用；归一化后统一缩放总仓位）
        regime_meta: regime 元信息 {regime, confidence, exposure_scale, entropy_norm}（28-T6）
        aligned_exposure_config: 同向敞口惩罚配置（G1，35-gap-closure-plan；
            AlignedExposureConfig，None=关闭，向后兼容；启用时与 exposure_scale 乘性合并）
        turnover_budget_config: 换手预算分配配置（G3，35-gap-closure-plan；
            TurnoverBudgetConfig，None=关闭，向后兼容；归一化后剔除边际收益最低弱信号）

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
            synthesis_mode=cast(Literal["equal_weight", "sharpe_weight", "lightgbm"], mode),
            signals=signals,
            combo_sharpe=0.0,
            signal_sharpe=None,
            net_combo_sharpe=None,
            combo_turnover=0.0,
            max_correlation=0.0,
            n_factors=0,
            status="pending",
            created_at=datetime.now().isoformat(),
            metrics_source="estimated",
            qc_standards={},
            exposure_scale=None,
            regime_meta=None,
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

    # 换手惩罚（GAP-I303，v2.85.0）：组合目标函数显式换手惩罚项 λ，粘性约束后收缩权重变动
    if turnover_penalty and turnover_penalty > 0 and prev_weights:
        apply_turnover_penalty(retained, prev_weights, turnover_penalty)
        logger.info(
            "[L3] Step 5: 换手惩罚已应用 (λ=%.2f, 权重变动按 1/(1+λ) 收缩)",
            turnover_penalty,
        )

    # 权重归一化
    total_w = sum(s.get("weight", 0) for s in retained)
    if total_w > 0:
        for s in retained:
            s["weight"] = s.get("weight", 0) / total_w

    # G3 换手预算分配（35-gap-closure-plan）：单日组合换手超上限时剔除边际收益最低弱信号。
    # 归一化后执行（target 与 prev_weights 尺度一致）；剔除项回退当前持仓并重归一化。
    if turnover_budget_config is not None and prev_weights:
        from .portfolio_turnover import allocate_turnover_budget

        target_w = {s.get("factor_id", ""): s.get("weight", 0.0) for s in retained if s.get("factor_id")}
        current_w = {k: float(v) for k, v in prev_weights.items()}
        scores = {s.get("factor_id", ""): float(s.get("sharpe", 0.0) or 0.0) for s in retained}
        allocated = allocate_turnover_budget(target_w, current_w, scores, turnover_budget_config)
        for s in retained:
            fid = s.get("factor_id", "")
            if fid in allocated:
                s["weight"] = allocated[fid]

    # G1 同向敞口惩罚（35-gap-closure-plan）：多因子同向共振时压缩组合总敞口。
    # 与置信度仓位缩放（28-T6）乘性合并：exposure_final = exposure_scale × aligned_scale。
    # 缩放前权重快照：供 signal_sharpe（信号质量夏普）计算使用（方案③，与风控后 combo_sharpe 区分）
    pre_scale_weights: list[float] = [float(s.get("weight", 0.0)) for s in retained]
    aligned_scale: float = 1.0
    if aligned_exposure_config is not None:
        from .portfolio_risk_controls import check_aligned_exposure

        _aligned = check_aligned_exposure(retained, aligned_exposure_config)
        aligned_scale = float(_aligned["compress_scale"])
        if _aligned["triggered"]:
            logger.info(
                "[L3] G1 同向敞口压缩: 看多占比=%.3f 看空占比=%.3f → scale=%.4f",
                float(_aligned["long_ratio"]),
                float(_aligned["short_ratio"]),
                aligned_scale,
            )

    # 置信度仓位缩放（28-T6）：归一化后统一缩放总暴露，随组合落盘可追溯
    if (exposure_scale is not None or aligned_scale < 1.0) and total_w > 0:
        exposure_scale = float(exposure_scale if exposure_scale is not None else 1.0) * aligned_scale
        for s in retained:
            s["weight"] = s["weight"] * exposure_scale
        logger.info("[L3-WEIGHT] 仓位缩放: exposure_scale=%.4f (置信度×同向敞口)", exposure_scale)
        # regime_meta 统一携带 exposure_scale，保证落盘可追溯（不修改调用方传入 dict）
        regime_meta = {**(regime_meta or {}), "exposure_scale": round(exposure_scale, 4)}

    # [WEIGHT-LOG] 归一化后权重分布
    sorted_retained = sorted(retained, key=lambda x: -x["weight"])
    effective_n = 1.0 / sum((s["weight"] ** 2) for s in sorted_retained)
    logger.info(
        "[L3-WEIGHT] 最终权重分布: %d 因子, effective_n=%.2f, HHI=%.4f",
        len(sorted_retained),
        effective_n,
        sum(s["weight"] ** 2 for s in sorted_retained),
    )
    for idx, s in enumerate(sorted_retained):
        logger.info(
            "[L3-WEIGHT]   [%d] %s | weight=%.4f | sharpe=%.2f | ic=%.4f",
            idx + 1,
            s["name"],
            s["weight"],
            s["sharpe"],
            s.get("ic", 0),
        )

    n_ret = len(retained)

    # ── 组合指标（GAP-L301 实测化）──
    # 优先用因子收益矩阵 w×R 实测组合夏普/相关性；矩阵缺失、不可对齐或样本不足时回退估算。
    metrics_source: str = "estimated"
    combo_turnover = sum(s.get("turnover", 0) for s in retained) / n_ret
    _qc_drawdown: dict[str, float] = {}  # GAP-063 实测回撤控制（仅 measured 路径）
    if factor_returns is not None and n_ret > 0:
        retained_ids = [s.get("factor_id") for s in retained if s.get("factor_id")]
        if retained_ids:
            try:
                fr = FactorReturnsBuilder.align_to_factors(factor_returns, retained_ids)
                if len(fr) >= 20:
                    w_arr = np.array([s.get("weight", 0.0) for s in retained], dtype=float)
                    if np.sum(w_arr) > 0:
                        w_arr = w_arr / float(np.sum(w_arr))
                    pf = FactorReturnsBuilder.portfolio_returns(fr, w_arr)
                    combo_sharpe = FactorReturnsBuilder.annualized_sharpe(pf, annualize_factor)
                    max_corr = FactorReturnsBuilder.max_abs_correlation(fr)
                    metrics_source = "measured"
                    # GAP-063 回撤控制：组合最大回撤 vs 成分因子最大回撤均值（净值从 1 起，与 portfolio_risk_controls 口径一致）
                    try:
                        combo_nav = np.concatenate([[1.0], np.cumprod(1.0 + np.asarray(pf, dtype=float))])
                        combo_peak = np.maximum.accumulate(combo_nav)
                        combo_dd = float(np.max((combo_peak - combo_nav) / combo_peak)) if len(combo_nav) else 0.0
                        f_dds: list[float] = []
                        for col in fr.columns:
                            fnav = np.concatenate([[1.0], np.cumprod(1.0 + fr[col].to_numpy(dtype=float))])
                            fpeak = np.maximum.accumulate(fnav)
                            if len(fnav):
                                f_dds.append(float(np.max((fpeak - fnav) / fpeak)))
                        if f_dds:
                            _qc_drawdown = {
                                "combo_max_dd": combo_dd,
                                "mean_factor_max_dd": float(np.mean(f_dds)),
                            }
                    except Exception:  # noqa: BLE001
                        _qc_drawdown = {}
            except Exception as e:
                logger.warning("[L3] 实测指标计算失败，回退估算: %s", e)

    if metrics_source != "measured":
        # 组合指标：diversity-adjusted 加权 Sharpe（P0 过拟合修复）
        # 用权重集中度（HHI）调整组合 Sharpe，集中度越高折扣越大。
        # 等权组合 diversity_factor≈1.0，极度集中组合 diversity_factor→0。
        weighted_sharpe = sum(s.get("weight", 0) * s.get("sharpe", 0) for s in retained)
        hhi = sum(s.get("weight", 0) ** 2 for s in retained)
        effective_n = 1.0 / hhi if hhi > 0 else float(n_ret)
        diversity_factor = min(1.0, (effective_n / n_ret) ** 0.5)
        combo_sharpe = weighted_sharpe * diversity_factor

        # 更准确的相关性估算: 基于因子权重集中度 + Ridge 惩罚后的相关性衰减
        if n_ret > 1:
            total_w = sum(s.get("weight", 0) for s in retained)
            if total_w > 0:
                weighted_concentration = sum((s.get("weight", 0) / total_w) ** 2 for s in retained)
                effective_n = 1.0 / weighted_concentration if weighted_concentration > 0 else float(n_ret)
                diversity = min(1.0, effective_n / n_ret)
                avg_sharpe = sum(s.get("sharpe", 0) for s in retained) / n_ret
                max_corr = min(0.7, (1.0 - diversity) * 0.35 + avg_sharpe * 0.015)
            else:
                max_corr = 0.15
        else:
            max_corr = 0.0

    # ── 信号质量夏普 signal_sharpe（方案③）──
    # 缩放前权重口径：separate 风控约束（regime 降仓 × 同向敞口压缩）对指标的影响。
    # measured 口径下 portfolio_returns 内部重新归一化权重，缩放前后一致，直接沿用 combo_sharpe；
    # estimated 口径下用缩放前权重重算 diversity-adjusted 加权 Sharpe。
    if metrics_source == "measured":
        signal_sharpe: Optional[float] = combo_sharpe
    else:
        pre_w = np.array(pre_scale_weights, dtype=float)
        pre_w_sum = float(np.sum(pre_w))
        if pre_w_sum > 0:
            pre_w = pre_w / pre_w_sum
        else:
            pre_w = np.ones(n_ret) / max(n_ret, 1)
        pre_weighted_sharpe = float(np.sum(pre_w * np.array([s.get("sharpe", 0.0) for s in retained], dtype=float)))
        pre_hhi = float(np.sum(pre_w**2))
        pre_effective_n = 1.0 / pre_hhi if pre_hhi > 0 else float(n_ret)
        pre_diversity_factor = min(1.0, (pre_effective_n / n_ret) ** 0.5)
        signal_sharpe = pre_weighted_sharpe * pre_diversity_factor
    logger.info(
        "[L3] 双指标: signal_sharpe=%.4f (缩放前信号质量) combo_sharpe=%.4f (风控后净暴露, scale=%s)",
        signal_sharpe,
        combo_sharpe,
        round(exposure_scale, 4) if exposure_scale is not None else "1.0",
    )

    # Sharpe 虚高验证（P1 差距修复）
    sharpe_warning = _validate_combo_sharpe(combo_sharpe)
    sharpe_randomization_passed = _run_sharpe_randomization_test(retained)
    if sharpe_warning:
        logger.warning(f"[L3-SHARPE] {sharpe_warning}")
    if not sharpe_randomization_passed:
        logger.warning("[L3-SHARPE] 随机化测试未通过: 打乱因子信号后仍能获得高夏普，夏普可能虚高")

    # net 指标（GAP-L305）：扣除交易成本后的净夏普。
    # 复用 cost_model 的成本换算：cost_penalty = total_cost_bps/10000 × 12 / 0.15。
    net_combo_sharpe: Optional[float] = None
    if cost_config is not None:
        try:
            from .cost_model import TransactionCostModel

            cost_model = TransactionCostModel(config=cost_config)
            cfg = cost_model.get_cost_bps(market)
            slippage = cfg.get("slippage_bps", 0.5)
            commission = cfg.get("commission_bps", 0.3)
            impact = cfg.get("impact_bps_per_pct", 2.0)
            min_cost = cfg.get("min_cost_bps", 0.5)
            raw_cost = combo_turnover * (slippage + commission + impact)
            total_cost_bps = max(raw_cost, min_cost)
            cost_penalty = (total_cost_bps / 10000.0) * 12.0 / 0.15
            net_combo_sharpe = combo_sharpe - cost_penalty
            logger.info(
                "[L3-NET] net_combo_sharpe=%.3f (gross=%.3f, cost=%.1f bps/mo)",
                net_combo_sharpe,
                combo_sharpe,
                total_cost_bps,
            )
        except Exception as e:
            logger.warning("[L3-NET] net 指标计算失败（非致命）: %s", e)

    # ── 组合质检三标准（GAP-063）──
    qc_standards: dict[str, Any] = {}
    sharpe_vals = [float(s.get("sharpe", 0.0) or 0.0) for s in retained]
    weight_vals = [float(s.get("weight", 0.0) or 0.0) for s in retained]
    best_single_sharpe = max(sharpe_vals) if sharpe_vals else 0.0
    tw = sum(weight_vals)
    wavg_sharpe = sum(w * s for w, s in zip(weight_vals, sharpe_vals)) / tw if tw > 0 else 0.0
    if best_single_sharpe > 0:
        qc_standards["synthesis_gain"] = float(combo_sharpe / best_single_sharpe)
        qc_standards["synthesis_passed"] = bool(combo_sharpe > best_single_sharpe)
    if wavg_sharpe > 0:
        qc_standards["diversification_gain"] = float(combo_sharpe / wavg_sharpe)
        qc_standards["diversification_passed"] = bool(combo_sharpe > wavg_sharpe)
    if _qc_drawdown:
        combo_dd = _qc_drawdown["combo_max_dd"]
        mean_fdd = _qc_drawdown["mean_factor_max_dd"]
        qc_standards["drawdown_control_ratio"] = float(combo_dd / mean_fdd) if mean_fdd > 1e-10 else None
        qc_standards["drawdown_control_passed"] = bool(mean_fdd > 1e-10 and combo_dd < mean_fdd)
    if qc_standards:
        logger.info(
            "[L3-QC] 组合质检: synthesis_gain=%s diversification_gain=%s drawdown_ratio=%s",
            qc_standards.get("synthesis_gain"),
            qc_standards.get("diversification_gain"),
            qc_standards.get("drawdown_control_ratio"),
        )

    return PortfolioCombo(
        version=EVOLUTION_VERSION,
        updated_at=datetime.now().isoformat(),
        combo_id=f"cmb_{secrets.token_hex(4)}",
        trace_id=trace_id or generate_trace_id("l3"),
        synthesis_mode=cast(Literal["equal_weight", "sharpe_weight", "lightgbm"], mode),
        signals=signals,
        combo_sharpe=combo_sharpe,
        signal_sharpe=signal_sharpe,
        net_combo_sharpe=net_combo_sharpe,
        combo_turnover=combo_turnover,
        max_correlation=max_corr,
        n_factors=len(retained),
        status="active",
        created_at=datetime.now().isoformat(),
        sharpe_warning=sharpe_warning,
        sharpe_randomization_passed=sharpe_randomization_passed,
        metrics_source=metrics_source,
        qc_standards=qc_standards,
        exposure_scale=round(exposure_scale, 4) if exposure_scale is not None else None,
        regime_meta=regime_meta,
    )


# ─── 组合漂移监控 ─────────────────────────────────────────


class DriftMonitor:
    """L3 组合漂移监控 — 记录成员重合率 + 权重 L1 变化率。

    每次组合构建后对比上次组合（combo_history 归档），
    指标持久化到 memory/portfolio/drift_history/YYYY-MM-DD.json。
    GAP-F13 (v2.67.0): 新增阈值告警 + 可选自动粘性重平衡。
    """

    def __init__(
        self,
        portfolio_dir: str | Path = "memory/portfolio",
        alert_config: Optional[DriftAlertConfig] = None,
    ):
        self.portfolio_dir = Path(portfolio_dir)
        self.drift_history_dir = self.portfolio_dir / DRIFT_HISTORY_DIR
        self.drift_history_dir.mkdir(parents=True, exist_ok=True)
        self.alert_config = alert_config or DEFAULT_DRIFT_ALERT_CONFIG

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
        prev_members: dict[str, str] = {}  # factor_id -> name
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
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "[L3] 漂移记录已写入 %s: 重合率=%.4f L1变化=%.4f (prev=%d→new=%d)",
            fp.name,
            metrics.get("member_overlap_rate", 0),
            metrics.get("weight_l1_change", 0),
            metrics.get("n_prev_members", 0),
            metrics.get("n_new_members", 0),
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

    def check_and_alert(
        self,
        metrics: DriftMetrics,
        trace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """检查漂移指标是否超阈值，触发告警并返回告警详情。

        Args:
            metrics: 本次漂移指标
            trace_id: 追踪 ID

        Returns:
            alert_info: {
                "alerted": bool,       # 是否触发告警
                "overlap_alert": bool, # 成员重合率是否超阈值
                "weight_alert": bool,  # 权重 L1 变化率是否超阈值
                "overlap_rate": float, # 实际重合率
                "weight_l1": float,    # 实际权重 L1 变化率
                "overlap_threshold": float,  # 重合率阈值
                "weight_l1_threshold": float, # 权重变化率阈值
                "trigger_rebalance": bool,    # 是否触发重平衡
            }
        """
        cfg = self.alert_config
        overlap_rate = metrics.get("member_overlap_rate", 1.0)
        weight_l1 = metrics.get("weight_l1_change", 0.0)
        o_th = cfg.get("overlap_threshold", 0.50)
        w_th = cfg.get("weight_l1_threshold", 0.40)

        overlap_alert = overlap_rate < o_th
        weight_alert = weight_l1 > w_th
        triggered = overlap_alert or weight_alert

        if triggered:
            reasons: list[str] = []
            if overlap_alert:
                reasons.append(f"成员重合率 {overlap_rate:.2%} < {o_th:.0%}")
            if weight_alert:
                reasons.append(f"权重变化率 {weight_l1:.2%} > {w_th:.0%}")
            alert_msg = f"[L3] 组合漂移告警 (trace={trace_id or '?'}): {'; '.join(reasons)}"
            logger.warning(alert_msg)
            # 写入 Prometheus 兼容日志（可被监控采集解析）
            logger.info(
                "METRIC drift_alert{overlap=%.2f,weight=%.2f,o_th=%.2f,w_th=%.2f} 1",
                overlap_rate,
                weight_l1,
                o_th,
                w_th,
            )
        else:
            logger.info(
                "[L3] 组合漂移正常: 重合率=%.2f%% (>=%.0f%%), 权重变化率=%.2f%% (<=%.0f%%)",
                overlap_rate * 100,
                o_th * 100,
                weight_l1 * 100,
                w_th * 100,
            )

        return {
            "alerted": triggered,
            "overlap_alert": overlap_alert,
            "weight_alert": weight_alert,
            "overlap_rate": overlap_rate,
            "weight_l1": weight_l1,
            "overlap_threshold": o_th,
            "weight_l1_threshold": w_th,
            "trigger_rebalance": triggered and cfg.get("trigger_rebalance", False),
        }

    @staticmethod
    def generate_rebalance_proposal(
        metrics: DriftMetrics,
        alert_info: dict[str, Any],
    ) -> Optional[AgentOptimizationProposal]:
        """生成重平衡建议（供 Agent 消费）。"""
        if not alert_info.get("trigger_rebalance"):
            return None
        return AgentOptimizationProposal(
            proposal_id=generate_trace_id("rebalance"),
            description=(
                f"组合漂移告警: 重合率={metrics.get('member_overlap_rate', 0):.2%}, "
                f"权重L1变化={metrics.get('weight_l1_change', 0):.2%} — "
                "建议启用粘性约束限制单边更换率≤30%"
            ),
            confidence=0.7,
            source="drift_monitor",
        )


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

    proposals.append(
        AgentOptimizationProposal(
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
        )
    )

    return proposals


# ─── 精英因子读取 ────────────────────────────────────────

# ── 运行时质量门槛（与 DEFAULT_VERIFIER_CONFIG 对齐） ──

_RUNTIME_MIN_IC = DEFAULT_VERIFIER_CONFIG["min_ic"]  # 0.03
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
            source,
            len(failed),
            _RUNTIME_MIN_IC,
            _RUNTIME_MIN_SHARPE,
            "\n  ".join(failed[:10]) + ("\n  ..." if len(failed) > 10 else ""),
        )

    logger.info(
        "[L3] 质量门槛通过 [%s]: %d/%d 因子 [min_ic=%.2f, min_sharpe=%.1f]",
        source,
        len(passed),
        len(factors),
        _RUNTIME_MIN_IC,
        _RUNTIME_MIN_SHARPE,
    )
    return passed


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
        except (FactorCompileError, Exception) as exc:
            errors.append(f"{fid}: {type(exc).__name__}: {str(exc)[:80]}")

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
            merges.append(f"{base}: [{', '.join(all_names)}] → {best.get('name')} (IC={best.get('ic', 0):.4f})")
        else:
            # 相关性模式：先计算相关性，再贪心选择
            corr = _compute_signal_correlations(group, panel_data)

            if not corr:
                # 无法计算相关性，回退到 IC-only
                best = max(group, key=lambda x: abs(x.get("ic", 0)))
                result.append(best)
                all_names = [f.get("name", "?") for f in group]
                merges.append(
                    f"{base}: [{', '.join(all_names)}] → {best.get('name')} (IC={best.get('ic', 0):.4f}, 无代码回退)"
                )
                continue

            selected = _greedy_select_by_correlation(group, corr, corr_threshold)
            result.extend(selected)

            sel_names = [f.get("name", "?") for f in selected]
            removed_factors = [f for f in group if f not in selected]
            rem_info = []
            for f in removed_factors:
                rem_info.append(f"{f.get('name')}(IC={f.get('ic', 0):.4f}, reason={f.get('_removed_reason', '?')})")

            if len(sel_names) < len(group):
                selections.append(f"{base}: 保留 [{', '.join(sel_names)}] 合并 [{', '.join(rem_info)}]")
            else:
                selections.append(f"{base}: 全部保留 [{', '.join(sel_names)}] (互相关<{corr_threshold})")

    mode = "相关性模式" if use_corr else "IC-only 模式"
    removed_count = len(factors) - len(result)
    logger.info(
        "[L3] 基础因子名去重 [%s] (%s, threshold=%.2f): %d → %d 因子 (移除 %d 个冗余世代)",
        source,
        mode,
        corr_threshold,
        len(factors),
        len(result),
        removed_count,
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
            source,
            len(pending),
            ", ".join(names[:10]) + ("..." if len(names) > 10 else ""),
        )
    return [f for f in factors if not _is_shadow_pending(f)]


# ─── 纯外推验证 ──────────────────────────────────────────


def _validate_oos_extrapolation(
    factor: dict[str, Any],
    data_panel: dict[str, pd.DataFrame],
    combo_updated_at: str,
    decay_threshold: float = 0.20,
) -> dict[str, Any]:
    """因子纯外推验证：检查因子在新数据上的 IC 衰减。

    因子晋升 elite 后，每次 L3 运行检查其在新数据上的表现。
    如果连续 3 次 L3 运行 IC 衰减 > 20%，标记为待降级。

    Args:
        factor: 因子数据（含 promoted_at, evaluation, code）
        data_panel: {symbol: DataFrame} 市场数据面板
        combo_updated_at: 当前组合构建时间
        decay_threshold: IC 衰减阈值（默认 20%）

    Returns:
        更新后的 factor（含 oos_extrapolation 字段）
    """
    from datetime import datetime as _dt

    promoted_at = factor.get("promoted_at")
    if not promoted_at:
        return factor  # 旧因子无 promoted_at，跳过验证

    promoted_dt = _dt.fromisoformat(promoted_at)
    combo_dt = _dt.fromisoformat(combo_updated_at)

    # 晋升后至少 5 个交易日才做外推验证
    min_trading_days = 5
    if (combo_dt - promoted_dt).days < min_trading_days:
        return factor

    # 从 factor 中获取原始 IC
    evaluation = factor.get("evaluation", {})
    level_1 = evaluation.get("level_1_backtest", {}) if isinstance(evaluation, dict) else {}
    original_ic = level_1.get("ic", 0.0)
    if abs(original_ic) < 0.01:
        return factor  # IC 太弱，跳过

    # 尝试在新数据上计算 IC
    factor_code = factor.get("code", "")
    if not factor_code:
        return factor

    try:
        from .factor_program import FactorExecutor

        executor = FactorExecutor(factor)

        # 收集所有新数据（晋升后的数据）
        new_data_list = []
        for symbol, df in data_panel.items():
            if isinstance(df.index, pd.DatetimeIndex):
                new_df = df[df.index >= promoted_dt]
            elif "date" in df.columns:
                df["_date"] = pd.to_datetime(df["date"])
                new_df = df[df["_date"] >= promoted_dt]
            else:
                continue
            if not new_df.empty:
                new_data_list.append(new_df)

        if len(new_data_list) < 3:
            return factor  # 数据不足

        # 计算因子信号
        combined = pd.concat(new_data_list)
        signal = executor.execute(combined, factor.get("params", {}))

        if isinstance(signal, pd.Series) and len(signal) > 10:
            # 使用 close 收益率作为代理
            if "close" in combined.columns:
                returns = combined["close"].pct_change().shift(-1).values[: len(signal)]
            elif "close_" in combined.columns:
                returns = combined["close_"].pct_change().shift(-1).values[: len(signal)]
            else:
                return factor

            from scipy import stats as _sp_stats

            valid = ~(np.isnan(signal.values[: len(returns)]) | np.isnan(returns))
            if valid.sum() > 10:
                new_ic, _ = _sp_stats.spearmanr(
                    signal.values[: len(returns)][valid],
                    returns[valid],
                )
                if not np.isnan(new_ic):
                    # 计算 IC 衰减率
                    ic_decay = 1.0 - abs(new_ic) / abs(original_ic) if abs(original_ic) > 0.01 else 0.0
                    ic_decay = max(0.0, min(1.0, ic_decay))

                    # 记录衰减次数
                    oos_history = factor.get("_oos_history", [])
                    oos_history.append(
                        {
                            "checked_at": combo_updated_at,
                            "new_ic": float(new_ic),
                            "original_ic": float(original_ic),
                            "ic_decay": float(ic_decay),
                        }
                    )
                    # 最多保留最近 10 次记录
                    oos_history = oos_history[-10:]

                    # 统计最近 3 次连续衰减 > 阈值的次数
                    recent = [h for h in oos_history[-3:]]
                    consecutive_decay = sum(1 for h in recent if h["ic_decay"] > decay_threshold)

                    needs_demotion = consecutive_decay >= 3 and len(recent) >= 3

                    factor["_oos_history"] = oos_history
                    factor["oos_extrapolation"] = {
                        "new_ic": float(new_ic),
                        "original_ic": float(original_ic),
                        "ic_decay": float(ic_decay),
                        "consecutive_decay_count": consecutive_decay,
                        "needs_demotion": needs_demotion,
                        "checked_at": combo_updated_at,
                    }

                    if needs_demotion:
                        logger.warning(
                            "[L3-OOS] 因子 %s 连续 %d 次 IC 衰减 > %.0f%%, 建议降级",
                            factor.get("name", "?"),
                            consecutive_decay,
                            decay_threshold * 100,
                        )
    except Exception as e:
        logger.debug("[L3-OOS] 外推验证失败: %s", e)

    return factor


def load_elite_factors(
    elite_dir: str | Path,
    use_duckdb: bool = True,
    market: str = "futures",
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
        market: 市场类型过滤（futures 等）
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

            repo = FactorRepository(market=market)
            try:
                total_count = repo._execute(
                    "SELECT count(*) FROM factor_catalog WHERE market=? AND status='active' AND is_elite=true", [market]
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
                        # style_tags: DuckDB 字段优先，缺省回退名称推断
                        style_tags = f.get("style_tags") or []
                        if not style_tags:
                            style_tags = [_infer_factor_style_from_name(f.get("name", ""))]
                        factors.append(
                            {
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
                                "style_tags": style_tags,
                                "family": f.get("family") or "other",
                                # 正交化闭环（GAP-I206 补充，v2.71.0/v2.72.0 基底）
                                "orthogonalized": metadata.get("orthogonalized", False),
                                "orthogonalized_against": metadata.get("orthogonalized_against", ""),
                                "orthogonalized_pearson": metadata.get("orthogonalized_pearson", 0.0),
                                "orthogonalized_basis": metadata.get("orthogonalized_basis", []),
                                "orthogonal_signal": metadata.get("orthogonal_signal", []),
                            }
                        )
                    logger.info("[L3] ✅ 从 DuckDB 加载 %d 个 elite 因子 [market=%s]", len(factors), market)
                    passed = _filter_by_quality_gate(factors, "DuckDB")
                    passed = _filter_shadow_pending(passed, "DuckDB")
                    try:
                        result = _deduplicate_by_base_name(
                            passed, "DuckDB", panel_data=panel_data, corr_threshold=corr_threshold
                        )
                        return result
                    except Exception as dedup_err:
                        logger.warning("[L3] DuckDB 相关性去重失败: %s，回退到 IC-only", dedup_err)
                        import traceback

                        logger.exception("[L3] 去重异常堆栈:")
                        # 回退: 不使用 panel_data
                        return _deduplicate_by_base_name(
                            passed, "DuckDB", panel_data=None, corr_threshold=corr_threshold
                        )
                else:
                    logger.warning("[L3] ⚠️ DuckDB 查询返回 0 行 [market=%s]，回退到 JSON 加载", market)
                    # 额外诊断：检查该市场的所有因子（不限 is_elite）
                    all_count = repo._execute(
                        "SELECT count(*) FROM factor_catalog WHERE market=?", [market]
                    ).fetchone()[0]
                    logger.info("[L3] 诊断: market=%s 全部因子数=%d", market, all_count)
                    if all_count > 0:
                        sample = repo._execute(
                            "SELECT factor_id, name, market, is_elite, status FROM factor_catalog WHERE market=? LIMIT 3",
                            [market],
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
    factors = []
    if not elite_path.exists():
        logger.warning("[L3] JSON 兜底路径不存在: %s", elite_path)
        return factors

    json_files = sorted(elite_path.glob("*.json"))
    valid_files = [f for f in json_files if not f.name.startswith("_")]
    logger.info(
        "[L3] JSON 兜底: 扫描 %d 个文件 (有效=%d) [路径=%s, market=%s]",
        len(json_files),
        len(valid_files),
        elite_path,
        market,
    )

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
            factor_market = data.get("market", "futures")
            if factor_market != market:
                skipped_market += 1
                continue

            # style_tags: JSON 字段优先，缺省回退名称推断
            style_tags = data.get("style_tags") or []
            if not style_tags:
                style_tags = [_infer_factor_style_from_name(data.get("name", fp.stem))]

            factors.append(
                {
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
                    "style_tags": style_tags,
                    "family": data.get("family") or "other",
                }
            )
        except (json.JSONDecodeError, TypeError) as e:
            parse_errors += 1
            logger.debug("[L3] JSON 解析错误: %s - %s", fp.name, e)
            continue
    logger.info(
        "[L3] 从 JSON 文件加载 %d 个 elite 因子 [market=%s] (跳过市场不匹配=%d, 解析错误=%d)",
        len(factors),
        market,
        skipped_market,
        parse_errors,
    )
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
        logger.info(
            "[L3] 加载 L2 相关性索引: %d 对高相关因子 (source=%s, created_at=%s)",
            len(correlations),
            data.get("source", "?"),
            data.get("created_at", "?"),
        )
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
        json.dumps(combo, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["combo"] = str(combo_fp.resolve())

    # 写入权重配置（可直接被 multi_factor_strategy.py 加载的 JSON）
    weights = {}
    for s in combo.get("signals", []):
        if s.get("retained", True):
            weights[s["name"]] = s["weight"]
    weights_fp = out / "factor_weights.json"
    weights_fp.write_text(
        json.dumps(
            {
                "version": EVOLUTION_VERSION,
                "updated_at": combo.get("updated_at", datetime.now().isoformat()),
                "synthesis_mode": combo.get("synthesis_mode", "equal_weight"),
                "weights": weights,
                "combo_sharpe": combo.get("combo_sharpe", 0),
                "n_factors": combo.get("n_factors", 0),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    paths["weights"] = str(weights_fp.resolve())

    # 写入 Agent 建议
    props_dir = out / PROPOSALS_DIR
    props_dir.mkdir(parents=True, exist_ok=True)
    for p in proposals:
        pp = props_dir / f"{p['proposal_id']}.json"
        pp.write_text(
            json.dumps(p, ensure_ascii=False, indent=2),
            encoding="utf-8",
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
    signal_sharpe: Optional[float]
    max_correlation: float
    n_proposals: int
    status: str
    error: Optional[str]
    output_paths: dict[str, str]


class PortfolioLoop:
    """L3 Portfolio Loop 主循环。

    流程:
        Step 1: 加载 elite 因子
        Step 1.7: 活跃因子数量上限（ACTIVE_FACTOR_CAP=20）
        Step 1.8: P1 因子聚类（可选，enable_clustering=True）
        Step 1.9: P2 PCA 降维（可选，enable_pca=True）
        Step 2: 信号合成（默认 elastic_net）
        Step 2.5: Regime 自适应权重调整 (可选)
        Step 3: 因子正交化（elastic_net 模式跳过，L1 已做变量选择）
        Step 4: 衰减检验
        Step 5: 组合构建
        Step 6: Verifier 判定
        Step 7: 注入 FDT

    Regime 自适应:
        当传入 market_ohlcv 时，自动检测市场制度并调整因子权重。
        支持 bull/bear/oscillate/high_vol/low_vol 五种制度的差异化权重。

    P1 因子聚类:
        当 enable_clustering=True 时，在 Step 1.8 对因子进行信号相关性层次聚类，
        从每个簇中选择 Sharpe 最高的代表因子，系统性降低冗余。

    P2 PCA 降维:
        当 enable_pca=True 时，在 Step 1.9 对因子信号矩阵进行 PCA 降维，
        保留解释 95% 方差的主成分，通过载荷矩阵映射回因子权重。
    """

    def __init__(
        self,
        memory_dir: str | Path = "memory/portfolio",
        elite_dir: str | Path = "memory/knowledge/factors/futures_elite",
        verifier_config: Optional[L3VerifierConfig] = None,
        synthesis_mode: str = "equal_weight",
        use_duckdb: bool = True,
        enable_regime_adaptation: bool = True,
        market: str = "futures",
        sticky_config: Optional[StickyConfig] = None,
        enable_clustering: bool = True,
        enable_pca: bool = False,
        adaptive_config: Optional[AdaptiveWeightConfig] = None,
        optimizer_mode: str = "risk_parity",
        optimizer_config: Optional[dict[str, Any]] = None,
        cost_config: Optional[dict[str, Any]] = None,
        weight_config: Optional[Any] = None,
        turnover_penalty: Optional[float] = None,
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
        # 自适应权重配置（A.3 / v2.56.0）：dimension + smoother + clamp
        self.adaptive_config = adaptive_config or DEFAULT_ADAPTIVE_CONFIG
        self.state_manager = PortfolioStateManager(memory_dir)
        self.portfolio_manager = PortfolioManager(memory_dir)
        self.drift_monitor = DriftMonitor(memory_dir)
        self._regime_selector: Optional[Any] = None
        self._regime_smoother: Optional[Any] = None
        # 28-T4: 置信度仓位缩放因子（Step 2.5 计算，T6 在 build_combo 消费）与 regime 元信息
        self._regime_exposure_scale: float = 1.0
        self._regime_meta: Optional[dict[str, Any]] = None
        # P1/P2 控制开关
        self.enable_clustering = enable_clustering
        self.enable_pca = enable_pca
        self._clustering_engine: Optional[Any] = None
        self._pca_compressor: Optional[Any] = None
        # GAP-L303: optimizer 模式与配置（synthesis_mode="optimizer" 时生效）
        self.optimizer_mode = optimizer_mode
        self.optimizer_config = dict(optimizer_config or {})
        # GAP-L305: 交易成本配置（None=不启用 net 指标）
        self.cost_config = dict(cost_config) if cost_config else None
        # v2.74.0: 机构级权重学习配置（elastic_net 风险调整/滚动验证/面板自动匹配）
        self.weight_config = weight_config
        # GAP-I303 (v2.85.0): 组合目标函数换手惩罚项 λ（None 从 FTSConfig 读取，默认 0 关闭）
        if turnover_penalty is not None:
            self.turnover_penalty = float(turnover_penalty)
        else:
            try:
                from fts.config.settings import get_config as _l3_cfg

                self.turnover_penalty = float(getattr(_l3_cfg(), "l3_turnover_penalty", 0.0))
            except Exception:
                self.turnover_penalty = 0.0
        # G3 换手预算分配开关（v2.103.0+17）：默认关闭（FTS_L3_TURNOVER_BUDGET_ENABLED），
        # 关闭后 build_combo 传 None 跳过换手预算裁剪，换手控制由粘性约束 + 换手惩罚 λ 兜底
        try:
            from fts.config.settings import get_config as _l3_cfg

            self.turnover_budget_enabled = bool(getattr(_l3_cfg(), "l3_turnover_budget_enabled", False))
        except Exception:
            self.turnover_budget_enabled = False

    def _generate_quality_report(self) -> None:
        """从 DuckDB 或 combo 回退，生成精英因子最终质量报告 JSON。

        报告保存到 memory/portfolio/elite_final_quality_YYYY-MM-DD.json，
        与初始种子评测 (quality_ranking.json) 区分，记录实际进入组合的因子质量。
        """
        from datetime import datetime as _dt

        factors: list[dict[str, Any]] = []
        source = ""

        # ── 路径 A: 优先从 DuckDB 查询 ──
        try:
            from .factor_db import FactorRepository

            repo = FactorRepository(market=self.market)
            try:
                rows = repo._execute(
                    """
                    SELECT factor_id, name, ic, sharpe, turnover_monthly, decay_6m,
                           market, is_elite, status
                    FROM factor_catalog
                    WHERE status='active' AND is_elite=TRUE AND market=?
                    ORDER BY ic DESC
                """,
                    [self.market],
                ).fetchall()

                if rows:
                    for r in rows:
                        factors.append(
                            {
                                "factor_id": r[0],
                                "name": r[1],
                                "ic": round(r[2], 4),
                                "sharpe": round(r[3], 4),
                                "turnover_monthly": round(r[4], 4),
                                "decay_6m": round(r[5], 4),
                                "market": r[6],
                                "is_elite": r[7],
                                "status": r[8],
                            }
                        )
                    source = "DuckDB"
            finally:
                repo.close()
        except Exception as e:
            import traceback

            logger.warning("[L3] Step 7.5: DuckDB 查询失败 (%s)，尝试 combo 回退", e)
            logger.debug("[L3] Step 7.5: DuckDB 异常详情:\n%s", traceback.format_exc())

        # ── 路径 B: 从 combo 文件回退 ──
        if not factors:
            try:
                combo_file = self.memory_dir / "current_combo.json"
                if combo_file.exists():
                    combo = json.loads(combo_file.read_text(encoding="utf-8"))
                    weights = combo.get("weights", {}) or {}
                    # 从 weights 构建因子列表
                    for name, w in weights.items():
                        factors.append(
                            {
                                "factor_id": name,
                                "name": name,
                                "ic": round(w.get("ic", 0), 4) if isinstance(w, dict) else 0.0,
                                "sharpe": round(w.get("sharpe", 0), 4) if isinstance(w, dict) else 0.0,
                                "turnover_monthly": 0.0,
                                "decay_6m": 0.0,
                                "market": self.market,
                                "is_elite": True,
                                "status": "active",
                            }
                        )
                    source = "combo_fallback"
                    logger.info("[L3] Step 7.5: combo 回退加载 %d 个因子", len(factors))
                else:
                    logger.info("[L3] Step 7.5: combo 文件不存在，跳过质量报告")
                    return
            except Exception as e2:
                logger.warning("[L3] Step 7.5: combo 回退也失败: %s", e2)
                return

        if not factors:
            logger.info("[L3] Step 7.5: 无因子数据，跳过质量报告")
            return

        ics = [f["ic"] for f in factors if f.get("ic") is not None]
        sharpes = [f["sharpe"] for f in factors if f.get("sharpe") is not None]

        state = self.state_manager.load_or_init()
        report = {
            "report_type": "elite_final_quality",
            "generated_at": _dt.now().isoformat(),
            "source": source,
            "trace_id": state.get("current_trace_id"),
            "thresholds": {"min_ic": _RUNTIME_MIN_IC, "min_sharpe": _RUNTIME_MIN_SHARPE},
            "summary": {
                "count": len(factors),
                "ic_range": [round(min(ics), 4), round(max(ics), 4)] if ics else [0, 0],
                "ic_mean": round(sum(ics) / len(ics), 4) if ics else 0,
                "sharpe_range": [round(min(sharpes), 4), round(max(sharpes), 4)] if sharpes else [0, 0],
                "sharpe_mean": round(sum(sharpes) / len(sharpes), 4) if sharpes else 0,
                "below_ic_threshold": sum(1 for ic in ics if abs(ic) < _RUNTIME_MIN_IC) if ics else 0,
                "below_sharpe_threshold": sum(1 for s in sharpes if s < _RUNTIME_MIN_SHARPE) if sharpes else 0,
            },
            "factors": factors,
        }

        ts = _dt.now().strftime("%Y-%m-%d")
        out_file = self.memory_dir / f"elite_final_quality_{ts}.json"
        out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(
            "[L3] Step 7.5: 质量报告已生成 [%s, source=%s]: %d 因子, IC=[%.4f, %.4f], IC<%.2f: %d, Sharpe<%.1f: %d",
            out_file.name,
            source,
            len(factors),
            min(ics),
            max(ics) if ics else 0,
            _RUNTIME_MIN_IC,
            sum(1 for ic in ics if abs(ic) < _RUNTIME_MIN_IC) if ics else 0,
            _RUNTIME_MIN_SHARPE,
            sum(1 for s in sharpes if s < _RUNTIME_MIN_SHARPE) if sharpes else 0,
        )

    def _generate_attribution_report(
        self,
        factor_returns: pd.DataFrame,
        combo: PortfolioCombo,
        trace_id: str,
    ) -> Optional[Path]:
        """生成组合归因报告（GAP-L307，C 阶段）。

        输入组合权重 + 因子收益矩阵 R，输出:
            - 因子贡献度（协方差分解，contributions）
            - 暴露分析（组合对各因子的平均绝对暴露）
            - VaR 95/99 与 ES 95（历史模拟法）
            - 组合实际收益序列（w×R）

        报告写入 `reports/{market}/{date}/portfolio_attribution_{combo_id}.md`。

        Args:
            factor_returns: 因子收益矩阵（T×N，列名=factor_id）
            combo: 当前组合（含 signals 权重）
            trace_id: 追踪 ID

        Returns:
            报告文件路径；失败/不可对齐时 None。
        """
        from .risk_attributor import RiskAttributor

        retained = [s for s in combo.get("signals", []) if s.get("retained", True)]
        retained_ids = [s.get("factor_id") for s in retained if s.get("factor_id")]
        if not retained_ids:
            return None

        fr = FactorReturnsBuilder.align_to_factors(factor_returns, retained_ids)
        if len(fr) < 20:
            logger.info("[L3] Step 7.6: 归因矩阵样本不足 (%d < 20)，跳过归因", len(fr))
            return None

        w_arr = np.array([s.get("weight", 0.0) for s in retained], dtype=float)
        total_w = float(np.sum(w_arr))
        if total_w <= 0:
            return None
        w_arr = w_arr / total_w

        pf = FactorReturnsBuilder.portfolio_returns(fr, w_arr)
        weights_map = dict(zip(retained_ids, w_arr.tolist()))
        report = RiskAttributor().attribute(
            portfolio_returns=pf,
            factor_returns=fr,
            weights=weights_map,
        )

        ts = datetime.now().strftime("%Y-%m-%d")
        combo_id = combo.get("combo_id", "unknown")
        reports_dir = Path("reports") / self.market / ts
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_file = reports_dir / f"portfolio_attribution_{combo_id}.md"

        contrib_lines = (
            "\n".join(
                f"| {fid} | {val:.6f} |"
                for fid, val in sorted(report.factor_contributions.items(), key=lambda kv: -abs(kv[1]))
            )
            or "| (无因子收益) | - |"
        )
        exposure_lines = (
            "\n".join(f"| {fid} | {val:.4f} |" for fid, val in sorted(report.exposures.items(), key=lambda kv: -kv[1]))
            or "| (无暴露数据) | - |"
        )

        md = f"""# 组合归因报告

- **combo_id**: {combo_id}
- **trace_id**: {trace_id}
- **生成时间**: {datetime.now().isoformat()}
- **组合夏普（风控后净暴露）**: {combo.get("combo_sharpe", 0.0):.3f}
- **信号质量夏普（缩放前）**: {combo.get("signal_sharpe") if combo.get("signal_sharpe") is not None else "N/A"}
- **净夏普（扣成本）**: {combo.get("net_combo_sharpe") if combo.get("net_combo_sharpe") is not None else "N/A"}
- **归因矩阵样本**: {len(fr)} 天 × {len(retained_ids)} 因子
- **年化波动率**: {report.realized_vol:.4f}

## 风险指标

| 指标 | 值 |
|:-----|:---|
| VaR 95 | {report.var_95:.6f} |
| VaR 99 | {report.var_99:.6f} |
| ES 95 | {report.es_95:.6f} |
| 年化波动率 | {report.realized_vol:.4f} |

## 因子贡献度（协方差分解）

| 因子 | 贡献度 |
|:-----|:-------|
{contrib_lines}

## 组合暴露（平均绝对）

| 因子 | 平均暴露 |
|:-----|:---------|
{exposure_lines}
"""
        out_file.write_text(md, encoding="utf-8")
        logger.info(
            "[L3] Step 7.6: 归因报告已生成 [%s]: %d 因子, var95=%.4f, vol=%.4f",
            out_file.name,
            len(retained_ids),
            report.var_95,
            report.realized_vol,
        )
        return out_file

    def _generate_walk_forward_report(
        self,
        factor_returns: pd.DataFrame,
        combo: PortfolioCombo,
        trace_id: str,
    ) -> Optional[Path]:
        """生成组合层走航报告（GAP-L306，C 阶段）。

        滚动窗口验证组合权重稳定性：每窗口 train 段确定权重（Sharpe 加权），
        test 段实测组合夏普/IC/相关性，输出跨窗口一致性得分。

        报告写入 `reports/{date}/portfolio_wf_{combo_id}.md`。

        Args:
            factor_returns: 因子收益矩阵（T×N，列名=factor_id）
            combo: 当前组合（含 signals 权重/Sharpe）
            trace_id: 追踪 ID

        Returns:
            报告文件路径；数据不足/失败时 None。
        """
        from .portfolio_walk_forward import PortfolioWalkForward

        retained = [s for s in combo.get("signals", []) if s.get("retained", True)]
        retained_ids = [s.get("factor_id") for s in retained if s.get("factor_id")]
        if not retained_ids:
            return None
        fr = FactorReturnsBuilder.align_to_factors(factor_returns, retained_ids)
        if len(fr) < 120:
            logger.info("[L3] Step 7.7: 走航矩阵样本不足 (%d < 120)，跳过走航", len(fr))
            return None

        # 权重函数：train 段 Sharpe 加权（与 L3 sharpe_weight 基线一致）
        sharpe_map = {s.get("factor_id"): s.get("sharpe", 0.0) for s in retained}

        def weight_fn(train_df: pd.DataFrame) -> np.ndarray:
            w = np.array([max(sharpe_map.get(c, 0.0), 0.01) for c in train_df.columns], dtype=float)
            total = float(np.sum(w))
            return w / total if total > 0 else np.ones(len(w)) / len(w)

        wf = PortfolioWalkForward()
        result = wf.evaluate(fr, weight_fn)

        ts = datetime.now().strftime("%Y-%m-%d")
        combo_id = combo.get("combo_id", "unknown")
        reports_dir = Path("reports") / self.market / ts
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_file = reports_dir / f"portfolio_wf_{combo_id}.md"

        win_lines = (
            "\n".join(
                f"| {w.get('train_start')} → {w.get('test_end')} | "
                f"{w.get('sharpe', 0.0):.3f} | {w.get('ic', 0.0):.4f} | "
                f"{w.get('max_correlation', 0.0):.3f} | {w.get('turnover', 0.0):.3f} |"
                for w in result.get("windows", [])
            )
            or "| (无窗口) | - | - | - | - |"
        )

        md = f"""# 组合层走航验证报告

- **combo_id**: {combo_id}
- **trace_id**: {trace_id}
- **生成时间**: {datetime.now().isoformat()}
- **窗口数**: {result.get("n_windows_completed", 0)}
- **夏普一致性**: {result.get("sharpe_consistency", 0.0):.2f}
- **跨窗口夏普波动**: {result.get("sharpe_volatility", 0.0):.3f}
- **一致性得分**: {result.get("consistency_score", 0.0):.2f}
- **通过**: {result.get("passed", False)}

## 各窗口表现

| 窗口区间 (train→test) | 夏普 | IC | 最大相关性 | 换手 |
|:-----------------------|:-----|:---|:-----------|:-----|
{win_lines}
"""
        out_file.write_text(md, encoding="utf-8")
        logger.info(
            "[L3] Step 7.7: 走航报告已生成 [%s]: %d 窗口, score=%.2f, passed=%s",
            out_file.name,
            result.get("n_windows_completed", 0),
            result.get("consistency_score", 0.0),
            result.get("passed", False),
        )
        return out_file

    def run(
        self,
        market_ohlcv: Optional[Any] = None,
        factor_returns: Optional[pd.DataFrame] = None,
        exposure_matrix: Optional[np.ndarray] = None,
        recompute_weights: Optional[bool] = None,
    ) -> PortfolioRunResult:
        """执行一次完整的 L3 Portfolio Loop。

        Args:
            market_ohlcv: 市场 OHLCV 数据（pd.DataFrame），用于 Regime 检测。
                         若为 None 或 enable_regime_adaptation=False，跳过自适应调整。
            recompute_weights: 是否重算组合权重（GAP-072，v2.99.0）。
                         None=按配置 l3_weight_recompute_cadence 自动判定
                         （weekly 仅重算日全量构建，其余日冻结返回 status="frozen"）；
                         True=强制全量重算；False=强制冻结。
        """
        trace_id = generate_trace_id("l3")
        state = self.state_manager.mark_running()
        logger.info("[L3] ========== Portfolio Loop 启动 ==========")
        logger.info("[L3] trace_id=%s run_id=%s", trace_id, state.get("run_id"))
        logger.info("[L3] market=%s elite_dir=%s synthesis_mode=%s", self.market, self.elite_dir, self.synthesis_mode)
        logger.info(
            "[L3] use_duckdb=%s enable_regime=%s memory_dir=%s",
            self.use_duckdb,
            self.enable_regime_adaptation,
            self.memory_dir,
        )

        # GAP-072 (v2.99.0): 权重重算日判定 — 解绑 L3 与信号管道。
        # 冻结日不重算权重、不重建组合（复用上次 current_combo.json），
        # 信号管道每日独立运行并复用权重快照，仅刷新因子值。
        if recompute_weights is None:
            try:
                from fts.config import is_weight_recompute_day

                recompute_weights = is_weight_recompute_day()
            except Exception:  # noqa: BLE001
                recompute_weights = True
            # 冷启动保护：尚无上次组合时，冻结日仍执行全量构建（无权重可冻结）
            if not recompute_weights and self.portfolio_manager.load_prev_combo() is None:
                logger.info("[L3] 冷启动（无上次组合）：冻结日仍执行全量组合构建")
                recompute_weights = True
        if not recompute_weights:
            logger.info("[L3] 权重冻结日：跳过权重重算与组合重建（复用上次组合，仅信号管道每日刷新因子值）")
            state["total_signals_processed"] = 0
            state["total_signals_retained"] = 0
            state["status"] = "frozen"
            state["last_error"] = None
            self.state_manager.save(state)
            return PortfolioRunResult(
                run_id=state["run_id"],
                trace_id=trace_id,
                n_factors_input=0,
                n_factors_retained=0,
                combo_sharpe=0.0,
                signal_sharpe=None,
                max_correlation=0.0,
                n_proposals=0,
                status="frozen",
                error=None,
                output_paths={},
            )

        try:
            # Step 0.5: 加载市场数据（用于相关性去重）
            panel_data = None
            try:
                from ..data import FTSDataProvider

                provider = FTSDataProvider()
                panel_data, _cdates = provider.get_futures_panel(days=MIN_EVAL_DAYS)
                logger.info(
                    "[L3] Step 0.5: 期货面板数据加载完成 (%d 品种, %d 交易日)", len(panel_data), len(_cdates)
                )
            except Exception as e:
                logger.warning("[L3] Step 0.5: 期货面板数据加载失败 (%s)，使用 IC-only 去重", e)
                panel_data = None
            # Step 0.5b: 未显式传入市场数据时，自动构建市场级合成 OHLCV，
            # 供 Step 2.5 Regime 自适应权重调整使用（v2.98.1，方案 B）。
            if market_ohlcv is None and panel_data:
                try:
                    from .regime import SectorRegimeSelector

                    _mkt = SectorRegimeSelector._build_sector_ohlcv(panel_data, list(panel_data.keys()))
                    if _mkt is not None and not _mkt.empty:
                        market_ohlcv = _mkt
                        logger.info(
                            "[L3] Step 0.5b: 市场合成 OHLCV 构建完成 (%d 交易日)，Step 2.5 Regime 自适应启用",
                            len(market_ohlcv),
                        )
                    else:
                        logger.info("[L3] Step 0.5b: 面板数据不足，跳过 Regime 自适应")
                        market_ohlcv = None
                except Exception as e:
                    logger.warning("[L3] Step 0.5b: 市场 OHLCV 构建失败，跳过 Regime 自适应: %s", e)
                    market_ohlcv = None

            # Step 1: 加载 elite 因子
            logger.info("[L3] Step 1a: 开始加载 elite 因子 [market=%s, use_duckdb=%s]", self.market, self.use_duckdb)
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
                logger.info(
                    "[L3] 因子统计: sharpe=[%.2f, %.2f] ic=[%.4f, %.4f] turnover=[%.2f, %.2f]",
                    min(sharpe_values),
                    max(sharpe_values),
                    min(ic_values),
                    max(ic_values),
                    min(turnover_values),
                    max(turnover_values),
                )
                for i, f in enumerate(factors[:5]):
                    logger.info(
                        "[L3]   Top%d: %s | sharpe=%.2f | ic=%.4f | turnover=%.2f | src=%s",
                        i + 1,
                        f.get("name", "?"),
                        f.get("sharpe", 0),
                        f.get("ic", 0),
                        f.get("turnover", 0),
                        f.get("source_file", "?"),
                    )
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

            # Step 1.5: 纯外推验证（P2 差距修复）
            # 检查每个因子在新数据上的 IC 衰减，连续 3 次衰减 > 20% 标记为待降级
            if panel_data:
                combo_updated_at = datetime.now().isoformat()
                oos_demoted = 0
                for i, factor in enumerate(factors):
                    factors[i] = _validate_oos_extrapolation(
                        factor,
                        panel_data,
                        combo_updated_at,
                    )
                    oos_info = factors[i].get("oos_extrapolation", {})
                    if oos_info.get("needs_demotion", False):
                        oos_demoted += 1
                        logger.warning(
                            "[L3] Step 1.5: 因子 %s IC 衰减 %d%% (连续 %d/%d), 标记降级",
                            factor.get("name", "?"),
                            int(oos_info.get("ic_decay", 0) * 100),
                            oos_info.get("consecutive_decay_count", 0),
                            3,
                        )
                if oos_demoted > 0:
                    logger.warning(
                        "[L3] Step 1.5: %d 个因子因连续 IC 衰减被标记为待降级",
                        oos_demoted,
                    )
                else:
                    logger.info("[L3] Step 1.5: 纯外推验证完成, 无因子需要降级")

            # Step 1.7: 活跃因子数量上限（ACTIVE_FACTOR_CAP）
            # 超过上限时按 Sharpe 排序保留前 N 个，防止冗余因子稀释组合夏普
            if len(factors) > ACTIVE_FACTOR_CAP:
                sorted_factors = sorted(factors, key=lambda f: -abs(f.get("sharpe", 0.0)))
                removed = sorted_factors[ACTIVE_FACTOR_CAP:]
                factors = sorted_factors[:ACTIVE_FACTOR_CAP]
                logger.info(
                    "[L3] Step 1.7: ACTIVE_FACTOR_CAP=%d 触发, 过滤 %d 个因子 (sharpe 排序)",
                    ACTIVE_FACTOR_CAP,
                    len(removed),
                )
                for r in removed:
                    logger.info(
                        "[L3] Step 1.7:   过滤因子 %s | sharpe=%.2f | ic=%.4f",
                        r.get("name", "?"),
                        r.get("sharpe", 0),
                        r.get("ic", 0),
                    )
            else:
                logger.info(
                    "[L3] Step 1.7: 因子数 %d ≤ ACTIVE_FACTOR_CAP=%d, 无需过滤",
                    len(factors),
                    ACTIVE_FACTOR_CAP,
                )

            # Step 1.8: P1 因子聚类（可选，系统性降低冗余）
            if self.enable_clustering and len(factors) >= 3:
                logger.info("[L3] Step 1.8: 开始 P1 因子聚类 (factors=%d, threshold=%s)", len(factors), "0.7")
                try:
                    if self._clustering_engine is None:
                        from .factor_clustering import FactorClusteringEngine

                        self._clustering_engine = FactorClusteringEngine(
                            cluster_threshold=0.7,
                            linkage_method="average",
                        )
                    n_before = len(factors)
                    factors = self._clustering_engine.run(factors, panel_data)
                    n_after = len(factors)
                    reduced = n_before - n_after
                    if reduced > 0:
                        logger.info(
                            "[L3] Step 1.8: P1 聚类完成, 移除 %d 个冗余因子 (%d → %d)",
                            reduced,
                            n_before,
                            n_after,
                        )
                    else:
                        logger.info(
                            "[L3] Step 1.8: P1 聚类完成, 无冗余因子移除 (保留 %d 个)",
                            n_after,
                        )
                except Exception as e:
                    logger.warning("[L3] Step 1.8: P1 聚类失败 (非致命): %s", e)
            else:
                logger.info(
                    "[L3] Step 1.8: P1 聚类跳过 (enable_clustering=%s, n_factors=%d)",
                    self.enable_clustering,
                    len(factors),
                )

            # Step 1.9: P2 PCA 降维（可选，信号源压缩）
            if self.enable_pca and len(factors) >= 3 and panel_data:
                logger.info("[L3] Step 1.9: 开始 P2 PCA 降维 (factors=%d)", len(factors))
                try:
                    if self._pca_compressor is None:
                        from .factor_clustering import PCASignalCompressor

                        self._pca_compressor = PCASignalCompressor(
                            variance_ratio=0.95,
                            max_components=10,
                        )
                    pca_result = self._pca_compressor.run(factors, panel_data)
                    if pca_result.get("pca_applied", False):
                        # PCA 降维后，使用 PCA 信号替换原有因子信号
                        pca_signals = pca_result.get("pca_signals", [])
                        n_components = pca_result.get("n_components", 0)
                        explained = pca_result.get("explained_variance_ratio", 0.0)
                        logger.info(
                            "[L3] Step 1.9: PCA 降维完成: %d 因子 → %d 主成分 (解释方差=%.1f%%)",
                            len(factors),
                            n_components,
                            explained * 100,
                        )
                        if pca_signals:
                            # 更新因子权重
                            sig_map = {s["factor_id"]: s for s in pca_signals}
                            for f in factors:
                                fid = f.get("factor_id", f.get("name", "?"))
                                if fid in sig_map:
                                    f["pca_weight"] = sig_map[fid].get("weight", 0.0)
                                    f["pca_orthogonalized"] = True
                            # PCA 权重对比日志：原始 Sharpe 权重 vs PCA 权重
                            pca_weight_log = []
                            for f in factors:
                                fid = f.get("factor_id", f.get("name", "?"))
                                pca_w = f.get("pca_weight", 0.0)
                                sharpe_w = f.get("sharpe", 0.0)
                                if pca_w > 0.001:
                                    pca_weight_log.append(
                                        f"{f.get('name', fid)}: sharpe_w={sharpe_w:.2f} → pca_w={pca_w:.4f}"
                                    )
                            if pca_weight_log:
                                logger.info(
                                    "[L3] Step 1.9: PCA 权重对比 (原始 Sharpe → PCA 权重):\n  %s",
                                    "\n  ".join(pca_weight_log[:10]),
                                )
                                if len(pca_weight_log) > 10:
                                    logger.info(
                                        "[L3] Step 1.9: ... 还有 %d 个因子",
                                        len(pca_weight_log) - 10,
                                    )
                    else:
                        logger.info("[L3] Step 1.9: PCA 降维跳过 (信号矩阵不足)")
                except Exception as e:
                    logger.warning("[L3] Step 1.9: PCA 降维失败 (非致命): %s", e)
            else:
                logger.info(
                    "[L3] Step 1.9: PCA 降维跳过 (enable_pca=%s, n_factors=%d, panel_data=%s)",
                    self.enable_pca,
                    len(factors),
                    panel_data is not None,
                )

            # Step 2: 信号合成
            signals, _max_corr, _combo_turn = synthesize_signals(
                factors,
                self.synthesis_mode,
                elite_dir=self.elite_dir,
                returns_matrix=factor_returns,
                optimizer_mode=self.optimizer_mode,
                optimizer_config={
                    **self.optimizer_config,
                    **({"exposure_matrix": exposure_matrix} if exposure_matrix is not None else {}),
                },
                market=self.market,
                weight_config=self.weight_config,
            )
            logger.info("[L3] Step 2: 信号合成完成, mode=%s, 信号数=%d", self.synthesis_mode, len(signals))
            # [WEIGHT-LOG] Step 2 合成后权重摘要
            if signals:
                sorted_by_w = sorted(signals, key=lambda x: -x.get("weight", 0))
                w_sum = sum(s.get("weight", 0) for s in sorted_by_w)
                logger.info(
                    "[L3-WEIGHT] Step 2 权重摘要: sum=%.4f, top3=[%s], bottom3=[%s]",
                    w_sum,
                    ", ".join(f"{s['name']}={s['weight']:.4f}" for s in sorted_by_w[:3]),
                    ", ".join(f"{s['name']}={s['weight']:.4f}" for s in sorted_by_w[-3:]),
                )
            state["total_signals_processed"] = len(signals)

            # Step 2.5: Regime 自适应权重调整
            if self.enable_regime_adaptation and market_ohlcv is not None:
                try:
                    if self._regime_selector is None:
                        from .regime import RegimeAwareSelector

                        self._regime_selector = RegimeAwareSelector()

                    regime = self._regime_selector.detect(market_ohlcv)  # type: ignore[arg-type]

                    aconfig = self.adaptive_config or DEFAULT_ADAPTIVE_CONFIG
                    if aconfig.get("enabled", True):
                        signals = regime_adaptive_weight_adjustment(
                            signals,
                            regime,
                            factors,
                            min_weight=aconfig.get("min_weight", 0.01),
                            dimension=aconfig.get("dimension", "both"),
                            min_clamp=aconfig.get("min_clamp", 0.5),
                            max_clamp=aconfig.get("max_clamp", 1.5),
                            probability_mix=aconfig.get("probability_mix", True),
                        )
                        # 28-T4: 计算并暂存置信度仓位缩放因子（T6 在 build_combo 消费）
                        self._regime_exposure_scale = _compute_exposure_scale(
                            regime,
                            enabled=aconfig.get("confidence_scale", True),
                            scale_min=aconfig.get("confidence_scale_min", 0.3),
                            entropy_penalty=aconfig.get("confidence_entropy_penalty", 0.5),
                        )
                        self._regime_meta = {
                            "regime": regime.get("regime", "unknown"),
                            "confidence": regime.get("confidence", 0.0),
                            "exposure_scale": self._regime_exposure_scale,
                            "entropy_norm": None,
                        }
                        # 28-T10: 上报 regime 观测指标（置信度/熵/exposure_scale/blend HHI），
                        # 供 /metrics 审计；失败不阻断主流程。
                        try:
                            from fts.monitor.prometheus_metrics import metrics_registry

                            metrics_registry.record_regime_metrics(
                                self.market,
                                regime.get("regime", ""),
                                regime.get("confidence", 0.0),
                                regime.get("regime_probs"),
                                self._regime_exposure_scale,
                            )
                        except Exception as met_err:
                            logger.warning("[L3] Step 2.5: Regime 指标上报失败: %s", met_err)
                        # RegimeSmoother 权重平滑（A.3 / v2.56.0）
                        try:
                            from .adaptive_weight import RegimeSmoother

                            if self._regime_smoother is None:
                                sm = aconfig.get("smoother", {}) or {}
                                self._regime_smoother = RegimeSmoother(
                                    alpha=float(sm.get("alpha", 0.5)),
                                    min_days=int(sm.get("min_days", 2)),
                                    de_risk_alpha=float(sm.get("de_risk_alpha", 0.8)),
                                    re_risk_alpha=float(sm.get("re_risk_alpha", 0.1)),
                                )
                            # 平滑需要当前权重；首次运行（无 prev_weights）时直接采用
                            prev_combo = self.portfolio_manager.load_prev_combo()
                            prev_weights = self.portfolio_manager.extract_prev_weights(prev_combo)
                            if prev_weights:
                                new_weights = {
                                    s.get("factor_id"): s.get("weight", 0.0) for s in signals if s.get("retained", True)
                                }
                                smoothed = self._regime_smoother.should_apply(
                                    regime.get("regime", "oscillate"),
                                    prev_weights,
                                    new_weights,
                                )
                                for s in signals:
                                    if s.get("retained", True) and s.get("factor_id") in smoothed:
                                        s["weight"] = smoothed[s["factor_id"]]
                        except Exception as sm_err:
                            logger.warning("[L3] Step 2.5: RegimeSmoother 失败，使用调整后权重: %s", sm_err)
                    logger.info(
                        "[L3] Step 2.5: Regime=%s (confidence=%.2f), 自适应调整完成 [dim=%s, exposure_scale=%.2f]",
                        regime.get("regime", "unknown"),
                        regime.get("confidence", 0.0),
                        aconfig.get("dimension", "both"),
                        self._regime_exposure_scale,
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
                # L2 正交化元数据透传（GAP-I206 补充）：正交化因子在 L3 不重复剔除
                _factor_by_id = {f.get("factor_id", ""): f for f in factors}
                for s in signals:
                    _f = _factor_by_id.get(s.get("factor_id", ""), {})
                    if _f.get("orthogonalized"):
                        s["orthogonalized"] = True
                        s["orthogonalized_against"] = _f.get("orthogonalized_against", "")
                        s["orthogonalized_pearson"] = _f.get("orthogonalized_pearson", 0.0)
                signals = orthogonalize_factors(
                    signals,
                    max_corr_threshold=0.7,
                    factors=factors,
                    use_tiered=(len(factors) >= 30),
                    l2_prior_correlations=l2_prior,
                )
                post_retained = [s["name"] for s in signals if s.get("retained", True)]
                removed_in_ortho = set(pre_retained) - set(post_retained)
                if removed_in_ortho:
                    logger.info(
                        "[L3-WEIGHT] 正交化移除 %d 个因子: %s",
                        len(removed_in_ortho),
                        ", ".join(sorted(removed_in_ortho)),
                    )
                logger.info(
                    "[L3] Step 3: 正交化完成, 保留 %d/%d",
                    sum(1 for s in signals if s.get("retained", True)),
                    len(signals),
                )
            else:
                logger.info("[L3] Step 3: 跳过正交化（elastic_net L1 已做变量选择）")

            # Step 4: 衰减检验
            signals = decay_test(signals, max_decay_rate=0.30)
            n_retained = sum(1 for s in signals if s.get("retained", True))
            logger.info("[L3] Step 4: 衰减检验完成, 保留 %d 个因子", n_retained)

            # Step 5: 组合构建（含粘性约束 + 漂移监控）
            # 方案①：未显式传因子收益矩阵时，自动从面板+因子代码构建（measured 实测口径）；
            # 构建失败/数据不足时保持 None，build_combo 回退估算口径。
            if factor_returns is None and panel_data:
                auto_fr = _auto_build_factor_returns(panel_data, factors, self.elite_dir, market=self.market)
                if auto_fr is not None:
                    factor_returns = auto_fr
                else:
                    logger.info("[L3] Step 5: 无因子收益矩阵，组合指标回退估算口径（estimated）")
            prev_combo = self.portfolio_manager.load_prev_combo()
            prev_weights = self.portfolio_manager.extract_prev_weights(prev_combo)
            if self.sticky_config and prev_weights and prev_combo is not None:
                logger.info(
                    "[L3] Step 5: 读取上次组合 %s 共 %d 个因子权重 (粘性约束)",
                    prev_combo.get("combo_id", "?"),
                    len(prev_weights),
                )
            # G1 同向敞口惩罚 + G3 换手预算默认开启（35-gap-closure-plan D5；单测直接调用默认关闭）
            from .portfolio_risk_controls import AlignedExposureConfig
            from .portfolio_turnover import TurnoverBudgetConfig

            combo = build_combo(
                signals,
                self.synthesis_mode,
                trace_id,
                prev_weights=prev_weights or None,
                sticky_config=self.sticky_config,
                factor_returns=factor_returns,
                market=self.market,
                cost_config=self.cost_config,
                turnover_penalty=self.turnover_penalty,
                exposure_scale=self._regime_exposure_scale,
                regime_meta=self._regime_meta,
                aligned_exposure_config=AlignedExposureConfig(),
                turnover_budget_config=TurnoverBudgetConfig() if self.turnover_budget_enabled else None,
            )
            logger.info(
                "[L3] Step 5: 组合构建完成, 夏普=%.2f, 换手率=%.2f",
                combo.get("combo_sharpe", 0),
                combo.get("combo_turnover", 0),
            )
            if combo.get("net_combo_sharpe") is not None:
                logger.info(
                    "[L3] Step 5: net 夏普=%.2f (gross=%.2f, 成本扣除)",
                    combo.get("net_combo_sharpe"),
                    combo.get("combo_sharpe"),
                )

            # Step 5.5: 漂移监控 — 记录成员重合率 + 权重 L1 变化率 + 阈值告警（GAP-F13）
            drift_alert: dict[str, Any] = {}
            try:
                drift = self.drift_monitor.compute(prev_combo, combo, trace_id)
                self.drift_monitor.record(drift)
                drift_alert = self.drift_monitor.check_and_alert(drift, trace_id)
                if drift_alert.get("alerted"):
                    state["drift_alerted"] = True
                    state["drift_alert_info"] = drift_alert
            except Exception as e:
                logger.warning("[L3] Step 5.5: 漂移监控记录失败 (非致命): %s", e)

            # Step 6: Verifier 判定
            passed, reasons = self.verifier.check(combo)
            if not passed:
                logger.warning("[L3] Step 6: Verifier 未通过: %s", "; ".join(reasons))
                state["last_error"] = "; ".join(reasons)

            # Step 7: 注入 FDT
            proposals = generate_agent_proposals(combo, trace_id)
            # GAP-F13: 漂移超阈值且开启自动重平衡时，附加粘性重平衡建议供 Agent 消费
            if drift_alert.get("trigger_rebalance"):
                rebal_proposal = self.drift_monitor.generate_rebalance_proposal(
                    drift,
                    drift_alert,
                )
                if rebal_proposal is not None:
                    proposals.append(rebal_proposal)
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

            # Step 7.6: 归因报告（GAP-L307）— 因子收益矩阵可用时输出贡献度/暴露/VaR/ES
            try:
                if factor_returns is not None and combo.get("signals"):
                    attr_path = self._generate_attribution_report(
                        factor_returns,
                        combo,
                        trace_id,
                    )
                    if attr_path:
                        state["attribution_report"] = str(attr_path)
            except Exception as e:
                logger.warning("[L3] Step 7.6: 归因报告生成失败 (非致命): %s", e)

            # Step 7.7: 组合层走航验证（GAP-L306）— 滚动窗口权重前段确定/后段实测
            try:
                if factor_returns is not None and combo.get("signals"):
                    wf_path = self._generate_walk_forward_report(
                        factor_returns,
                        combo,
                        trace_id,
                    )
                    if wf_path:
                        state["walk_forward_report"] = str(wf_path)
            except Exception as e:
                logger.warning("[L3] Step 7.7: 走航报告生成失败 (非致命): %s", e)

            # Step 7.8: 组合级风控（GAP-067）— 回撤止损 + 相关性熔断建议（写入 state，执行由 FDT）
            try:
                if factor_returns is not None and combo.get("signals"):
                    from .portfolio_risk_controls import run_portfolio_risk_controls

                    retained_rc = [s for s in combo.get("signals", []) if s.get("retained", True)]
                    retained_ids_rc = [s.get("factor_id") for s in retained_rc if s.get("factor_id")]
                    if retained_ids_rc:
                        fr_rc = FactorReturnsBuilder.align_to_factors(factor_returns, retained_ids_rc)
                        if len(fr_rc) >= 5:
                            w_rc = np.array([s.get("weight", 0.0) for s in retained_rc], dtype=float)
                            tw_rc = float(np.sum(w_rc))
                            if tw_rc > 0:
                                w_rc = w_rc / tw_rc
                            pf_rc = FactorReturnsBuilder.portfolio_returns(fr_rc, w_rc)
                            alert = run_portfolio_risk_controls(
                                combo_returns=np.asarray(pf_rc, dtype=float),
                                member_returns=fr_rc,
                            )
                            state["risk_alerts"] = alert.to_dict()
                            if alert.drawdown_stop or alert.correlation_breaker:
                                logger.warning("[L3] Step 7.8: 组合风控告警: %s", alert.notes)
                            else:
                                logger.info(
                                    "[L3] Step 7.8: 组合风控正常 dd=%.2f%% corr=%.2f",
                                    alert.drawdown_current * 100.0,
                                    alert.correlation_current,
                                )
            except Exception as e:
                logger.warning("[L3] Step 7.8: 组合风控检查失败 (非致命): %s", e)

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
                signal_sharpe=combo.get("signal_sharpe"),
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
                signal_sharpe=None,
                max_correlation=0.0,
                n_proposals=0,
                status="circuit_broken",
                error=str(e),
                output_paths={},
            )


# ─── CLI ──────────────────────────────────────────────────


def main() -> None:
    """CLI 入口: python -m loop_engine.portfolio_loop [--once] [--mode ...] [--optimizer-mode ...]"""
    parser = argparse.ArgumentParser(description="L3 Portfolio Loop")
    parser.add_argument("--once", action="store_true", help="单次运行模式")
    parser.add_argument(
        "--mode",
        default="equal_weight",
        choices=["equal_weight", "sharpe_weight", "elastic_net", "optimizer"],
        help="信号合成模式（optimizer 需 --returns-matrix，GAP-L303）",
    )
    parser.add_argument(
        "--optimizer-mode",
        default="risk_parity",
        choices=["risk_parity", "mvo"],
        help="optimizer 目标（GAP-L303，默认 risk_parity）",
    )
    parser.add_argument(
        "--returns-matrix", default=None, help="因子收益矩阵 CSV 路径（optimizer 模式与实测化需要，可选）"
    )
    parser.add_argument("--memory-dir", default="memory/portfolio", help="状态/组合存储目录")
    parser.add_argument("--elite-dir", default="memory/knowledge/factors/futures_elite", help="精英因子目录")
    parser.add_argument(
        "--cost-config",
        default=None,
        help='交易成本配置 JSON 路径（GAP-L305 net 指标，可选；如 {"market": "futures", "slippage_bps": 0.5}）',
    )
    parser.add_argument("--capacity-limits", default=None, help="容量权重上限 CSV/JSON 数组（GAP-L305，可选）")
    parser.add_argument(
        "--force-recompute",
        action="store_true",
        help="强制全量重算组合权重（GAP-072，默认按 l3_weight_recompute_cadence 自动判定）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    factor_returns: Optional[pd.DataFrame] = None
    if args.returns_matrix:
        try:
            factor_returns = pd.read_csv(args.returns_matrix, index_col=0, parse_dates=True)
        except Exception as e:
            logger.warning("[L3] 读取 returns-matrix 失败 (%s)，跳过实测化/optimizer 输入", e)

    cost_config: Optional[dict[str, Any]] = None
    if args.cost_config:
        try:
            import json as _json

            cost_config = _json.loads(Path(args.cost_config).read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[L3] 读取 cost-config 失败 (%s)，net 指标禁用", e)

    optimizer_config: dict[str, Any] = {}
    if args.capacity_limits:
        try:
            import json as _json

            cap_vals = _json.loads(Path(args.capacity_limits).read_text(encoding="utf-8"))
            optimizer_config["capacity_limits"] = cap_vals
        except Exception as e:
            logger.warning("[L3] 读取 capacity-limits 失败 (%s)，容量约束禁用", e)

    loop = PortfolioLoop(
        memory_dir=args.memory_dir,
        elite_dir=args.elite_dir,
        synthesis_mode=args.mode,
        optimizer_mode=args.optimizer_mode,
        optimizer_config=optimizer_config,
        cost_config=cost_config,
    )
    result = loop.run(factor_returns=factor_returns, recompute_weights=(True if args.force_recompute else None))

    print(
        f"[L3] run_id={result.run_id} status={result.status} "
        f"input_factors={result.n_factors_input} retained={result.n_factors_retained} "
        f"sharpe={result.combo_sharpe:.2f} proposals={result.n_proposals}"
    )
    if result.error:
        print(f"[L3] 警告/错误: {result.error}")
    sys.exit(0 if result.status in ("passed", "verifier_warning", "completed", "frozen") else 1)


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
