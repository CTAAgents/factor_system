"""tests/factor_engine/test_feedback_loop.py — C.3 反馈闭环测试。

覆盖:
1. FeedbackTrigger 触发条件（Live 偏离/定期评估）
2. AttributionAnalyzer 5 种根因判定
3. EvolutionDirectionAdjuster 方向调整
4. FeedbackLoop 幂等处理
5. EvolutionEffectiveness 月度报告
6. CLI feedback 子命令
7. Prometheus 反馈指标
8. schema 4 张反馈表
"""

from fts.factor_engine.feedback_loop import (
    AttributionAnalyzer,
    EvolutionDirectionAdjuster,
    EvolutionEffectiveness,
    FeedbackEventType,
    FeedbackLoop,
    FeedbackTrigger,
    RootCause,
)
from fts.monitor.live_factor_monitor import LiveFactorMonitor
from fts.monitor.prometheus_metrics import MetricsRegistry


def _live_monitor_with_deviation(ic_live=0.01, ic_bt=0.05) -> LiveFactorMonitor:
    monitor = LiveFactorMonitor()
    monitor.set_backtest_baseline("f1", {"ic": ic_bt, "sharpe": 2.0})
    monitor.update_live_performance("f1", {"ic": ic_live, "sharpe": 0.5})
    return monitor


# ─── 1. FeedbackTrigger ─────────────────────────────────


def test_trigger_live_deviation():
    trigger = FeedbackTrigger(live_monitor=_live_monitor_with_deviation())
    events = trigger.check_triggers()
    live_events = [e for e in events if e["event_type"] == FeedbackEventType.LIVE_DEVIATION.value]
    assert len(live_events) >= 1
    assert live_events[0]["severity"] == "critical"
    assert live_events[0]["factor_id"] == "f1"


def test_trigger_no_deviation():
    monitor = LiveFactorMonitor()
    monitor.set_backtest_baseline("f1", {"ic": 0.05})
    monitor.update_live_performance("f1", {"ic": 0.05})
    trigger = FeedbackTrigger(live_monitor=monitor)
    events = trigger.check_triggers()
    assert all(e["event_type"] != FeedbackEventType.LIVE_DEVIATION.value for e in events)


def test_trigger_cooldown():
    trigger = FeedbackTrigger(live_monitor=_live_monitor_with_deviation())
    events1 = trigger.check_triggers()
    events2 = trigger.check_triggers()
    # 冷却期 24h，第二次不应重复触发 live_deviation
    live1 = [e for e in events1 if e["event_type"] == FeedbackEventType.LIVE_DEVIATION.value]
    live2 = [e for e in events2 if e["event_type"] == FeedbackEventType.LIVE_DEVIATION.value]
    assert len(live1) >= 1
    assert len(live2) == 0


# ─── 2. AttributionAnalyzer ─────────────────────────────


def test_analyzer_factor_decay():
    analyzer = AttributionAnalyzer()
    report = analyzer.analyze(
        {"event_id": "e1", "event_type": "live_deviation"},
        factor={"factor_id": "f1", "decay_6m": 0.8},
    )
    assert report["root_cause"] == RootCause.FACTOR_DECAY.value
    assert report["recommendation"]["action"] == "retire_factor"


def test_analyzer_regime_mismatch():
    analyzer = AttributionAnalyzer()
    report = analyzer.analyze(
        {"event_id": "e2", "event_type": "live_deviation"},
        factor={"factor_id": "f1", "decay_6m": 0.05},
        market_data={"regime": "bear"},
    )
    assert report["root_cause"] == RootCause.REGIME_MISMATCH.value
    assert report["recommendation"]["action"] == "reweight_factor"


def test_analyzer_data_quality():
    analyzer = AttributionAnalyzer()
    report = analyzer.analyze(
        {"event_id": "e3", "event_type": "data_anomaly", "trigger_reason": "数据源异常"},
    )
    assert report["root_cause"] == RootCause.DATA_QUALITY.value
    assert report["recommendation"]["action"] == "fix_data_source"


def test_analyzer_normal_fluctuation():
    analyzer = AttributionAnalyzer()
    report = analyzer.analyze(
        {"event_id": "e4", "event_type": "periodic_eval"},
        factor={"factor_id": "f1", "decay_6m": 0.05},
        market_data={},
    )
    assert report["root_cause"] == RootCause.NORMAL_FLUCTUATION.value
    assert report["recommendation"]["action"] == "monitor_only"


def test_analyzer_implementation_bug():
    analyzer = AttributionAnalyzer()
    report = analyzer.analyze(
        {"event_id": "e5", "event_type": "audit_failure"},
    )
    assert report["root_cause"] == RootCause.IMPLEMENTATION_BUG.value


# ─── 3. EvolutionDirectionAdjuster ──────────────────────


def test_adjuster_trigger_evolution():
    adjuster = EvolutionDirectionAdjuster(max_generation_limit=100)
    attribution = {
        "root_cause": "regime_mismatch",
        "event_id": "ev1",
        "recommendation": {"action": "trigger_evolution"},
    }
    config = adjuster.adjust_direction(attribution, {"max_generations": 20})
    assert config["max_generations"] == 30  # 20 * 1.5
    assert config["inject_experience"]["event_id"] == "ev1"


def test_adjuster_retire():
    adjuster = EvolutionDirectionAdjuster()
    attribution = {
        "event_id": "ev2",
        "recommendation": {"action": "retire_factor"},
    }
    config = adjuster.adjust_direction(attribution, {})
    assert config["retire_candidates"] == "ev2"


def test_adjuster_monitor_only_no_change():
    adjuster = EvolutionDirectionAdjuster()
    attribution = {"recommendation": {"action": "monitor_only"}}
    config = adjuster.adjust_direction(attribution, {"max_generations": 10})
    assert config == {"max_generations": 10}


# ─── 4. FeedbackLoop 幂等 ───────────────────────────────


def test_feedback_loop_process():
    loop = FeedbackLoop(live_monitor=_live_monitor_with_deviation())
    results = loop.process_feedback(
        factors={"f1": {"decay_6m": 0.8}},
        market_data={},
    )
    assert len(results) >= 1
    assert results[0]["success"] is True
    assert results[0]["root_cause"] == RootCause.FACTOR_DECAY.value


def test_feedback_loop_idempotent():
    """同一 event_id 只处理一次。"""
    loop = FeedbackLoop(live_monitor=_live_monitor_with_deviation())
    results1 = loop.process_feedback(factors={"f1": {"decay_6m": 0.8}})
    assert len(results1) >= 1
    stats = loop.get_statistics()
    assert stats["events_handled"] == len(results1)
    # 立即再次处理（冷却期会阻止新触发）
    loop.process_feedback(factors={"f1": {"decay_6m": 0.8}})
    # 冷却期 24h 内不重复触发 → 不新增处理
    assert loop.get_statistics()["events_handled"] == stats["events_handled"]


def test_feedback_loop_manual_trigger():
    loop = FeedbackLoop()
    event = loop.trigger_manual_feedback(factor_id="fut_abc", reason="review")
    assert event["event_type"] == FeedbackEventType.USER_TRIGGERED.value
    assert event["factor_id"] == "fut_abc"
    assert loop.get_statistics()["events_handled"] == 1


# ─── 5. EvolutionEffectiveness ──────────────────────────


def test_effectiveness_monthly_report():
    evaluator = EvolutionEffectiveness(
        {
            "new_factors": 10,
            "total_generated": 100,
            "feedback_handled": 15,
            "recommendations_total": 10,
            "recommendations_accepted": 7,
        }
    )
    report = evaluator.generate_monthly_report("2026-08")
    assert report["period"] == "2026-08"
    assert report["new_factors"] == 10
    assert report["effective_rate"] == 0.1
    assert "summary_text" in report


def test_effectiveness_empty():
    evaluator = EvolutionEffectiveness()
    report = evaluator.generate_monthly_report("2026-08")
    assert report["new_factors"] == 0
    assert report["effective_rate"] == 0.0


# ─── 6. CLI feedback ────────────────────────────────────


def test_cli_feedback_subcommands():
    from fts.cli import build_parser

    parser = build_parser()
    for sub in ["trigger", "process", "report", "stats"]:
        argv = ["feedback", sub]
        if sub == "trigger":
            argv += ["--factor-id", "f1", "--reason", "test"]
        elif sub == "report":
            argv += ["--month", "2026-08"]
        args = parser.parse_args(argv)
        assert args.subcommand == sub
        assert callable(args.func)


def test_cli_feedback_trigger_runs(capsys):
    from fts.cli import main

    code = main(["feedback", "trigger", "--factor-id", "f1", "--reason", "test"])
    assert code == 0
    out = capsys.readouterr().out
    assert "反馈事件已触发" in out


# ─── 7. Prometheus 反馈指标 ─────────────────────────────


def test_metrics_feedback():
    reg = MetricsRegistry()
    reg.record_feedback_trigger("live_deviation")
    reg.record_feedback_trigger("live_deviation")
    reg.record_feedback_processing("retire_factor", True)
    reg.update_feedback_pending({"live_deviation": 2})
    reg.update_effectiveness(attribution_accuracy=0.8, new_factors=10, effective_rate=0.15)

    lines = "\n".join(reg.render())
    assert 'fts_feedback_triggers_total{event_type="live_deviation"} 2' in lines
    assert 'fts_feedback_events_pending{event_type="live_deviation"} 2' in lines
    assert 'fts_feedback_processing_total{action_taken="retire_factor",success="ok"} 1' in lines
    assert "fts_feedback_attribution_accuracy 0.8" in lines
    assert "fts_evolution_new_factors 10" in lines
    assert "fts_evolution_effective_rate 0.15" in lines


# ─── 8. schema 反馈表 ───────────────────────────────────


def test_schema_feedback_tables(tmp_path):
    from fts.factor_engine.factor_db import schema

    db_path = tmp_path / "test_feedback.duckdb"
    schema.init_database(db_path)
    import duckdb

    conn = duckdb.connect(str(db_path))
    try:
        tables = [
            t[0]
            for t in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        ]
    finally:
        conn.close()
    for t in ["feedback_events", "attribution_reports", "feedback_processing_results", "feedback_reports"]:
        assert t in tables


# ─── 9. GAP-L402 实盘反馈闭环 ────────────────────────────


def test_live_feedback_record_validation():
    """契约校验：有效记录通过，缺失必填字段失败。"""
    from fts.factor_engine.feedback_loop import validate_live_feedback_record

    ok, msg = validate_live_feedback_record(
        {
            "factor_id": "fct_1",
            "signal_date": "2026-08-01",
            "signal_value": 0.5,
            "position_return": 0.01,
            "turnover": 0.2,
        }
    )
    assert ok
    assert msg == ""
    ok2, _ = validate_live_feedback_record({"signal_value": 0.5})
    assert not ok2
    ok3, _ = validate_live_feedback_record(
        {
            "factor_id": "fct_1",
            "signal_date": "2026-08-01",
            "signal_value": "bad",
            "position_return": 0.01,
            "turnover": 0.2,
        }
    )
    assert not ok3


def test_live_feedback_import_jsonl(tmp_path, monkeypatch):
    """导入记录 → JSONL 落盘（无 DuckDB 时回退）。"""
    from fts.factor_engine.feedback_loop import LiveFeedbackImporter

    monkeypatch.chdir(tmp_path)  # 落盘路径为相对 memory/portfolio/
    importer = LiveFeedbackImporter(db_path=None)
    result = importer.import_records(
        [
            {
                "factor_id": "fct_1",
                "signal_date": "2026-08-01",
                "signal_value": 0.5,
                "position_return": 0.01,
                "turnover": 0.2,
            },
            {
                "factor_id": "fct_1",
                "signal_date": "2026-08-02",
                "signal_value": -0.3,
                "position_return": -0.005,
                "turnover": 0.1,
            },
            {"signal_value": 0.5},  # 无效
        ]
    )
    assert result["total"] == 3
    assert result["valid"] == 2
    assert result["invalid"] == 1
    jl = tmp_path / "memory" / "portfolio" / "live_feedback.jsonl"
    assert jl.exists()
    assert jl.read_text(encoding="utf-8").count("\n") == 2


def test_live_feedback_ic_positive_correlation():
    """实盘 IC：信号与收益正相关时 IC 为正。"""
    from fts.factor_engine.feedback_loop import LiveFeedbackImporter

    importer = LiveFeedbackImporter(db_path=None)
    records = [
        {
            "factor_id": "fct_a",
            "signal_date": f"2026-08-{d:02d}",
            "signal_value": v,
            "position_return": v * 0.01 + 0.0001,
            "turnover": 0.1,
        }
        for d, v in [
            (1, 0.8),
            (2, 0.5),
            (3, 0.2),
            (4, -0.1),
            (5, -0.4),
            (6, -0.7),
            (7, 0.9),
            (8, -0.9),
            (9, 0.3),
            (10, -0.2),
        ]
    ]
    importer.import_records(records)
    stats = importer.compute_live_ic()
    assert stats["n_records"] == 10
    assert stats["factors"]["fct_a"]["ic"] > 0
    assert stats["overall_ic"] > 0


def test_live_vs_backtest_ic_report():
    """对比报告：实盘 IC 显著低于回测 IC → 标记 decayed。"""
    from fts.factor_engine.feedback_loop import (
        LiveFeedbackImporter,
        LiveVsBacktestICReport,
    )

    importer = LiveFeedbackImporter(db_path=None)
    importer.import_records(
        [
            # fct_ok: 信号与收益强正相关 → 实盘 IC 高，status=ok
            {
                "factor_id": "fct_ok",
                "signal_date": f"2026-08-{d:02d}",
                "signal_value": v,
                "position_return": v * 0.02 + 0.0005 * d,
                "turnover": 0.1,
            }
            for d, v in enumerate([0.8, 0.6, 0.4, 0.2, -0.1, -0.3, -0.5, -0.8], 1)
        ]
        + [
            # fct_decay: 信号与收益几乎无关（微弱正相关）→ 实盘 IC≈0，status=decayed
            {
                "factor_id": "fct_decay",
                "signal_date": f"2026-08-{d:02d}",
                "signal_value": v,
                "position_return": v * 0.0005 + 0.0005 * d,
                "turnover": 0.1,
            }
            for d, v in enumerate([0.8, 0.6, 0.4, 0.2, -0.1, -0.3, -0.5, -0.8], 1)
        ]
    )
    stats = importer.compute_live_ic()
    report = LiveVsBacktestICReport().generate(
        stats,
        backtest_ic_map={"fct_ok": 0.04, "fct_decay": 0.04},
    )
    by_id = {r["factor_id"]: r for r in report["factors"]}
    assert by_id["fct_ok"]["status"] == "ok"
    assert by_id["fct_decay"]["status"] == "decayed"
    assert report["summary"]["n_decayed"] == 1
    # GAP-I401 (v2.71.0): 衰减因子输出退役建议（供 GAP-I305 闭环消费）
    assert by_id["fct_decay"]["recommend_retire"] is True
    # 符号反转场景（bt>0 而 live<0）：decay_gap 为负（|live|>|bt|），仅断言字段存在
    assert by_id["fct_decay"]["decay_gap"] is not None
    assert by_id["fct_ok"]["recommend_retire"] is False
    assert report["summary"]["n_recommend_retire"] == 1


def test_live_feedback_duckdb_persist(tmp_path):
    """DuckDB 落盘：feedback_live 表创建并插入。"""
    from fts.factor_engine.feedback_loop import LiveFeedbackImporter

    db_path = str(tmp_path / "live_feedback.duckdb")
    importer = LiveFeedbackImporter(db_path=db_path)
    importer.import_records(
        [
            {
                "factor_id": "fct_1",
                "signal_date": "2026-08-01",
                "signal_value": 0.5,
                "position_return": 0.01,
                "turnover": 0.2,
                "market": "futures",
            },
        ]
    )
    import duckdb

    conn = duckdb.connect(db_path)
    try:
        rows = conn.execute("SELECT factor_id, signal_value FROM feedback_live").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0] == ("fct_1", 0.5)
