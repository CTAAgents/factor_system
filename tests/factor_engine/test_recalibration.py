"""因子自动重校准队列测试（C6，v2.100.1）。

覆盖:
    - RecalibrationQueue 状态机（enqueue 去重/上限/list_pending/transition/幂等持久化/损坏回退）
    - recalibrate_factor 判定（done 提升 / skipped 无提升 / failed 异常）
    - process_recalibration_queue（elite 回写 / not_found / dry_run 不落盘）
    - LiveVsBacktestICReport 触发源接线（开关开/关）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.recalibration import (  # noqa: E402
    RecalibrationConfig,
    RecalibrationQueue,
    RecalibrationStatus,
    recalibrate_factor,
    process_recalibration_queue,
)

_SIMPLE_CODE = (
    "def factor_program(data, params):\n"
    "    import numpy as np\n"
    "    lookback = int(params.get('lookback', 5))\n"
    "    sig = data['close'].pct_change(lookback).fillna(0.0)\n"
    "    return np.asarray(sig, dtype=float)"
)


def _make_data(n: int = 120, seed: int = 1) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, n))
    df = pd.DataFrame({"close": close}, index=pd.date_range("2024-01-01", periods=n))
    fwd = (close / np.roll(close, 1) - 1.0)  # 简单收益对齐
    fwd = pd.Series(close).pct_change().to_numpy()
    return df, fwd


def _make_factor(factor_id: str = "fct_x", name: str = "x") -> dict:
    return {
        "factor_id": factor_id,
        "name": name,
        "code": _SIMPLE_CODE,
        "params": {"lookback": 5},
        "market": "futures",
    }


@pytest.fixture()
def queue(tmp_path: Path) -> RecalibrationQueue:
    return RecalibrationQueue(tmp_path / "recalibration_queue.json")


class TestQueue:
    def test_enqueue_and_list(self, queue: RecalibrationQueue) -> None:
        assert queue.enqueue("f_a", name="a", reason="decayed") is True
        assert queue.enqueue("f_b") is True
        pending = queue.list_pending()
        assert [i.factor_id for i in pending] == ["f_a", "f_b"]
        assert pending[0].reason == "decayed"
        assert pending[0].status == RecalibrationStatus.PENDING.value

    def test_enqueue_duplicate_pending_rejected(self, queue: RecalibrationQueue) -> None:
        assert queue.enqueue("f_a") is True
        assert queue.enqueue("f_a") is False
        assert len(queue.list_pending()) == 1

    def test_enqueue_after_done_allows_reentry(self, queue: RecalibrationQueue) -> None:
        queue.enqueue("f_a")
        queue.transition("f_a", RecalibrationStatus.DONE.value)
        assert queue.enqueue("f_a", reason="decayed-again") is True

    def test_enqueue_empty_id_rejected(self, queue: RecalibrationQueue) -> None:
        assert queue.enqueue("") is False

    def test_enqueue_max_queue(self, queue: RecalibrationQueue) -> None:
        for i in range(3):
            assert queue.enqueue(f"f_{i}", max_queue=3) is True
        assert queue.enqueue("f_overflow", max_queue=3) is False

    def test_persistence_roundtrip(self, tmp_path: Path) -> None:
        q1 = RecalibrationQueue(tmp_path / "q.json")
        q1.enqueue("f_a", name="a", reason="decayed")
        q1.transition("f_a", RecalibrationStatus.DONE.value, recalibrated_ic=0.12)
        # 重新实例化（模拟跨进程）后仍可读
        q2 = RecalibrationQueue(tmp_path / "q.json")
        items = q2.load()
        assert len(items) == 1
        assert items[0].status == RecalibrationStatus.DONE.value
        assert items[0].recalibrated_ic == 0.12

    def test_corrupt_queue_falls_back_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "q.json"
        p.write_text("{invalid json", encoding="utf-8")
        q = RecalibrationQueue(p)
        assert q.load() == []

    def test_transition_unknown_id(self, queue: RecalibrationQueue) -> None:
        assert queue.transition("nope", RecalibrationStatus.DONE.value) is None

    def test_list_pending_limit(self, queue: RecalibrationQueue) -> None:
        for i in range(5):
            queue.enqueue(f"f_{i}")
        assert len(queue.list_pending(limit=2)) == 2


class TestRecalibrateFactor:
    def test_done_when_improved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data, fwd = _make_data()
        factor = _make_factor()
        monkeypatch.setattr(
            "fts.factor_engine.micro_evolution.optimize_params_staged",
            lambda *a, **k: ({"lookback": 10}, 0.6, True),
        )
        best_params, new_ic, baseline_ic, status = recalibrate_factor(
            factor, data, fwd, RecalibrationConfig(min_ic_gap=0.0)
        )
        assert status == RecalibrationStatus.DONE.value
        assert new_ic == 0.6
        assert best_params == {"lookback": 10}
        assert np.isfinite(baseline_ic)  # 基线为真实执行信号 IC

    def test_skipped_when_no_improvement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data, fwd = _make_data()
        factor = _make_factor()
        monkeypatch.setattr(
            "fts.factor_engine.micro_evolution.optimize_params_staged",
            lambda *a, **k: ({"lookback": 10}, -0.2, True),  # 低于基线
        )
        _, new_ic, _, status = recalibrate_factor(factor, data, fwd)
        assert status == RecalibrationStatus.SKIPPED.value

    def test_failed_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data, fwd = _make_data()
        factor = _make_factor()

        def _boom(*a, **k):
            raise RuntimeError("optuna down")

        monkeypatch.setattr("fts.factor_engine.micro_evolution.optimize_params_staged", _boom)
        _, _, _, status = recalibrate_factor(factor, data, fwd)
        assert status == RecalibrationStatus.FAILED.value


class TestProcessQueue:
    def _setup(self, tmp_path: Path, factor_id: str = "fct_x") -> dict:
        elite = tmp_path / "elite"
        elite.mkdir()
        (elite / "x.json").write_text(json.dumps(_make_factor(factor_id)), encoding="utf-8")
        queue = RecalibrationQueue(tmp_path / "queue.json")
        queue.enqueue(factor_id, name="x", reason="decayed")
        return {"elite": elite, "queue": queue}

    def test_done_writes_elite_and_transitions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env = self._setup(tmp_path)
        data, fwd = _make_data()
        monkeypatch.setattr(
            "fts.factor_engine.micro_evolution.optimize_params_staged",
            lambda *a, **k: ({"lookback": 10}, 0.7, True),
        )
        stats = process_recalibration_queue(
            env["elite"], data, fwd, queue=env["queue"]
        )
        assert stats["processed"] == 1
        assert stats["done"] == 1
        # elite JSON 回写
        saved = json.loads((env["elite"] / "x.json").read_text(encoding="utf-8"))
        assert "recalibrated_at" in saved
        assert "recalibrated_ic" in saved
        assert saved["recalibrated_params"] == {"lookback": 10}
        # 队列状态迁移
        assert env["queue"].load()[0].status == RecalibrationStatus.DONE.value

    def test_skipped_no_elite_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env = self._setup(tmp_path)
        data, fwd = _make_data()
        monkeypatch.setattr(
            "fts.factor_engine.micro_evolution.optimize_params_staged",
            lambda *a, **k: ({"lookback": 10}, -0.3, True),
        )
        stats = process_recalibration_queue(env["elite"], data, fwd, queue=env["queue"])
        assert stats["skipped"] == 1
        saved = json.loads((env["elite"] / "x.json").read_text(encoding="utf-8"))
        assert "recalibrated_at" not in saved
        assert env["queue"].load()[0].status == RecalibrationStatus.SKIPPED.value

    def test_not_found(self, tmp_path: Path) -> None:
        elite = tmp_path / "empty"
        elite.mkdir()
        queue = RecalibrationQueue(tmp_path / "q.json")
        queue.enqueue("ghost")
        data, fwd = _make_data()
        stats = process_recalibration_queue(elite, data, fwd, queue=queue)
        assert stats["not_found"] == 1
        assert stats["processed"] == 0

    def test_dry_run_no_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env = self._setup(tmp_path)
        data, fwd = _make_data()
        monkeypatch.setattr(
            "fts.factor_engine.micro_evolution.optimize_params_staged",
            lambda *a, **k: ({"lookback": 10}, 0.7, True),
        )
        stats = process_recalibration_queue(
            env["elite"], data, fwd, queue=env["queue"], dry_run=True
        )
        assert stats["processed"] == 1
        saved = json.loads((env["elite"] / "x.json").read_text(encoding="utf-8"))
        assert "recalibrated_at" not in saved
        assert env["queue"].load()[0].status == RecalibrationStatus.PENDING.value


class TestFeedbackTrigger:
    def _live_result(self) -> dict:
        return {
            "factors": {
                "f_a": {"ic": 0.01, "n_days": 20, "mean_return": 0.0002},
                "f_b": {"ic": 0.09, "n_days": 20, "mean_return": 0.001},
            },
            "overall_ic": 0.05,
            "n_records": 40,
        }

    def test_enabled_queues_decayed(self, tmp_path: Path) -> None:
        from fts.factor_engine.feedback_loop import LiveVsBacktestICReport

        report = LiveVsBacktestICReport(
            recalibration_enabled=True,
            recalibration_queue_path=str(tmp_path / "queue.json"),
        )
        result = report.generate(
            self._live_result(), backtest_ic_map={"f_a": 0.10, "f_b": 0.05}
        )
        rows = {r["factor_id"]: r for r in result["factors"]}
        # f_a: bt=0.10, live=0.01 → |live| < |bt|-0.02 → decayed → 入队
        assert rows["f_a"]["status"] == "decayed"
        assert rows["f_a"]["recalibration_queued"] is True
        # f_b: bt=0.05, live=0.09 → ok → 不入队
        assert rows["f_b"]["recalibration_queued"] is False

        q = RecalibrationQueue(tmp_path / "queue.json")
        pending = q.list_pending()
        assert [i.factor_id for i in pending] == ["f_a"]
        assert pending[0].reason == "decayed"

    def test_disabled_keeps_behavior(self, tmp_path: Path) -> None:
        from fts.factor_engine.feedback_loop import LiveVsBacktestICReport

        report = LiveVsBacktestICReport(recalibration_enabled=False)
        result = report.generate(
            self._live_result(), backtest_ic_map={"f_a": 0.10}
        )
        row = next(r for r in result["factors"] if r["factor_id"] == "f_a")
        assert row["recommend_retire"] is True  # 既有退役建议不变
        assert row["recalibration_queued"] is False
        # 队列不应存在（默认路径未被触碰）
        assert not (tmp_path / "portfolio" / "recalibration_queue.json").exists()
