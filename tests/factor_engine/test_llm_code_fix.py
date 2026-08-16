"""
tests/factor_engine/test_llm_code_fix.py — plans/44 C1: LLM 编译修复接口与 bootstrap 集成

覆盖:
    - LLMClient.fix_factor_code 三层契约（基类 None / Mock 有效代码 / OpenAI 提取代码）
    - OpenAIClient._extract_python_code 围栏/纯文本/空响应
    - BootstrappingChain.bootstrap 编译失败 → 规则修复失败 → LLM 修复兜底 → is_executable=True

版本: v1.0.0（与 FTS 同步）
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.factor_program import fix_factor_code, validate_factor_code  # noqa: E402
from fts.factor_engine.meta_loop import BootstrappingChain  # noqa: E402
from fts.llm import LLMClient, MockLLMClient, OpenAIClient  # noqa: E402


# ─── LLMClient.fix_factor_code 契约 ───────────────────────


class TestLLMCodeFixInterface:
    """fix_factor_code 三层契约。"""

    def test_base_client_returns_none(self):
        """基类默认不支持 → None。"""
        class _ConcreteBase(LLMClient):
            """最小可实例化子类（补齐抽象 complete）。"""

            def complete(self, prompt: str, max_tokens: int = 4000) -> tuple[str, int]:
                return "", 0

        assert _ConcreteBase().fix_factor_code("code", "err", "t") is None

    def test_mock_client_returns_valid_code(self):
        """Mock 返回可通过语法验证的代码。"""
        client = MockLLMClient()
        fixed = client.fix_factor_code(
            "def factor_program(data, params):\n    return x @@@\n",
            "语法错误: invalid syntax",
            "t",
        )
        assert fixed is not None
        ok, _ = validate_factor_code(fixed)
        assert ok, "Mock 修复代码应通过语法验证"

    def test_extract_python_code_fences(self):
        """从 ```python ``` 围栏提取代码。"""
        raw = "```python\ndef factor_program(data, params):\n    return 1\n```"
        assert OpenAIClient._extract_python_code(raw) == "def factor_program(data, params):\n    return 1"

    def test_extract_python_code_plain(self):
        """无围栏时原样返回（去首尾空白）。"""
        code = "def factor_program(data, params):\n    return 1\n"
        assert OpenAIClient._extract_python_code(code) == code.strip()

    def test_extract_python_code_empty(self):
        """空/纯空白响应 → None。"""
        assert OpenAIClient._extract_python_code("   ") is None
        assert OpenAIClient._extract_python_code("") is None

    def test_openai_code_fix_prompt_shape(self):
        """代码修复 prompt 含错误与代码。"""
        client = OpenAIClient()
        prompt = client._build_code_fix_prompt("code", "invalid syntax", "t")
        assert "invalid syntax" in prompt
        assert "code" in prompt
        assert "factor_program" in prompt


# ─── bootstrap 编译失败 → LLM 修复集成 ────────────────────


class TestBootstrapLLMCodeFix:
    """BootstrappingChain.bootstrap 编译失败 LLM 修复兜底。"""

    def test_rule_fix_fails_precondition(self):
        """前提: 本测试用例的破损代码规则修复必然失败（@@@ 无法修复）。"""
        code = "def factor_program(data, params):\n    return x @@@\n"
        fixed, _ = fix_factor_code(code, "语法错误: invalid syntax")
        assert not fixed

    def test_bootstrap_llm_fix_integration(self):
        """LLM 返回破损代码候选，规则修复失败后经 LLM 修复 → is_executable=True。"""
        llm = MagicMock()
        llm.bootstrap_factors.return_value = [
            {
                "name": "llm_fix_factor",
                "code": "def factor_program(data, params):\n    return x @@@\n",
                "params": {},
                "signature": {
                    "input_fields": ["close"],
                    "output_type": "signal",
                    "frequency": "daily",
                    "lookback": 1,
                },
                "economic_logic": {
                    "theory": 4,
                    "behavioral": 4,
                    "microstructure": 4,
                    "institutional": 4,
                    "narrative": "足够长度的经济逻辑论证内容，满足验证长度要求。",
                },
                "source": "l1_bootstrapping",
                "parent_topic": "test",
            }
        ]
        llm.fix_factor_code.return_value = (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    return np.zeros(len(data['close']))\n"
        )
        chain = BootstrappingChain(llm_client=llm, extractor_pipeline=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=1,
            seed_pool=None,
            trace_id="t",
        )
        assert candidates, "应有候选"
        cand = candidates[0]
        assert cand["is_executable"] is True, "LLM 修复后应可执行"
        assert any("LLM 修复成功" in r for r in cand.get("failure_reasons", []))
        assert "@@@" not in cand["code"]

    def test_bootstrap_llm_fix_still_invalid(self):
        """LLM 修复返回仍无效代码 → is_executable=False（不采纳）。"""
        llm = MagicMock()
        llm.bootstrap_factors.return_value = [
            {
                "name": "llm_fix_fail",
                "code": "def factor_program(data, params):\n    return x @@@\n",
                "params": {},
                "signature": {
                    "input_fields": ["close"],
                    "output_type": "signal",
                    "frequency": "daily",
                    "lookback": 1,
                },
                "economic_logic": {
                    "theory": 4,
                    "behavioral": 4,
                    "microstructure": 4,
                    "institutional": 4,
                    "narrative": "足够长度的经济逻辑论证内容，满足验证长度要求。",
                },
                "source": "l1_bootstrapping",
                "parent_topic": "test",
            }
        ]
        llm.fix_factor_code.return_value = "def factor_program(data, params):\n    return @@@ still broken\n"
        chain = BootstrappingChain(llm_client=llm, extractor_pipeline=None)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=1,
            seed_pool=None,
            trace_id="t",
        )
        assert candidates
        assert candidates[0]["is_executable"] is False
        assert any("编译失败" in r for r in candidates[0].get("failure_reasons", []))

    def test_bootstrap_no_llm_code_fix_interface(self):
        """LLM 无 fix_factor_code 接口 → 编译失败保持原样（_try_llm_code_fix 返回 None）。"""
        chain = BootstrappingChain(llm_client=object(), extractor_pipeline=None)  # 无 fix_factor_code 属性
        assert chain._try_llm_code_fix("code", "err", "t") is None

    def test_bootstrap_llm_code_fix_exception(self):
        """LLM fix_factor_code 抛异常 → 返回 None（不阻断）。"""
        llm = MagicMock()
        llm.fix_factor_code.side_effect = RuntimeError("LLM down")
        chain = BootstrappingChain(llm_client=llm, extractor_pipeline=None)
        assert chain._try_llm_code_fix("code", "err", "t") is None
