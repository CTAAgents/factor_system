"""tests/factor_engine/extractors/test_stock_pipeline.py — 股票三源提取器管道测试。

覆盖范围:
    - YamlSeedExtractor: YAML 加载 / 内置回退 / 暂停 / 加载异常
    - StockResearchReportExtractor: 研报 API 获取 / LLM 提取 / 回退
    - StockAcademicPaperExtractor: arXiv 论文获取 / LLM 提取 / 空结果
    - StockExtractorPipeline: 三源组装 / 首次提取后自动暂停 jq_factors
    - create_stock_extractor_pipeline 工厂函数

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

from fts.factor_engine.extractors.stock_pipeline import (  # noqa: E402
    FACTOR_FILE_MAP,
    SEEDS_DIR,
    StockAcademicPaperExtractor,
    StockExtractorPipeline,
    StockResearchReportExtractor,
    YamlSeedExtractor,
    create_stock_extractor_pipeline,
)


# ─── 工具 ────────────────────────────────────────────────────────


class FakeLLM:
    """假 LLM 客户端，返回固定候选。"""

    def generate_json(self, prompt: str, max_tokens: int = 4000):
        return [
            {
                "name": "stk_momentum",
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
                    "theory": 4, "behavioral": 3,
                    "microstructure": 4, "institutional": 3,
                    "narrative": "股票动量因子",
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


@pytest.fixture
def stock_yaml_file(tmp_path: Path) -> Path:
    """创建一个含 2 个因子的股票 YAML 种子文件。"""
    doc = {
        "factors": [
            {
                "name": "stock_factor_a",
                "code": "def factor_program(data, params): return data['close']",
                "params": {"window": 10},
                "input_fields": ["close", "volume"],
                "economic_logic": {
                    "theory": 3, "behavioral": 3,
                    "microstructure": 3, "institutional": 3,
                    "narrative": "测试股票因子 A",
                },
            },
            {
                "name": "stock_factor_b",
                "code": "def factor_program(data, params): return data['close'] * -1",
                "economic_logic": {
                    "theory": 3, "behavioral": 3,
                    "microstructure": 3, "institutional": 3,
                    "narrative": "测试股票因子 B",
                },
            },
        ]
    }
    p = tmp_path / "stock_factors.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True)
    return p


# ─── YamlSeedExtractor ───────────────────────────────────────────


class TestYamlSeedExtractor:
    """测试股票 YamlSeedExtractor。"""

    def test_paused_returns_empty(self):
        """暂停时返回空列表。"""
        ext = YamlSeedExtractor(name="jq_factors", yaml_file=None, paused=True)
        assert ext.extract("trace_001") == []

    def test_load_from_yaml_file(self, stock_yaml_file: Path):
        """从 YAML 文件提取因子。"""
        ext = YamlSeedExtractor(name="jq_factors", yaml_file=stock_yaml_file)
        cands = ext.extract("trace_001")
        assert len(cands) == 2
        names = {c["name"] for c in cands}
        assert names == {"stock_factor_a", "stock_factor_b"}
        assert cands[0]["market"] == "stock"
        assert cands[0]["source"] == "l1_extractor_pipeline"
        assert cands[0]["trace_id"] == "trace_001"

    def test_yaml_missing_falls_back_to_builtin(self, tmp_path: Path):
        """YAML 文件不存在时回退到内置因子。"""
        builtin = [
            {"name": "builtin_stock", "code": "code",
             "economic_logic": {"narrative": "n", "theory": 3, "behavioral": 3,
                                "microstructure": 3, "institutional": 3}},
        ]
        ext = YamlSeedExtractor(
            name="jq_factors",
            yaml_file=tmp_path / "nonexistent.yaml",
            builtin_factors=builtin,
        )
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        assert cands[0]["name"] == "builtin_stock"

    def test_yaml_empty_factors_falls_back_to_builtin(self, tmp_path: Path):
        """YAML 中 factors 为空时回退到内置因子。"""
        p = tmp_path / "empty.yaml"
        with open(p, "w", encoding="utf-8") as f:
            yaml.dump({"factors": []}, f, allow_unicode=True)
        builtin = [{"name": "builtin_b", "code": "code"}]
        ext = YamlSeedExtractor(name="jq_factors", yaml_file=p, builtin_factors=builtin)
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        assert cands[0]["name"] == "builtin_b"

    def test_yaml_invalid_falls_back_to_builtin(self, tmp_path: Path):
        """YAML 解析异常时回退到内置因子（不抛出）。"""
        p = tmp_path / "bad.yaml"
        p.write_text("not: [valid: yaml: [[[", encoding="utf-8")
        builtin = [{"name": "builtin_c", "code": "code"}]
        ext = YamlSeedExtractor(name="jq_factors", yaml_file=p, builtin_factors=builtin)
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        assert cands[0]["name"] == "builtin_c"

    def test_no_yaml_no_builtin_returns_empty(self):
        """无文件无内置因子时返回空列表。"""
        ext = YamlSeedExtractor(name="jq_factors")
        assert ext.extract("trace_001") == []

    def test_convert_factor_default_family(self):
        """family_name 默认取 name。"""
        ext = YamlSeedExtractor(name="jq_factors")
        c = ext._convert_factor({"name": "x", "code": "code"}, "trace_001")
        assert c["name"] == "x"
        assert c["parent_topic"] == "extractor_pipeline/jq_factors/x"


# ─── StockResearchReportExtractor ────────────────────────────────


class TestStockResearchReportExtractor:
    """测试券商研报提取器。"""

    def test_paused_returns_empty(self):
        """暂停时返回空列表。"""
        ext = StockResearchReportExtractor(paused=True)
        assert ext.extract("trace_001") == []

    def test_fetch_reports_success(self, monkeypatch):
        """研报 API 返回数据时拼接为文本。"""
        resp = _FakeResponse(data={"data": [
            {"title": "钢铁行业研报", "industryName": "钢铁", "stockName": "宝钢", "summary": "需求回暖"},
        ]})
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        ext = StockResearchReportExtractor()
        text = ext._fetch_reports()
        assert "标题: 钢铁行业研报" in text
        assert "板块: 钢铁" in text
        assert "标的: 宝钢" in text
        assert "摘要: 需求回暖" in text

    def test_fetch_reports_empty_data(self, monkeypatch):
        """研报 API 返回空 data 时返回空字符串。"""
        resp = _FakeResponse(data={"data": []})
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        ext = StockResearchReportExtractor()
        assert ext._fetch_reports() == ""

    def test_fetch_reports_no_title_skipped(self, monkeypatch):
        """无 title 的研报条目被跳过。"""
        resp = _FakeResponse(data={"data": [{"industryName": "钢铁"}]})
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        ext = StockResearchReportExtractor()
        assert ext._fetch_reports() == ""

    def test_fetch_reports_request_exception(self, monkeypatch):
        """研报 API 抛异常时返回空字符串。"""
        def raiser(*a, **k):
            raise RuntimeError("network down")
        monkeypatch.setattr(requests, "get", raiser)
        ext = StockResearchReportExtractor()
        assert ext._fetch_reports() == ""

    def test_extract_with_reports_and_llm(self, monkeypatch):
        """获取到研报且 LLM 提取成功时返回候选。"""
        ext = StockResearchReportExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_reports", lambda: "研报文本内容")
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        c = cands[0]
        assert c["source"] == "l1_extractor_pipeline"
        assert c["market"] == "stock"
        assert c["parent_topic"].startswith("extractor_pipeline/broker_reports_stock/")
        assert c["name"] == "stk_momentum"

    def test_extract_fallback_when_no_reports(self, monkeypatch):
        """研报获取为空时回退到 LLM 生成，且标记 market=stock。"""
        ext = StockResearchReportExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_reports", lambda: "")
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        assert cands[0]["market"] == "stock"
        assert cands[0]["source"] == "l1_extractor_pipeline"

    def test_extract_llm_returns_empty_then_fallback(self, monkeypatch):
        """研报有值但 LLM 返回空时走回退逻辑。"""
        ext = StockResearchReportExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_reports", lambda: "研报文本")

        # 第一次调用返回候选（研报路径），第二次返回空（回退路径）
        calls = {"n": 0}
        def fake_generate_json(prompt, max_tokens=4000):
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return [{
                "name": "stk_fallback",
                "code": "def factor_program(data, params): return data['close']",
                "economic_logic": {"narrative": "fallback"},
            }]
        ext.llm_client.generate_json = fake_generate_json

        cands = ext.extract("trace_001")
        assert len(cands) == 1
        assert cands[0]["name"] == "stk_fallback"
        assert cands[0]["market"] == "stock"

    def test_extract_no_llm_client(self, monkeypatch):
        """无 LLM 客户端时返回空列表（_llm_extract_factors 跳过）。"""
        ext = StockResearchReportExtractor(llm_client=None)
        monkeypatch.setattr(ext, "_fetch_reports", lambda: "研报文本内容")
        assert ext.extract("trace_001") == []


# ─── StockAcademicPaperExtractor ─────────────────────────────────


class TestStockAcademicPaperExtractor:
    """测试学术论文提取器。"""

    def test_paused_returns_empty(self):
        """暂停时返回空列表。"""
        ext = StockAcademicPaperExtractor(paused=True)
        assert ext.extract("trace_001") == []

    def test_fetch_papers_success(self, monkeypatch):
        """arXiv API 返回 Atom XML 时解析出论文文本。"""
        xml = (
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>Stock Momentum Paper</title>"
            "<summary>论文摘要内容，用于提取因子。</summary></entry>"
            "</feed>"
        )
        resp = _FakeResponse(content=xml.encode("utf-8"))
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
        ext = StockAcademicPaperExtractor()
        text = ext._fetch_papers()
        assert "标题: Stock Momentum Paper" in text
        assert "摘要: 论文摘要内容" in text

    def test_fetch_papers_request_exception(self, monkeypatch):
        """arXiv API 抛异常时返回空字符串。"""
        def raiser(*a, **k):
            raise RuntimeError("network down")
        monkeypatch.setattr(requests, "get", raiser)
        ext = StockAcademicPaperExtractor()
        assert ext._fetch_papers() == ""

    def test_extract_with_papers_and_llm(self, monkeypatch):
        """获取到论文且 LLM 提取成功时返回候选。"""
        ext = StockAcademicPaperExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_papers", lambda: "论文文本内容")
        cands = ext.extract("trace_001")
        assert len(cands) == 1
        c = cands[0]
        assert c["market"] == "stock"
        assert c["source"] == "l1_extractor_pipeline"
        assert c["parent_topic"].startswith("extractor_pipeline/academic_papers_stock/")

    def test_extract_no_papers_returns_empty(self, monkeypatch):
        """论文获取为空时返回空列表。"""
        ext = StockAcademicPaperExtractor(llm_client=FakeLLM())
        monkeypatch.setattr(ext, "_fetch_papers", lambda: "")
        assert ext.extract("trace_001") == []


# ─── StockExtractorPipeline ──────────────────────────────────────


class TestStockExtractorPipeline:
    """测试股票提取器管道。"""

    def test_construct_three_sources(self, tmp_path: Path):
        """管道包含三个源且市场为 stock。"""
        pipe = StockExtractorPipeline(state_path=str(tmp_path / "state.json"))
        assert set(pipe.extractors) == {
            "jq_factors", "broker_reports_stock", "academic_papers_stock",
        }
        assert pipe.market == "stock"

    def test_jq_yaml_path_resolved(self):
        """jq_factors 的 YAML 路径指向 seeds/stock 目录。"""
        assert SEEDS_DIR.name == "stock"
        assert FACTOR_FILE_MAP["jq_factors"] == "jq_factors.yaml"

    def test_extract_pauses_jq_after_first(self, tmp_path: Path, monkeypatch):
        """首次提取后自动暂停 jq_factors 源。"""
        pipe = StockExtractorPipeline(state_path=str(tmp_path / "state.json"))
        for name, ext in pipe.extractors.items():
            monkeypatch.setattr(ext, "extract", lambda trace_id, _n=name: [])
        pipe.extract("trace_001")
        assert pipe.extractors["jq_factors"].paused is True
        assert pipe.extractors["broker_reports_stock"].paused is False
        assert pipe.extractors["academic_papers_stock"].paused is False
        assert pipe._first_extract is False

    def test_extract_second_time_no_repeat_pause(self, tmp_path: Path, monkeypatch):
        """第二次提取时不再重复暂停逻辑（jq 已暂停）。"""
        pipe = StockExtractorPipeline(state_path=str(tmp_path / "state.json"))
        for ext in pipe.extractors.values():
            monkeypatch.setattr(ext, "extract", lambda trace_id: [])
        pipe.extract("trace_001")
        pipe.extract("trace_002")
        assert pipe.extractors["jq_factors"].paused is True

    def test_extract_no_pause_when_flag_false(self, tmp_path: Path, monkeypatch):
        """pause_jq_after_first=False 时不自动暂停。"""
        pipe = StockExtractorPipeline(
            state_path=str(tmp_path / "state.json"), pause_jq_after_first=False,
        )
        for ext in pipe.extractors.values():
            monkeypatch.setattr(ext, "extract", lambda trace_id: [])
        pipe.extract("trace_001")
        assert pipe.extractors["jq_factors"].paused is False

    def test_extract_aggregates_candidates(self, tmp_path: Path, monkeypatch):
        """管道合并三个源的候选。"""
        pipe = StockExtractorPipeline(state_path=str(tmp_path / "state.json"))
        jq = pipe.extractors["jq_factors"]
        broker = pipe.extractors["broker_reports_stock"]
        academic = pipe.extractors["academic_papers_stock"]
        monkeypatch.setattr(jq, "extract", lambda trace_id: [{"name": "jq_cand", "code": "c"}])
        monkeypatch.setattr(broker, "extract", lambda trace_id: [{"name": "broker_cand", "code": "c"}])
        monkeypatch.setattr(academic, "extract", lambda trace_id: [])
        cands = pipe.extract("trace_001")
        assert len(cands) == 2
        names = {c["name"] for c in cands}
        assert names == {"jq_cand", "broker_cand"}

    def test_pause_and_resume_source(self, tmp_path: Path):
        """pause_source / resume_source 正常工作并持久化状态。"""
        state_file = tmp_path / "state.json"
        pipe = StockExtractorPipeline(state_path=str(state_file))
        pipe.pause_source("broker_reports_stock")
        assert pipe.extractors["broker_reports_stock"].paused is True
        assert state_file.exists()

        pipe2 = StockExtractorPipeline(state_path=str(state_file))
        assert pipe2.extractors["broker_reports_stock"].paused is True
        assert pipe2.extractors["academic_papers_stock"].paused is False

        pipe.resume_source("broker_reports_stock")
        assert pipe.extractors["broker_reports_stock"].paused is False

    def test_is_paused(self, tmp_path: Path):
        """is_paused 查询状态。"""
        pipe = StockExtractorPipeline(state_path=str(tmp_path / "state.json"))
        assert pipe.is_paused("jq_factors") is False
        pipe.pause_source("jq_factors")
        assert pipe.is_paused("jq_factors") is True
        # 未知源视为暂停
        assert pipe.is_paused("unknown_source") is True


# ─── 工厂函数 ────────────────────────────────────────────────────


class TestFactory:
    """测试便捷工厂函数。"""

    def test_create_pipeline(self, tmp_path: Path):
        """工厂函数返回可用的管道实例。"""
        pipe = create_stock_extractor_pipeline(
            state_path=str(tmp_path / "state.json"), pause_jq_after_first=False,
        )
        assert isinstance(pipe, StockExtractorPipeline)
        assert set(pipe.extractors) == {
            "jq_factors", "broker_reports_stock", "academic_papers_stock",
        }
