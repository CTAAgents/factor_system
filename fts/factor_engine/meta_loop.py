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
import shutil
import sys
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .contracts import (
    DEFAULT_L1_BUDGET_CONFIG,
    DEFAULT_L1_VERIFIER_CONFIG,
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
    validate_factor_code,
)
from .extractors import FuturesExtractorPipeline, StockExtractorPipeline
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
            reasons.append(
                f"经济逻辑达标维度 {dimensions_passed}/4 < {config['min_economic_score']}"
            )

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
            checked_against=dict(self._config),  # type: ignore[arg-type]
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
    """L1 Meta-Loop 状态文件管理器。

    存储位置: memory/meta_loop/state.json
    备份位置: memory/meta_loop/state.json.backup
    """

    def __init__(self, memory_dir: str | Path = "memory/meta_loop"):
        self.memory_dir = Path(memory_dir)
        self.state_file = self.memory_dir / "state.json"
        self.backup_file = self.memory_dir / "state.json.backup"

    def load_or_init(self, budget_limit: int) -> L1MetaLoopState:
        """加载或初始化状态。"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                # schema 版本检查（仅状态结构变更时冷启动）
                from .contracts import STATE_SCHEMA_VERSION
                if state.get("schema_version") != STATE_SCHEMA_VERSION:
                    logger.warning(
                        "L1 状态 schema 版本不匹配: %s != %s, 冷启动",
                        state.get("schema_version"), STATE_SCHEMA_VERSION,
                    )
                    return self._init_state(budget_limit)
                return state
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("L1 状态文件损坏: %s, 尝试备份恢复", e)
                return self._recover_from_backup(budget_limit)
        return self._init_state(budget_limit)

    def _recover_from_backup(self, budget_limit: int) -> L1MetaLoopState:
        """从备份恢复状态。"""
        if self.backup_file.exists():
            try:
                shutil.copy2(self.backup_file, self.state_file)
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error("备份恢复失败: %s, 冷启动", e)
        return self._init_state(budget_limit)

    @staticmethod
    def _init_state(budget_limit: int) -> L1MetaLoopState:
        """初始化新的状态。"""
        from .contracts import STATE_SCHEMA_VERSION
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
        """持久化状态 — 先写主文件，再镜像到 backup。"""
        from .contracts import STATE_SCHEMA_VERSION
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise MetaStateManagerError(
                f"状态 schema 版本不匹配: {state.get('schema_version')} != {STATE_SCHEMA_VERSION}"
            )
        state["last_updated"] = datetime.now().isoformat()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        # 先写主文件
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        # 再镜像到 backup
        try:
            shutil.copy2(self.state_file, self.backup_file)
        except OSError as e:
            raise MetaStateManagerError(f"备份失败: {e}") from e

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
        pool["pending_count"] = sum(
            1 for f in pool.get("factors", []) if f.get("status") == "pending"
        )
        with open(self.factor_pool_path, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)
        self._cache = pool

    def add_entry(self, entry: FactorPoolEntry) -> None:
        """添加一条因子记录。"""
        pool = self._cache or self.load_or_init()
        factors = pool.setdefault("factors", [])
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
                f["status"] = status
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
        "bullish_weak",      # 多头论证薄弱
        "bearish_weak",      # 空头论证薄弱
        "insufficient_rounds",  # 辩论轮次不足
        "no_debate",         # 无辩论数据
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
        debate_records = [
            e for e in entries if e.get("action") == "debate_record"
        ][-10:]

        for rec in debate_records:
            symbols = rec.get("symbols", {})
            if isinstance(symbols, dict):
                for sym, sym_data in symbols.items():
                    gap = self._detect_gap(sym_data)
                    if gap:
                        result["topics"].append({
                            "topic": sym,
                            "gap": gap,
                            "debate_round": sym_data.get("debate_round", 0)
                            if isinstance(sym_data, dict) else 0,
                        })

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
                theory=4, behavioral=4, microstructure=3, institutional=4,
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
                theory=4, behavioral=3, microstructure=5, institutional=4,
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
                theory=3, behavioral=5, microstructure=4, institutional=3,
                narrative="新闻情绪衰减代理: 捕捉新闻情绪的持续性，反映投资者反应不足。",
            ),
        },
    ]

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        web_collector: Optional[Callable[..., dict]] = None,
        extractor_pipeline: Optional[Any] = None,
    ):
        """
        Args:
            llm_client: LLM 客户端（必须实现 generate(prompt: str) -> str 接口）。
                        None 时使用内置模板回退。
            web_collector: f10/web_collector 的 collect_fundamental_web 函数。
                        None 时跳过感知步骤。
            extractor_pipeline: 三源提取器管道（如 FuturesExtractorPipeline /
                        StockExtractorPipeline），在 LLM 候选后自动调用来补充候选。
        """
        self.llm_client = llm_client
        self.web_collector = web_collector
        self.extractor_pipeline = extractor_pipeline

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
            trace_id, max_candidates, len(existing_names), len(extra_existing_names or set()),
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
                                cand_name, cand.get("source", "unknown"),
                            )
                            continue
                        non_dup_extractor.append(cand)
                    candidates.extend(non_dup_extractor)
                    extractor_count = len(non_dup_extractor)
                    logger.info(
                        "[bootstrap] 提取器管道候选已加入, trace_id=%s, raw=%d, filtered=%d, total=%d",
                        trace_id, len(raw_extractor_candidates), extractor_count, len(candidates),
                    )
                else:
                    logger.info(
                        "[bootstrap] 提取器管道返回空, trace_id=%s", trace_id,
                    )
            except Exception as e:
                logger.error(
                    "[bootstrap] 提取器管道异常: %s, trace_id=%s, 跳过",
                    e, trace_id, exc_info=True,
                )

        # 2. LLM 补足剩余配额（提取器未填满的部分）
        llm_needed = max_candidates - len(candidates)
        if self.llm_client is not None and llm_needed > 0:
            llm_candidates = self._bootstrap_with_llm(
                market_snapshot, debate_gaps, llm_needed, trace_id
            )
            candidates.extend(llm_candidates)
            logger.info(
                "[bootstrap] LLM 候选已加入, trace_id=%s, llm_count=%d, total=%d",
                trace_id, len(llm_candidates), len(candidates),
            )
        else:
            logger.info(
                "[bootstrap] LLM 跳过, trace_id=%s, llm_needed=%d, llm_client=%s",
                trace_id, llm_needed, bool(self.llm_client),
            )

        # 3. 如果候选数仍不足，从内置模板补充
        if len(candidates) < max_candidates:
            needed = max_candidates - len(candidates)
            template_candidates = self._bootstrap_from_templates(
                market_snapshot, debate_gaps, needed,
                existing_names, trace_id,
            )
            candidates.extend(template_candidates)
            logger.info(
                "[bootstrap] 模板候选已加入, trace_id=%s, needed=%d, template_count=%d, total=%d",
                trace_id, needed, len(template_candidates), len(candidates),
            )
        else:
            logger.info(
                "[bootstrap] 候选数已满足, 跳过模板补充, trace_id=%s, current=%d, max=%d",
                trace_id, len(candidates), max_candidates,
            )

        # 4. 限制数量
        candidates = candidates[:max_candidates]

        # 5. 编译验证 + 去重标记
        validated: list[SeedCandidate] = []
        dup_count = 0
        fail_count = 0
        for cand in candidates:
            cand_name = cand.get("name", "unknown")
            cand_id = cand.get("candidate_id", "unknown")
            # 编译验证 — validate_factor_code 返回 (passed, reasons) tuple
            try:
                ok, reasons = validate_factor_code(cand["code"])
                if ok:
                    cand["is_executable"] = True
                else:
                    cand["is_executable"] = False
                    cand.setdefault("failure_reasons", []).append(
                        f"编译失败: {'; '.join(reasons)}"
                    )
            except Exception as e:
                cand["is_executable"] = False
                cand.setdefault("failure_reasons", []).append(f"编译异常: {e}")

            # 去重判断
            cand["is_duplicate"] = cand.get("name", "").lower() in existing_names
            if cand["is_duplicate"]:
                dup_count += 1
                logger.warning(
                    "[bootstrap] 重复候选, trace_id=%s, candidate_id=%s, name=%s",
                    trace_id, cand_id, cand_name,
                )
            if not cand["is_executable"]:
                fail_count += 1
            validated.append(cand)

        logger.info(
            "[bootstrap] 完成, trace_id=%s, total=%d, validated=%d, duplicates=%d, failed_compile=%d",
            trace_id, len(candidates), len(validated), dup_count, fail_count,
        )
        return validated

    def _bootstrap_with_llm(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        trace_id: str,
    ) -> list[SeedCandidate]:
        """用 LLM 生成候选因子。"""
        import time
        t0 = time.time()
        logger.info(
            "[_bootstrap_with_llm] 开始, trace_id=%s, max_candidates=%d, debate_gaps=%d, has_bootstrap=%s",
            trace_id, max_candidates, len(debate_gaps),
            hasattr(self.llm_client, "bootstrap_factors"),
        )
        try:
            if not hasattr(self.llm_client, "bootstrap_factors"):
                logger.info(
                    "[_bootstrap_with_llm] 客户端无 bootstrap_factors 方法, 跳过, trace_id=%s",
                    trace_id,
                )
                return []
            raw_candidates = self.llm_client.bootstrap_factors(
                market_snapshot, debate_gaps, max_candidates, trace_id,
            )
            elapsed = (time.time() - t0) * 1000
            logger.info(
                "[_bootstrap_with_llm] LLM 返回, trace_id=%s, elapsed_ms=%.1f, raw_count=%d",
                trace_id, elapsed, len(raw_candidates),
            )
            if not raw_candidates:
                logger.warning(
                    "[_bootstrap_with_llm] LLM 返回空候选列表, trace_id=%s",
                    trace_id,
                )
                return []
            candidates: list[SeedCandidate] = []
            skipped = 0
            for i, raw in enumerate(raw_candidates):
                name = raw.get("name", "unknown")
                code = raw.get("code", "")
                if not code:
                    skipped += 1
                    logger.warning(
                        "[_bootstrap_with_llm] 候选 %d 缺少 code, 跳过, trace_id=%s, name=%s",
                        i, trace_id, name,
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
                    economic_logic=raw.get("economic_logic", {}),
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
                trace_id, elapsed_total, len(candidates), skipped,
                [c["name"] for c in candidates],
            )
            return candidates
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            logger.error(
                "[_bootstrap_with_llm] 异常, trace_id=%s, elapsed_ms=%.1f, error=%s",
                trace_id, elapsed, e, exc_info=True,
            )
            return []

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
            )
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

            candidates.append(SeedCandidate(
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
            ))

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
        }


# ─── L1 Meta-Loop 主循环 ────────────────────────────────

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
        debates_dir: str | Path = "memory/debates",
        budget: Optional[L1BudgetConfig] = None,
        verifier: Optional[L1Verifier] = None,
        llm_client: Optional[Any] = None,
        web_collector: Optional[Callable[..., dict]] = None,
        seed_pool: Optional[SeedPool] = None,
        sample_symbols: Optional[list[str]] = None,
        market: str = "futures",
    ):
        """
        Args:
            memory_dir: L1 状态目录
            factor_pool_path: factor_pool.json 路径
            inject_dir: L1 注入因子存储目录
            debates_dir: 辩论数据目录
            budget: L1 预算配置
            verifier: L1 Verifier（None 时用默认）
            llm_client: LLM 客户端
            web_collector: f10/web_collector 函数
            seed_pool: 现有种子池
            sample_symbols: 感知层抽样品种（None 时用默认 3 个）
            market: 市场类型 ("futures" 或 "stock")，默认 "futures"
        """
        self.memory_dir = Path(memory_dir)
        self.factor_pool_path = Path(factor_pool_path)
        self.inject_dir = Path(inject_dir)
        self.debates_dir = Path(debates_dir)
        self.budget: L1BudgetConfig = dict(budget or DEFAULT_L1_BUDGET_CONFIG)  # type: ignore[assignment]
        self.verifier = verifier or L1Verifier(DEFAULT_L1_VERIFIER_CONFIG)
        self.llm_client = llm_client
        self.web_collector = web_collector
        self.market = market

        # ── 日志: 市场类型与种子池初始化 ──
        logger.info(
            "[L1.init] market=%s, seed_pool_mode=%s, sample_symbols=%s",
            market, market, sample_symbols or [
                "rb", "i", "j", "hc",
                "au", "ag", "cu",
                "sc", "ta", "ma",
                "m", "a", "y",
            ],
        )
        if market == "futures":
            logger.info(
                "[L1.init] 期货知识注入模式: 将加载 81 个期货专用种子因子 (14 大因子家族)",
            )
        elif market == "stock":
            counts = SeedPool.get_seed_counts()
            logger.info(
                "[L1.init] 股票知识注入模式: 将加载 %d 内置 + %d 外部 (WQ101=%d, Qlib158=%d, GTJA191=%d, 基本面=%d)",
                counts["stock_internal"], counts["stock_external_total"],
                counts["stock_wq101"], counts["stock_qlib158"],
                counts["stock_gtja191"], counts["stock_fundamental"],
            )
        else:
            logger.warning("[L1.init] 未知市场类型: %s", market)

        self.seed_pool = seed_pool or SeedPool(market=market)
        seed_count = len(self.seed_pool.load_all_seeds())
        logger.info(
            "[L1.init] SeedPool 初始化完成: market=%s, total_seeds=%d, seed_names=%s",
            market, seed_count, self.seed_pool.list_names()[:5],
        )

        self.sample_symbols = sample_symbols or [
            "rb", "i", "j", "hc",        # 黑色系
            "au", "ag", "cu",            # 有色金属
            "sc", "ta", "ma",            # 化工（原能源化工拆分）
            "m", "a", "y",               # 农产品
        ]  # 默认抽样 13 个期货品种，覆盖五大板块

        self.state_manager = MetaStateManager(self.memory_dir)
        self.factor_pool_manager = FactorPoolManager(self.factor_pool_path)
        self.debate_analyzer = DebateQualityAnalyzer(self.debates_dir)

        # ── 根据市场类型创建对应的三源提取器管道 ──
        if market == "futures":
            self._extractor_pipeline = FuturesExtractorPipeline(
                llm_client=self.llm_client,
            )
            logger.info(
                "[L1.init] 期货三源提取器管道已就绪: 天软/券商研报(动态)/学术论文(动态)"
            )
        elif market == "stock":
            self._extractor_pipeline = StockExtractorPipeline(
                llm_client=self.llm_client,
            )
            logger.info(
                "[L1.init] 股票三源提取器管道已就绪: 聚宽因子/券商研报(动态)/学术论文(动态)"
            )
        else:
            self._extractor_pipeline = None
            logger.warning(
                "[L1.init] 未知市场类型 %s，跳过提取器管道", market,
            )

        self.bootstrap_chain = BootstrappingChain(
            llm_client=self.llm_client,
            web_collector=self.web_collector,
            extractor_pipeline=self._extractor_pipeline,
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

        # 加载/初始化状态
        state = self.state_manager.load_or_init(budget_limit)
        # 重置 token 消耗（每日预算，新运行应从零开始）
        state["tokens_consumed"] = 0
        state = self.state_manager.mark_running(state)
        run_id = state["run_id"]

        logger.info(
            "🧠 L1 Meta-Loop 启动 (run_id=%s, trace_id=%s, market=%s, max_cand=%d, budget_limit=%d)",
            run_id, trace_id, self.market, max_cand, budget_limit,
        )
        logger.info(
            "[L1.run] 种子池状态: total=%d, names=%s",
            len(self.seed_pool.list_names()), self.seed_pool.list_names()[:10],
        )
        logger.info(
            "[L1.run] 注入目录: %s, 种子池路径: %s",
            self.inject_dir, self.factor_pool_path,
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
                snapshot_count, market_snapshot.get("skipped", False),
            )

            # ─── Step 2: debate_round 分析 ──────────────────
            logger.info("[L1.run] Step 2: debate_round 分析")
            debate_gaps, debate_gaps_detected = self._analyze_debate(state)
            logger.info(
                "[L1.run] Step 2 完成: gaps=%d, gap_topics=%s",
                debate_gaps_detected, [g.get("topic", "") for g in debate_gaps[:3]],
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

            # ─── Step 3: factorengine Bootstrapping ─────────
            logger.info(
                "[L1.run] Step 3: Bootstrapping 开始, max_cand=%d, debate_gaps=%d, existing_names=%d",
                max_cand, len(debate_gaps), len(extra_existing_names or set()),
            )
            candidates, candidates_generated = self._run_bootstrap(
                market_snapshot, debate_gaps, max_cand, trace_id, state,
                extra_existing_names=extra_existing_names,
            )
            logger.info(
                "[L1.run] Step 3 完成: candidates_generated=%d, candidate_names=%s",
                candidates_generated, [c.get("name", "") for c in candidates[:5]],
            )

            # ─── Step 4: L1 Verifier + 注入 ────────────────
            logger.info(
                "[L1.run] Step 4: Verifier + 注入开始, candidates=%d", len(candidates),
            )
            circuit_broken = self._verify_and_inject(
                candidates, state, trace_id, injected_ids, candidates_generated,
            )
            if circuit_broken:
                logger.warning(
                    "[L1.run] Step 4 熔断触发: reason=%s, injected=%d",
                    circuit_broken, len(injected_ids),
                )
                return self._make_result(
                    run_id, trace_id, candidates_generated, len(injected_ids),
                    debate_gaps_detected, tokens_consumed,
                    status="circuit_broken", circuit_breaker_reason=circuit_broken,
                    injected_ids=injected_ids,
                )

            # 估算 token 消耗
            tokens_consumed = self._estimate_tokens(candidates_generated, debate_gaps_detected)
            state["tokens_consumed"] = tokens_consumed

            # ─── 完成 ─────────────────────────────────────
            state = self.state_manager.mark_completed(state)
            logger.info(
                "✅ L1 Meta-Loop 完成 (run_id=%s, market=%s): 生成 %d, 注入 %d, tokens=%d, injected_ids=%s",
                run_id, self.market, candidates_generated, len(injected_ids),
                tokens_consumed, injected_ids,
            )
            return self._make_result(
                run_id, trace_id, candidates_generated, len(injected_ids),
                debate_gaps_detected, tokens_consumed,
                status="completed", injected_ids=injected_ids,
            )

        except Exception as e:
            logger.error(
                "❌ L1 Meta-Loop 异常 (run_id=%s, market=%s): %s",
                run_id, self.market, e, exc_info=True,
            )
            state = self.state_manager.mark_paused(state, str(e))
            return self._make_result(
                run_id, trace_id, candidates_generated, len(injected_ids),
                debate_gaps_detected, tokens_consumed,
                status="paused", circuit_breaker_reason=str(e),
                injected_ids=injected_ids,
            )

    def _analyze_debate(
        self, state: L1MetaLoopState
    ) -> tuple[list[dict[str, Any]], int]:
        """Step 2: 分析辩论记录，识别薄弱维度。"""
        debate_analysis = self.debate_analyzer.analyze_latest_debate()
        debate_gaps = debate_analysis.get("topics", [])
        debate_gaps_detected = len(debate_gaps)
        state["total_debate_gaps_detected"] = (
            state.get("total_debate_gaps_detected", 0) + debate_gaps_detected
        )
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
        state["total_candidates_generated"] = (
            state.get("total_candidates_generated", 0) + candidates_generated
        )
        state["last_bootstrap_topic"] = (
            candidates[0].get("parent_topic", "") if candidates else ""
        )
        return candidates, candidates_generated

    def _scan_injected_names(self) -> set[str]:
        """扫描 l1_injected/ 目录，返回已注入因子名称（小写）。"""
        if not self.inject_dir.exists():
            return set()
        names: set[str] = set()
        for f in self.inject_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    cand = json.load(fp)
                    name = cand.get("name", "")
                    if name:
                        names.add(name.lower())
            except (json.JSONDecodeError, OSError):
                continue
        return names

    def _verify_and_inject(
        self,
        candidates: list[SeedCandidate],
        state: L1MetaLoopState,
        trace_id: str,
        injected_ids: list[str],
        candidates_generated: int,
    ) -> Optional[str]:
        """Step 4: L1 Verifier 检查 + 注入候选因子。返回熔断原因或 None。"""
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
        for i, cand in enumerate(candidates):
            cand_name = cand.get("name", "unknown")
            cand_id = cand.get("candidate_id", "unknown")

            cb_reason = self._check_circuit_breaker(state, candidates_generated)
            if cb_reason:
                logger.warning(
                    "[L1.verify] 熔断触发: reason=%s, 已处理=%d/%d, 剩余跳过",
                    cb_reason, i, len(candidates),
                )
                state = self.state_manager.mark_circuit_broken(state, cb_reason)
                return cb_reason

            verdict = self.verifier.check(cand, self.seed_pool)
            cand["passed_l1_verifier"] = verdict["passed"]
            cand["failure_reasons"] = verdict["failure_reasons"]

            if not verdict["passed"]:
                rejected_count += 1
                logger.warning(
                    "[L1.verify] 候选[%d/%d] 未通过: name=%s, candidate_id=%s, reasons=%s",
                    i + 1, len(candidates), cand_name, cand_id, verdict["failure_reasons"],
                )
                self._consecutive_low_quality += 1
                continue

            passed_count += 1
            logger.info(
                "[L1.verify] 候选[%d/%d] 通过: name=%s, candidate_id=%s, source=%s, code_len=%d",
                i + 1, len(candidates), cand_name, cand_id,
                cand.get("source", "unknown"), len(cand.get("code", "")),
            )

            injected_id = self._inject_candidate(cand, trace_id)
            if injected_id:
                injected_ids.append(injected_id)
                state["total_candidates_injected"] = (
                    state.get("total_candidates_injected", 0) + 1
                )
                state.setdefault("candidates_ref", []).append(injected_id)
                self._consecutive_low_quality = 0

        logger.info(
            "[L1.verify] 验证注入完成: total=%d, passed=%d, rejected=%d, injected=%d, consecutive_low_quality=%d",
            len(candidates), passed_count, rejected_count, len(injected_ids), self._consecutive_low_quality,
        )
        return None

    @staticmethod
    def _make_result(
        run_id: str, trace_id: str,
        candidates_generated: int, candidates_injected: int,
        debate_gaps_detected: int, tokens_consumed: int,
        status: str, circuit_breaker_reason: Optional[str] = None,
        injected_ids: Optional[list[str]] = None,
    ) -> MetaRunResult:
        """构造 MetaRunResult。"""
        return MetaRunResult(
            run_id=run_id, trace_id=trace_id,
            candidates_generated=candidates_generated,
            candidates_injected=candidates_injected,
            debate_gaps_detected=debate_gaps_detected,
            tokens_consumed=tokens_consumed,
            status=status,
            circuit_breaker_reason=circuit_breaker_reason,
            injected_candidate_ids=injected_ids or [],
        )

    def _perceive_market(self, trace_id: str) -> dict[str, Any]:
        """Step 1: agentic 感知 — f10/web_collector 拉取市场快照。"""
        if self.web_collector is None:
            logger.info("L1 Step 1: 未配置 web_collector, 跳过感知")
            return {"trace_id": trace_id, "snapshots": {}, "skipped": True}

        snapshots: dict[str, Any] = {}
        for sym in self.sample_symbols:
            try:
                snap = self.web_collector(sym)
                snapshots[sym] = snap
            except Exception as e:
                logger.warning("L1 感知 %s 失败: %s", sym, e)
                snapshots[sym] = {"error": str(e)}

        return {
            "trace_id": trace_id,
            "snapshots": snapshots,
            "skipped": False,
        }

    def _inject_candidate(self, cand: SeedCandidate, trace_id: str) -> Optional[str]:
        """Step 5: 注入候选到 L2 种子池入口。"""
        cand_name = cand.get("name", "unknown")
        cand_id = cand.get("candidate_id", "unknown")
        logger.info(
            "[L1.inject] 开始注入: name=%s, candidate_id=%s, source=%s, code_len=%d",
            cand_name, cand_id, cand.get("source", "unknown"), len(cand.get("code", "")),
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
                trace_id=trace_id,
                created_at=cand.get("created_at", datetime.now().isoformat()),
                updated_at=datetime.now().isoformat(),
            )
            self.factor_pool_manager.add_entry(entry)
            logger.info("[L1.inject] factor_pool.json 更新完成: factor_id=%s, priority=%s", cand["candidate_id"], entry["priority"])

            # 3. 标记候选已注入
            cand["injected_to_l2"] = True
            cand["injected_at"] = datetime.now().isoformat()

            logger.info(
                "✅ [L1.inject] 注入成功: name=%s, candidate_id=%s, file=%s",
                cand_name, cand_id, inject_file,
            )
            return cand["candidate_id"]

        except Exception as e:
            logger.error(
                "❌ [L1.inject] 注入失败: name=%s, candidate_id=%s, error=%s",
                cand_name, cand_id, e, exc_info=True,
            )
            return None

    @staticmethod
    def _compute_priority(cand: SeedCandidate) -> str:
        """根据经济逻辑和 debate_gap 计算优先级。"""
        economic = cand.get("economic_logic", {})
        total_score = (
            economic.get("theory", 0) + economic.get("behavioral", 0)
            + economic.get("microstructure", 0) + economic.get("institutional", 0)
        )
        if cand.get("debate_gap") or total_score >= 16:
            return "high"
        if total_score >= 12:
            return "medium"
        return "low"

    def _check_circuit_breaker(
        self, state: L1MetaLoopState, candidates_generated: int
    ) -> Optional[str]:
        """熔断检查。返回原因字符串（None = 未触发）。"""
        # 1. Token 超 2x
        tokens = state.get("tokens_consumed", 0)
        limit = state.get("budget_limit", self.budget["daily_token_limit"])
        if tokens > limit * self.budget["circuit_breaker_token_ratio"]:
            return (
                f"Token 熔断: {tokens} > {limit} * "
                f"{self.budget['circuit_breaker_token_ratio']}"
            )

        # 2. 失败率 > 95%
        evaluated = state.get("total_candidates_generated", 0) + candidates_generated
        injected = state.get("total_candidates_injected", 0)
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

def _make_web_collector(provider: Any | None = None) -> Callable[..., dict]:
    """创建 web_collector 可调用对象 — 基于 FTSDataProvider 的市场快照采集。

    Args:
        provider: FTSDataProvider 实例（None 时惰性初始化）

    Returns:
        Callable(symbol: str) -> dict — 市场快照，包含 quote、kline、news 等字段
    """
    lazy_provider: Any | None = provider

    def _collect(symbol: str) -> dict:
        """采集单个品种的市场快照。"""
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

        # 1. 获取 OHLCV 数据
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

        # 2. 获取实时价格
        try:
            from fts.data_futures import get_realtime_prices
            prices = get_realtime_prices([contract_symbol])
            if contract_symbol in prices:
                result["quote"]["realtime_price"] = prices[contract_symbol]
        except Exception as e:
            result["warnings"].append(f"实时价获取失败: {e}")

        return result

    return _collect


# ─── CLI 入口 ───────────────────────────────────────────

def main():
    """CLI 入口: python -m fts.factor_engine.meta_loop --once [--market stock]"""
    parser = argparse.ArgumentParser(description="L1 Meta-Loop 知识补给循环")
    parser.add_argument("--once", action="store_true", help="运行一次完整 L1 循环")
    parser.add_argument(
        "--max-bootstraps", type=int, default=None,
        help="最大 Bootstrapping 数（默认 5）",
    )
    parser.add_argument(
        "--memory-dir", default="memory/meta_loop",
        help="L1 状态目录（默认 memory/meta_loop）",
    )
    parser.add_argument(
        "--factor-pool", default="memory/knowledge/factors/factor_pool.json",
        help="factor_pool.json 路径",
    )
    parser.add_argument(
        "--inject-dir", default="memory/knowledge/factors/l1_injected",
        help="L1 注入因子存储目录",
    )
    parser.add_argument(
        "--market", default="futures", choices=["futures", "stock"],
        help="市场类型: futures（期货，默认）或 stock（股票）",
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
    web_collector = _make_web_collector(provider)
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
    # 主循环
    "MetaLoop",
    "MetaRunResult",
    # CLI
    "main",
]
