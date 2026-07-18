"""tests/test_llm.py — FTS LLM 客户端全面测试。

HARNESS §测试随重构: 测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import builtins
import json
import os
import types
from unittest.mock import MagicMock, patch

import pytest

from fts.llm import (
    AnthropicClient,
    LLMClient,
    LLMError,
    MockLLMClient,
    OpenAIClient,
    get_llm_client,
)


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
        text = "前置文字\n```json\n{\"key\": \"value\"}\n```\n后置文字"
        client = _make_mock_client(text)
        result = client.generate_json("test")
        assert result == {"key": "value"}

    def test_plain_code_block(self):
        """普通 ``` 代码块被正确提取和解析。"""
        text = "输出：\n```\n{\"key\": \"value\"}\n```\n结束。"
        client = _make_mock_client(text)
        result = client.generate_json("test")
        assert result == {"key": "value"}

    def test_json_code_block_preferred_over_plain(self):
        """同时存在 ```json 和普通 ``` 时优先尝试 ```json。"""
        text = "```json\n{\"json_only\": true}\n```\n```\n{\"plain\": true}\n```"
        client = _make_mock_client(text)
        result = client.generate_json("test")
        assert result == {"json_only": True}

    def test_non_json_response_raises_llm_error(self):
        """非 JSON 响应抛出 LLMError。"""
        client = _make_mock_client("这不是合法的 JSON 内容")
        with pytest.raises(LLMError, match="不是合法 JSON"):
            client.generate_json("test")

    def test_code_block_with_invalid_content_raises(self):
        """代码块内包含非 JSON 内容时抛出 JSONDecodeError（代码未包装该路径）。"""
        text = "```\nnot valid json at all\n```"
        client = _make_mock_client(text)
        with pytest.raises(json.JSONDecodeError):
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
        with patch.dict(os.environ, {}, clear=True):
            client = get_llm_client()
            assert isinstance(client, MockLLMClient)

    def test_backend_mock_returns_mock(self):
        """backend='mock' 显式指定时返回 MockLLMClient。"""
        with patch.dict(os.environ, {}, clear=True):
            client = get_llm_client(backend="mock")
            assert isinstance(client, MockLLMClient)

    def test_openai_api_key_returns_openai_client(self):
        """OPENAI_API_KEY 存在时返回 OpenAIClient。"""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-key"}, clear=True):
            client = get_llm_client()
            assert isinstance(client, OpenAIClient)

    def test_anthropic_api_key_returns_anthropic_client(self):
        """ANTHROPIC_API_KEY 存在时返回 AnthropicClient。"""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            client = get_llm_client()
            assert isinstance(client, AnthropicClient)

    def test_openai_key_preferred_over_anthropic(self):
        """两者都存在时优先返回 OpenAIClient。"""
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-test-key",
            "ANTHROPIC_API_KEY": "sk-ant-test",
        }, clear=True):
            client = get_llm_client()
            assert isinstance(client, OpenAIClient)

    def test_backend_openai_without_key_returns_openai(self):
        """backend='openai' 且无 API Key 时仍返回 OpenAIClient。"""
        with patch.dict(os.environ, {}, clear=True):
            client = get_llm_client(backend="openai")
            assert isinstance(client, OpenAIClient)

    def test_backend_anthropic_without_key_returns_anthropic(self):
        """backend='anthropic' 且无 API Key 时仍返回 AnthropicClient。"""
        with patch.dict(os.environ, {}, clear=True):
            client = get_llm_client(backend="anthropic")
            assert isinstance(client, AnthropicClient)

    def test_fts_llm_backend_env_var(self):
        """FTS_LLM_BACKEND 环境变量生效。"""
        with patch.dict(os.environ, {"FTS_LLM_BACKEND": "mock"}, clear=True):
            client = get_llm_client()
            assert isinstance(client, MockLLMClient)
