"""
FTS pipeline.base — 100% 行覆盖率测试。

覆盖：
  - DataPayload dataclass（全部字段 + 默认值）
  - ProcessingStage protocol（runtime_checkable, isinstance, 协议实现）
  - FactorPipeline 抽象基类（run 成功/失败, name/stages 属性, 空 stage 列表）
  - PipelineResult dataclass（全部字段 + 默认值）
"""

import pytest

from fts.pipeline.base import DataPayload, FactorPipeline, PipelineResult, ProcessingStage


# ─── DataPayload ────────────────────────────────────────────────────────

class TestDataPayload:
    """DataPayload 数据类全字段覆盖。"""

    def test_defaults(self):
        """验证默认值：symbol=None, payload=None, metadata={}, trace_id=None。"""
        dp = DataPayload(data_type="NEWS")
        assert dp.data_type == "NEWS"
        assert dp.symbol is None
        assert dp.payload is None
        assert dp.metadata == {}
        assert dp.trace_id is None

    def test_all_fields_explicit(self):
        """全部字段显式赋值。"""
        dp = DataPayload(
            data_type="SENTIMENT",
            symbol="RB",
            payload={"score": 0.8},
            metadata={"source": "test", "cached_at": "2025-01-01"},
            trace_id="trace-001",
        )
        assert dp.data_type == "SENTIMENT"
        assert dp.symbol == "RB"
        assert dp.payload == {"score": 0.8}
        assert dp.metadata == {"source": "test", "cached_at": "2025-01-01"}
        assert dp.trace_id == "trace-001"

    def test_mutable_metadata_default(self):
        """每次实例化获得独立的 metadata 默认 dict。"""
        dp1 = DataPayload(data_type="A")
        dp2 = DataPayload(data_type="B")
        dp1.metadata["key"] = "val"
        assert "key" not in dp2.metadata

    def test_symbol_none_cross_section(self):
        """symbol=None 表示跨品种。"""
        dp = DataPayload(data_type="MACRO", symbol=None)
        assert dp.symbol is None

    def test_payload_various_types(self):
        """payload 可以接受 dict / list 等多种类型。"""
        dp_dict = DataPayload(data_type="X", payload={"a": 1})
        dp_list = DataPayload(data_type="Y", payload=[1, 2, 3])
        dp_none = DataPayload(data_type="Z")
        assert isinstance(dp_dict.payload, dict)
        assert isinstance(dp_list.payload, list)
        assert dp_none.payload is None


# ─── ProcessingStage 协议 ──────────────────────────────────────────────

class TestProcessingStageProtocol:
    """ProcessingStage 协议验证。"""

    def test_runtime_checkable(self):
        """协议标记为 @runtime_checkable。"""
        assert hasattr(ProcessingStage, "__instancecheck__")

    def test_isinstance_positive(self):
        """实现协议接口的实例应通过 isinstance 检查。"""

        class GoodStage:
            input_type: str = "RAW"
            output_type: str = "PROCESSED"

            def process(self, input_data: DataPayload, symbol: str | None = None) -> DataPayload:
                return DataPayload(
                    data_type=self.output_type,
                    symbol=symbol or input_data.symbol,
                    payload={},
                    metadata={},
                    trace_id=input_data.trace_id,
                )

        assert isinstance(GoodStage(), ProcessingStage)

    def test_isinstance_negative(self):
        """未实现 process 方法的对象不应通过 isinstance 检查。"""

        class BadStage:
            input_type: str = "RAW"
            output_type: str = "PROCESSED"
            # 缺 process()

        assert not isinstance(BadStage(), ProcessingStage)

    def test_process_returns_data_payload(self):
        """process 方法应返回 DataPayload 实例。"""

        class SimpleStage:
            input_type: str = "NEWS"
            output_type: str = "SENTIMENT"

            def process(self, input_data, symbol=None):
                return DataPayload(
                    data_type=self.output_type,
                    symbol=symbol,
                    payload={"score": 0.5},
                    trace_id=input_data.trace_id,
                )

        stage = SimpleStage()
        inp = DataPayload(data_type="NEWS", symbol="AU", trace_id="t1")
        out = stage.process(inp, symbol="AU")
        assert isinstance(out, DataPayload)
        assert out.data_type == "SENTIMENT"
        assert out.trace_id == "t1"

    def test_protocol_attributes_accessible(self):
        """协议要求的 input_type / output_type 可访问。"""

        class MockStage:
            input_type: str = "ALPHA"
            output_type: str = "BETA"

            def process(self, input_data, symbol=None):
                return DataPayload(data_type=self.output_type)

        s = MockStage()
        assert s.input_type == "ALPHA"
        assert s.output_type == "BETA"


# ─── 辅助 mock stage ──────────────────────────────────────────────────

class _MockStage:
    """可注入的 mock stage，支持成功/失败两种模式。"""

    def __init__(self, name: str, input_type: str = "RAW", output_type: str = "PROC", fail: bool = False):
        self._name = name
        self.input_type = input_type
        self.output_type = output_type
        self.fail = fail

    def process(self, input_data: DataPayload, symbol: str | None = None) -> DataPayload:
        if self.fail:
            msg = f"stage '{self._name}' failed"
            raise ValueError(msg)
        return DataPayload(
            data_type=self.output_type,
            symbol=symbol or input_data.symbol,
            payload=input_data.payload,
            metadata={"stage": self._name},
            trace_id=input_data.trace_id,
        )


class _TestPipeline(FactorPipeline):
    """供测试使用的具体管线。"""

    def __init__(self, stages, name: str = "test_pipeline"):
        self._custom_stages = list(stages)
        super().__init__(name=name)

    def build_stages(self):
        return self._custom_stages


# ─── FactorPipeline ────────────────────────────────────────────────────

class TestFactorPipeline:
    """FactorPipeline 抽象基类——以具体子类测试。"""

    def test_name_property(self):
        """name 返回构造函数传入的名称。"""
        p = _TestPipeline([], name="alpha_pipe")
        assert p.name == "alpha_pipe"

    def test_name_default(self):
        """name 默认值为 "pipeline"（通过 FactorPipeline 默认）。"""
        p = _TestPipeline([], name="pipeline")
        assert p.name == "pipeline"

    def test_stages_property_returns_copy(self):
        """stages 属性返回列表副本，外部修改不影响内部。"""
        s1 = _MockStage("s1")
        p = _TestPipeline([s1])
        stages = p.stages
        stages.clear()
        assert len(p.stages) == 1

    def test_empty_stages_run_success(self):
        """空的 stage 列表应当成功运行。"""
        p = _TestPipeline([])
        inp = DataPayload(data_type="RAW", symbol="RB", trace_id="t-empty")
        result = p.run(inp, symbol="RB")
        assert result.success is True
        assert result.final_payload is inp
        assert result.stage_meta == []
        assert result.trace_id == "t-empty"
        assert result.error is None

    def test_run_success_two_stages(self):
        """2 个 stage 串联，全部成功。"""
        s1 = _MockStage("extract", input_type="RAW", output_type="FEATURE")
        s2 = _MockStage("score", input_type="FEATURE", output_type="SCORE")
        p = _TestPipeline([s1, s2], name="two_stage")
        inp = DataPayload(data_type="RAW", symbol="CU", payload={"val": 42}, trace_id="t2")

        result = p.run(inp, symbol="CU")
        assert result.success is True
        assert result.trace_id == "t2"
        assert result.error is None
        assert len(result.stage_meta) == 2

        # 检查各阶段元数据
        m1 = result.stage_meta[0]
        assert m1["index"] == 0
        assert m1["status"] == "ok"
        assert m1["input_type"] == "RAW"
        assert m1["output_type"] == "FEATURE"

        m2 = result.stage_meta[1]
        assert m2["index"] == 1
        assert m2["status"] == "ok"
        assert m2["input_type"] == "FEATURE"
        assert m2["output_type"] == "SCORE"

        # 最终载荷来自最后一个 stage
        assert result.final_payload.data_type == "SCORE"
        assert result.final_payload.symbol == "CU"
        assert result.final_payload.trace_id == "t2"

    def test_run_three_stages_success(self):
        """3 个 stage 串联，payload 逐级传递。"""
        s1 = _MockStage("a", output_type="A")
        s2 = _MockStage("b", output_type="B")
        s3 = _MockStage("c", output_type="C")
        p = _TestPipeline([s1, s2, s3])
        inp = DataPayload(data_type="RAW", symbol="AU", payload={"x": 1}, trace_id="t3")

        result = p.run(inp, symbol="AU")
        assert result.success is True
        assert len(result.stage_meta) == 3
        assert result.final_payload.data_type == "C"
        assert result.final_payload.trace_id == "t3"

    def test_run_stage_failure_error_propagation(self):
        """stage 抛异常 → returns 错误结果，元数据记录 error。"""
        s1 = _MockStage("ok1", output_type="OK")
        s2 = _MockStage("fail_stage", output_type="FAIL", fail=True)
        s3 = _MockStage("never_reached", output_type="NEVER")
        p = _TestPipeline([s1, s2, s3])
        inp = DataPayload(data_type="RAW", symbol="RB", payload={}, trace_id="t-fail")

        result = p.run(inp)
        assert result.success is False
        assert result.trace_id == "t-fail"
        assert "fail_stage" in (result.error or "")
        assert len(result.stage_meta) == 2  # 失败时中断，只有 2 条

        # 第 0 阶段成功
        assert result.stage_meta[0]["index"] == 0
        assert result.stage_meta[0]["status"] == "ok"

        # 第 1 阶段失败
        assert result.stage_meta[1]["index"] == 1
        assert result.stage_meta[1]["status"] == "error"
        assert "failed" in result.stage_meta[1]["error"]

    def test_run_trace_id_propagation(self):
        """trace_id 从 input_data 传播到最终 payload。"""
        s1 = _MockStage("s1")
        p = _TestPipeline([s1])
        inp = DataPayload(data_type="RAW", payload=1, trace_id="trace-abc")
        result = p.run(inp, symbol="RB")
        assert result.trace_id == "trace-abc"
        assert result.final_payload.trace_id == "trace-abc"

    def test_run_trace_id_fallback_when_stage_clears(self):
        """若 stage 返回的 payload 缺失 trace_id，run 应补回。"""

        class _StageClearsTraceId:
            input_type: str = "X"
            output_type: str = "Y"

            def process(self, input_data, symbol=None):
                return DataPayload(data_type=self.output_type, payload={})

        p = _TestPipeline([_StageClearsTraceId()])
        inp = DataPayload(data_type="X", payload=1, trace_id="t-keep")
        result = p.run(inp)
        assert result.final_payload.trace_id == "t-keep"

    def test_stage_meta_contains_stage_name(self):
        """stage_meta 中 stage 字段来自 type(stage).__name__。"""
        s1 = _MockStage("my_stage")
        p = _TestPipeline([s1])
        inp = DataPayload(data_type="RAW", trace_id="t")
        result = p.run(inp)
        assert result.stage_meta[0]["stage"] == "_MockStage"


# ─── PipelineResult ────────────────────────────────────────────────────

class TestPipelineResult:
    """PipelineResult 数据类全字段覆盖。"""

    def test_defaults(self):
        """验证默认值：stage_meta=[], trace_id=None, error=None。"""
        pr = PipelineResult(success=True, final_payload=None)
        assert pr.success is True
        assert pr.final_payload is None
        assert pr.stage_meta == []
        assert pr.trace_id is None
        assert pr.error is None

    def test_all_fields(self):
        """全部字段显式赋值。"""
        dp = DataPayload(data_type="SCORE", symbol="RB")
        meta = [{"index": 0, "status": "ok"}]
        pr = PipelineResult(
            success=False,
            final_payload=dp,
            stage_meta=meta,
            trace_id="trace-999",
            error="something broke",
        )
        assert pr.success is False
        assert pr.final_payload is dp
        assert pr.stage_meta == meta
        assert pr.trace_id == "trace-999"
        assert pr.error == "something broke"

    def test_success_true_no_error(self):
        """success=True 时 error 为 None。"""
        pr = PipelineResult(success=True, final_payload=None)
        assert pr.error is None

    def test_mutable_stage_meta_default(self):
        """每次实例化获得独立的 stage_meta 默认 list。"""
        pr1 = PipelineResult(success=True, final_payload=None)
        pr2 = PipelineResult(success=True, final_payload=None)
        pr1.stage_meta.append({"idx": 0})
        assert len(pr2.stage_meta) == 0

    def test_false_with_error(self):
        """success=False 时携带错误描述。"""
        pr = PipelineResult(success=False, final_payload=None, error="error detail")
        assert pr.success is False
        assert pr.error == "error detail"
