"""tests/scripts/test_cleanup_push_scope.py — 推送范围治理工具（scripts/cleanup_push_scope.py）单元测试。

覆盖: _is_forbidden 禁止/白名单判定（含 seeds/ L1 配置层放行回归）、
_tracked_scripts 探测、cmd_prune / cmd_verify 命令路径（mock _git）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import cleanup_push_scope  # noqa: E402


# ─── _is_forbidden: 禁止项命中 ───

@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".env.production",
        "data/kline.duckdb",
        "memory/knowledge/factors/a.json",
        "logs/run.log",
        ".rhi/history.json",
        "reports/out.txt",
        "output/x.csv",
        "signals/s.parquet",
        "credentials.json",
        "debug_llm_response_1.txt",
        "cert/private.key",
        "cert/a.pem",
        "cert/a.p12",
        "cert/a.jks",
        "cert/a.crt",
        "tmp/cache.db",
        "tmp/cache.duckdb",
        "logs/a.log",
        "pkg/mod.pyc",
    ],
)
def test_is_forbidden_positive(path: str) -> None:
    assert cleanup_push_scope._is_forbidden(path) is True, path


# ─── _is_forbidden: 允许项（白名单 + 正常代码/文档） ───

@pytest.mark.parametrize(
    "path",
    [
        ".env.example",  # 配置模板占位符
        "memory/meta_loop/.gitkeep",  # 占位文件
        "memory/portfolio/.gitkeep",
        "seeds/futures/momentum.yaml",  # L1 配置层（plans/58 §5.3 误拦修复回归）
        "seeds/README.md",
        "config/settings.yaml",
        "fts/llm.py",
        "tests/test_llm.py",
        "docs/harness/01-architecture.md",
        "scripts/bump_version.py",
        "README.md",
        "pyproject.toml",
    ],
)
def test_is_forbidden_negative(path: str) -> None:
    assert cleanup_push_scope._is_forbidden(path) is False, path


# ─── _tracked_scripts ───

def _monkey_git(monkeypatch: pytest.MonkeyPatch, ls_files_output: list[str]) -> None:
    def fake_git(*args: str) -> list[str]:
        assert args[0] == "ls-files"
        return ls_files_output

    monkeypatch.setattr(cleanup_push_scope, "_git", fake_git)


def test_tracked_scripts_detects_underscore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "_probe.py").write_text("x = 1", encoding="utf-8")
    (scripts_dir / "_tmp_a.py").write_text("x = 1", encoding="utf-8")
    (scripts_dir / "bump_version.py").write_text("x = 1", encoding="utf-8")  # 非 _ 开头忽略

    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)
    _monkey_git(monkeypatch, ["scripts/_probe.py", "scripts/bump_version.py", "scripts/_tmp_a.py"])

    assert cleanup_push_scope._tracked_scripts() == ["scripts/_probe.py", "scripts/_tmp_a.py"]


def test_tracked_scripts_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)
    _monkey_git(monkeypatch, ["scripts/bump_version.py"])
    assert cleanup_push_scope._tracked_scripts() == []


# ─── cmd_prune ───

def test_cmd_prune_dry_run_no_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "_probe.py").write_text("x = 1", encoding="utf-8")
    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)
    _monkey_git(monkeypatch, ["scripts/_probe.py"])

    rc = cleanup_push_scope.cmd_prune(argparse.Namespace(apply=False))
    assert rc == 0
    # dry-run 不调用 git rm
    # （_git 仅被 _tracked_scripts 以 ls-files 调用，无 rm 断言失败即通过）


def test_cmd_prune_apply_calls_git_rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "_probe.py").write_text("x = 1", encoding="utf-8")
    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)

    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> list[str]:
        calls.append(args)
        if args[0] == "ls-files":
            return ["scripts/_probe.py"]
        return []

    monkeypatch.setattr(cleanup_push_scope, "_git", fake_git)

    rc = cleanup_push_scope.cmd_prune(argparse.Namespace(apply=True))
    assert rc == 0
    assert calls[-1][0] == "rm"
    assert calls[-1][1] == "--cached"
    assert "scripts/_probe.py" in calls[-1]


def test_cmd_prune_nothing_to_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)
    _monkey_git(monkeypatch, [])
    assert cleanup_push_scope.cmd_prune(argparse.Namespace(apply=True)) == 0


# ─── cmd_verify ───

def test_cmd_verify_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)
    _monkey_git(monkeypatch, ["fts/llm.py", "README.md", "seeds/futures/momentum.yaml"])
    assert cleanup_push_scope.cmd_verify(argparse.Namespace()) == 0


def test_cmd_verify_reports_violations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)
    _monkey_git(monkeypatch, ["fts/llm.py", ".env", "data/kline.duckdb"])
    assert cleanup_push_scope.cmd_verify(argparse.Namespace()) == 1


def test_cmd_verify_warns_stray_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "_probe.py").write_text("x = 1", encoding="utf-8")
    monkeypatch.setattr(cleanup_push_scope, "PROJECT_ROOT", tmp_path)
    _monkey_git(monkeypatch, ["scripts/_probe.py"])
    # 有残留 _ 脚本但无禁止项 → rc=0（仅告警）
    assert cleanup_push_scope.cmd_verify(argparse.Namespace()) == 0
