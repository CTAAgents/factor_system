"""verify_doc_consistency.py 文档一致性校验脚本测试。

覆盖:
    - find_docs 目录扫描（空目录 / 顶层 md / 目录缺失）
    - check_metadata_table 元数据表格检查（完整 / 缺章节 / 缺字段 / 缺版本 / 缺日期）
    - check_file_exists 文件存在性
    - check_doc_assertions 各文档断言（01/06/07/08 分支）
    - check_flow_docs_exist 流程文档存在性
    - check_version_consistency 版本号一致性（全绿 / 漂移 / plans 子目录覆盖）
    - run_all_checks 汇总（全绿 / 失败计数）
    - main() CLI（--file / --json / --fix-versions 成功与失败路径，
      回归解释器路径含空格时 os.system 拆分缺陷 → subprocess 列表参数）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

import scripts.verify_doc_consistency as vdc  # noqa: E402


def _write_doc(
    path: Path,
    *,
    version: str = "v2.100.1",
    date: str = "2099-01-01",
    include_meta: bool = True,
    extra_body: str = "",
) -> Path:
    """写一篇最小文档：版本头 + 可选一致性元数据表。"""
    content = (
        f"> 版本: {version}\n"
        f"> 最后更新: {date}\n"
        "\n"
        "## 内容\n"
    )
    if include_meta:
        content += (
            "\n## 一致性元数据\n"
            "\n| 字段 | 值 |\n"
            "|:-----|:----|\n"
            "| 代码→文档映射 | src |\n"
            "| 可验证断言 | ok |\n"
            "| 检验方式 | pytest |\n"
        )
    content += extra_body
    path.write_text(content, encoding="utf-8")
    return path


class _FakeResult:
    """模拟 subprocess.CompletedProcess。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """隔离环境：临时根目录 + docs/harness/ + scripts/，并接管模块路径。"""
    harness = tmp_path / "docs" / "harness"
    scripts = tmp_path / "scripts"
    harness.mkdir(parents=True)
    scripts.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "2.100.1"\n', encoding="utf-8"
    )
    monkeypatch.setattr(vdc, "HARNESS_DIR", harness)
    monkeypatch.setattr(vdc, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(vdc, "SCRIPTS_DIR", scripts)
    return {"root": tmp_path, "harness": harness, "scripts": scripts}


class TestFindDocs:
    def test_empty_dir_returns_empty(self, iso: dict[str, Path]) -> None:
        assert vdc.find_docs() == []

    def test_scans_top_level_md_sorted(self, iso: dict[str, Path]) -> None:
        _write_doc(iso["harness"] / "02-lifecycle.md")
        _write_doc(iso["harness"] / "01-architecture.md")
        # 子目录/非 md 不纳入
        (iso["harness"] / "plans").mkdir()
        _write_doc(iso["harness"] / "plans" / "10-plan.md")
        (iso["harness"] / "notes.txt").write_text("x", encoding="utf-8")
        names = [p.name for p in vdc.find_docs()]
        assert names == ["01-architecture.md", "02-lifecycle.md"]

    def test_missing_dir_returns_empty(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vdc, "HARNESS_DIR", iso["root"] / "nonexistent")
        assert vdc.find_docs() == []


class TestCheckMetadataTable:
    def test_complete_doc_no_issues(self, iso: dict[str, Path]) -> None:
        doc = _write_doc(iso["harness"] / "01-architecture.md")
        assert vdc.check_metadata_table(doc) == []

    def test_missing_section(self, iso: dict[str, Path]) -> None:
        doc = _write_doc(iso["harness"] / "01-architecture.md", include_meta=False)
        issues = vdc.check_metadata_table(doc)
        assert "缺少 '## 一致性元数据' 章节" in issues
        assert any("缺少元数据字段" in i for i in issues)

    def test_missing_field(self, iso: dict[str, Path]) -> None:
        doc = iso["harness"] / "01-architecture.md"
        doc.write_text(
            "> 版本: v2.100.1\n> 最后更新: 2099-01-01\n\n## 一致性元数据\n\n| 代码→文档映射 | a |\n",
            encoding="utf-8",
        )
        issues = vdc.check_metadata_table(doc)
        assert "缺少元数据字段: 可验证断言" in issues
        assert "缺少元数据字段: 检验方式" in issues

    def test_missing_version_and_date(self, iso: dict[str, Path]) -> None:
        doc = iso["harness"] / "01-architecture.md"
        doc.write_text("# 无版本头\n", encoding="utf-8")
        issues = vdc.check_metadata_table(doc)
        assert "缺少版本号声明" in issues
        assert "缺少最后更新日期" in issues


class TestCheckFileExists:
    def test_exists(self, iso: dict[str, Path]) -> None:
        target = iso["root"] / "fts" / "cli.py"
        target.parent.mkdir(parents=True)
        target.write_text("x", encoding="utf-8")
        assert vdc.check_file_exists("fts/cli.py", base_dir=iso["root"]) is True

    def test_not_exists(self, iso: dict[str, Path]) -> None:
        assert vdc.check_file_exists("fts/nope.py", base_dir=iso["root"]) is False


class TestCheckDocAssertions:
    def test_01_architecture_seed_count(self, iso: dict[str, Path]) -> None:
        ok = _write_doc(iso["harness"] / "01-architecture.md", extra_body="种子 185 个")
        bad = _write_doc(iso["root"] / "01-architecture.md")  # 同名不同路径：无 185
        assert vdc.check_doc_assertions(ok) == []
        assert vdc.check_doc_assertions(bad) != []

    def test_06_testing_count(self, iso: dict[str, Path]) -> None:
        ok = _write_doc(iso["harness"] / "06-testing.md", extra_body="4020+ 用例")
        assert vdc.check_doc_assertions(ok) == []
        bad = _write_doc(iso["harness"] / "06-testing.md")
        assert vdc.check_doc_assertions(bad) != []

    def test_07_operations_version_file(self, iso: dict[str, Path]) -> None:
        # 版本文件缺失
        doc = _write_doc(iso["harness"] / "07-operations.md")
        assert vdc.check_doc_assertions(doc) != []
        # 版本文件存在但缺 __version__
        init_file = iso["root"] / "fts" / "__init__.py"
        init_file.parent.mkdir(parents=True)
        init_file.write_text("x = 1\n", encoding="utf-8")
        assert vdc.check_doc_assertions(doc) != []
        # 完整
        init_file.write_text("__version__ = '2.100.1'\n", encoding="utf-8")
        assert vdc.check_doc_assertions(doc) == []

    def test_08_gap_analysis_table(self, iso: dict[str, Path]) -> None:
        ok = _write_doc(iso["harness"] / "08-gap-analysis.md", extra_body="✅ 已关闭")
        assert vdc.check_doc_assertions(ok) == []
        bad = _write_doc(iso["harness"] / "08-gap-analysis.md")
        assert vdc.check_doc_assertions(bad) != []

    def test_other_docs_no_assertions(self, iso: dict[str, Path]) -> None:
        doc = _write_doc(iso["harness"] / "03-configuration.md")
        assert vdc.check_doc_assertions(doc) == []


class TestCheckFlowDocsExist:
    def test_all_present(self, iso: dict[str, Path]) -> None:
        _write_doc(iso["harness"] / "execution_modes_flowchart.md")
        _write_doc(iso["harness"] / "business_flow.md")
        assert vdc.check_flow_docs_exist() == []

    def test_missing_one(self, iso: dict[str, Path]) -> None:
        _write_doc(iso["harness"] / "business_flow.md")
        issues = vdc.check_flow_docs_exist()
        assert len(issues) == 1
        assert "execution_modes_flowchart.md" in issues[0]


class TestCheckVersionConsistency:
    def test_all_match(self, iso: dict[str, Path]) -> None:
        _write_doc(iso["harness"] / "01-architecture.md")
        (iso["harness"] / "plans").mkdir()
        _write_doc(iso["harness"] / "plans" / "10-plan.md")
        assert vdc.check_version_consistency() == []

    def test_drift_detected(self, iso: dict[str, Path]) -> None:
        _write_doc(iso["harness"] / "01-architecture.md", version="v9.9.9")
        issues = vdc.check_version_consistency()
        assert len(issues) == 1
        assert issues[0] == {
            "file": "01-architecture.md",
            "expected": "v2.100.1",
            "actual": "v9.9.9",
        }

    def test_plans_subdir_scanned(self, iso: dict[str, Path]) -> None:
        (iso["harness"] / "plans").mkdir()
        _write_doc(iso["harness"] / "plans" / "23-plan.md", version="v9.9.9")
        issues = vdc.check_version_consistency()
        assert [i["file"] for i in issues] == ["23-plan.md"]

    def test_doc_without_version_header_skipped(self, iso: dict[str, Path]) -> None:
        (iso["harness"] / "04-resilience.md").write_text("# 无版本头\n", encoding="utf-8")
        assert vdc.check_version_consistency() == []


class TestRunAllChecks:
    def test_all_green(self, iso: dict[str, Path]) -> None:
        _write_doc(iso["harness"] / "01-architecture.md", extra_body="种子 185 个")
        _write_doc(iso["harness"] / "02-lifecycle.md")
        _write_doc(iso["harness"] / "execution_modes_flowchart.md")
        _write_doc(iso["harness"] / "business_flow.md")
        results = vdc.run_all_checks()
        assert results["failed"] == 0
        assert results["passed"] >= 5  # 2 文档综合 + 版本号 + 流程存在性

    def test_failures_counted(self, iso: dict[str, Path]) -> None:
        (iso["harness"] / "01-architecture.md").write_text("# 无元数据\n", encoding="utf-8")
        results = vdc.run_all_checks()
        assert results["failed"] > 0
        assert results["checks"][0]["status"] == "FAIL"

    def test_no_docs_records_error(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(vdc, "HARNESS_DIR", iso["root"] / "empty")
        results = vdc.run_all_checks()
        assert results["errors"] == ["未找到 Harness 文档"]
        assert results["failed"] == 0


class TestMain:
    def _call_main(self, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
        monkeypatch.setattr(sys, "argv", ["verify_doc_consistency.py", *argv])
        return vdc.main()

    def test_no_args_all_green(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _write_doc(iso["harness"] / "01-architecture.md", extra_body="种子 185 个")
        _write_doc(iso["harness"] / "execution_modes_flowchart.md")
        _write_doc(iso["harness"] / "business_flow.md")
        assert self._call_main(monkeypatch, []) == 0
        assert "全部通过" in capsys.readouterr().out

    def test_json_output(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        _write_doc(iso["harness"] / "01-architecture.md", extra_body="种子 185 个")
        _write_doc(iso["harness"] / "execution_modes_flowchart.md")
        _write_doc(iso["harness"] / "business_flow.md")
        assert self._call_main(monkeypatch, ["--json"]) == 0
        out = capsys.readouterr().out
        import json
        parsed = json.loads(out)
        assert parsed["failed"] == 0

    def test_file_not_found(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._call_main(monkeypatch, ["--file", "nope.md"]) == 1

    def test_file_scoped_check(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        # 存在坏文档 + 其他正常：--file 指定好文档应只对该文档做元数据/断言检查并放行
        _write_doc(iso["harness"] / "01-architecture.md", extra_body="种子 185 个")
        (iso["harness"] / "02-lifecycle.md").write_text("# 坏文档\n", encoding="utf-8")
        _write_doc(iso["harness"] / "execution_modes_flowchart.md")
        _write_doc(iso["harness"] / "business_flow.md")
        assert self._call_main(monkeypatch, ["--file", "01-architecture.md"]) == 0
        # 反向：指定坏文档应失败
        assert self._call_main(monkeypatch, ["--file", "02-lifecycle.md"]) == 1

    def test_fix_versions_success(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        update_script = iso["scripts"] / "update_doc_versions.py"
        update_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        # 完整文档集，保证 fix 后继续校验全绿
        _write_doc(iso["harness"] / "01-architecture.md", extra_body="种子 185 个")
        _write_doc(iso["harness"] / "execution_modes_flowchart.md")
        _write_doc(iso["harness"] / "business_flow.md")
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            vdc.subprocess,
            "run",
            lambda *a, **k: captured.update(call=a) or _FakeResult(returncode=0),
        )
        assert self._call_main(monkeypatch, ["--fix-versions"]) == 0
        assert "版本号修复完成" in capsys.readouterr().out
        # 回归：列表参数（含 sys.executable 首项），路径含空格也不会被 cmd 拆分
        args_list = captured["call"][0]  # subprocess.run 首个位置参数即完整参数列表
        assert args_list == [sys.executable, str(update_script), "--apply"]

    def test_fix_versions_failure(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        (iso["scripts"] / "update_doc_versions.py").write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(
            vdc.subprocess,
            "run",
            lambda *a, **k: _FakeResult(returncode=1, stderr="boom"),
        )
        assert self._call_main(monkeypatch, ["--fix-versions"]) == 1
        out = capsys.readouterr().out
        assert "版本号修复失败" in out
        assert "boom" in out

    def test_fix_versions_missing_script(self, iso: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._call_main(monkeypatch, ["--fix-versions"]) == 1
