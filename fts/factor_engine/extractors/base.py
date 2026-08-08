"""
fts/factor_engine/extractors/base.py — 提取器基类与管道抽象

定义 BaseExtractor 和 BaseExtractorPipeline 抽象基类，
所有具体提取器必须实现 extract() 接口。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..contracts import SeedCandidate

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """单个因子提取器基类。

    每个提取器对应一个数据源（如天软文档、券商研报、学术论文），
    实现 extract() 方法返回 SeedCandidate 兼容的字典列表。
    """

    def __init__(self, name: str, paused: bool = False, llm_client: Optional[Any] = None):
        """
        Args:
            name: 提取器名称标识，如 "tinysoft", "broker_reports", "academic_papers"
            paused: 是否暂停（True 时 extract() 返回空列表）
            llm_client: LLM 客户端（用于动态因子提取），必须实现 complete(prompt) 或 generate_json(prompt)
        """
        self.name = name
        self.paused = paused
        self.llm_client = llm_client

    @abstractmethod
    def extract(self, trace_id: str) -> list[SeedCandidate]:
        """执行提取，返回候选因子列表。

        Args:
            trace_id: 全链路 trace_id

        Returns:
            list[SeedCandidate] — 候选因子列表
        """

    def pause(self) -> None:
        """暂停此提取器（后续调用返回空）。"""
        self.paused = True
        logger.info("[Extractor] %s 已暂停", self.name)

    def resume(self) -> None:
        """恢复此提取器。"""
        self.paused = False
        logger.info("[Extractor] %s 已恢复", self.name)

    @staticmethod
    def _make_candidate_id(name: str) -> str:
        """生成唯一 candidate_id。"""
        raw = f"{name}|{secrets.token_hex(8)}"
        return "cand_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def _make_signature(
        input_fields: list[str],
        output_type: str = "signal",
        frequency: str = "daily",
        lookback: int = 20,
    ) -> dict[str, Any]:
        """构造 FactorSignature 兼容的字典。"""
        return {
            "input_fields": input_fields,
            "output_type": output_type,
            "frequency": frequency,
            "lookback": lookback,
        }

    def _llm_extract_factors(
        self,
        source_text: str,
        trace_id: str,
        max_factors: int = 5,
        market: str = "futures",
    ) -> list[SeedCandidate]:
        """使用 LLM 从给定文本中提取因子候选。

        Args:
            source_text: 文本内容（研报摘要、论文摘要等）
            trace_id: 全链路 trace_id
            max_factors: 最大提取因子数
            market: 市场类型 ("futures" 或 "stock")

        Returns:
            list[SeedCandidate] — LLM 提取的候选因子
        """
        if self.llm_client is None or not source_text.strip():
            logger.info(
                "[_llm_extract_factors] %s: llm_client=%s, source_text_len=%d, 跳过",
                self.name, bool(self.llm_client), len(source_text),
            )
            return []

        if market == "stock":
            prefix = "stk_"
            market_desc = "股票/ETF"
            extra_fields = "（含 close, high, low, volume, pe, pb, market_cap 等字段）"
            extra_directions = (
                "包括但不限于：截面动量、估值因子、成长因子、质量因子、"
                "低波因子、红利因子、情绪因子、资金流因子、分析师预期修正等方向。"
            )
        else:
            prefix = "fut_"
            market_desc = "期货/CTA"
            extra_fields = "（含 close, high, low, volume 等字段）"
            extra_directions = (
                "包括但不限于：趋势跟踪、截面动量、期限结构套利、波动率预测、"
                "量价背离、持仓量分析、季节性模式、跳跃风险、偏度交易等方向。"
            )

        prompt = f"""你是一个量化因子研究专家。请从以下文本中提取可行的{market_desc}因子想法。

要求:
1. 识别文本中提到的量化交易策略、因子逻辑或市场规律
2. 为每个因子想法生成 Python 代码（函数签名: def factor_program(data, params) -> np.ndarray）
3. 代码必须使用 numpy，输入为 data dict{extra_fields}
4. 输出范围 [-1, 1]，shape 与输入一致
5. 每个因子提供四维经济逻辑评分（theory/behavioral/microstructure/institutional, 0-5）
6. 返回 JSON 数组，不要 markdown 代码块标记

输出格式:
[
  {{
    "name": "{prefix}<英文名>",
    "code": "def factor_program(data, params):\\n    import numpy as np\\n    ...",
    "params": {{"window": 20}},
    "input_fields": ["close", "volume"],
    "lookback": 20,
    "output_type": "signal",
    "frequency": "daily",
    "economic_logic": {{
      "theory": 4, "behavioral": 3, "microstructure": 4, "institutional": 3,
      "narrative": "因子的经济学解释"
    }}
  }}
]

文本内容:
{source_text[:8000]}

请返回最多 {max_factors} 个因子。"""
        try:
            if hasattr(self.llm_client, "generate_json"):
                result = self.llm_client.generate_json(prompt, max_tokens=4000)
            else:
                text, _ = self.llm_client.complete(prompt, max_tokens=4000)
                import json
                result = json.loads(text)

            if not isinstance(result, list):
                result = [result]

            candidates: list[SeedCandidate] = []
            for item in result:
                if not isinstance(item, dict) or not item.get("code"):
                    continue
                name = item.get("name", f"fut_llm_{self.name}_{len(candidates)}")
                raw_id = f"{name}|{secrets.token_hex(8)}"
                candidate_id = "cand_" + hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:8]
                economic_logic = item.get("economic_logic", {})
                if not isinstance(economic_logic, dict):
                    economic_logic = {}
                candidates.append(SeedCandidate(
                    candidate_id=candidate_id,
                    name=name,
                    code=item["code"],
                    params=item.get("params", {}),
                    signature=self._make_signature(
                        input_fields=item.get("input_fields", ["close"]),
                        output_type=item.get("output_type", "signal"),
                        frequency=item.get("frequency", "daily"),
                        lookback=item.get("lookback", 20),
                    ),
                    economic_logic=economic_logic,
                    source="l1_extractor_pipeline",
                    market="futures",
                    parent_topic=f"extractor_pipeline/{self.name}/{name}",
                    debate_round_ref=None,
                    debate_gap=None,
                    web_snapshot_ref=None,
                    is_executable=False,
                    is_duplicate=False,
                    passed_l1_verifier=False,
                    failure_reasons=[],
                    trace_id=trace_id,
                    created_at=datetime.now().isoformat(),
                    injected_to_l2=False,
                    injected_at=None,
                ))

            logger.info(
                "[_llm_extract_factors] %s: LLM 提取完成, candidates=%d, source_len=%d",
                self.name, len(candidates), len(source_text),
            )
            return candidates

        except Exception as e:
            logger.error(
                "[_llm_extract_factors] %s: LLM 提取异常: %s, trace_id=%s",
                self.name, e, trace_id, exc_info=True,
            )
            return []


class BaseExtractorPipeline(ABC):
    """提取器管道基类 — 管理多个提取器源。

    负责:
        - 管理多个 BaseExtractor 的生命周期
        - 控制每个源的暂停/恢复状态
        - 收集所有未暂停源的候选因子
        - 持久化暂停状态到 JSON 文件
    """

    def __init__(
        self,
        extractors: list[BaseExtractor],
        market: str,
        state_path: str | Path = "memory/extractors/state.json",
    ):
        """
        Args:
            extractors: 提取器列表
            market: 市场类型 ("futures" 或 "stock")
            state_path: 状态持久化路径
        """
        self.extractors = {e.name: e for e in extractors}
        self.market = market
        self.state_path = Path(state_path)
        self._load_state()

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        """执行所有未暂停提取器的提取。

        Args:
            trace_id: 全链路 trace_id

        Returns:
            list[SeedCandidate] — 合并后的候选因子列表
        """
        all_candidates: list[SeedCandidate] = []
        for name, extractor in self.extractors.items():
            if extractor.paused:
                logger.info(
                    "[ExtractorPipeline] 跳过已暂停源: %s (market=%s)",
                    name, self.market,
                )
                continue
            try:
                candidates = extractor.extract(trace_id)
                logger.info(
                    "[ExtractorPipeline] 源 %s 提取完成: %d 个候选 (market=%s)",
                    name, len(candidates), self.market,
                )
                all_candidates.extend(candidates)
            except Exception as e:
                logger.error(
                    "[ExtractorPipeline] 源 %s 提取异常: %s (market=%s)",
                    name, e, self.market, exc_info=True,
                )

        logger.info(
            "[ExtractorPipeline] 全部提取完成: 共 %d 个候选 (market=%s, trace_id=%s)",
            len(all_candidates), self.market, trace_id,
        )
        return all_candidates

    def pause_source(self, name: str) -> None:
        """暂停指定源。"""
        if name in self.extractors:
            self.extractors[name].pause()
            self._save_state()

    def resume_source(self, name: str) -> None:
        """恢复指定源。"""
        if name in self.extractors:
            self.extractors[name].resume()
            self._save_state()

    def is_paused(self, name: str) -> bool:
        """查询指定源是否暂停。"""
        ext = self.extractors.get(name)
        return ext.paused if ext else True

    def _load_state(self) -> None:
        """从文件加载暂停状态。"""
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            market_state = state.get(self.market, {})
            for name, paused in market_state.items():
                if name in self.extractors:
                    self.extractors[name].paused = paused
            logger.info(
                "[ExtractorPipeline] 状态已加载: market=%s, sources=%s",
                self.market, {k: v for k, v in market_state.items()},
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[ExtractorPipeline] 状态加载失败: %s", e)

    def _save_state(self) -> None:
        """持久化暂停状态到文件。"""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        # 加载现有状态
        state: dict[str, dict[str, bool]] = {}
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # 更新当前 market 的状态
        state.setdefault(self.market, {})
        for name, ext in self.extractors.items():
            state[self.market][name] = ext.paused

        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        logger.info(
            "[ExtractorPipeline] 状态已持久化: market=%s, sources=%s",
            self.market, {k: v for k, v in state.get(self.market, {}).items()},
        )

    @staticmethod
    def _yaml_factor_to_candidate(
        factor: dict[str, Any],
        source: str,
        market: str,
        trace_id: str,
        family_name: str = "",
    ) -> SeedCandidate:
        """将 YAML 种子因子格式转换为 SeedCandidate。"""
        name = factor.get("name", "unknown")
        raw = f"{name}|{secrets.token_hex(8)}"
        candidate_id = "cand_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

        economic_logic = factor.get("economic_logic", {})
        if not isinstance(economic_logic, dict):
            economic_logic = {}

        return SeedCandidate(
            candidate_id=candidate_id,
            name=name,
            code=factor.get("code", ""),
            params=factor.get("params", {}),
            signature={
                "input_fields": factor.get("input_fields", ["close"]),
                "output_type": factor.get("output_type", "signal"),
                "frequency": factor.get("frequency", "daily"),
                "lookback": factor.get("lookback", 20),
            },
            economic_logic=economic_logic,
            source="l1_extractor_pipeline",
            market=market,
            parent_topic=f"extractor_pipeline/{family_name or source}/{name}",
            debate_round_ref=None,
            debate_gap=None,
            web_snapshot_ref=None,
            is_executable=True,
            is_duplicate=False,
            passed_l1_verifier=False,
            failure_reasons=[],
            trace_id=trace_id,
            created_at=datetime.now().isoformat(),
            injected_to_l2=False,
            injected_at=None,
        )