"""tests/scheduler/test_engine.py — FTS 调度器引擎单元测试。

HARNESS §测试随重构: 全量覆盖 engine.py，目标 70%+ line coverage。

测试策略:
    - 使用 unittest.mock 模拟 APScheduler 导入及行为
    - 使用 pytest fixture 隔离测试状态
    - 覆盖正常路径、异常路径、降级路径
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from fts.scheduler.engine import SchedulerEngine, run_scheduler
from fts.scheduler.tasks import REGISTRY, TaskRegistry, TaskSpec, register_default_tasks


# ─── Fixtures ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个测试前清空全局 REGISTRY（避免测试间状态污染）。"""
    keys = list(REGISTRY._tasks.keys())
    for k in keys:
        REGISTRY.unregister(k)
    assert len(REGISTRY) == 0
    yield
    keys = list(REGISTRY._tasks.keys())
    for k in keys:
        REGISTRY.unregister(k)


@pytest.fixture
def fresh_registry() -> TaskRegistry:
    """返回一个新的空 TaskRegistry（不触全局 REGISTRY）。

    注意: 空 TaskRegistry 的 __len__ 返回 0，bool() 为 False。
    如需用于"task_registry or REGISTRY"表达式，请先 register 一个任务。
    """
    return TaskRegistry()


@pytest.fixture
def sample_task() -> TaskSpec:
    """返回一个有效的示例任务。"""
    return TaskSpec(
        name="test_job",
        cron_expression="0 9 * * *",
        callable_path="some.module.func",
        description="测试任务",
    )


@pytest.fixture
def apscheduler_available() -> MagicMock:
    """使 APScheduler 在 sys.modules 中可用，并返回 mock BackgroundScheduler 实例。

    用法:
        def test_foo(apscheduler_available):
            mock_sched = apscheduler_available
            # ... 测试逻辑 ...
    """
    mock_sched = MagicMock()
    bg_module = MagicMock(spec=[])
    bg_module.BackgroundScheduler = MagicMock(return_value=mock_sched)

    schedulers_module = MagicMock(spec=[])
    schedulers_module.background = bg_module

    apscheduler_module = MagicMock(spec=[])
    apscheduler_module.schedulers = schedulers_module

    with patch.dict(
        "sys.modules",
        {
            "apscheduler": apscheduler_module,
            "apscheduler.schedulers": schedulers_module,
            "apscheduler.schedulers.background": bg_module,
        },
    ):
        yield mock_sched


@pytest.fixture
def engine_with_mock_scheduler() -> SchedulerEngine:
    """返回一个已注入 mock scheduler 的 SchedulerEngine 实例。"""
    engine = SchedulerEngine()
    engine._scheduler = MagicMock()
    engine._running = True
    return engine


# ─── __init__ ───────────────────────────────────────────


class TestSchedulerEngineInit:
    """SchedulerEngine.__init__ 测试。"""

    def test_default_registry(self):
        """默认使用全局 REGISTRY。"""
        engine = SchedulerEngine()
        assert engine._registry is REGISTRY
        assert engine._scheduler is None
        assert engine._running is False

    def test_custom_registry(self, fresh_registry: TaskRegistry):
        """传入自定义 registry。"""
        # 先注册一个任务使 TaskRegistry 的 bool() 为 True（避免 or 短路到 REGISTRY）
        fresh_registry.register(TaskSpec("t1", "* * * * *", "mod.fn"))
        engine = SchedulerEngine(task_registry=fresh_registry)
        assert engine._registry is fresh_registry
        assert engine._registry is not REGISTRY


# ─── running property ────────────────────────────────────


class TestSchedulerEngineRunning:
    """SchedulerEngine.running 属性测试。"""

    def test_running_default_false(self):
        """默认 running 为 False。"""
        engine = SchedulerEngine()
        assert engine.running is False

    def test_running_after_start(self, apscheduler_available: MagicMock):
        """启动后 running 为 True。"""
        engine = SchedulerEngine()
        result = engine.start(daemon=True)
        assert result is True
        assert engine.running is True

    def test_running_after_stop(self, engine_with_mock_scheduler: SchedulerEngine):
        """停止后 running 为 False。"""
        engine_with_mock_scheduler.stop()
        assert engine_with_mock_scheduler.running is False


# ─── start ──────────────────────────────────────────────


class TestSchedulerEngineStart:
    """SchedulerEngine.start() 测试。"""

    def test_start_no_apscheduler(self, caplog):
        """APScheduler 未安装时 start 返回 False。"""
        # 确保 apscheduler 不可导入
        with patch.dict("sys.modules", {"apscheduler": None}):
            # 需要让 import apscheduler 触发 ImportError
            # 用 patch 拦截 import
            import builtins

            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name.startswith("apscheduler"):
                    raise ImportError("No module named apscheduler")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                caplog.set_level(logging.WARNING)
                engine = SchedulerEngine()
                result = engine.start()
                assert result is False
                assert engine.running is False
                assert "APScheduler 未安装" in caplog.text

    def test_start_already_running_warns(self, engine_with_mock_scheduler: SchedulerEngine, caplog):
        """已运行时 start 发出警告并返回 True。"""
        caplog.set_level(logging.WARNING)
        result = engine_with_mock_scheduler.start()
        assert result is True
        assert engine_with_mock_scheduler.running is True
        assert "已在运行" in caplog.text

    def test_start_happy_path(self, apscheduler_available: MagicMock, sample_task: TaskSpec, caplog):
        """正常启动流程：注册默认任务、添加 job、启动 scheduler。"""
        # 先注册任务到全局 REGISTRY（start() 内部还会注册 4 个默认任务）
        REGISTRY.register(sample_task)

        caplog.set_level(logging.INFO)
        engine = SchedulerEngine()
        result = engine.start()

        assert result is True
        assert engine._scheduler is apscheduler_available
        assert engine.running is True
        apscheduler_available.start.assert_called_once()
        # 5 = 4 个默认任务 + 1 个 sample_task
        assert apscheduler_available.add_job.call_count == 5
        # 验证 sample_task 的 job 被加入
        job_calls = apscheduler_available.add_job.call_args_list
        test_job_call = next(
            (c for c in job_calls if c.kwargs.get("id") == "test_job"),
            None,
        )
        assert test_job_call is not None
        assert test_job_call.kwargs["minute"] == "0"
        assert test_job_call.kwargs["hour"] == "9"
        assert "已启动" in caplog.text
        assert "5 个任务" in caplog.text


# ─── stop ───────────────────────────────────────────────


class TestSchedulerEngineStop:
    """SchedulerEngine.stop() 测试。"""

    def test_stop_when_not_running(self, caplog):
        """未运行时 stop 为 no-op。"""
        caplog.set_level(logging.INFO)
        engine = SchedulerEngine()
        engine.stop()
        assert engine._scheduler is None
        assert engine.running is False
        # 应输出"已停止"日志
        assert "已停止" in caplog.text

    def test_stop_when_running(self, engine_with_mock_scheduler: SchedulerEngine, caplog):
        """运行时 stop 调用 scheduler.shutdown 并重置状态。"""
        mock_sched = engine_with_mock_scheduler._scheduler
        caplog.set_level(logging.INFO)
        engine_with_mock_scheduler.stop()

        mock_sched.shutdown.assert_called_once_with(wait=False)
        assert engine_with_mock_scheduler._scheduler is None
        assert engine_with_mock_scheduler.running is False
        assert "已停止" in caplog.text

    def test_stop_with_shutdown_exception(self, caplog):
        """shutdown 抛异常时被捕获并记录警告。"""
        mock_sched = MagicMock()
        mock_sched.shutdown.side_effect = RuntimeError("shutdown failed")
        engine = SchedulerEngine()
        engine._scheduler = mock_sched
        engine._running = True

        caplog.set_level(logging.WARNING)
        engine.stop()

        assert engine._scheduler is None
        assert engine.running is False
        assert "调度器关闭异常" in caplog.text
        assert "shutdown failed" in caplog.text

    def test_stop_with_scheduler_none_but_running(self, caplog):
        """_scheduler 为 None 但 _running 为 True 时安全降级。"""
        engine = SchedulerEngine()
        engine._scheduler = None
        engine._running = True

        caplog.set_level(logging.INFO)
        engine.stop()

        assert engine._scheduler is None
        assert engine.running is False
        assert "已停止" in caplog.text


# ─── _create_scheduler ──────────────────────────────────


class TestSchedulerEngineCreateScheduler:
    """SchedulerEngine._create_scheduler() 测试。"""

    def test_create_scheduler_unavailable(self, caplog):
        """APScheduler 不可用时返回 None。"""
        caplog.set_level(logging.WARNING)

        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("apscheduler"):
                raise ImportError("No module named apscheduler")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            engine = SchedulerEngine()
            result = engine._create_scheduler()
            assert result is None
            assert "APScheduler 未安装" in caplog.text

    def test_create_scheduler_available(self):
        """APScheduler 可用时返回 BackgroundScheduler 实例。"""
        mock_scheduler_class = MagicMock(return_value=MagicMock())
        with patch.dict(
            "sys.modules",
            {
                "apscheduler": MagicMock(),
                "apscheduler.schedulers": MagicMock(),
                "apscheduler.schedulers.background": MagicMock(),
            },
        ):
            with patch(
                "apscheduler.schedulers.background.BackgroundScheduler",
                mock_scheduler_class,
            ):
                engine = SchedulerEngine()
                scheduler = engine._create_scheduler(daemon=True)
                assert scheduler is mock_scheduler_class.return_value
                mock_scheduler_class.assert_called_once_with(daemon=True)

    def test_create_scheduler_daemon_false(self):
        """daemon=False 传递给 BackgroundScheduler。"""
        mock_scheduler_class = MagicMock(return_value=MagicMock())
        with patch.dict(
            "sys.modules",
            {
                "apscheduler": MagicMock(),
                "apscheduler.schedulers": MagicMock(),
                "apscheduler.schedulers.background": MagicMock(),
            },
        ):
            with patch(
                "apscheduler.schedulers.background.BackgroundScheduler",
                mock_scheduler_class,
            ):
                engine = SchedulerEngine()
                scheduler = engine._create_scheduler(daemon=False)
                assert scheduler is mock_scheduler_class.return_value
                mock_scheduler_class.assert_called_once_with(daemon=False)


# ─── _cron_field ────────────────────────────────────────


class TestSchedulerEngineCronField:
    """SchedulerEngine._cron_field() 测试。"""

    def test_normal_case(self):
        """正常 5 字段提取。"""
        expr = "0 9 * * 1"
        assert SchedulerEngine._cron_field(expr, 0, "*") == "0"
        assert SchedulerEngine._cron_field(expr, 1, "*") == "9"
        assert SchedulerEngine._cron_field(expr, 2, "*") == "*"
        assert SchedulerEngine._cron_field(expr, 3, "*") == "*"
        assert SchedulerEngine._cron_field(expr, 4, "*") == "1"

    def test_out_of_range_index(self):
        """index 超出范围时返回 default。"""
        expr = "0 9 * * *"
        assert SchedulerEngine._cron_field(expr, 5, "*") == "*"
        assert SchedulerEngine._cron_field(expr, 10, "?") == "?"

    def test_default_fallback(self):
        """空表达式或 index=0 时返回 default。"""
        # 空字符串拆分后只有一个元素
        assert SchedulerEngine._cron_field("", 0, "*") == "*"
        # 只有一个字段，取 index=1 返回 default
        assert SchedulerEngine._cron_field("*/10", 1, "*") == "*"


# ─── _add_job ────────────────────────────────────────────


class TestSchedulerEngineAddJob:
    """SchedulerEngine._add_job() 测试。"""

    def test_add_job_valid(self, sample_task: TaskSpec):
        """有效任务调用 scheduler.add_job 并传递正确参数。"""
        mock_sched = MagicMock()
        engine = SchedulerEngine()

        engine._add_job(mock_sched, sample_task)

        mock_sched.add_job.assert_called_once()
        call_kwargs = mock_sched.add_job.call_args[1]
        assert call_kwargs["trigger"] == "cron"
        assert call_kwargs["minute"] == "0"
        assert call_kwargs["hour"] == "9"
        assert call_kwargs["day"] == "*"
        assert call_kwargs["month"] == "*"
        assert call_kwargs["day_of_week"] == "*"
        assert call_kwargs["id"] == "test_job"
        assert call_kwargs["name"] == "测试任务"
        assert call_kwargs["replace_existing"] is False

    def test_add_job_invalid_cron(self, caplog):
        """无效 cron 表达式导致异常，被捕获并记录错误。"""
        mock_sched = MagicMock()
        mock_sched.add_job.side_effect = ValueError("invalid cron expression")
        task = TaskSpec(
            name="bad_cron",
            cron_expression="not a valid cron",
            callable_path="mod.fn",
        )
        engine = SchedulerEngine()

        caplog.set_level(logging.ERROR)
        engine._add_job(mock_sched, task)

        mock_sched.add_job.assert_called_once()
        assert "任务注册失败" in caplog.text
        assert "bad_cron" in caplog.text
        assert "invalid cron expression" in caplog.text

    def test_add_job_with_star_slash_cron(self):
        """处理 */N 格式的 cron 字段。"""
        mock_sched = MagicMock()
        task = TaskSpec(
            name="health_check",
            cron_expression="*/10 * * * *",
            callable_path="mod.check_all",
        )
        engine = SchedulerEngine()

        engine._add_job(mock_sched, task)

        call_kwargs = mock_sched.add_job.call_args[1]
        assert call_kwargs["minute"] == "*/10"
        assert call_kwargs["hour"] == "*"


# ─── _make_job_fn ────────────────────────────────────────


class TestSchedulerEngineMakeJobFn:
    """SchedulerEngine._make_job_fn() 测试。

    覆盖:
        - 正常执行路径
        - 无效模块路径
        - 模块存在但函数缺失
        - 执行中抛异常
        - trace_id 传播
    """

    def test_successful_execution(self, caplog):
        """有效 callable_path 成功导入并执行。"""
        mock_func = MagicMock()
        mock_module = MagicMock()
        mock_module.func = mock_func

        with patch("importlib.import_module", return_value=mock_module):
            caplog.set_level(logging.INFO)
            task = TaskSpec(
                name="good_task",
                cron_expression="0 9 * * *",
                callable_path="some.module.func",
                description="正常运行的任务",
            )
            engine = SchedulerEngine()
            job_fn = engine._make_job_fn(task)
            job_fn()

            mock_func.assert_called_once()
            assert "任务开始" in caplog.text
            assert "good_task" in caplog.text
            assert "任务完成" in caplog.text

    def test_invalid_module_path(self, caplog):
        """无效模块路径导致 ImportError，被捕获并记录错误。"""
        with patch("importlib.import_module", side_effect=ImportError("no module named 'nonexistent'")):
            caplog.set_level(logging.ERROR)
            task = TaskSpec(
                name="bad_module",
                cron_expression="0 9 * * *",
                callable_path="nonexistent.module.func",
            )
            engine = SchedulerEngine()
            job_fn = engine._make_job_fn(task)
            job_fn()

            assert "任务失败" in caplog.text
            assert "bad_module" in caplog.text
            assert "no module named" in caplog.text

    def test_missing_function_in_module(self, caplog):
        """模块存在但函数缺失导致 AttributeError，被捕获并记录错误。"""
        mock_module = MagicMock()
        # 模块中没有 target_func
        del mock_module.target_func

        with patch("importlib.import_module", return_value=mock_module):
            caplog.set_level(logging.ERROR)
            task = TaskSpec(
                name="missing_func",
                cron_expression="0 9 * * *",
                callable_path="some.module.target_func",
            )
            engine = SchedulerEngine()
            job_fn = engine._make_job_fn(task)
            job_fn()

            assert "任务失败" in caplog.text
            assert "missing_func" in caplog.text

    def test_exception_during_execution(self, caplog):
        """函数执行中抛异常，被捕获并记录错误。"""
        mock_func = MagicMock(side_effect=RuntimeError("execution failed"))
        mock_module = MagicMock()
        mock_module.my_func = mock_func

        with patch("importlib.import_module", return_value=mock_module):
            caplog.set_level(logging.ERROR)
            task = TaskSpec(
                name="crash_task",
                cron_expression="0 9 * * *",
                callable_path="some.module.my_func",
            )
            engine = SchedulerEngine()
            job_fn = engine._make_job_fn(task)
            job_fn()

            assert "任务失败" in caplog.text
            assert "crash_task" in caplog.text
            assert "execution failed" in caplog.text

    def test_trace_id_in_log(self, caplog):
        """job_fn 执行日志中包含 trace_id。"""
        # 先注册默认任务，以便 make_trace_id 能找到 prefix
        register_default_tasks()

        mock_func = MagicMock()
        mock_module = MagicMock()
        mock_module.run = mock_func

        with patch("importlib.import_module", return_value=mock_module):
            caplog.set_level(logging.INFO)
            task = REGISTRY.get("l1_meta_loop")
            engine = SchedulerEngine()
            job_fn = engine._make_job_fn(task)
            job_fn()

            # 日志中应包含 trace_id（格式 fts.l1.xxx）
            assert "trace_id=" in caplog.text
            # 应包含前缀
            assert "fts.l1." in caplog.text

    def test_trace_id_propagation_to_log_record(self, caplog):
        """trace_id 在日志记录中可被检索到。"""
        # 必须注册到 REGISTRY，make_trace_id 才能找到正确的 prefix
        task = TaskSpec(
            name="custom_job",
            cron_expression="0 9 * * *",
            callable_path="some.module.fn",
            description="自定义任务",
            trace_id_prefix="fts.custom",
        )
        REGISTRY.register(task)

        mock_func = MagicMock()
        mock_module = MagicMock()
        mock_module.fn = mock_func

        with patch("importlib.import_module", return_value=mock_module):
            caplog.set_level(logging.INFO)
            engine = SchedulerEngine()
            job_fn = engine._make_job_fn(task)
            job_fn()

            # 验证 trace_id 出现在日志中
            trace_id_found = False
            for record in caplog.records:
                if "trace_id=" in record.getMessage():
                    trace_id_found = True
                    # 验证包含自定义前缀
                    assert "fts.custom." in record.getMessage()
                    break
            assert trace_id_found, "trace_id 未在日志中找到"

    def test_multiple_job_fn_isolation(self):
        """多次调用 _make_job_fn 返回不同的闭包（trace_id 不同）。"""
        task = TaskSpec(
            name="iso_task",
            cron_expression="0 9 * * *",
            callable_path="some.module.fn",
        )
        engine = SchedulerEngine()
        fn1 = engine._make_job_fn(task)
        fn2 = engine._make_job_fn(task)

        # 每次调用应返回新函数（闭包含不同 trace_id）
        assert fn1 is not fn2


# ─── run_scheduler ──────────────────────────────────────


class TestRunScheduler:
    """run_scheduler() 顶层函数测试。"""

    def test_run_scheduler_no_apscheduler(self, caplog):
        """APScheduler 不可用时 run_scheduler 记录错误并返回。"""
        caplog.set_level(logging.ERROR)

        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("apscheduler"):
                raise ImportError("No module named apscheduler")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            run_scheduler(daemon=True)
            assert "调度器启动失败" in caplog.text
            assert "APScheduler 未安装" in caplog.text

    def test_run_scheduler_daemon_true(self, apscheduler_available: MagicMock):
        """daemon=True 时 start 后不进入阻塞循环。"""
        run_scheduler(daemon=True)
        # 不抛异常即通过，start 被调用
        assert apscheduler_available.start.called

    def test_run_scheduler_daemon_false_keyboard_interrupt(self, caplog):
        """daemon=False 时收到 KeyboardInterrupt 后正常停止。"""
        with patch(
            "fts.scheduler.engine.time.sleep",
            side_effect=KeyboardInterrupt,
        ):
            with patch("fts.scheduler.engine.SchedulerEngine._create_scheduler") as mock_create:
                mock_sched = MagicMock()
                mock_create.return_value = mock_sched

                caplog.set_level(logging.INFO)
                run_scheduler(daemon=False)

                # 验证 scheduler 被启动、停止
                assert mock_sched.start.called
                assert mock_sched.shutdown.called
                assert "收到中断信号" in caplog.text

    def test_run_scheduler_engine_start_returns_false(self, caplog):
        """engine.start() 返回 False 时记录错误。"""
        with patch(
            "fts.scheduler.engine.SchedulerEngine.start",
            return_value=False,
        ):
            caplog.set_level(logging.ERROR)
            run_scheduler(daemon=True)
            assert "调度器启动失败" in caplog.text


# ─── 集成场景 ────────────────────────────────────────────


class TestSchedulerEngineIntegration:
    """SchedulerEngine 集成场景测试。"""

    def test_start_then_stop_cycle(self, apscheduler_available: MagicMock, sample_task: TaskSpec):
        """完整的 start → 运行 → stop 生命周期。"""
        REGISTRY.register(sample_task)

        engine = SchedulerEngine()
        assert engine.running is False

        # start
        result = engine.start()
        assert result is True
        assert engine.running is True
        apscheduler_available.start.assert_called_once()

        # stop
        apscheduler_available.shutdown.reset_mock()
        engine.stop()
        assert engine.running is False
        apscheduler_available.shutdown.assert_called_once_with(wait=False)

    def test_double_start(self, apscheduler_available: MagicMock, sample_task: TaskSpec, caplog):
        """两次调用 start 第二次是 no-op。"""
        REGISTRY.register(sample_task)

        engine = SchedulerEngine()

        # 第一次 start
        result1 = engine.start()
        assert result1 is True
        assert engine.running is True
        apscheduler_available.start.assert_called_once()

        # 第二次 start
        caplog.set_level(logging.WARNING)
        result2 = engine.start()
        assert result2 is True
        assert "已在运行" in caplog.text
        # scheduler.start 仍然只被调用一次
        apscheduler_available.start.assert_called_once()

    def test_double_stop(self):
        """两次调用 stop 安全。"""
        mock_sched = MagicMock()
        engine = SchedulerEngine()
        engine._scheduler = mock_sched
        engine._running = True

        engine.stop()
        assert engine.running is False
        assert engine._scheduler is None
        mock_sched.shutdown.assert_called_once_with(wait=False)

        # 第二次 stop —— 此时 _scheduler 为 None，_running 为 False
        engine.stop()
        assert engine.running is False
        # shutdown 仍然只被调用一次
        mock_sched.shutdown.assert_called_once_with(wait=False)
