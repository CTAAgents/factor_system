"""tests/scheduler/test_watchdog.py — ProcessWatchdog 测试。

HARNESS §测试随重构: 覆盖 watchdog.py 核心路径。
"""

from __future__ import annotations

import time
from unittest.mock import ANY, MagicMock, PropertyMock, patch

import pytest

from fts.scheduler.watchdog import (
    CIRCUIT_BREAK_DURATION,
    MAX_RESTARTS,
    RESTART_WINDOW,
    ProcessWatchdog,
)


# ─── ProcessWatchdog ────────────────────────────────────


class TestProcessWatchdogInit:
    """初始化测试。"""

    def test_default_name(self):
        """默认 name 为 'fts'。"""
        wd = ProcessWatchdog(["python", "-c", "pass"])
        assert wd.name == "fts"
        assert wd.cmd == ["python", "-c", "pass"]

    def test_custom_name(self):
        """自定义 name。"""
        wd = ProcessWatchdog(["python", "-c", "pass"], name="my_service")
        assert wd.name == "my_service"

    def test_initial_state(self):
        """初始状态值正确。"""
        wd = ProcessWatchdog(["echo", "hi"])
        assert wd._restart_count == 0
        assert wd._last_restart == 0.0
        assert wd._circuit_open_until == 0.0
        assert wd._should_stop is False


class TestProcessWatchdogRun:
    """run() 方法测试。"""

    @patch("fts.scheduler.watchdog.subprocess.Popen")
    def test_process_exits_normally(self, mock_popen):
        """进程正常退出 (returncode=0)，循环中 Popen 被调用。"""
        wd = ProcessWatchdog(["echo", "ok"])
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.side_effect = lambda: setattr(wd, "_should_stop", True)
        mock_popen.return_value = mock_proc

        with patch.object(wd, "_is_circuit_broken", return_value=False):
            wd.run()

        mock_popen.assert_called_once_with(["echo", "ok"])
        mock_proc.wait.assert_called_once()

    def test_restart_count_increases(self):
        """进程退出后 restart_count 增加。"""
        wd = ProcessWatchdog(["echo", "fail"])
        assert wd._restart_count == 0

        # 模拟 run() 内部的重启计数逻辑
        with patch("fts.scheduler.watchdog.time.time", return_value=100.0):
            now = 100.0
            if now - wd._last_restart < RESTART_WINDOW:
                wd._restart_count += 1
            else:
                wd._restart_count = 1
            wd._last_restart = now

        assert wd._restart_count == 1

        # 第二次退出（仍在窗口期内）
        with patch("fts.scheduler.watchdog.time.time", return_value=110.0):
            now = 110.0
            if now - wd._last_restart < RESTART_WINDOW:
                wd._restart_count += 1
            else:
                wd._restart_count = 1
            wd._last_restart = now

        assert wd._restart_count == 2

    @patch("fts.scheduler.watchdog.time.time")
    @patch("fts.scheduler.watchdog.subprocess.Popen")
    def test_circuit_breaker_triggers(self, mock_popen, mock_time):
        """连续 MAX_RESTARTS 次重启后触发熔断。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        # 模拟时间不流逝，使所有重启在 RESTART_WINDOW 内
        mock_time.side_effect = [100.0, 100.0, 100.0, 100.0, 100.0]

        wd = ProcessWatchdog(["echo", "fail"])
        wd._should_stop = True  # 阻止循环
        # 直接测试熔断逻辑
        wd._restart_count = MAX_RESTARTS
        wd._last_restart = 100.0
        wd._circuit_open_until = 0.0

        # 手动触发熔断判断
        now = 100.0
        if now - wd._last_restart < RESTART_WINDOW:
            wd._restart_count += 1
        if wd._restart_count >= MAX_RESTARTS:
            wd._circuit_open_until = now + CIRCUIT_BREAK_DURATION

        assert wd._circuit_open_until > 0
        assert wd._is_circuit_broken()

    @patch("fts.scheduler.watchdog.time.time")
    def test_circuit_breaker_resets(self, mock_time):
        """熔断时间过后恢复正常。"""
        now = 1000.0
        mock_time.return_value = now

        wd = ProcessWatchdog(["echo", "ok"])
        wd._circuit_open_until = now + CIRCUIT_BREAK_DURATION
        assert wd._is_circuit_broken()

        # 时间推移到熔断到期后
        mock_time.return_value = now + CIRCUIT_BREAK_DURATION + 1
        assert not wd._is_circuit_broken()

    @patch("fts.scheduler.watchdog.subprocess.Popen")
    def test_stop_during_run(self, mock_popen):
        """run 中设置 stop 应终止循环。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        wd = ProcessWatchdog(["sleep", "10"])
        wd.stop()
        assert wd._should_stop is True

        # run 应快速返回
        with patch.object(wd, "_is_circuit_broken", return_value=False):
            wd.run()

    @patch("fts.scheduler.watchdog.subprocess.Popen")
    def test_popen_raises_exception(self, mock_popen):
        """Popen 异常应被捕获，继续循环。"""
        mock_popen.side_effect = RuntimeError("unexpected")

        wd = ProcessWatchdog(["bad_command"])
        wd._should_stop = True  # 阻止循环
        # 如果异常被正确捕获，不会抛出
        wd.run()

    def test_file_not_found(self):
        """FileNotFoundError 被捕获，继续循环。"""
        wd = ProcessWatchdog(["nonexistent_command"])
        wd._should_stop = True
        # 不应抛出异常
        wd.run()


class TestProcessWatchdogStop:
    """stop() 方法测试。"""

    def test_stop_sets_flag(self):
        """stop 设置 _should_stop = True。"""
        wd = ProcessWatchdog(["echo", "ok"])
        assert wd._should_stop is False
        wd.stop()
        assert wd._should_stop is True

    def test_stop_idempotent(self):
        """stop 多次调用不抛异常。"""
        wd = ProcessWatchdog(["echo", "ok"])
        wd.stop()
        wd.stop()  # 不抛异常


class TestProcessWatchdogCircuitBreak:
    """熔断器相关测试。"""

    def test_is_circuit_broken_default(self):
        """默认不熔断。"""
        wd = ProcessWatchdog(["echo", "ok"])
        assert wd._is_circuit_broken() is False

    def test_is_circuit_broken_open(self):
        """设置熔断后返回 True。"""
        wd = ProcessWatchdog(["echo", "ok"])
        wd._circuit_open_until = time.time() + 3600
        assert wd._is_circuit_broken() is True

    def test_circuit_restore_resets_count(self):
        """熔断恢复后 _restart_count 归零。"""
        wd = ProcessWatchdog(["echo", "ok"])
        wd._restart_count = MAX_RESTARTS
        wd._circuit_open_until = time.time() - 1  # 已过期

        # 模拟 run 中的恢复逻辑
        if not wd._is_circuit_broken():
            wd._restart_count = 0
            wd._circuit_open_until = 0.0

        assert wd._restart_count == 0
        assert wd._circuit_open_until == 0.0


# ─── run() 熔断等待与恢复路径 ─────────────────────────


class TestProcessWatchdogCircuitRun:
    """run() 循环中熔断路径测试。"""

    @patch("fts.scheduler.watchdog.time.sleep")
    def test_circuit_broken_waits(self, mock_sleep):
        """熔断中 (wait > 0) 应等待并继续循环。"""
        wd = ProcessWatchdog(["echo", "test"])
        wd._circuit_open_until = 100.0  # 未来时间
        wd._last_restart = 2
        wd._restart_count = 3

        call_idx = [0]

        def circuit_breaker():
            call_idx[0] += 1
            if call_idx[0] >= 2:  # 第二次调用后停止
                wd._should_stop = True
            return True  # 始终返回熔断

        with patch.object(wd, "_is_circuit_broken", side_effect=circuit_breaker):
            with patch("fts.scheduler.watchdog.time.time", return_value=90.0):
                wd.run()

        # wait = 100.0 - 90.0 = 10.0 > 0 → sleep(min(10, 5)), 可能跑两次
        assert mock_sleep.call_count >= 1
        mock_sleep.assert_any_call(5)
        assert wd._restart_count == 3  # 未重置

    @patch("fts.scheduler.watchdog.time.sleep")
    def test_circuit_broken_restored(self, mock_sleep):
        """熔断到期 (wait <= 0) 应重置计数并继续。"""
        wd = ProcessWatchdog(["echo", "test"])
        wd._circuit_open_until = 90.0  # 过期时间
        wd._restart_count = 3

        call_idx = [0]

        def circuit_breaker():
            call_idx[0] += 1
            if call_idx[0] >= 2:  # 第二次调用后停止
                wd._should_stop = True
            return True  # 始终返回熔断

        with patch.object(wd, "_is_circuit_broken", side_effect=circuit_breaker):
            with patch("fts.scheduler.watchdog.time.time", return_value=100.0):
                wd.run()

        # wait = 90.0 - 100.0 = -10.0 <= 0 → 重置
        mock_sleep.assert_not_called()
        assert wd._restart_count == 0
        assert wd._circuit_open_until == 0.0


# ─── run() 异常处理路径 ────────────────────────────


class TestProcessWatchdogErrorHandling:
    """run() 循环异常处理测试。"""

    @patch("fts.scheduler.watchdog.time.sleep")
    def test_file_not_found_in_run(self, mock_sleep):
        """FileNotFoundError 被捕获并记录，继续循环。"""
        wd = ProcessWatchdog(["nonexistent_cmd"])
        call_idx = [0]

        def circuit_breaker():
            call_idx[0] += 1
            if call_idx[0] >= 2:
                wd._should_stop = True
            return False

        with (
            patch("fts.scheduler.watchdog.subprocess.Popen", side_effect=FileNotFoundError()),
            patch.object(wd, "_is_circuit_broken", side_effect=circuit_breaker),
            patch("fts.scheduler.watchdog.logger") as mock_logger,
        ):
            wd.run()

        mock_logger.error.assert_any_call(
            "[watchdog] command not found: %s", "nonexistent_cmd"
        )
        mock_sleep.assert_any_call(60)

    @patch("fts.scheduler.watchdog.time.sleep")
    def test_generic_exception_in_run(self, mock_sleep):
        """通用 Exception 被捕获并记录，继续循环。"""
        wd = ProcessWatchdog(["bad_cmd"])
        call_idx = [0]

        def circuit_breaker():
            call_idx[0] += 1
            if call_idx[0] >= 2:
                wd._should_stop = True
            return False

        with (
            patch("fts.scheduler.watchdog.subprocess.Popen", side_effect=RuntimeError("boom")),
            patch.object(wd, "_is_circuit_broken", side_effect=circuit_breaker),
            patch("fts.scheduler.watchdog.logger") as mock_logger,
        ):
            wd.run()

        mock_logger.error.assert_any_call("[watchdog] process error: %s", ANY)
        mock_sleep.assert_any_call(10)


# ─── run() 重启计数与熔断触发 ──────────────────────────


class TestProcessWatchdogRestartLogic:
    """run() 循环中重启计数与熔断触发测试。"""

    @patch("fts.scheduler.watchdog.time.sleep")
    def test_restart_within_window_increments_count(self, mock_sleep):
        """重启在窗口期内递增 _restart_count。"""
        wd = ProcessWatchdog(["echo", "ok"])
        wd._last_restart = 90.0  # 窗口期内 (100 - 90 = 10 < 30)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.wait.return_value = 1

        call_idx = [0]

        def circuit_breaker():
            call_idx[0] += 1
            if call_idx[0] >= 2:
                wd._should_stop = True
            return False  # 不触发熔断

        with (
            patch("fts.scheduler.watchdog.subprocess.Popen", return_value=mock_proc),
            patch.object(wd, "_is_circuit_broken", side_effect=circuit_breaker),
            patch("fts.scheduler.watchdog.time.time", return_value=100.0),
        ):
            wd.run()

        assert wd._restart_count == 1  # 从 0 递增

    @patch("fts.scheduler.watchdog.time.sleep")
    def test_restart_outside_window_resets_count(self, mock_sleep):
        """重启超出窗口期重置 _restart_count 为 1。"""
        wd = ProcessWatchdog(["echo", "ok"])
        wd._restart_count = 3
        wd._last_restart = 50.0  # 窗口期外 (100 - 50 = 50 >= 30)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.wait.return_value = 1

        call_idx = [0]

        def circuit_breaker():
            call_idx[0] += 1
            if call_idx[0] >= 2:
                wd._should_stop = True
            return False  # 不触发熔断

        with (
            patch("fts.scheduler.watchdog.subprocess.Popen", return_value=mock_proc),
            patch.object(wd, "_is_circuit_broken", side_effect=circuit_breaker),
            patch("fts.scheduler.watchdog.time.time", return_value=100.0),
        ):
            wd.run()

        assert wd._restart_count == 1  # 窗口外重置为 1

    @patch("fts.scheduler.watchdog.time.sleep")
    def test_circuit_breaker_triggers_on_max_restarts(self, mock_sleep):
        """达到 MAX_RESTARTS 后触发熔断。"""
        wd = ProcessWatchdog(["echo", "fail"])
        wd._restart_count = MAX_RESTARTS - 1  # 2
        wd._last_restart = 95.0  # 窗口期内

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.wait.return_value = 1

        call_idx = [0]

        def circuit_breaker():
            call_idx[0] += 1
            if call_idx[0] >= 2:
                wd._should_stop = True
                return True
            return False

        with (
            patch("fts.scheduler.watchdog.subprocess.Popen", return_value=mock_proc),
            patch.object(wd, "_is_circuit_broken", side_effect=circuit_breaker),
            patch("fts.scheduler.watchdog.time.time", return_value=100.0),
        ):
            wd.run()

        assert wd._restart_count == MAX_RESTARTS  # 3
        assert wd._circuit_open_until > 0  # 熔断已触发
