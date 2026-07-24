"""
tests/factor_engine/test_monitor.py — Loop Engineering 监控状态查询测试

覆盖范围:
    - check_loop 检查存在的/不存在的目录
    - check_loop 各种运行状态（running、paused、completed、circuit_broken）
    - check_loop 异常处理（无效 last_updated）
    - check_all 汇总多个检查结果
    - check_all 熔断/过期检测
    - LoopStatus / AllStatus 数据类创建

版本: v1.1.0（与 FTS 同步）
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.monitor import AllStatus, LoopStatus, check_all, check_loop


# ─── 辅助函数 ────────────────────────────────────────

def write_state(dir_path: Path, overrides: dict | None = None) -> Path:
    """在 dir_path 下创建 state.json。"""
    dir_path.mkdir(parents=True, exist_ok=True)
    state: dict = {
        "run_id": "run-001",
        "status": "completed",
        "last_updated": datetime.now().isoformat(),
        "tokens_consumed": 1500,
        "budget_limit": 5000,
        "last_error": None,
    }
    if overrides:
        state.update(overrides)
    p = dir_path / "state.json"
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return p


# ─── check_loop 测试 ────────────────────────────────────

class TestCheckLoop:
    """check_loop 函数的单元测试。"""

    def test_existing_dir(self, tmp_path: Path) -> None:
        """存在的目录 + 有效状态文件 → 正确解析所有字段。"""
        d = tmp_path / "evolution"
        write_state(d)

        status = check_loop("L2", d)

        assert status.name == "L2"
        assert status.exists is True
        assert status.run_id == "run-001"
        assert status.status == "completed"
        assert status.tokens_consumed == 1500
        assert status.budget_limit == 5000
        assert status.last_error is None
        assert status.healthy is True
        assert status.age_hours < 0.1  # 刚写入，几乎 0

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        """不存在的目录 → exists=False，其余默认值。"""
        d = tmp_path / "nonexistent"

        status = check_loop("L1", d)

        assert status.name == "L1"
        assert status.exists is False
        assert status.run_id == ""
        assert status.status == "unknown"
        assert status.tokens_consumed == 0
        assert status.budget_limit == 0
        assert status.last_error is None
        assert status.age_hours == 0.0
        assert status.healthy is True  # unknown 且 age=0 → healthy

    def test_existing_dir_no_state_file(self, tmp_path: Path) -> None:
        """目录存在但无 state.json → exists=False。"""
        d = tmp_path / "evolution"
        d.mkdir(parents=True, exist_ok=True)

        status = check_loop("L2", d)

        assert status.name == "L2"
        assert status.exists is False
        assert status.status == "unknown"
        assert status.age_hours == 0.0

    def test_circuit_broken_status(self, tmp_path: Path) -> None:
        """circuit_broken 状态 → healthy=False。"""
        d = tmp_path / "meta_loop"
        write_state(d, {"status": "circuit_broken", "last_error": "Budget exceeded"})

        status = check_loop("L1", d)

        assert status.status == "circuit_broken"
        assert status.healthy is False
        assert status.last_error == "Budget exceeded"

    def test_stale_status(self, tmp_path: Path) -> None:
        """超过 24h 未更新 → healthy=False。"""
        d = tmp_path / "evolution"
        stale_time = (datetime.now() - timedelta(hours=48)).isoformat()
        write_state(d, {"last_updated": stale_time})

        status = check_loop("L2", d, max_stale_hours=24.0)

        assert status.healthy is False
        assert status.age_hours >= 48.0
        assert status.exists is True

    def test_invalid_last_updated(self, tmp_path: Path) -> None:
        """last_updated 格式无效 → age_hours 为 0.0，不抛出异常。"""
        d = tmp_path / "evolution"
        write_state(d, {"last_updated": "not-a-date"})

        status = check_loop("L2", d)

        assert status.age_hours == 0.0
        assert status.status == "completed"

    def test_empty_last_updated(self, tmp_path: Path) -> None:
        """last_updated 为空字符串 → age_hours 为 0.0。"""
        d = tmp_path / "evolution"
        write_state(d, {"last_updated": ""})

        status = check_loop("L2", d)

        assert status.age_hours == 0.0

    def test_paused_status(self, tmp_path: Path) -> None:
        """paused 状态 → healthy=True。"""
        d = tmp_path / "portfolio"
        write_state(d, {"status": "paused"})

        status = check_loop("L3", d)

        assert status.status == "paused"
        assert status.healthy is True

    def test_running_status(self, tmp_path: Path) -> None:
        """running 状态 → healthy=True。"""
        d = tmp_path / "meta_loop"
        write_state(d, {"status": "running"})

        status = check_loop("L1", d)

        assert status.status == "running"
        assert status.healthy is True

    def test_custom_max_stale_hours(self, tmp_path: Path) -> None:
        """自定义 max_stale_hours 阈值。"""
        d = tmp_path / "evolution"
        stale_time = (datetime.now() - timedelta(hours=2)).isoformat()
        write_state(d, {"last_updated": stale_time})

        # 2小时更新在默认24小时内 → healthy
        status_default = check_loop("L2", d, max_stale_hours=24.0)
        assert status_default.healthy is True

        # 2小时更新超过1小时阈值 → not healthy
        status_strict = check_loop("L2", d, max_stale_hours=1.0)
        assert status_strict.healthy is False

    def test_state_file_resolved_path(self, tmp_path: Path) -> None:
        """state_file 返回 resolved 绝对路径。"""
        d = tmp_path / "evolution"
        write_state(d)

        status = check_loop("L2", d)

        expected = str((d / "state.json").resolve())
        assert status.state_file == expected

    def test_invalid_json_state(self, tmp_path: Path) -> None:
        """state.json 内容不是合法 JSON → 当作空字典处理。"""
        d = tmp_path / "evolution"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text("not json", encoding="utf-8")

        status = check_loop("L2", d)

        # read_state 返回 {}，所以所有字段都是默认值
        assert status.exists is True  # 文件物理上存在
        assert status.status == "unknown"
        assert status.run_id == ""
        assert status.tokens_consumed == 0
        assert status.age_hours == 0.0
        assert status.healthy is True


# ─── check_all 测试 ────────────────────────────────────

class TestCheckAll:
    """check_all 函数的单元测试。"""

    def test_all_dirs_exist(self, tmp_path: Path) -> None:
        """三个子目录都有 state.json → 返回三个 LoopStatus。"""
        root = tmp_path
        for sub in ("meta_loop", "evolution", "portfolio"):
            write_state(root / "memory" / sub)

        status = check_all(root)

        assert len(status.loops) == 3
        assert all(l.exists for l in status.loops)
        assert [l.name for l in status.loops] == ["L1", "L2", "L3"]
        assert status.any_circuit_broken is False
        assert status.any_stale is False
        assert status.total_tokens_today == 1500 * 3
        assert status.checked_at != ""

    def test_some_dirs_missing(self, tmp_path: Path) -> None:
        """部分目录缺失 → 对应 loop exists=False。"""
        root = tmp_path
        write_state(root / "memory" / "meta_loop")
        # evolution 和 portfolio 不存在

        status = check_all(root)

        assert len(status.loops) == 3
        assert status.loops[0].exists is True   # L1
        assert status.loops[1].exists is False  # L2
        assert status.loops[2].exists is False  # L3

    def test_circuit_broken_detected(self, tmp_path: Path) -> None:
        """任意一层熔断 → any_circuit_broken=True。"""
        root = tmp_path
        write_state(root / "memory" / "meta_loop", {"status": "completed"})
        write_state(root / "memory" / "evolution", {"status": "circuit_broken"})
        write_state(root / "memory" / "portfolio", {"status": "completed"})

        status = check_all(root)

        assert status.any_circuit_broken is True
        assert status.loops[1].status == "circuit_broken"

    def test_stale_detected(self, tmp_path: Path) -> None:
        """任意一层过时 → any_stale=True。"""
        root = tmp_path
        stale_time = (datetime.now() - timedelta(hours=48)).isoformat()
        write_state(root / "memory" / "meta_loop", {"last_updated": stale_time})
        write_state(root / "memory" / "evolution", {"status": "completed"})
        write_state(root / "memory" / "portfolio", {"status": "completed"})

        status = check_all(root)

        assert status.any_stale is True

    def test_nonexistent_stale_not_counted(self, tmp_path: Path) -> None:
        """不存在的目录不计入 any_stale。"""
        root = tmp_path
        # 所有目录都不存在 → age_hours=0.0，但 exists=False，不计入 any_stale
        # 不需要写入任何文件

        status = check_all(root)

        assert status.any_stale is False
        assert all(l.exists is False for l in status.loops)

    def test_empty_root_default(self, tmp_path: Path) -> None:
        """fdt_root 为空字符串 → 使用当前工作目录。"""
        # 在 tmp_path 内运行，确保不会意外读取真实目录
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(tmp_path)

            status = check_all("")
            assert len(status.loops) == 3
            assert all(l.exists is False for l in status.loops)
        finally:
            os.chdir(original_cwd)

    def test_total_tokens_sum(self, tmp_path: Path) -> None:
        """total_tokens_today 为三个 loop 的 tokens_consumed 之和。"""
        root = tmp_path
        write_state(root / "memory" / "meta_loop", {"tokens_consumed": 100})
        write_state(root / "memory" / "evolution", {"tokens_consumed": 200})
        write_state(root / "memory" / "portfolio", {"tokens_consumed": 300})

        status = check_all(root)

        assert status.total_tokens_today == 600


# ─── Dataclass 创建测试 ─────────────────────────────────

class TestDataclassCreation:
    """LoopStatus 和 AllStatus 数据类创建路径。"""

    def test_loop_status_defaults(self) -> None:
        """LoopStatus 使用最少参数创建。"""
        s = LoopStatus(name="L1", state_file="/path/to/state.json", exists=True)
        assert s.name == "L1"
        assert s.run_id == ""
        assert s.status == "unknown"
        assert s.tokens_consumed == 0
        assert s.budget_limit == 0
        assert s.last_error is None
        assert s.age_hours == 0.0
        assert s.healthy is True

    def test_loop_status_full(self) -> None:
        """LoopStatus 使用全部参数创建。"""
        s = LoopStatus(
            name="L2",
            state_file="/path/state.json",
            exists=True,
            run_id="run-999",
            status="running",
            last_updated="2026-07-18T10:00:00",
            tokens_consumed=3000,
            budget_limit=10000,
            last_error=None,
            age_hours=1.5,
            healthy=True,
        )
        assert s.name == "L2"
        assert s.run_id == "run-999"
        assert s.age_hours == 1.5

    def test_all_status_defaults(self) -> None:
        """AllStatus 使用默认工厂创建。"""
        s = AllStatus()
        assert s.loops == []
        assert s.any_circuit_broken is False
        assert s.any_stale is False
        assert s.total_tokens_today == 0
        assert s.checked_at == ""

    def test_all_status_with_loops(self) -> None:
        """AllStatus 包含 LoopStatus 列表。"""
        loops = [
            LoopStatus(name="L1", state_file="/a", exists=True),
            LoopStatus(name="L2", state_file="/b", exists=False),
        ]
        s = AllStatus(
            loops=loops,
            any_circuit_broken=True,
            any_stale=False,
            total_tokens_today=500,
            checked_at="2026-07-18T12:00:00",
        )
        assert len(s.loops) == 2
        assert s.any_circuit_broken is True
        assert s.total_tokens_today == 500
