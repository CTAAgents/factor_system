"""tests/core/test_atomic.py — FTS 原子文件读写测试。

验证 fts.core.atomic 的原子写入、安全读取和备份轮转功能。

HARNESS §契约优先: 接口变更必须同步更新测试。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from fts.core.atomic import atomic_read, atomic_write, atomic_write_state


# ─── TestAtomicWrite ─────────────────────────────────────


class TestAtomicWrite:
    """atomic_write — 原子 JSON 写入。"""

    def test_write_and_read(self, tmp_path: Path):
        """基础写入后能正确读出。"""
        p = tmp_path / "test.json"
        data = {"key": "value", "num": 42}
        atomic_write(p, data)
        assert p.exists()
        assert json.loads(p.read_text(encoding="utf-8")) == data

    def test_tmp_file_cleanup_after_success(self, tmp_path: Path):
        """写入成功后 .tmp 临时文件不存在。"""
        p = tmp_path / "test.json"
        atomic_write(p, {"a": 1})
        assert not p.with_suffix(".tmp").exists()

    def test_overwrite_existing(self, tmp_path: Path):
        """覆盖已有文件不报错，内容正确替换。"""
        p = tmp_path / "test.json"
        atomic_write(p, {"version": 1})
        atomic_write(p, {"version": 2})
        assert json.loads(p.read_text(encoding="utf-8")) == {"version": 2}

    def test_auto_create_parent_directory(self, tmp_path: Path):
        """父目录不存在时自动创建。"""
        p = tmp_path / "sub" / "deep" / "test.json"
        atomic_write(p, {"auto": "dir"})
        assert p.exists()
        assert json.loads(p.read_text(encoding="utf-8")) == {"auto": "dir"}

    def test_error_on_unserializable_data_cleans_tmp(self, tmp_path: Path):
        """不可序列化的数据 raise TypeError，临时文件被清理。"""
        p = tmp_path / "test.json"
        with patch("fts.core.atomic.json.dumps", side_effect=TypeError("bad type")):
            with pytest.raises(TypeError):
                atomic_write(p, object())
        assert not p.with_suffix(".tmp").exists()
        assert not p.exists()

    def test_os_replace_failure_cleans_tmp(self, tmp_path: Path):
        """os.replace 失败时临时文件被清理。"""
        p = tmp_path / "test.json"
        with patch("fts.core.atomic.os.replace", side_effect=OSError("permission denied")):
            with pytest.raises(OSError):
                atomic_write(p, {"a": 1})
        assert not p.with_suffix(".tmp").exists()

    def test_empty_dict(self, tmp_path: Path):
        """空 dict 写入和读取。"""
        p = tmp_path / "empty.json"
        atomic_write(p, {})
        assert json.loads(p.read_text(encoding="utf-8")) == {}

    def test_nested_dict(self, tmp_path: Path):
        """嵌套 dict 正确写入。"""
        p = tmp_path / "nested.json"
        data = {"level1": {"level2": {"key": "deep"}, "list": [1, 2, 3]}}
        atomic_write(p, data)
        assert json.loads(p.read_text(encoding="utf-8")) == data

    def test_list_data(self, tmp_path: Path):
        """list 作为顶层数据写入。"""
        p = tmp_path / "list.json"
        data = [1, "two", {"three": 3}]
        atomic_write(p, data)
        assert json.loads(p.read_text(encoding="utf-8")) == data

    def test_none_data(self, tmp_path: Path):
        """None 作为顶层数据写入。"""
        p = tmp_path / "none.json"
        atomic_write(p, None)
        assert json.loads(p.read_text(encoding="utf-8")) is None

    def test_make_dir(self, tmp_path: Path):
        """make_dir=True 自动创建父目录。"""
        p = tmp_path / "a" / "b" / "c" / "test.json"
        atomic_write(p, {"ok": True}, make_dir=True)
        assert p.exists()

    def test_make_dir_false_happy_path(self, tmp_path: Path):
        """make_dir=False 且父目录已存在则正常写入。"""
        p = tmp_path / "existing" / "test.json"
        p.parent.mkdir(parents=True)
        atomic_write(p, {"ok": True}, make_dir=False)
        assert p.exists()

    def test_make_dir_false_no_parent_error(self, tmp_path: Path):
        """make_dir=False 且父目录不存在时抛出 OSError。"""
        p = tmp_path / "ghost" / "test.json"
        with pytest.raises(OSError):
            atomic_write(p, {"ok": True}, make_dir=False)


# ─── TestAtomicRead ──────────────────────────────────────


class TestAtomicRead:
    """atomic_read — 安全 JSON 读取。"""

    def test_read_existing(self, tmp_path: Path):
        """读取已有文件返回正确数据。"""
        p = tmp_path / "test.json"
        p.write_text(json.dumps({"ok": True}, ensure_ascii=False), encoding="utf-8")
        assert atomic_read(p) == {"ok": True}

    def test_read_non_existent_returns_default(self, tmp_path: Path):
        """不存在的文件返回默认值。"""
        p = tmp_path / "nonexistent.json"
        assert atomic_read(p, default="fallback") == "fallback"

    def test_read_non_existent_default_none(self, tmp_path: Path):
        """不存在的文件不指定默认值时返回 None。"""
        p = tmp_path / "nonexistent.json"
        assert atomic_read(p) is None

    def test_read_corrupted_json_returns_default(self, tmp_path: Path):
        """损坏的 JSON 文件返回默认值。"""
        p = tmp_path / "corrupt.json"
        p.write_text("{invalid json!!}", encoding="utf-8")
        result = atomic_read(p, default={"safe": True})
        assert result == {"safe": True}

    def test_read_empty_file_returns_default(self, tmp_path: Path):
        """空文件返回默认值（JSONDecodeError）。"""
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        assert atomic_read(p, default=[]) == []

    def test_read_with_chinese_chars(self, tmp_path: Path):
        """中文内容的 JSON 正常读取。"""
        p = tmp_path / "utf8.json"
        data = {"你好": "世界"}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert atomic_read(p, default={}) == data

    def test_read_directory_returns_default(self, tmp_path: Path):
        """路径是目录时返回默认值（OSError）。"""
        d = tmp_path / "adir"
        d.mkdir()
        assert atomic_read(d, default="fallback") == "fallback"


# ─── TestAtomicWriteState ────────────────────────────────


class TestAtomicWriteState:
    """atomic_write_state — 原子写入 + 备份轮转。"""

    def test_basic_write_no_backup(self, tmp_path: Path):
        """首次写入时无备份文件产生。"""
        p = tmp_path / "state.json"
        atomic_write_state(p, {"version": 1})
        assert p.exists()
        assert json.loads(p.read_text(encoding="utf-8")) == {"version": 1}
        assert not p.with_suffix(".bak.0").exists()

    def test_backup_rotation(self, tmp_path: Path):
        """多次写入产生正确的备份链。"""
        p = tmp_path / "state.json"
        atomic_write_state(p, {"version": 1})
        atomic_write_state(p, {"version": 2})
        # 旧文件应备份为 .bak.0
        assert p.with_suffix(".bak.0").exists()
        assert json.loads(p.with_suffix(".bak.0").read_text(encoding="utf-8")) == {"version": 1}
        # 主文件是最新版本
        assert json.loads(p.read_text(encoding="utf-8")) == {"version": 2}

    def test_backup_rotation_chain(self, tmp_path: Path):
        """多次写入后备份链正确（.bak.0 → .bak.1 → .bak.2）。"""
        p = tmp_path / "state.json"
        for v in range(5):  # 写入 0~4
            atomic_write_state(p, {"version": v})
        # 主文件 = 最新
        assert json.loads(p.read_text(encoding="utf-8")) == {"version": 4}
        # backup_count=3，保留 3 个备份
        assert json.loads(p.with_suffix(".bak.0").read_text(encoding="utf-8")) == {"version": 3}
        assert json.loads(p.with_suffix(".bak.1").read_text(encoding="utf-8")) == {"version": 2}
        assert json.loads(p.with_suffix(".bak.2").read_text(encoding="utf-8")) == {"version": 1}

    def test_custom_backup_count(self, tmp_path: Path):
        """自定义 backup_count 参数生效。"""
        p = tmp_path / "state.json"
        for v in range(6):
            atomic_write_state(p, {"version": v}, backup_count=5)
        # backup_count=5，应保留 .bak.0 ~ .bak.4
        assert json.loads(p.read_text(encoding="utf-8")) == {"version": 5}
        assert json.loads(p.with_suffix(".bak.0").read_text(encoding="utf-8")) == {"version": 4}
        assert json.loads(p.with_suffix(".bak.4").read_text(encoding="utf-8")) == {"version": 0}

    def test_backup_first_write(self, tmp_path: Path):
        """首次写入时无论 backup_count 多少都没有备份文件。"""
        p = tmp_path / "fresh.json"
        atomic_write_state(p, {"first": True})
        assert not p.with_suffix(".bak.0").exists()
        assert not p.with_suffix(".bak.1").exists()
        assert not p.with_suffix(".bak.2").exists()

    def test_backup_data_integrity(self, tmp_path: Path):
        """备份轮转过程中所有文件均可读、数据完整。"""
        p = tmp_path / "state.json"
        versions = [{"v": i, "data": "x" * 100} for i in range(4)]
        for state in versions:
            atomic_write_state(p, state)
        # 主文件正确
        assert atomic_read(p) == versions[-1]
        # 所有备份都可读
        for i in range(3):
            bak = p.with_suffix(f".bak.{i}")
            assert bak.exists()
            assert atomic_read(bak) is not None


# ─── 集成 / 边界 ─────────────────────────────────────────


class TestIntegration:
    """集成场景测试。"""

    def test_write_then_read_roundtrip(self, tmp_path: Path):
        """写入后读取正确来回。"""
        p = tmp_path / "roundtrip.json"
        original = {"a": [1, 2, 3], "b": {"nested": True}, "c": None}
        atomic_write(p, original)
        assert atomic_read(p) == original

    def test_write_state_then_read(self, tmp_path: Path):
        """atomic_write_state 写入后 atomic_read 可读。"""
        p = tmp_path / "combo.json"
        atomic_write_state(p, {"factors": ["alpha", "beta"]})
        assert atomic_read(p) == {"factors": ["alpha", "beta"]}

    def test_write_state_backup_readable(self, tmp_path: Path):
        """atomic_write_state 的备份文件也可被 atomic_read 读取。"""
        p = tmp_path / "evolving.json"
        atomic_write_state(p, {"gen": 1})
        atomic_write_state(p, {"gen": 2})
        atomic_write_state(p, {"gen": 3})
        bak0 = p.with_suffix(".bak.0")
        assert atomic_read(bak0) == {"gen": 2}
        bak1 = p.with_suffix(".bak.1")
        assert atomic_read(bak1) == {"gen": 1}

    def test_atomic_write_state_creates_parent_dir(self, tmp_path: Path):
        """atomic_write_state 自动创建父目录。"""
        p = tmp_path / "nested" / "dir" / "state.json"
        atomic_write_state(p, {"ok": True})
        assert p.exists()


# ─── 覆盖 lines 62-63, 111-112 ───────────────────────────


class TestCoverageGaps:
    """覆盖遗漏行。"""

    def test_atomic_write_double_exception_cleanup(self, tmp_path: Path):
        """lines 62-63: os.replace 和 tmp.unlink 都失败时静默处理。"""
        p = tmp_path / "test.json"
        with patch("fts.core.atomic.os.replace", side_effect=OSError("replace failed")):
            with patch("pathlib.Path.unlink", side_effect=OSError("unlink failed")):
                with pytest.raises(OSError):
                    atomic_write(p, {"a": 1})
        # tmp 文件应仍在（因为 unlink 失败）
        assert p.with_suffix(".tmp").exists()

    def test_atomic_write_state_backup_oserror(self, tmp_path: Path):
        """lines 111-112: 备份轮转中 os.replace 失败时静默通过。"""
        p = tmp_path / "state.json"
        _real_replace = os.replace  # 保存真实引用
        # 先写入两次，产生备份，确保 prev.exists() 条件满足
        atomic_write_state(p, {"version": 1})
        atomic_write_state(p, {"version": 2})

        # 再次写入，让备份轮转触发 os.replace 并失败
        os_replace_calls = [0]

        def _failing_replace(src, dst):
            os_replace_calls[0] += 1
            # 前 2 次 os.replace 是备份轮转，需要失败
            # 第 3 次是 atomic_write 内部的 os.replace，必须成功
            if os_replace_calls[0] <= 2:
                raise OSError("模拟备份失败")
            return _real_replace(src, dst)

        with patch("fts.core.atomic.os.replace", side_effect=_failing_replace):
            atomic_write_state(p, {"version": 3})

        # 主文件应被正确写入
        assert atomic_read(p) == {"version": 3}
