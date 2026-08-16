"""tests/factor_engine/test_web_search_extractor.py — plans/41 A: WebSearchExtractor 测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_FTS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.extractors.futures_pipeline import FuturesExtractorPipeline
from fts.factor_engine.extractors.web_search import WebSearchExtractor

_MOCK_HTML = (
    "<html><head><title>t</title></head><body>"
    "<script>var x=1;</script>"
    "<p>能源化工产业链 裂解价差 聚酯加工差 库存周期 因子</p>"
    "</body></html>"
)


class TestWebSearchExtractor:
    """WebSearchExtractor — 搜索 → 去标签 → LLM 提取候选。"""

    def test_extract_empty_without_llm(self):
        """未配置 llm_client 时直接返回空（不发起网络请求）。"""
        ex = WebSearchExtractor(llm_client=None)
        assert ex.extract("t1") == []

    def test_extract_empty_when_paused(self):
        """暂停态返回空。"""
        ex = WebSearchExtractor(llm_client=MagicMock(), paused=True)
        assert ex.extract("t1") == []

    def test_extract_llm_called_with_text(self):
        """搜索文本去标签后交给 LLM 提取。"""
        llm = MagicMock()
        llm.generate_json.return_value = [
            {
                "name": "fut_chain_spread_01",
                "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
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
                    "narrative": "链内价差均值回归机制，围绕边际成本传导。",
                },
            }
        ]
        ex = WebSearchExtractor(llm_client=llm)
        with patch("requests.get") as mock_get:
            mock_get.return_value.raise_for_status.return_value = None
            mock_get.return_value.text = _MOCK_HTML
            candidates = ex.extract("t1")

        assert len(candidates) == 1
        assert candidates[0]["name"] == "fut_chain_spread_01"
        assert candidates[0]["source"] == "l1_extractor_pipeline"
        assert candidates[0]["parent_topic"].startswith("extractor_pipeline/web_search/")
        # LLM 收到的文本不含 script 标签
        prompt_arg = llm.generate_json.call_args[0][0]
        assert "<script>" not in prompt_arg
        assert "裂解价差" in prompt_arg
        # plans/41 A3: LLM 提取配额为 20
        assert "最多 20 个因子" in prompt_arg

    def test_search_failure_returns_empty(self):
        """所有搜索失败时返回空（不阻断）。"""
        llm = MagicMock()
        ex = WebSearchExtractor(llm_client=llm)
        with patch("requests.get", side_effect=Exception("network down")):
            candidates = ex.extract("t1")
        assert candidates == []

    def test_pipeline_registers_web_search(self):
        """管道默认注册 web_search 源（plans/41 A）。"""
        p = FuturesExtractorPipeline(llm_client=None, macro_enabled=False)
        assert "web_search" in p.extractors
        assert "broker_reports" in p.extractors
        assert "academic_papers" in p.extractors
        assert "tinysoft" in p.extractors

    def test_pipeline_max_factors_default_20(self):
        """LLM 提取源 max_factors 默认 20（plans/41 A3 配置化，config/settings.yaml）。"""
        p = FuturesExtractorPipeline(llm_client=None, macro_enabled=True)
        for name in ("broker_reports", "academic_papers", "macro_events", "web_search"):
            assert getattr(p.extractors[name], "max_factors", None) == 20

    def test_pipeline_max_factors_override(self):
        """显式 max_factors 参数覆盖配置，且仅注入 LLM 提取源（天软不参与）。"""
        p = FuturesExtractorPipeline(llm_client=None, macro_enabled=True, max_factors=30)
        for name in ("broker_reports", "academic_papers", "macro_events", "web_search"):
            assert getattr(p.extractors[name], "max_factors", None) == 30
        # 天软 tinysoft 为静态 YAML 感知源，不参与 LLM 配额注入（保持类默认 20）
        assert getattr(p.extractors["tinysoft"], "max_factors", None) == 20

    def test_pipeline_max_factors_from_config(self):
        """未显式传参时读取 FTSConfig.l1_extractor_max_factors。"""
        from fts.config.settings import FTSConfig

        cfg = FTSConfig(l1_extractor_max_factors=35)
        with patch("fts.factor_engine.extractors.futures_pipeline.get_config", return_value=cfg):
            p = FuturesExtractorPipeline(llm_client=None, macro_enabled=False)
        assert getattr(p.extractors["broker_reports"], "max_factors", None) == 35
        assert getattr(p.extractors["academic_papers"], "max_factors", None) == 35
        assert getattr(p.extractors["web_search"], "max_factors", None) == 35


class TestBootstrapPromptChainFocus:
    """plans/41 D2: bootstrap prompt 支持 chain_focus。"""

    def test_chain_focus_in_prompt(self):
        """market_snapshot 含 chain_focus 时 prompt 注入【本批聚焦子链】。"""
        from fts.llm import OpenAIClient

        prompt = OpenAIClient._build_bootstrap_prompt(
            {"chain_knowledge": "k", "chain_focus": "能源(SC0,FU0,BU0)"},
            [],
            max_candidates=8,
            trace_id="t1",
        )
        assert "【本批聚焦子链】" in prompt
        assert "能源(SC0,FU0,BU0)" in prompt

    def test_no_chain_focus_no_block(self):
        """无 chain_focus 时不注入该段（向后兼容）。"""
        from fts.llm import OpenAIClient

        prompt = OpenAIClient._build_bootstrap_prompt(
            {"chain_knowledge": "k"},
            [],
            max_candidates=8,
            trace_id="t1",
        )
        assert "【本批聚焦子链】" not in prompt
