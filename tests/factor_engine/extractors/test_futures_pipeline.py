"""tests/factor_engine/extractors/test_futures_pipeline.py — 期货三源提取器管道测试。

覆盖范围:
    - YamlSeedExtractor: YAML 加载 / 文件缺失 / 解析异常 / 空列表 / 暂停
    - ResearchReportExtractor: 研报 API 获取（含备用 API 分支）/ LLM 提取 / 回退
    - AcademicPaperExtractor: arXiv 论文获取 / LLM 提取 / 空结果
    - FuturesExtractorPipeline: 三源组装 / 首次提取后自动暂停 tinysoft
    - create_futures_extractor_pipeline 工厂函数

说明:
    - 所有网络请求均通过 mock 替代，测试不依赖真实网络。
    - LLM 客户端使用假实现（generate_json），避免真实调用。

版本: v1.0.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests
import yaml

_FTS_ROOT = Path(__file__).resolve().parents[3]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.extractors.futures_pipeline import (  # noqa: E402
    FACTOR_FILE_MAP,
    SEEDS_DIR,
    AcademicPaperExtractor,
    FuturesExtractorPipeline,
    ResearchReportExtractor,
    YamlSeedExtractor,
    create_futures_extractor_pipeline,
)


# ─── 工具 ────────────────────────────────────────────────────────


class FakeLLM:
    """假 LLM 客户端，返回固定候选。"""

    def generate_json(self, prompt: str, max_tokens: int = 4000):
        return [
            {
                "name": "fut_trend",
                "code": (
                    "def factor_program(data, params):\n"
                    "    import numpy as np\n"
                    "    return np.clip(data['close'], -1, 1)\n"
                ),
                "params": {"window": 20},
                "input_fields": ["close"],
                "lookback": 20,
                "output_type": "signal",
                "frequency": "daily",
                "economic_logic": {
                    "theory": 4,
                    "behavioral": 3,
                    "microstructure": 4,
                    "institutional": 3,
                    "narrative": "期货趋势因子",
                },
            }
        ]


class _FakeResponse:
    """假 HTTP 响应。"""

    def __init__(self, status_code=200, data=None, content=b"", text=""):
        self.status_code = status_code
        self._data = data
        self.content = content
        self.text = text

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def _isolate_state_store(tmp_path, monkeypatch):
    """全文隔离 state.duckdb（SSOT 读路径切换后，管道默认走全局 SSOT）。"""
    from fts.store import state_db

    store = state_db.StateKVStore(tmp_path / "state.duckdb")
    monkeypatch.setattr(state_db, "get_state_store", lambda: store)
    yield
    store.close()


@pytest.fixture
def futures_yaml_file(tmp_path: Path) -> Path:
    """创建一个含 2 个因子的期货 YAML 种子文件。"""
    doc = {
        "factors": [
            {
                "name": "fut_factor_a",
                "code": "def factor_program(data, params): return data['close']",
                "params": {"window": 10},
                "input_fields": ["close", "volume"],
                "economic_logic": {
                    "theory": 3,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 3,
                    "narrative": "测试期货因子 A",
                },
            },
            {
                "name": "fut_factor_b",
                "code": "def factor_program(data, params): return data['close'] * -1",
                "economic_logic": {
                    "theory": 3,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 3,
                    "narrative": "测试期货因子 B",
                },
            },
        ]
    }
    p = tmp_path / "futures_factors.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True)
    return p


# ─── YamlSeedExtractor ───────────────────────────────────────────


class TestYamlSeedExtractor:
    """测试期货 YamlSeedExtractor。"""

    def test_paused_returns_empty(self, tmp_path: Path):
        """暂停时返回空列表。"""
        ext = YamlSeedExtractor(name="tinysoft", yaml_file=tmp_path / "x.yaml", paused=True)
        assert ext.extract("trace_001") == []

    def test_load_from_yaml_file(self, futures_yaml_file: Path):
        """从 YAML 文件提取因子。"""
        ext = YamlSeedExtractor(name="tinysoft", yaml_file=futures_yaml_file)
        cands = ext.extract("trace_001")
        assert len(cands) == 2
        names = {c["name"] for c in cands}
        assert names == {"fut_factor_a", "fut_factor_b"}
        assert cands[0]["market"] == "futures"
        assert cands[0]["source"] == "l1_extractor_pipeline"
        assert cands[0]["trace_id"] == "trace_001"

    def test_file_not_exists_returns_empty(self, tmp_path: Path):
        """YAML 文件不存在时返回空列表。"""
        ext = YamlSeedExtractor(name="tinysoft", yaml_file=tmp_path / "missing.yaml")
        assert ext.extract("trace_001") == []

    def test_invalid_yaml_returns_empty(self, tmp_path: Path):
        """YAML 解析异常时返回空列表（不抛出）。"""
        p = tmp_path / "bad.yaml"
        p.write_text("not: [valid: yaml: [[[", encoding="utf-8")
        ext = YamlSeedExtractor(name="tinysoft", yaml_file=p)
        assert ext.extract("trace_001") == []

    def test_empty_factors_returns_empty(self, tmp_path: Path):
        """factors 为空列表时返回空列表。"""
        p = tmp_path / "empty.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump({"factors": []}, f, allow_unicode=True)
        ext = YamlSeedExtractor(name="tinysoft", yaml_file=p)
        assert ext.extract("trace_001") == []

    def test_convert_factor_default_family(self, tmp_path: Path):
        """family_name 默认取 name。"""
        ext = YamlSeedExtractor(name="tinysoft", yaml_file=tmp_path / "x.yaml")
        c = ext._convert_factor({"name": "x", "code": "code"}, "trace_001")
        assert c["name"] == "x"
        assert c["parent_topic"] == "extractor_pipeline/tinysoft/x"
        assert c["market"] == "futures"


# ─── ResearchReportExtractor ─────────────────────────────────────


class TestResearchReportExtractor:
    """测试券商研报提取器。"""

    def test_paused_returns_empty(self):
        """暂停时返回空列表。"""
        ext = ResearchReportExtractor(paused=True)
        assert ext.extract("trace_001") == []

    def test_fetch_reports_success(self, monkeypatch):
        """研报 API 返回数据时拼接为文本。"""
        resp = _FakeResponse(
            data={
                "data": [
                    {"title": "螺纹钢研报", "industryName": "黑色系", "stockName": "RB", "summary": "供给收缩"},
                ]
            }
        )
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        ext = ResearchReportExtractor()
        text = ext._fetch_reports()
        assert "标题: 螺纹钢研报" in text
        assert "板块: 黑色系" in text
        assert "标的: RB" in text

    def test_fetch_reports_backup_api(self, monkeypatch):
        """主 API 无数据时走备用搜索 API。"""

        def handler(url, *a, **k):
            if "reportapi" in url:
                return _FakeResponse(data={"data": []})
            # 备用 API
            return _FakeResponse(text="备用研报搜索结果内容")

        monkeypatch.setattr(requests, "get", handler)
        ext = ResearchReportExtractor()
        text = ext._fetch_reports()
        assert "备用研报搜索返回" in text

    def test_fetch_reports_all_fail(self, monkeypatch):
        """主 API 与备用 API 均失败时返回空字符串。"""

        def handler(url, *a, **k):
            if "reportapi" in url:
                return _FakeResponse(data={"data": []})
            return _FakeResponse(status_code=500)

        monkeypatch.setattr(requests, "get", handler)
        ext = ResearchReportExtractor()
        assert ext._fetch_reports() == ""

    def test_fetch_reports_request_exception(self, monkeypatch):
        """研报 API 抛异常时返回空字符串。"""

        def raiser(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(requests, "get", raiser)
        ext = ResearchReportExtractor()
        assert ext._fetch_reports() == ""

    def test_extract_with_reports_and_llm(self, monkeypatch):
        """获取到研报且 LLM 提取成功时返回候选。"""
        ext = ResearchReportExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_reports", lambda: "研报文本内容")
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        c = cands[0]
        assert c["source"] == "l1_extractor_pipeline"
        assert c["parent_topic"].startswith("extractor_pipeline/broker_reports/")
        assert c["name"] == "fut_trend"

    def test_extract_fallback_when_no_reports(self, monkeypatch):
        """研报获取为空时回退到 LLM 生成，并标记 market=futures。"""
        ext = ResearchReportExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_reports", lambda: "")
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        assert cands[0]["market"] == "futures"
        assert cands[0]["source"] == "l1_extractor_pipeline"

    def test_extract_llm_returns_empty_then_fallback(self, monkeypatch):
        """研报有值但 LLM 返回空时走回退逻辑。"""
        ext = ResearchReportExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_reports", lambda: "研报文本")

        calls = {"n": 0}

        def fake_generate_json(prompt, max_tokens=4000):
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return [
                {
                    "name": "fut_fallback",
                    "code": "def factor_program(data, params): return data['close']",
                    "economic_logic": {"narrative": "fallback"},
                }
            ]

        ext.llm_client.generate_json = fake_generate_json

        cands = ext.extract("trace_001")
        assert len(cands) == 1
        assert cands[0]["name"] == "fut_fallback"
        assert cands[0]["market"] == "futures"


# ─── AcademicPaperExtractor ──────────────────────────────────────


class TestAcademicPaperExtractor:
    """测试学术论文提取器。"""

    def test_paused_returns_empty(self):
        """暂停时返回空列表。"""
        ext = AcademicPaperExtractor(paused=True)
        assert ext.extract("trace_001") == []

    def test_fetch_papers_success(self, monkeypatch):
        """arXiv API 返回 Atom XML 时解析出论文文本。"""
        xml = (
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>Futures Trend Paper</title>"
            "<summary>论文摘要内容，用于提取因子。</summary></entry>"
            "</feed>"
        )
        resp = _FakeResponse(content=xml.encode("utf-8"))
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        ext = AcademicPaperExtractor()
        text = ext._fetch_papers()
        assert "标题: Futures Trend Paper" in text
        assert "摘要: 论文摘要内容" in text

    def test_fetch_papers_request_exception(self, monkeypatch):
        """arXiv API 抛异常时返回空字符串。"""

        def raiser(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(requests, "get", raiser)
        ext = AcademicPaperExtractor()
        assert ext._fetch_papers() == ""

    def test_extract_with_papers_and_llm(self, monkeypatch):
        """获取到论文且 LLM 提取成功时返回候选，标记 market=futures。"""
        ext = AcademicPaperExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_papers", lambda: "论文文本内容")
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        c = cands[0]
        assert c["market"] == "futures"
        assert c["source"] == "l1_extractor_pipeline"
        assert c["parent_topic"].startswith("extractor_pipeline/academic_papers/")

    def test_extract_no_papers_returns_empty(self, monkeypatch):
        """论文获取为空时返回空列表。"""
        ext = AcademicPaperExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_papers", lambda: "")
        assert ext.extract("trace_001") == []


# ─── FuturesExtractorPipeline ────────────────────────────────────


class TestFuturesExtractorPipeline:
    """测试期货提取器管道。"""

    def test_construct_three_sources(self, tmp_path: Path):
        """管道包含三源及宏观事件源且市场为 futures（GAP-I103）。"""
        pipe = FuturesExtractorPipeline(state_path=str(tmp_path / "state.json"))
        assert set(pipe.extractors) == {"tinysoft", "broker_reports", "academic_papers", "macro_events"}
        assert pipe.market == "futures"

    def test_tinysoft_yaml_path_resolved(self):
        """tinysoft 的 YAML 路径指向 seeds/futures 目录。"""
        assert SEEDS_DIR.name == "futures"
        assert FACTOR_FILE_MAP["tinysoft"] == "tinysoft.yaml"

    def test_extract_pauses_tinysoft_after_first(self, tmp_path: Path, monkeypatch):
        """首次提取后自动暂停 tinysoft 源。"""
        pipe = FuturesExtractorPipeline(state_path=str(tmp_path / "state.json"))
        for ext in pipe.extractors.values():
            monkeypatch.setattr(ext, "extract", lambda trace_id: [])
        pipe.extract("trace_001")
        assert pipe.extractors["tinysoft"].paused is True
        assert pipe.extractors["broker_reports"].paused is False
        assert pipe.extractors["academic_papers"].paused is False
        assert pipe._first_extract is False

    def test_extract_no_pause_when_flag_false(self, tmp_path: Path, monkeypatch):
        """pause_tinysoft_after_first=False 时不自动暂停。"""
        pipe = FuturesExtractorPipeline(
            state_path=str(tmp_path / "state.json"),
            pause_tinysoft_after_first=False,
        )
        for ext in pipe.extractors.values():
            monkeypatch.setattr(ext, "extract", lambda trace_id: [])
        pipe.extract("trace_001")
        assert pipe.extractors["tinysoft"].paused is False

    def test_extract_aggregates_candidates(self, tmp_path: Path, monkeypatch):
        """管道合并三个源的候选。"""
        pipe = FuturesExtractorPipeline(state_path=str(tmp_path / "state.json"))
        tinysoft = pipe.extractors["tinysoft"]
        broker = pipe.extractors["broker_reports"]
        academic = pipe.extractors["academic_papers"]
        monkeypatch.setattr(tinysoft, "extract", lambda trace_id: [{"name": "tiny_cand", "code": "c"}])
        monkeypatch.setattr(broker, "extract", lambda trace_id: [{"name": "broker_cand", "code": "c"}])
        monkeypatch.setattr(academic, "extract", lambda trace_id: [])
        cands = pipe.extract("trace_001")
        assert len(cands) == 2
        names = {c["name"] for c in cands}
        assert names == {"tiny_cand", "broker_cand"}

    def test_pause_and_resume_source(self, tmp_path: Path):
        """pause_source / resume_source 正常工作并持久化状态到 state.duckdb。"""
        pipe = FuturesExtractorPipeline(state_path=str(tmp_path / "state.json"))
        pipe.pause_source("broker_reports")
        assert pipe.extractors["broker_reports"].paused is True
        from fts.store.state_db import get_state_store

        assert get_state_store().get("extractors", "state")["futures"]["broker_reports"] is True

        pipe2 = FuturesExtractorPipeline(state_path=str(tmp_path / "state.json"))
        assert pipe2.extractors["broker_reports"].paused is True
        assert pipe2.extractors["academic_papers"].paused is False

        pipe.resume_source("broker_reports")
        assert pipe.extractors["broker_reports"].paused is False


# ─── 工厂函数 ────────────────────────────────────────────────────


class TestFactory:
    """测试便捷工厂函数。"""

    def test_create_pipeline(self, tmp_path: Path):
        """工厂函数返回可用的管道实例。"""
        pipe = create_futures_extractor_pipeline(
            state_path=str(tmp_path / "state.json"),
            pause_tinysoft_after_first=False,
        )
        assert isinstance(pipe, FuturesExtractorPipeline)
        assert set(pipe.extractors) == {"tinysoft", "broker_reports", "academic_papers", "macro_events"}
