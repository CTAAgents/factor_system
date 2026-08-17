"""
tests/factor_engine/test_subchain_waiver.py — L2 晋升子链放行测试（GAP-144）。

覆盖:
    - evolution_candidate._subchain_waiver_effective_ic：effective 子链 max |mean_ic|
    - evolution_candidate._apply_subchain_ic_waiver：仅豁免 IC/ICIR 稀释维度，
      Sharpe 等其它维度失败仍拦截；非 energy/开关关/无 effective 子链不放行
    - evolution_candidate._subchain_waiver_view：评分卡放行视图（ic 替换）
    - evolution_seeds._subchain_waiver_pass：评估链 IC 门槛豁免
    - evolution_seeds._apply_seed_subchain_waiver：种子路径 Verifier 豁免同构

纯计算无 DB/IO，不触真实因子库。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fts.factor_engine.evolution_candidate import (
    _apply_subchain_ic_waiver,
    _subchain_waiver_effective_ic,
    _subchain_waiver_view,
)
from fts.factor_engine.evolution_seeds import (
    _apply_seed_subchain_waiver,
    _subchain_waiver_pass,
)

CHAINS = ["能源", "聚酯", "油化工", "煤化工"]


def _profile(effective_chains: set[str]) -> dict:
    """构造 subchain_ic_profile：effective 链 true、其余 false（附 mean_ic）。"""
    out: dict[str, dict] = {}
    for c in CHAINS:
        out[c] = {
            "n_symbols": 3,
            "mean_ic": 0.20 if c in effective_chains else 0.01,
            "std_ic": 0.01,
            "t_stat": 10.0,
            "p_value": 0.001,
            "effective": c in effective_chains,
        }
    return out


def _evaluation(effective_chains: set[str], ic: float = 0.01) -> dict:
    return {
        "factor_id": "f1",
        "level_1_backtest": {
            "ic": ic,
            "icir": 0.2,
            "sharpe": 2.0,
            "max_drawdown": 0.2,
            "subchain_ic_report": {"subchain_ic_profile": _profile(effective_chains)},
        },
    }


def _energy_owner(waiver_enabled: bool = True) -> SimpleNamespace:
    """energy 链 owner mock（读配置开关）。"""
    return SimpleNamespace(
        market="energy",
        verifier=object(),
    )


def _enable_waiver(monkeypatch, enabled: bool) -> None:
    """monkeypatch 全局配置 l2_subchain_waiver_enabled 属性。

    注意：get_config() 为缓存单例（settings.py 模块级 _default_config），
    setenv 不生效——须直接改配置实例属性（项目既有约定）。
    """
    from fts.config.settings import get_config

    monkeypatch.setattr(get_config(), "l2_subchain_waiver_enabled", enabled)


class TestSubchainWaiverEffectiveIc:
    def test_single_chain_effective(self):
        # 单链特异：仅能源 effective → 返回该链 |mean_ic|=0.20
        ev = _evaluation({"能源"})
        assert _subchain_waiver_effective_ic(ev) == pytest.approx(0.20)

    def test_multi_chain_effective_takes_max(self):
        # 部分链：能源/油化工 effective → 取 max |mean_ic|=0.20（两链同值）
        ev = _evaluation({"能源", "油化工"})
        assert _subchain_waiver_effective_ic(ev) == pytest.approx(0.20)

    def test_no_effective_returns_none(self):
        # 无 effective 子链 → None（不放行）
        assert _subchain_waiver_effective_ic(_evaluation(set())) is None

    def test_missing_report_returns_none(self):
        # 画像缺失 → None
        assert _subchain_waiver_effective_ic({"factor_id": "f1"}) is None


class TestApplySubchainIcWaiver:
    def test_waives_ic_only_when_effective(self, monkeypatch):
        _enable_waiver(monkeypatch, True)
        owner = _energy_owner()
        ev = _evaluation({"能源"}, ic=0.01)
        result = _apply_subchain_ic_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": ["Level 1 失败: IC=0.0100 < 0.03"],
        })
        assert result is not None
        assert result["passed"] is True
        assert result["subchain_waiver"] is True

    def test_waives_icir_dimension_too(self, monkeypatch):
        # IC 与 ICIR 同被稀释 → 两者均豁免
        _enable_waiver(monkeypatch, True)
        owner = _energy_owner()
        ev = _evaluation({"能源"}, ic=0.01)
        result = _apply_subchain_ic_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": [
                "Level 1 失败: IC=0.0100 < 0.03",
                "Level 1 失败: ICIR=0.2000 < 0.3",
            ],
        })
        assert result is not None
        assert result["passed"] is True

    def test_other_dimension_failure_blocks(self, monkeypatch):
        # Sharpe 不达标 → 不放行（plans/49 §B2 语义：仅 IC 维度）
        _enable_waiver(monkeypatch, True)
        owner = _energy_owner()
        ev = _evaluation({"能源"}, ic=0.01)
        result = _apply_subchain_ic_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": [
                "Level 1 失败: IC=0.0100 < 0.03",
                "Level 1 失败: 夏普=0.5 < 1.0",
            ],
        })
        assert result is None  # 不放行

    def test_disabled_switch_blocks(self, monkeypatch):
        _enable_waiver(monkeypatch, False)
        owner = _energy_owner()
        ev = _evaluation({"能源"}, ic=0.01)
        result = _apply_subchain_ic_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": ["Level 1 失败: IC=0.0100 < 0.03"],
        })
        assert result is None  # 开关关 → 不放行

    def test_non_energy_market_blocks(self, monkeypatch):
        _enable_waiver(monkeypatch, True)
        owner = SimpleNamespace(market="futures")
        ev = _evaluation({"能源"}, ic=0.01)
        result = _apply_subchain_ic_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": ["Level 1 失败: IC=0.0100 < 0.03"],
        })
        assert result is None

    def test_no_effective_blocks(self, monkeypatch):
        _enable_waiver(monkeypatch, True)
        owner = _energy_owner()
        ev = _evaluation(set(), ic=0.01)
        result = _apply_subchain_ic_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": ["Level 1 失败: IC=0.0100 < 0.03"],
        })
        assert result is None


class TestSubchainWaiverView:
    def test_view_replaces_ic_with_effective(self):
        # 评分卡放行视图：ic 替换为 effective 子链 |mean_ic|，其余字段不变
        ev = _evaluation({"能源"}, ic=0.01)
        view = _subchain_waiver_view(ev)
        assert view["level_1_backtest"]["ic"] == pytest.approx(0.20)
        assert view["level_1_backtest"]["sharpe"] == pytest.approx(2.0)  # 其余保持

    def test_view_no_effective_returns_same(self):
        ev = _evaluation(set(), ic=0.01)
        assert _subchain_waiver_view(ev) is ev


class TestSeedWaiver:
    def test_pass_true_when_effective(self, monkeypatch):
        _enable_waiver(monkeypatch, True)
        bt = {"ic": 0.01, "subchain_ic_report": {"subchain_ic_profile": _profile({"能源"})}}
        assert _subchain_waiver_pass(_energy_owner(), bt) is True

    def test_pass_false_when_disabled(self, monkeypatch):
        _enable_waiver(monkeypatch, False)
        bt = {"ic": 0.01, "subchain_ic_report": {"subchain_ic_profile": _profile({"能源"})}}
        assert _subchain_waiver_pass(_energy_owner(), bt) is False

    def test_seed_waiver_equivalent_to_candidate(self, monkeypatch):
        # 种子路径 Verifier 豁免与演化路径同构（只豁免 IC/ICIR）
        _enable_waiver(monkeypatch, True)
        owner = _energy_owner()
        ev = _evaluation({"能源"}, ic=0.01)
        result = _apply_seed_subchain_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": [
                "Level 1 失败: IC=0.0100 < 0.03",
                "Level 1 失败: ICIR=0.2000 < 0.3",
            ],
        })
        assert result is not None
        assert result["passed"] is True

    def test_seed_other_dimension_blocks(self, monkeypatch):
        _enable_waiver(monkeypatch, True)
        owner = _energy_owner()
        ev = _evaluation({"能源"}, ic=0.01)
        result = _apply_seed_subchain_waiver(owner, ev, {
            "passed": False,
            "failure_reasons": [
                "Level 1 失败: IC=0.0100 < 0.03",
                "Level 1 失败: 夏普=0.5 < 1.0",
            ],
        })
        assert result is None
