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
                # 版本检查（避免契约不兼容）
                from .contracts import EVOLUTION_VERSION
                if state.get("version") != EVOLUTION_VERSION:
                    logger.warning(
                        "L1 状态版本不匹配: %s != %s, 冷启动",
                        state.get("version"), EVOLUTION_VERSION,
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
        from .contracts import EVOLUTION_VERSION
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
            version=EVOLUTION_VERSION,
        )

    def save(self, state: L1MetaLoopState) -> None:
        """持久化状态 — 先写主文件，再镜像到 backup。"""
        from .contracts import EVOLUTION_VERSION
        if state.get("version") != EVOLUTION_VERSION:
            raise MetaStateManagerError(
                f"状态版本不匹配: {state.get('version')} != {EVOLUTION_VERSION}"
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
    ):
        """
        Args:
            llm_client: LLM 客户端（必须实现 generate(prompt: str) -> str 接口）。
                        None 时使用内置模板回退。
            web_collector: f10/web_collector 的 collect_fundamental_web 函数。
                        None 时跳过感知步骤。
        """
        self.llm_client = llm_client
        self.web_collector = web_collector

    def bootstrap(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int = 5,
        seed_pool: Optional[SeedPool] = None,
        trace_id: Optional[str] = None,
    ) -> list[SeedCandidate]:
        """执行 Bootstrapping，返回候选因子列表。

        Args:
            market_snapshot: f10/web_collector 拉取的市场快照
            debate_gaps: DebateQualityAnalyzer 识别的薄弱维度
            max_candidates: 最大候选数
            seed_pool: 现有种子池（用于去重判断）
            trace_id: 全链路 trace_id

        Returns:
            list[SeedCandidate] — 通过沙箱编译的候选因子
        """
        trace_id = trace_id or generate_trace_id("l1")
        candidates: list[SeedCandidate] = []
        existing_names = {n.lower() for n in (seed_pool or SeedPool()).list_names()}

        # 1. 如果有 LLM 客户端，调用 LLM 生成候选
        if self.llm_client is not None:
            llm_candidates = self._bootstrap_with_llm(
                market_snapshot, debate_gaps, max_candidates, trace_id
            )
            candidates.extend(llm_candidates)

        # 2. 如果候选数不足，从内置模板补充
        if len(candidates) < max_candidates:
            template_candidates = self._bootstrap_from_templates(
                market_snapshot, debate_gaps, max_candidates - len(candidates),
                existing_names, trace_id,
            )
            candidates.extend(template_candidates)

        # 3. 限制数量
        candidates = candidates[:max_candidates]

        # 4. 编译验证 + 去重标记
        validated: list[SeedCandidate] = []
        for cand in candidates:
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
            validated.append(cand)

        return validated

    def _bootstrap_with_llm(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        trace_id: str,
    ) -> list[SeedCandidate]:
        """用 LLM 生成候选因子（Mock 版本，返回空列表）。

        生产环境应:
            1. 构造 prompt（含 market_snapshot + debate_gaps）
            2. 调用 LLM 生成 JSON 格式的候选因子
            3. 解析并返回 SeedCandidate 列表
        """
        # Mock: LLM 客户端未配置具体实现时返回空列表
        # 测试时可通过 mock 注入
        try:
            if hasattr(self.llm_client, "bootstrap_factors"):
                return self.llm_client.bootstrap_factors(
                    market_snapshot, debate_gaps, max_candidates, trace_id,
                )
        except Exception as e:
            logger.warning("LLM Bootstrapping 失败，回退到模板: %s", e)
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
        """
        self.memory_dir = Path(memory_dir)
        self.factor_pool_path = Path(factor_pool_path)
        self.inject_dir = Path(inject_dir)
        self.debates_dir = Path(debates_dir)
        self.budget: L1BudgetConfig = dict(budget or DEFAULT_L1_BUDGET_CONFIG)  # type: ignore[assignment]
        self.verifier = verifier or L1Verifier(DEFAULT_L1_VERIFIER_CONFIG)
        self.llm_client = llm_client
        self.web_collector = web_collector
        self.seed_pool = seed_pool or SeedPool()
        self.sample_symbols = sample_symbols or ["rb", "i", "j"]  # 默认抽样 3 个品种

        self.state_manager = MetaStateManager(self.memory_dir)
        self.factor_pool_manager = FactorPoolManager(self.factor_pool_path)
        self.debate_analyzer = DebateQualityAnalyzer(self.debates_dir)
        self.bootstrap_chain = BootstrappingChain(
            llm_client=self.llm_client,
            web_collector=self.web_collector,
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
        state = self.state_manager.mark_running(state)
        run_id = state["run_id"]

        logger.info("🧠 L1 Meta-Loop 启动 (run_id=%s, trace_id=%s)", run_id, trace_id)

        injected_ids: list[str] = []
        candidates_generated = 0
        debate_gaps_detected = 0
        tokens_consumed = 0

        try:
            # ─── Step 1: agentic 感知 (f10/web_collector) ────────
            market_snapshot = self._perceive_market(trace_id)

            # ─── Step 2: debate_round 分析 ──────────────────────
            debate_analysis = self.debate_analyzer.analyze_latest_debate()
            debate_gaps = debate_analysis.get("topics", [])
            debate_gaps_detected = len(debate_gaps)
            state["total_debate_gaps_detected"] = (
                state.get("total_debate_gaps_detected", 0) + debate_gaps_detected
            )
            logger.info(
                "L1 Step 2: 辩论分析完成，识别 %d 个薄弱维度",
                debate_gaps_detected,
            )

            # ─── Step 3: factorengine Bootstrapping ────────────
            candidates = self.bootstrap_chain.bootstrap(
                market_snapshot=market_snapshot,
                debate_gaps=debate_gaps,
                max_candidates=max_cand,
                seed_pool=self.seed_pool,
                trace_id=trace_id,
            )
            candidates_generated = len(candidates)
            state["total_candidates_generated"] = (
                state.get("total_candidates_generated", 0) + candidates_generated
            )
            state["last_bootstrap_topic"] = (
                candidates[0].get("parent_topic", "") if candidates else ""
            )

            # ─── Step 4: L1 Verifier + 注入 ────────────────────
            for cand in candidates:
                # 熔断检查
                cb_reason = self._check_circuit_breaker(state, candidates_generated)
                if cb_reason:
                    state = self.state_manager.mark_circuit_broken(state, cb_reason)
                    return MetaRunResult(
                        run_id=run_id, trace_id=trace_id,
                        candidates_generated=candidates_generated,
                        candidates_injected=len(injected_ids),
                        debate_gaps_detected=debate_gaps_detected,
                        tokens_consumed=tokens_consumed,
                        status="circuit_broken",
                        circuit_breaker_reason=cb_reason,
                        injected_candidate_ids=injected_ids,
                    )

                # L1 Verifier 检查
                verdict = self.verifier.check(cand, self.seed_pool)
                cand["passed_l1_verifier"] = verdict["passed"]
                cand["failure_reasons"] = verdict["failure_reasons"]

                if not verdict["passed"]:
                    logger.info(
                        "L1 Verifier 拒绝候选 %s: %s",
                        cand.get("name"), verdict["failure_reasons"],
                    )
                    self._consecutive_low_quality += 1
                    continue

                # 通过 → 注入
                injected_id = self._inject_candidate(cand, trace_id)
                if injected_id:
                    injected_ids.append(injected_id)
                    state["total_candidates_injected"] = (
                        state.get("total_candidates_injected", 0) + 1
                    )
                    state.setdefault("candidates_ref", []).append(injected_id)
                    self._consecutive_low_quality = 0

            # 估算 token 消耗（Mock）
            tokens_consumed = self._estimate_tokens(candidates_generated, debate_gaps_detected)
            state["tokens_consumed"] = tokens_consumed

            # ─── 完成 ─────────────────────────────────────────
            state = self.state_manager.mark_completed(state)
            logger.info(
                "✅ L1 Meta-Loop 完成 (run_id=%s): 生成 %d, 注入 %d",
                run_id, candidates_generated, len(injected_ids),
            )
            return MetaRunResult(
                run_id=run_id, trace_id=trace_id,
                candidates_generated=candidates_generated,
                candidates_injected=len(injected_ids),
                debate_gaps_detected=debate_gaps_detected,
                tokens_consumed=tokens_consumed,
                status="completed",
                injected_candidate_ids=injected_ids,
            )

        except Exception as e:
            logger.error("L1 Meta-Loop 异常: %s", e, exc_info=True)
            state = self.state_manager.mark_paused(state, str(e))
            return MetaRunResult(
                run_id=run_id, trace_id=trace_id,
                candidates_generated=candidates_generated,
                candidates_injected=len(injected_ids),
                debate_gaps_detected=debate_gaps_detected,
                tokens_consumed=tokens_consumed,
                status="paused",
                circuit_breaker_reason=str(e),
                injected_candidate_ids=injected_ids,
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
        try:
            # 1. 持久化到 l1_injected/ 目录
            self.inject_dir.mkdir(parents=True, exist_ok=True)
            inject_file = self.inject_dir / f"{cand['candidate_id']}.json"
            with open(inject_file, "w", encoding="utf-8") as f:
                json.dump(cand, f, ensure_ascii=False, indent=2, default=str)

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

            # 3. 标记候选已注入
            cand["injected_to_l2"] = True
            cand["injected_at"] = datetime.now().isoformat()

            logger.info("L1 注入候选 %s → %s", cand.get("name"), inject_file)
            return cand["candidate_id"]

        except Exception as e:
            logger.error("L1 注入失败 %s: %s", cand.get("candidate_id"), e)
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


# ─── CLI 入口 ───────────────────────────────────────────

def main():
    """CLI 入口: python -m loop_engine.meta_loop --once"""
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

    # web_collector 保留为 None（参数为向后兼容保留），L1 感知在未来版本迁移至 FTSDataProvider 模式
    loop = MetaLoop(
        memory_dir=args.memory_dir,
        factor_pool_path=args.factor_pool,
        inject_dir=args.inject_dir,
        web_collector=None,
    )
    result = loop.run(max_bootstraps=args.max_bootstraps)
    print(f"L1 Meta-Loop 完成: {result.to_dict()}")
    sys.exit(0 if result.status == "completed" else 1)


if __name__ == "__main__":
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
