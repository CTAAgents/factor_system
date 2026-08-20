"""tests/scripts/test_rhi_global_setup.py — RHI 自进化脚本（scripts/rhi_global_setup.py）单元测试。

覆盖: _score_claude 四维评分全分支（含 RHI 自指章节排除）、_improvement_rate、
cmd_init / cmd_step 核心状态机（.rhi/history.json 读写、收敛判定）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import rhi_global_setup  # noqa: E402


# ─── _score_claude: memory_coverage ───

def _write_claude(tmp_path: Path, content: str) -> Path:
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(content, encoding="utf-8")
    return claude


def test_score_memory_coverage_none(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n仅规则说明，不含特定引用")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["memory_coverage"] == 0.0


def test_score_memory_coverage_partial(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\nmemory 目录用于状态持久化")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["memory_coverage"] == 0.5


def test_score_memory_coverage_full(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\nmemory 体系 + D:\\Knowledge 知识库")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["memory_coverage"] == 1.0


# ─── _score_claude: rule_completeness ───

def test_score_rule_completeness_none(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n纯规则描述")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["rule_completeness"] == 0.0


def test_score_rule_completeness_check_only(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n13 项检查清单，无其他")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["rule_completeness"] == 0.4


def test_score_rule_completeness_check_and_anti(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n13 项检查清单 + 反模式 AP01")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["rule_completeness"] == 0.7


def test_score_rule_completeness_full(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n13 项检查清单 + 反模式 AP01 + verify_doc_consistency")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["rule_completeness"] == 1.0


# ─── _score_claude: consistency ───

def test_score_consistency_partial(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\ndocs/harness 文档体系")
    assert rhi_global_setup._score_claude(claude)["breakdown"]["consistency"] == 0.4


def test_score_consistency_full(tmp_path: Path) -> None:
    claude = _write_claude(
        tmp_path, "# 测试\ndocs/harness 文档体系 + trace_id 全链路 + 08-gap-analysis"
    )
    assert rhi_global_setup._score_claude(claude)["breakdown"]["consistency"] == 1.0


# ─── _score_claude: clarity（行数仅统计 CLAUDE.md 本体 + 版本纪律） ───

def test_score_clarity_short_with_version(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n版本号纪律 bump_version\n" + "x\n" * 10)
    assert rhi_global_setup._score_claude(claude)["breakdown"]["clarity"] == 1.0


def test_score_clarity_short_without_version(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n" + "x\n" * 10)
    assert rhi_global_setup._score_claude(claude)["breakdown"]["clarity"] == 0.5


def test_score_clarity_huge(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# 测试\n版本号纪律\n" + "x\n" * 900)
    assert rhi_global_setup._score_claude(claude)["breakdown"]["clarity"] == 0.2


# ─── _score_claude: RHI 自指章节排除（防"描述即命中"污染） ───

def test_score_rhi_marker_excluded(tmp_path: Path) -> None:
    """CLAUDE.md 含 RHI 自述章节时，其内部关键词不得抬升评分。"""
    claude = _write_claude(
        tmp_path,
        "# 测试\n"
        "13 项检查清单\n"
        "---\n"
        "## RHI 递归 Harness 自进化\n"
        "rule_completeness 检测 verify_doc_consistency 与 反模式 AP01；"
        "memory_coverage 引用 D:\\Knowledge\n",
    )
    breakdown = rhi_global_setup._score_claude(claude)["breakdown"]
    # 自述章节被剔除：rule 只剩 13 项命中 → 0.4；memory 无命中 → 0.0
    assert breakdown["rule_completeness"] == 0.4
    assert breakdown["memory_coverage"] == 0.0


def test_score_total_weights(tmp_path: Path) -> None:
    claude = _write_claude(
        tmp_path,
        "# 测试\n"
        "memory 体系 + D:\\Knowledge 知识库\n"
        "13 项检查清单 + 反模式 AP01 + verify_doc_consistency\n"
        "docs/harness 文档体系 + trace_id 全链路 + 08-gap-analysis\n"
        "版本号纪律 bump_version\n",
    )
    result = rhi_global_setup._score_claude(claude)
    assert result["breakdown"]["memory_coverage"] == 1.0
    assert result["breakdown"]["rule_completeness"] == 1.0
    assert result["breakdown"]["consistency"] == 1.0
    assert result["breakdown"]["clarity"] == 1.0
    assert result["score"] == 1.0


# ─── _improvement_rate ───

def test_improvement_rate_empty() -> None:
    assert rhi_global_setup._improvement_rate([]) == 0.0


def test_improvement_rate_all_improve() -> None:
    prefs = [{"preference": "improve"}, {"preference": "improve"}]
    assert rhi_global_setup._improvement_rate(prefs) == 1.0


def test_improvement_rate_mixed() -> None:
    prefs = [{"preference": "improve"}, {"preference": "regress"}, {"preference": "improve"}]
    assert rhi_global_setup._improvement_rate(prefs) == pytest.approx(2 / 3)


# ─── cmd_init / cmd_step 状态机 ───

def _args(project: Path, max_iters: int = 5) -> argparse.Namespace:
    return argparse.Namespace(project=str(project), max_iters=max_iters)


def test_cmd_init_creates_history(tmp_path: Path) -> None:
    _write_claude(tmp_path, "# 测试\n13 项检查清单")
    assert rhi_global_setup.cmd_init(_args(tmp_path)) == 0
    history = json.loads((tmp_path / ".rhi" / "history.json").read_text(encoding="utf-8"))
    assert len(history["versions"]) == 1
    assert history["versions"][0]["version"] == 0
    assert history["converged"] is False


def test_cmd_init_missing_claude(tmp_path: Path) -> None:
    assert rhi_global_setup.cmd_init(_args(tmp_path)) == 1


def test_cmd_step_improves_score(tmp_path: Path) -> None:
    claude = _write_claude(tmp_path, "# v0\n仅基础规则")
    rhi_global_setup.cmd_init(_args(tmp_path))
    # 提升 CLAUDE.md 质量
    claude.write_text(
        "# v1\nmemory 体系 + 13 项检查清单 + 反模式 AP01 + verify_doc_consistency + docs/harness + trace_id + 08-gap-analysis + 版本号纪律",
        encoding="utf-8",
    )
    assert rhi_global_setup.cmd_step(_args(tmp_path)) == 0
    history = json.loads((tmp_path / ".rhi" / "history.json").read_text(encoding="utf-8"))
    assert len(history["versions"]) == 2
    assert history["preferences"][-1]["preference"] == "improve"
    assert history["versions"][-1]["score"] > history["versions"][0]["score"]


def test_cmd_step_convergence_on_max_iters(tmp_path: Path) -> None:
    _write_claude(tmp_path, "# 测试\n13 项检查清单")
    rhi_global_setup.cmd_init(_args(tmp_path, max_iters=2))
    rhi_global_setup.cmd_step(_args(tmp_path, max_iters=2))
    rhi_global_setup.cmd_step(_args(tmp_path, max_iters=2))  # 已达最大轮次
    history = json.loads((tmp_path / ".rhi" / "history.json").read_text(encoding="utf-8"))
    assert history["converged"] is True


def test_cmd_step_before_init(tmp_path: Path) -> None:
    _write_claude(tmp_path, "# 测试")
    assert rhi_global_setup.cmd_step(_args(tmp_path)) == 1  # 未初始化报错
