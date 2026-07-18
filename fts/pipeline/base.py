"""
fts.pipeline.base — 因子推演管线抽象基类。

HARNESS §契约优先：本文件定义 FTS 因子计算层的核心契约。
- 输入: Data-Core 已加工的结构化数据（DataPayload）
- 输出: 因子输入数据（DataPayload，类型由 stage 决定）
- 管线可串联组合，LLM 是其中的一个 stage

边界:
    - 数据采集和基础加工（新闻分类、实体抽取）由 Data-Core 完成
    - FTS 管线从已结构化的数据开始
    - 情绪聚合器/market_regime 等数据加工能力已迁移到 Data-Core

版本: v0.1.0
"""
# pylint: disable=broad-exception-caught,too-few-public-methods

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


# ─── 数据载荷（与 Data-Core 对齐）─────────────────────────

@dataclass
class DataPayload:
    """数据载荷 — Data-Core 与 FTS 管线之间的标准传输对象。

    FTS 不重复定义 DataType/MarketType，由 datacore.models.enums 提供。
    本类仅作为管线内部的载荷容器。
    """
    data_type: str                              # DataType 字符串值（如 "NEWS", "SENTIMENT"）
    symbol: Optional[str] = None                # 关联品种（None = 跨品种）
    payload: Any = None                         # 实际数据（dict / DataFrame / list）
    metadata: dict = field(default_factory=dict)  # 元数据（source, cached_at, trace_id, ...）
    trace_id: Optional[str] = None              # HARNESS §trace_id 全链路


# ─── 管线阶段协议 ─────────────────────────────────────────

@runtime_checkable
class ProcessingStage(Protocol):
    """数据处理管线阶段协议 — FTS 因子计算层。

    输入 Data-Core 已加工的结构化数据 → 输出因子输入数据。
    管线可组合串联，LLM 是管线中的一个 stage。

    约束:
        - input_type / output_type 必须在初始化时声明
        - process() 必须传播 trace_id
        - 不允许跨网络获取原始数据（应通过 UnifiedDataProvider 预取）
    """

    input_type: str                             # 输入 DataType（字符串值）
    output_type: str                            # 输出 DataType（字符串值）

    def process(self,
                input_data: DataPayload,
                symbol: Optional[str] = None,
                ) -> DataPayload:
        """处理数据，返回新的 DataPayload。"""


# ─── 抽象基类（提供默认实现）──────────────────────────────

class FactorPipeline(ABC):
    """因子推演管线抽象基类 — 串联多个 ProcessingStage。

    用法:
        class MyPipeline(FactorPipeline):
            def build_stages(self) -> list[ProcessingStage]:
                return [StageA(), StageB()]

        pipe = MyPipeline()
        result = pipe.run(input_payload, symbol="RB")
    """

    def __init__(self, name: str = "pipeline"):
        self._name = name
        self._stages: list[ProcessingStage] = self.build_stages()

    @property
    def name(self) -> str:
        return self._name

    @property
    def stages(self) -> list[ProcessingStage]:
        return list(self._stages)

    @abstractmethod
    def build_stages(self) -> list[ProcessingStage]:
        """构造管线阶段列表（子类实现）。"""

    def run(self,
            input_data: DataPayload,
            symbol: Optional[str] = None,
            ) -> "PipelineResult":
        """按顺序运行所有 stage。

        Args:
            input_data: 管线输入载荷
            symbol: 关联品种（None = 跨品种）

        Returns:
            PipelineResult: 包含最终载荷、各阶段元数据、trace_id
        """
        trace_id = input_data.trace_id
        current = input_data
        stage_meta: list[dict] = []

        for idx, stage in enumerate(self._stages):
            try:
                current = stage.process(current, symbol=symbol)
                # 传播 trace_id
                if current.trace_id is None and trace_id is not None:
                    current.trace_id = trace_id
                stage_meta.append({
                    "index": idx,
                    "stage": getattr(stage, "__class__", type(stage)).__name__,
                    "input_type": getattr(stage, "input_type", None),
                    "output_type": getattr(stage, "output_type", None),
                    "status": "ok",
                })
            except Exception as e:  # noqa: BLE001
                stage_meta.append({
                    "index": idx,
                    "stage": type(stage).__name__,
                    "status": "error",
                    "error": str(e),
                })
                return PipelineResult(
                    success=False,
                    final_payload=current,
                    stage_meta=stage_meta,
                    trace_id=trace_id,
                    error=str(e),
                )

        return PipelineResult(
            success=True,
            final_payload=current,
            stage_meta=stage_meta,
            trace_id=trace_id,
        )


# ─── 管线运行结果 ─────────────────────────────────────────

@dataclass
class PipelineResult:
    """管线运行结果。

    Attributes:
        success: 是否所有 stage 都成功
        final_payload: 最终输出的 DataPayload
        stage_meta: 各阶段运行元数据（含错误信息）
        trace_id: 全链路 trace_id
        error: 失败时的错误描述（success=True 时为 None）
    """
    success: bool
    final_payload: Optional[DataPayload]
    stage_meta: list[dict] = field(default_factory=list)
    trace_id: Optional[str] = None
    error: Optional[str] = None
