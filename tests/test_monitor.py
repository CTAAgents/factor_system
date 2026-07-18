"""
tests/test_monitor.py — FTS 健康监控模块全面测试。

HARNESS §测试随重构: 测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fts.monitor import (
    LoopStatusReport,
    SystemStatusReport,
    _loop_status_to_report,
    check_all_status,
    check_loop_status,
    format_status_report,
    status_report_to_json,
)
from fts.factor_engine.monitor import LoopStatus, AllStatus


# ═══════════════════════════════════════════════════════════
# LoopStatusReport dataclass
# ═══════════════════════════════════════════════════════════

class TestLoopStatusReport:
    """测试 LoopStatusReport dataclass。"""

    def test_all_fields_present(self):
        """所有必需字段都存在。"""
        r = LoopStatusReport(
            loop_name="L1",
            healthy=True,
            last_run_at="2026-07-18T00:00:00",
            status="running",
            last_error=None,
            version="8.10.0",
            run_id="run_abc_20260718",
            tokens_consumed=1500,
            age_hours=2.5,
        )
        assert r.loop_name == "L1"
        assert r.healthy is True
        assert r.last_run_at == "2026-07-18T00:00:00"
        assert r.status == "running"
        assert r.last_error is None
        assert r.version == "8.10.0"
        assert r.run_id == "run_abc_20260718"
        assert r.tokens_consumed == 1500
        assert r.age_hours == 2.5

    def test_default_values(self):
        """默认值正确。"""
        r = LoopStatusReport(loop_name="L2", healthy=False)
        assert r.last_run_at == ""
        assert r.status == "unknown"
        assert r.last_error is None
        # version 从 EVOLUTION_VERSION 来，但不在这里断言具体值
        assert isinstance(r.version, str)
        assert r.run_id == ""
        assert r.tokens_consumed == 0
        assert r.age_hours == 0.0

    def test_different_loop_names(self):
        """支持 L1 / L2 / L3 等不同循环名。"""
        for name in ("L1", "L2", "L3", "meta_loop", "evolution"):
            r = LoopStatusReport(loop_name=name, healthy=True)
            assert r.loop_name == name


# ═══════════════════════════════════════════════════════════
# SystemStatusReport dataclass
# ═══════════════════════════════════════════════════════════

class TestSystemStatusReport:
    """测试 SystemStatusReport dataclass。"""

    def test_all_fields_present(self):
        """所有字段都正确设置。"""
        loops = [
            LoopStatusReport(loop_name="L1", healthy=True),
            LoopStatusReport(loop_name="L2", healthy=True),
        ]
        r = SystemStatusReport(
            healthy=True,
            loops=loops,
            checked_at="2026-07-18T00:00:00",
            fts_version="8.10.0",
            any_circuit_broken=False,
            any_stale=False,
            total_tokens_today=3000,
        )
        assert r.healthy is True
        assert len(r.loops) == 2
        assert r.checked_at == "2026-07-18T00:00:00"
        assert r.fts_version == "8.10.0"
        assert r.any_circuit_broken is False
        assert r.any_stale is False
        assert r.total_tokens_today == 3000

    def test_default_values(self):
        """默认值正确。"""
        r = SystemStatusReport(healthy=False)
        assert r.loops == []
        assert r.checked_at == ""
        assert r.fts_version == ""
        assert r.any_circuit_broken is False
        assert r.any_stale is False
        assert r.total_tokens_today == 0

    def test_loop_default_factory(self):
        """默认 factory 生成空列表。"""
        r = SystemStatusReport(healthy=True)
        assert r.loops == []
        r.loops.append(LoopStatusReport(loop_name="L1", healthy=True))
        assert len(r.loops) == 1


# ═══════════════════════════════════════════════════════════
# _loop_status_to_report()
# ═══════════════════════════════════════════════════════════

class TestLoopStatusToReport:
    """测试 _loop_status_to_report 转换函数。"""

    def test_converts_all_fields(self):
        """LoopStatus 的所有字段正确映射到 LoopStatusReport。"""
        ls = LoopStatus(
            name="L2",
            state_file="/tmp/state.json",
            exists=True,
            run_id="run_abc_20260718",
            status="completed",
            last_updated="2026-07-18T12:00:00",
            tokens_consumed=5000,
            budget_limit=10000,
            last_error=None,
            age_hours=3.2,
            healthy=True,
        )
        r = _loop_status_to_report(ls)
        assert r.loop_name == "L2"
        assert r.healthy is True
        assert r.last_run_at == "2026-07-18T12:00:00"
        assert r.status == "completed"
        assert r.last_error is None
        assert isinstance(r.version, str)
        assert r.run_id == "run_abc_20260718"
        assert r.tokens_consumed == 5000
        assert r.age_hours == 3.2

    def test_handles_edge_values(self):
        """处理空字符串和 None 等边界值。"""
        ls = LoopStatus(
            name="L1",
            state_file="/tmp/state.json",
            exists=False,
            run_id="",
            status="unknown",
            last_updated="",
            tokens_consumed=0,
            budget_limit=0,
            last_error="something broke",
            age_hours=0.0,
            healthy=False,
        )
        r = _loop_status_to_report(ls)
        assert r.loop_name == "L1"
        assert r.healthy is False
        assert r.last_run_at == ""
        assert r.status == "unknown"
        assert r.last_error == "something broke"
        assert r.run_id == ""
        assert r.age_hours == 0.0

    def test_converts_types_correctly(self):
        """类型转换正确（bool → bool, int → int, str → str）。"""
        ls = LoopStatus(
            name="L3",
            state_file="/tmp/state.json",
            exists=True,
            healthy=False,
            tokens_consumed=0,
            budget_limit=0,
            age_hours=0.0,
        )
        r = _loop_status_to_report(ls)
        assert isinstance(r.healthy, bool)
        assert isinstance(r.tokens_consumed, int)
        assert isinstance(r.age_hours, float)
        assert isinstance(r.loop_name, str)
        assert isinstance(r.status, str)


# ═══════════════════════════════════════════════════════════
# check_loop_status()
# ═══════════════════════════════════════════════════════════

class TestCheckLoopStatus:
    """测试 check_loop_status。"""

    @patch("fts.monitor.check_loop")
    def test_valid_loop_name(self, mock_check_loop):
        """有效循环名调用 check_loop 并返回报告。"""
        mock_check_loop.return_value = LoopStatus(
            name="L1",
            state_file="/tmp/memory/meta_loop/state.json",
            exists=True,
            run_id="run_abc",
            status="running",
            last_updated="2026-07-18T00:00:00",
            tokens_consumed=100,
            budget_limit=500,
            last_error=None,
            age_hours=0.5,
            healthy=True,
        )
        r = check_loop_status("L1")
        assert r.loop_name == "L1"
        assert r.healthy is True
        assert r.status == "running"
        assert r.run_id == "run_abc"
        mock_check_loop.assert_called_once()

    def test_invalid_loop_name(self):
        """无效循环名返回 error 报告。"""
        r = check_loop_status("INVALID")
        assert r.loop_name == "INVALID"
        assert r.healthy is False
        assert r.status == "unknown"
        assert r.last_error is not None
        assert "unknown loop name" in r.last_error

    @patch("fts.monitor.check_loop")
    def test_unhealthy_loop(self, mock_check_loop):
        """不健康的循环反映在报告中。"""
        mock_check_loop.return_value = LoopStatus(
            name="L2",
            state_file="/tmp/state.json",
            exists=True,
            status="circuit_broken",
            last_error="Budget exhausted",
            healthy=False,
            tokens_consumed=0,
            budget_limit=0,
            age_hours=48.0,
        )
        r = check_loop_status("L2")
        assert r.healthy is False
        assert r.status == "circuit_broken"
        assert r.last_error == "Budget exhausted"

    @patch("fts.monitor.check_loop", side_effect=ValueError("bad state file"))
    def test_check_loop_raises(self, mock_check_loop):
        """底层 check_loop 抛出异常时返回 error 报告。"""
        r = check_loop_status("L1")
        assert r.healthy is False
        assert r.status == "error"
        assert r.last_error == "bad state file"

    @patch("fts.monitor.check_loop")
    def test_with_project_root(self, mock_check_loop):
        """传入 project_root 参数。"""
        mock_check_loop.return_value = LoopStatus(
            name="L3", state_file="/tmp/state.json", exists=True,
            healthy=True, tokens_consumed=0, budget_limit=0, age_hours=0.0,
        )
        r = check_loop_status("L3", project_root=Path("/custom/root"))
        assert r.loop_name == "L3"
        assert r.healthy is True
        mock_check_loop.assert_called_once()

    @pytest.mark.parametrize("loop_name", ["L1", "L2", "L3"])
    @patch("fts.monitor.check_loop")
    def test_all_valid_loop_names(self, mock_check_loop, loop_name):
        """L1/L2/L3 都是有效循环名。"""
        mock_check_loop.return_value = LoopStatus(
            name=loop_name, state_file="/tmp/state.json", exists=True,
            healthy=True, tokens_consumed=0, budget_limit=0, age_hours=0.0,
        )
        r = check_loop_status(loop_name)
        assert r.loop_name == loop_name
        assert r.healthy is True

    @pytest.mark.parametrize("alias", ["meta_loop", "evolution", "portfolio"])
    @patch("fts.monitor.check_loop")
    def test_aliases(self, mock_check_loop, alias):
        """别名映射有效。"""
        mock_check_loop.return_value = LoopStatus(
            name=alias, state_file="/tmp/state.json", exists=True,
            healthy=True, tokens_consumed=0, budget_limit=0, age_hours=0.0,
        )
        r = check_loop_status(alias)
        assert r.loop_name == alias
        assert r.healthy is True


# ═══════════════════════════════════════════════════════════
# check_all_status()
# ═══════════════════════════════════════════════════════════

class TestCheckAllStatus:
    """测试 check_all_status。"""

    @patch("fts.monitor.check_all")
    def test_all_healthy(self, mock_check_all):
        """所有循环健康时返回 healthy=True。"""
        mock_check_all.return_value = AllStatus(
            loops=[
                LoopStatus(name="L1", state_file="/s1.json", exists=True,
                           healthy=True, tokens_consumed=0, budget_limit=0, age_hours=1.0),
                LoopStatus(name="L2", state_file="/s2.json", exists=True,
                           healthy=True, tokens_consumed=0, budget_limit=0, age_hours=1.0),
                LoopStatus(name="L3", state_file="/s3.json", exists=True,
                           healthy=True, tokens_consumed=0, budget_limit=0, age_hours=1.0),
            ],
            any_circuit_broken=False,
            any_stale=False,
            total_tokens_today=500,
            checked_at="2026-07-18T00:00:00",
        )
        r = check_all_status()
        assert r.healthy is True
        assert len(r.loops) == 3
        assert r.total_tokens_today == 500
        assert r.any_circuit_broken is False
        assert r.any_stale is False
        assert r.fts_version != ""

    @patch("fts.monitor.check_all")
    def test_one_unhealthy(self, mock_check_all):
        """任一循环不健康时 healthy=False。"""
        mock_check_all.return_value = AllStatus(
            loops=[
                LoopStatus(name="L1", state_file="/s1.json", exists=True,
                           healthy=True, tokens_consumed=0, budget_limit=0, age_hours=1.0),
                LoopStatus(name="L2", state_file="/s2.json", exists=True,
                           healthy=False, status="circuit_broken",
                           tokens_consumed=0, budget_limit=0, age_hours=1.0,
                           last_error="OOM"),
                LoopStatus(name="L3", state_file="/s3.json", exists=True,
                           healthy=True, tokens_consumed=0, budget_limit=0, age_hours=1.0),
            ],
            any_circuit_broken=True,
            any_stale=False,
            total_tokens_today=300,
            checked_at="2026-07-18T00:00:00",
        )
        r = check_all_status()
        assert r.healthy is False
        assert r.any_circuit_broken is True

    @patch("fts.monitor.check_all")
    def test_empty_loops(self, mock_check_all):
        """无循环时 healthy 为 falsy。"""
        mock_check_all.return_value = AllStatus(
            loops=[],
            any_circuit_broken=False,
            any_stale=False,
            total_tokens_today=0,
            checked_at="2026-07-18T00:00:00",
        )
        r = check_all_status()
        assert not r.healthy
        assert len(r.loops) == 0

    @patch("fts.monitor.check_all", side_effect=RuntimeError("check_all failed"))
    def test_check_all_raises(self, mock_check_all):
        """底层 check_all 抛出异常时返回 failure 报告。"""
        r = check_all_status()
        assert r.healthy is False
        assert r.loops == []
        assert r.fts_version != ""

    @patch("fts.monitor.check_all")
    def test_all_stale(self, mock_check_all):
        """所有循环超时。"""
        mock_check_all.return_value = AllStatus(
            loops=[
                LoopStatus(name="L1", state_file="/s1.json", exists=True,
                           healthy=False, tokens_consumed=0, budget_limit=0, age_hours=48.0),
                LoopStatus(name="L2", state_file="/s2.json", exists=True,
                           healthy=False, tokens_consumed=0, budget_limit=0, age_hours=49.0),
            ],
            any_circuit_broken=False,
            any_stale=True,
            total_tokens_today=0,
            checked_at="2026-07-18T00:00:00",
        )
        r = check_all_status()
        assert r.healthy is False
        assert r.any_stale is True

    @patch("fts.monitor.check_all")
    def test_with_project_root(self, mock_check_all):
        """传入 project_root 参数。"""
        mock_check_all.return_value = AllStatus(
            loops=[], any_circuit_broken=False, any_stale=False,
            total_tokens_today=0, checked_at="",
        )
        r = check_all_status(project_root=Path("/custom/root"))
        assert not r.healthy
        mock_check_all.assert_called_once_with(Path("/custom/root"))

    @patch("fts.monitor.check_all")
    def test_propagates_circuit_broken_and_stale_flags(self, mock_check_all):
        """熔断和过期标志正确传递。"""
        mock_check_all.return_value = AllStatus(
            loops=[
                LoopStatus(name="L1", state_file="/s1.json", exists=True,
                           healthy=True, tokens_consumed=0, budget_limit=0, age_hours=1.0),
            ],
            any_circuit_broken=True,
            any_stale=True,
            total_tokens_today=100,
            checked_at="2026-07-18T12:00:00",
        )
        r = check_all_status()
        assert r.any_circuit_broken is True
        assert r.any_stale is True


# ═══════════════════════════════════════════════════════════
# format_status_report()
# ═══════════════════════════════════════════════════════════

class TestFormatStatusReport:
    """测试 format_status_report。"""

    def test_healthy_report(self):
        """健康报告包含 'YES'。"""
        r = SystemStatusReport(
            healthy=True,
            loops=[
                LoopStatusReport(loop_name="L1", healthy=True),
            ],
            checked_at="2026-07-18T00:00:00",
            fts_version="8.10.0",
        )
        output = format_status_report(r)
        assert "Overall healthy : YES" in output
        assert "L1" in output
        assert "[OK]" in output

    def test_unhealthy_report(self):
        """不健康报告包含 'NO'。"""
        r = SystemStatusReport(
            healthy=False,
            loops=[
                LoopStatusReport(loop_name="L2", healthy=False, status="circuit_broken"),
            ],
            checked_at="2026-07-18T00:00:00",
            fts_version="8.10.0",
            any_circuit_broken=True,
        )
        output = format_status_report(r)
        assert "NO" in output  # healthy=NO
        assert "[FAIL]" in output

    def test_empty_loops(self):
        """无循环时显示 '(no loop status available)'。"""
        r = SystemStatusReport(
            healthy=False,
            loops=[],
        )
        output = format_status_report(r)
        assert "(no loop status available)" in output

    def test_loop_with_error(self):
        """循环有错误时包含错误消息。"""
        r = SystemStatusReport(
            healthy=False,
            loops=[
                LoopStatusReport(
                    loop_name="L2", healthy=False,
                    last_error="Budget exhausted",
                ),
            ],
        )
        output = format_status_report(r)
        assert "error: Budget exhausted" in output

    def test_circuit_broken_flag(self):
        """熔断标志显示 YES。"""
        r = SystemStatusReport(
            healthy=False,
            loops=[],
            any_circuit_broken=True,
        )
        output = format_status_report(r)
        assert "Circuit broken  : YES" in output

    def test_stale_flag(self):
        """过期标志显示 YES。"""
        r = SystemStatusReport(
            healthy=False,
            loops=[],
            any_stale=True,
        )
        output = format_status_report(r)
        assert "Stale (>24h)    : YES" in output

    def test_all_flags_no(self):
        """无熔断、无过期时显示 NO。"""
        r = SystemStatusReport(
            healthy=True,
            loops=[],
            any_circuit_broken=False,
            any_stale=False,
        )
        output = format_status_report(r)
        assert "Circuit broken  : NO" in output
        assert "Stale (>24h)    : NO" in output

    def test_run_id_display(self):
        """run_id 显示为 '-' 当为空时。"""
        r = SystemStatusReport(
            healthy=True,
            loops=[LoopStatusReport(loop_name="L1", healthy=True, run_id="")],
        )
        output = format_status_report(r)
        assert "run_id=-" in output

    def test_run_id_present(self):
        """run_id 非空时显示实际值。"""
        r = SystemStatusReport(
            healthy=True,
            loops=[LoopStatusReport(loop_name="L1", healthy=True, run_id="run_abc")],
        )
        output = format_status_report(r)
        assert "run_id=run_abc" in output

    def test_multiple_loops(self):
        """多个循环全部显示。"""
        loops = [
            LoopStatusReport(loop_name="L1", healthy=True, age_hours=1.0),
            LoopStatusReport(loop_name="L2", healthy=False, age_hours=2.5),
            LoopStatusReport(loop_name="L3", healthy=True, age_hours=0.5),
        ]
        r = SystemStatusReport(healthy=False, loops=loops)
        output = format_status_report(r)
        for l in loops:
            assert l.loop_name in output

    def test_report_structure(self):
        """报告包含所有节标题。"""
        r = SystemStatusReport(healthy=True, loops=[])
        output = format_status_report(r)
        assert "=== FTS System Status ===" in output
        assert "=== Loop Status ===" in output
        assert "Overall healthy" in output
        assert "Checked at" in output
        assert "FTS version" in output


# ═══════════════════════════════════════════════════════════
# status_report_to_json()
# ═══════════════════════════════════════════════════════════

class TestStatusReportToJson:
    """测试 status_report_to_json。"""

    def test_returns_valid_json(self):
        """返回有效的 JSON 字符串。"""
        r = SystemStatusReport(
            healthy=True,
            loops=[LoopStatusReport(loop_name="L1", healthy=True)],
            checked_at="2026-07-18T00:00:00",
            fts_version="8.10.0",
            any_circuit_broken=False,
            any_stale=False,
            total_tokens_today=500,
        )
        output = status_report_to_json(r)
        parsed = json.loads(output)
        assert parsed["healthy"] is True
        assert len(parsed["loops"]) == 1
        assert parsed["loops"][0]["loop_name"] == "L1"
        assert parsed["checked_at"] == "2026-07-18T00:00:00"
        assert parsed["fts_version"] == "8.10.0"
        assert parsed["total_tokens_today"] == 500

    def test_empty_report(self):
        """空报告也能序列化。"""
        r = SystemStatusReport(healthy=False)
        output = status_report_to_json(r)
        parsed = json.loads(output)
        assert parsed["healthy"] is False
        assert parsed["loops"] == []
        assert parsed["checked_at"] == ""

    def test_has_ensure_ascii_false(self):
        """包含中文时能正确处理。"""
        r = SystemStatusReport(
            healthy=True,
            fts_version="8.10.0",
        )
        output = status_report_to_json(r)
        # 确保输出不包含 \\u 转义
        assert "\\u" not in output

    def test_loop_with_none_last_error(self):
        """last_error 为 None 时正确序列化为 null。"""
        r = SystemStatusReport(
            healthy=True,
            loops=[LoopStatusReport(loop_name="L1", healthy=True, last_error=None)],
        )
        output = status_report_to_json(r)
        assert '"last_error": null' in output

    def test_loop_with_error_message(self):
        """last_error 非空时正确序列化。"""
        r = SystemStatusReport(
            healthy=False,
            loops=[LoopStatusReport(loop_name="L1", healthy=False, last_error="OOM")],
        )
        output = status_report_to_json(r)
        parsed = json.loads(output)
        assert parsed["loops"][0]["last_error"] == "OOM"

    def test_uses_asdict_structure(self):
        """输出结构匹配 dataclass asdict 的结构。"""
        r = SystemStatusReport(
            healthy=True,
            loops=[LoopStatusReport(loop_name="L1", healthy=True, tokens_consumed=100)],
            total_tokens_today=100,
        )
        expected_keys = {"healthy", "loops", "checked_at", "fts_version",
                         "any_circuit_broken", "any_stale", "total_tokens_today"}
        output = json.loads(status_report_to_json(r))
        assert set(output.keys()) == expected_keys


# ═══════════════════════════════════════════════════════════
# 集成场景：format 与 json 保持一致
# ═══════════════════════════════════════════════════════════

class TestIntegration:
    """集成测试：确保各部分配合正确。"""

    @patch("fts.monitor.check_loop")
    def test_check_loop_status_then_report(self, mock_check_loop):
        """check_loop_status 的结果可以被 format 函数处理。"""
        mock_check_loop.return_value = LoopStatus(
            name="L1", state_file="/tmp/s.json", exists=True,
            run_id="run_abc", status="running",
            last_updated="2026-07-18T00:00:00",
            tokens_consumed=100, budget_limit=500,
            last_error=None, age_hours=0.5, healthy=True,
        )
        report = check_loop_status("L1")
        # 手动构建 SystemStatusReport
        sys_report = SystemStatusReport(
            healthy=True,
            loops=[report],
            checked_at="2026-07-18T00:00:00",
            fts_version="8.10.0",
        )
        output = format_status_report(sys_report)
        assert "L1" in output
        assert "run_abc" in output
        assert "YES" in output

    @patch("fts.monitor.check_all")
    def test_check_all_then_json(self, mock_check_all):
        """check_all_status 的结果可序列化为 JSON。"""
        mock_check_all.return_value = AllStatus(
            loops=[
                LoopStatus(name="L1", state_file="/s1.json", exists=True,
                           healthy=True, tokens_consumed=0, budget_limit=0, age_hours=1.0),
            ],
            any_circuit_broken=False,
            any_stale=False,
            total_tokens_today=100,
            checked_at="2026-07-18T00:00:00",
        )
        report = check_all_status()
        output = status_report_to_json(report)
        parsed = json.loads(output)
        assert parsed["healthy"] is True
        assert len(parsed["loops"]) == 1
