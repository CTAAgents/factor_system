"""tests/factor_engine/test_executor_backend.py — 可插拔执行器后端测试（GAP-I502，v2.83.0）。

覆盖:
    1. 四后端工厂创建 + 行为一致性（thread vs process 结果一致）
    2. process 后端 lambda/bound method 跨进程序列化（cloudpickle）
    3. dask/ray 缺依赖降级 process（返回对应后端实例，内部 degraded 行为一致）
    4. 未知后端回退 thread + 告警
    5. BatchMiner.filter_batch 接入后端：process 与 thread 结果一致 + 单任务异常隔离
    6. BatchMiningConfig 新字段默认值与配置
"""

from __future__ import annotations

import sys
from pathlib import Path

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.batch_mining import BatchMiningConfig, BatchMiner  # noqa: E402
from fts.factor_engine.executor_backend import (  # noqa: E402
    DaskBackend,
    ProcessBackend,
    RayBackend,
    ThreadBackend,
    create_executor_backend,
)


def _double(x: int) -> int:
    return x * 2


class _Square:
    """bound method 序列化测试：实例方法可跨进程执行。"""

    def apply(self, x: int) -> int:
        return x * x


class TestExecutorBackend:
    """GAP-I502: 执行器后端抽象行为。"""

    def test_thread_map_order_preserved(self):
        with create_executor_backend("thread", 4) as b:
            assert list(b.map(_double, [1, 2, 3, 4])) == [2, 4, 6, 8]

    def test_process_map_order_preserved(self):
        with create_executor_backend("process", 4) as b:
            assert list(b.map(_double, [1, 2, 3, 4])) == [2, 4, 6, 8]

    def test_thread_vs_process_consistent(self):
        data = list(range(1, 9))
        with create_executor_backend("thread", 3) as tb, create_executor_backend("process", 3) as pb:
            assert list(tb.map(_double, data)) == list(pb.map(_double, data))

    def test_process_backend_handles_lambda(self):
        with create_executor_backend("process", 2) as b:
            assert list(b.map(lambda x: x * 10, [1, 2, 3])) == [10, 20, 30]

    def test_process_backend_handles_bound_method(self):
        obj = _Square()
        with create_executor_backend("process", 2) as b:
            assert list(b.map(obj.apply, [2, 3, 4])) == [4, 9, 16]

    def test_dask_missing_degrades_to_process(self):
        with create_executor_backend("dask", 2) as b:
            assert isinstance(b, DaskBackend)
            assert b._degraded is not None
            assert list(b.map(_double, [1, 2, 3])) == [2, 4, 6]

    def test_ray_missing_degrades_to_process(self):
        with create_executor_backend("ray", 2) as b:
            assert isinstance(b, RayBackend)
            assert b._degraded is not None
            assert list(b.map(_double, [1, 2, 3])) == [2, 4, 6]

    def test_unknown_backend_falls_back_to_thread(self):
        with create_executor_backend("unknown_backend", 2) as b:
            assert isinstance(b, ThreadBackend)

    def test_create_returns_typed_instances(self):
        assert isinstance(create_executor_backend("thread"), ThreadBackend)
        assert isinstance(create_executor_backend("process"), ProcessBackend)
        assert isinstance(create_executor_backend("dask"), DaskBackend)
        assert isinstance(create_executor_backend("ray"), RayBackend)

    def test_context_manager_shutdown(self):
        b = create_executor_backend("thread", 2)
        with b as ctx:
            assert ctx is b
            assert list(b.map(_double, [1])) == [2]


class TestBatchMinerBackend:
    """GAP-I502: BatchMiner.filter_batch 执行器后端接入。"""

    @staticmethod
    def _proposal(name: str, factor_code: str = "def factor_program(data, params):\n    return data['close']") -> dict:
        return {
            "factor": {
                "factor_id": f"f_{name}",
                "name": name,
                "code": factor_code,
                "signature": {"input_fields": ["close"], "lookback": 5},
                "economic_logic": {},
            },
            "parent_id": "p1",
            "method": "operator",
            "summary": f"candidate {name}",
            "tokens": 10,
        }

    def _make_miner(self, backend: str):
        return BatchMiner(
            config=BatchMiningConfig(executor_backend=backend, max_workers=2),
            generate_cb=None,
            runtime_check_cb=lambda factor: (True, ""),
            prefilter_cb=lambda factor, trace_id: (True, "", 0.5),
        )

    def test_filter_batch_process_matches_thread(self):
        proposals = [self._proposal(f"c{i}") for i in range(4)]
        t_res = self._make_miner("thread").filter_batch(proposals, "t1")
        p_res = self._make_miner("process").filter_batch(proposals, "t1")
        assert t_res.total_passed == p_res.total_passed == 4
        assert {p["factor"]["name"] for p in t_res.passed} == {p["factor"]["name"] for p in p_res.passed}

    def test_filter_batch_process_prefilter_failure(self):
        """process 后端单任务异常/预筛失败不影响其他任务。"""
        proposals = [
            self._proposal("ok1"),
            self._proposal("ok2"),
        ]

        def prefilter(factor, trace_id):
            if factor["name"] == "ok2":
                raise RuntimeError("boom")
            return (True, "", 0.5)

        miner = BatchMiner(
            config=BatchMiningConfig(executor_backend="process", max_workers=2),
            runtime_check_cb=lambda factor: (True, ""),
            prefilter_cb=prefilter,
        )
        res = miner.filter_batch(proposals, "t1")
        # 异常任务降级 rejected，正常任务通过
        assert res.total_passed == 1
        assert res.total_rejected == 1
        assert res.passed[0]["factor"]["name"] == "ok1"
        assert "粗筛异常" in res.rejected[0]["prefilter_reason"]

    def test_config_default_backend_thread(self):
        cfg = BatchMiningConfig()
        assert cfg.executor_backend == "thread"
        assert cfg.executor_max_workers is None

    def test_config_custom_backend(self):
        cfg = BatchMiningConfig(executor_backend="process", executor_max_workers=8)
        assert cfg.executor_backend == "process"
        assert cfg.executor_max_workers == 8
