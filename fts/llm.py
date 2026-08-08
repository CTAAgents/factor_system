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
import re
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
        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """尝试解析 JSON 文本，含修复逻辑。

        处理策略:
          0. 用正则去除 markdown 代码块标记（```json ... ```）
          1. 直接 json.loads
          1.5 修复字符串内未转义换行符后再解析
          2. 从 markdown 代码块按标记提取
          3. 修复式解析（截断修复）
          4. 抛出 LLMError
        """
        # 0. 用正则去除 markdown 代码块标记，避免 code 字段内含特殊字符干扰
        cleaned = text.strip()
        # 匹配 ```json 或 ``` 开头的代码块，非贪婪提取内容
        m = re.search(r'^```(?:json)?\s*\n?(.*?)\n?```\s*$', cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()

        # 1. 直接解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 1.25 非严格模式（允许字符串内换行符）
        # DeepSeek 等模型常返回包含实际换行符的 JSON，json.loads 默认拒绝
        try:
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            pass

        # 1.5 修复字符串内未转义换行符后再解析
        # LLM 生成的 code 字段常包含实际换行符而非 \\n，导致 json.loads 失败
        try:
            escaped = LLMClient._escape_newlines_in_json(cleaned)
            return json.loads(escaped)
        except json.JSONDecodeError:
            pass

        # 1.75 修复换行符 + 非严格模式（双重兜底）
        try:
            escaped = LLMClient._escape_newlines_in_json(cleaned)
            return json.loads(escaped, strict=False)
        except json.JSONDecodeError:
            pass

        # 2. 从 markdown 代码块按标记提取（兜底）
        for marker in ["```json", "```"]:
            if marker in text:
                try:
                    block = text.split(marker, 1)[1].split("```", 1)[0].strip()
                    return json.loads(block)
                except (json.JSONDecodeError, IndexError):
                    pass

        # 3. 尝试修复式解析
        repaired = LLMClient._repair_json(text)
        if repaired is not None:
            return repaired

        raise LLMError(f"LLM 响应不是合法 JSON: {text[:200]}...")

    @staticmethod
    def _repair_json(text: str) -> Optional[dict]:
        """尝试修复常见 JSON 格式错误并解析。

        处理策略:
          1. 用栈找到最外层匹配的 {}（跳过字符串内的 {/}）
          2. 尝试直接解析
          3. 截断修复: 用栈跟踪已打开的括号，生成正确的关闭序列
          4. 逐段截断: 去掉末尾不完整字段
        """
        first_brace = text.find("{")
        if first_brace == -1:
            return None

        # 用栈跟踪括号打开顺序，处理截断时按逆序生成关闭序列
        stack: list[str] = []  # 打开的括号栈（"{" 或 "["）
        in_string = False
        escape = False
        match_end = -1
        last_brace_pos = -1  # 最后一个 } 的位置（截断备选）
        for i in range(first_brace, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"' and not in_string:
                in_string = True
                continue
            if ch == '"' and in_string:
                in_string = False
                continue
            if not in_string:
                if ch == '{':
                    stack.append('{')
                elif ch == '}':
                    if stack and stack[-1] == '{':
                        stack.pop()
                    last_brace_pos = i
                    if not stack:
                        match_end = i
                        break
                elif ch == '[':
                    stack.append('[')
                elif ch == ']':
                    if stack and stack[-1] == '[':
                        stack.pop()

        if match_end == -1 and last_brace_pos != -1:
            # 截断 JSON: 用栈中剩余括号按逆序生成关闭序列
            closing_map = {'{': '}', '[': ']'}
            closing = ''.join(closing_map[b] for b in reversed(stack))
            candidate = text[first_brace : last_brace_pos + 1] + closing
        elif match_end == -1:
            # 完全无括号: 用文本末尾 + 单一 }
            candidate = text[first_brace:] + "}"
        else:
            candidate = text[first_brace : match_end + 1]

        # 尝试直接解析
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # 逐段截断: 去掉末尾不完整字段
        for _ in range(100):
            # 只截断字符串外的逗号，避免截断 code 字段内的逗号
            last_comma = LLMClient._last_top_level_comma(candidate)
            if last_comma == -1:
                break
            candidate = candidate[:last_comma].rstrip() + "}"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        return None

    @staticmethod
    def _last_top_level_comma(text: str) -> int:
        """找到最外层（非字符串内）的最后一个逗号位置。"""
        in_string = False
        escape = False
        last_comma = -1
        for i in range(len(text) - 1, -1, -1):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"' and not in_string:
                in_string = True
                continue
            if ch == '"' and in_string:
                in_string = False
                continue
            if not in_string and ch == ',':
                last_comma = i
                break
        return last_comma

    @staticmethod
    def _escape_newlines_in_json(text: str) -> str:
        """将 JSON 字符串值内的原始换行符替换为 \\n。

        LLM 生成的 JSON 中，code 字段的 Python 代码常包含实际换行符
        （而非 \\n 转义序列），导致 json.loads 失败。此方法跟踪字符串
        上下文，仅替换字符串内的换行符。
        """
        result: list[str] = []
        in_string = False
        escape = False
        for ch in text:
            if escape:
                result.append(ch)
                escape = False
                continue
            if ch == '\\' and in_string:
                result.append(ch)
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string and ch in '\n\r':
                result.append('\\n')
                continue
            result.append(ch)
        return ''.join(result)

    def bootstrap_factors(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        trace_id: str,
    ) -> list[dict[str, Any]]:
        """L1 Bootstrapping — 生成种子候选因子。

        Args:
            market_snapshot: 市场快照（来自 f10/web_collector）
            debate_gaps: 辩论薄弱维度列表
            max_candidates: 最大候选数
            trace_id: 全链路 trace_id

        Returns:
            list[SeedCandidate dict] — 每个 dict 包含 name, code, params, signature, economic_logic 等字段
        """
        logger.info(
            "[bootstrap_factors] 基类默认实现, trace_id=%s, max_candidates=%d, debate_gaps=%d, snapshot_keys=%d",
            trace_id, max_candidates, len(debate_gaps), len(market_snapshot),
        )
        return []


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

    def bootstrap_factors(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        trace_id: str,
    ) -> list[dict[str, Any]]:
        """L1 Bootstrapping — 通过 LLM 生成种子候选因子。"""
        import time
        t0 = time.time()
        logger.info(
            "[bootstrap_factors] OpenAI 开始, trace_id=%s, max_candidates=%d, debate_gaps=%d, snapshot_keys=%d",
            trace_id, max_candidates, len(debate_gaps), len(market_snapshot),
        )
        prompt = self._build_bootstrap_prompt(
            market_snapshot, debate_gaps, max_candidates, trace_id
        )
        logger.info(
            "[bootstrap_factors] Prompt 构造完成, trace_id=%s, prompt_len=%d",
            trace_id, len(prompt),
        )

        # 最多 2 次尝试: 首次 + 1 次 JSON 修复重试
        max_attempts = 2
        data: Optional[dict] = None
        for attempt in range(max_attempts):
            # 第 1 步: 调用 complete()
            try:
                raw_text, _ = self.complete(prompt, max_tokens=16000)
            except Exception as e:
                elapsed = (time.time() - t0) * 1000
                if attempt == max_attempts - 1:
                    logger.error(
                        "[bootstrap_factors] LLM 调用失败 (重试耗尽), trace_id=%s, elapsed_ms=%.1f, error=%s",
                        trace_id, elapsed, e, exc_info=True,
                    )
                    return []
                logger.warning(
                    "[bootstrap_factors] LLM 调用失败 (重试 %d), trace_id=%s, elapsed_ms=%.1f, error=%s",
                    attempt + 1, trace_id, elapsed, e,
                )
                continue

            # 调试: 保存原始响应用于分析
            debug_path = f"debug_llm_response_{trace_id}_{attempt}.txt"
            try:
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(raw_text)
                logger.info("[bootstrap_factors] 原始响应已保存: %s, len=%d", debug_path, len(raw_text))
            except Exception as de:
                logger.warning("[bootstrap_factors] 保存调试文件失败: %s", de)

            # 第 2 步: 解析 JSON
            try:
                data = LLMClient._parse_json(raw_text)
                break  # 解析成功
            except LLMError as e:
                elapsed = (time.time() - t0) * 1000
                if attempt == max_attempts - 1:
                    logger.warning(
                        "[bootstrap_factors] JSON 解析失败 (重试耗尽), trace_id=%s, elapsed_ms=%.1f, error=%s",
                        trace_id, elapsed, e,
                    )
                    return []
                logger.warning(
                    "[bootstrap_factors] JSON 解析失败 (重试 %d), trace_id=%s, elapsed_ms=%.1f, error=%s",
                    attempt + 1, trace_id, elapsed, e,
                )
                # 构造修复 prompt: 告知 LLM 其 JSON 不合法，要求重输出
                prompt = self._build_repair_prompt(raw_text, max_candidates)
                continue
        assert data is not None  # 确保此处 data 已赋值
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list):
            elapsed = (time.time() - t0) * 1000
            logger.warning(
                "[bootstrap_factors] candidates 字段非列表, trace_id=%s, elapsed_ms=%.1f, type=%s",
                trace_id, elapsed, type(candidates).__name__,
            )
            return []
        truncated = candidates[:max_candidates]
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "[bootstrap_factors] OpenAI 完成, trace_id=%s, elapsed_ms=%.1f, raw_count=%d, returned=%d, names=%s",
            trace_id, elapsed, len(candidates), len(truncated),
            [c.get("name", "?") for c in truncated],
        )
        return truncated

    @staticmethod
    def _build_bootstrap_prompt(
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        trace_id: str,
    ) -> str:
        """构造 L1 Bootstrapping Prompt。"""
        snapshot_summary = json.dumps(
            {k: v for k, v in market_snapshot.items() if k != "trace_id"},
            ensure_ascii=False, default=str,
        )[:2000]
        gaps_summary = json.dumps(
            debate_gaps[:5], ensure_ascii=False, default=str
        )[:1000]
        return f"""你是因子工程专家（FTS L1 Bootstrapping Agent）。基于市场快照和辩论薄弱维度，生成 {max_candidates} 个期货因子候选。

【市场快照】
{snapshot_summary}

【辩论薄弱维度】
{gaps_summary}

【任务】
生成 {max_candidates} 个因子候选，每个因子必须包含完整的 Python 代码。

【规则 — 必须严格遵守】
1. 代码函数签名固定为 `def factor_program(data, params):`
2. 输入: data 是 dict，键为 'open','high','low','close','volume' 等，值为 numpy 数组（长度 n）
   - 推荐写法: `close = data['close']`（返回 np.ndarray）
   - 兼容写法: `close = data['close'].values if hasattr(data, 'close') else data['close']`
3. 输出: 长度为 n 的 numpy 数组，值域在 [-1, 1] 之间
4. 代码中必须 `import numpy as np`
5. 任何运算（np.diff, np.roll, np.convolve）后必须保持输出长度为 n
6. 使用 `np.clip` 确保输出值域 [-1, 1]
7. 使用 `np.zeros(n)` 初始化数组
8. 因子逻辑应体现创新性，避免与常见因子重复
9. 代码必须简洁，不超过 50 行

【常见错误 — 必须避免】
❌ 使用未定义变量
❌ 长度不匹配: np.diff 输出 n-1，必须填充
❌ 忘记 import numpy as np
❌ 未保持输出长度
❌ 输出值超出 [-1, 1] 范围
❌ code 字段包含实际换行符！必须使用 \\n 转义序列替代实际换行符

【输出格式 — 必须严格遵守】
- 输出必须是 **纯 JSON 格式**，不包含任何 markdown 代码块标记（```json）
- 不要添加任何额外的文字说明或注释
- 只输出 JSON 对象本身
- **code 字段必须使用 \\n 转义实际换行符**（即整个 code 值占一行）
  - 错误: "code": "def f():\n    return 1"
  - 正确: "code": "def f():\\n    return 1"

【JSON 结构】
{{
    "candidates": [
        {{
            "name": "<因子名称>",
            "code": "<完整 factor_program 函数代码>",
            "params": {{"<param>": <value>}},
            "signature": {{
                "input_fields": ["close", "volume"],
                "output_type": "signal",
                "frequency": "daily",
                "lookback": 20
            }},
            "economic_logic": {{
                "theory": <0-5>,
                "behavioral": <0-5>,
                "microstructure": <0-5>,
                "institutional": <0-5>,
                "narrative": "<经济学解释，>= 20 字>"
            }},
            "parent_topic": "<主题来源>",
            "source": "l1_bootstrapping"
        }}
    ]
}}

【trace_id】: {trace_id}
现在请生成 {max_candidates} 个候选因子。"""

    @staticmethod
    def _build_repair_prompt(broken_raw: str, max_candidates: int) -> str:
        """构造 JSON 修复 Prompt — 告知 LLM 上次响应不合法，要求重输出。"""
        # 取前 2000 字符作为上下文
        snippet = broken_raw[:2000]
        return f"""你上次的响应包含不合法的 JSON，请重新生成 {max_candidates} 个因子候选。

【你上次输出的前 2000 字符】
{snippet}

【要求】
1. 输出必须是 **纯 JSON 格式**，不包含任何 markdown 代码块标记（```json）
2. 注意 JSON 中 code 字段的 Python 代码内换行符必须正确转义为 \\n
3. 每个字符串字段都必须正确闭合引号
4. 不要使用注释或额外文字
5. 只输出 JSON 对象，结构如下:
{{
    "candidates": [
        {{
            "name": "...",
            "code": "def factor_program(data, params):\\n...",
            "params": {{}},
            "signature": {{"input_fields": [...], "output_type": "signal", "frequency": "daily", "lookback": 20}},
            "economic_logic": {{"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "..."}},
            "parent_topic": "l1_bootstrapping_repair",
            "source": "l1_bootstrapping"
        }}
    ]
}}"""


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

    def bootstrap_factors(
        self,
        market_snapshot: dict[str, Any],
        debate_gaps: list[dict[str, Any]],
        max_candidates: int,
        trace_id: str,
    ) -> list[dict[str, Any]]:
        """Mock Bootstrapping — 返回预设因子候选。"""
        logger.info(
            "[bootstrap_factors] Mock 开始, trace_id=%s, max_candidates=%d, debate_gaps=%d, snapshot_keys=%d",
            trace_id, max_candidates, len(debate_gaps), len(market_snapshot),
        )
        candidates = [
            {
                "name": "mock_volume_price_divergence",
                "code": (
                    "def factor_program(data, params):\n"
                    "    import numpy as np\n"
                    "    close = data['close']\n"
                    "    volume = data['volume']\n"
                    "    window = int(params.get('window', 10))\n"
                    "    n = len(close)\n"
                    "    if n < window + 1:\n"
                    "        return np.zeros(n)\n"
                    "    vol_ma = np.zeros(n)\n"
                    "    vol_ma[window:] = np.convolve(volume, np.ones(window)/window, mode='valid')\n"
                    "    price_chg = np.zeros(n)\n"
                    "    price_chg[1:] = close[1:] - close[:-1]\n"
                    "    vol_ratio = np.where(vol_ma > 0, volume / np.maximum(vol_ma, 1e-10), 1.0)\n"
                    "    score = np.where((vol_ratio > 1.5) & (price_chg < 0), -0.6,\n"
                    "             np.where((vol_ratio < 0.7) & (price_chg > 0), 0.4, 0.0))\n"
                    "    return np.clip(score, -1.0, 1.0)\n"
                ),
                "params": {"window": 10},
                "signature": {
                    "input_fields": ["close", "volume"],
                    "output_type": "signal",
                    "frequency": "daily",
                    "lookback": 20,
                },
                "economic_logic": {
                    "theory": 4, "behavioral": 4, "microstructure": 3, "institutional": 4,
                    "narrative": "量价背离因子: 放量下跌反映空头主导，缩量上涨反映空头回补，捕捉短期反转机会。",
                },
                "parent_topic": "mock_bootstrapping_test",
                "source": "l1_bootstrapping",
            }
        ]
        logger.info(
            "[bootstrap_factors] Mock 完成, trace_id=%s, returned=%d, names=%s",
            trace_id, len(candidates), [c["name"] for c in candidates],
        )
        return candidates


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
