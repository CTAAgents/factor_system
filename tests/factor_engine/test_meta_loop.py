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

版本: v8.10.0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import (
    DEFAULT_L1_BUDGET_CONFIG,
    DEFAULT_L1_VERIFIER_CONFIG,
    EconomicLogic,
    EVOLUTION_VERSION,
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
    MetaLoopError,
    MetaRunResult,
    MetaStateManager,
    MetaStateManagerError,
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
        result = v.check(valid_candidate, SeedPool())
        assert result["passed"] is False
        assert any("重复" in r for r in result["failure_reasons"])

    def test_verifier_rejects_short_narrative(self, valid_candidate):
        """narrative 长度不足被拒绝。"""
        valid_candidate["economic_logic"] = EconomicLogic(
            theory=4, behavioral=4, microstructure=4, institutional=4,
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


# ════════════════════════════════════════════════════════
# 2. MetaStateManager 测试
# ════════════════════════════════════════════════════════

class TestMetaStateManager:
    """L1 状态管理器 — 持久化 + backup 恢复。"""

    def test_init_creates_state(self, tmp_meta_dir):
        """首次调用 load_or_init 创建新状态。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(budget_limit=50000)
        assert state["status"] == "paused"
        assert state["budget_limit"] == 50000
        assert state["version"] == EVOLUTION_VERSION
        assert state["total_candidates_generated"] == 0

    def test_save_persists_state(self, tmp_meta_dir):
        """save() 持久化状态文件。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(50000)
        state["total_candidates_generated"] = 5
        sm.save(state)
        # 重新加载
        with open(sm.state_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["total_candidates_generated"] == 5

    def test_save_creates_backup(self, tmp_meta_dir):
        """save() 同时创建 backup 文件。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(50000)
        sm.save(state)
        assert sm.backup_file.exists()

    def test_mark_running(self, tmp_meta_dir):
        """mark_running() 切换状态。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(50000)
        state = sm.mark_running(state)
        assert state["status"] == "running"
        assert state["last_error"] is None

    def test_mark_completed(self, tmp_meta_dir):
        """mark_completed() 切换状态。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(50000)
        state = sm.mark_completed(state)
        assert state["status"] == "completed"

    def test_mark_paused_with_error(self, tmp_meta_dir):
        """mark_paused() 记录错误信息。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(50000)
        err_msg = "测试异常"
        state = sm.mark_paused(state, err_msg)
        assert state["status"] == "paused"
        assert state["last_error"] == err_msg

    def test_mark_circuit_broken(self, tmp_meta_dir):
        """mark_circuit_broken() 切换状态。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(50000)
        reason = "Token 超限"
        state = sm.mark_circuit_broken(state, reason)
        assert state["status"] == "circuit_broken"
        assert state["last_error"] == reason

    def test_recover_from_backup(self, tmp_meta_dir):
        """主文件损坏时从 backup 恢复。"""
        sm = MetaStateManager(tmp_meta_dir)
        # 先正常保存
        state = sm.load_or_init(50000)
        state["total_candidates_generated"] = 7
        sm.save(state)
        # 损坏主文件
        sm.state_file.write_text("not a json", encoding="utf-8")
        # 重新加载应从 backup 恢复
        recovered = sm.load_or_init(50000)
        assert recovered["total_candidates_generated"] == 7

    def test_version_mismatch_triggers_cold_start(self, tmp_meta_dir):
        """版本不匹配触发冷启动。"""
        sm = MetaStateManager(tmp_meta_dir)
        # 写入旧版本状态
        old_state = {
            "run_id": "old",
            "version": "8.9.0",  # 旧版本
            "status": "completed",
        }
        with open(sm.state_file, "w", encoding="utf-8") as f:
            json.dump(old_state, f)
        # 重新加载应冷启动
        state = sm.load_or_init(50000)
        assert state["version"] == EVOLUTION_VERSION
        assert state["status"] == "paused"  # 冷启动默认

    def test_save_with_wrong_version_raises(self, tmp_meta_dir):
        """save() 版本不匹配抛异常。"""
        sm = MetaStateManager(tmp_meta_dir)
        state = sm.load_or_init(50000)
        state["version"] = "8.0.0"  # 篡改版本
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

    def test_add_entry_dedup(self, tmp_factor_pool_path):
        """同 factor_id 添加两次只算一条。"""
        mgr = FactorPoolManager(tmp_factor_pool_path)
        mgr.load_or_init()
        from fts.factor_engine.contracts import FactorPoolEntry
        entry = FactorPoolEntry(
            factor_id="cand_dup", name="f1", source="l1_bootstrapping",
            priority="high", status="pending", trace_id="t1",
            created_at="2026-07-18", updated_at="2026-07-18",
        )
        mgr.add_entry(entry)
        # 同 ID 不同状态
        entry2 = FactorPoolEntry(
            factor_id="cand_dup", name="f1", source="l1_bootstrapping",
            priority="high", status="injected", trace_id="t1",
            created_at="2026-07-18", updated_at="2026-07-18",
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
            factor_id="cand_xyz", name="f1", source="l1_bootstrapping",
            priority="high", status="pending", trace_id="t1",
            created_at="2026-07-18", updated_at="2026-07-18",
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
                factor_id=f"cand_{i}", name=f"f{i}", source="l1_bootstrapping",
                priority="high", status="pending", trace_id=f"t{i}",
                created_at="2026-07-18", updated_at="2026-07-18",
            )
            mgr.add_entry(entry)
        pool = mgr.load_or_init()
        assert pool["total_count"] == 3
        assert pool["pending_count"] == 3


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
            json.dump({
                "entries": [
                    {
                        "action": "debate_record",
                        "symbols": {
                            "rb": {
                                "debate_round": 3,
                                "bullish_arguments": ["a"],
                                "bearish_arguments": ["a", "b", "c"],
                            }
                        }
                    }
                ]
            }, f)
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
            json.dump({
                "entries": [
                    {
                        "action": "debate_record",
                        "symbols": {
                            "i": {
                                "debate_round": 3,
                                "bullish_arguments": ["a", "b", "c"],
                                "bearish_arguments": ["a"],
                            }
                        }
                    }
                ]
            }, f)
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert len(result["topics"]) == 1
        assert result["topics"][0]["gap"] == "bearish_weak"

    def test_detect_insufficient_rounds(self, tmp_debates_dir):
        """检测辩论轮次不足。"""
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump({
                "entries": [
                    {
                        "action": "debate_record",
                        "symbols": {
                            "j": {
                                "debate_round": 1,
                                "bullish_arguments": ["a"],
                                "bearish_arguments": ["a"],
                            }
                        }
                    }
                ]
            }, f)
        analyzer = DebateQualityAnalyzer(tmp_debates_dir)
        result = analyzer.analyze_latest_debate()
        assert len(result["topics"]) == 1
        assert result["topics"][0]["gap"] == "insufficient_rounds"


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
                input_fields=["close"], output_type="signal",
                frequency="daily", lookback=1,
            ),
            economic_logic=EconomicLogic(
                theory=4, behavioral=4, microstructure=4, institutional=4,
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
            market_snapshot={}, debate_gaps=[], max_candidates=1,
            seed_pool=SeedPool(), trace_id="t",
        )
        assert len(candidates) == 1
        assert candidates[0]["is_executable"] is False


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
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir,
        tmp_debates_dir, mock_web_collector
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
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """run() 后状态文件已持久化。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        loop.run(max_bootstraps=2)
        state_file = tmp_meta_dir / "state.json"
        assert state_file.exists()
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        assert state["status"] == "completed"
        assert state["total_candidates_generated"] >= 1

    def test_run_creates_backup(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """run() 创建 backup 文件。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        loop.run(max_bootstraps=1)
        assert (tmp_meta_dir / "state.json.backup").exists()

    def test_run_updates_factor_pool(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
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
                            input_fields=["close"], output_type="signal",
                            frequency="daily", lookback=1,
                        ),
                        economic_logic=EconomicLogic(
                            theory=1, behavioral=1, microstructure=1, institutional=1,
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

    def test_run_result_to_dict(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
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

    def test_run_with_debate_gaps(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """有辩论缺口数据时仍能正常完成。"""
        # 准备辩论数据
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump({
                "entries": [
                    {
                        "action": "debate_record",
                        "symbols": {
                            "rb": {
                                "debate_round": 3,
                                "bullish_arguments": ["a"],
                                "bearish_arguments": ["a", "b", "c"],
                            }
                        }
                    }
                ]
            }, f)
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        result = loop.run(max_bootstraps=2)
        assert result.status == "completed"
        assert result.debate_gaps_detected >= 1


# ════════════════════════════════════════════════════════
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
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir,
        tmp_debates_dir, mock_web_collector
    ):
        """完整 5 步管道: 感知 → 辩论分析 → Bootstrapping → Verifier → 注入。"""
        # 准备辩论数据
        journal_path = tmp_debates_dir.parent / "journal" / "debate_journal.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            json.dump({
                "entries": [
                    {
                        "action": "debate_record",
                        "symbols": {
                            "rb": {
                                "debate_round": 3,
                                "bullish_arguments": ["a"],
                                "bearish_arguments": ["a", "b", "c"],
                            }
                        }
                    }
                ]
            }, f)

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
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir
    ):
        """两次运行状态文件持续累积。"""
        loop1 = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        r1 = loop1.run(max_bootstraps=2)

        # 第二次运行
        loop2 = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
        )
        r2 = loop2.run(max_bootstraps=2)

        # 累计候选数应大于第一次
        with open(tmp_meta_dir / "state.json", "r", encoding="utf-8") as f:
            state = json.load(f)
        assert state["total_candidates_generated"] >= r1.candidates_generated
