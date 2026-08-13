"""tests/factor_engine/extractors/test_base.py — 提取器基类与管道抽象测试。

覆盖:
    1. BaseExtractor: 初始化 / pause / resume / candidate_id / signature / LLM 提取全路径
    2. BaseExtractorPipeline: 提取合并 / 暂停恢复 / 状态持久化 / YAML 转换
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[3]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import SeedCandidate  # noqa: E402
from fts.factor_engine.extractors.base import BaseExtractor, BaseExtractorPipeline  # noqa: E402


# ─── 测试用具体提取器 ──────────────────────────────────────


class DummyExtractor(BaseExtractor):
    """最小可实例化提取器。"""

    def __init__(self, name: str, paused: bool = False, llm_client=None, results=None):
        super().__init__(name, paused=paused, llm_client=llm_client)
        self.results = results or []

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        if self.paused:
            return []
        return self.results


def _dummy_candidate(name: str = "dummy") -> SeedCandidate:
    return SeedCandidate(
        candidate_id="cand_test",
        name=name,
        code="def factor_program(data, params):\n    return data['close']",
        params={},
        signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 20},
        economic_logic={},
        source="test",
        market="futures",
        parent_topic="test",
        trace_id="t1",
        is_executable=True,
    )


# ─── BaseExtractor ─────────────────────────────────────────


class TestBaseExtractor:
    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseExtractor("x")  # type: ignore[abstract]

    def test_init_attributes(self):
        ext = DummyExtractor("src_a", llm_client=object())
        assert ext.name == "src_a"
        assert ext.paused is False
        assert ext.llm_client is not None

    def test_pause_resume(self):
        ext = DummyExtractor("src_a")
        ext.pause()
        assert ext.paused is True
        ext.resume()
        assert ext.paused is False

    def test_make_candidate_id_format(self):
        cid = BaseExtractor._make_candidate_id("my_extractor")
        assert cid.startswith("cand_")
        assert len(cid) == len("cand_") + 8
        # 每次调用随机（secrets.token_hex）→ 不同
        assert cid != BaseExtractor._make_candidate_id("my_extractor")

    def test_make_signature_fields(self):
        sig = BaseExtractor._make_signature(["close", "volume"], output_type="score", frequency="1h", lookback=5)
        assert sig == {
            "input_fields": ["close", "volume"],
            "output_type": "score",
            "frequency": "1h",
            "lookback": 5,
        }

    # ── _llm_extract_factors ──

    def test_llm_extract_without_client_returns_empty(self):
        ext = DummyExtractor("src_a")
        assert ext._llm_extract_factors("some text", "t1") == []

    def test_llm_extract_blank_text_returns_empty(self):
        ext = DummyExtractor("src_a", llm_client=object())
        assert ext._llm_extract_factors("   ", "t1") == []

    def test_llm_extract_generate_json_path(self):
        client = _LLMStub(mode="generate_json")
        ext = DummyExtractor("src_a", llm_client=client)
        candidates = ext._llm_extract_factors("text about momentum", "t1", market="futures")
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["name"].startswith("fut_")  # futures 市场前缀（主系统期货化，plans/32 剥离）
        assert cand["market"] == "futures"  # SeedCandidate 固定 market
        assert cand["source"] == "l1_extractor_pipeline"
        assert cand["trace_id"] == "t1"
        assert cand["is_executable"] is False
        assert cand["economic_logic"].get("theory") == 4

    def test_llm_extract_complete_json_parse_path(self):
        client = _CompleteOnlyStub()
        ext = DummyExtractor("src_a", llm_client=client)
        candidates = ext._llm_extract_factors("text", "t1", market="futures")
        assert len(candidates) == 1
        assert candidates[0]["name"].startswith("fut_")

    def test_llm_extract_complete_saves_debug_file(self, tmp_path, monkeypatch):
        """complete 路径保存 LLM 原始响应到 debug 文件（P1b）。"""
        monkeypatch.chdir(tmp_path)
        client = _CompleteOnlyStub()
        ext = DummyExtractor("src_a", llm_client=client)
        candidates = ext._llm_extract_factors("text", "t1", market="futures")
        assert len(candidates) == 1
        debug_file = tmp_path / "debug_llm_response_t1_src_a.txt"
        assert debug_file.exists()
        content = debug_file.read_text(encoding="utf-8")
        assert "fut_trend_1" in content

    def test_llm_extract_debug_write_failure_does_not_break(self, tmp_path, monkeypatch):
        """debug 文件写入失败不应中断提取流程（P1b）。"""
        monkeypatch.chdir(tmp_path)
        client = _CompleteOnlyStub()
        ext = DummyExtractor("src_a", llm_client=client)
        with patch("builtins.open", side_effect=OSError("disk full")):
            candidates = ext._llm_extract_factors("text", "t1", market="futures")
        assert len(candidates) == 1  # 提取仍成功

    def test_llm_extract_non_list_wrapped(self):
        class _Stub:
            def generate_json(self, prompt, max_tokens=4000):
                return {"name": "fut_single", "code": "x = 1"}  # 单个 dict 非 list

        ext = DummyExtractor("src_a", llm_client=_Stub())
        candidates = ext._llm_extract_factors("text", "t1")
        assert len(candidates) == 1

    def test_llm_extract_skips_invalid_items(self):
        class _Stub:
            def generate_json(self, prompt, max_tokens=4000):
                return [
                    {"name": "fut_ok", "code": "x = 1"},
                    {"name": "no_code"},  # 缺 code → 跳过
                    "not-a-dict",  # 非 dict → 跳过
                ]

        ext = DummyExtractor("src_a", llm_client=_Stub())
        candidates = ext._llm_extract_factors("text", "t1")
        assert len(candidates) == 1
        assert candidates[0]["name"] == "fut_ok"

    def test_llm_extract_economic_logic_non_dict(self):
        class _Stub:
            def generate_json(self, prompt, max_tokens=4000):
                return {"name": "fut_x", "code": "x = 1", "economic_logic": "not-dict"}

        ext = DummyExtractor("src_a", llm_client=_Stub())
        candidates = ext._llm_extract_factors("text", "t1")
        assert len(candidates) == 1
        assert candidates[0]["economic_logic"] == {}

    def test_llm_extract_exception_returns_empty(self):
        class _Stub:
            def generate_json(self, prompt, max_tokens=4000):
                raise RuntimeError("boom")

        ext = DummyExtractor("src_a", llm_client=_Stub())
        assert ext._llm_extract_factors("text", "t1") == []

    def test_llm_extract_complete_parse_error_returns_empty(self):
        class _Stub:
            def complete(self, prompt, max_tokens=4000):
                return ("not json at all", None)

        ext = DummyExtractor("src_a", llm_client=_Stub())
        assert ext._llm_extract_factors("text", "t1") == []


class _CompleteOnlyStub:
    """仅实现 complete() 的 LLM 客户端 — 用于测试 complete 分支（无 generate_json 时）。"""

    def complete(self, prompt, max_tokens=4000):
        payload = [
            {
                "name": "fut_trend_1",
                "code": "def factor_program(data, params):\n    return data['close']",
                "params": {"window": 10},
            }
        ]
        return json.dumps(payload), None


class _LLMStub:
    """模拟 LLM 客户端：generate_json 或 complete 两种模式。"""

    def __init__(self, mode: str = "generate_json"):
        self.mode = mode

    def generate_json(self, prompt, max_tokens=4000):
        return [
            {
                "name": "stk_momentum_1" if "股票" in prompt else "fut_momentum_1",
                "code": "def factor_program(data, params):\n    return data['close']",
                "params": {"window": 20},
                "input_fields": ["close", "volume"],
                "lookback": 20,
                "output_type": "signal",
                "frequency": "daily",
                "economic_logic": {"theory": 4, "behavioral": 3, "microstructure": 4, "institutional": 3},
            }
        ]

    def complete(self, prompt, max_tokens=4000):
        payload = [
            {
                "name": "fut_trend_1",
                "code": "def factor_program(data, params):\n    return data['close']",
                "params": {"window": 10},
            }
        ]
        return json.dumps(payload), None


# ─── BaseExtractorPipeline ─────────────────────────────────


class TestBaseExtractorPipeline:
    @pytest.fixture(autouse=True)
    def _isolated_store(self, tmp_path):
        """每个测试使用独立临时 state.duckdb，避免全局 SSOT 相互污染。"""
        from fts.store.state_db import StateKVStore

        self._store = StateKVStore(tmp_path / "state.duckdb")
        yield
        self._store.close()

    def _make_pipeline(self, tmp_path, paused_src: str | None = None):
        exts = [
            DummyExtractor("a", results=[_dummy_candidate("ca")]),
            DummyExtractor("b", results=[_dummy_candidate("cb"), _dummy_candidate("cb2")]),
        ]
        if paused_src:
            for e in exts:
                if e.name == paused_src:
                    e.pause()
        pipe = BaseExtractorPipeline(
            exts, market="futures", state_path=tmp_path / "state.json", state_store=self._store
        )
        return pipe, exts

    def test_init_builds_extractor_map(self, tmp_path):
        pipe, exts = self._make_pipeline(tmp_path)
        assert set(pipe.extractors) == {"a", "b"}
        assert pipe.market == "futures"

    def test_extract_merges_all(self, tmp_path):
        pipe, _ = self._make_pipeline(tmp_path)
        candidates = pipe.extract("t1")
        assert len(candidates) == 3

    def test_extract_skips_paused(self, tmp_path):
        pipe, _ = self._make_pipeline(tmp_path, paused_src="a")
        candidates = pipe.extract("t1")
        assert len(candidates) == 2
        assert all(c["name"].startswith("cb") for c in candidates)

    def test_extract_survives_source_exception(self, tmp_path):
        class BoomExtractor(DummyExtractor):
            def extract(self, trace_id):
                raise RuntimeError("source down")

        pipe = BaseExtractorPipeline(
            [BoomExtractor("bad"), DummyExtractor("good", results=[_dummy_candidate("ok")])],
            market="futures",
            state_path=tmp_path / "state.json",
            state_store=self._store,
        )
        candidates = pipe.extract("t1")
        assert len(candidates) == 1
        assert candidates[0]["name"] == "ok"

    def _get_extractor_state(self):
        """读取当前测试临时 store 中 extractors/state 值。"""
        return self._store.get("extractors", "state")

    def test_pause_resume_source_persists(self, tmp_path):
        pipe, _ = self._make_pipeline(tmp_path)
        pipe.pause_source("a")
        assert pipe.is_paused("a") is True
        assert pipe.is_paused("b") is False
        # 状态已写入 DuckDB（SSOT）
        assert self._get_extractor_state()["futures"]["a"] is True
        # 重新创建管道从 DuckDB 加载已暂停状态
        pipe2 = BaseExtractorPipeline(
            [DummyExtractor("a"), DummyExtractor("b")],
            market="futures",
            state_path=tmp_path / "state.json",
            state_store=self._store,
        )
        assert pipe2.is_paused("a") is True
        pipe2.resume_source("a")
        assert pipe2.is_paused("a") is False

    def test_pause_unknown_source_noop(self, tmp_path):
        pipe, _ = self._make_pipeline(tmp_path)
        pipe.pause_source("nonexistent")  # 不抛异常
        assert pipe.is_paused("nonexistent") is True  # 不存在视为暂停

    def test_load_state_when_no_duckdb_state(self, tmp_path):
        # 无 DuckDB 状态时，所有源默认未暂停
        pipe = BaseExtractorPipeline(
            [DummyExtractor("a")], market="futures", state_path=tmp_path / "nope.json", state_store=self._store
        )
        assert pipe.is_paused("a") is False

    def test_load_state_from_duckdb(self, tmp_path):
        pipe, _ = self._make_pipeline(tmp_path)
        pipe.pause_source("b")
        # 新管道从 DuckDB 加载已暂停状态
        pipe2 = BaseExtractorPipeline(
            [DummyExtractor("a"), DummyExtractor("b")],
            market="futures",
            state_path=tmp_path / "state.json",
            state_store=self._store,
        )
        assert pipe2.is_paused("b") is True
        assert pipe2.is_paused("a") is False

    def test_save_state_preserves_other_markets(self, tmp_path):
        pipe1 = BaseExtractorPipeline(
            [DummyExtractor("a")], market="futures", state_path=tmp_path / "state.json", state_store=self._store
        )
        pipe1.pause_source("a")
        # 另一个 market 实例写状态（同一 store，不同 market 键共存）
        pipe2 = BaseExtractorPipeline(
            [DummyExtractor("a")], market="stock", state_path=tmp_path / "state.json", state_store=self._store
        )
        pipe2.pause_source("a")
        state = self._get_extractor_state()
        assert state["futures"]["a"] is True
        assert state["stock"]["a"] is True

    def test_yaml_factor_to_candidate(self, tmp_path):
        factor = {
            "name": "momentum_yaml",
            "code": "def factor_program(data, params):\n    return data['close']",
            "params": {"window": 5},
            "input_fields": ["close"],
            "lookback": 5,
            "economic_logic": {"theory": 3},
        }
        cand = BaseExtractorPipeline._yaml_factor_to_candidate(
            factor, source="broker", market="stock", trace_id="t9", family_name="trend"
        )
        assert isinstance(cand, dict)
        assert cand["name"] == "momentum_yaml"
        assert cand["market"] == "stock"
        assert cand["trace_id"] == "t9"
        assert cand["is_executable"] is True
        assert cand["parent_topic"] == "extractor_pipeline/trend/momentum_yaml"
        assert cand["signature"]["lookback"] == 5

    def test_yaml_factor_to_candidate_defaults(self, tmp_path):
        cand = BaseExtractorPipeline._yaml_factor_to_candidate({}, source="s", market="futures", trace_id="t")
        assert cand["name"] == "unknown"
        assert cand["signature"]["input_fields"] == ["close"]
        assert cand["economic_logic"] == {}

    def test_yaml_factor_economic_logic_non_dict(self, tmp_path):
        cand = BaseExtractorPipeline._yaml_factor_to_candidate(
            {"name": "x", "economic_logic": 42}, source="s", market="futures", trace_id="t"
        )
        assert cand["economic_logic"] == {}
