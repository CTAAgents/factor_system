"""tests/scheduler/test_jobs.py — FTS 定时任务工作函数单元测试。

HARNESS §测试随重构: 全量覆盖 jobs.py（除 sync_futures_data_job 由
test_sync_futures_task.py 覆盖，此处仅补其未覆盖分支）。

测试策略:
    - 各 job 内均为延迟导入（函数内 from-import），通过 patch.dict
      向 sys.modules 注入 fake 模块以隔离真实外部依赖（LLM / DuckDB / 数据源）
    - 覆盖每个 job 的成功路径、分支路径与异常捕获路径
"""

from __future__ import annotations

import gzip
import json
import logging
import sys
import types
from unittest.mock import MagicMock, patch


def _fake_module(**attrs: object) -> types.ModuleType:
    """构造带指定属性的 fake 模块对象。"""
    mod = types.ModuleType("fake_module")
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _fake_cfg(**attrs: object) -> MagicMock:
    """构造带指定字段的配置对象（默认市场 futures，匹配 FTS_DEFAULT_MARKET 未设时语义）。"""
    return MagicMock(
        memory_dir="/tmp/mem",
        elite_dir="/tmp/elite",
        futures_elite_dir="/tmp/futures_elite",
        default_market="futures",
        **attrs,
    )


def _make_kline_df(n: int = 5, price: float = 3500.0):
    """构造测试 K 线 DataFrame。"""
    import pandas as pd

    return pd.DataFrame(
        {
            "close": [price] * n,
            "volume": [100_000] * n,
        }
    )


def _make_cfg_module() -> types.ModuleType:
    """构造 fake fts.config 模块（get_config 返回 /tmp 配置）。"""
    return _fake_module(get_config=MagicMock(return_value=_fake_cfg()))


def _fake_reaudit_mod(counts=None) -> types.ModuleType:
    """构造 fake fts.monitor.reaudit 模块（run_reaudit 返回假 report）。"""
    counts = counts or {"retain": 0, "shadow": 0, "retire": 0, "error": 0}
    report = MagicMock(total=0, counts=counts)
    return _fake_module(run_reaudit=MagicMock(return_value=report))


# 确保父包已加载，patch 子模块时不会触发 "Parent module not loaded"
import fts.scheduler.jobs as jobs  # noqa: E402
import fts.factor_engine  # noqa: E402,F401
import fts.llm  # noqa: E402,F401
import fts.config  # noqa: E402,F401
import fts.monitor.prometheus_metrics  # noqa: E402,F401  确保 fts.monitor 属性存在（patch 需解析）


# ─── L1 Meta-Loop ────────────────────────────────────────


class TestL1MetaLoopJob:
    """l1_meta_loop_job 测试。"""

    def test_success(self, caplog):
        """成功路径：构造 MetaLoop 并运行，日志输出完成状态。"""
        result = MagicMock(status="ok", injected_candidate_ids=["a", "b"])
        fake_meta = _fake_module(MetaLoop=MagicMock(), _make_web_collector=MagicMock(return_value=MagicMock()))
        fake_meta.MetaLoop.return_value.run.return_value = result
        fake_llm = _fake_module(get_llm_client=MagicMock(return_value=MagicMock()))
        fake_cfg = _make_cfg_module()

        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.meta_loop": fake_meta,
                "fts.llm": fake_llm,
                "fts.config": fake_cfg,
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.l1_meta_loop_job()

        assert "[L1] 完成: status=ok injected=2" in caplog.text
        fake_meta.MetaLoop.assert_called_once()
        fake_meta.MetaLoop.return_value.run.assert_called_once()

    def test_success_logs_trace_id(self, caplog):
        """启动日志包含 trace_id。"""
        fake_meta = _fake_module(MetaLoop=MagicMock(), _make_web_collector=MagicMock(return_value=MagicMock()))
        fake_llm = _fake_module(get_llm_client=MagicMock())
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.meta_loop": fake_meta,
                "fts.llm": fake_llm,
                "fts.config": _make_cfg_module(),
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.l1_meta_loop_job()

        assert "[L1] Meta-Loop 启动 trace_id=fts.l1.sched_" in caplog.text

    def test_failure_caught(self, caplog):
        """MetaLoop 构造失败时捕获并记录错误，不抛出。"""
        fake_meta = _fake_module(MetaLoop=MagicMock(side_effect=RuntimeError("llm unavailable")), _make_web_collector=MagicMock(return_value=MagicMock()))
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.meta_loop": fake_meta,
                "fts.llm": _fake_module(get_llm_client=MagicMock()),
                "fts.config": _make_cfg_module(),
            },
        ):
            caplog.set_level(logging.ERROR)
            jobs.l1_meta_loop_job()  # 不应抛出

        assert "[L1] 运行失败: llm unavailable" in caplog.text


# ─── L2 Evolution Loop ───────────────────────────────────


class TestL2EvolutionLoopJob:
    """l2_evolution_loop_job 测试。"""

    def _build_mocks(self, panel=None, subset=None, holdout=None, loop_error=None):
        import pandas as pd
        import numpy as np

        df = pd.DataFrame({"close": np.linspace(100, 110, 10)})
        panel = panel if panel is not None else {"RB": df}

        fake_evolution = _fake_module(EvolutionLoop=MagicMock())
        loop = fake_evolution.EvolutionLoop.return_value
        loop.run.return_value = MagicMock(status="ok", elite_factor_ids=["f1", "f2"])
        if loop_error:
            fake_evolution = _fake_module(EvolutionLoop=MagicMock(side_effect=loop_error))

        fake_verifier = _fake_module(FactorVerifier=MagicMock())
        fake_seed = _fake_module(SeedPool=MagicMock())
        fake_contracts = _fake_module(DEFAULT_BUDGET_CONFIG={"max_generation": 30})

        fake_data = _fake_module(FTSDataProvider=MagicMock())
        fake_data.FTSDataProvider.return_value.get_futures_panel.return_value = (
            panel,
            ["2024-01-01"],
        )

        fake_data_futures = _fake_module(
            FUTURES_STRATIFIED_SUBSET=(
                subset if subset is not None else ["RB", "HC", "I", "J", "JM", "FG", "MA", "TA", "RU", "NR", "FU", "SC"]
            ),
            FUTURES_HOLDOUT=(holdout if holdout is not None else ["SC", "NR"]),
        )

        fake_llm = _fake_module(
            MockLLMClient=MagicMock(),
            get_llm_client=MagicMock(return_value=MagicMock()),
        )

        return fake_evolution, fake_verifier, fake_seed, fake_contracts, fake_data, fake_data_futures, fake_llm

    def test_success(self, caplog):
        """成功路径：拉取期货面板并运行演化。"""
        fake_evolution, fake_verifier, fake_seed, fake_contracts, fake_data, fake_data_futures, fake_llm = (
            self._build_mocks()
        )
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.evolution_loop": fake_evolution,
                "fts.factor_engine.verifier": fake_verifier,
                "fts.factor_engine.seed_pool": fake_seed,
                "fts.factor_engine.contracts": fake_contracts,
                "fts.data": fake_data,
                "fts.data_futures": fake_data_futures,
                "fts.llm": fake_llm,
                "fts.config": _make_cfg_module(),
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.l2_evolution_loop_job()

        assert "[L2][weekday] 分层训练品种: 10 个" in caplog.text
        assert "[L2][weekday] 完成: status=ok elite=2" in caplog.text

    def test_insufficient_train_symbols(self, caplog):
        """排除盲测品种后不足 10 个时跳过。"""
        fake_evolution, fake_verifier, fake_seed, fake_contracts, fake_data, fake_data_futures, fake_llm = (
            self._build_mocks(subset=["RB"], holdout=[])
        )
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.evolution_loop": fake_evolution,
                "fts.factor_engine.verifier": fake_verifier,
                "fts.factor_engine.seed_pool": fake_seed,
                "fts.factor_engine.contracts": fake_contracts,
                "fts.data": fake_data,
                "fts.data_futures": fake_data_futures,
                "fts.llm": fake_llm,
                "fts.config": _make_cfg_module(),
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.l2_evolution_loop_job()

        assert "[L2][weekday] 训练品种不足" in caplog.text
        # 未进入演化流程
        fake_evolution.EvolutionLoop.assert_not_called()

    def test_no_futures_panel(self, caplog):
        """面板为空时跳过。"""
        fake_evolution, fake_verifier, fake_seed, fake_contracts, fake_data, fake_data_futures, fake_llm = (
            self._build_mocks(panel={})
        )
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.evolution_loop": fake_evolution,
                "fts.factor_engine.verifier": fake_verifier,
                "fts.factor_engine.seed_pool": fake_seed,
                "fts.factor_engine.contracts": fake_contracts,
                "fts.data": fake_data,
                "fts.data_futures": fake_data_futures,
                "fts.llm": fake_llm,
                "fts.config": _make_cfg_module(),
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.l2_evolution_loop_job()

        assert "[L2][weekday] 无期货数据，跳过" in caplog.text

    def test_failure_caught(self, caplog):
        """演化运行失败时捕获并记录错误。"""
        fake_evolution, fake_verifier, fake_seed, fake_contracts, fake_data, fake_data_futures, fake_llm = (
            self._build_mocks(loop_error=RuntimeError("evolution crash"))
        )
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.evolution_loop": fake_evolution,
                "fts.factor_engine.verifier": fake_verifier,
                "fts.factor_engine.seed_pool": fake_seed,
                "fts.factor_engine.contracts": fake_contracts,
                "fts.data": fake_data,
                "fts.data_futures": fake_data_futures,
                "fts.llm": fake_llm,
                "fts.config": _make_cfg_module(),
            },
        ):
            caplog.set_level(logging.ERROR)
            jobs.l2_evolution_loop_job()  # 不应抛出

        assert "[L2][weekday] 运行失败: evolution crash" in caplog.text


# ─── L3 Portfolio Loop ───────────────────────────────────


class TestL3PortfolioLoopJob:
    """l3_portfolio_loop_job 测试。"""

    def test_success(self, caplog):
        """成功路径：期货组合构建（futures_elite + market=futures），与信号管道解绑不联动。"""
        result = MagicMock(status="ok", n_factors_retained=5, combo_sharpe=1.2345)
        fake_portfolio = _fake_module(PortfolioLoop=MagicMock())
        fake_portfolio.PortfolioLoop.return_value.run.return_value = result

        with (
            patch.dict(
                sys.modules,
                {
                    "fts.factor_engine.portfolio_loop": fake_portfolio,
                    "fts.config": _make_cfg_module(),
                },
            ),
            patch("fts.scheduler.jobs._run_futures_signal_pipeline") as mock_pipeline,
        ):
            caplog.set_level(logging.INFO)
            jobs.l3_portfolio_loop_job()

        assert "[L3] 完成: status=ok retained=5 sharpe=1.2345" in caplog.text
        mock_pipeline.assert_not_called()  # GAP-072: L3 与信号管道解绑，不再联动触发

    def test_uses_futures_path(self, caplog):
        """显式期货路径：elite_dir=futures_elite_dir + market="futures"（v2.73.0）。"""
        result = MagicMock(status="ok", n_factors_retained=5, combo_sharpe=1.2345)
        fake_portfolio = _fake_module(PortfolioLoop=MagicMock())
        fake_portfolio.PortfolioLoop.return_value.run.return_value = result

        with (
            patch.dict(
                sys.modules,
                {
                    "fts.factor_engine.portfolio_loop": fake_portfolio,
                    "fts.config": _make_cfg_module(),
                },
            ),
            patch("fts.scheduler.jobs._run_futures_signal_pipeline"),
        ):
            jobs.l3_portfolio_loop_job()

        _, kwargs = fake_portfolio.PortfolioLoop.call_args
        assert kwargs["market"] == "futures"
        assert kwargs["elite_dir"] == "/tmp/futures_elite"
        assert kwargs["elite_dir"] != "/tmp/elite"  # 不再误用股票 elite 目录

    def test_failure_caught(self, caplog):
        """组合构建失败时捕获并记录错误。"""
        fake_portfolio = _fake_module(PortfolioLoop=MagicMock(side_effect=RuntimeError("portfolio crash")))
        with (
            patch.dict(
                sys.modules,
                {
                    "fts.factor_engine.portfolio_loop": fake_portfolio,
                    "fts.config": _make_cfg_module(),
                },
            ),
            patch("fts.scheduler.jobs._run_futures_signal_pipeline"),
        ):
            caplog.set_level(logging.ERROR)
            jobs.l3_portfolio_loop_job()  # 不应抛出

        assert "[L3] 运行失败: portfolio crash" in caplog.text


# ─── 期货信号管道 ────────────────────────────────────────


class TestFuturesSignalPipeline:
    """_run_futures_signal_pipeline / futures_signal_pipeline_job 测试。"""

    def test_pipeline_success(self, caplog):
        """管道主函数成功并返回 0。"""
        scripts_pkg = types.ModuleType("scripts")
        scripts_pkg.__path__ = []
        fake_pipeline = _fake_module(main=MagicMock(return_value=0))

        with patch.dict(
            sys.modules,
            {
                "scripts": scripts_pkg,
                "scripts.futures_signal_pipeline": fake_pipeline,
            },
        ):
            caplog.set_level(logging.INFO)
            jobs._run_futures_signal_pipeline()

        fake_pipeline.main.assert_called_once_with(max_symbols=82, days=120, universe="all")
        assert "[信号管道] 完成: exit_code=0" in caplog.text

    def test_pipeline_failure_caught(self, caplog):
        """管道主函数抛异常时捕获并记录错误。"""
        scripts_pkg = types.ModuleType("scripts")
        scripts_pkg.__path__ = []
        fake_pipeline = _fake_module(main=MagicMock(side_effect=RuntimeError("pipeline crash")))

        with patch.dict(
            sys.modules,
            {
                "scripts": scripts_pkg,
                "scripts.futures_signal_pipeline": fake_pipeline,
            },
        ):
            caplog.set_level(logging.ERROR)
            jobs._run_futures_signal_pipeline()  # 不应抛出

        assert "[信号管道] 失败: pipeline crash" in caplog.text

    @patch("fts.config.get_config")
    def test_futures_signal_pipeline_job(self, mock_cfg, caplog):
        """独立任务入口调用 _run_futures_signal_pipeline（全局市场 futures 时执行）。"""
        mock_cfg.return_value = types.SimpleNamespace(default_market="futures")
        with patch("fts.scheduler.jobs._run_futures_signal_pipeline") as mock_pipeline:
            caplog.set_level(logging.INFO)
            jobs.futures_signal_pipeline_job()

        mock_pipeline.assert_called_once()
        assert "[信号管道] 启动 trace_id=fts.signal.sched_" in caplog.text


# ─── 健康检查 ────────────────────────────────────────────


class TestHealthCheckJob:
    """health_check_job 测试。"""

    def test_healthy(self, caplog):
        """全部健康时输出正常日志。"""
        fake_monitor = _fake_module(check_all_status=MagicMock(return_value=MagicMock(healthy=True)))
        with patch.dict(sys.modules, {"fts.monitor": fake_monitor}):
            caplog.set_level(logging.INFO)
            jobs.health_check_job()

        assert "[健康检查] 正常" in caplog.text

    def test_unhealthy(self, caplog):
        """存在不健康状态时输出警告。"""
        fake_monitor = _fake_module(check_all_status=MagicMock(return_value=MagicMock(healthy=False)))
        with patch.dict(sys.modules, {"fts.monitor": fake_monitor}):
            caplog.set_level(logging.WARNING)
            jobs.health_check_job()

        assert "[健康检查] 不健康:" in caplog.text

    def test_failure_caught(self, caplog):
        """check_all_status 不可用时捕获并记录错误。"""
        fake_monitor = _fake_module()  # 无 check_all_status
        with patch.dict(sys.modules, {"fts.monitor": fake_monitor}):
            caplog.set_level(logging.ERROR)
            jobs.health_check_job()  # 不应抛出

        assert "[健康检查] 失败:" in caplog.text


# ─── 月度衰减评估 ────────────────────────────────────────


class TestL2ReviewJob:
    """l2_review_job 测试（45 计划候选③：月度衰减周度化重命名）。"""

    def _build_mocks(self, retired=None, factor_results=None):
        fake_tracker_mod = _fake_module(
            EliteFactorTracker=MagicMock(),
            AutoRetireManager=MagicMock(),
        )
        tracker = fake_tracker_mod.EliteFactorTracker.return_value
        tracker.run_monthly_evaluation.return_value = MagicMock()
        tracker.list_all.return_value = [
            {"status": "active"},
            {"status": "decaying"},
            {"status": "critical_decay"},
            {"status": "deprecated"},
        ]
        fake_tracker_mod.AutoRetireManager.return_value.run.return_value = retired if retired is not None else []

        fake_db = _fake_module(FactorRepository=MagicMock())
        repo = fake_db.FactorRepository.return_value
        if factor_results is not None:
            repo.get_factor.side_effect = factor_results
        repo.retire_factor.return_value = True

        fake_registry = MagicMock()
        return fake_tracker_mod, fake_db, fake_registry

    def test_success_no_retirement(self, caplog):
        """无淘汰因子时仅完成评估。"""
        fake_tracker_mod, fake_db, fake_registry = self._build_mocks(retired=[])
        with (
            patch.dict(
                sys.modules,
                {
                    "fts.monitor.elite_tracker": fake_tracker_mod,
                    "fts.monitor.reaudit": _fake_reaudit_mod(),
                    "fts.config": _make_cfg_module(),
                    "fts.factor_engine.factor_db": fake_db,
                },
            ),
            patch("fts.monitor.prometheus_metrics.metrics_registry", fake_registry),
        ):
            caplog.set_level(logging.INFO)
            jobs.l2_review_job()

        assert "[L2评审] 衰减评估完成:" in caplog.text
        fake_registry.update_decay_counts.assert_called_once_with(
            active=1,
            decaying=1,
            critical=1,
            deprecated=1,
        )
        # retired 为空时不会进入淘汰同步分支，仓库不应被构造
        fake_db.FactorRepository.assert_not_called()
        assert "淘汰已同步至" not in caplog.text

    def test_step_a_reaudit_invoked(self, caplog):
        """Step A 新标准重审被调用（默认启用）并记录汇总日志。"""
        fake_tracker_mod, fake_db, fake_registry = self._build_mocks(retired=[])
        reaudit_mod = _fake_reaudit_mod(counts={"retain": 1, "shadow": 2, "retire": 3, "error": 0})
        with (
            patch.dict(
                sys.modules,
                {
                    "fts.monitor.elite_tracker": fake_tracker_mod,
                    "fts.monitor.reaudit": reaudit_mod,
                    "fts.config": _make_cfg_module(),
                    "fts.factor_engine.factor_db": fake_db,
                },
            ),
            patch("fts.monitor.prometheus_metrics.metrics_registry", fake_registry),
        ):
            caplog.set_level(logging.INFO)
            jobs.l2_review_job()

        reaudit_mod.run_reaudit.assert_called_once()
        assert "Step A 新标准重审完成: retain=1 shadow=2 retire=3 error=0" in caplog.text

    def test_step_a_reaudit_disabled(self, monkeypatch, caplog):
        """FTS_MONTHLY_REAUDIT_ENABLED=0 时跳过重审。"""
        monkeypatch.setenv("FTS_MONTHLY_REAUDIT_ENABLED", "0")
        fake_tracker_mod, fake_db, fake_registry = self._build_mocks(retired=[])
        with (
            patch.dict(
                sys.modules,
                {
                    "fts.monitor.elite_tracker": fake_tracker_mod,
                    "fts.monitor.reaudit": _fake_reaudit_mod(),
                    "fts.config": _make_cfg_module(),
                    "fts.factor_engine.factor_db": fake_db,
                },
            ),
            patch("fts.monitor.prometheus_metrics.metrics_registry", fake_registry),
        ):
            caplog.set_level(logging.INFO)
            jobs.l2_review_job()

        assert "Step A 新标准重审已关闭" in caplog.text

    def test_success_with_retirement(self, caplog):
        """存在淘汰因子时同步 DuckDB + JSON（期货版单库，无分库回退）。"""
        fake_tracker_mod, fake_db, fake_registry = self._build_mocks(
            retired=["fid1", "fid2"],
            factor_results=[{"market": "futures"}, None],
        )
        with (
            patch.dict(
                sys.modules,
                {
                    "fts.monitor.elite_tracker": fake_tracker_mod,
                    "fts.monitor.reaudit": _fake_reaudit_mod(),
                    "fts.config": _make_cfg_module(),
                    "fts.factor_engine.factor_db": fake_db,
                },
            ),
            patch("fts.monitor.prometheus_metrics.metrics_registry", fake_registry),
        ):
            caplog.set_level(logging.WARNING)
            jobs.l2_review_job()

        repo = fake_db.FactorRepository.return_value
        assert repo.get_factor.call_count == 2
        assert repo.retire_factor.call_count == 2
        assert "[L2评审] 淘汰已同步至 DuckDB + JSON: 2/2 个因子" in caplog.text

    def test_metrics_sync_failure_continues(self, caplog):
        """指标同步失败时记录 warning 并继续后续流程。"""
        fake_tracker_mod = _fake_module(
            EliteFactorTracker=MagicMock(),
            AutoRetireManager=MagicMock(),
        )
        tracker = fake_tracker_mod.EliteFactorTracker.return_value
        tracker.run_monthly_evaluation.return_value = MagicMock()
        tracker.list_all.side_effect = RuntimeError("list fail")
        fake_tracker_mod.AutoRetireManager.return_value.run.return_value = []
        fake_db = _fake_module(FactorRepository=MagicMock())

        with (
            patch.dict(
                sys.modules,
                {
                    "fts.monitor.elite_tracker": fake_tracker_mod,
                    "fts.monitor.reaudit": _fake_reaudit_mod(),
                    "fts.config": _make_cfg_module(),
                    "fts.factor_engine.factor_db": fake_db,
                },
            ),
            patch("fts.monitor.prometheus_metrics.metrics_registry", MagicMock()),
        ):
            caplog.set_level(logging.INFO)
            jobs.l2_review_job()

        assert "[L2评审] 指标同步失败: list fail" in caplog.text
        assert "[L2评审] 衰减评估完成:" in caplog.text

    def test_failure_caught(self, caplog):
        """评估失败时捕获并记录错误。"""
        fake_tracker_mod = _fake_module(
            EliteFactorTracker=MagicMock(side_effect=RuntimeError("tracker crash")),
            AutoRetireManager=MagicMock(),
        )
        with (
            patch.dict(
                sys.modules,
                {
                    "fts.monitor.elite_tracker": fake_tracker_mod,
                    "fts.monitor.reaudit": _fake_reaudit_mod(),
                    "fts.config": _make_cfg_module(),
                    "fts.factor_engine.factor_db": _fake_module(FactorRepository=MagicMock()),
                },
            ),
            patch("fts.monitor.prometheus_metrics.metrics_registry", MagicMock()),
        ):
            caplog.set_level(logging.ERROR)
            jobs.l2_review_job()  # 不应抛出

        assert "[L2评审] 失败: tracker crash" in caplog.text


# ─── 逻辑监控 ────────────────────────────────────────────


class TestLogicMonitorJob:
    """logic_monitor_job 测试。"""

    def _build_mocks(self, rows=None, run_side_effect=None, repo_error=None):
        fake_logic = _fake_module(LogicMonitor=MagicMock())
        fake_db = _fake_module(FactorRepository=MagicMock())
        fake_repo_mod = _fake_module(DATABASE_PATH="/tmp/db.duckdb")
        fake_contracts = _fake_module(FactorProgram=MagicMock())

        repo = fake_db.FactorRepository.return_value
        if repo_error:
            fake_db = _fake_module(FactorRepository=MagicMock(side_effect=repo_error))
            repo = None
        else:
            conn = MagicMock()
            conn.description = [("factor_id",), ("name",), ("code",)]
            conn.execute.return_value.fetchall.return_value = (
                rows
                if rows is not None
                else [
                    ("f1", "F1", "code1"),
                    ("f2", "F2", "code2"),
                ]
            )
            repo._get_conn.return_value = conn

        logic = fake_logic.LogicMonitor.return_value
        result = MagicMock()
        result.all_healthy = False
        result.drift.is_drifted = True
        result.extreme_prediction.is_alarmed = True
        logic.run.return_value = result
        if run_side_effect:
            logic.run.side_effect = run_side_effect

        return fake_logic, fake_db, fake_repo_mod, fake_contracts

    def test_success(self, caplog):
        """成功路径：遍历精英因子执行行为漂移与极端预测检查。"""
        fake_logic, fake_db, fake_repo_mod, fake_contracts = self._build_mocks()
        with patch.dict(
            sys.modules,
            {
                "fts.monitor.logic_monitor": fake_logic,
                "fts.factor_engine.factor_db": fake_db,
                "fts.factor_engine.factor_db.repository": fake_repo_mod,
                "fts.factor_engine.contracts": fake_contracts,
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.logic_monitor_job()

        assert "[逻辑监控] 完成: total=2 drift=2 extreme=2" in caplog.text
        assert "[逻辑监控] 因子异常: f1 drift=True extreme=True" in caplog.text

    def test_no_active_factors(self, caplog):
        """无活跃精英因子时跳过。"""
        fake_logic, fake_db, fake_repo_mod, fake_contracts = self._build_mocks(rows=[])
        with patch.dict(
            sys.modules,
            {
                "fts.monitor.logic_monitor": fake_logic,
                "fts.factor_engine.factor_db": fake_db,
                "fts.factor_engine.factor_db.repository": fake_repo_mod,
                "fts.factor_engine.contracts": fake_contracts,
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.logic_monitor_job()

        assert "[逻辑监控] 无活跃精英因子，跳过" in caplog.text
        fake_logic.LogicMonitor.return_value.run.assert_not_called()

    def test_factor_check_failure_caught(self, caplog):
        """单个因子检查失败时记录警告并继续。"""
        fake_logic, fake_db, fake_repo_mod, fake_contracts = self._build_mocks(
            run_side_effect=RuntimeError("factor check crash")
        )
        with patch.dict(
            sys.modules,
            {
                "fts.monitor.logic_monitor": fake_logic,
                "fts.factor_engine.factor_db": fake_db,
                "fts.factor_engine.factor_db.repository": fake_repo_mod,
                "fts.factor_engine.contracts": fake_contracts,
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.logic_monitor_job()

        assert "[逻辑监控] 因子 f1 检查失败: factor check crash" in caplog.text
        # 完成后仍输出汇总日志
        assert "[逻辑监控] 完成: total=2 drift=0 extreme=0" in caplog.text

    def test_failure_caught(self, caplog):
        """数据库加载失败时捕获并记录错误。"""
        fake_logic, fake_db, fake_repo_mod, fake_contracts = self._build_mocks(repo_error=RuntimeError("db crash"))
        with patch.dict(
            sys.modules,
            {
                "fts.monitor.logic_monitor": fake_logic,
                "fts.factor_engine.factor_db": fake_db,
                "fts.factor_engine.factor_db.repository": fake_repo_mod,
                "fts.factor_engine.contracts": fake_contracts,
            },
        ):
            caplog.set_level(logging.ERROR)
            jobs.logic_monitor_job()  # 不应抛出

        assert "[逻辑监控] 运行失败: db crash" in caplog.text


# ─── 因子巡检 ────────────────────────────────────────────


class TestFactorInspectorJob:
    """factor_inspector_job 测试。"""

    @patch("fts.config.get_config")
    def test_success(self, mock_cfg, caplog):
        """成功路径：全局市场为 energy 时巡检能化链 + dry-run 输出汇总。"""
        mock_cfg.return_value = types.SimpleNamespace(default_market="energy")
        fake_inspector = _fake_module(FactorInspector=MagicMock())
        fake_inspector.FactorInspector.return_value.inspect_and_downgrade.return_value = {
            "summary": {
                "total_audited": 5,
                "degraded_detected": 1,
                "downgraded": 0,
                "deferred_approved": 0,
                "skipped": 0,
                "errors": 0,
            }
        }
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.factor_inspector": fake_inspector,
            },
        ):
            caplog.set_level(logging.INFO)
            jobs.factor_inspector_job()

        # GAP-132：巡检市场跟随全局 FTS_DEFAULT_MARKET + dry-run（评估历史不足，不自动降级）
        fake_inspector.FactorInspector.assert_called_once_with(market="energy")
        fake_inspector.FactorInspector.return_value.inspect_and_downgrade.assert_called_once_with(
            threshold=-0.2,
            commit=False,
        )
        assert "[因子巡检] 完成: audited=5 degraded=1 downgraded=0 deferred_approved=0 skipped=0 errors=0" in caplog.text

    @patch("fts.config.get_config")
    def test_follows_global_market(self, mock_cfg, caplog):
        """巡检市场跟随全局 FTS_DEFAULT_MARKET（futures 时巡检期货库）。"""
        mock_cfg.return_value = types.SimpleNamespace(default_market="futures")
        fake_inspector = _fake_module(FactorInspector=MagicMock())
        fake_inspector.FactorInspector.return_value.inspect_and_downgrade.return_value = {
            "summary": {"total_audited": 0, "degraded_detected": 0, "downgraded": 0,
                        "deferred_approved": 0, "skipped": 0, "errors": 0}
        }
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.factor_inspector": fake_inspector,
            },
        ):
            jobs.factor_inspector_job()

        fake_inspector.FactorInspector.assert_called_once_with(market="futures")

    def test_failure_caught(self, caplog):
        """巡检失败时捕获并记录错误。"""
        fake_inspector = _fake_module(FactorInspector=MagicMock(side_effect=RuntimeError("inspector crash")))
        with patch.dict(
            sys.modules,
            {
                "fts.factor_engine.factor_inspector": fake_inspector,
            },
        ):
            caplog.set_level(logging.ERROR)
            jobs.factor_inspector_job()  # 不应抛出

        assert "[因子巡检] 运行失败: inspector crash" in caplog.text


class TestMarketGate:
    """全局市场门控（FTS_DEFAULT_MARKET 运行时全局切换，v2.104.0+101）。"""

    @patch("fts.config.get_config")
    def test_market_gate_follows_global(self, mock_cfg):
        """_market_gate 按全局市场返回 True/False。"""
        mock_cfg.return_value = types.SimpleNamespace(default_market="futures")
        assert jobs._market_gate("futures", task="t") is True
        assert jobs._market_gate("energy", task="t") is False
        mock_cfg.return_value = types.SimpleNamespace(default_market="energy")
        assert jobs._market_gate("energy", task="t") is True
        assert jobs._market_gate("futures", task="t") is False

    @patch("fts.config.get_config")
    def test_energy_job_noop_when_global_futures(self, mock_cfg, caplog):
        """energy 专属任务在全局 futures 下门控 no-op（不触发重依赖导入）。"""
        mock_cfg.return_value = types.SimpleNamespace(default_market="futures")
        caplog.set_level(logging.INFO)
        jobs.l2_batch_mining_energy_job()  # 门控短路，应立即返回不抛异常
        assert "不匹配，跳过" in caplog.text

    @patch("fts.config.get_config")
    def test_futures_job_noop_when_global_energy(self, mock_cfg, caplog):
        """futures 专属任务在全局 energy 下门控 no-op。"""
        mock_cfg.return_value = types.SimpleNamespace(default_market="energy")
        caplog.set_level(logging.INFO)
        jobs.futures_signal_pipeline_job()  # 门控短路，不应触发信号管道
        assert "不匹配，跳过" in caplog.text


# ─── 数据质量评估 ────────────────────────────────────────


class TestDataQualityEvalJob:
    """data_quality_eval_job 测试。"""

    def test_no_monitor(self, caplog):
        """无已注册监控器时跳过。"""
        fake_http = _fake_module(get_data_quality_monitor=MagicMock(return_value=None))
        with patch.dict(sys.modules, {"fts.monitor.http_server": fake_http}):
            caplog.set_level(logging.INFO)
            jobs.data_quality_eval_job()

        assert "[数据质量] 无已注册监控器，跳过" in caplog.text

    def test_with_monitor(self, caplog):
        """有监控器时输出评估快照。"""
        monitor = MagicMock()
        monitor.get_metrics_snapshot.return_value = {"sources_ok": 3}
        fake_http = _fake_module(get_data_quality_monitor=MagicMock(return_value=monitor))
        with patch.dict(sys.modules, {"fts.monitor.http_server": fake_http}):
            caplog.set_level(logging.INFO)
            jobs.data_quality_eval_job()

        monitor.get_metrics_snapshot.assert_called_once()
        assert "[数据质量] 评估完成" in caplog.text

    def test_failure_caught(self, caplog):
        """获取监控器失败时捕获并记录错误。"""
        fake_http = _fake_module(get_data_quality_monitor=MagicMock(side_effect=RuntimeError("http crash")))
        with patch.dict(sys.modules, {"fts.monitor.http_server": fake_http}):
            caplog.set_level(logging.ERROR)
            jobs.data_quality_eval_job()  # 不应抛出

        assert "[数据质量] 评估失败: http crash" in caplog.text


# ─── 期货多源数据同步（补充分支） ────────────────────────


class TestSyncFuturesDataJobExtra:
    """sync_futures_data_job 未覆盖分支补充（主路径见 test_sync_futures_task.py）。

    注意: jobs.py 中落盘使用相对路径 ``Path("data") / "_lineage"``（相对 cwd），
    因此测试通过 ``monkeypatch.chdir(tmp_path)`` 隔离落盘位置。
    """

    def test_source_status_exception(self, tmp_path, monkeypatch, caplog):
        """get_source_status 抛异常时降级为空 dict 并继续落盘。"""
        monkeypatch.chdir(tmp_path)
        mock_agg = MagicMock()
        mock_agg.get_ohlcv.return_value = _make_kline_df(n=3)
        mock_agg.get_source_status.side_effect = RuntimeError("status unavailable")

        with patch("fts.cli._build_default_aggregator", return_value=mock_agg):
            caplog.set_level(logging.INFO)
            jobs.sync_futures_data_job(symbols=["RB0"], days=5)

        lineage = tmp_path / "data" / "_lineage"
        files = list(lineage.glob("sync_summary_*.json.gz"))
        assert len(files) == 1
        summary = json.loads(gzip.decompress(files[0].read_bytes()))
        assert summary["source_status"] == {}
        assert summary["success"] == 1
        assert summary["failure"] == 0
        assert "[Sync] 完成:" in caplog.text


# ─── 数据驱动动态池刷新（GAP-054） ───────────────────────


class TestSyncLiquidityPoolJob:
    """sync_liquidity_pool_job 成功/失败路径（GAP-054 动态池刷新）。"""

    def test_success_calls_main(self):
        """成功路径：调用 scripts.sync_liquidity_pool.main()。"""
        with patch("scripts.sync_liquidity_pool.main") as m_main:
            jobs.sync_liquidity_pool_job()
            m_main.assert_called_once()

    def test_failure_logs_without_raising(self, caplog):
        """失败路径：main 抛异常仅记录错误日志，不向上抛出。"""
        with patch("scripts.sync_liquidity_pool.main", side_effect=RuntimeError("snapshot failed")):
            caplog.set_level(logging.ERROR)
            jobs.sync_liquidity_pool_job()
        assert "[L-Pool] 动态池刷新失败: snapshot failed" in caplog.text


# ─── energy 链面板回溯天数（GAP-133） ─────────────────────


class TestEnergyChainPanelDays:
    """l2_panel_days 配置项约束（GAP-133，v2.104.0+107 参数化）。

    750 日：_build_wf_config 方案②回退分支下可切 4 个完整 OOS 窗口
    （审计 oos_consistency 需 n_windows≥2）；env FTS_L2_PANEL_DAYS 可覆盖。
    """

    def test_panel_days_from_config(self) -> None:
        from fts.config.settings import load_config

        assert load_config().l2_panel_days == 750
        # 回退分支（W=365/S=91/M=60，oos=max(60,91)=91）下 4 窗口需 N≥698
        assert 698 <= load_config().l2_panel_days

    def test_panel_days_env_override(self, monkeypatch) -> None:
        from fts.config.settings import load_config

        monkeypatch.setenv("FTS_L2_PANEL_DAYS", "800")
        assert load_config().l2_panel_days == 800


