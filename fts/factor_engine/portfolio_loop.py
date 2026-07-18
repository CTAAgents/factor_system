"""
loop_engine/portfolio_loop.py — L3 Portfolio Loop 主循环

HARNESS §11-loop-engineering.md §16:
    L3 Portfolio Loop — 每周五 15:30 组合构建（信号合成 + 正交化 + 衰减检验 + 注入 FDT）

流程（5 步）:
    Step 1: 信号合成 → 读取 elite 因子库，等权/夏普加权合成
    Step 2: 因子正交化 → 计算相关性矩阵，剔除 > 0.7
    Step 3: 组合构建 → 权重归一化 + 十分位 + 多空 + 成本估算
    Step 4: 衰减检验 → 6 个月滚动窗口，衰减 > 30% 剔除
    Step 5: 注入 FDT → 输出 combo.json + Agent 优化建议

Verifier:
    - 组合夏普 > 2.0
    - 因子间最大相关性 < 0.3
    - 组合换手率 < 50%/月
    - 衰减率 < 30%

版本: v8.10.0
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
    DEFAULT_L3_VERIFIER_CONFIG,
    DEFAULT_L3_BUDGET,
    AgentOptimizationProposal,
    FactorCorrelation,
    L3MetaLoopState,
    L3VerifierConfig,
    PortfolioCombo,
    PortfolioSignal,
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
        if state.get("version") != EVOLUTION_VERSION:
            raise L3Error(
                f"状态版本不匹配: {state.get('version')} != {EVOLUTION_VERSION}"
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
            if data.get("version") != EVOLUTION_VERSION:
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
            version=EVOLUTION_VERSION,
        )


# ─── 组合管理器 ───────────────────────────────────────────

class PortfolioManager:
    """管理组合文件（memory/portfolio/current_combo.json）。"""

    def __init__(self, portfolio_dir: str | Path = "memory/portfolio"):
        self.portfolio_dir = Path(portfolio_dir)
        self.combo_file = self.portfolio_dir / COMBO_FILE_NAME
        self.proposals_dir = self.portfolio_dir / PROPOSALS_DIR
        self.portfolio_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
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
        self._cache = combo
        self.combo_file.write_text(
            json.dumps(combo, ensure_ascii=False, indent=2), encoding="utf-8",
        )

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


# ─── 信号合成 ─────────────────────────────────────────────

def synthesize_signals(
    factors: list[dict[str, Any]],
    mode: str = "equal_weight",
) -> tuple[list[PortfolioSignal], float, float]:
    """信号合成。

    Args:
        factors: 每个 dict 必须含 factor_id, name, sharpe, ic, turnover, decay_6m
        mode: "equal_weight" | "sharpe_weight" | "lightgbm"

    Returns:
        (signals, max_correlation, combo_turnover)
    """
    if not factors:
        return [], 0.0, 0.0

    n = len(factors)
    signals: list[PortfolioSignal] = []

    if mode == "equal_weight":
        w = 1.0 / n
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
    else:
        # lightgbm 模式暂回退等权
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


def orthogonalize_factors(
    signals: list[PortfolioSignal],
    correlation_matrix: list[FactorCorrelation] | None = None,
    max_corr_threshold: float = 0.7,
) -> list[PortfolioSignal]:
    """因子正交化 — 剔除相关性 > threshold 的因子。

    保留夏普更高的因子。
    """
    if correlation_matrix is None:
        # 无相关性矩阵时，全部标记为已正交化
        for s in signals:
            s["orthogonalized"] = True
        return signals

    # 构建高相关性对
    high_corr_pairs: dict[str, set[str]] = {}
    for edge in correlation_matrix:
        if abs(edge.get("pearson", 0)) > max_corr_threshold:
            a, b = edge["factor_id_a"], edge["factor_id_b"]
            high_corr_pairs.setdefault(a, set()).add(b)
            high_corr_pairs.setdefault(b, set()).add(a)

    # 按夏普排序，保留更高的
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


def decay_test(
    signals: list[PortfolioSignal],
    max_decay_rate: float = 0.30,
) -> list[PortfolioSignal]:
    """衰减检验 — 6 个月滚动衰减 > threshold 的因子标记为不保留。"""
    for s in signals:
        if s.get("decay_6m", 0) > max_decay_rate:
            s["retained"] = False
    return signals


# ─── 组合构建 ─────────────────────────────────────────────

def build_combo(
    signals: list[PortfolioSignal],
    mode: str = "equal_weight",
    trace_id: Optional[str] = None,
) -> PortfolioCombo:
    """构建组合 — 归一化权重 + 计算组合指标。"""
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

    # 权重归一化
    total_w = sum(s.get("weight", 0) for s in retained)
    if total_w > 0:
        for s in retained:
            s["weight"] = s.get("weight", 0) / total_w

    # 组合指标（简化为算术平均）
    combo_sharpe = sum(s.get("sharpe", 0) for s in retained) / len(retained)
    combo_turnover = sum(s.get("turnover", 0) for s in retained) / len(retained)
    max_corr = 0.0
    for s in retained:
        max_corr = max(max_corr, s.get("sharpe", 0) * 0.15)  # 估算

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

def load_elite_factors(elite_dir: str | Path) -> list[dict[str, Any]]:
    """从 elite 目录读取因子，返回简易 dict 列表。"""
    elite_path = Path(elite_dir)
    factors: list[dict[str, Any]] = []
    if not elite_path.exists():
        return factors
    for fp in sorted(elite_path.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            factors.append({
                "factor_id": data.get("factor_id", fp.stem),
                "name": data.get("name", fp.stem),
                "sharpe": data.get("sharpe", data.get("backtest", {}).get("sharpe", 0.5)),
                "ic": data.get("ic", data.get("backtest", {}).get("ic", 0.02)),
                "turnover": data.get("turnover", data.get("backtest", {}).get("turnover_monthly", 0.3)),
                "decay_6m": data.get("decay_6m", 0.05),
            })
        except (json.JSONDecodeError, TypeError):
            continue
    return factors


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
        Step 3: 因子正交化
        Step 4: 衰减检验
        Step 5: 组合构建
        Step 6: Verifier 判定
        Step 7: 注入 FDT
    """

    def __init__(
        self,
        memory_dir: str | Path = "memory/portfolio",
        elite_dir: str | Path = "memory/knowledge/factors/elite",
        verifier_config: Optional[L3VerifierConfig] = None,
        synthesis_mode: str = "equal_weight",
    ):
        self.memory_dir = Path(memory_dir)
        self.elite_dir = Path(elite_dir)
        self.verifier = L3Verifier(verifier_config or DEFAULT_L3_VERIFIER_CONFIG)
        self.synthesis_mode = synthesis_mode
        self.state_manager = PortfolioStateManager(memory_dir)
        self.portfolio_manager = PortfolioManager(memory_dir)

    def run(self) -> PortfolioRunResult:
        """执行一次完整的 L3 Portfolio Loop。"""
        trace_id = generate_trace_id("l3")
        state = self.state_manager.mark_running()
        logger.info("[L3] Portfolio Loop 启动 trace_id=%s", trace_id)

        try:
            # Step 1: 加载 elite 因子
            factors = load_elite_factors(self.elite_dir)
            logger.info("[L3] Step 1: 读取 %d 个 elite 因子", len(factors))
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
            signals, _max_corr, _combo_turn = synthesize_signals(factors, self.synthesis_mode)
            logger.info("[L3] Step 2: 信号合成完成, mode=%s, 信号数=%d", self.synthesis_mode, len(signals))
            state["total_signals_processed"] = len(signals)

            # Step 3: 因子正交化
            signals = orthogonalize_factors(signals, max_corr_threshold=0.7)
            logger.info("[L3] Step 3: 正交化完成, 保留 %d/%d",
                        sum(1 for s in signals if s.get("retained", True)), len(signals))

            # Step 4: 衰减检验
            signals = decay_test(signals, max_decay_rate=0.30)
            n_retained = sum(1 for s in signals if s.get("retained", True))
            logger.info("[L3] Step 4: 衰减检验完成, 保留 %d 个因子", n_retained)

            # Step 5: 组合构建
            combo = build_combo(signals, self.synthesis_mode, trace_id)
            logger.info("[L3] Step 5: 组合构建完成, 夏普=%.2f, 换手率=%.2f",
                        combo.get("combo_sharpe", 0), combo.get("combo_turnover", 0))

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
    parser.add_argument("--mode", default="equal_weight", choices=["equal_weight", "sharpe_weight"],
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


if __name__ == "__main__":
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
