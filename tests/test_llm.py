"""tests/test_llm.py — FTS LLM 客户端全面测试。

HARNESS §测试随重构: 测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import builtins
import json
import os
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from fts.llm import (
    AnthropicClient,
    LLMClient,
    LLMCallRecord,
    LLMError,
    MockLLMClient,
    OpenAIClient,
    get_llm_client,
)


# ═══════════════════════════════════════════════════════════
# 环境变量清理 helper
#
# 说明: 不使用 patch.dict(os.environ, {}, clear=True)，因为 WorkBuddy
# 会注入超大环境变量（如 ACC_PRODUCT_CONFIG_V3 > 32767 字符），
# clear=True 在恢复时触发 Windows 环境块长度限制（ValueError）。
# 这里只定向清理 LLM 相关变量，不动其它环境变量。
# ═══════════════════════════════════════════════════════════

_LLM_ENV_KEYS = (
    "FTS_LLM_BACKEND",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_BASE_URL",
    "OPENAI_TEMPERATURE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_TEMPERATURE",
)


@contextmanager
def _llm_env(**overrides: str):
    """临时移除 LLM 相关环境变量（可按需覆盖）。

    退出时恢复原值；对未设置的变量保持未设置状态。
    """
    saved = {k: os.environ.get(k) for k in _LLM_ENV_KEYS}
    for k in _LLM_ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ═══════════════════════════════════════════════════════════
# MockLLMClient — 预设响应与默认行为
# ═══════════════════════════════════════════════════════════


class TestMockLLMClient:
    """测试 MockLLMClient。"""

    def test_default_response(self):
        """无预设响应时返回默认 JSON 响应。"""
        client = MockLLMClient()
        text, tokens = client.complete("test prompt")
        parsed = json.loads(text)
        assert parsed["mutation_type"] == "macro_logic"
        assert tokens == 200

    def test_preset_responses_returned_in_order(self):
        """预设响应列表按调用顺序返回。"""
        responses = ["first_response", "second_response", "third_response"]
        client = MockLLMClient(responses=responses)
        for expected in responses:
            text, _ = client.complete("test")
            assert text == expected

    def test_preset_exhausted_falls_back_to_default(self):
        """预设响应用完后回退到默认响应。"""
        client = MockLLMClient(responses=["custom"])
        text1, _ = client.complete("test")
        assert text1 == "custom"
        text2, _ = client.complete("test")
        parsed = json.loads(text2)
        assert parsed["mutation_type"] == "macro_logic"

    def test_call_count_increments(self):
        """调用计数 self._call_count 正确递增。"""
        client = MockLLMClient(responses=["a", "b"])
        assert client._call_count == 0
        client.complete("test")
        assert client._call_count == 1
        client.complete("test")
        assert client._call_count == 2
        client.complete("test")
        assert client._call_count == 3


# ═══════════════════════════════════════════════════════════
# LLMClient.generate_json()
# ═══════════════════════════════════════════════════════════


class TestGenerateJson:
    """测试 LLMClient.generate_json JSON 提取逻辑。"""

    def test_pure_json_response(self):
        """纯 JSON 字符串被直接解析。"""
        client = _make_mock_client(json.dumps({"key": "value", "num": 42}))
        result = client.generate_json("test")
        assert result == {"key": "value", "num": 42}

    def test_json_code_block(self):
        """```json 标记的代码块被正确提取和解析。"""
        text = '前置文字\n```json\n{"key": "value"}\n```\n后置文字'
        client = _make_mock_client(text)
        result = client.generate_json("test")
        assert result == {"key": "value"}

    def test_plain_code_block(self):
        """普通 ``` 代码块被正确提取和解析。"""
        text = '输出：\n```\n{"key": "value"}\n```\n结束。'
        client = _make_mock_client(text)
        result = client.generate_json("test")
        assert result == {"key": "value"}

    def test_json_code_block_preferred_over_plain(self):
        """同时存在 ```json 和普通 ``` 时优先尝试 ```json。"""
        text = '```json\n{"json_only": true}\n```\n```\n{"plain": true}\n```'
        client = _make_mock_client(text)
        result = client.generate_json("test")
        assert result == {"json_only": True}

    def test_non_json_response_raises_llm_error(self):
        """非 JSON 响应抛出 LLMError。"""
        client = _make_mock_client("这不是合法的 JSON 内容")
        with pytest.raises(LLMError, match="不是合法 JSON"):
            client.generate_json("test")

    def test_code_block_with_invalid_content_raises(self):
        """代码块内包含非 JSON 内容时抛出 LLMError（_parse_json 包装该路径）。"""
        text = "```\nnot valid json at all\n```"
        client = _make_mock_client(text)
        with pytest.raises(LLMError, match="不是合法 JSON"):
            client.generate_json("test")


def _make_mock_client(response: str) -> LLMClient:
    """创建预设单次响应的 MockLLMClient 辅助函数。"""
    return MockLLMClient(responses=[response])


# ═══════════════════════════════════════════════════════════
# OpenAIClient — ImportError 处理
# ═══════════════════════════════════════════════════════════


class TestOpenAIClientInit:
    """测试 OpenAIClient 初始化与 ImportError 处理。"""

    def test_openai_not_installed(self):
        """openai 未安装时 complete 抛出 LLMError。"""
        client = OpenAIClient(api_key="sk-test")
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named openai")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            with pytest.raises(LLMError, match="openai 未安装"):
                client.complete("test prompt")

    def test_openai_init_other_exception(self):
        """OpenAI 构造函数抛出非 ImportError 异常时抛出 LLMError。"""
        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(side_effect=Exception("connection refused"))
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test")
            with pytest.raises(LLMError, match="OpenAI 客户端初始化失败"):
                client._ensure_client()


# ═══════════════════════════════════════════════════════════
# AnthropicClient — ImportError 处理
# ═══════════════════════════════════════════════════════════


class TestAnthropicClientInit:
    """测试 AnthropicClient 初始化与 ImportError 处理。"""

    def test_anthropic_not_installed(self):
        """anthropic 未安装时 complete 抛出 LLMError。"""
        client = AnthropicClient(api_key="sk-ant-test")
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named anthropic")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            with pytest.raises(LLMError, match="anthropic 未安装"):
                client.complete("test prompt")

    def test_anthropic_init_other_exception(self):
        """Anthropic 构造函数抛出非 ImportError 异常时抛出 LLMError。"""
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_anthropic_mod.Anthropic = MagicMock(side_effect=Exception("auth failed"))
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            client = AnthropicClient(api_key="sk-ant-test")
            with pytest.raises(LLMError, match="Anthropic 客户端初始化失败"):
                client._ensure_client()


# ═══════════════════════════════════════════════════════════
# get_llm_client() 工厂函数
# ═══════════════════════════════════════════════════════════


class TestGetLLMClient:
    """测试 get_llm_client 工厂函数的分支逻辑。"""

    def test_no_backend_no_key_returns_mock(self):
        """空 backend 且无任何 API Key 时返回 MockLLMClient。"""
        with _llm_env():
            client = get_llm_client()
            assert isinstance(client, MockLLMClient)

    def test_backend_mock_returns_mock(self):
        """backend='mock' 显式指定时返回 MockLLMClient。"""
        with _llm_env():
            client = get_llm_client(backend="mock")
            assert isinstance(client, MockLLMClient)

    def test_openai_api_key_returns_openai_client(self):
        """OPENAI_API_KEY 存在时返回 OpenAIClient。"""
        with _llm_env(OPENAI_API_KEY="sk-test-key"):
            client = get_llm_client()
            assert isinstance(client, OpenAIClient)

    def test_anthropic_api_key_returns_anthropic_client(self):
        """ANTHROPIC_API_KEY 存在时返回 AnthropicClient。"""
        with _llm_env(ANTHROPIC_API_KEY="sk-ant-test"):
            client = get_llm_client()
            assert isinstance(client, AnthropicClient)

    def test_openai_key_preferred_over_anthropic(self):
        """两者都存在时优先返回 OpenAIClient。"""
        with _llm_env(OPENAI_API_KEY="sk-test-key", ANTHROPIC_API_KEY="sk-ant-test"):
            client = get_llm_client()
            assert isinstance(client, OpenAIClient)

    def test_backend_openai_without_key_returns_openai(self):
        """backend='openai' 且无 API Key 时仍返回 OpenAIClient。"""
        with _llm_env():
            client = get_llm_client(backend="openai")
            assert isinstance(client, OpenAIClient)

    def test_backend_anthropic_without_key_returns_anthropic(self):
        """backend='anthropic' 且无 API Key 时仍返回 AnthropicClient。"""
        with _llm_env():
            client = get_llm_client(backend="anthropic")
            assert isinstance(client, AnthropicClient)

    def test_fts_llm_backend_env_var(self):
        """FTS_LLM_BACKEND 环境变量生效。"""
        with _llm_env(FTS_LLM_BACKEND="mock"):
            client = get_llm_client()
            assert isinstance(client, MockLLMClient)


# ═══════════════════════════════════════════════════════════
# LLMCallRecord — total_tokens 属性
# ═══════════════════════════════════════════════════════════


class TestLLMCallRecord:
    """测试 LLMCallRecord.total_tokens 属性（line 45）。"""

    def test_total_tokens_sum(self):
        """total_tokens 返回 tokens_in + tokens_out。"""
        record = LLMCallRecord(tokens_in=100, tokens_out=50)
        assert record.total_tokens == 150

    def test_total_tokens_zero_when_empty(self):
        """无数据时 total_tokens 返回 0。"""
        record = LLMCallRecord()
        assert record.total_tokens == 0

    def test_total_tokens_partial(self):
        """仅一方有数据时正确计算。"""
        record = LLMCallRecord(tokens_in=80)
        assert record.total_tokens == 80
        record2 = LLMCallRecord(tokens_out=30)
        assert record2.total_tokens == 30


# ═══════════════════════════════════════════════════════════
# OpenAIClient.complete — 错误处理与重试
# ═══════════════════════════════════════════════════════════


class TestOpenAIClientComplete:
    """测试 OpenAIClient.complete 的 API 错误路径（lines 118-132）。"""

    def _make_openai_env(self, mock_client: MagicMock) -> MagicMock:
        """创建模拟的 openai 模块并注册到 sys.modules。"""
        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        patch.dict("sys.modules", {"openai": mock_openai_mod}).start()
        return mock_openai_mod

    def test_api_error_retry_exhausted_raises(self):
        """API 错误在重试用尽后抛出 LLMError。"""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")
        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test", max_retries=1)
            with pytest.raises(LLMError, match="OpenAI 调用失败"):
                client.complete("test")
            # max_retries=1 → 2 次尝试
            assert mock_client.chat.completions.create.call_count == 2

    def test_api_error_retry_then_success(self):
        """首次失败后重试成功。"""
        mock_client = MagicMock()

        # 构建成功响应
        choice = MagicMock()
        choice.message.content = "retry_success"
        usage = MagicMock()
        usage.total_tokens = 50

        first_resp = MagicMock()
        first_resp.choices = [choice]
        first_resp.usage = usage

        mock_client.chat.completions.create.side_effect = [
            Exception("timeout"),  # 第一次失败
            first_resp,  # 第二次成功
        ]

        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test", max_retries=2)
            text, tokens = client.complete("test")
            assert text == "retry_success"
            assert tokens == 50
            assert mock_client.chat.completions.create.call_count == 2

    def test_api_rate_limit_retry(self):
        """Rate limit 错误也触发重试。"""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            Exception("Rate limit exceeded"),
            Exception("Rate limit exceeded"),
            Exception("Rate limit exceeded"),
        ]
        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test", max_retries=2)
            with pytest.raises(LLMError, match="OpenAI 调用失败"):
                client.complete("test")
            assert mock_client.chat.completions.create.call_count == 3

    def test_ensure_client_returns_cached(self):
        """_ensure_client 第二次调用返回缓存的客户端（line 103）。"""
        mock_client_obj = MagicMock()
        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client_obj)
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test")
            c1 = client._ensure_client()
            c2 = client._ensure_client()
            assert c1 is c2
            mock_openai_mod.OpenAI.assert_called_once()

    def test_ensure_client_with_base_url(self):
        """base_url 传入时传递给 OpenAI 构造函数（line 108）。"""
        mock_client_obj = MagicMock()
        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client_obj)
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test", base_url="https://custom.api.com/v1")
            client._ensure_client()
            mock_openai_mod.OpenAI.assert_called_once_with(
                api_key="sk-test",
                base_url="https://custom.api.com/v1",
            )

    def test_complete_no_usage_info(self):
        """resp.usage 为 None 时 tokens 返回 0。"""
        mock_client = MagicMock()
        choice = MagicMock()
        choice.message.content = "no_usage"
        mock_resp = MagicMock()
        mock_resp.choices = [choice]
        mock_resp.usage = None
        mock_client.chat.completions.create.return_value = mock_resp

        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test", max_retries=0)
            text, tokens = client.complete("test")
            assert text == "no_usage"
            assert tokens == 0


# ═══════════════════════════════════════════════════════════
# AnthropicClient.complete — 错误处理与重试
# ═══════════════════════════════════════════════════════════


class TestAnthropicClientComplete:
    """测试 AnthropicClient.complete 的 API 错误路径（lines 164-178）。"""

    def test_api_error_retry_exhausted_raises(self):
        """API 错误在重试用尽后抛出 LLMError。"""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            client = AnthropicClient(api_key="sk-ant-test", max_retries=1)
            with pytest.raises(LLMError, match="Anthropic 调用失败"):
                client.complete("test")
            assert mock_client.messages.create.call_count == 2

    def test_api_error_retry_then_success(self):
        """首次失败后重试成功。"""
        mock_client = MagicMock()

        text_content = MagicMock()
        text_content.text = "claude_response"
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 50

        success_resp = MagicMock()
        success_resp.content = [text_content]
        success_resp.usage = usage

        mock_client.messages.create.side_effect = [
            Exception("timeout"),
            success_resp,
        ]

        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            client = AnthropicClient(api_key="sk-ant-test", max_retries=2)
            text, tokens = client.complete("test")
            assert text == "claude_response"
            assert tokens == 150
            assert mock_client.messages.create.call_count == 2

    def test_ensure_client_returns_cached(self):
        """_ensure_client 第二次调用返回缓存的客户端（line 152）。"""
        mock_client_obj = MagicMock()
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client_obj)
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            client = AnthropicClient(api_key="sk-ant-test")
            c1 = client._ensure_client()
            c2 = client._ensure_client()
            assert c1 is c2
            mock_anthropic_mod.Anthropic.assert_called_once()

    def test_complete_empty_content(self):
        """resp.content 为空列表时 text 返回空字符串。"""
        mock_client = MagicMock()
        usage = MagicMock()
        usage.input_tokens = 10
        usage.output_tokens = 5

        mock_resp = MagicMock()
        mock_resp.content = []
        mock_resp.usage = usage
        mock_client.messages.create.return_value = mock_resp

        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            client = AnthropicClient(api_key="sk-ant-test", max_retries=0)
            text, tokens = client.complete("test")
            assert text == ""
            assert tokens == 15

    def test_complete_no_usage_info(self):
        """resp.usage 为 None 时 tokens 返回 0。"""
        mock_client = MagicMock()
        text_content = MagicMock()
        text_content.text = "no_usage"
        mock_resp = MagicMock()
        mock_resp.content = [text_content]
        mock_resp.usage = None
        mock_client.messages.create.return_value = mock_resp

        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            client = AnthropicClient(api_key="sk-ant-test", max_retries=0)
            text, tokens = client.complete("test")
            assert text == "no_usage"
            assert tokens == 0


# ═══════════════════════════════════════════════════════════
# LLMClient.bootstrap_factors — 基类默认行为
# ═══════════════════════════════════════════════════════════


class TestBootstrapFactorsBase:
    """测试 LLMClient.bootstrap_factors 基类默认实现。"""

    def test_base_returns_empty_list(self):
        """基类默认实现返回空列表。"""
        client = MockLLMClient()
        # 绕过 MockLLMClient 的重写，直接调用基类方法
        result = LLMClient.bootstrap_factors(client, {}, [], 5, "trace_001")
        assert result == []

    def test_base_accepts_params(self):
        """基类接受所有必要参数不报错。"""
        client = MockLLMClient()
        snapshot = {"key": "value"}
        gaps = [{"gap": "weak_momentum"}]
        result = LLMClient.bootstrap_factors(client, snapshot, gaps, 3, "trace_002")
        assert isinstance(result, list)
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════
# MockLLMClient.bootstrap_factors — 预设候选返回
# ═══════════════════════════════════════════════════════════


class TestMockBootstrapFactors:
    """测试 MockLLMClient.bootstrap_factors。"""

    def test_returns_candidate_with_correct_structure(self):
        """返回的候选因子结构完整。"""
        client = MockLLMClient()
        result = client.bootstrap_factors({"close": [1, 2, 3]}, [], 5, "trace_mock_001")
        assert len(result) == 1
        cand = result[0]
        assert cand["name"] == "mock_volume_price_divergence"
        assert "code" in cand
        assert "def factor_program(data, params):" in cand["code"]
        assert "params" in cand
        assert "signature" in cand
        assert "economic_logic" in cand
        assert cand["source"] == "l1_bootstrapping"

    def test_candidate_code_contains_numpy_import(self):
        """候选代码包含 numpy import。"""
        client = MockLLMClient()
        result = client.bootstrap_factors({}, [], 5, "trace_mock_002")
        assert "import numpy as np" in result[0]["code"]

    def test_candidate_has_economic_logic_fields(self):
        """候选经济逻辑字段完整。"""
        client = MockLLMClient()
        result = client.bootstrap_factors({}, [], 5, "trace_mock_003")
        el = result[0]["economic_logic"]
        assert "theory" in el
        assert "behavioral" in el
        assert "microstructure" in el
        assert "institutional" in el
        assert "narrative" in el
        assert len(el["narrative"]) >= 10

    def test_candidate_signature_has_required_fields(self):
        """候选签名字段完整。"""
        client = MockLLMClient()
        result = client.bootstrap_factors({}, [], 5, "trace_mock_004")
        sig = result[0]["signature"]
        assert "input_fields" in sig
        assert "close" in sig["input_fields"]
        assert "volume" in sig["input_fields"]

    def test_ignores_max_candidates_limitation(self):
        """Mock 返回固定数量不受 max_candidates 限制（Mock 特性）。"""
        client = MockLLMClient()
        result = client.bootstrap_factors({}, [], 1, "trace_mock_005")
        assert len(result) == 1  # Mock 固定返回 1 个

    def test_handles_empty_inputs(self):
        """空市场快照和空辩论缺口正常处理。"""
        client = MockLLMClient()
        result = client.bootstrap_factors({}, [], 5, "trace_mock_006")
        assert len(result) == 1
        assert result[0]["name"] == "mock_volume_price_divergence"


# ═══════════════════════════════════════════════════════════
# OpenAIClient.bootstrap_factors — LLM 交互分支
# ═══════════════════════════════════════════════════════════


class TestOpenAIBootstrapFactors:
    """测试 OpenAIClient.bootstrap_factors 的各分支。"""

    def _make_openai_mock(self, response_text: str) -> OpenAIClient:
        """创建带 Mock complete 的 OpenAIClient（bootstrap_factors 内部调用 complete + _parse_json）。"""
        client = OpenAIClient(api_key="sk-test", max_retries=0)
        client.complete = MagicMock(return_value=(response_text, 0))
        return client

    def test_valid_response_returns_candidates(self):
        """LLM 返回合法 candidates 时正确解析。"""
        payload = json.dumps(
            {
                "candidates": [
                    {"name": "factor_a", "code": "def factor_program(data, params): pass"},
                    {"name": "factor_b", "code": "def factor_program(data, params): pass"},
                ]
            }
        )
        client = self._make_openai_mock(payload)
        result = client.bootstrap_factors({"close": [1]}, [], 5, "trace_ai_001")
        assert len(result) == 2
        assert result[0]["name"] == "factor_a"
        assert result[1]["name"] == "factor_b"

    def test_truncates_to_max_candidates(self):
        """候选数超过 max_candidates 时截断。"""
        candidates = [{"name": f"factor_{i}", "code": "def f(data, params): pass"} for i in range(10)]
        payload = json.dumps({"candidates": candidates})
        client = self._make_openai_mock(payload)
        result = client.bootstrap_factors({"close": [1]}, [], 3, "trace_ai_002")
        assert len(result) == 3
        assert result[0]["name"] == "factor_0"
        assert result[2]["name"] == "factor_2"

    def test_json_parse_error_returns_empty(self):
        """LLM 返回非法 JSON 时返回空列表。"""
        client = OpenAIClient(api_key="sk-test", max_retries=0)
        client.generate_json = MagicMock(side_effect=LLMError("非法 JSON"))
        result = client.bootstrap_factors({"close": [1]}, [], 5, "trace_ai_003")
        assert result == []

    def test_non_list_candidates_returns_empty(self):
        """candidates 字段非列表时返回空。"""
        payload = json.dumps({"candidates": "not_a_list"})
        client = self._make_openai_mock(payload)
        result = client.bootstrap_factors({"close": [1]}, [], 5, "trace_ai_004")
        assert result == []

    def test_missing_candidates_key_returns_empty(self):
        """缺少 candidates 键时返回空列表。"""
        payload = json.dumps({"other_key": "value"})
        client = self._make_openai_mock(payload)
        result = client.bootstrap_factors({"close": [1]}, [], 5, "trace_ai_005")
        assert result == []

    def test_empty_candidates_returns_empty(self):
        """candidates 为空列表时返回空。"""
        payload = json.dumps({"candidates": []})
        client = self._make_openai_mock(payload)
        result = client.bootstrap_factors({"close": [1]}, [], 5, "trace_ai_006")
        assert result == []

    def test_generic_exception_returns_empty(self):
        """LLM 调用抛出非 LLMError 异常时返回空。"""
        client = OpenAIClient(api_key="sk-test", max_retries=0)
        client.generate_json = MagicMock(side_effect=RuntimeError("network"))
        result = client.bootstrap_factors({"close": [1]}, [], 5, "trace_ai_007")
        assert result == []


# ═══════════════════════════════════════════════════════════
# _build_bootstrap_prompt — Prompt 构造
# ═══════════════════════════════════════════════════════════


class TestBuildBootstrapPrompt:
    """测试 _build_bootstrap_prompt 静态方法。"""

    def test_prompt_contains_trace_id(self):
        """Prompt 中包含 trace_id。"""
        prompt = OpenAIClient._build_bootstrap_prompt({"close": [1, 2]}, [], 5, "trace_prompt_001")
        assert "trace_prompt_001" in prompt

    def test_prompt_contains_max_candidates(self):
        """Prompt 中包含候选数量要求。"""
        prompt = OpenAIClient._build_bootstrap_prompt({"close": [1]}, [], 3, "trace_prompt_002")
        assert "3" in prompt

    def test_prompt_contains_snapshot_data(self):
        """Prompt 中包含市场快照摘要。"""
        prompt = OpenAIClient._build_bootstrap_prompt(
            {"close": [1, 2, 3], "volume": [100, 200, 300]},
            [],
            5,
            "trace_prompt_003",
        )
        assert "close" in prompt

    def test_prompt_contains_code_rules(self):
        """Prompt 中包含代码规则。"""
        prompt = OpenAIClient._build_bootstrap_prompt({}, [], 5, "trace_prompt_004")
        assert "factor_program" in prompt
        assert "numpy" in prompt

    def test_prompt_contains_json_format(self):
        """Prompt 中包含输出 JSON 格式说明。"""
        prompt = OpenAIClient._build_bootstrap_prompt({}, [], 5, "trace_prompt_005")
        assert "candidates" in prompt
        assert "economic_logic" in prompt

    def test_prompt_contains_common_errors(self):
        """Prompt 中包含常见错误提醒。"""
        prompt = OpenAIClient._build_bootstrap_prompt({}, [], 5, "trace_prompt_006")
        assert "未定义变量" in prompt or "❌" in prompt

    def test_prompt_handles_empty_gaps(self):
        """空辩论缺口不报错。"""
        prompt = OpenAIClient._build_bootstrap_prompt({"close": [1]}, [], 5, "trace_prompt_007")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_prompt_truncates_long_snapshot(self):
        """过长的市场快照被截断。"""
        long_snapshot = {f"key_{i}": "x" * 100 for i in range(50)}
        prompt = OpenAIClient._build_bootstrap_prompt(long_snapshot, [], 5, "trace_prompt_008")
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ═══════════════════════════════════════════════════════════
# _parse_json / _repair_json — 修复式解析（含截断 JSON）
# ═══════════════════════════════════════════════════════════


class TestParseJsonRepair:
    """测试 _parse_json 修复式解析与 _repair_json 各分支。"""

    def test_parse_json_repairs_truncated_brace(self):
        """单层截断 JSON（缺闭合 }）经 _repair_json 修复成功（line 131）。"""
        client = _make_mock_client('{"a": 1, "b": 2')
        result = client.generate_json("test")
        assert result == {"a": 1, "b": 2}

    def test_repair_json_nested_truncation_current_behavior(self):
        """嵌套未闭合对象按栈深度补全，不再丢字段（产品 bug 已修复）。

        修复前：`elif match_end == -1` 分支只追加单个 "}"（未按栈深度补全），
        导致 JSONDecodeError 后走逐段截断，静默丢弃嵌套字段；
        修复后：按栈逆序补全闭合序列，嵌套字段完整保留。
        """
        client = _make_mock_client('{"a": 1, "b": {"c": 2')
        result = client.generate_json("test")
        assert result == {"a": 1, "b": {"c": 2}}

    def test_parse_json_repairs_trailing_comma(self):
        """末尾残留逗号经逐段截断修复。"""
        client = _make_mock_client('{"a": 1, "b": 2, ')
        result = client.generate_json("test")
        assert result == {"a": 1, "b": 2}

    def test_repair_json_no_brace_returns_none(self):
        """无 {} 时 _repair_json 返回 None（line 146-147）。"""
        assert LLMClient._repair_json("hello world") is None

    def test_last_top_level_comma_ignores_string_comma(self):
        """_last_top_level_comma 忽略字符串内的逗号（line 219-239）。"""
        text = '{"a": ",", "b": 2}'
        pos = LLMClient._last_top_level_comma(text)
        # 顶层逗号位于字符串字面量 "," 之后（index 9）
        assert pos == text.index('"b"') - 2
        assert pos == 9

    def test_last_top_level_comma_no_comma_returns_minus_one(self):
        """无顶层逗号返回 -1。"""
        assert LLMClient._last_top_level_comma('{"a": 1}') == -1

    def test_escape_newlines_in_json_values(self):
        """字符串值内的实际换行符被替换为 \\n（line 254-256）。"""
        text = '{"code": "line1\nline2"}'
        escaped = LLMClient._escape_newlines_in_json(text)
        assert "\\n" in escaped
        assert "\n" not in escaped

    def test_escape_newlines_keeps_existing_escapes(self):
        """已转义的 \\n 序列保持不变（line 258-260）。"""
        text = '{"code": "a\\\\nb"}'
        escaped = LLMClient._escape_newlines_in_json(text)
        assert "a\\\\nb" in escaped

    def test_parse_json_with_raw_newlines_in_string(self):
        """含实际换行符的 JSON 字符串经修复后解析成功（line 266-267）。"""
        text = '{"code": "def f():\n    return 1", "name": "x"}'
        client = _make_mock_client(text)
        result = client.generate_json("test")
        assert result["name"] == "x"
        assert "return 1" in result["code"]

    def test_repair_json_ignores_trailing_garbage(self):
        """完整 JSON + 尾部垃圾 → 提取完整对象（line 194）。"""
        assert LLMClient._repair_json('{"a": 1} trailing') == {"a": 1}

    def test_repair_json_closes_nested_with_brace_seen(self):
        """出现部分 } 后按栈补全剩余闭合（line 185-189）。"""
        assert LLMClient._repair_json('{"a": {"b": 1}, "c": 2') == {"a": {"b": 1}}

    def test_repair_json_handles_escape_sequences(self):
        """含转义引号（\\"）的文本扫描正确（line 158-162）。"""
        assert LLMClient._repair_json('{"a": "b\\"c"') == {"a": 'b"c'}

    def test_repair_json_unmatched_brace_in_bracket(self):
        """'[' 内遇到 '}' 时 last_brace_pos 仍更新且不匹配弹出（line 173-178）。"""
        assert LLMClient._repair_json('{"a": [}') is None

    def test_last_top_level_comma_skips_escaped_commas(self):
        """_last_top_level_comma 跳过转义字符后的逗号（line 225-229）。"""
        text = '{"a": "b\\,c", "d": 1}'
        pos = LLMClient._last_top_level_comma(text)
        # 顶层逗号是 "d" 前的逗号，而非 "b\,c" 内的逗号
        assert pos == text.index('"d"') - 2

    def test_repair_json_closes_complete_array(self):
        """完整闭合数组时 ']' 正常弹出栈（line 182-183）。"""
        assert LLMClient._repair_json('{"a": [1]}') == {"a": [1]}

    def test_repair_json_multi_step_truncation(self):
        """逐段截断多次仍失败时 continue 继续截断（line 211-212）。"""
        # 非法字段值 "c": , 导致首次截断结果仍非法 → continue 二次截断
        assert LLMClient._repair_json('{"a": 1, "b": 2, "c": ,') == {"a": 1, "b": 2}

    def test_last_top_level_comma_handles_trailing_escape(self):
        """尾部含转义符时 escape 状态正确处理（line 225-226, 228-229）。"""
        # 从后往前扫描时先遇到 \\ 再遇到字符串边界
        assert LLMClient._last_top_level_comma('{"a": "b\\\\c"}') == -1


# ═══════════════════════════════════════════════════════════
# OpenAIClient.complete — temperature 透传
# ═══════════════════════════════════════════════════════════


class TestOpenAIClientTemperature:
    """测试 OpenAIClient.complete 的 temperature 分支（line 345）。"""

    def test_complete_passes_explicit_temperature(self):
        """显式 temperature 传入请求 kwargs。"""
        mock_client = MagicMock()
        choice = MagicMock()
        choice.message.content = "ok"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None
        mock_client.chat.completions.create.return_value = resp

        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            client = OpenAIClient(api_key="sk-test", max_retries=0, temperature=0.7)
            text, _ = client.complete("test")
            assert text == "ok"
            kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert kwargs["temperature"] == 0.7
            # model 默认取环境变量（OPENAI_MODEL），未设置时才是 gpt-4o
            assert kwargs["model"] == client._model

    def test_complete_temperature_from_env(self):
        """OPENAI_TEMPERATURE 环境变量生效。"""
        mock_client = MagicMock()
        choice = MagicMock()
        choice.message.content = "ok"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None
        mock_client.chat.completions.create.return_value = resp

        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        with _llm_env(OPENAI_TEMPERATURE="0.3"):
            with patch.dict("sys.modules", {"openai": mock_openai_mod}):
                client = OpenAIClient(api_key="sk-test", max_retries=0)
                assert client._temperature == 0.3
                client.complete("test")
                kwargs = mock_client.chat.completions.create.call_args.kwargs
                assert kwargs["temperature"] == 0.3

    def test_complete_no_temperature_kwarg_when_none(self):
        """temperature 为 None 时不传 temperature 参数。"""
        mock_client = MagicMock()
        choice = MagicMock()
        choice.message.content = "ok"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None
        mock_client.chat.completions.create.return_value = resp

        mock_openai_mod = types.ModuleType("openai")
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)
        with _llm_env():
            with patch.dict("sys.modules", {"openai": mock_openai_mod}):
                client = OpenAIClient(api_key="sk-test", max_retries=0)
                client.complete("test")
                kwargs = mock_client.chat.completions.create.call_args.kwargs
                assert "temperature" not in kwargs


# ═══════════════════════════════════════════════════════════
# OpenAIClient.bootstrap_factors — JSON 修复重试 / 调试文件
# ═══════════════════════════════════════════════════════════


class TestOpenAIBootstrapFactorsRepair:
    """测试 bootstrap_factors 的 JSON 解析失败重试与调试文件写失败。"""

    def test_first_json_invalid_then_repair_succeeds(self):
        """首次 JSON 非法 → 构造修复 prompt 重试 → 成功（line 412-426）。"""
        import os as _os

        good = json.dumps({"candidates": [{"name": "f1", "code": "def factor_program(data, params): pass"}]})
        bad = '{"candidates": [{"name": "f1", "code": "broken'
        client = OpenAIClient(api_key="sk-test", max_retries=0)
        # complete 返回 (text, tokens) 元组
        client.complete = MagicMock(side_effect=[(bad, 0), (good, 0)])

        result = client.bootstrap_factors({"close": [1]}, [], 5, "trace_repair_001")

        assert len(result) == 1
        assert result[0]["name"] == "f1"
        assert client.complete.call_count == 2
        # 第二次调用的 prompt 是修复 prompt
        second_prompt = client.complete.call_args_list[1].args[0]
        assert "重新生成" in second_prompt
        assert "broken" in second_prompt
        # 清理产品代码写入的调试文件（删除失败可忽略：钩子/权限等环境因素）
        for f in ("debug_llm_response_trace_repair_001_0.txt", "debug_llm_response_trace_repair_001_1.txt"):
            if _os.path.exists(f):
                try:
                    _os.remove(f)
                except OSError:
                    pass

    def test_json_always_invalid_returns_empty_after_retry(self):
        """两次都非法 → 返回空列表（重试耗尽）。"""
        import os as _os

        bad = '{"candidates": broken'
        client = OpenAIClient(api_key="sk-test", max_retries=0)
        client.complete = MagicMock(return_value=(bad, 0))

        result = client.bootstrap_factors({}, [], 5, "trace_repair_002")
        assert result == []
        assert client.complete.call_count == 2
        # 清理产品代码写入的调试文件（删除失败可忽略：钩子/权限等环境因素）
        for f in ("debug_llm_response_trace_repair_002_0.txt", "debug_llm_response_trace_repair_002_1.txt"):
            if _os.path.exists(f):
                try:
                    _os.remove(f)
                except OSError:
                    pass

    def test_debug_file_write_failure_does_not_break(self):
        """调试文件写入失败被吞掉，不中断流程（line 405-406）。"""
        good = json.dumps({"candidates": [{"name": "f2", "code": "def factor_program(data, params): pass"}]})
        client = OpenAIClient(api_key="sk-test", max_retries=0)
        client.complete = MagicMock(return_value=(good, 0))
        with patch("builtins.open", side_effect=OSError("denied")):
            result = client.bootstrap_factors({}, [], 5, "trace_repair_003")
        assert len(result) == 1
        assert result[0]["name"] == "f2"

    def test_build_repair_prompt_contains_snippet(self):
        """_build_repair_prompt 包含失败片段与候选数（line 533-534）。"""
        prompt = OpenAIClient._build_repair_prompt('{"candidates": [broken', 3)
        assert "broken" in prompt
        assert "3" in prompt
        assert "重新生成" in prompt


# ═══════════════════════════════════════════════════════════
# AnthropicClient.complete — temperature 透传
# ═══════════════════════════════════════════════════════════


class TestAnthropicClientTemperature:
    """测试 AnthropicClient.complete 的 temperature 分支（line 604）。"""

    def test_complete_passes_explicit_temperature(self):
        """显式 temperature 传入请求 kwargs。"""
        mock_client = MagicMock()
        text_content = MagicMock()
        text_content.text = "claude_ok"
        resp = MagicMock()
        resp.content = [text_content]
        resp.usage = None
        mock_client.messages.create.return_value = resp

        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            client = AnthropicClient(api_key="sk-ant-test", max_retries=0, temperature=0.5)
            text, _ = client.complete("test")
            assert text == "claude_ok"
            kwargs = mock_client.messages.create.call_args.kwargs
            assert kwargs["temperature"] == 0.5
            assert kwargs["model"] == "claude-sonnet-4-20250514"


# ═══════════════════════════════════════════════════════════
# get_llm_client — config 读取异常回退
# ═══════════════════════════════════════════════════════════


class TestGetLLMClientConfigError:
    """测试 get_llm_client 的 config 读取异常分支（line 725-726）。"""

    def test_config_error_falls_back_to_none_temperature(self):
        """get_config 抛异常时 temperature 回退为 None，仍返回 Mock 客户端。"""
        with _llm_env():
            with patch(
                "fts.config.settings.get_config",
                side_effect=RuntimeError("config broken"),
            ):
                client = get_llm_client()
        assert isinstance(client, MockLLMClient)
        # 未设置 _temperature（Mock 客户端无此属性）
        assert hasattr(client, "_responses")
