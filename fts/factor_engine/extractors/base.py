"""
fts/factor_engine/extractors/base.py — 提取器基类与管道抽象

定义 BaseExtractor 和 BaseExtractorPipeline 抽象基类，
所有具体提取器必须实现 extract() 接口。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast

from ..contracts import EconomicLogic, FactorSignature, SeedCandidate

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
    ) -> FactorSignature:
        """构造 FactorSignature 兼容的字典。"""
        return cast(
            FactorSignature,
            {
                "input_fields": input_fields,
                "output_type": output_type,
                "frequency": frequency,
                "lookback": lookback,
            },
        )

    def _llm_extract_factors(
        self,
        source_text: str,
        trace_id: str,
        max_factors: int = 20,
        market: str = "futures",
    ) -> list[SeedCandidate]:
        """使用 LLM 从给定文本中提取因子候选。

        Args:
            source_text: 文本内容（研报摘要、论文摘要等）
            trace_id: 全链路 trace_id
            max_factors: 最大提取因子数（plans/41 A3：5→8→20）
            market: 市场类型（默认 "futures"）

        Returns:
            list[SeedCandidate] — LLM 提取的候选因子
        """
        if self.llm_client is None or not source_text.strip():
            logger.info(
                "[_llm_extract_factors] %s: llm_client=%s, source_text_len=%d, 跳过",
                self.name,
                bool(self.llm_client),
                len(source_text),
            )
            return []

        prefix = "fut_"
        market_desc = "期货/CTA"
        extra_fields = "（含 close, high, low, volume 等字段）"

        prompt = f"""你是一个量化因子研究专家。请从以下文本中提取可行的{market_desc}因子想法。

要求:
1. 识别文本中提到的量化交易策略、因子逻辑或市场规律
2. 为每个因子想法生成 Python 代码（函数签名: def factor_program(data, params) -> np.ndarray）
3. 代码必须使用 numpy，输入为 data dict{extra_fields}
4. 输出范围 [-1, 1]，shape 与输入一致
5. 每个因子提供四维经济逻辑评分（theory/behavioral/microstructure/institutional, 0-5）
   评分量规: 3-5 分=该维度有明确机制支撑（理论/行为偏差/微观结构/机构制度）并在 narrative 中论证;
   2 分=仅直觉逻辑无机制论证; 0-1 分=与本维度无关。narrative 必须逐维度说明评分依据，
   禁止对不明确定义的维度一律打 2 分；institutional 维度参考机构参与度/持仓结构/期限结构制度/资金流向等口径。
6. 返回 JSON 数组，不要 markdown 代码块标记
7. 代码必须通过沙箱校验: 仅允许 import numpy/pandas/scipy/math/statistics 等白名单模块，
   禁止 import os/sys/sklearn/torch/requests 等任何外部或 ML 框架模块；
   论文中的 LSTM/机器学习方法须降级为纯 numpy 量价近似实现，或仅输出因子思想而不生成代码

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
            # plans/41 A3: max_factors 提升至 20 后，输出 token 预算同步放大（4000→8000）
            if hasattr(self.llm_client, "generate_json"):
                result = self.llm_client.generate_json(prompt, max_tokens=8000)
            else:
                text, _ = self.llm_client.complete(prompt, max_tokens=8000)
                # P1b: 保存 LLM 原始响应，便于沙箱编译失败等问题的定位分析
                debug_path = f"debug_llm_response_{trace_id}_{self.name}.txt"
                try:
                    with open(debug_path, "w", encoding="utf-8") as f:
                        f.write(text)
                except Exception as de:  # noqa: BLE001
                    logger.warning("[_llm_extract_factors] 保存调试文件失败: %s", de)
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
                candidates.append(
                    SeedCandidate(
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
                        economic_logic=cast(EconomicLogic, economic_logic),
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
                    )
                )

            logger.info(
                "[_llm_extract_factors] %s: LLM 提取完成, candidates=%d, source_len=%d",
                self.name,
                len(candidates),
                len(source_text),
            )
            return candidates

        except Exception as e:
            logger.error(
                "[_llm_extract_factors] %s: LLM 提取异常: %s, trace_id=%s",
                self.name,
                e,
                trace_id,
                exc_info=True,
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
        state_store: Any | None = None,
    ):
        """
        Args:
            extractors: 提取器列表
            market: 市场类型（"futures"）
            state_path: 兼容保留（DuckDB SSOT 下不再使用）
            state_store: 可选状态存储（StateKVStore），缺省用全局 SSOT（供测试隔离）
        """
        self.extractors = {e.name: e for e in extractors}
        self.market = market
        self.state_path = Path(state_path)
        self._state_store = state_store
        self._load_state()

    def extract(self, trace_id: str) -> list[SeedCandidate]:
        """并行执行所有未暂停提取器的提取（GAP-I101 二期，v2.80.0）。

        多源并行注入：各源独立无共享可变状态，线程并行可显著缩短
        多路知识源（研报/论文/公告/宏观等）的合并等待时间。

        Args:
            trace_id: 全链路 trace_id

        Returns:
            list[SeedCandidate] — 合并后的候选因子列表
        """
        active = [(name, ex) for name, ex in self.extractors.items() if not ex.paused]
        if not active:
            logger.info("[ExtractorPipeline] 全部源已暂停, 跳过 (market=%s)", self.market)
            return []

        # 单源直跑（避免线程开销）；多源并行收集
        if len(active) == 1:
            name, ex = active[0]
            cands = self._extract_one(name, ex, trace_id)
            all_candidates: list[SeedCandidate] = list(cands)
        else:
            all_candidates = self._extract_parallel(active, trace_id)

        logger.info(
            "[ExtractorPipeline] 全部提取完成: 共 %d 个候选 (market=%s, trace_id=%s)",
            len(all_candidates),
            self.market,
            trace_id,
        )
        return all_candidates

    def _extract_parallel(
        self,
        active: list[tuple[str, BaseExtractor]],
        trace_id: str,
    ) -> list[SeedCandidate]:
        """并行收集多源候选（ThreadPoolExecutor，单源异常不影响其他源）。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: list[list[SeedCandidate]] = []
        with ThreadPoolExecutor(max_workers=min(len(active), 4)) as ex:
            futures = {ex.submit(self._extract_one, name, ex_, trace_id): name for name, ex_ in active}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as e:  # pragma: no cover - 防御兜底
                    logger.error(
                        "[ExtractorPipeline] 并行提取异常: 源 %s: %s (market=%s)",
                        name,
                        e,
                        self.market,
                        exc_info=True,
                    )
        return [c for cands in results for c in cands]

    def _extract_one(self, name: str, extractor: BaseExtractor, trace_id: str) -> list[SeedCandidate]:
        """执行单源提取（含异常降级与统计日志）。"""
        try:
            candidates = extractor.extract(trace_id)
            logger.info(
                "[ExtractorPipeline] 源 %s 提取完成: %d 个候选 (market=%s)",
                name,
                len(candidates),
                self.market,
            )
            return candidates
        except Exception as e:
            logger.error(
                "[ExtractorPipeline] 源 %s 提取异常: %s (market=%s)",
                name,
                e,
                self.market,
                exc_info=True,
            )
            return []

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
        """从 state.duckdb 加载暂停状态（SSOT，plans/29 P4 读路径切换）。"""
        from fts.store.state_db import get_state_store

        store = self._state_store if self._state_store is not None else get_state_store()
        try:
            state = store.get("extractors", "state")
            market_state = (state or {}).get(self.market, {}) if isinstance(state, dict) else {}
        except Exception as e:  # noqa: BLE001 — 加载失败不阻断提取
            logger.warning("[ExtractorPipeline] 状态加载失败: %s", e)
            return
        for name, paused in market_state.items():
            if name in self.extractors:
                self.extractors[name].paused = paused
        logger.info(
            "[ExtractorPipeline] 状态已加载: market=%s, sources=%s",
            self.market,
            {k: v for k, v in market_state.items()},
        )

    def _save_state(self) -> None:
        """持久化暂停状态到 state.duckdb（SSOT，UPSERT）。"""
        from fts.store.state_db import get_state_store

        store = self._state_store if self._state_store is not None else get_state_store()
        current = store.get("extractors", "state")
        state: dict[str, dict[str, bool]] = dict(current) if isinstance(current, dict) else {}
        # 更新当前 market 的状态
        state.setdefault(self.market, {})
        for name, ext in self.extractors.items():
            state[self.market][name] = ext.paused
        store.upsert("extractors", "state", state, run_id="extractor_pipeline")

        logger.info(
            "[ExtractorPipeline] 状态已持久化: market=%s, sources=%s",
            self.market,
            {k: v for k, v in state.get(self.market, {}).items()},
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
            economic_logic=cast(EconomicLogic, economic_logic),
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
