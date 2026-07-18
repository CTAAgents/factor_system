"""
FTS factor_engine/macro_evolution.py — 宏观演化（LLM 改逻辑）

factorengine 核心约束（三层分离）：
    LLM 负责: 因子逻辑修改、新因子想法生成
    CPU 负责: 参数空间搜索、快速验证

经验链约束（防过拟合第 6 道防线）:
    每次调用必须读取经验链摘要
    新因子变异必须显式说明"避免重复踩坑"的依据

版本: v0.1.0（从 FDT v8.10.0 剥离，LLM 客户端改用 fts.llm）
"""
# pylint: disable=too-few-public-methods,fixme

from __future__ import annotations

import json
import re
from typing import Optional

from .contracts import EconomicLogic, ExperienceTrace, FactorProgram
from .experience_chain import ExperienceChain
from .factor_program import create_factor_program
from ..llm import LLMClient, MockLLMClient, get_llm_client as _get_llm_client


# ─── 宏观演化器 ───────────────────────────────────────────

class MacroEvolutionError(Exception):
    """宏观演化失败。"""


class MacroEvolver:
    """宏观演化器 — LLM 驱动的因子逻辑修改。

    Usage:
        evolver = MacroEvolver(llm_client=MockLLMClient(), experience_chain=chain)
        new_factor = evolver.evolve(parent_factor, generation=1)
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        experience_chain: Optional[ExperienceChain] = None,
        max_tokens_per_call: int = 4000,
    ):
        self.llm = llm_client or MockLLMClient()
        self.experience_chain = experience_chain
        self.max_tokens_per_call = max_tokens_per_call

    def evolve(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: Optional[str] = None,
    ) -> tuple[FactorProgram, str, int]:
        """对父因子进行宏观演化（LLM 改逻辑）。

        Args:
            parent: 父因子
            generation: 新因子的代数（= parent.generation + 1）
            trace_id: 全链路 trace_id

        Returns:
            (new_factor, mutation_summary, tokens_consumed)
        """
        # 读取经验链（HARNESS 强制约束）
        experience_context = self._read_experience_for_llm()

        # 构造 LLM prompt
        prompt = self._build_prompt(parent, generation, experience_context)

        # 调用 LLM
        try:
            response_text, tokens = self.llm.complete(prompt, self.max_tokens_per_call)
        except Exception as e:
            raise MacroEvolutionError(f"LLM 调用失败: {e}") from e

        # 解析 LLM 响应
        try:
            response = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise MacroEvolutionError(f"LLM 响应非 JSON: {e}") from e

        # 构造新因子代码（在 mock 场景下做参数扰动）
        new_code = self._apply_code_modification(
            parent.get("code", ""),
            response.get("code_modification", ""),
        )

        # 构造经济逻辑
        el_mod = response.get("economic_logic_modification", {})
        new_el = EconomicLogic(
            theory=int(el_mod.get("theory", parent.get("economic_logic", {}).get("theory", 3))),
            behavioral=int(el_mod.get("behavioral", parent.get("economic_logic", {}).get("behavioral", 3))),
            microstructure=int(el_mod.get("microstructure", parent.get("economic_logic", {}).get("microstructure", 3))),
            institutional=int(el_mod.get("institutional", parent.get("economic_logic", {}).get("institutional", 3))),
            narrative=el_mod.get("narrative", parent.get("economic_logic", {}).get("narrative", "LLM 生成")),
        )

        # 创建新因子
        new_factor = create_factor_program(
            name=f"{parent.get('name', 'factor')}_g{generation}",
            code=new_code,
            params=dict(parent.get("params", {})),  # params 由微观演化负责
            signature=parent.get("signature", {}),
            economic_logic=new_el,
            source="macro_evolution",
            parent_id=parent.get("factor_id"),
            generation=generation,
            trace_id=trace_id,
        )

        mutation_summary = response.get(
            "mutation_summary", f"LLM 演化代 {generation}"
        )
        return new_factor, mutation_summary, tokens

    def _read_experience_for_llm(self) -> dict[str, list[ExperienceTrace]]:
        """读取最近经验链供 LLM 参考。"""
        if self.experience_chain is None:
            return {"success": [], "failure": []}
        return self.experience_chain.read_recent_for_llm()

    def _build_prompt(
        self,
        parent: FactorProgram,
        generation: int,
        experience: dict[str, list[ExperienceTrace]],
    ) -> str:
        """构造 LLM 提示词。"""
        recent_success = experience.get("success", [])
        recent_failure = experience.get("failure", [])

        prompt = f"""你是因子工程专家。基于以下父因子，生成新的因子变异。

父因子:
- name: {parent.get('name', '?')}
- factor_id: {parent.get('factor_id', '?')}
- generation: {parent.get('generation', 0)}
- code: {parent.get('code', '')[:500]}
- params: {parent.get('params', {})}
- economic_logic: {parent.get('economic_logic', {})}

最近成功轨迹（参考）:
{self._format_experience_for_prompt(recent_success)}

最近失败轨迹（避免重复踩坑）:
{self._format_experience_for_prompt(recent_failure)}

任务: 生成代 {generation} 的新因子变异。

输出 JSON 格式:
{{
    "mutation_type": "macro_logic",
    "mutation_summary": "<一句话描述本次变异>",
    "code_modification": "<代码修改指令>",
    "economic_logic_modification": {{
        "theory": <0-5>,
        "behavioral": <0-5>,
        "microstructure": <0-5>,
        "institutional": <0-5>,
        "narrative": "<经济学解释>"
    }},
    "lessons_referenced": ["<引用的历史教训>"]
}}
"""
        return prompt

    @staticmethod
    def _format_experience_for_prompt(traces: list[ExperienceTrace]) -> str:
        """格式化经验链为 LLM 易读字符串。"""
        if not traces:
            return "(无)"
        lines = []
        for i, t in enumerate(traces[:5], 1):  # 最多 5 条
            eval_ = t.get("evaluation", {})
            bt = eval_.get("level_1_backtest", {})
            lines.append(
                f"  {i}. {t.get('factor_id', '?')}: {t.get('mutation_summary', '')}"
                f" | IC={bt.get('ic', '?')}, 夏普={bt.get('sharpe', '?')}"
            )
            for r in eval_.get("failure_reasons", [])[:2]:
                lines.append(f"     失败: {r}")
        return "\n".join(lines)

    @staticmethod
    def _apply_code_modification(original_code: str, modification: str) -> str:
        """应用 LLM 指定的代码修改。

        在 mock 场景下，根据 code_modification 字段做简单的字符串替换。
        生产环境需要解析 LLM 生成的真实代码修改指令。
        """
        if not modification:
            return original_code

        # Mock: window_plus_5 → 把所有 window 默认值 +5
        if modification == "window_plus_5":
            # 修改 params.get('window', N) 中的 N
            new_code = re.sub(
                r"params\.get\(['\"]window['\"],\s*(\d+)\)",
                lambda m: f"params.get('window', {int(m.group(1)) + 5})",
                original_code,
            )
            return new_code

        # 默认：原样返回
        return original_code


def get_default_llm_client() -> LLMClient:
    """获取默认 LLM 客户端。

    通过 fts.llm.get_llm_client() 自动检测可用的 LLM 后端。
    自动检测顺序：
        1. OPENAI_API_KEY → OpenAIClient
        2. ANTHROPIC_API_KEY → AnthropicClient
        3. 两者均无 → MockLLMClient

    环境变量:
        FTS_LLM_BACKEND: 强制指定 "openai" / "anthropic" / "mock"
        OPENAI_API_KEY: OpenAI 的 API Key
        OPENAI_MODEL: OpenAI 模型名 (默认 gpt-4o)
        ANTHROPIC_API_KEY: Anthropic 的 API Key
        ANTHROPIC_MODEL: Anthropic 模型名 (默认 claude-sonnet-4-20250514)
    """
    return _get_llm_client()


__all__ = [
    "LLMClient",
    "MockLLMClient",
    "MacroEvolutionError",
    "MacroEvolver",
    "get_default_llm_client",
]
