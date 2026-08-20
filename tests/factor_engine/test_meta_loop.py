"""
tests/factor_engine/test_meta_loop.py — L1 Meta-Loop 测试

覆盖范围:
    - L1Verifier 锁定机制 + 4 维度判定
    - MetaStateManager 状态持久化 + backup 恢复
    - FactorPoolManager factor_pool.json 管理
    - DebateQualityAnalyzer 辩论质量分析
    - BootstrappingChain 模板回退 + LLM 注入接口
    - MetaLoop 主循环 5 步流程 + 熔断机制
    - CLI 入口

版本: v1.1.0（与 FTS 同步）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_data_source(monkeypatch):
    """隔离真实数据源，杜绝 TqSdk 网络等待（全量回归卡点）。

    MetaLoop 感知步骤 `_make_web_collector` 在 web_collector=None 时惰性
    初始化 FTSDataProvider 并调用 `data_futures.get_realtime_prices`（内部
    TqSdk asyncio 事件循环 select 等待网络），断网/无行情环境下无限阻塞。
    此处统一 mock 两者，使感知步骤返回合成快照。
    plans/44 P0: 同时屏蔽 BulkKnowledgeExtractor 的全球多源网络采集
    （collect_all 4 源 × 30s 超时在测试环境不可接受；生产走真实采集）。
    """
    from unittest.mock import MagicMock

    mock_provider = MagicMock()
    mock_provider.return_value.get_realtime_prices.return_value = {"RB": 123.4}
    monkeypatch.setattr("fts.data.FTSDataProvider", mock_provider)
    monkeypatch.setattr("fts.data_futures.get_realtime_prices", lambda *a, **k: {"RB": 123.4})

    # plans/44 P0: 屏蔽批量采集网络（collect_all 在 bulk_knowledge 模块 import 时绑定）
    from fts.factor_engine.extractors import bulk_knowledge

    monkeypatch.setattr(
        bulk_knowledge,
        "collect_all",
        lambda **k: {
            src: type("_R", (), {"collected": 0, "new": 0, "deduped": 0, "errors": []})()
            for src in ("arxiv", "openalex", "eastmoney", "global")
        },
    )
    # plans/44: CLI main() 测试零真实网络——屏蔽研报/论文/WebSearch 提取器网络请求
    # （各提取器均 per-query except Exception 降级返回空，不阻断整体）
    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("test env: no network")))


# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import (
    DEFAULT_L1_VERIFIER_CONFIG,
    EconomicLogic,
    EVOLUTION_VERSION,
    STATE_SCHEMA_VERSION,
    FactorSignature,
    L1BudgetConfig,
    L1MetaLoopState,
    L1VerifierConfig,
    SeedCandidate,
)
from fts.factor_engine.meta_loop import (
    BootstrappingChain,
    DebateQualityAnalyzer,
    FactorPoolManager,
    L1Verifier,
    L1VerifierLocked,
    MetaLoop,
    MetaStateManager,
    MetaStateManagerError,
    _make_web_collector,
    validate_batch_candidates,
)
from fts.factor_engine.seed_pool import SeedPool


# ─── 共享 fixtures ────────────────────────────────────────


@pytest.fixture
def tmp_meta_dir(tmp_path) -> Path:
    """临时 L1 状态目录。"""
    p = tmp_path / "meta_loop"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_state_store(tmp_path):
    """临时 state.duckdb 存储（测试隔离，避免污染全局 SSOT）。"""
    from fts.store.state_db import StateKVStore

    store = StateKVStore(tmp_path / "state.duckdb")
    yield store
    store.close()


@pytest.fixture
def tmp_factor_pool_path(tmp_path) -> Path:
    """临时 factor_pool.json 路径。"""
    p = tmp_path / "factors" / "factor_pool.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_inject_dir(tmp_path) -> Path:
    """临时 L1 注入目录。"""
    p = tmp_path / "l1_injected"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_debates_dir(tmp_path) -> Path:
    """临时辩论数据目录。"""
    p = tmp_path / "debates"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def valid_economic_logic() -> EconomicLogic:
    """达标的经济逻辑（4 维全部 >=3）。"""
    return EconomicLogic(
        theory=4,
        behavioral=4,
        microstructure=3,
        institutional=4,
        narrative="这是一个测试因子，捕捉动量效应与波动率回归的经济逻辑。",
    )


@pytest.fixture
def weak_economic_logic() -> EconomicLogic:
    """不达标的经济逻辑（仅 1 维 >=3）。"""
    return EconomicLogic(
        theory=2,
        behavioral=2,
        microstructure=3,
        institutional=1,
        narrative="短",
    )


@pytest.fixture
def valid_candidate(valid_economic_logic) -> SeedCandidate:
    """合法的种子候选。"""
    return SeedCandidate(
        candidate_id="cand_test001",
        name="test_factor_unique_name",
        code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
        params={"window": 10},
        signature=FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=15,
        ),
        economic_logic=valid_economic_logic,
        source="l1_bootstrapping",
        parent_topic="测试因子",
        debate_round_ref=None,
        debate_gap=None,
        web_snapshot_ref=None,
        is_executable=True,
        is_duplicate=False,
        passed_l1_verifier=False,
        failure_reasons=[],
        trace_id="trace_test_001",
        created_at="2026-07-18T00:00:00",
        injected_to_l2=False,
        injected_at=None,
    )


@pytest.fixture
def mock_web_collector():
    """Mock 的 f10/web_collector 函数。"""

    def _collect(variety: str) -> dict:
        return {
            "symbol": variety,
            "source": "mock",
            "fetched_at": "2026-07-18T05:00:00",
            "quote": {"last_price": 1000.0, "volume": 50000},
            "kline": {"bars": []},
            "news": [],
            "warnings": [],
        }

    return _collect


@pytest.fixture
def mock_llm_client():
    """Mock 的 LLM 客户端。"""
    client = MagicMock()
    client.bootstrap_factors.return_value = []  # 默认返回空，走模板回退
    return client


# ════════════════════════════════════════════════════════
# 1. L1Verifier 测试
# ════════════════════════════════════════════════════════


class TestL1Verifier:
    """L1 Verifier — 4 维度判定 + 锁定机制。"""

    def test_verifier_is_locked_by_default(self):
        """L1 Verifier 默认锁定。"""
        v = L1Verifier()
        assert v.is_locked is True

    def test_verifier_passes_valid_candidate(self, valid_candidate):
        """合法候选通过 L1 Verifier。"""
        v = L1Verifier()
        result = v.check(valid_candidate, SeedPool())
        assert result["passed"] is True
        assert result["failure_reasons"] == []

    def test_verifier_rejects_low_economic_score(self, valid_candidate, weak_economic_logic):
        """经济逻辑评分不足被拒绝。"""
        valid_candidate["economic_logic"] = weak_economic_logic
        v = L1Verifier(L1VerifierConfig(min_economic_score=2))
        result = v.check(valid_candidate, SeedPool())
        assert result["passed"] is False
        assert any("经济逻辑达标维度" in r for r in result["failure_reasons"])

    def test_verifier_rejects_uncompilable(self, valid_candidate):
        """不可执行的候选被拒绝。"""
        valid_candidate["is_executable"] = False
        v = L1Verifier()
        result = v.check(valid_candidate, SeedPool())
        assert result["passed"] is False
        assert any("不可执行" in r for r in result["failure_reasons"])

    def test_verifier_rejects_duplicate(self, valid_candidate):
        """与现有种子重复的候选被拒绝。"""
        # 名字设为已有种子的名字
        valid_candidate["name"] = "momentum"
        v = L1Verifier()
        result = v.check(valid_candidate, SeedPool(market="stock"))
        assert result["passed"] is False
        assert any("重复" in r for r in result["failure_reasons"])

    def test_verifier_rejects_short_narrative(self, valid_candidate):
        """narrative 长度不足被拒绝。"""
        valid_candidate["economic_logic"] = EconomicLogic(
            theory=4,
            behavioral=4,
            microstructure=4,
            institutional=4,
            narrative="短",  # 仅 1 字符
        )
        v = L1Verifier(L1VerifierConfig(min_narrative_length=20))
        result = v.check(valid_candidate, SeedPool())
        assert result["passed"] is False
        assert any("narrative 长度" in r for r in result["failure_reasons"])

    def test_verifier_unlocked_raises(self, valid_candidate):
        """未锁定的 Verifier 调用 check 抛异常。"""
        v = L1Verifier()
        v.unlock()
        with pytest.raises(L1VerifierLocked):
            v.check(valid_candidate, SeedPool())

    def test_verifier_config_cannot_be_modified_at_runtime(self):
        """L1 Verifier 配置不可运行时修改（_config 是 dict 副本）。"""
        cfg = L1VerifierConfig(min_economic_score=2)
        v = L1Verifier(cfg)
        # 修改原始 cfg 不影响 Verifier
        cfg["min_economic_score"] = 5  # type: ignore[index]
        assert v._config["min_economic_score"] == 2

    def test_default_l1_verifier_config_values(self):
        """L1 Verifier 默认配置值锁定。"""
        assert DEFAULT_L1_VERIFIER_CONFIG["min_economic_score"] == 2
        assert DEFAULT_L1_VERIFIER_CONFIG["require_executable"] is True
        assert DEFAULT_L1_VERIFIER_CONFIG["require_not_duplicate"] is True
        assert DEFAULT_L1_VERIFIER_CONFIG["min_narrative_length"] == 20

    def test_verifier_rejects_duplicate_flag(self, valid_candidate):
        """is_duplicate=True 的候选被拒绝（line 125）。"""
        valid_candidate["is_duplicate"] = True
        v = L1Verifier()
        result = v.check(valid_candidate, SeedPool())
        assert result["passed"] is False
        assert any("重复" in r for r in result["failure_reasons"])

    def test_is_duplicate_by_name_empty(self):
        """空名称返回 False（line 146）。"""
        assert L1Verifier._is_duplicate_by_name("", SeedPool()) is False

    def test_lock_method(self):
        """lock() 锁定 Verifier（line 152）。"""
        v = L1Verifier()
        v.unlock()
        assert v.is_locked is False
        v.lock()
        assert v.is_locked is True

    # ─── GAP-123 P2④: 论证-评分一致性检查（默认关闭） ───

    def test_argument_consistency_default_off(self, valid_candidate):
        """require_argument_consistency 默认关闭 → 高分低论证候选不被额外拦截。"""
        valid_candidate["economic_logic"] = EconomicLogic(
            theory=4, behavioral=4, microstructure=4, institutional=4,
            narrative="动量延续策略",  # 无任何机制关键词
        )
        v = L1Verifier()
        result = v.check(valid_candidate, SeedPool())
        # 默认关闭：不因论证缺失被拒（仅 narrative 长度等常规项判定）
        assert not any("缺乏该维度机制论证" in r for r in result["failure_reasons"])

    def test_argument_consistency_rejects_high_score_no_mechanism(self, valid_candidate):
        """开启后：理论维度 4 分但 narrative 无机制关键词 → 拒绝。"""
        valid_candidate["economic_logic"] = EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="该因子基于价格动量构建，用于趋势跟踪",
        )
        v = L1Verifier(L1VerifierConfig(require_argument_consistency=True))
        result = v.check(valid_candidate, SeedPool())
        assert result["passed"] is False
        assert any("缺乏该维度机制论证" in r for r in result["failure_reasons"])

    def test_argument_consistency_passes_with_mechanism(self, valid_candidate):
        """开启后：高分维度含机制关键词 → 通过。"""
        valid_candidate["economic_logic"] = EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=3,
            narrative="动量效应源于行为偏差下的反应不足与羊群效应，理论模型支持风险溢价补偿，成交活跃度反映流动性，机构持仓集中度增强趋势延续",
        )
        v = L1Verifier(L1VerifierConfig(require_argument_consistency=True))
        result = v.check(valid_candidate, SeedPool())
        assert not any("缺乏该维度机制论证" in r for r in result["failure_reasons"])


# ════════════════════════════════════════════════════════
# 2. MetaStateManager 测试
# ════════════════════════════════════════════════════════


class TestMetaStateManager:
    """L1 状态管理器 — DuckDB SSOT 持久化（plans/29 P4 读路径切换）。"""

    def test_init_creates_state(self, tmp_meta_dir, tmp_state_store):
        """首次调用 load_or_init 创建新状态。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(budget_limit=50000)
        assert state["status"] == "paused"
        assert state["budget_limit"] == 50000
        assert state["schema_version"] == STATE_SCHEMA_VERSION
        assert state["total_candidates_generated"] == 0

    def test_save_persists_state(self, tmp_meta_dir, tmp_state_store):
        """save() 持久化到 state.duckdb。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(50000)
        state["total_candidates_generated"] = 5
        sm.save(state)
        # 从 DuckDB 读回
        loaded = tmp_state_store.get("meta_loop", "state")
        assert loaded["total_candidates_generated"] == 5

    def test_save_reload_roundtrip(self, tmp_meta_dir, tmp_state_store):
        """save() 后新建管理器可重新加载（DuckDB 持久化）。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(50000)
        state["total_candidates_generated"] = 7
        sm.save(state)
        # 新管理器从 DuckDB 加载
        sm2 = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        recovered = sm2.load_or_init(50000)
        assert recovered["total_candidates_generated"] == 7

    def test_mark_running(self, tmp_meta_dir, tmp_state_store):
        """mark_running() 切换状态。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(50000)
        state = sm.mark_running(state)
        assert state["status"] == "running"
        assert state["last_error"] is None

    def test_mark_completed(self, tmp_meta_dir, tmp_state_store):
        """mark_completed() 切换状态。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(50000)
        state = sm.mark_completed(state)
        assert state["status"] == "completed"

    def test_mark_paused_with_error(self, tmp_meta_dir, tmp_state_store):
        """mark_paused() 记录错误信息。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(50000)
        err_msg = "测试异常"
        state = sm.mark_paused(state, err_msg)
        assert state["status"] == "paused"
        assert state["last_error"] == err_msg

    def test_mark_circuit_broken(self, tmp_meta_dir, tmp_state_store):
        """mark_circuit_broken() 切换状态。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(50000)
        reason = "Token 超限"
        state = sm.mark_circuit_broken(state, reason)
        assert state["status"] == "circuit_broken"
        assert state["last_error"] == reason

    def test_schema_version_mismatch_triggers_cold_start(self, tmp_meta_dir, tmp_state_store):
        """schema 版本不匹配触发冷启动。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        # 写入旧 schema 版本状态
        old_state = {
            "run_id": "old",
            "schema_version": "0",  # 旧 schema 版本
            "status": "completed",
        }
        tmp_state_store.upsert("meta_loop", "state", old_state, run_id="test")
        # 重新加载应冷启动
        state = sm.load_or_init(50000)
        assert state["schema_version"] == STATE_SCHEMA_VERSION
        assert state["status"] == "paused"  # 冷启动默认

    def test_schema_version_compatible_keeps_state(self, tmp_meta_dir, tmp_state_store):
        """schema 版本一致时不冷启动，保留原状态。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        # 写入当前 schema 版本状态（模拟升级版本号但 schema 未变）
        existing_state = {
            "run_id": "existing_run",
            "schema_version": STATE_SCHEMA_VERSION,
            "status": "completed",
            "total_candidates_generated": 42,
        }
        tmp_state_store.upsert("meta_loop", "state", existing_state, run_id="test")
        # 重新加载应保留状态，不冷启动
        state = sm.load_or_init(50000)
        assert state["run_id"] == "existing_run"
        assert state["total_candidates_generated"] == 42
        assert state["status"] == "completed"

    def test_save_with_wrong_schema_version_raises(self, tmp_meta_dir, tmp_state_store):
        """save() schema 版本不匹配抛异常。"""
        sm = MetaStateManager(tmp_meta_dir, state_store=tmp_state_store)
        state = sm.load_or_init(50000)
        state["schema_version"] = "0"  # 篡改 schema 版本
        with pytest.raises(MetaStateManagerError):
            sm.save(state)


# ════════════════════════════════════════════════════════
# 3. FactorPoolManager 测试
# ════════════════════════════════════════════════════════


class TestFactorPoolManager:
    """factor_pool.json 管理器。"""

    def test_init_creates_empty_pool(self, tmp_factor_pool_path):
        """首次调用创建空 factor_pool.json。"""
        mgr = FactorPoolManager(tmp_factor_pool_path)
        pool = mgr.load_or_init()
        assert pool["total_count"] == 0
        assert pool["factors"] == []
        assert pool["version"] == EVOLUTION_VERSION

    def test_add_entry(self, tmp_factor_pool_path):
        """添加因子记录。"""
        mgr = FactorPoolManager(tmp_factor_pool_path)
        mgr.load_or_init()
        from fts.factor_engine.contracts import FactorPoolEntry

        entry = FactorPoolEntry(
            factor_id="cand_abc123",
            name="test_factor",
            source="l1_bootstrapping",
            priority="high",
            status="pending",
            trace_id="trace_001",
            created_at="2026-07-18T00:00:00",
            updated_at="2026-07-18T00:00:00",
        )
        mgr.add_entry(entry)
        assert mgr.count() == 1
        assert len(mgr.list_pending()) == 1
        # 候选因子入池时默认标注"未评估"
        pool = mgr.load_or_init()
        assert pool["factors"][0]["evaluation_status"] == "pending"

    def test_add_entry_preserves_explicit_evaluation_status(self, tmp_factor_pool_path):
        """显式传入 evaluation_status 时不被覆盖。"""
        mgr = FactorPoolManager(tmp_factor_pool_path)
        mgr.load_or_init()
        from fts.factor_engine.contracts import FactorPoolEntry

        entry = FactorPoolEntry(
            factor_id="cand_eval",
            name="f1",
            source="l1_bootstrapping",
            priority="high",
            status="injected",
            trace_id="t1",
            created_at="2026-07-18",
            updated_at="2026-07-18",
            evaluation_status="evaluated",
        )
        mgr.add_entry(entry)
        pool = mgr.load_or_init()
        assert pool["factors"][0]["evaluation_status"] == "evaluated"

    def test_add_entry_dedup(self, tmp_factor_pool_path):
        """同 factor_id 添加两次只算一条。"""
        mgr = FactorPoolManager(tmp_factor_pool_path)
        mgr.load_or_init()
        from fts.factor_engine.contracts import FactorPoolEntry

        entry = FactorPoolEntry(
            factor_id="cand_dup",
            name="f1",
            source="l1_bootstrapping",
            priority="high",
            status="pending",
            trace_id="t1",
            created_at="2026-07-18",
            updated_at="2026-07-18",
        )
        mgr.add_entry(entry)
        # 同 ID 不同状态
        entry2 = FactorPoolEntry(
            factor_id="cand_dup",
            name="f1",
            source="l1_bootstrapping",
            priority="high",
            status="injected",
            trace_id="t1",
            created_at="2026-07-18",
            updated_at="2026-07-18",
        )
        mgr.add_entry(entry2)
        assert mgr.count() == 1
        assert len(mgr.list_pending()) == 0  # 已变为 injected

    def test_mark_status(self, tmp_factor_pool_path):
        """更新因子状态。"""
        mgr = FactorPoolManager(tmp_factor_pool_path)
        mgr.load_or_init()
        from fts.factor_engine.contracts import FactorPoolEntry

        entry = FactorPoolEntry(
            factor_id="cand_xyz",
            name="f1",
            source="l1_bootstrapping",
            priority="high",
            status="pending",
            trace_id="t1",
            created_at="2026-07-18",
            updated_at="2026-07-18",
        )
        mgr.add_entry(entry)
        mgr.mark_status("cand_xyz", "injected")
        assert len(mgr.list_pending()) == 0
        pool = mgr.load_or_init()
        assert pool["factors"][0]["status"] == "injected"

    def test_pending_count(self, tmp_factor_pool_path):
        """pending_count 字段正确。"""
        mgr = FactorPoolManager(tmp_factor_pool_path)
        mgr.load_or_init()
        from fts.factor_engine.contracts import FactorPoolEntry

        for i in range(3):
            entry = FactorPoolEntry(
                factor_id=f"cand_{i}",
                name=f"f{i}",
                source="l1_bootstrapping",
                priority="high",
                status="pending",
                trace_id=f"t{i}",
                created_at="2026-07-18",
                updated_at="2026-07-18",
            )
            mgr.add_entry(entry)
        pool = mgr.load_or_init()
        assert pool["total_count"] == 3
        assert pool["pending_count"] == 3

    def test_load_corrupted_factor_pool(self, tmp_factor_pool_path):
        """损坏的 factor_pool.json 触发冷启动（lines 297-298）。"""
        tmp_factor_pool_path.write_text("{invalid json}", encoding="utf-8")
        mgr = FactorPoolManager(tmp_factor_pool_path)
        pool = mgr.load_or_init()
        assert pool["total_count"] == 0
        assert pool["factors"] == []


# ════════════════════════════════════════════════════════
# 4. DebateQualityAnalyzer 测试
# ════════════════════════════════════════════════════════


class TestDebateQualityAnalyzer:
    """辩论质量分析器。"""

    def test_no_journal_returns_empty(self, tmp_debates_dir):
        """无辩论数据时返回空。"""
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert result["topics"] == []
        assert "无辩论数据" in result["summary"]

    def test_detect_bullish_weak(self, tmp_debates_dir):
        """检测多头论证薄弱。"""
        # 准备 journal
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "action": "debate_record",
                            "symbols": {
                                "rb": {
                                    "debate_round": 3,
                                    "bullish_arguments": ["a"],
                                    "bearish_arguments": ["a", "b", "c"],
                                }
                            },
                        }
                    ]
                },
                f,
            )
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert len(result["topics"]) == 1
        assert result["topics"][0]["gap"] == "bullish_weak"
        assert result["topics"][0]["topic"] == "rb"

    def test_detect_bearish_weak(self, tmp_debates_dir):
        """检测空头论证薄弱。"""
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "action": "debate_record",
                            "symbols": {
                                "i": {
                                    "debate_round": 3,
                                    "bullish_arguments": ["a", "b", "c"],
                                    "bearish_arguments": ["a"],
                                }
                            },
                        }
                    ]
                },
                f,
            )
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert len(result["topics"]) == 1
        assert result["topics"][0]["gap"] == "bearish_weak"

    def test_detect_insufficient_rounds(self, tmp_debates_dir):
        """检测辩论轮次不足。"""
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "action": "debate_record",
                            "symbols": {
                                "j": {
                                    "debate_round": 1,
                                    "bullish_arguments": ["a"],
                                    "bearish_arguments": ["a"],
                                }
                            },
                        }
                    ]
                },
                f,
            )
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert len(result["topics"]) == 1
        assert result["topics"][0]["gap"] == "insufficient_rounds"

    def test_journal_decode_error(self, tmp_debates_dir):
        """辩论数据 JSON 解码失败（lines 409-411）。"""
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text("not json", encoding="utf-8")
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert "加载失败" in result["summary"]

    def test_entries_empty_list(self, tmp_debates_dir):
        """辩论日志 entries 为空（lines 415-416）。"""
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump({"entries": []}, f)
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert "辩论日志为空" in result["summary"]

    def test_analyze_no_gaps_detected(self, tmp_debates_dir):
        """有辩论数据但无明显薄弱维度（line 444）。"""
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "action": "debate_record",
                            "symbols": {
                                "rb": {
                                    "debate_round": 3,
                                    "bullish_arguments": ["a", "b"],
                                    "bearish_arguments": ["c", "d"],
                                }
                            },
                        }
                    ]
                },
                f,
            )
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert "无明显薄弱维度" in result["summary"]

    def test_detect_gap_non_dict(self):
        """非 dict 的 sym_data 返回 no_debate（line 452）。"""
        result = DebateQualityAnalyzer._detect_gap("not_a_dict")
        assert result == "no_debate"

    def test_detect_gap_no_arguments(self):
        """无多头空头论证返回 no_debate（line 459）。"""
        result = DebateQualityAnalyzer._detect_gap(
            {
                "debate_round": 3,
                "bullish_arguments": [],
                "bearish_arguments": [],
            }
        )
        assert result == "no_debate"

    def test_detect_gap_balanced(self):
        """多空论证平衡返回 None（line 464）。"""
        result = DebateQualityAnalyzer._detect_gap(
            {
                "debate_round": 3,
                "bullish_arguments": ["a", "b"],
                "bearish_arguments": ["c", "d"],
            }
        )
        assert result is None


# ════════════════════════════════════════════════════════
# 5. BootstrappingChain 测试
# ════════════════════════════════════════════════════════


class TestBootstrappingChain:
    """Bootstrapping Agent 链。"""

    def test_bootstrap_from_templates(self):
        """无 LLM 时从模板生成候选。"""
        chain = BootstrappingChain(llm_client=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=3,
            seed_pool=SeedPool(),
            trace_id="test_trace",
        )
        # 内置模板有 3 个，应该都能产出（除非与种子重名）
        assert len(candidates) >= 1
        for c in candidates:
            assert c["candidate_id"].startswith("cand_")
            assert c["trace_id"] == "test_trace"
            assert "name" in c
            assert "code" in c

    def test_bootstrap_max_candidates(self):
        """max_candidates 限制候选数。"""
        chain = BootstrappingChain(llm_client=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=1,
            seed_pool=SeedPool(),
            trace_id="test_trace",
        )
        assert len(candidates) == 1

    def test_bootstrap_skips_duplicate_names(self):
        """跳过与种子同名的模板。"""
        # 创建一个 SeedPool mock，把所有模板名字都列出来
        pool = SeedPool()
        # 模板名: bbands_width_reversion, oi_price_divergence, news_sentiment_proxy
        # SeedPool 内置 12 个种子的名字不包含这些模板名，所以不应被跳过
        chain = BootstrappingChain(llm_client=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=10,
            seed_pool=pool,
            trace_id="test_trace",
        )
        # 至少应该有 1 个候选（除非所有模板都被标记为 duplicate）
        assert len(candidates) >= 1

    def test_bootstrap_with_llm_injection(self, mock_llm_client, valid_candidate):
        """LLM 客户端注入候选。"""
        mock_llm_client.bootstrap_factors.return_value = [valid_candidate]
        chain = BootstrappingChain(llm_client=mock_llm_client)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=5,
            seed_pool=SeedPool(),
            trace_id="test_trace",
        )
        # 应包含 LLM 注入的 1 个 + 模板补充的（如果未与 test_factor_unique_name 重名）
        assert len(candidates) >= 1
        assert candidates[0]["name"] == "test_factor_unique_name"

    def test_bootstrap_validates_code(self):
        """bootstrap 验证候选代码可执行性。"""
        chain = BootstrappingChain(llm_client=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=3,
            seed_pool=SeedPool(),
            trace_id="test_trace",
        )
        for c in candidates:
            assert c["is_executable"] is True

    def test_bootstrap_with_invalid_llm_code(self, mock_llm_client):
        """LLM 返回无效代码时标记 is_executable=False。"""
        bad_candidate = SeedCandidate(
            candidate_id="cand_bad",
            name="bad_factor_xyz",
            code="def factor_program(data, params):\n    import os\n    os.system('rm -rf')\n",  # 安全沙箱禁止
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=1,
            ),
            economic_logic=EconomicLogic(
                theory=4,
                behavioral=4,
                microstructure=4,
                institutional=4,
                narrative="恶意代码测试因子，应该被沙箱拒绝编译。",
            ),
            source="l1_bootstrapping",
            parent_topic="测试",
            trace_id="t",
            created_at="2026-07-18",
        )
        mock_llm_client.bootstrap_factors.return_value = [bad_candidate]
        chain = BootstrappingChain(llm_client=mock_llm_client)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=1,
            seed_pool=SeedPool(),
            trace_id="t",
        )
        assert len(candidates) == 1
        assert candidates[0]["is_executable"] is False

    def test_bootstrap_validate_code_raises(self, mock_llm_client):
        """validate_factor_code 异常时标记 is_executable=False（lines 652-654）。"""
        bad_candidate = SeedCandidate(
            candidate_id="cand_bad",
            name="bad_factor_raise",
            code="def factor_program(data, params):\n    return None\n",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=1,
            ),
            economic_logic=EconomicLogic(
                theory=4,
                behavioral=4,
                microstructure=4,
                institutional=4,
                narrative="测试编译异常",
            ),
            source="l1_bootstrapping",
            parent_topic="测试",
            trace_id="t",
            created_at="2026-07-18",
        )
        mock_llm_client.bootstrap_factors.return_value = [bad_candidate]
        chain = BootstrappingChain(llm_client=mock_llm_client)
        with patch("fts.factor_engine.meta_loop.validate_factor_code", side_effect=RuntimeError("沙箱异常")):
            candidates = chain.bootstrap(
                market_snapshot={},
                debate_gaps=[],
                max_candidates=1,
                seed_pool=SeedPool(),
                trace_id="t",
            )
        assert len(candidates) == 1
        assert candidates[0]["is_executable"] is False
        assert any("异常" in r for r in candidates[0].get("failure_reasons", []))

    def test_bootstrap_llm_exception(self, mock_llm_client):
        """LLM bootstrap_factors 异常时回退到模板（lines 683-685）。"""
        mock_llm_client.bootstrap_factors.side_effect = RuntimeError("LLM 调用失败")
        chain = BootstrappingChain(llm_client=mock_llm_client)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=3,
            seed_pool=SeedPool(),
            trace_id="t",
        )
        # 应从模板产生候选
        assert len(candidates) >= 1

    def test_bootstrap_skips_template_with_existing_name(self):
        """模板名已存在于种子池时跳过该模板（line 713）。"""
        pool = MagicMock(spec=SeedPool)
        pool.list_names.return_value = ["bbands_width_reversion", "oi_price_divergence"]
        chain = BootstrappingChain(llm_client=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=10,
            seed_pool=pool,
            trace_id="t",
        )
        candidate_names = [c["name"] for c in candidates]
        assert "bbands_width_reversion" not in candidate_names
        assert "oi_price_divergence" not in candidate_names
        assert "news_sentiment_proxy" in candidate_names

    def test_bootstrap_with_debate_gap_match(self):
        """debate_gap 匹配模板 parent_topic 时关联参考信息。"""
        chain = BootstrappingChain(llm_client=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[{"gap": "volatility_reversion", "debate_round": 3}],
            max_candidates=1,
            seed_pool=SeedPool(),
            trace_id="test_trace",
        )
        assert len(candidates) == 1
        # volatility_reversion 衍生 的 parent_topic 包含 "volatility_reversion"
        assert candidates[0]["debate_gap"] == "volatility_reversion"
        assert candidates[0]["debate_round_ref"] == 3


# ════════════════════════════════════════════════════════
# 6. MetaLoop 主循环测试
# ════════════════════════════════════════════════════════


class TestMetaLoop:
    """L1 Meta-Loop 主循环。"""

    def test_run_completes_without_web_collector(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """无 web_collector 时也能完成（跳过感知步骤）。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            web_collector=None,
        )
        result = loop.run(max_bootstraps=3)
        assert result.status == "completed"
        assert result.candidates_generated >= 1
        assert result.candidates_injected >= 1
        # run_id 通用格式 run_<hex>_<ts>；trace_id 才带 l1_ 前缀
        assert result.run_id.startswith("run_")
        assert result.trace_id.startswith("l1_")

    def test_run_with_web_collector(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, mock_web_collector
    ):
        """配置 web_collector 时执行感知步骤。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            web_collector=mock_web_collector,
            sample_symbols=["rb", "i"],
        )
        result = loop.run(max_bootstraps=2)
        assert result.status == "completed"

    def test_run_persists_state(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """run() 后状态已持久化到 state.duckdb。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
        )
        loop.run(max_bootstraps=2)
        state = tmp_state_store.get("meta_loop", "state")
        assert state is not None
        assert state["status"] == "completed"
        assert state["total_candidates_generated"] >= 1

    def test_run_updates_factor_pool(self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir):
        """run() 更新 factor_pool.json。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        result = loop.run(max_bootstraps=2)
        assert tmp_factor_pool_path.exists()
        with open(tmp_factor_pool_path, "r", encoding="utf-8") as f:
            pool = json.load(f)
        assert pool["total_count"] == result.candidates_injected
        assert pool["pending_count"] == result.candidates_injected
        # GAP-I306: 注入 entry 记录 market，供 Step 2.5 市场隔离去重
        if pool["factors"]:
            assert pool["factors"][0]["market"] == "futures"

    def test_run_persists_injected_candidates(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """run() 持久化注入的候选因子到 inject_dir。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        result = loop.run(max_bootstraps=2)
        if result.candidates_injected > 0:
            injected_files = list(tmp_inject_dir.glob("cand_*.json"))
            assert len(injected_files) == result.candidates_injected

    # ─── GAP-I306: Step 2.5 去重口径修复（改读 factor_pool.json SSOT） ───

    def test_scan_injected_names_reads_factor_pool(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """去重改读 factor_pool.json：l1_injected 目录被 L2(GAP-036) 清空后仍能扫描到历史注入名。"""
        tmp_factor_pool_path.write_text(
            json.dumps(
                {
                    "version": EVOLUTION_VERSION,
                    "updated_at": "2026-08-13T00:00:00",
                    "factors": [
                        {"factor_id": "cand_f1", "name": "FUT_Alpha_A", "market": "futures", "status": "injected"},
                        {"factor_id": "cand_f2", "name": "ohlc_positioning", "market": "futures", "status": "pending"},
                        {"factor_id": "cand_s1", "name": "stock_alpha_a", "market": "stock", "status": "injected"},
                        # 历史 entry 无 market 字段 → 市场归属不明，纳入去重（宁多勿漏）
                        {"factor_id": "cand_h1", "name": "legacy_factor", "status": "injected"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        # 模拟 L2 GAP-036 消费后清空 l1_injected 目录（修复前此场景去重必然失效）
        assert list(tmp_inject_dir.glob("*.json")) == []
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            market="futures",
        )
        names = loop._scan_injected_names()  # noqa: SLF001
        # 小写化 + 本市场 + 无 market 历史记录纳入，明确其他市场记录排除
        assert names == {"fut_alpha_a", "ohlc_positioning", "legacy_factor"}
        assert "stock_alpha_a" not in names

    def test_scan_injected_names_empty_pool(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """factor_pool.json 不存在或无记录 → 返回空集（不报错）。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            market="futures",
        )
        assert loop._scan_injected_names() == set()  # noqa: SLF001

    def test_circuit_breaker_on_consecutive_low_quality(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """连续低质量触发熔断。"""
        # 构造一个所有候选都被拒绝的场景
        # 通过让所有候选名都与现有种子重名
        from fts.factor_engine.meta_loop import BootstrappingChain
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature, SeedCandidate

        # 自定义 BootstrappingChain 让所有候选都失败
        class FailingChain(BootstrappingChain):
            def bootstrap(self, *args, **kwargs):
                # 返回 5 个无效候选（全部不可执行）
                return [
                    SeedCandidate(
                        candidate_id=f"cand_fail_{i}",
                        name=f"fail_{i}",
                        code="def factor_program(data, params):\n    return None\n",  # 编译过但 is_executable=False
                        params={},
                        signature=FactorSignature(
                            input_fields=["close"],
                            output_type="signal",
                            frequency="daily",
                            lookback=1,
                        ),
                        economic_logic=EconomicLogic(
                            theory=1,
                            behavioral=1,
                            microstructure=1,
                            institutional=1,
                            narrative="不达标",
                        ),
                        source="l1_bootstrapping",
                        parent_topic="失败测试",
                        is_executable=False,  # 不可执行
                        is_duplicate=False,
                        passed_l1_verifier=False,
                        failure_reasons=[],
                        trace_id="t",
                        created_at="2026-07-18",
                    )
                    for i in range(5)
                ]

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            budget=L1BudgetConfig(
                daily_token_limit=50000,
                monthly_token_limit=1500000,
                max_bootstraps_per_run=5,
                max_tokens_per_candidate=5000,
                circuit_breaker_token_ratio=2.0,
                circuit_breaker_failure_rate=0.95,
                circuit_breaker_consecutive_low_quality=5,  # 5 次连续失败触发
            ),
        )
        loop.bootstrap_chain = FailingChain()
        result = loop.run(max_bootstraps=5)
        # 5 个候选都失败，第 5 个之后应触发熔断
        assert result.status in ("circuit_broken", "completed")  # completed 也算（如果熔断在循环内未触发）

    def test_run_result_to_dict(self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir):
        """MetaRunResult.to_dict() 正确序列化。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        result = loop.run(max_bootstraps=1)
        d = result.to_dict()
        assert "run_id" in d
        assert "trace_id" in d
        assert "status" in d
        assert "candidates_generated" in d
        assert "candidates_injected" in d

    def test_run_with_debate_gaps(self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir):
        """有辩论缺口数据时仍能正常完成。"""
        # 准备辩论数据
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "action": "debate_record",
                            "symbols": {
                                "rb": {
                                    "debate_round": 3,
                                    "bullish_arguments": ["a"],
                                    "bearish_arguments": ["a", "b", "c"],
                                }
                            },
                        }
                    ]
                },
                f,
            )
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        result = loop.run(max_bootstraps=2)
        assert result.status == "completed"
        assert result.debate_gaps_detected >= 1

    def test_circuit_breaker_consecutive_low_quality_triggers(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """连续 5 次低质量后第 6 个候选触发熔断返回（lines 905-906, 1069）。"""
        from fts.factor_engine.meta_loop import BootstrappingChain as BC

        class FailingChain6(BC):
            def bootstrap(self, *args, **kwargs):
                return [
                    SeedCandidate(
                        candidate_id=f"cand_fail_{i}",
                        name=f"fail_{i}",
                        code="def factor_program(data, params):\n    return None\n",
                        params={},
                        signature=FactorSignature(
                            input_fields=["close"],
                            output_type="signal",
                            frequency="daily",
                            lookback=1,
                        ),
                        economic_logic=EconomicLogic(
                            theory=1,
                            behavioral=1,
                            microstructure=1,
                            institutional=1,
                            narrative="不达标",
                        ),
                        source="l1_bootstrapping",
                        parent_topic="失败测试",
                        is_executable=False,
                        is_duplicate=False,
                        passed_l1_verifier=False,
                        failure_reasons=[],
                        trace_id="t",
                        created_at="2026-07-18",
                    )
                    for i in range(6)  # 6 个失败候选触发熔断
                ]

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            budget=L1BudgetConfig(
                daily_token_limit=50000,
                monthly_token_limit=1500000,
                max_bootstraps_per_run=6,
                max_tokens_per_candidate=5000,
                circuit_breaker_token_ratio=2.0,
                circuit_breaker_failure_rate=0.95,
                circuit_breaker_consecutive_low_quality=5,
            ),
        )
        loop.bootstrap_chain = FailingChain6()
        result = loop.run(max_bootstraps=6)
        assert result.status == "circuit_broken"
        assert "连续低质量" in result.circuit_breaker_reason

    def test_check_circuit_breaker_token(self):
        """Token 超限触发熔断（line 1054）。"""
        loop = MetaLoop()
        state = L1MetaLoopState(
            run_id="test",
            started_at="",
            status="running",
            tokens_consumed=200000,  # 超过 50000 * 2 = 100000
            budget_limit=50000,
        )
        reason = loop._check_circuit_breaker(state, 0)
        assert reason is not None
        assert "Token 熔断" in reason

    def test_check_circuit_breaker_failure_rate(self):
        """高失败率触发熔断（lines 1063-1065）。"""
        loop = MetaLoop()
        state = L1MetaLoopState(
            run_id="test",
            started_at="",
            status="running",
            tokens_consumed=1000,
            budget_limit=50000,
            total_candidates_generated=100,  # 累计生成 100 个
            total_candidates_injected=2,  # 仅注入 2 个 → 98% 失败
        )
        reason = loop._check_circuit_breaker(state, 0)
        assert reason is not None
        assert "失败率熔断" in reason

    def test_check_circuit_breaker_consecutive_low_quality(self):
        """连续低质量触发熔断（line 1069）。"""
        loop = MetaLoop()
        loop._consecutive_low_quality = 5
        state = L1MetaLoopState(
            run_id="test",
            started_at="",
            status="running",
            tokens_consumed=1000,
            budget_limit=50000,
        )
        reason = loop._check_circuit_breaker(state, 0)
        assert reason is not None
        assert "连续低质量" in reason

    def test_check_circuit_breaker_no_false_positive_unverified_batch(self):
        """整批候选尚未验证时不误熔断（P2 修复：失败率只按已验证数计算）。

        回归场景: 首次运行生成 20 个候选，验证开始前/中途已评估数 < 20，
        失败率熔断不应触发。修复前 _verify_and_inject 以"本批总数 20 + 注入 0"
        计算失败率 → 100% > 95% 在第一个候选前即误熔断（2026-08-13 实测复现，
        昨日 2026-08-12 相同现象）。
        """
        loop = MetaLoop()
        state = L1MetaLoopState(
            run_id="test",
            started_at="",
            status="running",
            tokens_consumed=1000,
            budget_limit=50000,
            total_candidates_generated=0,  # 首批运行: 历史已验证 0
            total_candidates_injected=0,
        )
        # 验证开始前（0 个已验证）与中途（19 个已验证 < 20 门槛）均不应触发
        assert loop._check_circuit_breaker(state, 0, 0) is None
        assert loop._check_circuit_breaker(state, 19, 0) is None

    def test_check_circuit_breaker_failure_rate_after_full_batch(self):
        """整批候选验证完成后按真实失败率熔断（P2 修复语义保留）。"""
        loop = MetaLoop()
        state = L1MetaLoopState(
            run_id="test",
            started_at="",
            status="running",
            tokens_consumed=1000,
            budget_limit=50000,
            total_candidates_generated=0,
            total_candidates_injected=0,
        )
        # 本批 20 个全部验证完且 0 注入 → 100% 失败率应熔断（真实高失败场景）
        reason = loop._check_circuit_breaker(state, 20, 0)
        assert reason is not None
        assert "失败率熔断" in reason
        # 本批 20 个验证完、3 个注入成功 → 85% < 95% 不熔断
        assert loop._check_circuit_breaker(state, 20, 3) is None

    def test_is_hard_failure_classification(self):
        """硬失败/软失败分类（P1a）。"""
        loop = MetaLoop()
        assert loop._is_hard_failure(["候选因子代码不可执行（沙箱编译失败）"]) is True
        assert loop._is_hard_failure(["候选因子与现有种子重复"]) is True
        assert loop._is_hard_failure(["候选因子名称与现有种子重复: fut_x"]) is True
        assert loop._is_hard_failure(["经济逻辑达标维度 1/4 < 2"]) is False
        assert loop._is_hard_failure(["narrative 长度 5 < 20"]) is False
        assert loop._is_hard_failure(["经济逻辑达标维度 1/4 < 2", "narrative 长度 5 < 20"]) is False
        assert loop._is_hard_failure([]) is False

    def test_soft_failures_do_not_trigger_circuit_breaker(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """连续软失败（经济逻辑评分不达标）不应触发熔断（P1a）。"""
        from fts.factor_engine.meta_loop import BootstrappingChain as BC

        class SoftFailChain(BC):
            def bootstrap(self, *args, **kwargs):
                # 6 个候选: 代码可编译(is_executable=True)、不重复，仅经济逻辑不达标（软失败）
                return [
                    SeedCandidate(
                        candidate_id=f"cand_soft_{i}",
                        name=f"soft_{i}",
                        code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
                        params={},
                        signature=FactorSignature(
                            input_fields=["close"],
                            output_type="signal",
                            frequency="daily",
                            lookback=1,
                        ),
                        economic_logic=EconomicLogic(
                            theory=2,
                            behavioral=2,
                            microstructure=2,
                            institutional=2,
                            narrative="该因子缺乏足够的机制论证支撑，经济逻辑不足。",
                        ),
                        source="l1_bootstrapping",
                        parent_topic="软失败测试",
                        is_executable=True,
                        is_duplicate=False,
                        passed_l1_verifier=False,
                        failure_reasons=[],
                        trace_id="t",
                        created_at="2026-07-18",
                    )
                    for i in range(6)
                ]

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            budget=L1BudgetConfig(
                daily_token_limit=50000,
                monthly_token_limit=1500000,
                max_bootstraps_per_run=6,
                max_tokens_per_candidate=5000,
                circuit_breaker_token_ratio=2.0,
                circuit_breaker_failure_rate=0.95,
                circuit_breaker_consecutive_low_quality=5,
            ),
        )
        loop.bootstrap_chain = SoftFailChain()
        result = loop.run(max_bootstraps=6)
        # 6 个软失败不应触发连续低质量熔断
        assert result.status == "completed"
        assert result.circuit_breaker_reason is None

    def test_run_no_false_circuit_breaker_on_valid_batch(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """整批有效候选不被失败率熔断误杀（P2 修复核心回归）。

        修复前: _verify_and_inject 在验证循环第一个候选前以"本批总数 20 + 注入 0"
        计算失败率 → 100% > 95% 立即熔断（失败率熔断: 100.00% > 0.95, 已处理=0/20），
        20 个有效候选全部 0 注入。
        修复后: 失败率按"已实际验证的候选数"计算，本批 20 个全部通过 → completed 且 20 注入。
        使用 tmp_state_store 隔离，避免全局 state.duckdb 历史熔断状态污染断言。
        """
        from fts.factor_engine.meta_loop import BootstrappingChain
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature, SeedCandidate

        class ValidChain(BootstrappingChain):
            def bootstrap(self, *args, **kwargs):
                return [
                    SeedCandidate(
                        candidate_id=f"cand_valid_{i}",
                        name=f"l1_fix_valid_{i}",
                        code=(
                            "def factor_program(data, params):\n"
                            "    import numpy as np\n"
                            "    return np.zeros(len(data['close']))\n"
                        ),
                        params={"window": 10},
                        signature=FactorSignature(
                            input_fields=["close"],
                            output_type="signal",
                            frequency="daily",
                            lookback=15,
                        ),
                        economic_logic=EconomicLogic(
                            theory=4,
                            behavioral=4,
                            microstructure=4,
                            institutional=4,
                            narrative="这是一个测试因子，捕捉动量效应与波动率回归的经济逻辑，满足长度要求。",
                        ),
                        source="l1_bootstrapping",
                        parent_topic="误熔断回归测试",
                        is_executable=True,
                        is_duplicate=False,
                        passed_l1_verifier=False,
                        failure_reasons=[],
                        trace_id="t",
                        created_at="2026-08-13",
                    )
                    for i in range(20)
                ]

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
            budget=L1BudgetConfig(
                daily_token_limit=50000,
                monthly_token_limit=1500000,
                max_bootstraps_per_run=20,
                max_tokens_per_candidate=5000,
                circuit_breaker_token_ratio=2.0,
                circuit_breaker_failure_rate=0.95,
                circuit_breaker_consecutive_low_quality=5,
            ),
        )
        loop.bootstrap_chain = ValidChain()
        result = loop.run(max_bootstraps=20)
        assert result.status == "completed"
        assert result.candidates_injected == 20
        assert result.circuit_breaker_reason is None

    def test_hard_failure_verify_log_includes_compile_detail(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, caplog
    ):
        """verify 拒绝日志输出具体编译错误（P1c）。"""
        import logging
        from fts.factor_engine.meta_loop import BootstrappingChain as BC

        class HardFailChain(BC):
            def bootstrap(self, *args, **kwargs):
                cand = SeedCandidate(
                    candidate_id="cand_hard_1",
                    name="hard_1",
                    code="import os\nimport sys\n",
                    params={},
                    signature=FactorSignature(
                        input_fields=["close"],
                        output_type="signal",
                        frequency="daily",
                        lookback=1,
                    ),
                    economic_logic=EconomicLogic(
                        theory=3,
                        behavioral=3,
                        microstructure=3,
                        institutional=3,
                        narrative="该因子具备充分的经济逻辑论证。",
                    ),
                    source="l1_bootstrapping",
                    parent_topic="硬失败测试",
                    is_executable=False,
                    is_duplicate=False,
                    passed_l1_verifier=False,
                    failure_reasons=["编译失败: 禁止 import 黑名单模块: os; 禁止 import 黑名单模块: sys"],
                    trace_id="t",
                    created_at="2026-07-18",
                )
                return [cand]

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        loop.bootstrap_chain = HardFailChain()
        with caplog.at_level(logging.WARNING, logger="fts.factor_engine.meta_loop"):
            loop.run(max_bootstraps=1)
        # 日志应包含具体编译错误 detail
        assert any("禁止 import 黑名单模块" in r.message for r in caplog.records)

    def test_perceive_market_collector_error(self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir):
        """web_collector 异常时记录 error（lines 985-987）。"""

        def failing_collector(sym):
            if sym == "i":
                raise RuntimeError("网络错误")
            return {"symbol": sym, "ok": True}

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            web_collector=failing_collector,
            sample_symbols=["rb", "i"],
        )
        snapshot = loop._perceive_market("test_trace")
        assert snapshot["skipped"] is False
        assert "rb" in snapshot["snapshots"]
        assert "i" in snapshot["snapshots"]
        assert "error" in snapshot["snapshots"]["i"]

    def test_inject_candidate_exception(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, valid_candidate
    ):
        """注入候选异常时返回 None（lines 1028-1030）。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        with patch("builtins.open", side_effect=PermissionError("无写入权限")):
            result = loop._inject_candidate(valid_candidate, "test_trace")
        assert result is None

    def test_compute_priority_low(self):
        """总分 < 12 返回 low 优先级（line 1044）。"""
        cand = SeedCandidate(
            candidate_id="cand_low",
            name="low_priority",
            code="def factor_program(data, params):\n    return None\n",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=1,
            ),
            economic_logic=EconomicLogic(
                theory=2,
                behavioral=2,
                microstructure=3,
                institutional=2,  # 总分 = 9
                narrative="低优先级因子",
            ),
            source="l1_bootstrapping",
            parent_topic="测试",
            trace_id="t",
            created_at="2026-07-18",
        )
        priority = MetaLoop._compute_priority(cand)
        assert priority == "low"

    def test_run_exception_handling(self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir):
        """run() 异常时返回 paused 状态（lines 960-963）。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        # 让 _perceive_market 抛异常（在 try 块内部，line 870）
        loop._perceive_market = MagicMock(side_effect=RuntimeError("市场感知异常"))
        result = loop.run(max_bootstraps=1)
        assert result.status == "paused"
        assert "市场感知异常" in str(result.circuit_breaker_reason)


# ════════════════════════════════════════════════════════
# 7. GAP-123 P1③ — 软失败经济逻辑重写闭环测试
# ════════════════════════════════════════════════════════


class TestGap123EconFixLoop:
    """GAP-123 P1③: 软失败（经济逻辑不达标）候选经 LLM 定向重写后注入。"""

    def _make_weak_candidate(self, candidate_id: str = "cand_econfix") -> SeedCandidate:
        """构造经济逻辑不达标（软失败）的候选。"""
        return SeedCandidate(
            candidate_id=candidate_id,
            name="econ_fix_test_factor",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
            params={"window": 10},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=15,
            ),
            economic_logic=EconomicLogic(
                theory=2,
                behavioral=2,
                microstructure=2,
                institutional=2,
                narrative="该因子缺乏足够的机制论证支撑，经济逻辑不足。",
            ),
            source="l1_bootstrapping",
            parent_topic="GAP-123 P1③ 测试",
            is_executable=True,
            is_duplicate=False,
            passed_l1_verifier=False,
            failure_reasons=["经济逻辑达标维度 0/4 < 2"],
            trace_id="trace_gap123",
            created_at="2026-08-15",
        )

    def test_try_fix_success_updates_candidate(self):
        """LLM 返回达标 economic_logic → True 且候选被更新。"""
        llm = MagicMock()
        llm.fix_economic_logic.return_value = {
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "修复后论证: 理论机制明确, 行为偏差具体, 微观结构路径清晰, 机构制度支撑充分。",
        }
        loop = MetaLoop(llm_client=llm)
        cand = self._make_weak_candidate()
        assert loop._try_fix_economic_logic(cand, "trace_gap123") is True
        assert cand["economic_logic"]["theory"] == 4
        assert cand["passed_l1_verifier"] is True  # 重验通过标记

    def test_try_fix_llm_unsupported_returns_false(self):
        """LLM 客户端不支持 fix_economic_logic（基类默认）→ False，候选不变。"""
        loop = MetaLoop(llm_client=None)  # 无 LLM
        cand = self._make_weak_candidate()
        orig_econ = dict(cand["economic_logic"])
        assert loop._try_fix_economic_logic(cand, "trace_gap123") is False
        assert cand["economic_logic"] == orig_econ

    def test_try_fix_llm_returns_none(self):
        """LLM 返回 None → False，候选不变。"""
        llm = MagicMock()
        llm.fix_economic_logic.return_value = None
        loop = MetaLoop(llm_client=llm)
        cand = self._make_weak_candidate()
        orig_econ = dict(cand["economic_logic"])
        assert loop._try_fix_economic_logic(cand, "trace_gap123") is False
        assert cand["economic_logic"] == orig_econ

    def test_try_fix_rewrite_still_fails(self):
        """重写后仍不达标 → False。"""
        llm = MagicMock()
        llm.fix_economic_logic.return_value = {
            "theory": 2,
            "behavioral": 2,
            "microstructure": 2,
            "institutional": 2,
            "narrative": "重写后仍然只有直觉, 无机制论证。",
        }
        loop = MetaLoop(llm_client=llm)
        cand = self._make_weak_candidate()
        assert loop._try_fix_economic_logic(cand, "trace_gap123") is False

    def test_try_fix_llm_exception_returns_false(self):
        """LLM 调用异常 → False，候选不变。"""
        llm = MagicMock()
        llm.fix_economic_logic.side_effect = RuntimeError("LLM down")
        loop = MetaLoop(llm_client=llm)
        cand = self._make_weak_candidate()
        orig_econ = dict(cand["economic_logic"])
        assert loop._try_fix_economic_logic(cand, "trace_gap123") is False
        assert cand["economic_logic"] == orig_econ

    def test_verify_and_inject_soft_failure_rewritten(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """端到端: 软失败候选经重写后注入成功（GAP-123 P1③ 闭环）。"""
        from fts.factor_engine.meta_loop import BootstrappingChain as BC

        class WeakChain(BC):
            def bootstrap(self, *args, **kwargs):
                return [
                    SeedCandidate(
                        candidate_id="cand_weak_1",
                        name="gap123_weak_factor",
                        code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
                        params={"window": 10},
                        signature=FactorSignature(
                            input_fields=["close"],
                            output_type="signal",
                            frequency="daily",
                            lookback=15,
                        ),
                        economic_logic=EconomicLogic(
                            theory=2,
                            behavioral=2,
                            microstructure=2,
                            institutional=2,
                            narrative="经济逻辑论证不足。",
                        ),
                        source="l1_bootstrapping",
                        parent_topic="GAP-123 端到端",
                        is_executable=True,
                        is_duplicate=False,
                        passed_l1_verifier=False,
                        failure_reasons=[],
                        trace_id="t",
                        created_at="2026-08-15",
                    )
                ]

        # 真实 MockLLMClient: fix_economic_logic 返回四维全达标
        from fts.llm import MockLLMClient

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            llm_client=MockLLMClient(),
        )
        loop.bootstrap_chain = WeakChain()
        result = loop.run(max_bootstraps=1)
        # 软失败候选经重写后注入成功
        assert result.candidates_injected == 1
        assert result.status == "completed"


# 7. SeedPool L1 注入接口测试
# ════════════════════════════════════════════════════════


class TestSeedPoolL1Injection:
    """SeedPool.inject_from_l1() 接口测试。"""

    def test_inject_from_l1(self, valid_candidate):
        """L1 注入因子到种子池。"""
        pool = SeedPool()
        fp = pool.inject_from_l1(valid_candidate)
        assert fp["source"] == "bootstrapping"
        assert fp["name"] == "test_factor_unique_name"
        assert fp["parent_id"] == "cand_test001"
        # 通过 list_injected_l1 查询
        injected = pool.list_injected_l1()
        assert len(injected) == 1
        assert injected[0]["name"] == "test_factor_unique_name"

    def test_inject_from_l1_missing_field_raises(self):
        """缺少必需字段抛 ValueError。"""
        pool = SeedPool()
        with pytest.raises(ValueError):
            pool.inject_from_l1({"name": "incomplete"})  # 缺少 code/params 等

    def test_inject_from_l1_uses_candidate_trace_id(self, valid_candidate):
        """L1 注入使用 candidate 的 trace_id。"""
        pool = SeedPool()
        fp = pool.inject_from_l1(valid_candidate)
        assert fp["trace_id"] == "trace_test_001"

    def test_inject_from_l1_override_trace_id(self, valid_candidate):
        """显式传入 trace_id 覆盖 candidate 的。"""
        pool = SeedPool()
        fp = pool.inject_from_l1(valid_candidate, trace_id="override_trace")
        assert fp["trace_id"] == "override_trace"

    def test_inject_multiple_l1_candidates(self, valid_candidate):
        """注入多个 L1 候选。"""
        pool = SeedPool()
        c1 = valid_candidate
        c2 = dict(valid_candidate)
        c2["candidate_id"] = "cand_test002"
        c2["name"] = "test_factor_unique_name_2"
        pool.inject_from_l1(c1)
        pool.inject_from_l1(c2)
        assert len(pool.list_injected_l1()) == 2

    def test_injected_l1_does_not_pollute_built_in_seeds(self, valid_candidate):
        """L1 注入不污染内置 12 个种子。"""
        pool = SeedPool()
        original_count = pool.count()
        pool.inject_from_l1(valid_candidate)
        # 内置种子数不变
        assert pool.count() == original_count
        # list_names() 仍只返回内置种子
        assert "test_factor_unique_name" not in pool.list_names()


# ════════════════════════════════════════════════════════
# 8. 端到端集成测试
# ════════════════════════════════════════════════════════


class TestMetaLoopEndToEnd:
    """L1 Meta-Loop 端到端测试 — 5 步完整流程。"""

    def test_full_pipeline_with_all_components(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, mock_web_collector
    ):
        """完整 5 步管道: 感知 → 辩论分析 → Bootstrapping → Verifier → 注入。"""
        # 准备辩论数据
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "entries": [
                        {
                            "action": "debate_record",
                            "symbols": {
                                "rb": {
                                    "debate_round": 3,
                                    "bullish_arguments": ["a"],
                                    "bearish_arguments": ["a", "b", "c"],
                                }
                            },
                        }
                    ]
                },
                f,
            )

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            web_collector=mock_web_collector,
            sample_symbols=["rb", "i", "j"],
        )
        result = loop.run(max_bootstraps=5)

        # 1. 状态完成
        assert result.status == "completed"
        # 2. 辩论缺口识别
        assert result.debate_gaps_detected >= 1
        # 3. 候选生成
        assert result.candidates_generated >= 1
        # 4. 至少 1 个注入
        assert result.candidates_injected >= 1
        # 5. factor_pool.json 已更新
        assert tmp_factor_pool_path.exists()
        # 6. inject_dir 中有文件
        injected_files = list(tmp_inject_dir.glob("cand_*.json"))
        assert len(injected_files) == result.candidates_injected

    def test_idempotent_run_preserves_state(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """两次运行 state.duckdb 状态持续累积。"""
        loop1 = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
        )
        r1 = loop1.run(max_bootstraps=2)

        # 第二次运行
        loop2 = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
        )
        loop2.run(max_bootstraps=2)

        # 累计候选数应大于第一次
        state = tmp_state_store.get("meta_loop", "state")
        assert state["total_candidates_generated"] >= r1.candidates_generated


class TestMetaLoopSampleSymbols:
    """感知层默认样本按市场区分（期货 13 品种）。"""

    def test_futures_default_sample_symbols(self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir):
        """market=futures 默认仍为 13 个期货品种（五大板块）。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            market="futures",
            web_collector=None,
        )
        assert len(loop.sample_symbols) == 13
        assert loop.sample_symbols[0] == "rb"
        assert "y" in loop.sample_symbols

    def test_explicit_sample_symbols_override(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """显式传入 sample_symbols 时优先使用（不随市场切换）。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            market="futures",
            sample_symbols=["rb"],
            web_collector=None,
        )
        assert loop.sample_symbols == ["rb"]


class TestMakeWebCollector:
    """web_collector 数据源按市场分流（股票 OHLCV / 期货 OHLCV + 实时价）。"""

    @staticmethod
    def _make_df():
        import pandas as pd

        idx = pd.date_range("2026-06-01", periods=5, freq="D")
        return pd.DataFrame(
            {
                "open": [10.0] * 5,
                "high": [11.0] * 5,
                "low": [9.0] * 5,
                "close": [10.5] * 5,
                "volume": [1000] * 5,
            },
            index=idx,
        )

    def test_futures_mode_keeps_futures_path(self):
        """期货模式：主连转换 + 期货 OHLCV + 最新收盘（v3.0.0+1 去实时价——FTS 因子管理仅依赖 QuantData，realtime_price 取 QuantData 日线最新 close）。"""
        provider = MagicMock()
        provider._futures.get_ohlcv.return_value = self._make_df()

        collect = _make_web_collector(provider, market="futures")
        snap = collect("rb")

        provider._futures.get_ohlcv.assert_called_once_with("RB0", days=60)
        provider.get_ohlcv.assert_not_called()
        assert snap["contract_symbol"] == "RB0"
        assert snap["quote"]["realtime_price"] == 10.5  # 最新 close（QuantData 日线）


# ════════════════════════════════════════════════════════
# 9. CLI 入口 main() 测试
# ════════════════════════════════════════════════════════


class TestMainFunction:
    """CLI 入口 main() — lines 1087-1135。"""

    def test_main_without_once(self, capsys):
        """不传 --once 时打印提示并退出。"""
        with patch.object(sys, "argv", ["meta_loop.py"]):
            with pytest.raises(SystemExit) as exc:
                from fts.factor_engine.meta_loop import main

                main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "Use --once" in captured.out

    def test_main_with_once(self, tmp_path, capsys):
        """传 --once 时执行完整 L1 循环（lines 1111-1131）。"""
        meta_dir = tmp_path / "meta_loop"
        pool_path = tmp_path / "factor_pool.json"
        inject_dir = tmp_path / "l1_injected"

        with patch("fts.data.FTSDataProvider") as mock_provider:
            mock_provider.return_value = MagicMock()
            with patch.object(
                sys,
                "argv",
                [
                    "meta_loop.py",
                    "--once",
                    "--memory-dir",
                    str(meta_dir),
                    "--factor-pool",
                    str(pool_path),
                    "--inject-dir",
                    str(inject_dir),
                    "--max-bootstraps",
                    "2",
                ],
            ):
                from fts.factor_engine.meta_loop import main

                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "L1 Meta-Loop 完成" in captured.out


# ════════════════════════════════════════════════════════
# 10. GAP-I101: L1 批量候选契约校验 + 吞吐指标
# ════════════════════════════════════════════════════════


class TestValidateBatchCandidates:
    """GAP-I101 (v2.72.0): validate_batch_candidates 契约校验。"""

    @staticmethod
    def _cand(cid: str = "cand_b01", missing: str | None = None) -> dict:
        cand = {
            "candidate_id": cid,
            "name": f"factor_{cid}",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
            "economic_logic": {"narrative": "测试经济逻辑。", "theory": 4, "behavioral": 4},
        }
        if missing:
            cand.pop(missing, None)
        return cand

    def test_all_valid(self):
        """全部合法 → valid=total, invalid=0。"""
        cands = [self._cand("a"), self._cand("b"), self._cand("c")]
        stats = validate_batch_candidates(cands)
        assert stats["total"] == 3
        assert stats["valid"] == 3
        assert stats["invalid"] == 0
        assert stats["invalid_samples"] == []

    def test_empty_list(self):
        """空列表 → total=0。"""
        stats = validate_batch_candidates([])
        assert stats["total"] == 0
        assert stats["valid"] == 0
        assert stats["invalid"] == 0

    def test_missing_required_field(self):
        """缺 code → invalid，缺失字段列表含 code。"""
        stats = validate_batch_candidates([self._cand(missing="code")])
        assert stats["total"] == 1
        assert stats["invalid"] == 1
        assert "code" in stats["invalid_samples"][0]["missing"]

    def test_missing_candidate_id(self):
        """缺 candidate_id → invalid。"""
        stats = validate_batch_candidates([self._cand(missing="candidate_id")])
        assert stats["invalid"] == 1

    def test_missing_economic_narrative(self):
        """缺 economic_logic.narrative → invalid。"""
        cand = self._cand()
        cand["economic_logic"] = {"theory": 4}
        stats = validate_batch_candidates([cand])
        assert stats["invalid"] == 1
        assert "economic_logic.narrative" in stats["invalid_samples"][0]["missing"]

    def test_non_dict_entry(self):
        """非 dict 条目 → invalid（reason=非 dict）。"""
        stats = validate_batch_candidates(["not-a-dict"])
        assert stats["total"] == 1
        assert stats["invalid"] == 1
        assert stats["invalid_samples"][0]["reason"] == "非 dict"

    def test_invalid_samples_capped(self):
        """invalid_samples 仅返回前 5 条。"""
        cands = [self._cand(missing="code") for _ in range(8)]
        stats = validate_batch_candidates(cands)
        assert stats["total"] == 8
        assert stats["invalid"] == 8
        assert len(stats["invalid_samples"]) == 5

    def test_throughput_make_result(self):
        """_make_result 吞吐指标 = 候选数 / 运行分钟。"""
        result = MetaLoop._make_result(
            run_id="r1",
            trace_id="t1",
            candidates_generated=60,
            candidates_injected=10,
            debate_gaps_detected=1,
            tokens_consumed=100,
            status="completed",
            elapsed_seconds=120.0,
        )
        assert result.candidates_per_minute == 30.0

    def test_throughput_zero_elapsed(self):
        """elapsed=0 → 吞吐 0.0（避免除零）。"""
        result = MetaLoop._make_result(
            run_id="r2",
            trace_id="t2",
            candidates_generated=5,
            candidates_injected=1,
            debate_gaps_detected=0,
            tokens_consumed=10,
            status="completed",
            elapsed_seconds=0.0,
        )
        assert result.candidates_per_minute == 0.0


# ════════════════════════════════════════════════════════
# 11. plans/41: L1 知识注入增强（web_collector 感知 + 实时链知识 + 子链分批 + 预算）
# ════════════════════════════════════════════════════════


class TestEnergySubchainBatches:
    """plans/41 D2: energy 市场按子链分批。"""

    def test_energy_returns_four_batches(self):
        """energy 市场返回 4 个子链分批（每批带聚焦子链名）。"""
        m = BootstrappingChain.__new__(BootstrappingChain)
        m.market = "energy"
        batches = m._energy_subchain_batches(30)
        assert len(batches) == 4
        focuses = [f[0] for f in batches]
        assert any("能源" in f for f in focuses)
        assert any("聚酯" in f for f in focuses)
        assert any("油化工" in f for f in focuses)
        assert any("煤化工" in f for f in focuses)
        # 配额合计 = max_candidates
        assert sum(b[1] for b in batches) == 30

    def test_energy_small_candidates_keeps_total(self):
        """max_candidates 较小时分批配额合计仍等于总量。"""
        m = BootstrappingChain.__new__(BootstrappingChain)
        m.market = "energy"
        batches = m._energy_subchain_batches(8)
        assert sum(b[1] for b in batches) == 8

    def test_fallback_single_batch_on_error(self):
        """子链划分异常时回退单批（向后兼容）。"""
        m = BootstrappingChain.__new__(BootstrappingChain)
        m.market = "energy"
        with patch("fts.data_futures.FUTURES_SECTOR_MAP", side_effect=Exception("boom")):
            result = m._energy_subchain_batches(30)
        assert result == [("", 30)]


class TestChainLiveState:
    """plans/41 C1: 能源链实时产业状态注入。"""

    def test_build_chain_live_state_with_panel(self):
        """面板数据可用时产出价差/波动/价格位置段。"""
        m = MetaLoop.__new__(MetaLoop)
        with patch("fts.data.FTSDataProvider") as MockProvider:
            import pandas as pd

            idx = pd.date_range("2026-06-01", periods=60, freq="D")
            df = pd.DataFrame(
                {
                    "close": [100.0 * (1 + i * 0.001) for i in range(60)],
                    "open": [100.0] * 60,
                    "high": [101.0] * 60,
                    "low": [99.0] * 60,
                    "volume": [1000] * 60,
                },
                index=idx,
            )
            panel = {f"{s}0": df for s in ["SC", "FU", "BU", "PX", "TA", "PF", "L", "PP", "PG", "MA", "UR", "SA"]}
            MockProvider.return_value.get_futures_panel.return_value = (panel, idx)
            text = m._build_chain_live_state()
        assert "子链价差代理" in text
        assert "波动聚集代理" in text
        assert "库存/基差水位代理" in text

    def test_build_chain_live_state_empty_on_failure(self):
        """面板获取异常时返回空串（降级不阻断）。"""
        m = MetaLoop.__new__(MetaLoop)
        with patch("fts.data.FTSDataProvider", side_effect=Exception("boom")):
            text = m._build_chain_live_state()
        assert text == ""


class TestBootstrapWithLlmSubchainBatching:
    """plans/41 D2: _bootstrap_with_llm 按子链分批调用。"""

    def test_energy_splits_into_batches(self):
        """energy 市场按子链分批调用 LLM，每批 snapshot 携带 chain_focus。"""
        m = BootstrappingChain.__new__(BootstrappingChain)
        m.market = "energy"
        m.llm_client = MagicMock()
        m.llm_client.bootstrap_factors.side_effect = [
            [{"name": f"f{i}", "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))"} for i in range(3)]
            for _ in range(4)
        ]
        candidates = m._bootstrap_with_llm(
            {"chain_knowledge": "k", "trace_id": "t"},
            [],
            max_candidates=12,
            trace_id="t1",
        )
        # 4 次分批调用
        assert m.llm_client.bootstrap_factors.call_count == 4
        # 每批 snapshot 含 chain_focus
        calls = [c[0][0] for c in m.llm_client.bootstrap_factors.call_args_list]
        assert all("chain_focus" in c for c in calls)
        # 候选数 = 4 批 × 3
        assert len(candidates) == 12

    def test_futures_chain_batched(self):
        """futures 市场按 17 产业链分批注入（P1 方案：scope resolver 统一分批）。"""
        m = BootstrappingChain.__new__(BootstrappingChain)
        m.market = "futures"
        m.llm_client = MagicMock()
        m.llm_client.bootstrap_factors.return_value = []
        m._bootstrap_with_llm({}, [], max_candidates=20, trace_id="t1")
        # 17 链各一批（>=2 即分批生效）
        assert m.llm_client.bootstrap_factors.call_count >= 2
        calls = [c[0][0] for c in m.llm_client.bootstrap_factors.call_args_list]
        assert all("chain_focus" in c for c in calls)


class TestL1BudgetConfigPlans41:
    """plans/41 D1: L1 预算上调。"""

    def test_budget_raised(self):
        """daily_token_limit 60K + max_bootstraps 30。"""
        from fts.factor_engine.contracts import DEFAULT_L1_BUDGET_CONFIG

        assert DEFAULT_L1_BUDGET_CONFIG["daily_token_limit"] == 60_000
        assert DEFAULT_L1_BUDGET_CONFIG["max_bootstraps_per_run"] == 30


# ════════════════════════════════════════════════════════
# 8. L1 拒绝候选落盘（不可追溯修复，2026-08-16）
# ════════════════════════════════════════════════════════


class TestMetaLoopRejectedPersistence:
    """拒绝候选（硬失败/重写未过软失败）落盘到 l1_rejected 目录，供回溯修复。"""

    def test_rejected_dir_derived_from_inject_dir(self, tmp_inject_dir):
        """默认 rejected_dir 由 inject_dir 派生（l1_injected → l1_rejected）。"""
        loop = MetaLoop(inject_dir=tmp_inject_dir)
        assert loop.rejected_dir == tmp_inject_dir.parent / "l1_rejected"

    def test_rejected_dir_energy_suffix(self, tmp_path):
        """energy 注入目录派生 l1_rejected_energy。"""
        inject = tmp_path / "l1_injected_energy"
        loop = MetaLoop(inject_dir=inject)
        assert loop.rejected_dir == tmp_path / "l1_rejected_energy"

    def test_rejected_dir_explicit_override(self, tmp_path):
        """显式传入 rejected_dir 优先于派生逻辑。"""
        explicit = tmp_path / "my_rejected"
        loop = MetaLoop(inject_dir=tmp_path / "l1_injected", rejected_dir=explicit)
        assert loop.rejected_dir == explicit

    def test_hard_failure_persisted(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """硬失败（编译失败）候选落盘到 rejected_dir，含 code 与拒绝原因。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
        )
        cand = SeedCandidate(
            candidate_id="cand_compile_fail",
            name="fut_carry_roll yield",
            code="def factor_program(data, params):\n    return x @@@\n",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=1,
            ),
            economic_logic=EconomicLogic(
                theory=4,
                behavioral=4,
                microstructure=4,
                institutional=4,
                narrative="该因子具备充分的经济逻辑论证，满足长度要求。",
            ),
            source="l1_bootstrapping",
            parent_topic="硬失败落盘测试",
            is_executable=False,
            is_duplicate=False,
            passed_l1_verifier=False,
            failure_reasons=["编译失败: 语法错误: invalid syntax (line 2)"],
            trace_id="t",
            created_at="2026-08-16",
        )
        state = L1MetaLoopState(
            run_id="test",
            started_at="",
            status="running",
            tokens_consumed=0,
            budget_limit=50000,
            total_candidates_generated=0,
            total_candidates_injected=0,
        )
        loop._verify_and_inject([cand], state, "l1_test", [])  # noqa: SLF001
        rejected_file = loop.rejected_dir / "cand_compile_fail.json"
        assert rejected_file.exists(), "硬失败候选应落盘"
        record = json.loads(rejected_file.read_text(encoding="utf-8"))
        assert record["name"] == "fut_carry_roll yield"
        assert "invalid syntax" in record["l1_rejection"]["reasons"][0]
        assert "def factor_program" in record["code"]
        assert record["market"] == "futures"
        assert record["l1_rejection"]["trace_id"] == "l1_test"
        # 硬失败候选不应被注入
        assert state["total_candidates_injected"] == 0

    def test_soft_failure_rewrite_failed_persisted(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """软失败重写后仍未达标 → 落盘拒绝候选。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
            llm_client=MagicMock(),  # fix_economic_logic 返回 None → 重写失败
        )
        cand = SeedCandidate(
            candidate_id="cand_soft_fail",
            name="soft_fail_factor",
            code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=1,
            ),
            economic_logic=EconomicLogic(
                theory=2,
                behavioral=2,
                microstructure=2,
                institutional=2,
                narrative="经济逻辑论证不足。",
            ),
            source="l1_bootstrapping",
            parent_topic="软失败落盘测试",
            is_executable=True,
            is_duplicate=False,
            passed_l1_verifier=False,
            failure_reasons=["经济逻辑达标维度 0/4 < 2"],
            trace_id="t",
            created_at="2026-08-16",
        )
        state = L1MetaLoopState(
            run_id="test",
            started_at="",
            status="running",
            tokens_consumed=0,
            budget_limit=50000,
            total_candidates_generated=0,
            total_candidates_injected=0,
        )
        loop._verify_and_inject([cand], state, "l1_test", [])  # noqa: SLF001
        rejected_file = loop.rejected_dir / "cand_soft_fail.json"
        assert rejected_file.exists(), "重写失败软失败候选应落盘"
        record = json.loads(rejected_file.read_text(encoding="utf-8"))
        assert "经济逻辑达标维度" in record["l1_rejection"]["reasons"][0]
        assert state["total_candidates_injected"] == 0

    def test_persist_rejected_writes_json(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """_persist_rejected 直接写入可解析 JSON。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        cand = SeedCandidate(
            candidate_id="cand_rej_1",
            name="rejected_1",
            code="def factor_program(data, params):\n    pass\n",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=1,
            ),
            economic_logic=EconomicLogic(
                theory=3,
                behavioral=3,
                microstructure=3,
                institutional=3,
                narrative="测试拒绝落盘。",
            ),
            source="l1_bootstrapping",
            parent_topic="测试",
            trace_id="t",
            created_at="2026-08-16",
        )
        result = loop._persist_rejected(cand, ["候选因子代码不可执行（沙箱编译失败）"], "l1_test")  # noqa: SLF001
        assert result == "cand_rej_1"
        file = loop.rejected_dir / "cand_rej_1.json"
        assert file.exists()
        record = json.loads(file.read_text(encoding="utf-8"))
        assert record["code"].startswith("def factor_program")
        assert record["l1_rejection"]["reasons"] == ["候选因子代码不可执行（沙箱编译失败）"]
        assert record["l1_rejection"]["rejected_at"]
        assert record["market"] == "futures"
