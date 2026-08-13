"""bump_version.py 统一版本 bump 工具测试（v2.103.0 SemVer build 段制）。

覆盖:
    - 版本递增逻辑（patch/minor/major/build）
    - 里程碑 bump 清 build 段、build bump 递增 build 段
    - 竖线转义
    - pyproject/07-operations/README 文件操作（版本替换、条目追加、CRLF 保留）
    - 单日护栏仅约束里程碑 bump（同日重复 bump 拒绝 / --force 跳过 / build 不受限）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

import scripts.bump_version as bv  # noqa: E402


@pytest.fixture()
def iso_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """构造隔离的 pyproject/07-operations/README 临时文件（CRLF），并接管模块路径与副作用。"""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(b'name = "fts"\r\nversion = "2.100.1"\r\n')

    operations = tmp_path / "07-operations.md"
    operations.write_text(
        "# FTS 运维与版本管理\r\n"
        "\r\n"
        "## 1. 版本历史\r\n"
        "\r\n"
        "| 版本 | 日期 | 说明 |\r\n"
        "|:-----|:-----|:-----|\r\n"
        "| **v2.100.1** | **2099-01-01** | **已有条目** |** |\r\n"
        "| **v2.100.0** | **2099-01-01** | **更早条目** |** |\r\n"
        "\r\n"
        "## 2. 其他章节\r\n",
        encoding="utf-8",
        newline="",
    )

    readme = tmp_path / "README.md"
    readme.write_text(
        "[![Version](https://img.shields.io/badge/version-2.100.1-blue)](#)\n",
        encoding="utf-8",
        newline="",
    )

    monkeypatch.setattr(bv, "PYPROJECT", pyproject)
    monkeypatch.setattr(bv, "OPERATIONS", operations)
    monkeypatch.setattr(bv, "README", readme)
    monkeypatch.setattr(bv, "today", lambda: "2099-01-01")
    monkeypatch.setattr(bv.subprocess, "run", lambda *a, **k: None)
    return {"pyproject": pyproject, "operations": operations, "readme": readme}


class TestVersionComputation:
    @pytest.mark.parametrize(
        ("current", "bump_type", "expected"),
        [
            ("2.100.1", "patch", "2.100.2"),
            ("2.100.1", "minor", "2.101.0"),
            ("2.100.1", "major", "3.0.0"),
            ("2.99.0", "minor", "2.100.0"),
            ("2.101.0", "patch", "2.101.1"),
            ("3.0.0", "patch", "3.0.1"),
            # 里程碑 bump 清 build 段（SemVer 标准）
            ("2.100.1+3", "patch", "2.100.2"),
            ("2.100.1+3", "minor", "2.101.0"),
            ("2.100.1+3", "major", "3.0.0"),
        ],
    )
    def test_bump(self, current: str, bump_type: str, expected: str) -> None:
        assert bv.bump_version(current, bump_type) == expected

    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            ("2.100.1", "2.100.1+1"),  # 无 build 段 → 从 1 起
            ("2.100.1+1", "2.100.1+2"),
            ("2.100.1+42", "2.100.1+43"),
        ],
    )
    def test_bump_build(self, current: str, expected: str) -> None:
        assert bv.bump_build(current) == expected


class TestEscaped:
    def test_pipe_escaped(self) -> None:
        assert bv._escaped("a|b") == "a\\|b"

    def test_plain_unchanged(self) -> None:
        assert bv._escaped("普通说明") == "普通说明"


class TestFileOps:
    def test_update_pyproject(self, iso_files: dict[str, Path]) -> None:
        bv.update_pyproject("2.101.0")
        raw = iso_files["pyproject"].read_bytes()
        assert b'version = "2.101.0"' in raw
        assert raw.count(b"\r\n") == raw.count(b"\n")  # 全 CRLF，无孤立 LF

    def test_append_history_inserts_first_and_keeps_crlf(self, iso_files: dict[str, Path]) -> None:
        bv.append_history("2.101.0", "发布里程碑制落地")
        content = iso_files["operations"].read_text(encoding="utf-8")
        lines = content.splitlines()
        # 新条目紧跟表头分隔行
        sep = next(i for i, ln in enumerate(lines) if ln.startswith("|:"))
        assert lines[sep + 1] == "| **v2.101.0** | **2099-01-01** | **发布里程碑制落地** |** |"
        # 旧条目仍在
        assert any("v2.100.1" in ln for ln in lines)
        raw = iso_files["operations"].read_bytes()
        assert raw.count(b"\r\n") == raw.count(b"\n")

    def test_latest_history_date(self, iso_files: dict[str, Path]) -> None:
        assert bv.latest_history_date() == "2099-01-01"

    def test_update_readme_version_encodes_plus(self, iso_files: dict[str, Path]) -> None:
        bv.update_readme_version("2.100.1+1")
        content = iso_files["readme"].read_text(encoding="utf-8")
        assert "version-2.100.1%2B1" in content  # shields.io 路径中 + 编码为 %2B

    def test_update_readme_version_plain(self, iso_files: dict[str, Path]) -> None:
        bv.update_readme_version("2.100.2")
        content = iso_files["readme"].read_text(encoding="utf-8")
        assert "version-2.100.2" in content

    def test_update_readme_version_increments_encoded_build(
        self, iso_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # README 徽章已含编码 build 段（%2B）时再次更新不应残留旧段
        iso_files["readme"].write_text(
            "[![Version](https://img.shields.io/badge/version-2.100.1%2B1-blue)](#)\n",
            encoding="utf-8",
            newline="",
        )
        bv.update_readme_version("2.100.1+2")
        content = iso_files["readme"].read_text(encoding="utf-8")
        assert "version-2.100.1%2B2-blue" in content
        assert "version-2.100.1%2B2%2B1" not in content  # 旧 build 段不残留


class TestDailyGuard:
    def test_double_bump_rejected(self, iso_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        # 2099-01-01 已有条目 → 无 --force 应拒绝（SystemExit code 1）
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "--type", "patch", "--message", "x"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 1

    def test_build_bump_not_guarded(self, iso_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        # 同日已有里程碑条目，但 --build 不受单日护栏约束
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "--build", "--message", "x"])
        bv.main()  # 不应抛异常
        assert bv.get_current_version() == "2.100.1+1"

    def test_force_allows_same_day(self, iso_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            sys, "argv", ["bump_version.py", "--type", "minor", "--message", "x", "--force"]
        )
        bv.main()  # 不应抛异常
        assert bv.get_current_version() == "2.101.0"

    def test_check_flag_exits_1_when_bumped(
        self, iso_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "--check"])
        with pytest.raises(SystemExit) as exc:
            bv.main()
        assert exc.value.code == 1

    def test_peek_readonly(self, iso_files: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["bump_version.py", "--peek"])
        bv.main()
        out = capsys.readouterr().out
        assert "当前版本: v2.100.1" in out
        assert "下次 build: v2.100.1+1" in out
        assert "下次 minor: v2.101.0" in out
