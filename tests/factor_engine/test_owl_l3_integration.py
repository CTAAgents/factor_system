"""tests/factor_engine/test_owl_l3_integration.py — OWL 因子分组筛选 L3 旁路集成测试。

覆盖（plans/41 方案 A，Step 1.8c）:
    1. 配置契约：owl_config 映射到 PortfolioLoop 的 owl 属性与 _owl_selector_kwargs
    2. enabled=false（默认）零调用零行为变更
    3. enabled=true + report_only 走 OWL：产出报告、写入 state，不修改 factors
    4. OWL 失败回退不阻断（非致命）
    5. _run_owl_sidecar 函数级行为（正常/降级/失败回退）

HARNESS: 契约优先 + 零漂移回退（OWL 任何失败都不改变主链路语义）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.l3_signal_service import SignalMatrixBundle
from fts.factor_engine.owl_factor_selector import OwlSelectionResult
from fts.factor_engine.portfolio_loop import PortfolioLoop, _run_owl_sidecar

# ─── fixtures（与 test_portfolio_loop.py 同构） ─────────────


@pytest.fixture(autouse=True)
def _isolate_state_store(tmp_path, monkeypatch):
    """隔离 state.duckdb（状态管理器默认走全局 SSOT，测试须隔离防串扰）。"""
    from fts.store import state_db

    store = state_db.StateKVStore(tmp_path / "state.duckdb")
    monkeypatch.setattr(state_db, "get_state_store", lambda: store)
    yield
    store.close()


@pytest.fixture
def tmp_portfolio_dir(tmp_path):
    """临时 L3 组合目录。"""
    p = tmp_path / "portfolio"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_elite_dir(tmp_path):
    """临时 elite 因子目录。"""
    p = tmp_path / "elite"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_mock_factor(elite_dir, fid: str, name: str) -> None:
    """写入一个通过 Verifier 质量门槛的 mock elite 因子。"""
    (elite_dir / f"{fid}.json").write_text(
        json.dumps(
            {
                "factor_id": fid,
                "name": name,
                "sharpe": 2.5,
                "ic": 0.05,
                "turnover": 0.3,
                "decay_6m": 0.1,
                "code": f"def f(df, p): return df['close'].pct_change({name})",
            }
        ),
        encoding="utf-8",
    )


def _make_panel(n_dates: int = 60, n_syms: int = 3) -> dict[str, pd.DataFrame]:
    """构造合成 OHLCV 面板（Step 0.5 数据，供 OWL 共同日期判定）。"""
    idx = pd.bdate_range("2025-01-02", periods=n_dates)
    panel: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(["RB", "HC", "I"]):
        base = 3000.0 + 10.0 * i
        close = np.cumprod(1.0 + 0.001 * np.sin(np.arange(n_dates) + i)) * base
        panel[sym] = pd.DataFrame(
            {
                "open": close * 0.999,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": 1000.0,
            },
            index=idx,
        )
    return panel


# ════════════════════════════════════════════════════════════
# 1. 配置契约加载
# ════════════════════════════════════════════════════════════


class TestOwlConfigContract:
    """owl_config → PortfolioLoop 属性映射。"""

    def test_default_disabled(self, tmp_portfolio_dir, tmp_elite_dir):
        """不传 owl_config：默认 enabled=false，零开销零行为变更。"""
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
        )
        assert loop.owl_enabled is False
        assert loop.owl_report_only is True
        assert loop._owl_selector is None
        assert loop._owl_selector_kwargs == {
            "weight_scheme": "linear",
            "weight_tuning": 0.5,
            "train_frac": 0.7,
            "group_corr_threshold": 0.5,
            "lambda_": 0.05,
        }

    def test_full_config_mapping(self, tmp_portfolio_dir, tmp_elite_dir):
        """完整 owl_config 映射到各属性。"""
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            owl_config={
                "enabled": True,
                "report_only": False,
                "weight_scheme": "exp",
                "weight_tuning": 0.8,
                "train_frac": 0.8,
                "group_corr_threshold": 0.6,
                "lambda_": 0.1,
            },
        )
        assert loop.owl_enabled is True
        assert loop.owl_report_only is False
        assert loop._owl_selector_kwargs == {
            "weight_scheme": "exp",
            "weight_tuning": 0.8,
            "train_frac": 0.8,
            "group_corr_threshold": 0.6,
            "lambda_": 0.1,
        }

    def test_partial_config_defaults(self, tmp_portfolio_dir, tmp_elite_dir):
        """仅开 enabled：其余配置走默认值。"""
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            owl_config={"enabled": True},
        )
        assert loop.owl_enabled is True
        assert loop.owl_report_only is True  # report_only 默认 true（不越界）
        assert loop._owl_selector_kwargs["weight_scheme"] == "linear"
        assert loop._owl_selector_kwargs["train_frac"] == 0.7


# ════════════════════════════════════════════════════════════
# 2. enabled=false 零调用零行为变更
# ════════════════════════════════════════════════════════════


class TestStep18cDisabled:
    """enabled=false 时 Step 1.8c 不执行、不产出任何 owl 痕迹。"""

    def test_no_owl_report_in_state(self, tmp_portfolio_dir, tmp_elite_dir):
        """完整 run() 后 state 不含 owl_report 键。"""
        for i, (fid, name) in enumerate(
            [("f1", "mom"), ("f2", "rev"), ("f3", "carry")], start=1
        ):
            _write_mock_factor(tmp_elite_dir, fid, name)

        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_regime_adaptation=False,
            enable_clustering=False,
            owl_config={"enabled": False},
        )
        with patch(
            "fts.factor_engine.portfolio_loop._run_owl_sidecar"
        ) as mock_sidecar, patch(
            "fts.data.FTSDataProvider"
        ) as mock_provider:
            # 模拟面板数据加载成功（否则 panel_data=None，走跳过分支）
            mock_provider.return_value.get_futures_panel.return_value = (
                _make_panel(),
                list(pd.bdate_range("2025-01-02", periods=60)),
            )
            result = loop.run()
        assert result.status != "circuit_broken"
        mock_sidecar.assert_not_called()
        assert "owl_report" not in loop.state_manager.load_or_init()


# ════════════════════════════════════════════════════════════
# 3. enabled=true + report_only：产出报告、不修改 factors
# ════════════════════════════════════════════════════════════


class TestStep18cEnabled:
    """enabled=true + report_only 走 OWL 旁路。"""

    def _run_with_sidecar(
        self, tmp_portfolio_dir, tmp_elite_dir, sidecar_return, **loop_kw
    ):
        for i, (fid, name) in enumerate(
            [("f1", "mom"), ("f2", "rev"), ("f3", "carry")], start=1
        ):
            _write_mock_factor(tmp_elite_dir, fid, name)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_regime_adaptation=False,
            enable_clustering=False,
            owl_config={"enabled": True, "report_only": True},
            **loop_kw,
        )
        with patch(
            "fts.factor_engine.portfolio_loop._run_owl_sidecar",
            return_value=sidecar_return,
        ) as mock_sidecar, patch(
            "fts.data.FTSDataProvider"
        ) as mock_provider:
            mock_provider.return_value.get_futures_panel.return_value = (
                _make_panel(),
                list(pd.bdate_range("2025-01-02", periods=60)),
            )
            result = loop.run()
        return loop, result, mock_sidecar

    def test_sidecar_called_and_state_written(self, tmp_portfolio_dir, tmp_elite_dir):
        """OWL 完成：summary 写入 state['owl_report']，sidecar 被调用。"""
        summary = {
            "significant_groups": [[0, 1]],
            "nonsignificant_factors": ["f3"],
            "n_groups": 2,
            "n_significant_groups": 1,
            "n_factors": 3,
            "conflict_cluster_dropped_owl_kept": [],
            "report_only": True,
            "report_path": "memory/portfolio/owl/owl_report_2026-01-01.json",
        }
        loop, result, mock_sidecar = self._run_with_sidecar(
            tmp_portfolio_dir, tmp_elite_dir, {"summary": summary}
        )
        assert result.status != "circuit_broken"
        mock_sidecar.assert_called_once()
        state = loop.state_manager.load_or_init()
        assert state.get("owl_report") == summary

    def test_report_only_keeps_factors(self, tmp_portfolio_dir, tmp_elite_dir):
        """report_only=true：即便 OWL 建议剔除因子，factors 列表不被修改。"""
        summary = {
            "significant_groups": [[0, 1]],
            "nonsignificant_factors": ["f3"],
            "n_groups": 2,
            "n_significant_groups": 1,
            "n_factors": 3,
            "report_only": True,
        }
        loop, result, _ = self._run_with_sidecar(
            tmp_portfolio_dir, tmp_elite_dir, {"summary": summary}
        )
        # 主链路因子数未被 OWL 建议剔除影响（3 个 mock 因子全部保留进组合流程）
        assert result.n_factors_input == 3
        assert result.status != "circuit_broken"


# ════════════════════════════════════════════════════════════
# 4. OWL 失败回退不阻断
# ════════════════════════════════════════════════════════════


class TestStep18cFailureFallback:
    """OWL 旁路异常/不可用时 run() 不中断、主链路语义不变。"""

    def test_sidecar_raises_non_fatal(self, tmp_portfolio_dir, tmp_elite_dir):
        """sidecar 抛异常：run() 捕获并记录 warning，不熔断。"""
        for fid, name in [("f1", "mom"), ("f2", "rev"), ("f3", "carry")]:
            _write_mock_factor(tmp_elite_dir, fid, name)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_regime_adaptation=False,
            enable_clustering=False,
            owl_config={"enabled": True},
        )
        with patch(
            "fts.factor_engine.portfolio_loop._run_owl_sidecar",
            side_effect=RuntimeError("OWL 求解器崩溃"),
        ), patch("fts.data.FTSDataProvider") as mock_provider:
            mock_provider.return_value.get_futures_panel.return_value = (
                _make_panel(),
                list(pd.bdate_range("2025-01-02", periods=60)),
            )
            result = loop.run()
        assert result.status != "circuit_broken"
        assert result.error is None
        assert "owl_report" not in loop.state_manager.load_or_init()

    def test_sidecar_returns_none(self, tmp_portfolio_dir, tmp_elite_dir):
        """sidecar 返回 None（OWL 不可用/输入不足）：不写 state、不阻断。"""
        for fid, name in [("f1", "mom"), ("f2", "rev"), ("f3", "carry")]:
            _write_mock_factor(tmp_elite_dir, fid, name)
        loop = PortfolioLoop(
            memory_dir=tmp_portfolio_dir,
            elite_dir=tmp_elite_dir,
            use_duckdb=False,
            enable_regime_adaptation=False,
            enable_clustering=False,
            owl_config={"enabled": True},
        )
        with patch(
            "fts.factor_engine.portfolio_loop._run_owl_sidecar",
            return_value=None,
        ), patch("fts.data.FTSDataProvider") as mock_provider:
            mock_provider.return_value.get_futures_panel.return_value = (
                _make_panel(),
                list(pd.bdate_range("2025-01-02", periods=60)),
            )
            result = loop.run()
        assert result.status != "circuit_broken"
        assert "owl_report" not in loop.state_manager.load_or_init()


# ════════════════════════════════════════════════════════════
# 5. _run_owl_sidecar 函数级行为
# ════════════════════════════════════════════════════════════


def _make_fake_self(tmp_path) -> SimpleNamespace:
    """构造最小 self（满足 _run_owl_sidecar 读取的全部属性）。"""
    return SimpleNamespace(
        memory_dir=str(tmp_path / "portfolio"),
        market="futures",
        owl_report_only=True,
        _owl_selector=None,
        _owl_selector_kwargs={
            "weight_scheme": "linear",
            "weight_tuning": 0.5,
            "train_frac": 0.7,
            "group_corr_threshold": 0.5,
            "lambda_": 0.05,
        },
        _signal_cache=None,
        state={},
    )


class TestOwlSidecarFunction:
    """_run_owl_sidecar 模块级函数行为（mock 依赖，验证旁路契约）。"""

    def _mk_bundle(self, n_factors: int = 3, n_dates: int = 60, n_syms: int = 3):
        """构造正常 SignalMatrixBundle（随机信号 + 前向收益）。"""
        rng = np.random.default_rng(7)
        sig = rng.normal(size=(n_dates, n_syms, n_factors))
        fwd = rng.normal(size=(n_dates, n_syms))
        return SignalMatrixBundle(
            signal_matrix=sig,
            forward_returns=fwd,
            dates=list(range(n_dates)),
            symbols=[f"s{i}" for i in range(n_syms)],
            factor_ids=[f"f{i}" for i in range(n_factors)],
            forward_days=5,
        )

    def _mk_result(self, n_factors: int = 3) -> OwlSelectionResult:
        return OwlSelectionResult(
            applied=True,
            beta=np.array([0.4, 0.3, 0.0]),
            groups=[[0, 1]],
            significant_groups=[[0, 1]],
            nonsignificant_factors=["f2"],
            train_frac=0.7,
            n_train=40,
            lambda_=0.05,
            weight_scheme="linear",
            group_corr_threshold=0.5,
        )

    def test_success_produces_report(self, tmp_path):
        """正常路径：返回 summary+report，落盘报告文件。"""
        fake = _make_fake_self(tmp_path)
        bundle = self._mk_bundle()
        result = self._mk_result()
        with patch(
            "fts.factor_engine.owl_factor_selector.OwlFactorSelector"
        ) as mock_cls, patch(
            "fts.factor_engine.l3_signal_service.build_signal_matrix",
            return_value=bundle,
        ):
            mock_cls.return_value.select.return_value = result
            out = _run_owl_sidecar(fake, [{"factor_id": f"f{i}"} for i in range(3)], _make_panel())
        assert out is not None
        assert out["summary"]["significant_groups"] == [[0, 1]]
        assert out["summary"]["nonsignificant_factors"] == ["f2"]
        assert out["summary"]["report_only"] is True
        assert out["beta"] == [0.4, 0.3, 0.0]
        assert out["factor_ids"] == ["f0", "f1", "f2"]
        # 报告落盘
        import glob

        files = glob.glob(str(tmp_path / "portfolio" / "owl" / "owl_report_*.json"))
        assert len(files) == 1
        saved = json.loads(Path(files[0]).read_text(encoding="utf-8"))
        assert saved["significant_groups"] == [[0, 1]]

    def test_not_applied_returns_none(self, tmp_path):
        """select 返回 applied=False：返回 None（旁路静默）。"""
        fake = _make_fake_self(tmp_path)
        bundle = self._mk_bundle()
        with patch(
            "fts.factor_engine.owl_factor_selector.OwlFactorSelector"
        ) as mock_cls, patch(
            "fts.factor_engine.l3_signal_service.build_signal_matrix",
            return_value=bundle,
        ):
            mock_cls.return_value.select.return_value = OwlSelectionResult()
            out = _run_owl_sidecar(fake, [{"factor_id": f"f{i}"} for i in range(3)], _make_panel())
        assert out is None

    def test_import_failure_returns_none(self, tmp_path):
        """OwlFactorSelector 导入失败：返回 None（非致命）。"""
        fake = _make_fake_self(tmp_path)
        with patch(
            "fts.factor_engine.owl_factor_selector.OwlFactorSelector",
            side_effect=ImportError("cvxpy missing"),
        ):
            out = _run_owl_sidecar(fake, [{"factor_id": f"f{i}"} for i in range(3)], _make_panel())
        assert out is None

    def test_insufficient_common_dates_returns_none(self, tmp_path):
        """共同日期 <30：跳过 OWL（返回 None，select 不被调用）。"""
        fake = _make_fake_self(tmp_path)
        short_panel = _make_panel(n_dates=10)
        with patch("fts.factor_engine.owl_factor_selector.OwlFactorSelector") as mock_cls:
            out = _run_owl_sidecar(fake, [{"factor_id": f"f{i}"} for i in range(3)], short_panel)
        assert out is None
        mock_cls.return_value.select.assert_not_called()

    def test_insufficient_factors_returns_none(self, tmp_path):
        """因子 <3：直接返回 None（select 不被调用）。"""
        fake = _make_fake_self(tmp_path)
        with patch("fts.factor_engine.owl_factor_selector.OwlFactorSelector") as mock_cls:
            out = _run_owl_sidecar(fake, [{"factor_id": "f1"}], _make_panel())
        assert out is None
        mock_cls.return_value.select.assert_not_called()

    def test_build_signal_failure_returns_none(self, tmp_path):
        """build_signal_matrix 异常：返回 None（非致命）。"""
        fake = _make_fake_self(tmp_path)
        with patch(
            "fts.factor_engine.owl_factor_selector.OwlFactorSelector"
        ) as mock_cls, patch(
            "fts.factor_engine.l3_signal_service.build_signal_matrix",
            side_effect=RuntimeError("matrix build failed"),
        ):
            mock_cls.return_value.select.return_value = self._mk_result()
            out = _run_owl_sidecar(fake, [{"factor_id": f"f{i}"} for i in range(3)], _make_panel())
        assert out is None



