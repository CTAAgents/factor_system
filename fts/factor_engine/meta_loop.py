"""
loop_engine/meta_loop.py — L1 Meta-Loop 主循环

HARNESS §11-loop-engineering.md §15:
    L1 Meta-Loop — 每日 09:00 知识补给（Bootstrapping + Data-Core 感知 + debate 分析）

流程（5 步）:
    Step 1: agentic 感知 → FTSDataProvider 获取新闻与市场快照
    Step 2: debate_round 分析 → 读取昨日 fdt_langgraph 辩论数据，识别论证薄弱维度
    Step 3: factorengine Bootstrapping → 提取Agent / 验证Agent / 代码生成Agent 链
    Step 4: L1 Verifier → economic_logic >= 2/4 AND is_executable AND not_duplicate
    Step 5: 注入 factor_pool.json + memory/knowledge/factors/l1_injected/

预算控制 + 熔断:
    - 单日 token 超 2x → circuit_broken
    - 失败率 > 95% → circuit_broken
    - 连续 5 次低质量候选 → circuit_broken

版本: v1.1.0（与 FTS 同步）
"""
# pylint: disable=too-many-lines,import-outside-toplevel,broad-exception-caught,too-few-public-methods,too-many-instance-attributes,too-many-arguments,too-many-locals,too-many-positional-arguments

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional, cast

from .contracts import (
    DEFAULT_L1_BUDGET_CONFIG,
    DEFAULT_L1_VERIFIER_CONFIG,
    STATE_SCHEMA_VERSION,
    EconomicLogic,
    FactorPool,
    FactorPoolEntry,
    FactorSignature,
    L1BudgetConfig,
    L1MetaLoopState,
    L1VerifierConfig,
    L1VerifierResult,
    SeedCandidate,
)
from .factor_program import (
    fix_factor_code,
    validate_factor_code,
)
from .extractors import FuturesExtractorPipeline
from .extractors.knowledge_filter import TextEmbedder, dedup_semantic  # plans/44 C4 语义去重
from .l1_l2_funnel import funnel_record  # plans/44 D1: L1→L2 闭环漏斗
from .seed_pool import SeedPool
from .state import generate_run_id, generate_trace_id


logger = logging.getLogger(__name__)


# ─── 异常 ────────────────────────────────────────────────


class MetaLoopError(Exception):
    """L1 Meta-Loop 基础异常。"""


class MetaStateManagerError(MetaLoopError):
    """L1 状态管理异常。"""


class L1VerifierLocked(MetaLoopError):
    """L1 Verifier 已锁定，尝试修改抛出。"""


class FactorPoolError(MetaLoopError):
    """factor_pool.json 管理异常。"""


# ─── L1 Verifier ────────────────────────────────────────


class L1Verifier:
    """L1 Verifier — 锁定的种子候选评估机制。

    HARNESS §11 §15: L1 Verifier 锁定后不可修改。
    任何运行时尝试修改 _config 应抛 RuntimeError。
    """

    # GAP-123 P2④: 各维度机制关键词（论证-评分一致性检查；narrative 命中任一即视为有机制支撑）
    _DIM_MECHANISM_KEYWORDS: dict[str, tuple[str, ...]] = {
        "theory": ("理论", "定理", "模型", "均衡", "定价", "风险溢价", "溢价", "补偿"),
        "behavioral": ("行为", "偏差", "过度反应", "反应不足", "动量", "反转", "羊群", "处置", "锚定", "投资者情绪"),
        "microstructure": ("微观", "流动性", "价差", "盘口", "成交", "订单", "做市", "冲击", "深度"),
        "institutional": ("机构", "持仓", "期限结构", "基差", "资金", "换月", "套保", "参与者", "仓位"),
    }

    def __init__(self, config: L1VerifierConfig = DEFAULT_L1_VERIFIER_CONFIG):
        self._config: L1VerifierConfig = dict(config)  # type: ignore[assignment]
        self._locked: bool = True

    def check(self, candidate: SeedCandidate, seed_pool: SeedPool) -> L1VerifierResult:
        """判定种子候选是否通过 L1 Verifier。

        判定维度:
            1. economic_logic >= min_economic_score/4 维度达标
            2. is_executable（沙箱可编译）
            3. not_duplicate（与现有种子因子不重复）
            4. narrative 长度 >= min_narrative_length
        """
        if not self._locked:
            raise L1VerifierLocked("L1 Verifier 未锁定")

        reasons: list[str] = []
        config = self._config

        # 1. 经济逻辑评分
        economic = candidate.get("economic_logic", {})
        dimensions_passed = 0
        for dim in ("theory", "behavioral", "microstructure", "institutional"):
            if economic.get(dim, 0) >= 3:
                dimensions_passed += 1
        if dimensions_passed < config.get("min_economic_score", 2):
            reasons.append(f"经济逻辑达标维度 {dimensions_passed}/4 < {config['min_economic_score']}")

        # GAP-123 P2④: 论证-评分一致性检查（默认关闭，开启后防「高分低论证」凑分）
        if config.get("require_argument_consistency", False):
            narrative_text = economic.get("narrative", "") or ""
            for dim, kws in self._DIM_MECHANISM_KEYWORDS.items():
                score = economic.get(dim, 0)
                if score >= 3 and narrative_text and not any(kw in narrative_text for kw in kws):
                    reasons.append(
                        f"经济逻辑维度 {dim} 评分 {score} ≥3 但 narrative 缺乏该维度机制论证（GAP-123）"
                    )
                    break

        # 2. 可执行性
        if config.get("require_executable", True):
            if not candidate.get("is_executable", False):
                reasons.append("候选因子代码不可执行（沙箱编译失败）")

        # 3. 重复性
        if config.get("require_not_duplicate", True):
            if candidate.get("is_duplicate", False):
                reasons.append("候选因子与现有种子重复")
            elif self._is_duplicate_by_name(candidate.get("name", ""), seed_pool):
                reasons.append(f"候选因子名称与现有种子重复: {candidate.get('name')}")

        # 4. narrative 长度
        narrative = economic.get("narrative", "")
        min_len = config.get("min_narrative_length", 20)
        if len(narrative) < min_len:
            reasons.append(f"narrative 长度 {len(narrative)} < {min_len}")

        return L1VerifierResult(
            passed=len(reasons) == 0,
            failure_reasons=reasons,
            checked_against=cast(L1VerifierConfig, dict(self._config)),
            checked_at=datetime.now().isoformat(),
        )

    @staticmethod
    def _is_duplicate_by_name(name: str, seed_pool: SeedPool) -> bool:
        """通过名称判断是否与现有种子重复。"""
        if not name:
            return False
        existing_names = {n.lower() for n in seed_pool.list_names()}
        return name.lower() in existing_names

    def lock(self) -> None:
        """锁定 Verifier。"""
        self._locked = True

    def unlock(self) -> None:
        """解锁 Verifier（仅用于测试）。"""
        self._locked = False

    @property
    def is_locked(self) -> bool:
        return self._locked


# ─── L1 状态管理器 ───────────────────────────────────────


class MetaStateManager:
    """L1 Meta-Loop 状态管理器（SSOT 为 state.duckdb，plans/29 P4 读路径切换）。

    JSON（state.json/backup）退役为只读历史快照不再回写。
    """

    def __init__(self, memory_dir: str | Path = "memory/meta_loop", state_store=None):
        # 保留 memory_dir 以派生 namespace/key（meta_loop 根目录 vs 子目录）
        self.memory_dir = Path(memory_dir)
        self._store = state_store  # None → 全局 SSOT（供测试注入临时 store）

    def _store_conn(self):
        """返回状态存储连接（注入的或全局 SSOT）。"""
        from fts.store.state_db import get_state_store

        return self._store if self._store is not None else get_state_store()

    def _ns_key(self) -> tuple[str, str]:
        """派生 state.duckdb 的 (namespace, key)（与 migrate 规则一致）。"""
        if self.memory_dir.name == "meta_loop":
            return "meta_loop", "state"
        return "meta_loop", f"{self.memory_dir.name}/state"

    def load_or_init(self, budget_limit: int) -> L1MetaLoopState:
        """从 state.duckdb 加载状态；缺失则冷启动初始化。"""
        ns, key = self._ns_key()
        data = self._store_conn().get(ns, key)
        if isinstance(data, dict) and data.get("schema_version") == STATE_SCHEMA_VERSION:
            return cast(L1MetaLoopState, data)
        state = self._init_state(budget_limit)
        self.save(state)
        return state

    @staticmethod
    def _init_state(budget_limit: int) -> L1MetaLoopState:
        """初始化新的状态。"""
        # generate_run_id 不接受参数，用 prefix 通过 trace_id 体系区分
        # run_id 格式: run_<8hex>_<timestamp>
        return L1MetaLoopState(
            run_id=generate_run_id(),
            started_at=datetime.now().isoformat(),
            last_bootstrap_topic="",
            total_candidates_generated=0,
            total_candidates_injected=0,
            total_debate_gaps_detected=0,
            tokens_consumed=0,
            budget_limit=budget_limit,
            status="paused",
            last_error=None,
            candidates_ref=[],
            last_updated=datetime.now().isoformat(),
            schema_version=STATE_SCHEMA_VERSION,
        )

    def save(self, state: L1MetaLoopState) -> None:
        """持久化状态 → 写 state.duckdb（SSOT，UPSERT + 历史追加）。"""
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise MetaStateManagerError(
                f"状态 schema 版本不匹配: {state.get('schema_version')} != {STATE_SCHEMA_VERSION}"
            )
        state["last_updated"] = datetime.now().isoformat()
        ns, key = self._ns_key()
        self._store_conn().upsert(ns, key, state, run_id=state.get("run_id") or "")

    def mark_running(self, state: L1MetaLoopState) -> L1MetaLoopState:
        """标记为运行中。"""
        state["status"] = "running"
        state["last_error"] = None
        self.save(state)
        return state

    def mark_completed(self, state: L1MetaLoopState) -> L1MetaLoopState:
        """标记为已完成。"""
        state["status"] = "completed"
        self.save(state)
        return state

    def mark_paused(self, state: L1MetaLoopState, error: str) -> L1MetaLoopState:
        """标记为暂停（异常）。"""
        state["status"] = "paused"
        state["last_error"] = error
        self.save(state)
        return state

    def mark_circuit_broken(self, state: L1MetaLoopState, reason: str) -> L1MetaLoopState:
        """标记为熔断。"""
        state["status"] = "circuit_broken"
        state["last_error"] = reason
        self.save(state)
        return state


# ─── FactorPool 管理器 ──────────────────────────────────


class FactorPoolManager:
    """factor_pool.json 管理器 — L1 种子池索引。

    存储位置: memory/knowledge/factors/factor_pool.json
    """

    def __init__(self, factor_pool_path: str | Path = "memory/knowledge/factors/factor_pool.json"):
        self.factor_pool_path = Path(factor_pool_path)
        self._cache: Optional[FactorPool] = None

    def load_or_init(self) -> FactorPool:
        """加载或初始化 factor_pool.json。"""
        if self.factor_pool_path.exists():
            try:
                with open(self.factor_pool_path, "r", encoding="utf-8") as f:
                    pool = json.load(f)
                self._cache = pool
                return pool
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("factor_pool.json 损坏: %s, 冷启动", e)
        # 冷启动
        from .contracts import EVOLUTION_VERSION

        pool = FactorPool(
            version=EVOLUTION_VERSION,
            updated_at=datetime.now().isoformat(),
            factors=[],
            total_count=0,
            pending_count=0,
        )
        self.save(pool)
        return pool

    def save(self, pool: FactorPool) -> None:
        """持久化 factor_pool.json。"""
        self.factor_pool_path.parent.mkdir(parents=True, exist_ok=True)
        pool["updated_at"] = datetime.now().isoformat()
        pool["total_count"] = len(pool.get("factors", []))
        pool["pending_count"] = sum(1 for f in pool.get("factors", []) if f.get("status") == "pending")
        with open(self.factor_pool_path, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)
        self._cache = pool

    def add_entry(self, entry: FactorPoolEntry) -> None:
        """添加一条因子记录。"""
        pool = self._cache or self.load_or_init()
        factors = pool.setdefault("factors", [])
        # 候选因子入池时未评估（无 IC/Sharpe），显式标注 evaluation_status
        entry.setdefault("evaluation_status", "pending")
        # 去重（按 factor_id）
        for i, f in enumerate(factors):
            if f.get("factor_id") == entry["factor_id"]:
                factors[i] = entry  # 更新
                break
        else:
            factors.append(entry)
        self.save(pool)

    def list_pending(self) -> list[FactorPoolEntry]:
        """列出所有 pending 状态的因子。"""
        pool = self._cache or self.load_or_init()
        return [f for f in pool.get("factors", []) if f.get("status") == "pending"]

    def mark_status(self, factor_id: str, status: str) -> None:
        """更新因子状态。"""
        pool = self._cache or self.load_or_init()
        for f in pool.get("factors", []):
            if f.get("factor_id") == factor_id:
                f["status"] = cast(Any, status)
                f["updated_at"] = datetime.now().isoformat()
                break
        self.save(pool)

    def count(self) -> int:
        """返回因子总数。"""
        pool = self._cache or self.load_or_init()
        return len(pool.get("factors", []))


# ─── DebateRound 分析器 ─────────────────────────────────


class DebateQualityAnalyzer:
    """辩论质量分析器 — 读取 fdt_langgraph 辩论数据，识别论证薄弱维度。

    输入: memory/debates/ 或 memory/journal/debate_journal.json
    输出: list[dict] — 每个品种的薄弱维度标签

    薄弱维度定义:
        - bullish_arguments 长度 < bearish_arguments 长度 → "bullish_weak"
        - bearish_arguments 长度 < bullish_arguments 长度 → "bearish_weak"
        - debate_round < 2 → "insufficient_rounds"
        - 无 bullish/bearish → "no_debate"
    """

    DEBATE_DIMENSIONS = (
        "bullish_weak",  # 多头论证薄弱
        "bearish_weak",  # 空头论证薄弱
        "insufficient_rounds",  # 辩论轮次不足
        "no_debate",  # 无辩论数据
    )

    def __init__(self, debates_dir: str | Path = "memory/debates"):
        self.debates_dir = Path(debates_dir)

    def analyze_latest_debate(self) -> dict[str, Any]:
        """分析最近的辩论数据，返回薄弱维度字典。

        Returns:
            {
                "topics": [{"topic": str, "gap": str, "debate_round": int}],
                "summary": str,
                "analyzed_at": str
            }
        """
        result: dict[str, Any] = {
            "topics": [],
            "summary": "",
            "analyzed_at": datetime.now().isoformat(),
        }

        # 尝试从 debate_journal.json 加载
        journal_path = self.debates_dir.parent / "journal" / "debate_journal.json"
        if not journal_path.exists():
            journal_path = self.debates_dir.parent / "debate_journal.json"
        if not journal_path.exists():
            result["summary"] = "无辩论数据可用"
            return result

        try:
            with open(journal_path, "r", encoding="utf-8") as f:
                journal = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            result["summary"] = f"辩论数据加载失败: {e}"
            return result

        entries = journal.get("entries", [])
        if not entries:
            result["summary"] = "辩论日志为空"
            return result

        # 取最近 10 条 debate_record
        debate_records = [e for e in entries if e.get("action") == "debate_record"][-10:]

        for rec in debate_records:
            symbols = rec.get("symbols", {})
            if isinstance(symbols, dict):
                for sym, sym_data in symbols.items():
                    gap = self._detect_gap(sym_data)
                    if gap:
                        result["topics"].append(
                            {
                                "topic": sym,
                                "gap": gap,
                                "debate_round": sym_data.get("debate_round", 0) if isinstance(sym_data, dict) else 0,
                            }
                        )

        # 汇总
        if result["topics"]:
            gap_counts: dict[str, int] = {}
            for t in result["topics"]:
                gap_counts[t["gap"]] = gap_counts.get(t["gap"], 0) + 1
            summary_parts = [f"{gap}:{cnt}" for gap, cnt in gap_counts.items()]
            result["summary"] = f"识别 {len(result['topics'])} 个薄弱维度（" + ", ".join(summary_parts) + ")"
        else:
            result["summary"] = "无明显薄弱维度"

        return result

    @staticmethod
    def _detect_gap(sym_data: Any) -> Optional[str]:
        """检测单个品种的论证缺口。"""
        if not isinstance(sym_data, dict):
            return "no_debate"
        debate_round = sym_data.get("debate_round", 0)
        if debate_round < 2:
            return "insufficient_rounds"
        bullish_args = sym_data.get("bullish_arguments", [])
        bearish_args = sym_data.get("bearish_arguments", [])
        if not bullish_args and not bearish_args:
            return "no_debate"
        if len(bullish_args) < len(bearish_args):
            return "bullish_weak"
        if len(bearish_args) < len(bullish_args):
            return "bearish_weak"
        return None


# ─── Bootstrapping Agent 链 ─────────────────────────────


def validate_batch_candidates(
    candidates: list[SeedCandidate],
) -> dict[str, Any]:
    """GAP-I101 (v2.71.0): L1 批量候选契约校验。

    对 Bootstrapping 批量产出的候选逐条校验 SeedCandidate 必需字段
    （candidate_id/name/code/economic_logic.narrative），统计契约合规率，
    供批量候选吞吐指标监控与 L1 候选质量追踪。

    Args:
        candidates: Bootstrapping 产出的候选列表

    Returns:
        校验统计 {total, valid, invalid, invalid_samples}
    """
    required = ("candidate_id", "name", "code")
    invalid: list[dict[str, str]] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            invalid.append({"reason": "非 dict"})
            continue
        missing = [k for k in required if not cand.get(k)]
        econ = cand.get("economic_logic")
        if not isinstance(econ, dict) or not econ.get("narrative"):
            missing.append("economic_logic.narrative")
        if missing:
            invalid.append(
                {
                    "candidate_id": str(cand.get("candidate_id", "?")),
                    "missing": ",".join(missing),
                }
            )
    return {
        "total": len(candidates),
        "valid": len(candidates) - len(invalid),
        "invalid": len(invalid),
        "invalid_samples": invalid[:5],
    }


class BootstrappingChain:
    """factorengine Bootstrapping Agent 链 — 模拟版。

    生产环境将接入真实 LLM Agent 链:
        - 提取Agent (ExtractAgent) — 从研报/新闻提取因子想法
        - 验证Agent (ValidateAgent) — 经济逻辑评分
        - 代码生成Agent (CodeGenAgent) — 生成可执行因子代码

    Phase 2 v1.1.0 提供 Mock 实现，用于验证流程闭环。
    LLM 客户端可通过 llm_client 参数注入。
    """

    # 内置 Bootstrapping 模板（无 LLM 时的回退）
    _BOOTSTRAP_TEMPLATES: list[dict[str, Any]] = [
        {
            "name": "bbands_width_reversion",
            "parent_topic": "volatility_reversion 衍生",
            "code": """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 15))
    n = len(close)
    if n < window:
        return np.zeros(n)
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    std = np.array([np.std(close[max(0,i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
    bb_width = (2 * std) / np.maximum(ma, 1e-10)
    avg_width = np.mean(bb_width[window:]) if n > window else 0
    score = np.tanh((avg_width - bb_width) * 20)
    return np.clip(score, -1.0, 1.0)
""",
            "params": {"window": 15},
            "signature": FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=20,
            ),
            "economic_logic": EconomicLogic(
                theory=4,
                behavioral=4,
                microstructure=3,
                institutional=4,
                narrative="布林带宽度回归: 带宽收窄后扩张预期，捕捉波动率突破。",
            ),
        },
        {
            "name": "oi_price_divergence",
            "parent_topic": "量价背离因子",
            "code": """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 5))
    n = len(close)
    if n < window + 1:
        return np.zeros(n)
    vol_chg = np.zeros(n)
    vol_chg[1:] = (volume[1:] - volume[:-1]) / np.maximum(volume[:-1], 1e-10)
    px_chg = np.zeros(n)
    px_chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 量价背离: 放量但价跌 → 偏空；缩量但价涨 → 偏多
    divergence = np.where(
        (vol_chg > 0.3) & (px_chg < -0.005), -0.5,
        np.where((vol_chg < -0.3) & (px_chg > 0.005), 0.5, 0)
    )
    return np.clip(divergence, -1.0, 1.0)
""",
            "params": {"window": 5},
            "signature": FactorSignature(
                input_fields=["close", "volume"],
                output_type="signal",
                frequency="daily",
                lookback=10,
            ),
            "economic_logic": EconomicLogic(
                theory=4,
                behavioral=3,
                microstructure=5,
                institutional=4,
                narrative="持仓量与价格背离: OI 增+价跌反映空头主导，OI 减+价涨反映空头回补。",
            ),
        },
        {
            "name": "news_sentiment_proxy",
            "parent_topic": "f10 web_collector 新闻情绪衍生",
            "code": """
def factor_program(data, params):
    import numpy as np
    if 'news_sentiment' not in (data.columns if hasattr(data, 'columns') else data):
        return np.zeros(len(data['close']))
    sentiment = data['news_sentiment'].values if hasattr(data, 'news_sentiment') else data['news_sentiment']
    decay = float(params.get('decay', 0.3))
    n = len(sentiment)
    score = np.zeros(n)
    if n > 0:
        score[0] = sentiment[0]
        for i in range(1, n):
            score[i] = decay * sentiment[i] + (1 - decay) * score[i-1]
    return np.clip(score, -1.0, 1.0)
""",
            "params": {"decay": 0.3},
            "signature": FactorSignature(
                input_fields=["news_sentiment"],
                output_type="signal",
                frequency="daily",
                lookback=5,
            ),
            "economic_logic": EconomicLogic(
                theory=3,
                behavioral=5,
                microstructure=4,
                institutional=3,
                narrative="新闻情绪衰减代理: 捕捉新闻情绪的持续性，反映投资者反应不足。",
            ),
        },
    ]

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        web_collector: Optional[Callable[..., dict]] = None,
        extractor_pipeline: Optional[Any] = None,
        market: str = "futures",
    ):
        """
        Args:
            llm_client: LLM 客户端（必须实现 generate(prompt: str) -> str 接口）。
                        None 时使用内置模板回退。
            web_collector: f10/web_collector 的 collect_fundamental_web 函数。
                        None 时跳过感知步骤。
            extractor_pipeline: 三源提取器管道（如 FuturesExtractorPipeline），
                        在 LLM 候选后自动调用来补充候选。
            market: 市场类型（futures/energy；plans/41 D2 按子链分批依据）
        """
        self.llm_client = llm_client
        self.web_collector = web_collector
        self.extractor_pipeline = extractor_pipeline
        self.market = market

    def bootstrap(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int = 5,
        seed_pool: Optional[SeedPool] = None,
        trace_id: Optional[str] = None,
        extra_existing_names: Optional[set[str]] = None,
    ) -> list[SeedCandidate]:
        """执行 Bootstrapping，返回候选因子列表。

        优先级顺序: 提取器 → LLM → 内置模板。
        三源提取器（broker_reports/academic_papers/tinysoft）优先填充候选池，
        未满配额由 LLM 补充，最后以内置模板兜底。

        Args:
            market_snapshot: f10/web_collector 拉取的市场快照
            debate_gaps: DebateQualityAnalyzer 识别的薄弱维度
            max_candidates: 最大候选数
            seed_pool: 现有种子池（用于去重判断）
            trace_id: 全链路 trace_id
            extra_existing_names: 额外已注入因子名（小写），用于冷启动后去重

        Returns:
            list[SeedCandidate] — 通过沙箱编译的候选因子
        """
        trace_id = trace_id or generate_trace_id("l1")
        candidates: list[SeedCandidate] = []
        existing_names = {n.lower() for n in (seed_pool or SeedPool()).list_names()}
        if not seed_pool:
            logger.warning(
                "[bootstrap] seed_pool 未提供, 使用默认 SeedPool() (市场=futures), existing_count=%d",
                len(existing_names),
            )
        if extra_existing_names:
            existing_names |= extra_existing_names
        logger.info(
            "[bootstrap] 开始, trace_id=%s, max_candidates=%d, existing_seed_names=%d, extra_names=%d",
            trace_id,
            max_candidates,
            len(existing_names),
            len(extra_existing_names or set()),
        )

        # 1. 提取器优先: 先调用三源提取器生成候选，过滤掉与种子池重复的
        extractor_count = 0
        if self.extractor_pipeline is not None:
            try:
                raw_extractor_candidates = self.extractor_pipeline.extract(trace_id)
                if raw_extractor_candidates:
                    # 过滤掉与现有种子重复的提取器候选
                    non_dup_extractor: list[SeedCandidate] = []
                    for cand in raw_extractor_candidates:
                        cand_name = cand.get("name", "")
                        if cand_name.lower() in existing_names:
                            logger.info(
                                "[bootstrap] 提取器候选跳过(重复): name=%s, source=%s",
                                cand_name,
                                cand.get("source", "unknown"),
                            )
                            continue
                        non_dup_extractor.append(cand)
                    candidates.extend(non_dup_extractor)
                    extractor_count = len(non_dup_extractor)
                    logger.info(
                        "[bootstrap] 提取器管道候选已加入, trace_id=%s, raw=%d, filtered=%d, total=%d",
                        trace_id,
                        len(raw_extractor_candidates),
                        extractor_count,
                        len(candidates),
                    )
                else:
                    logger.info(
                        "[bootstrap] 提取器管道返回空, trace_id=%s",
                        trace_id,
                    )
            except Exception as e:
                logger.error(
                    "[bootstrap] 提取器管道异常: %s, trace_id=%s, 跳过",
                    e,
                    trace_id,
                    exc_info=True,
                )

        # 2. LLM 补足剩余配额（提取器未填满的部分）
        llm_needed = max_candidates - len(candidates)
        if self.llm_client is not None and llm_needed > 0:
            # plans/44 B1: 注入已注入因子名负面样本（≤20 个，控制 token），
            # 引导 LLM 生成非重复机制；energy 子链分批经 dict 拷贝自然透传。
            market_snapshot["negative_factor_names"] = sorted(existing_names)[:20]
            llm_candidates = self._bootstrap_with_llm(market_snapshot, debate_gaps, llm_needed, trace_id)
            candidates.extend(llm_candidates)
            logger.info(
                "[bootstrap] LLM 候选已加入, trace_id=%s, llm_count=%d, total=%d",
                trace_id,
                len(llm_candidates),
                len(candidates),
            )
        else:
            logger.info(
                "[bootstrap] LLM 跳过, trace_id=%s, llm_needed=%d, llm_client=%s",
                trace_id,
                llm_needed,
                bool(self.llm_client),
            )

        # 3. 如果候选数仍不足，从内置模板补充
        if len(candidates) < max_candidates:
            needed = max_candidates - len(candidates)
            template_candidates = self._bootstrap_from_templates(
                market_snapshot,
                debate_gaps,
                needed,
                existing_names,
                trace_id,
            )
            candidates.extend(template_candidates)
            logger.info(
                "[bootstrap] 模板候选已加入, trace_id=%s, needed=%d, template_count=%d, total=%d",
                trace_id,
                needed,
                len(template_candidates),
                len(candidates),
            )
        else:
            logger.info(
                "[bootstrap] 候选数已满足, 跳过模板补充, trace_id=%s, current=%d, max=%d",
                trace_id,
                len(candidates),
                max_candidates,
            )

        # 4. 限制数量
        candidates = candidates[:max_candidates]

        # plans/44 C4: 语义去重（l1_semantic_dedup 开关）— 候选名 vs 已注入名
        # embedding 高相似（> l1_dedup_threshold）拦截同构不同名；模型缺失自动
        # 降级为精确名称匹配（与既有行为等价，零回归）。
        semantic_blocked: set[str] = set()
        try:
            from fts.config.settings import get_config

            if getattr(get_config(), "l1_semantic_dedup", True) and candidates and existing_names:
                cand_texts = [c.get("name", "") for c in candidates]
                existing_texts = sorted(existing_names)[:200]
                flags = dedup_semantic(
                    cand_texts,
                    existing_texts,
                    threshold=float(getattr(get_config(), "l1_dedup_threshold", 0.90)),
                    embedder=TextEmbedder(),
                )
                # 按名称匹配（候选 candidate_id 可能被下游重建，名称稳定）
                for c, allowed in zip(candidates, flags):
                    if not allowed and c.get("name"):
                        semantic_blocked.add(str(c.get("name", "")).lower())
                if semantic_blocked:
                    logger.warning(
                        "[bootstrap] 语义去重拦截: %d 个候选与已注入因子语义高相似, names=%s",
                        len(semantic_blocked),
                        sorted(semantic_blocked),
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("[bootstrap] 语义去重异常, 跳过（不阻断）: %s", e)

        # 5. 编译验证 + 自动修复 + 去重标记
        validated: list[SeedCandidate] = []
        dup_count = 0
        fail_count = 0
        auto_fix_count = 0
        for cand in candidates:
            cand_name = cand.get("name", "unknown")
            cand_id = cand.get("candidate_id", "unknown")
            # 编译验证 — validate_factor_code 返回 (passed, reasons) tuple
            try:
                ok, reasons = validate_factor_code(cand["code"])
                if ok:
                    cand["is_executable"] = True
                else:
                    # 尝试自动修复
                    error_reason = "; ".join(reasons)
                    fixed, fixed_code = fix_factor_code(cand["code"], error_reason)
                    if fixed:
                        ok2, reasons2 = validate_factor_code(fixed_code)
                        if ok2:
                            cand["is_executable"] = True
                            cand["code"] = fixed_code
                            auto_fix_count += 1
                            cand.setdefault("failure_reasons", []).append(f"自动修复成功: 原错误={error_reason}")
                            logger.info(
                                "[bootstrap] 自动修复成功, name=%s, candidate_id=%s, error=%s",
                                cand_name,
                                cand_id,
                                error_reason,
                            )
                        else:
                            cand["is_executable"] = False
                            cand.setdefault("failure_reasons", []).append(f"编译失败: {error_reason}")
                    else:
                        # plans/44 C1: 规则修复失败 → LLM 修复兜底
                        llm_fixed = self._try_llm_code_fix(cand["code"], error_reason, trace_id)
                        if llm_fixed is not None:
                            ok3, reasons3 = validate_factor_code(llm_fixed)
                            if ok3:
                                cand["is_executable"] = True
                                cand["code"] = llm_fixed
                                auto_fix_count += 1
                                cand.setdefault("failure_reasons", []).append(f"LLM 修复成功: 原错误={error_reason}")
                                logger.info(
                                    "[bootstrap] LLM 修复成功, name=%s, candidate_id=%s, error=%s",
                                    cand_name,
                                    cand_id,
                                    error_reason,
                                )
                            else:
                                cand["is_executable"] = False
                                cand.setdefault("failure_reasons", []).append(f"编译失败: LLM 修复仍无效 ({error_reason})")
                        else:
                            cand["is_executable"] = False
                            cand.setdefault("failure_reasons", []).append(f"编译失败: {error_reason}")
            except Exception as e:
                cand["is_executable"] = False
                cand.setdefault("failure_reasons", []).append(f"编译异常: {e}")

            # 去重判断（名称精确匹配 + plans/44 C4 语义高相似拦截）
            cand["is_duplicate"] = (
                cand.get("name", "").lower() in existing_names or cand_name.lower() in semantic_blocked
            )
            if cand["is_duplicate"]:
                dup_count += 1
                logger.warning(
                    "[bootstrap] 重复候选, trace_id=%s, candidate_id=%s, name=%s",
                    trace_id,
                    cand_id,
                    cand_name,
                )
            if not cand["is_executable"]:
                fail_count += 1
            validated.append(cand)

        logger.info(
            "[bootstrap] 完成, trace_id=%s, total=%d, validated=%d, duplicates=%d, failed_compile=%d, auto_fixed=%d",
            trace_id,
            len(candidates),
            len(validated),
            dup_count,
            fail_count,
            auto_fix_count,
        )
        return validated

    def _try_llm_code_fix(self, code: str, error_reason: str, trace_id: str) -> Optional[str]:
        """plans/44 C1: 规则修复失败后调用 LLM 修复因子代码。

        返回 LLM 修复后的代码（未经过 validate 复核，由调用方复核）；
        LLM 不可用/无 fix_factor_code 接口/调用异常 → None。
        """
        llm = self.llm_client
        if llm is None or not hasattr(llm, "fix_factor_code"):
            return None
        try:
            fixed = llm.fix_factor_code(code, error_reason, trace_id)
            return fixed if isinstance(fixed, str) and fixed.strip() else None
        except Exception as e:
            logger.warning(
                "[bootstrap] LLM 代码修复异常, trace_id=%s, error=%s",
                trace_id,
                e,
            )
            return None

    def _bootstrap_with_llm(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        trace_id: str,
    ) -> list[SeedCandidate]:
        """用 LLM 生成候选因子（plans/41 D2: energy 市场按四子链分批）。"""
        import time

        t0 = time.time()
        logger.info(
            "[_bootstrap_with_llm] 开始, trace_id=%s, max_candidates=%d, debate_gaps=%d, has_bootstrap=%s",
            trace_id,
            max_candidates,
            len(debate_gaps),
            hasattr(self.llm_client, "bootstrap_factors"),
        )
        try:
            llm_client = self.llm_client
            if llm_client is None or not hasattr(llm_client, "bootstrap_factors"):
                logger.info(
                    "[_bootstrap_with_llm] 客户端无 bootstrap_factors 方法, 跳过, trace_id=%s",
                    trace_id,
                )
                return []
            raw_candidates: list[dict[str, Any]] = []
            if self.market == "energy" and max_candidates >= 8:
                # D2: 按四大化工子链分批（每批独立 chain_focus，提升多样性与总量）
                batches = self._energy_subchain_batches(max_candidates)
                for focus, per_batch in batches:
                    batch_snapshot = dict(market_snapshot)
                    batch_snapshot["chain_focus"] = focus
                    batch_raw = llm_client.bootstrap_factors(
                        batch_snapshot,
                        debate_gaps,
                        per_batch,
                        trace_id,
                    )
                    raw_candidates.extend(batch_raw or [])
                    logger.info(
                        "[_bootstrap_with_llm] 子链批完成: focus=%s, per_batch=%d, got=%d, total=%d, trace_id=%s",
                        focus,
                        per_batch,
                        len(batch_raw or []),
                        len(raw_candidates),
                        trace_id,
                    )
                raw_candidates = raw_candidates[:max_candidates]
            else:
                raw_candidates = llm_client.bootstrap_factors(
                    market_snapshot,
                    debate_gaps,
                    max_candidates,
                    trace_id,
                )
            elapsed = (time.time() - t0) * 1000
            logger.info(
                "[_bootstrap_with_llm] LLM 返回, trace_id=%s, elapsed_ms=%.1f, raw_count=%d",
                trace_id,
                elapsed,
                len(raw_candidates),
            )
            if not raw_candidates:
                logger.warning(
                    "[_bootstrap_with_llm] LLM 返回空候选列表, trace_id=%s",
                    trace_id,
                )
                return []
            candidates: list[SeedCandidate] = []
            skipped = 0
            # plans/44 C3: narrative < 20 字用模板补全（零额外 LLM 调用），
            # 降低软失败损耗；与提取器路径 _ensure_narrative 行为一致。
            from .extractors.base import _ensure_narrative

            for i, raw in enumerate(raw_candidates):
                name = raw.get("name", "unknown")
                code = raw.get("code", "")
                if not code:
                    skipped += 1
                    logger.warning(
                        "[_bootstrap_with_llm] 候选 %d 缺少 code, 跳过, trace_id=%s, name=%s",
                        i,
                        trace_id,
                        name,
                    )
                    continue
                raw_id = f"{name}|{secrets.token_hex(8)}"
                cand_id = "cand_" + hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:8]
                cand = SeedCandidate(
                    candidate_id=cand_id,
                    name=name,
                    code=code,
                    params=raw.get("params", {}),
                    signature=raw.get("signature", {}),
                    economic_logic=_ensure_narrative(name, raw.get("economic_logic", {})),
                    source=raw.get("source", "l1_bootstrapping"),
                    parent_topic=raw.get("parent_topic", ""),
                    debate_round_ref=None,
                    debate_gap=None,
                    web_snapshot_ref=market_snapshot.get("trace_id"),
                    is_executable=False,
                    is_duplicate=False,
                    passed_l1_verifier=False,
                    failure_reasons=[],
                    trace_id=trace_id,
                    created_at=datetime.now().isoformat(),
                    injected_to_l2=False,
                    injected_at=None,
                )
                candidates.append(cand)
            elapsed_total = (time.time() - t0) * 1000
            logger.info(
                "[_bootstrap_with_llm] 完成, trace_id=%s, total_ms=%.1f, candidates=%d, skipped=%d, names=%s",
                trace_id,
                elapsed_total,
                len(candidates),
                skipped,
                [c["name"] for c in candidates],
            )
            return candidates
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            logger.error(
                "[_bootstrap_with_llm] 异常, trace_id=%s, elapsed_ms=%.1f, error=%s",
                trace_id,
                elapsed,
                e,
                exc_info=True,
            )
            return []

    def _energy_subchain_batches(self, max_candidates: int) -> list[tuple[str, int]]:
        """构建 energy 市场按子链分批的 (聚焦子链, 每批候选数) 列表（plans/41 D2）。

        四大化工子链各一批，按训练池品种数比例分配配额（至少 2/批，保证每批 prompt
        有独立子链语境）。子链划分失败时回退单批（向后兼容）。
        """
        try:
            from fts.data_futures import ENERGY_CHAIN_SYMBOLS, FUTURES_SECTOR_MAP

            subchains = ["能源", "聚酯链", "油化工", "煤化工"]
            members = {
                sc: sorted(set(FUTURES_SECTOR_MAP.get(sc, [])) & set(ENERGY_CHAIN_SYMBOLS))
                for sc in subchains
            }
            non_empty = [(sc, m) for sc, m in members.items() if m]
            if len(non_empty) < 2:
                return [("", max_candidates)]
            total = sum(len(m) for _, m in non_empty)
            batches: list[tuple[str, int]] = []
            allocated = 0
            for i, (sc, m) in enumerate(non_empty):
                if i == len(non_empty) - 1:
                    per_batch = max_candidates - allocated
                else:
                    per_batch = max(2, round(max_candidates * len(m) / total))
                batches.append((f"{sc}({','.join(m)})", per_batch))
                allocated += per_batch
            return batches
        except Exception:  # noqa: BLE001
            return [("", max_candidates)]

    def _bootstrap_from_templates(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        existing_names: set[str],
        trace_id: str,
    ) -> list[SeedCandidate]:
        """从内置模板生成候选因子。"""
        candidates: list[SeedCandidate] = []
        # 根据 debate_gap 优先选择模板
        gap_types = {g.get("gap") for g in debate_gaps if g.get("gap")}

        # 模板优先级：与 debate_gap 相关的优先
        sorted_templates = sorted(
            self._BOOTSTRAP_TEMPLATES,
            key=lambda t: (
                0 if "weak" in str(t.get("parent_topic", "")).lower() else 1,
                0 if any(g in str(t.get("parent_topic", "")).lower() for g in gap_types) else 1,
            ),
        )

        for tmpl in sorted_templates:
            if len(candidates) >= max_candidates:
                break
            if tmpl["name"].lower() in existing_names:
                logger.debug(
                    "[_bootstrap_from_templates] 模板跳过(名称已存在), name=%s",
                    tmpl["name"],
                )
                continue

            # 生成唯一 candidate_id
            raw = f"{tmpl['name']}|{secrets.token_hex(8)}"
            cand_id = "cand_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

            # 关联 debate_gap
            debate_gap_ref: Optional[str] = None
            debate_round_ref: Optional[int] = None
            for g in debate_gaps:
                if g.get("gap") and g.get("gap") in str(tmpl.get("parent_topic", "")).lower():
                    debate_gap_ref = g.get("gap")
                    debate_round_ref = g.get("debate_round")
                    break

            candidates.append(
                SeedCandidate(
                    candidate_id=cand_id,
                    name=tmpl["name"],
                    code=tmpl["code"],
                    params=tmpl["params"],
                    signature=tmpl["signature"],
                    economic_logic=tmpl["economic_logic"],
                    source="l1_bootstrapping" if not debate_gap_ref else "l1_debate_gap",
                    parent_topic=tmpl["parent_topic"],
                    debate_round_ref=debate_round_ref,
                    debate_gap=debate_gap_ref,
                    web_snapshot_ref=market_snapshot.get("trace_id") if market_snapshot else None,
                    is_executable=False,  # 待 bootstrap() 中验证
                    is_duplicate=False,
                    passed_l1_verifier=False,
                    failure_reasons=[],
                    trace_id=trace_id,
                    created_at=datetime.now().isoformat(),
                    injected_to_l2=False,
                    injected_at=None,
                )
            )

        return candidates


# ─── MetaLoop 运行结果 ──────────────────────────────────


@dataclass
class MetaRunResult:
    """单次 L1 Meta-Loop 运行的结果。"""

    run_id: str
    trace_id: str
    candidates_generated: int
    candidates_injected: int
    debate_gaps_detected: int
    tokens_consumed: int
    status: str  # running / paused / completed / circuit_broken
    circuit_breaker_reason: Optional[str] = None
    injected_candidate_ids: list[str] = field(default_factory=list)
    # GAP-I101 (v2.71.0): L1 候选吞吐指标（候选/分钟）
    candidates_per_minute: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "candidates_generated": self.candidates_generated,
            "candidates_injected": self.candidates_injected,
            "debate_gaps_detected": self.debate_gaps_detected,
            "tokens_consumed": self.tokens_consumed,
            "status": self.status,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "injected_candidate_ids": self.injected_candidate_ids or [],
            "candidates_per_minute": self.candidates_per_minute,
        }


# ─── L1 Meta-Loop 主循环 ────────────────────────────────


def _derive_rejected_dir(inject_dir: str | Path) -> Path:
    """由 inject_dir 派生拒绝候选目录：l1_injected → l1_rejected；l1_injected_energy → l1_rejected_energy。"""
    inject_path = Path(inject_dir)
    name = inject_path.name
    if name.startswith("l1_injected"):
        return inject_path.parent / f"l1_rejected{name[len('l1_injected'):]}"
    return inject_path.parent / "l1_rejected"


class MetaLoop:
    """L1 Meta-Loop 主循环 — 每日 05:00 知识补给。

    Usage:
        loop = MetaLoop(
            memory_dir="memory/meta_loop",
            factor_pool_path="memory/knowledge/factors/factor_pool.json",
        )
        result = loop.run()
    """

    def __init__(
        self,
        memory_dir: str | Path = "memory/meta_loop",
        factor_pool_path: str | Path = "memory/knowledge/factors/factor_pool.json",
        inject_dir: str | Path = "memory/knowledge/factors/l1_injected",
        rejected_dir: str | Path | None = None,
        debates_dir: str | Path = "memory/debates",
        budget: Optional[L1BudgetConfig] = None,
        verifier: Optional[L1Verifier] = None,
        llm_client: Optional[Any] = None,
        web_collector: Optional[Callable[..., dict]] = None,
        seed_pool: Optional[SeedPool] = None,
        sample_symbols: Optional[list[str]] = None,
        market: str = "futures",
        state_store: Any | None = None,
    ):
        """
        Args:
            memory_dir: L1 状态目录
            factor_pool_path: factor_pool.json 路径
            inject_dir: L1 注入因子存储目录
            rejected_dir: L1 拒绝候选存储目录（None 时由 inject_dir 派生:
                        l1_injected → l1_rejected；l1_injected_energy → l1_rejected_energy）
            debates_dir: 辩论数据目录
            budget: L1 预算配置
            verifier: L1 Verifier（None 时用默认）
            llm_client: LLM 客户端
            web_collector: f10/web_collector 函数
            seed_pool: 现有种子池
            sample_symbols: 感知层抽样品种（None 时用默认 3 个）
            market: 市场类型（默认 "futures"）
            state_store: 可选状态存储（StateKVStore），缺省用全局 SSOT（供测试隔离）
        """
        self.memory_dir = Path(memory_dir)
        self.factor_pool_path = Path(factor_pool_path)
        self.inject_dir = Path(inject_dir)
        self.rejected_dir = _derive_rejected_dir(inject_dir) if rejected_dir is None else Path(rejected_dir)
        self.debates_dir = Path(debates_dir)
        self.budget: L1BudgetConfig = dict(budget or DEFAULT_L1_BUDGET_CONFIG)  # type: ignore[assignment]
        self.verifier = verifier or L1Verifier(DEFAULT_L1_VERIFIER_CONFIG)
        self.llm_client = llm_client
        self.web_collector = web_collector
        self.market = market
        self._state_store = state_store

        # ── 感知层默认样本：期货五大板块品种 / 能源链专属品种 ──
        if sample_symbols:
            effective_symbols = sample_symbols
        elif market == "energy":
            # 能源产业链专属工作流（GAP-121）：默认感知能化链 9 训练品种
            from fts.data_futures import ENERGY_CHAIN_SYMBOLS

            effective_symbols = [s[:-1].lower() if s.endswith("0") else s.lower() for s in ENERGY_CHAIN_SYMBOLS]
            logger.info(
                "[L1.init] energy 市场默认感知品种: %s",
                effective_symbols,
            )
        else:
            effective_symbols = [
                "rb",
                "i",
                "j",
                "hc",  # 黑色系
                "au",
                "ag",
                "cu",  # 有色金属
                "sc",
                "ta",
                "ma",  # 化工
                "m",
                "a",
                "y",  # 农产品
            ]  # 默认抽样 13 个期货品种，覆盖五大板块

        # ── 日志: 市场类型与种子池初始化 ──
        logger.info(
            "[L1.init] market=%s, seed_pool_mode=%s, sample_symbols=%s",
            market,
            market,
            effective_symbols,
        )
        if market == "futures":
            logger.info(
                "[L1.init] 期货知识注入模式: 将加载 81 个期货专用种子因子",
            )
        elif market == "energy":
            logger.info(
                "[L1.init] 能源链知识注入模式（GAP-121）: 混入加载通用期货种子 + 能化专属种子",
            )
        else:
            logger.warning("[L1.init] 未知市场类型: %s", market)

        self.seed_pool = seed_pool or SeedPool(market=market)
        seed_count = len(self.seed_pool.load_all_seeds())
        logger.info(
            "[L1.init] SeedPool 初始化完成: market=%s, total_seeds=%d, seed_names=%s",
            market,
            seed_count,
            self.seed_pool.list_names()[:5],
        )

        self.sample_symbols = effective_symbols

        self.state_manager = MetaStateManager(self.memory_dir, state_store=self._state_store)
        self.factor_pool_manager = FactorPoolManager(self.factor_pool_path)
        self.debate_analyzer = DebateQualityAnalyzer(self.debates_dir)

        # ── 根据市场类型创建对应的提取器管道（GAP-I103 多路知识源）──
        self._extractor_pipeline: Optional[FuturesExtractorPipeline] = None
        # 另类知识源开关（公告/舆情 + 宏观事件，FTS_L1_*_EXTRACTOR_ENABLED 可配）
        from fts.config.settings import get_config

        _cfg = get_config()
        _macro_enabled = bool(getattr(_cfg, "l1_macro_extractor_enabled", True))
        if market in ("futures", "energy"):
            self._extractor_pipeline = FuturesExtractorPipeline(
                llm_client=self.llm_client,
                macro_enabled=_macro_enabled,
            )
            logger.info(
                "[L1.init] %s 提取器管道已就绪: 天软/研报/论文/宏观(macro_enabled=%s)",
                "能源链" if market == "energy" else "期货",
                _macro_enabled,
            )
        else:
            self._extractor_pipeline = None
            logger.warning(
                "[L1.init] 未知市场类型 %s，跳过提取器管道",
                market,
            )

        self.bootstrap_chain = BootstrappingChain(
            llm_client=self.llm_client,
            web_collector=self.web_collector,
            extractor_pipeline=self._extractor_pipeline,
            market=market,
        )

        # 熔断计数器
        self._consecutive_low_quality: int = 0

    def run(self, max_bootstraps: Optional[int] = None) -> MetaRunResult:
        """执行一次 L1 Meta-Loop。

        Args:
            max_bootstraps: 本次最大 Bootstrapping 数（None 时用预算配置）

        Returns:
            MetaRunResult — 运行结果
        """
        trace_id = generate_trace_id("l1")
        max_cand = max_bootstraps or self.budget["max_bootstraps_per_run"]
        budget_limit = self.budget["daily_token_limit"]
        _run_started = time.monotonic()  # GAP-I101 (v2.71.0): 吞吐计时

        # 加载/初始化状态
        state = self.state_manager.load_or_init(budget_limit)
        # 重置 token 消耗（每日预算，新运行应从零开始）
        state["tokens_consumed"] = 0
        state = self.state_manager.mark_running(state)
        run_id = state["run_id"]

        logger.info(
            "🧠 L1 Meta-Loop 启动 (run_id=%s, trace_id=%s, market=%s, max_cand=%d, budget_limit=%d)",
            run_id,
            trace_id,
            self.market,
            max_cand,
            budget_limit,
        )
        logger.info(
            "[L1.run] 种子池状态: total=%d, names=%s",
            len(self.seed_pool.list_names()),
            self.seed_pool.list_names()[:10],
        )
        logger.info(
            "[L1.run] 注入目录: %s, 种子池路径: %s",
            self.inject_dir,
            self.factor_pool_path,
        )

        injected_ids: list[str] = []
        candidates_generated = 0
        debate_gaps_detected = 0
        tokens_consumed = 0

        try:
            # ─── Step 1: agentic 感知 (f10/web_collector) ──
            logger.info("[L1.run] Step 1: agentic 感知, sample_symbols=%s", self.sample_symbols)
            market_snapshot = self._perceive_market(trace_id)
            snapshot_count = len(market_snapshot.get("snapshots", {}))
            logger.info(
                "[L1.run] Step 1 完成: snapshots=%d, skipped=%s",
                snapshot_count,
                market_snapshot.get("skipped", False),
            )

            # ─── Step 2: debate_round 分析 ──────────────────
            logger.info("[L1.run] Step 2: debate_round 分析")
            debate_gaps, debate_gaps_detected = self._analyze_debate(state)
            logger.info(
                "[L1.run] Step 2 完成: gaps=%d, gap_topics=%s",
                debate_gaps_detected,
                [g.get("topic", "") for g in debate_gaps[:3]],
            )

            # ─── Step 2.5: 扫描已注入因子（冷启动去重） ────
            extra_existing_names = self._scan_injected_names()
            if extra_existing_names:
                logger.info(
                    "[L1.run] Step 2.5: 扫描到 %d 个历史注入因子名用于去重",
                    len(extra_existing_names),
                )
            else:
                logger.info("[L1.run] Step 2.5: 无历史注入因子，跳过去重")

            # ─── Step 2.75: 拒绝候选复活（plans/44 C2） ──
            retried = self._retry_rejected_candidates(state, trace_id, injected_ids)
            logger.info("[L1.run] Step 2.75 完成: 复活注入=%d", retried)

            # ─── Step 3: factorengine Bootstrapping ─────────
            logger.info(
                "[L1.run] Step 3: Bootstrapping 开始, max_cand=%d, debate_gaps=%d, existing_names=%d",
                max_cand,
                len(debate_gaps),
                len(extra_existing_names or set()),
            )
            candidates, candidates_generated = self._run_bootstrap(
                market_snapshot,
                debate_gaps,
                max_cand,
                trace_id,
                state,
                extra_existing_names=extra_existing_names,
            )
            logger.info(
                "[L1.run] Step 3 完成: candidates_generated=%d, candidate_names=%s",
                candidates_generated,
                [c.get("name", "") for c in candidates[:5]],
            )

            # ─── Step 4: L1 Verifier + 注入 ────────────────
            logger.info(
                "[L1.run] Step 4: Verifier + 注入开始, candidates=%d",
                len(candidates),
            )
            circuit_broken = self._verify_and_inject(
                candidates,
                state,
                trace_id,
                injected_ids,
            )
            # plans/44 D1: L1→L2 闭环 — 本次注入数回写漏斗（无论后续熔断与否）
            try:
                funnel_record(market=self.market, injected=len(injected_ids), run_id=trace_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[L1.run] 漏斗回写失败（不阻断）: %s", e)
            if circuit_broken:
                logger.warning(
                    "[L1.run] Step 4 熔断触发: reason=%s, injected=%d",
                    circuit_broken,
                    len(injected_ids),
                )
                return self._make_result(
                    run_id,
                    trace_id,
                    candidates_generated,
                    len(injected_ids),
                    debate_gaps_detected,
                    tokens_consumed,
                    status="circuit_broken",
                    circuit_breaker_reason=circuit_broken,
                    injected_ids=injected_ids,
                    elapsed_seconds=time.monotonic() - _run_started,
                )

            # 估算 token 消耗
            tokens_consumed = self._estimate_tokens(candidates_generated, debate_gaps_detected)
            state["tokens_consumed"] = tokens_consumed

            # ─── 完成 ─────────────────────────────────────
            state = self.state_manager.mark_completed(state)
            logger.info(
                "✅ L1 Meta-Loop 完成 (run_id=%s, market=%s): 生成 %d, 注入 %d, tokens=%d, injected_ids=%s",
                run_id,
                self.market,
                candidates_generated,
                len(injected_ids),
                tokens_consumed,
                injected_ids,
            )
            return self._make_result(
                run_id,
                trace_id,
                candidates_generated,
                len(injected_ids),
                debate_gaps_detected,
                tokens_consumed,
                status="completed",
                injected_ids=injected_ids,
                elapsed_seconds=time.monotonic() - _run_started,
            )

        except Exception as e:
            logger.error(
                "❌ L1 Meta-Loop 异常 (run_id=%s, market=%s): %s",
                run_id,
                self.market,
                e,
                exc_info=True,
            )
            state = self.state_manager.mark_paused(state, str(e))
            return self._make_result(
                run_id,
                trace_id,
                candidates_generated,
                len(injected_ids),
                debate_gaps_detected,
                tokens_consumed,
                status="paused",
                circuit_breaker_reason=str(e),
                injected_ids=injected_ids,
                elapsed_seconds=time.monotonic() - _run_started,
            )

    def _analyze_debate(self, state: L1MetaLoopState) -> tuple[list[dict[str, Any]], int]:
        """Step 2: 分析辩论记录，识别薄弱维度。"""
        debate_analysis = self.debate_analyzer.analyze_latest_debate()
        debate_gaps = debate_analysis.get("topics", [])
        debate_gaps_detected = len(debate_gaps)
        state["total_debate_gaps_detected"] = state.get("total_debate_gaps_detected", 0) + debate_gaps_detected
        logger.info("L1 Step 2: 辩论分析完成，识别 %d 个薄弱维度", debate_gaps_detected)
        return debate_gaps, debate_gaps_detected

    def _run_bootstrap(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_cand: int,
        trace_id: str,
        state: L1MetaLoopState,
        extra_existing_names: Optional[set[str]] = None,
    ) -> tuple[list[SeedCandidate], int]:
        """Step 3: 执行 Bootstrapping 生成候选因子。"""
        candidates = self.bootstrap_chain.bootstrap(
            market_snapshot=market_snapshot,
            debate_gaps=debate_gaps,
            max_candidates=max_cand,
            seed_pool=self.seed_pool,
            trace_id=trace_id,
            extra_existing_names=extra_existing_names,
        )
        candidates_generated = len(candidates)
        # GAP-I101 (v2.71.0): 批量候选契约校验（吞吐指标前置质量门）
        contract_stats = validate_batch_candidates(candidates)
        if contract_stats["invalid"]:
            logger.warning(
                "[L1.batch] 批量候选契约校验: total=%d valid=%d invalid=%d samples=%s",
                contract_stats["total"],
                contract_stats["valid"],
                contract_stats["invalid"],
                contract_stats["invalid_samples"],
            )
        else:
            logger.info(
                "[L1.batch] 批量候选契约校验通过: total=%d (GAP-I101)",
                contract_stats["total"],
            )
        # P2（2026-08-13）: total_candidates_generated 不再在此提前累计——避免失败率熔断
        # 把"已生成未验证"的候选计入分母；改由 _verify_and_inject 在整批验证后统一累计。
        state["last_bootstrap_topic"] = candidates[0].get("parent_topic", "") if candidates else ""
        return candidates, candidates_generated

    def _scan_injected_names(self) -> set[str]:
        """扫描 factor_pool.json，返回本市场已注入因子名称（小写）。

        GAP-I306 修复: 原实现扫描 l1_injected/ 目录文件——该目录会被 L2 演化
        按 GAP-036 消费后删除，导致 Step 2.5 去重事实源丢失（目录空即永远
        扫不到历史注入名）。改读 factor_pool.json（SSOT 索引，消费后仍保留）。
        market 缺失的历史记录纳入（宁多勿漏），仅排除明确属于其他市场的记录。
        """
        pool = self.factor_pool_manager.load_or_init()
        names: set[str] = set()
        for entry in pool.get("factors", []):
            name = entry.get("name", "")
            if not name:
                continue
            market = entry.get("market")
            if market and market != self.market:
                continue
            names.add(name.lower())
        return names

    def _verify_and_inject(
        self,
        candidates: list[SeedCandidate],
        state: L1MetaLoopState,
        trace_id: str,
        injected_ids: list[str],
    ) -> Optional[str]:
        """Step 4: L1 Verifier 检查 + 注入候选因子。返回熔断原因或 None。

        P2 修复（误熔断，2026-08-13）: 失败率熔断按"已实际验证的候选数"计算——
        循环内传入已处理数 i 与本批已注入数 batch_injected，整批验证完成后做
        最终失败率检查。原实现传入"本批总数"，在验证开始前以 0 注入误判 100%
        失败率，导致整批候选 0 验证 0 注入。
        """
        cfg = self.verifier._config  # noqa: SLF001 — L1 Verifier 配置引用
        logger.info(
            "[L1.verify] 开始验证注入: candidates=%d, verifier=min_economic=%s, require_exec=%s, require_nodup=%s",
            len(candidates),
            cfg.get("min_economic_score", "?"),
            cfg.get("require_executable", "?"),
            cfg.get("require_not_duplicate", "?"),
        )
        passed_count = 0
        rejected_count = 0
        batch_injected = 0  # 本次运行已注入数（失败率熔断分母/分子修正）
        for i, cand in enumerate(candidates):
            cand_name = cand.get("name", "unknown")
            cand_id = cand.get("candidate_id", "unknown")

            # 失败率熔断仅统计"已验证数 i"，未验证候选不计入分母（避免 0 注入误熔断）
            cb_reason = self._check_circuit_breaker(state, i, batch_injected)
            if cb_reason:
                logger.warning(
                    "[L1.verify] 熔断触发: reason=%s, 已处理=%d/%d, 剩余跳过",
                    cb_reason,
                    i,
                    len(candidates),
                )
                state["total_candidates_generated"] = state.get("total_candidates_generated", 0) + i
                state = self.state_manager.mark_circuit_broken(state, cb_reason)
                return cb_reason

            verdict = self.verifier.check(cand, self.seed_pool)
            # P1c: 暂存 bootstrap 阶段的具体错误（如编译失败详细原因），
            # 因 verifier.check 会覆盖 cand["failure_reasons"] 为笼统原因
            bootstrap_detail = list(cand.get("failure_reasons", []))
            cand["passed_l1_verifier"] = verdict["passed"]
            cand["failure_reasons"] = verdict["failure_reasons"]

            if not verdict["passed"]:
                rejected_count += 1
                # P1a: 硬失败（编译失败/重复）计入连续低质量熔断计数；
                # 软失败（经济逻辑评分/narrative 不达标）不计入，避免 LLM 评分波动误触熔断
                if self._is_hard_failure(verdict["failure_reasons"]):
                    self._consecutive_low_quality += 1
                    # 硬失败（编译失败/重复）→ 立即落盘拒绝候选（含 code + 具体编译错误），
                    # 此前不持久化导致不可追溯（2026-08-16 修复）
                    self._persist_rejected(cand, bootstrap_detail or verdict["failure_reasons"], trace_id)
                detail = bootstrap_detail or cand.get("failure_reasons")
                logger.warning(
                    "[L1.verify] 候选[%d/%d] 未通过: name=%s, candidate_id=%s, reasons=%s%s",
                    i + 1,
                    len(candidates),
                    cand_name,
                    cand_id,
                    verdict["failure_reasons"],
                    f", detail={detail}" if detail else "",
                )
                # GAP-123 P1③: 软失败（经济逻辑评分/narrative 不达标）触发一次 LLM 定向重写，
                # 重写后重新验证，通过则走注入闭环（每候选最多重写 1 次，见 _try_fix_economic_logic）
                if not self._is_hard_failure(verdict["failure_reasons"]):
                    fixed_ok = self._try_fix_economic_logic(cand, trace_id)
                    if fixed_ok:
                        rejected_count -= 1
                        passed_count += 1
                        cand["passed_l1_verifier"] = True
                        cand["failure_reasons"] = [f"经济逻辑重写修复: 原{verdict['failure_reasons']}"]
                        # 重写后通过以 WARNING 级别输出：与首轮"未通过"warning 同级，
                        # 确保默认日志级别下也能看到"未通过→重写→通过"完整闭环
                        logger.warning(
                            "[L1.verify] 候选[%d/%d] 经济逻辑重写后通过: name=%s, candidate_id=%s",
                            i + 1,
                            len(candidates),
                            cand_name,
                            cand_id,
                        )
                        injected_id = self._inject_candidate(cand, trace_id)
                        if injected_id:
                            injected_ids.append(injected_id)
                            state["total_candidates_injected"] = state.get("total_candidates_injected", 0) + 1
                            state.setdefault("candidates_ref", []).append(injected_id)
                            batch_injected += 1
                            self._consecutive_low_quality = 0
                        continue
                    # 软失败重写后仍未达标 → 落盘拒绝候选供回溯
                    self._persist_rejected(cand, verdict["failure_reasons"], trace_id)
                continue

            passed_count += 1
            logger.info(
                "[L1.verify] 候选[%d/%d] 通过: name=%s, candidate_id=%s, source=%s, code_len=%d",
                i + 1,
                len(candidates),
                cand_name,
                cand_id,
                cand.get("source", "unknown"),
                len(cand.get("code", "")),
            )

            injected_id = self._inject_candidate(cand, trace_id)
            if injected_id:
                injected_ids.append(injected_id)
                state["total_candidates_injected"] = state.get("total_candidates_injected", 0) + 1
                state.setdefault("candidates_ref", []).append(injected_id)
                batch_injected += 1
                self._consecutive_low_quality = 0

        # 整批验证完成后的最终失败率检查——此时已验证数 = len(candidates)，
        # 覆盖"本批全部失败"的真实熔断场景，且不再误判未验证候选
        cb_reason = self._check_circuit_breaker(state, len(candidates), batch_injected)
        if cb_reason:
            logger.warning(
                "[L1.verify] 整批验证后熔断触发: reason=%s, 已验证=%d, 本批注入=%d",
                cb_reason,
                len(candidates),
                batch_injected,
            )
            state["total_candidates_generated"] = state.get("total_candidates_generated", 0) + len(candidates)
            state = self.state_manager.mark_circuit_broken(state, cb_reason)
            return cb_reason

        # 本批全部验证完毕，累计到历史已验证数（供跨批失败率熔断使用）
        state["total_candidates_generated"] = state.get("total_candidates_generated", 0) + len(candidates)

        logger.info(
            "[L1.verify] 验证注入完成: total=%d, passed=%d, rejected=%d, injected=%d, consecutive_low_quality=%d",
            len(candidates),
            passed_count,
            rejected_count,
            len(injected_ids),
            self._consecutive_low_quality,
        )
        return None

    @staticmethod
    def _make_result(
        run_id: str,
        trace_id: str,
        candidates_generated: int,
        candidates_injected: int,
        debate_gaps_detected: int,
        tokens_consumed: int,
        status: str,
        circuit_breaker_reason: Optional[str] = None,
        injected_ids: Optional[list[str]] = None,
        elapsed_seconds: float = 0.0,
    ) -> MetaRunResult:
        """构造 MetaRunResult（GAP-I101: 吞吐指标 = 候选数 / 运行分钟）。"""
        return MetaRunResult(
            run_id=run_id,
            trace_id=trace_id,
            candidates_generated=candidates_generated,
            candidates_injected=candidates_injected,
            debate_gaps_detected=debate_gaps_detected,
            tokens_consumed=tokens_consumed,
            status=status,
            circuit_breaker_reason=circuit_breaker_reason,
            injected_candidate_ids=injected_ids or [],
            candidates_per_minute=(
                round(candidates_generated / (elapsed_seconds / 60.0), 2) if elapsed_seconds > 0 else 0.0
            ),
        )

    def _perceive_market(self, trace_id: str) -> dict[str, Any]:
        """Step 1: agentic 感知 — f10/web_collector 拉取市场快照。"""
        if self.web_collector is None:
            logger.info("L1 Step 1: 未配置 web_collector, 跳过感知")
            result: dict[str, Any] = {"trace_id": trace_id, "snapshots": {}, "skipped": True}
            self._inject_chain_knowledge(result)
            return result

        snapshots: dict[str, Any] = {}
        for sym in self.sample_symbols:
            try:
                snap = self.web_collector(sym)
                snapshots[sym] = snap
            except Exception as e:
                logger.warning("L1 感知 %s 失败: %s", sym, e)
                snapshots[sym] = {"error": str(e)}

        result = {
            "trace_id": trace_id,
            "snapshots": snapshots,
            "skipped": False,
        }
        self._inject_chain_knowledge(result)
        return result

    def _subchain_symbols(self, subchain: str) -> str:
        """从训练池动态推导子链品种（供 chain_knowledge 描述用）。"""
        try:
            from fts.data_futures import ENERGY_CHAIN_SYMBOLS, FUTURES_SECTOR_MAP

            members = set(FUTURES_SECTOR_MAP.get(subchain, []))
            return "/".join(sorted(set(ENERGY_CHAIN_SYMBOLS) & members)) or subchain
        except Exception:  # noqa: BLE001
            return subchain

    def _inject_chain_knowledge(self, result: dict[str, Any]) -> None:
        """能源链专属市场知识注入（GAP-121 + plans/41 C 层）：静态链知识 + 实时产业状态。"""
        if self.market != "energy":
            return
        try:
            from fts.data_futures import (
                ENERGY_CHAIN_CHEMICAL_SECTORS,
                ENERGY_CHAIN_HOLDOUT,
                ENERGY_CHAIN_SYMBOLS,
            )

            # 品种-链条位置描述（随训练池配置动态生成：缺省取通用品种中文名，特殊链描述优先）
            sym_desc: dict[str, str] = {}
            from fts.data_futures import FUTURES_SYMBOL_NAMES

            _chain_sym_desc = {
                "SC0": "原油(INE，能源链源头)",
                "FU0": "燃料油(SHFE，原油下游)",
                "BU0": "沥青(SHFE，炼化下游)",
                "PF0": "短纤(CZCE，聚酯成品)",
                "TA0": "PTA(CZCE，聚酯链中游)",
                "EG0": "乙二醇(DCE，聚酯原料/防冻剂)",
                "L0": "聚乙烯(DCE，塑料/油化工)",
                "PP0": "聚丙烯(DCE，塑料/油化工)",
                "PG0": "液化石油气(DCE，炼厂伴生气/油化工)",
                "MA0": "甲醇(CZCE，煤基化工/煤化工)",
                "UR0": "尿素(CZCE，氮肥/煤化工)",
                "SA0": "纯碱(CZCE，煤化工/建材)",
            }
            for sym in ENERGY_CHAIN_SYMBOLS:
                sym_desc[sym] = _chain_sym_desc.get(sym) or FUTURES_SYMBOL_NAMES.get(sym, sym)

            # plans/41 C1: 实时产业状态段（子链价差/基差-库存/开工代理），异常/缺失自动降级
            live_state = self._build_chain_live_state()

            result["chain_knowledge"] = (
                "【能源产业链专属知识】\n"
                f"训练链 {len(ENERGY_CHAIN_SYMBOLS)} 品种: {', '.join(ENERGY_CHAIN_SYMBOLS)}\n"
                f"品种-链条位置: {sym_desc}\n"
                f"训练池覆盖四大化工子链: "
                f"能源({self._subchain_symbols('能源')}) + 聚酯链({self._subchain_symbols('聚酯链')}) "
                f"+ 油化工({self._subchain_symbols('油化工')}) + 煤化工({self._subchain_symbols('煤化工')})；\n"
                "核心产业链逻辑: ①裂解价差（原油→燃料油/沥青/液化气，炼厂利润代理）；"
                "②聚酯链加工差（PX-原油、PTA-PX、聚酯-PTA，围绕边际成本均值回归）；"
                "③库存周期（沥青/液化气/PTA 季节性库存主导基差）；"
                "④链内纵向传导（原油成本经链条在下游加速/衰减）；"
                "⑤子链间相对强弱（油化工/煤化工与能源/聚酯的成本与利润周期差异）。\n"
                f"链外盲测池（化工产业链泛化验证）: {sorted(ENERGY_CHAIN_HOLDOUT)}\n"
                f"盲测分组: {', '.join(ENERGY_CHAIN_CHEMICAL_SECTORS)}\n"
                "因子设计要求: 优先利用能化品种特有的波动聚集、量价协同（库存/开工代理）、"
                "期限结构与价格位置（基差-库存回归）、链内联动、子链间相对强弱与季节性开工周期等机制；"
                "narrative 须体现能化产业链机制而非泛化量价规律。"
            )
            if live_state:
                result["chain_knowledge"] += (
                    "\n【能源产业链实时产业状态】\n"
                    "以下为训练链品种近 60 日量价衍生的实时产业代理（数据可用时注入，供因子设计参考）：\n"
                    f"{live_state}"
                )
            logger.info("L1 Step 1: 能源链市场知识注入完成 (chain_knowledge_len=%d)", len(result["chain_knowledge"]))
        except Exception as e:  # noqa: BLE001
            logger.warning("L1 Step 1: 能源链知识注入失败: %s", e)

    def _build_chain_live_state(self) -> str:
        """构建能源链实时产业状态描述（plans/41 C1）。

        经 FTSDataProvider 拉取训练链品种近 60 日 OHLCV，计算并注入：
          - 子链价差代理（如 SC-FU、SC-BU 裂解价差收益，炼厂利润代理）
          - 基差-库存水位代理（近 20 日动量/波动率偏离，量价代理）
          - 开工季节性代理（5 日/60 日滚动均值偏离）
        任一环节异常/数据缺失自动降级（返回可用的部分），不阻断整体注入。

        Returns:
            实时产业状态文本（无可用数据返回空串）。
        """
        lines: list[str] = []
        try:
            from fts.data import FTSDataProvider
            from fts.data_futures import ENERGY_CHAIN_SYMBOLS

            provider = FTSDataProvider()
            panel, _ = provider.get_futures_panel(symbols=list(ENERGY_CHAIN_SYMBOLS), days=60)
        except Exception as e:  # noqa: BLE001
            logger.warning("[L1.chain_live] 面板获取失败, 跳过实时状态: %s", e)
            return ""

        try:
            import numpy as np

            # 各品种量价代理（波动率聚集 / 动量 / 均值偏离）
            px_stats: dict[str, dict[str, float]] = {}
            for sym, df in panel.items():
                if df is None or df.empty or "close" not in df.columns:
                    continue
                close = df["close"].astype(float).dropna()
                if len(close) < 20:
                    continue
                ret = close.pct_change().dropna()
                vol20 = float(ret.tail(20).std()) if len(ret) >= 20 else float("nan")
                mom20 = float(close.iloc[-1] / close.iloc[-20] - 1) if len(close) >= 21 else float("nan")
                ma5 = float(close.tail(5).mean())
                ma60 = float(close.mean())
                dev5 = (ma5 / ma60 - 1) if ma60 else float("nan")
                px_stats[sym] = {
                    "vol20": vol20,
                    "mom20": mom20,
                    "dev5": dev5,
                }

            # 子链价差代理（按链内上下游配对，缺失品种跳过）
            pair_map = [
                ("能源裂解", ("SC0", "FU0")),
                ("能源裂解", ("SC0", "BU0")),
                ("聚酯加工差", ("SC0", "TA0")),
                ("油化工", ("SC0", "L0")),
                ("煤化工", ("MA0", "SA0")),
            ]
            spread_parts: list[str] = []
            for label, (a, b) in pair_map:
                if a in px_stats and b in px_stats:
                    ra, rb = px_stats[a]["mom20"], px_stats[b]["mom20"]
                    if ra is not None and rb is not None and not (isinstance(ra, float) and np.isnan(ra)) and not (
                        isinstance(rb, float) and np.isnan(rb)
                    ):
                        spread_parts.append(f"{label}({a}-{b}) 20日价差收益≈{ra - rb:+.3%}")
            if spread_parts:
                lines.append("子链价差代理: " + "; ".join(spread_parts))

            # 波动率聚集 + 基差-库存水位代理（滚动偏离）
            vol_parts: list[str] = []
            pos_parts: list[str] = []
            for sym, st in px_stats.items():
                if not (isinstance(st["vol20"], float) and np.isnan(st["vol20"])):
                    vol_parts.append(f"{sym} 20日波动≈{st['vol20']:.2%}")
                if not (isinstance(st["dev5"], float) and np.isnan(st["dev5"])):
                    pos_parts.append(f"{sym} 价格位置(5日/60日均值偏离)≈{st['dev5']:+.2%}")
            if vol_parts:
                lines.append("波动聚集代理: " + "; ".join(vol_parts[:12]))
            if pos_parts:
                lines.append("库存/基差水位代理(价格位置): " + "; ".join(pos_parts[:12]))
        except Exception as e:  # noqa: BLE001
            logger.warning("[L1.chain_live] 实时状态计算失败, 部分降级: %s", e)
        return "\n".join(lines)

    def _retry_rejected_candidates(
        self,
        state: L1MetaLoopState,
        trace_id: str,
        injected_ids: list[str],
    ) -> int:
        """plans/44 C2: 扫描 l1_rejected_*，对编译失败候选修复代码后重新验证注入。

        配置 `l1_rejected_retry` 关闭 / rejected 目录不存在 → 返回 0。
        修复仍失败的候选保留在 rejected 目录（不重复尝试同轮）。
        注入成功的候选从 rejected 目录移走（GAP-131 落盘闭环 → 复活）。

        Returns:
            本次复活注入数
        """
        from fts.config.settings import get_config

        if not getattr(get_config(), "l1_rejected_retry", True):
            logger.info("[L1.retry] l1_rejected_retry 关闭, 跳过复活")
            return 0
        if not self.rejected_dir.exists():
            logger.info("[L1.retry] 无拒绝候选目录, 跳过复活: %s", self.rejected_dir)
            return 0

        revived: list[SeedCandidate] = []
        for f in sorted(self.rejected_dir.glob("cand_*.json")):
            try:
                record = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("[L1.retry] 拒绝候选解析失败, 跳过: %s, error=%s", f.name, e)
                continue
            reasons_text = " ".join(str(r) for r in record.get("l1_rejection", {}).get("reasons", []))
            if "编译" not in reasons_text:
                continue  # 仅复活编译失败类候选
            code = record.get("code", "")
            if not code:
                continue
            fixed_code = self._try_revive_code(code, reasons_text, trace_id)
            if fixed_code is None:
                logger.info(
                    "[L1.retry] 复活修复仍失败, 保留: name=%s, file=%s",
                    record.get("name", "?"),
                    f.name,
                )
                continue
            cand = dict(record)
            cand["code"] = fixed_code
            cand["is_executable"] = True
            cand["passed_l1_verifier"] = False
            cand["failure_reasons"] = []
            revived.append(cand)
        if not revived:
            logger.info("[L1.retry] 无可复活候选, revived=0")
            return 0

        logger.info("[L1.retry] 复活候选 %d 个, 进入验证注入: trace_id=%s", len(revived), trace_id)
        before = len(injected_ids)
        self._verify_and_inject(revived, state, trace_id, injected_ids)
        # 注入成功的候选从 rejected 目录移走
        newly_injected = set(injected_ids[before:])
        removed = 0
        for cand in revived:
            cid = cand.get("candidate_id")
            if cid in newly_injected:
                f = self.rejected_dir / f"{cid}.json"
                try:
                    f.unlink(missing_ok=True)
                    removed += 1
                except OSError as e:
                    logger.warning("[L1.retry] 移走失败: %s, error=%s", f, e)
        logger.info(
            "[L1.retry] 复活完成: revived=%d, injected=%d, removed=%d",
            len(revived),
            len(newly_injected),
            removed,
        )
        return len(newly_injected)

    def _try_revive_code(self, code: str, error_reason: str, trace_id: str) -> Optional[str]:
        """复活修复：先规则修复（fix_factor_code），再 LLM 修复兜底，均需过 validate_factor_code。

        Returns:
            修复后的可编译代码或 None（全部失败）
        """
        from fts.factor_engine.factor_program import fix_factor_code, validate_factor_code

        ok, _ = validate_factor_code(code)
        if ok:
            return code  # 已可编译（错误信息可能已过时）
        fixed, fixed_code = fix_factor_code(code, error_reason)
        if fixed:
            ok2, _ = validate_factor_code(fixed_code)
            if ok2:
                return fixed_code
        # LLM 兜底
        llm = self.llm_client
        if llm is not None and hasattr(llm, "fix_factor_code"):
            try:
                llm_fixed = llm.fix_factor_code(code, error_reason, trace_id)
            except Exception as e:
                logger.warning("[L1.retry] LLM 修复异常, trace_id=%s, error=%s", trace_id, e)
                llm_fixed = None
            if isinstance(llm_fixed, str) and llm_fixed.strip():
                ok3, _ = validate_factor_code(llm_fixed)
                if ok3:
                    return llm_fixed
        return None

    def _persist_rejected(self, cand: SeedCandidate, reasons: list[str], trace_id: str) -> Optional[str]:
        """将未通过 L1 验证的候选落盘（含 code 与拒绝原因），供事后回溯/人工修复。

        背景: 编译失败等硬失败候选此前仅日志记录即丢弃，代码不可追溯（2026-08-16 修复）。
        落盘目录由 inject_dir 派生（l1_injected → l1_rejected；energy 同理）。
        """
        cand_id = cand.get("candidate_id", "unknown")
        try:
            self.rejected_dir.mkdir(parents=True, exist_ok=True)
            record = dict(cand)
            record["market"] = self.market
            record["l1_rejection"] = {
                "reasons": [str(r) for r in (reasons or [])],
                "rejected_at": datetime.now().isoformat(),
                "trace_id": trace_id,
            }
            out_file = self.rejected_dir / f"{cand_id}.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2, default=str)
            logger.warning(
                "[L1.reject] 拒绝候选已落盘: name=%s, candidate_id=%s, path=%s, reasons=%s",
                cand.get("name", "unknown"),
                cand_id,
                out_file,
                record["l1_rejection"]["reasons"],
            )
            return cand_id
        except Exception as e:  # noqa: BLE001
            logger.error(
                "[L1.reject] 拒绝候选落盘失败: name=%s, candidate_id=%s, error=%s",
                cand.get("name", "unknown"),
                cand_id,
                e,
                exc_info=True,
            )
            return None

    def _inject_candidate(self, cand: SeedCandidate, trace_id: str) -> Optional[str]:
        """Step 5: 注入候选到 L2 种子池入口。"""
        cand_name = cand.get("name", "unknown")
        cand_id = cand.get("candidate_id", "unknown")
        logger.info(
            "[L1.inject] 开始注入: name=%s, candidate_id=%s, source=%s, code_len=%d",
            cand_name,
            cand_id,
            cand.get("source", "unknown"),
            len(cand.get("code", "")),
        )
        try:
            # 0. 标记市场归属（GAP-031: L2 合并时按 market 过滤）
            cand["market"] = self.market
            # 1. 持久化到 l1_injected/ 目录
            self.inject_dir.mkdir(parents=True, exist_ok=True)
            inject_file = self.inject_dir / f"{cand['candidate_id']}.json"
            with open(inject_file, "w", encoding="utf-8") as f:
                json.dump(cand, f, ensure_ascii=False, indent=2, default=str)
            logger.info("[L1.inject] 持久化完成: path=%s, size=%d", inject_file, inject_file.stat().st_size)

            # 2. 更新 factor_pool.json
            entry = FactorPoolEntry(
                factor_id=cand["candidate_id"],
                name=cand.get("name", ""),
                source=cand.get("source", "l1_bootstrapping"),
                parent_topic=cand.get("parent_topic"),
                debate_round_ref=cand.get("debate_round_ref"),
                debate_gap=cand.get("debate_gap"),
                economic_logic=cand.get("economic_logic", {}),
                priority=self._compute_priority(cand),
                status="pending",
                market=self.market,
                trace_id=trace_id,
                created_at=cand.get("created_at", datetime.now().isoformat()),
                updated_at=datetime.now().isoformat(),
            )
            self.factor_pool_manager.add_entry(entry)
            logger.info(
                "[L1.inject] factor_pool.json 更新完成: factor_id=%s, priority=%s",
                cand["candidate_id"],
                entry["priority"],
            )

            # 3. 标记候选已注入
            cand["injected_to_l2"] = True
            cand["injected_at"] = datetime.now().isoformat()

            logger.info(
                "✅ [L1.inject] 注入成功: name=%s, candidate_id=%s, file=%s",
                cand_name,
                cand_id,
                inject_file,
            )
            return cand["candidate_id"]

        except Exception as e:
            logger.error(
                "❌ [L1.inject] 注入失败: name=%s, candidate_id=%s, error=%s",
                cand_name,
                cand_id,
                e,
                exc_info=True,
            )
            return None

    @staticmethod
    def _compute_priority(cand: SeedCandidate) -> Literal["high", "medium", "low"]:
        """根据经济逻辑和 debate_gap 计算优先级。"""
        economic = cand.get("economic_logic", {})
        total_score = (
            economic.get("theory", 0)
            + economic.get("behavioral", 0)
            + economic.get("microstructure", 0)
            + economic.get("institutional", 0)
        )
        if cand.get("debate_gap") or total_score >= 16:
            return "high"
        if total_score >= 12:
            return "medium"
        return "low"

    @staticmethod
    def _is_hard_failure(reasons: list[str]) -> bool:
        """判断验证失败是否为硬失败（结构性不可修复）。

        硬失败（计入连续低质量熔断计数）:
            - 沙箱编译失败（代码不可执行）
            - 与现有种子因子重复
        软失败（不计入熔断计数，LLM 输出波动可通过 Prompt 调优）:
            - 经济逻辑评分不达标
            - narrative 长度不足
        """
        text = " ".join(reasons)
        return ("编译" in text) or ("重复" in text)

    def _try_fix_economic_logic(self, cand: SeedCandidate, trace_id: str) -> bool:
        """GAP-123 P1③: 软失败候选经 LLM 定向重写 economic_logic 后重新验证。

        流程: 调用 llm_client.fix_economic_logic（若支持）→ 更新 cand 的
        economic_logic → 重新走 L1Verifier.check → 通过返回 True。

        约束:
            - 每候选仅重写 1 次（本方法只在软失败分支调用一次，不递归）
            - LLM 客户端不支持（基类默认 None）/调用异常/重写后仍不达标 → 返回 False
            - 不影响硬失败（编译/重复）路径，不改变熔断计数语义

        Args:
            cand: 未通过 L1 Verifier 的候选（软失败）
            trace_id: 全链路 trace_id

        Returns:
            True — 重写成功且重新验证通过；False — 重写失败/仍不达标
        """
        llm = self.llm_client
        if llm is None or not hasattr(llm, "fix_economic_logic"):
            logger.info(
                "[L1.fix_econ] LLM 客户端不支持 fix_economic_logic, 跳过, trace_id=%s, name=%s",
                trace_id,
                cand.get("name", "?"),
            )
            return False
        try:
            fixed_econ = llm.fix_economic_logic(cand, cand.get("failure_reasons", []), trace_id)
        except Exception as e:
            logger.warning(
                "[L1.fix_econ] LLM 重写异常, trace_id=%s, name=%s, error=%s",
                trace_id,
                cand.get("name", "?"),
                e,
            )
            return False
        if not fixed_econ or not isinstance(fixed_econ, dict) or not fixed_econ.get("narrative"):
            logger.info(
                "[L1.fix_econ] 重写返回无效 economic_logic, 保持原候选, trace_id=%s, name=%s",
                trace_id,
                cand.get("name", "?"),
            )
            return False
        cand["economic_logic"] = fixed_econ
        re_verdict = self.verifier.check(cand, self.seed_pool)
        if not re_verdict["passed"]:
            logger.warning(
                "[L1.fix_econ] 重写后仍不达标, trace_id=%s, name=%s, reasons=%s",
                trace_id,
                cand.get("name", "?"),
                re_verdict["failure_reasons"],
            )
            return False
        cand["passed_l1_verifier"] = True
        cand["failure_reasons"] = re_verdict["failure_reasons"]
        return True

    def _check_circuit_breaker(
        self,
        state: L1MetaLoopState,
        candidates_generated: int,
        batch_injected: int = 0,
    ) -> Optional[str]:
        """熔断检查。返回原因字符串（None = 未触发）。

        P2 修复（误熔断，2026-08-13）: `candidates_generated` 语义改为
        "本次运行已实际验证的候选数"，新增 `batch_injected`（本次运行已注入数）。
        失败率 = (累计已评估 - 累计已注入) / 累计已评估，其中"已评估"不包含
        尚未执行 verifier.check 的候选。原缺陷: 调用方在验证循环第一个候选前
        传入"本批总数 20 + 注入 0"，导致 100% > 95% 立即误熔断、整批 0 注入。
        """
        # 1. Token 超 2x
        tokens = state.get("tokens_consumed", 0)
        limit = state.get("budget_limit", self.budget["daily_token_limit"])
        if tokens > limit * self.budget["circuit_breaker_token_ratio"]:
            return f"Token 熔断: {tokens} > {limit} * {self.budget['circuit_breaker_token_ratio']}"

        # 2. 失败率 > 95% —— 仅基于已实际验证的候选（历史 + 本次已验证/已注入）
        evaluated = state.get("total_candidates_generated", 0) + candidates_generated
        injected = state.get("total_candidates_injected", 0) + batch_injected
        if evaluated >= 20:  # 至少累计 20 个候选才检查
            failure_rate = (evaluated - injected) / evaluated
            if failure_rate > self.budget["circuit_breaker_failure_rate"]:
                return f"失败率熔断: {failure_rate:.2%} > {self.budget['circuit_breaker_failure_rate']}"

        # 3. 连续低质量
        if self._consecutive_low_quality >= self.budget["circuit_breaker_consecutive_low_quality"]:
            return (
                f"连续低质量熔断: {self._consecutive_low_quality} >= "
                f"{self.budget['circuit_breaker_consecutive_low_quality']}"
            )

        return None

    @staticmethod
    def _estimate_tokens(candidates_generated: int, debate_gaps_detected: int) -> int:
        """估算本次运行 LLM token 消耗（Mock 版本）。"""
        # 假设: 每个候选 5K token + 每个辩论缺口 200 token + 基础 1K
        return 1000 + candidates_generated * 5000 + debate_gaps_detected * 200


# ─── web_collector ──────────────────────────────────────


def _make_web_collector(provider: Any | None = None, market: str = "futures") -> Callable[..., dict]:
    """创建 web_collector 可调用对象 — 基于 FTSDataProvider 的市场快照采集。

    Args:
        provider: FTSDataProvider 实例（None 时惰性初始化）
        market: 市场类型（默认 "futures"），决定 OHLCV 数据源与实时价路径。

    Returns:
        Callable(symbol: str) -> dict — 市场快照，包含 quote、kline、news 等字段
    """
    lazy_provider: Any | None = provider

    def _collect(symbol: str) -> dict:
        """采集单个标的的市场快照。"""
        nonlocal lazy_provider
        if lazy_provider is None:
            from fts.data import FTSDataProvider

            lazy_provider = FTSDataProvider()

        # 转换 symbol 格式: "rb" → "RB0"
        symbol_upper = symbol.upper().strip()
        contract_symbol = symbol_upper if symbol_upper.endswith("0") else f"{symbol_upper}0"

        result: dict = {
            "symbol": symbol,
            "contract_symbol": contract_symbol,
            "source": "fts_data_provider",
            "fetched_at": datetime.now().isoformat(),
            "quote": {},
            "kline": {"bars": []},
            "news": [],
            "warnings": [],
        }

        # 1. 获取 OHLCV 数据（多源聚合）
        try:
            df = lazy_provider._futures.get_ohlcv(contract_symbol, days=60)
            if df is not None and not df.empty:
                # 取最新 5 根 K 线
                recent = df.tail(5)
                bars = []
                for idx, row in recent.iterrows():
                    bar = {
                        "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "volume": float(row.get("volume", 0)),
                    }
                    bars.append(bar)
                result["kline"]["bars"] = bars

                # 最新 quote
                last = recent.iloc[-1]
                result["quote"] = {
                    "last_price": float(last.get("close", 0)),
                    "volume": float(last.get("volume", 0)),
                    "open": float(last.get("open", 0)),
                    "high": float(last.get("high", 0)),
                    "low": float(last.get("low", 0)),
                }
        except Exception as e:
            result["warnings"].append(f"OHLCV 获取失败: {e}")

        # 2. 实时价：v3.0.0+1 起 FTS 因子生命周期管理不需要盘中实时价，
        #    不再连接 TDX_LOCAL/AKShare 网络源；quote 直接用 QuantData 日线最新收盘
        #    （来源标注 fts_data_provider，避免 LLM 感知误以为盘中实时）。
        bars = result["kline"].get("bars", [])
        last_close = float(bars[-1]["close"]) if bars else None
        result["quote"]["realtime_price"] = last_close

        return result

    return _collect


# ─── CLI 入口 ───────────────────────────────────────────


def main():
    """CLI 入口: python -m fts.factor_engine.meta_loop --once"""
    parser = argparse.ArgumentParser(description="L1 Meta-Loop 知识补给循环")
    parser.add_argument("--once", action="store_true", help="运行一次完整 L1 循环")
    parser.add_argument(
        "--max-bootstraps",
        type=int,
        default=None,
        help="最大 Bootstrapping 数（默认 5）",
    )
    parser.add_argument(
        "--memory-dir",
        default="memory/meta_loop",
        help="L1 状态目录（默认 memory/meta_loop）",
    )
    parser.add_argument(
        "--factor-pool",
        default="memory/knowledge/factors/factor_pool.json",
        help="factor_pool.json 路径",
    )
    parser.add_argument(
        "--inject-dir",
        default="memory/knowledge/factors/l1_injected",
        help="L1 注入因子存储目录",
    )
    parser.add_argument(
        "--market",
        default="futures",
        choices=["futures"],
        help="市场类型（期货，默认）",
    )
    args = parser.parse_args()

    if not args.once:
        print("Use --once to run L1 Meta-Loop")
        sys.exit(1)

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 使用 FTSDataProvider 替代 futures_data_core
    from fts.data import FTSDataProvider

    provider = FTSDataProvider()
    logger.info("FTSDataProvider 已就绪 — 将用于 L1 感知步骤")

    from fts.llm import get_llm_client

    # 创建 web_collector — 基于 FTSDataProvider 的市场快照采集
    web_collector = _make_web_collector(provider, market=args.market)
    logger.info("web_collector 已就绪 — 市场快照感知已启用")

    loop = MetaLoop(
        memory_dir=args.memory_dir,
        factor_pool_path=args.factor_pool,
        inject_dir=args.inject_dir,
        web_collector=web_collector,
        llm_client=get_llm_client(),
        market=args.market,
    )
    result = loop.run(max_bootstraps=args.max_bootstraps)
    print(f"L1 Meta-Loop 完成: {result.to_dict()}")
    sys.exit(0 if result.status == "completed" else 1)


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    # 异常
    "MetaLoopError",
    "MetaStateManagerError",
    "L1VerifierLocked",
    "FactorPoolError",
    # Verifier
    "L1Verifier",
    # 状态管理
    "MetaStateManager",
    # FactorPool 管理
    "FactorPoolManager",
    # 辩论分析
    "DebateQualityAnalyzer",
    # Bootstrapping
    "BootstrappingChain",
    "validate_batch_candidates",
    # 主循环
    "MetaLoop",
    "MetaRunResult",
    # CLI
    "main",
]
