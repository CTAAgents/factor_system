"""
fts.llm — FTS LLM 客户端集成

提供统一的 LLM 调用接口，替换 MockLLMClient。
支持 OpenAI / Anthropic 两种后端，通过环境变量配置。

HARNESS §trace_id 全链路: 所有 LLM 调用携带 trace_id。
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── LLM 错误 ─────────────────────────────────────────────

class LLMError(RuntimeError):
    """LLM 调用失败。"""
    pass


# ─── 调用记录 ─────────────────────────────────────────────

@dataclass
class LLMCallRecord:
    """单次 LLM 调用的记录（用于审计和 token 统计）。"""
    prompt: str = ""
    response: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None
    trace_id: str = ""

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


# ─── 抽象 LLM 客户端 ──────────────────────────────────────

class LLMClient(ABC):
    """LLM 客户端抽象基类。"""

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 4000) -> tuple[str, int]:
        """文本补全。

        Args:
            prompt: 输入提示
            max_tokens: 最大输出 token 数

        Returns:
            (response_text, tokens_used)
        """
        ...

    def generate_json(self, prompt: str, max_tokens: int = 4000) -> dict:
        """生成 JSON 响应（解析 response 为 dict）。"""
        text, _ = self.complete(prompt, max_tokens=max_tokens)
        # 尝试提取 JSON 块
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试从 markdown 代码块提取
        if "```json" in text:
            block = text.split("```json")[1].split("```")[0].strip()
            return json.loads(block)
        if "```" in text:
            block = text.split("```")[1].split("```")[0].strip()
            return json.loads(block)
        raise LLMError(f"LLM 响应不是合法 JSON: {text[:200]}...")


# ─── OpenAI 客户端 ────────────────────────────────────────

class OpenAIClient(LLMClient):
    """OpenAI API 客户端。

    需要环境变量: OPENAI_API_KEY
    可选: OPENAI_BASE_URL, OPENAI_MODEL (默认 gpt-4o)
    """

    def __init__(self, model: str = "", api_key: str = "",
                 base_url: str = "", max_retries: int = 2):
        self._model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self._api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._base_url = base_url or os.getenv("OPENAI_BASE_URL", "")
        self._max_retries = max_retries
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
            kwargs = {}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(api_key=self._api_key, **kwargs)
            return self._client
        except ImportError:
            raise LLMError("openai 未安装。请执行: pip install fts[llm]")
        except Exception as e:
            raise LLMError(f"OpenAI 客户端初始化失败: {e}")

    def complete(self, prompt: str, max_tokens: int = 4000) -> tuple[str, int]:
        client = self._ensure_client()
        for attempt in range(self._max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                )
                text = resp.choices[0].message.content or ""
                tokens = resp.usage.total_tokens if resp.usage else 0
                return text, tokens
            except Exception as e:
                if attempt < self._max_retries:
                    logger.warning(f"OpenAI 调用失败 (重试 {attempt+1}): {e}")
                    continue
                raise LLMError(f"OpenAI 调用失败: {e}")


# ─── Anthropic 客户端 ─────────────────────────────────────

class AnthropicClient(LLMClient):
    """Anthropic Claude API 客户端。

    需要环境变量: ANTHROPIC_API_KEY
    可选: ANTHROPIC_MODEL (默认 claude-sonnet-4-20250514)
    """

    def __init__(self, model: str = "", api_key: str = "", max_retries: int = 2):
        self._model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._max_retries = max_retries
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self._api_key)
            return self._client
        except ImportError:
            raise LLMError("anthropic 未安装。请执行: pip install fts[llm]")
        except Exception as e:
            raise LLMError(f"Anthropic 客户端初始化失败: {e}")

    def complete(self, prompt: str, max_tokens: int = 4000) -> tuple[str, int]:
        client = self._ensure_client()
        for attempt in range(self._max_retries + 1):
            try:
                resp = client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.content[0].text if resp.content else ""
                tokens = (resp.usage.input_tokens + resp.usage.output_tokens) if resp.usage else 0
                return text, tokens
            except Exception as e:
                if attempt < self._max_retries:
                    logger.warning(f"Anthropic 调用失败 (重试 {attempt+1}): {e}")
                    continue
                raise LLMError(f"Anthropic 调用失败: {e}")


# ─── 模拟客户端（开发/测试用）────────────────────────────────

class MockLLMClient(LLMClient):
    """模拟 LLM 客户端 — 用于开发和测试。

    不调用真实 API，返回预设响应。
    """

    def __init__(self, responses: Optional[list[str]] = None):
        self._responses = responses or []
        self._call_count = 0

    def complete(self, prompt: str, max_tokens: int = 4000) -> tuple[str, int]:
        idx = self._call_count
        self._call_count += 1
        if idx < len(self._responses):
            return self._responses[idx], 0
        # 默认响应：返回 JSON 格式的模拟因子演化结果
        default_response = json.dumps({
            "mutation_type": "macro_logic",
            "mutation_summary": f"代 {1} mock 演化",
            "code_modification": "window_plus_5",
            "economic_logic_modification": {
                "theory": 4,
                "behavioral": 3,
                "microstructure": 3,
                "institutional": 5,
                "narrative": "模拟演化（测试用）",
            },
            "lessons_referenced": [],
        })
        return default_response, 200


# ─── 工厂函数 ─────────────────────────────────────────────

def get_llm_client(backend: str = "") -> LLMClient:
    """获取 LLM 客户端实例。

    Args:
        backend: "openai" / "anthropic" / "mock"（空=自动检测）

    自动检测顺序:
        1. OPENAI_API_KEY → OpenAI
        2. ANTHROPIC_API_KEY → Anthropic
        3. 两者均无 → MockLLMClient
    """
    backend = backend or os.getenv("FTS_LLM_BACKEND", "")

    if backend == "openai" or (not backend and os.getenv("OPENAI_API_KEY")):
        return OpenAIClient()
    if backend == "anthropic" or (not backend and os.getenv("ANTHROPIC_API_KEY")):
        return AnthropicClient()
    logger.info("未检测到 LLM API Key，使用 MockLLMClient")
    return MockLLMClient()
