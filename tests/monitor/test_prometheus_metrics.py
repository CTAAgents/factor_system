"""tests/monitor/test_prometheus_metrics.py — Prometheus 指标注册表单元测试。

HARNESS §测试随重构: 全量覆盖 prometheus_metrics.py。

测试策略:
    - 直接实例化 MetricsRegistry（纯内存，无外部依赖）
    - 覆盖全部指标更新方法与 render() 空/非空分支
    - 验证线程安全 getter 返回值
"""

from __future__ import annotations

from fts.monitor.prometheus_metrics import MetricsRegistry


# ─── 初始化 ──────────────────────────────────────────────


class TestMetricsRegistryInit:
    """MetricsRegistry.__init__ 测试。"""

    def test_initial_state(self):
        """初始计数全部为 0 / 空。"""
        reg = MetricsRegistry()
        assert reg.get_decay_counts() == {
            "active": 0,
            "decaying": 0,
            "critical_decay": 0,
            "deprecated": 0,
        }
        assert reg.get_regime() == ""
        assert reg._decay_evaluations == {}
        assert reg._rebalance_total == {}
        assert reg._live_factor_values == {}
        assert reg._live_deviation_alerts == {}
        assert reg._risk_check_total == {}
        assert reg._risk_check_blocked == {}
        assert reg._feedback_triggers == {}
        assert reg._feedback_processing == {}
        assert reg._feedback_pending == {}
        assert reg._attribution_accuracy == 0.0
        assert reg._recommendations_accepted == 0.0
        assert reg._new_factors == 0
        assert reg._effective_rate == 0.0
        assert reg._regime_metrics == {}
        assert reg._regime_by_market == {}


# ─── 衰减追踪指标 (A.2) ──────────────────────────────────


class TestDecayCounts:
    """update_decay_counts / get_decay_counts 测试。"""

    def test_update_all(self):
        """四个状态计数全部更新。"""
        reg = MetricsRegistry()
        reg.update_decay_counts(active=12, decaying=2, critical=1, deprecated=3)
        assert reg.get_decay_counts() == {
            "active": 12,
            "decaying": 2,
            "critical_decay": 1,
            "deprecated": 3,
        }

    def test_update_partial(self):
        """None 参数不修改对应计数。"""
        reg = MetricsRegistry()
        reg.update_decay_counts(active=5)
        assert reg.get_decay_counts()["active"] == 5
        assert reg.get_decay_counts()["decaying"] == 0

    def test_negative_clamped_to_zero(self):
        """负数被钳制为 0。"""
        reg = MetricsRegistry()
        reg.update_decay_counts(active=-3)
        assert reg.get_decay_counts()["active"] == 0

    def test_get_returns_copy(self):
        """get_decay_counts 返回副本，外部修改不影响内部。"""
        reg = MetricsRegistry()
        reg.update_decay_counts(active=1)
        got = reg.get_decay_counts()
        got["active"] = 999
        assert reg.get_decay_counts()["active"] == 1


class TestDecayEvaluation:
    """record_decay_evaluation 测试。"""

    def test_record_increments(self):
        """同键状态变更累加。"""
        reg = MetricsRegistry()
        reg.record_decay_evaluation("active", "decaying")
        reg.record_decay_evaluation("active", "decaying")
        assert reg._decay_evaluations[("active", "decaying")] == 2

    def test_record_different_keys(self):
        """不同状态变更分别计数。"""
        reg = MetricsRegistry()
        reg.record_decay_evaluation("active", "decaying")
        reg.record_decay_evaluation("decaying", "deprecated")
        assert len(reg._decay_evaluations) == 2


# ─── Regime / 权重指标 (A.3) ─────────────────────────────


class TestRegime:
    """set_regime / get_regime / record_rebalance 测试。"""

    def test_set_and_get_regime(self):
        """设置与读取当前市场状态。"""
        reg = MetricsRegistry()
        reg.set_regime("bull")
        assert reg.get_regime() == "bull"

    def test_set_regime_none_or_empty(self):
        """空字符串 / None 归一化为空。"""
        reg = MetricsRegistry()
        reg.set_regime("")
        assert reg.get_regime() == ""
        reg.set_regime(None)  # type: ignore[arg-type]
        assert reg.get_regime() == ""

    def test_record_rebalance(self):
        """同 regime 再平衡计数累加。"""
        reg = MetricsRegistry()
        reg.record_rebalance("bull")
        reg.record_rebalance("bull")
        reg.record_rebalance("bear")
        assert reg._rebalance_total == {"bull": 2, "bear": 1}

    def test_record_rebalance_unknown_when_empty(self):
        """空 regime 记入 unknown 桶。"""
        reg = MetricsRegistry()
        reg.record_rebalance("")
        assert reg._rebalance_total == {"unknown": 1}


class TestRegimeMetrics:
    """record_regime_metrics 测试（28-T10 观测指标）。"""

    def test_record_regime_metrics(self):
        """记录后 render() 输出 fts_regime_* 指标行。"""
        reg = MetricsRegistry()
        reg.record_regime_metrics(
            "futures",
            "bear",
            0.7,
            {"bear": 0.7, "bull": 0.1, "oscillate": 0.1, "high_vol": 0.05, "low_vol": 0.05},
            0.6,
        )
        text = "\n".join(reg.render())
        assert 'fts_regime_confidence{market="futures"} 0.7' in text
        assert 'fts_regime_entropy_norm{market="futures"}' in text
        assert 'fts_regime_exposure_scale{market="futures"} 0.6' in text
        assert 'fts_regime_blend_hhi{market="futures"}' in text
        assert 'fts_regime_name{market="futures",regime="bear"} 1' in text

    def test_record_regime_metrics_no_probs(self):
        """无 probs 时熵=0.0、HHI=1.0（确定性回退）。"""
        reg = MetricsRegistry()
        reg.record_regime_metrics("stock", "bull", 0.9, None, 1.0)
        text = "\n".join(reg.render())
        assert 'fts_regime_entropy_norm{market="stock"} 0.0' in text
        assert 'fts_regime_blend_hhi{market="stock"} 1.0' in text

    def test_record_regime_metrics_overwrite(self):
        """同市场重复上报覆盖旧值。"""
        reg = MetricsRegistry()
        reg.record_regime_metrics("futures", "bear", 0.7, None, 0.6)
        reg.record_regime_metrics("futures", "bull", 0.9, None, 1.0)
        text = "\n".join(reg.render())
        assert 'fts_regime_confidence{market="futures"} 0.9' in text
        assert 'fts_regime_name{market="futures",regime="bull"} 1' in text
        assert 'fts_regime_name{market="futures",regime="bear"}' not in text

    def test_record_regime_metrics_empty_market(self):
        """空 market 归入 unknown 桶。"""
        reg = MetricsRegistry()
        reg.record_regime_metrics("", "bear", 0.5, None, 1.0)
        assert reg._regime_by_market == {"unknown": "bear"}
        assert "fts_regime_name{market=\"unknown\",regime=\"bear\"} 1" in "\n".join(reg.render())


# ─── Live 因子指标 (C.2) ─────────────────────────────────


class TestLiveFactor:
    """update_live_factor / record_live_deviation_alert 测试。"""

    def test_update_live_factor(self):
        """更新因子 Live 指标。"""
        reg = MetricsRegistry()
        reg.update_live_factor("f1", {"ic": 0.05, "sharpe": 1.2, "max_drawdown": -0.1})
        assert reg._live_factor_values["f1"] == {
            "ic": 0.05,
            "sharpe": 1.2,
            "max_drawdown": -0.1,
        }

    def test_update_live_factor_filters_none(self):
        """None 值指标被过滤。"""
        reg = MetricsRegistry()
        reg.update_live_factor("f1", {"ic": 0.05, "sharpe": None})
        assert reg._live_factor_values["f1"] == {"ic": 0.05}

    def test_update_live_factor_empty_metrics(self):
        """全部为 None 时不写入。"""
        reg = MetricsRegistry()
        reg.update_live_factor("f1", {"ic": None})
        assert "f1" not in reg._live_factor_values

    def test_record_live_deviation_alert(self):
        """偏离告警按 (factor_id, severity) 累加。"""
        reg = MetricsRegistry()
        reg.record_live_deviation_alert("f1", "critical")
        reg.record_live_deviation_alert("f1", "critical")
        reg.record_live_deviation_alert("f1", "warning")
        assert reg._live_deviation_alerts[("f1", "critical")] == 2
        assert reg._live_deviation_alerts[("f1", "warning")] == 1


# ─── 风控指标 (C.2) ─────────────────────────────────────


class TestRiskCheckMetrics:
    """record_risk_check 测试。"""

    def test_record_passed(self):
        """passed 结果只计入 total。"""
        reg = MetricsRegistry()
        reg.record_risk_check("leverage_limit", "passed")
        assert reg._risk_check_total[("leverage_limit", "passed")] == 1
        assert "leverage_limit" not in reg._risk_check_blocked

    def test_record_blocked(self):
        """blocked 结果同时计入 total 与 blocked。"""
        reg = MetricsRegistry()
        reg.record_risk_check("leverage_limit", "blocked")
        reg.record_risk_check("leverage_limit", "blocked")
        assert reg._risk_check_total[("leverage_limit", "blocked")] == 2
        assert reg._risk_check_blocked["leverage_limit"] == 2


# ─── 反馈闭环指标 (C.3) ─────────────────────────────────


class TestFeedbackMetrics:
    """反馈闭环指标测试。"""

    def test_record_feedback_trigger(self):
        """反馈触发计数累加。"""
        reg = MetricsRegistry()
        reg.record_feedback_trigger("new_factor")
        reg.record_feedback_trigger("new_factor")
        assert reg._feedback_triggers == {"new_factor": 2}

    def test_record_feedback_trigger_unknown(self):
        """空 event_type 记入 unknown 桶。"""
        reg = MetricsRegistry()
        reg.record_feedback_trigger("")
        assert reg._feedback_triggers == {"unknown": 1}

    def test_update_feedback_pending(self):
        """待处理事件数更新（负数钳 0）。"""
        reg = MetricsRegistry()
        reg.update_feedback_pending({"review": 3, "fix": -2})
        assert reg._feedback_pending == {"review": 3, "fix": 0}

    def test_update_feedback_pending_empty(self):
        """空 dict 清空待处理。"""
        reg = MetricsRegistry()
        reg.update_feedback_pending({})
        assert reg._feedback_pending == {}

    def test_record_feedback_processing_ok(self):
        """处理成功记录 ok 桶。"""
        reg = MetricsRegistry()
        reg.record_feedback_processing("retire", True)
        assert reg._feedback_processing[("retire", "ok")] == 1

    def test_record_feedback_processing_fail(self):
        """处理失败记录 fail 桶。"""
        reg = MetricsRegistry()
        reg.record_feedback_processing("retire", False)
        assert reg._feedback_processing[("retire", "fail")] == 1

    def test_record_feedback_processing_unknown_action(self):
        """空 action 记入 unknown 桶。"""
        reg = MetricsRegistry()
        reg.record_feedback_processing("", True)
        assert reg._feedback_processing[("unknown", "ok")] == 1

    def test_update_effectiveness(self):
        """迭代效果指标更新。"""
        reg = MetricsRegistry()
        reg.update_effectiveness(
            attribution_accuracy=0.8,
            recommendations_accepted=0.6,
            new_factors=7,
            effective_rate=0.42,
        )
        assert reg._attribution_accuracy == 0.8
        assert reg._recommendations_accepted == 0.6
        assert reg._new_factors == 7
        assert reg._effective_rate == 0.42

    def test_update_effectiveness_partial(self):
        """None 参数不修改对应指标。"""
        reg = MetricsRegistry()
        reg.update_effectiveness(new_factors=3)
        assert reg._new_factors == 3
        assert reg._attribution_accuracy == 0.0


# ─── 渲染 ────────────────────────────────────────────────


class TestRender:
    """render() 空 / 非空分支测试。"""

    def test_render_empty_state(self):
        """空状态渲染：各指标输出 0 行 + HELP/TYPE 注释。"""
        reg = MetricsRegistry()
        text = "\n".join(reg.render())

        assert "fts_factor_decay_active_count 0" in text
        assert "fts_factor_decay_decaying_count 0" in text
        assert "fts_factor_decay_critical_count 0" in text
        assert "fts_factor_decay_deprecated_count 0" in text
        # 空分支: 输出无标签的 0 行
        assert "fts_factor_decay_evaluations_total 0" in text
        assert "fts_weight_rebalance_total 0" in text
        assert "fts_live_factor_deviation_alerts_total 0" in text
        assert "fts_risk_check_total 0" in text
        assert "fts_risk_check_blocked_total 0" in text
        assert "fts_feedback_triggers_total 0" in text
        assert "fts_feedback_events_pending 0" in text
        assert "fts_feedback_processing_total 0" in text
        assert "fts_feedback_attribution_accuracy 0.0" in text
        assert "fts_feedback_recommendations_accepted 0.0" in text
        assert "fts_evolution_new_factors 0" in text
        assert "fts_evolution_effective_rate 0.0" in text
        # HELP / TYPE 注释存在
        assert "# TYPE fts_factor_decay_active_count gauge" in text
        assert "# HELP fts_regime_current 当前市场状态 (1=当前生效)" in text

    def test_render_no_regime_line_when_empty(self):
        """无 regime 时不输出 fts_regime_current 值行。"""
        reg = MetricsRegistry()
        text = "\n".join(reg.render())
        assert "fts_regime_current{" not in text

    def test_render_decay_evaluation_labels(self):
        """状态变更带标签输出并按 key 排序。"""
        reg = MetricsRegistry()
        reg.record_decay_evaluation("decaying", "deprecated")
        reg.record_decay_evaluation("active", "decaying")
        text = "\n".join(reg.render())
        assert 'fts_factor_decay_evaluations_total{status_before="active",status_after="decaying"} 1' in text
        assert 'fts_factor_decay_evaluations_total{status_before="decaying",status_after="deprecated"} 1' in text
        # 空分支的 0 行不再输出
        assert "fts_factor_decay_evaluations_total 0" not in text

    def test_render_regime_line(self):
        """有 regime 时输出 1 标记行。"""
        reg = MetricsRegistry()
        reg.set_regime("bull")
        text = "\n".join(reg.render())
        assert 'fts_regime_current{regime="bull"} 1' in text

    def test_render_rebalance_lines(self):
        """再平衡计数带 regime 标签输出。"""
        reg = MetricsRegistry()
        reg.record_rebalance("bull")
        reg.record_rebalance("bear")
        text = "\n".join(reg.render())
        assert 'fts_weight_rebalance_total{regime="bear"} 1' in text
        assert 'fts_weight_rebalance_total{regime="bull"} 1' in text

    def test_render_live_factor_lines(self):
        """Live 因子 ic/sharpe 行输出。"""
        reg = MetricsRegistry()
        reg.update_live_factor("f1", {"ic": 0.05, "sharpe": 1.2})
        text = "\n".join(reg.render())
        assert 'fts_live_factor_ic{factor_id="f1"} 0.05' in text
        assert 'fts_live_factor_sharpe{factor_id="f1"} 1.2' in text

    def test_render_live_factor_without_ic(self):
        """无 ic 的因子不输出 ic 行（其余行仍输出）。"""
        reg = MetricsRegistry()
        reg.update_live_factor("f1", {"sharpe": 0.8})
        text = "\n".join(reg.render())
        assert 'fts_live_factor_ic{factor_id="f1"}' not in text
        assert 'fts_live_factor_sharpe{factor_id="f1"} 0.8' in text

    def test_render_deviation_alerts_lines(self):
        """偏离告警带 factor_id/severity 标签输出。"""
        reg = MetricsRegistry()
        reg.record_live_deviation_alert("f1", "critical")
        text = "\n".join(reg.render())
        assert 'fts_live_factor_deviation_alerts_total{factor_id="f1",severity="critical"} 1' in text

    def test_render_risk_check_lines(self):
        """风控检查 / 拦截计数输出。"""
        reg = MetricsRegistry()
        reg.record_risk_check("leverage_limit", "blocked")
        reg.record_risk_check("leverage_limit", "passed")
        text = "\n".join(reg.render())
        assert 'fts_risk_check_total{check_name="leverage_limit",result="blocked"} 1' in text
        assert 'fts_risk_check_total{check_name="leverage_limit",result="passed"} 1' in text
        assert 'fts_risk_check_blocked_total{check_name="leverage_limit"} 1' in text

    def test_render_feedback_lines(self):
        """反馈触发 / 待处理 / 处理计数输出。"""
        reg = MetricsRegistry()
        reg.record_feedback_trigger("new_factor")
        reg.update_feedback_pending({"review": 2})
        reg.record_feedback_processing("retire", True)
        text = "\n".join(reg.render())
        assert 'fts_feedback_triggers_total{event_type="new_factor"} 1' in text
        assert 'fts_feedback_events_pending{event_type="review"} 2' in text
        assert 'fts_feedback_processing_total{action_taken="retire",success="ok"} 1' in text

    def test_render_effectiveness_values(self):
        """效果指标输出实际数值。"""
        reg = MetricsRegistry()
        reg.update_effectiveness(
            attribution_accuracy=0.75,
            recommendations_accepted=0.5,
            new_factors=9,
            effective_rate=0.3,
        )
        text = "\n".join(reg.render())
        assert "fts_feedback_attribution_accuracy 0.75" in text
        assert "fts_feedback_recommendations_accepted 0.5" in text
        assert "fts_evolution_new_factors 9" in text
        assert "fts_evolution_effective_rate 0.3" in text

    def test_render_is_sorted(self):
        """渲染行保持稳定顺序（标签按 key 排序）。"""
        reg = MetricsRegistry()
        reg.record_rebalance("zzz")
        reg.record_rebalance("aaa")
        text = "\n".join(reg.render())
        assert text.index('regime="aaa"') < text.index('regime="zzz"')
