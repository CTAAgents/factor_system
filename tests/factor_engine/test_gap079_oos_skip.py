"""tests/factor_engine/test_gap079_oos_skip.py — GAP-079 oos 误杀路径修复测试（GAP-121 语义反转后保留）。

背景（plans/26-phase0-audit-breakdown.md）：1073 条 audit_fail 轨迹中 99.4% 由
oos_consistency 导致，其中 90%（964 条）WalkForward 实际完成窗口数 = 0——
"无法验证"而非"验证失败"却被判 failed。根因：`_run_factor_audit` 在评估链走航
窗口不足（n_windows_completed<2）且独立走航失败（数据不足/force_walkforward=false）
时回退 L1 icir 兜底（无 n_windows_completed 键），未命中 GAP-073 的
`n_windows<2 → skipped` 分支。

v2.104.0+44（GAP-121 评估链修复）：判定口径再反转——0/1 窗口即"样本外验证
缺失"，mark 为 failed 由晋升审计硬门拦截（原先 skipped 放行导致单一 OOS 切片
因子全部入库，能源链 64 因子 OOS 全链路失效根因之一）。独立走航成功优先与
真实拦截保留两条路径不受影响。

覆盖:
    1. 核心修复: 评估链走航 0 窗口 + 独立走航失败 → oos_consistency failed（GAP-121 硬拦截）
    2. 独立走航成功优先: 评估链走航 0 窗口 + 独立走航 n_windows=2 → oos passed
    3. 真实拦截保留: 评估链走航 2 窗口低一致性 → oos failed（不放松真实 OOS 拦截）
    4. L1 兜底保留: evaluation 无 walk_forward → L1 icir 兜底原判定
    5. 方法级补强: _check_oos_consistency 对 n_windows_completed=0 → failed
    6. 方法级补强: _check_oos_consistency 对 n_windows_completed=1 → failed
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fts.factor_engine.audit import FactorAuditor
from fts.factor_engine.contracts import FactorEvaluation


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)


def _factor_code() -> str:
    return (
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    close = data['close']\n"
        "    n = len(close)\n"
        "    ret = np.zeros(n)\n"
        "    if n > 5:\n"
        "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
        "    return np.tanh(ret * 10)\n"
    )


def _make_factor() -> dict:
    return {
        "factor_id": "fct_gap078",
        "name": "gap078_test",
        "code": _factor_code(),
        "params": {},
    }


def _make_evaluation() -> dict:
    """基础评估：带 L1 兜底触发条件（oos_ratio>0, icir=1.2），无 walk_forward。"""
    return dict(
        FactorEvaluation(
            factor_id="fct_gap078",
            trace_id="trace_gap078",
            passed=True,
            failure_reasons=[],
            level_1_backtest={"ic": 0.05, "sharpe": 1.5, "oos_ratio": 0.3, "icir": 1.2},
            evaluated_at="2026-08-11T00:00:00",
        )
    )


def _oos_item(report) -> object:
    """从审计报告中提取 oos_consistency 项。"""
    return [it for it in report.items if it.name == "oos_consistency"][0]


class TestGap078OosSkip:
    """GAP-078: _run_factor_audit oos_result 构造路径修复。"""

    def test_chain_wf_zero_windows_hard_gate(self, minimal_loop, sample_dataframe):
        """核心修复: 评估链走航 0 窗口 + 独立走航失败 → oos_consistency failed。

        修复前（GAP-079 skipped 放行）: L1 icir 兜底（icir=0.3 < 0.5 → failed）；
        GAP-079 改 skipped；v2.104.0+44（GAP-121）再反转：0 窗口=样本外验证缺失，
        显式 n_windows_completed=0 → 硬拦截 failed，由晋升审计硬门拒绝入库。
        """
        minimal_loop.data = sample_dataframe
        minimal_loop._run_walkforward_oos = MagicMock(return_value=None)
        ev = _make_evaluation()
        # 低 icir 使 L1 兜底判 failed，复现真实误杀路径（修复前行为）
        ev["level_1_backtest"] = {"ic": 0.05, "sharpe": 1.5, "oos_ratio": 0.3, "icir": 0.3}
        ev["walk_forward"] = {
            "windows": [],
            "ic_consistency": 0.0,
            "ic_volatility": 0.0,
            "sharpe_volatility": 0.0,
            "consistency_score": 0.0,
            "passed": False,
            "n_windows_completed": 0,
        }

        report = minimal_loop._run_factor_audit(_make_factor(), ev, "trace_gap078")

        item = _oos_item(report)
        assert item.status == "failed", f"GAP-121 硬拦截 0 窗口（样本外验证缺失），实际: {item.evidence}"

    def test_chain_wf_zero_windows_independent_wf_succeeds(self, minimal_loop, sample_dataframe):
        """独立走航成功优先: 评估链走航 0 窗口 + 独立走航 n_windows=2 → oos passed。"""
        minimal_loop.data = sample_dataframe
        minimal_loop._run_walkforward_oos = MagicMock(
            return_value={
                "ic_consistency": 0.6,
                "passed": True,
                "windows": [],
                "n_windows_completed": 2,
            }
        )
        ev = _make_evaluation()
        ev["walk_forward"] = {
            "windows": [],
            "ic_consistency": 0.0,
            "passed": False,
            "n_windows_completed": 0,
        }

        report = minimal_loop._run_factor_audit(_make_factor(), ev, "trace_gap078")

        item = _oos_item(report)
        assert item.status == "passed"
        minimal_loop._run_walkforward_oos.assert_called_once()

    def test_chain_wf_two_windows_low_consistency_still_fails(self, minimal_loop, sample_dataframe):
        """真实拦截保留: 评估链走航 2 窗口低一致性 → oos failed（不放松）。"""
        minimal_loop.data = sample_dataframe
        minimal_loop._run_walkforward_oos = MagicMock(return_value=None)
        ev = _make_evaluation()
        ev["walk_forward"] = {
            "windows": [],
            "ic_consistency": 0.2,
            "passed": False,
            "n_windows_completed": 2,
        }

        report = minimal_loop._run_factor_audit(_make_factor(), ev, "trace_gap078")

        item = _oos_item(report)
        assert item.status == "failed"

    def test_chain_wf_missing_keeps_l1_fallback(self, minimal_loop, sample_dataframe):
        """GAP-073 逻辑不变: evaluation 无 walk_forward → L1 icir 兜底原判定（passed）。"""
        minimal_loop.data = sample_dataframe
        minimal_loop._run_walkforward_oos = MagicMock(return_value=None)
        ev = _make_evaluation()  # 无 walk_forward 字段

        report = minimal_loop._run_factor_audit(_make_factor(), ev, "trace_gap078")

        item = _oos_item(report)
        # icir=1.2 → ic_consistency=min(1.0, 1.2)=1.0 ≥ 0.5 → passed
        assert item.status == "passed"


class TestCheckOosZeroWindows:
    """方法级补强: _check_oos_consistency 对不足窗口的判定。"""

    def test_check_oos_failed_when_zero_windows(self):
        """GAP-121 语义覆盖: n_windows_completed=0 → failed（样本外验证缺失，硬拦截）。"""
        auditor = FactorAuditor()
        item = auditor._check_oos_consistency({"ic_consistency": 0.0, "passed": False, "n_windows_completed": 0})
        assert item.status == "failed"

    def test_check_oos_failed_when_one_window(self):
        """GAP-121: n_windows_completed=1 → failed（单窗口无法做跨窗口一致性验证，硬拦截）。"""
        auditor = FactorAuditor()
        item = auditor._check_oos_consistency({"ic_consistency": 0.0, "passed": False, "n_windows_completed": 1})
        assert item.status == "failed"
