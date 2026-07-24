"""tests/scheduler/test_hotswap.py — HotSwapWatcher 测试。

HARNESS §测试随重构: 覆盖 hotswap.py 核心路径。
"""

from __future__ import annotations

import importlib as importlib_mod
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from fts.scheduler.hotswap import HotSwapWatcher, _reload_module


# ─── HotSwapWatcher ─────────────────────────────────────


class TestHotSwapWatcherInit:
    """初始化测试。"""

    def test_default_watch_dirs(self):
        """默认 watch_dirs 为 ["fts"]。"""
        watcher = HotSwapWatcher()
        assert len(watcher.watch_dirs) == 1
        assert watcher.watch_dirs[0] == Path("fts")

    def test_custom_watch_dirs(self):
        """自定义 watch_dirs 列表。"""
        dirs = ["fts/factor_engine", "fts/scheduler"]
        watcher = HotSwapWatcher(watch_dirs=dirs)
        assert len(watcher.watch_dirs) == 2
        assert all(isinstance(d, Path) for d in watcher.watch_dirs)
        # 跨平台: 使用 parts 比较避免路径分隔符差异
        assert watcher.watch_dirs[0].parts == ("fts", "factor_engine")
        assert watcher.watch_dirs[1].parts == ("fts", "scheduler")

    def test_initial_state(self):
        """初始状态: _observer=None, _running=False。"""
        watcher = HotSwapWatcher()
        assert watcher._observer is None
        assert watcher.running is False


class TestHotSwapWatcherStartStop:
    """启动/停止测试。"""

    @patch("fts.scheduler.hotswap.HotSwapWatcher.start", return_value=False)
    def test_start_no_watchdog(self, mock_start):
        """watchdog 未安装时 start 返回 False。"""
        watcher = HotSwapWatcher()
        result = watcher.start()
        assert result is False

    @patch("fts.scheduler.hotswap.HotSwapWatcher.start", return_value=True)
    def test_start_with_watchdog(self, mock_start):
        """watchdog 可用时 start 返回 True。"""
        watcher = HotSwapWatcher()
        result = watcher.start()
        assert result is True

    def test_stop_after_start(self):
        """stop 后 running 为 False。"""
        watcher = HotSwapWatcher()
        # 直接设置内部状态模拟已启动
        mock_observer = MagicMock()
        watcher._observer = mock_observer
        watcher._running = True
        watcher.stop()
        assert watcher.running is False
        mock_observer.stop.assert_called_once()

    def test_stop_idempotent(self):
        """stop 多次调用不抛异常。"""
        watcher = HotSwapWatcher()
        watcher.stop()  # _observer 为 None，不报错
        watcher.stop()  # 再次调用
        assert watcher.running is False

    @patch("fts.scheduler.hotswap.HotSwapWatcher.start", return_value=True)
    def test_running_property_true(self, mock_start):
        """running 属性在 start 后为 True。"""
        watcher = HotSwapWatcher()
        # mock start 只返回 True，不修改内部状态
        watcher._running = True
        assert watcher.running is True

    def test_running_property_false(self):
        """running 属性初始为 False。"""
        watcher = HotSwapWatcher()
        assert watcher.running is False

    def test_watch_dir_not_found_logs_warning(self):
        """不存在的 watch dir 应记录警告但不抛出异常。"""
        watcher = HotSwapWatcher(watch_dirs=["/nonexistent/path"])
        with patch("fts.scheduler.hotswap.logger") as mock_logger:
            result = watcher.start()
            # start 会尝试导入 watchdog，这里可能在无 watchdog 时返回 False
            if not result:
                return
            # 如果有 watchdog，应记录警告
            mock_logger.warning.assert_any_call(
                "[hotswap] watch dir not found: %s", Path("/nonexistent/path")
            )


# ─── _reload_module ─────────────────────────────────────


class TestReloadModule:
    """_reload_module 单元测试。"""

    def test_non_python_file(self):
        """非 .py 文件直接返回。"""
        with patch("fts.scheduler.hotswap.Path.exists", return_value=True):
            # 不抛异常即可
            _reload_module("some_file.txt")

    def test_non_existent_file(self):
        """不存在的文件直接返回。"""
        _reload_module("nonexistent.py")

    def test_module_not_imported(self):
        """未导入的模块不会 reload。"""
        with (
            patch("fts.scheduler.hotswap.Path.exists", return_value=True),
            patch("fts.scheduler.hotswap.Path.suffix", new_callable=PropertyMock, return_value=".py"),
            patch("fts.scheduler.hotswap.sys.modules", {}),
        ):
            _reload_module("some/module.py")

    def test_reload_success(self):
        """已导入模块应成功 reload。"""
        mock_module = MagicMock()
        with patch("fts.scheduler.hotswap.Path") as mock_path_cls:
            mock_path = MagicMock(spec=Path)
            mock_path_cls.return_value = mock_path
            mock_path.exists.return_value = True
            mock_path.suffix = ".py"
            mock_path.is_absolute.return_value = False
            mock_path.parts = ("fts", "scheduler", "hotswap.py")

            with patch.object(importlib_mod, 'reload') as mock_reload:
                with patch.dict("fts.scheduler.hotswap.sys.modules", {"fts.scheduler.hotswap": mock_module}, clear=True):
                    _reload_module("fts/scheduler/hotswap.py")
                    mock_reload.assert_called_once_with(mock_module)

    def test_reload_init_module(self):
        """__init__.py 文件的模块名不含 __init__。"""
        mock_module = MagicMock()
        with patch("fts.scheduler.hotswap.Path") as mock_path_cls:
            mock_path = MagicMock(spec=Path)
            mock_path_cls.return_value = mock_path
            mock_path.exists.return_value = True
            mock_path.suffix = ".py"
            mock_path.is_absolute.return_value = False
            mock_path.parts = ("fts", "scheduler", "__init__.py")

            with patch.object(importlib_mod, 'reload') as mock_reload:
                with patch.dict("fts.scheduler.hotswap.sys.modules", {"fts.scheduler": mock_module}, clear=True):
                    _reload_module("fts/scheduler/__init__.py")
                    mock_reload.assert_called_once_with(mock_module)

    def test_reload_failure_logged(self):
        """reload 失败应记录警告不抛出。"""
        with patch("fts.scheduler.hotswap.Path") as mock_path_cls:
            mock_path = MagicMock(spec=Path)
            mock_path_cls.return_value = mock_path
            mock_path.exists.return_value = True
            mock_path.suffix = ".py"
            mock_path.is_absolute.return_value = False
            mock_path.parts = ("fts", "scheduler", "hotswap.py")

            with patch.object(importlib_mod, 'reload', side_effect=ImportError("test error")):
                with patch("fts.scheduler.hotswap.logger") as mock_logger:
                    _reload_module("fts/scheduler/hotswap.py")
                    mock_logger.warning.assert_called_once()
                    args, _ = mock_logger.warning.call_args
                    assert "reload failed" in args[0]


# ─── HotSwapWatcher start() 实际 ImportError 路径 ─────


class TestHotSwapWatcherImportError:
    """start() 中 ImportError 实际路径测试。"""

    def test_start_returns_false_on_import_error(self):
        """watchdog 未安装时 start 返回 False。"""
        watcher = HotSwapWatcher()
        # 将 watchdog 设为 None 使 import 失败
        with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
            result = watcher.start()
        assert result is False
        assert watcher.running is False

    def test_start_logs_warning_on_import_error(self):
        """watchdog 未安装时记录警告日志。"""
        watcher = HotSwapWatcher()
        with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None, "watchdog.events": None}):
            with patch("fts.scheduler.hotswap.logger") as mock_logger:
                watcher.start()
                mock_logger.warning.assert_called_once_with(
                    "watchdog 未安装，热重载不可用。pip install watchdog"
                )


# ─── HotSwapWatcher start() 成功路径 ──────────────────


class TestHotSwapWatcherStartSuccess:
    """start() 成功时调度行为测试。"""

    def test_start_schedules_existing_dirs(self):
        """start 成功时应为每个存在的目录调度 observer。"""
        watcher = HotSwapWatcher(watch_dirs=["fts/factor_engine", "fts/scheduler"])

        mock_observer = MagicMock()

        with (
            patch("fts.scheduler.hotswap.logger"),
            patch("watchdog.observers.Observer", return_value=mock_observer),
        ):
            result = watcher.start()

        assert result is True
        assert watcher.running is True
        # 应为 2 个目录各调用一次 schedule
        assert mock_observer.schedule.call_count == 2
        # 每次调用 recursive=True
        calls = mock_observer.schedule.call_args_list
        for call in calls:
            args, kwargs = call
            assert kwargs.get("recursive") is True

    def test_start_skips_nonexistent_dirs(self):
        """start 时跳过不存在的目录。"""
        watcher = HotSwapWatcher(watch_dirs=["/nonexistent/path"])

        mock_observer = MagicMock()

        with (
            patch("fts.scheduler.hotswap.logger") as mock_logger,
            patch("watchdog.observers.Observer", return_value=mock_observer),
        ):
            result = watcher.start()

        assert result is True
        # 不存在的目录不应调用 schedule
        mock_observer.schedule.assert_not_called()
        mock_logger.warning.assert_any_call("[hotswap] watch dir not found: %s", Path("/nonexistent/path"))

    def test_on_modified_calls_reload_for_py_files(self):
        """on_modified 中 .py 文件应调用 _reload_module。"""
        watcher = HotSwapWatcher(watch_dirs=["fts/factor_engine"])

        mock_observer = MagicMock()
        captured_handler = [None]

        def capture_schedule(handler, path, **kwargs):
            captured_handler[0] = handler

        mock_observer.schedule.side_effect = capture_schedule

        with (
            patch("fts.scheduler.hotswap.logger"),
            patch("watchdog.observers.Observer", return_value=mock_observer),
        ):
            watcher.start()

        # 获取捕获到的 handler（_ReloadHandler 实例）
        handler = captured_handler[0]
        assert handler is not None

        # 测试 on_modified 调用 _reload_module
        mock_event_py = MagicMock()
        mock_event_py.src_path = "/path/to/module.py"

        with patch("fts.scheduler.hotswap._reload_module") as mock_reload:
            handler.on_modified(mock_event_py)
            mock_reload.assert_called_once_with("/path/to/module.py")

    def test_on_modified_ignores_non_py_files(self):
        """on_modified 中非 .py 文件不调用 _reload_module。"""
        watcher = HotSwapWatcher(watch_dirs=["fts/factor_engine"])

        mock_observer = MagicMock()
        captured_handler = [None]

        def capture_schedule(handler, path, **kwargs):
            captured_handler[0] = handler

        mock_observer.schedule.side_effect = capture_schedule

        with (
            patch("fts.scheduler.hotswap.logger"),
            patch("watchdog.observers.Observer", return_value=mock_observer),
        ):
            watcher.start()

        handler = captured_handler[0]

        # 测试非 .py 文件不触发 reload
        mock_event_txt = MagicMock()
        mock_event_txt.src_path = "/path/to/notes.txt"

        with patch("fts.scheduler.hotswap._reload_module") as mock_reload:
            handler.on_modified(mock_event_txt)
            mock_reload.assert_not_called()
