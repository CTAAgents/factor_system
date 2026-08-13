"""tests/factor_engine/test_position_rank_crowding.py — 会员持仓排名拥挤度测试（GAP-069）。

覆盖：契约字段 / 指标计算（合成 rank 数据）/ 综合拥挤度边界 / 信号方向 /
      降级路径（Provider 异常、空数据、行数不足）/ 列映射归一化 / 交易所路由 /
      AKShare Provider 降级。
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from fts.factor_engine.position_rank_crowding import (
    AKSharePositionRankProvider,
    CrowdingResult,
    PositionRankConfig,
    _normalize_rank_columns,
    _route_exchange,
    compute_crowding,
    crowding_score,
    position_rank_crowding_signal,
)


def _make_rank_df(
    n_days: int = 3,
    n_members: int = 30,
    concentrate_first: int = 20,
    long_short_ratio: float = 1.0,
    seed: int = 42,
) -> pd.DataFrame:
    """构造合成持仓排名：前 concentrate_first 会员净多头集中。

    - 集中会员：long = base·4，short = base/ratio → 净多 3·base；
    - 非集中会员：long = base，short = base/ratio → ratio=1.0 时净持仓 0。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-06-01", periods=n_days, freq="B")
    rows: list[dict] = []
    for d in dates:
        for i in range(n_members):
            base = float(rng.uniform(500, 5000))
            long_pos = base * 4 if i < concentrate_first else base
            rows.append(
                {
                    "date": d,
                    "member": f"会员{i:02d}",
                    "long_position": long_pos,
                    "short_position": base / long_short_ratio,
                }
            )
    return pd.DataFrame(rows)


class _FakeProvider:
    """返回合成 rank 数据的假 Provider。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def get_rank(self, symbol, start_date=None, end_date=None) -> pd.DataFrame:
        df = self._df.copy()
        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]
        return df


class _RaisingProvider:
    def get_rank(self, symbol, start_date=None, end_date=None) -> pd.DataFrame:
        raise RuntimeError("数据源不可用")


class _EmptyProvider:
    def get_rank(self, symbol, start_date=None, end_date=None) -> pd.DataFrame:
        return pd.DataFrame()


# ── 1. 契约字段 ────────────────────────────────────────────


def test_config_defaults():
    cfg = PositionRankConfig()
    assert cfg.top_n == 20
    assert cfg.min_rank_rows == 5
    assert cfg.lookback_days == 5


def test_crowding_result_fields():
    r = CrowdingResult(
        symbol="RB0",
        date="2026-06-01",
        cr_top_n=0.8,
        long_short_ratio=1.5,
        net_holding_ratio=0.7,
        crowding_score=0.6,
        rank_available=True,
    )
    assert r.cr_top_n == 0.8
    assert r.rank_available is True


# ── 2. 指标计算 ────────────────────────────────────────────


def test_compute_crowding_outputs_metrics():
    rank_df = _make_rank_df(n_days=3)
    metrics = compute_crowding(rank_df, PositionRankConfig(), symbol="RB0")
    assert not metrics.empty
    assert list(metrics.columns) == [
        "date", "cr_top_n", "long_short_ratio", "net_holding_ratio",
        "crowding_score", "rank_available", "symbol",
    ]
    assert (metrics["rank_available"]).all()
    # 前 20/30 集中 → CR 接近 1（容差吸收浮点误差）
    assert (metrics["cr_top_n"] >= 0.5 - 1e-9).all()
    assert (metrics["cr_top_n"] <= 1.0 + 1e-9).all()


def test_compute_crowding_uniform_low_cr():
    """完全均匀持仓（多空相等、净持仓 0）→ CR 低。"""
    rank_df = _make_rank_df(n_members=30, concentrate_first=0)  # 无集中
    metrics = compute_crowding(rank_df, PositionRankConfig(), symbol="RB0")
    # 净持仓≈0 → CR 趋近 0
    assert metrics["cr_top_n"].mean() < 0.75


def test_compute_crowding_insufficient_rows_skipped():
    """单日行数 < min_rank_rows → 该日丢弃。"""
    rank_df = _make_rank_df(n_days=2, n_members=3)
    metrics = compute_crowding(rank_df, PositionRankConfig(min_rank_rows=5))
    assert metrics.empty


# ── 3. 综合拥挤度边界 ──────────────────────────────────────


def test_crowding_score_bounds():
    assert 0.0 <= crowding_score(0.0, 1.0, 0.0) < 0.1
    assert crowding_score(1.0, 3.0, 1.0) > 0.7
    assert 0.0 <= crowding_score(0.5, 2.0, 0.5) <= 1.0


# ── 4. 信号方向 ────────────────────────────────────────────


def test_crowding_signal_direction():
    provider = _FakeProvider(_make_rank_df(n_days=5, concentrate_first=20))
    sig = position_rank_crowding_signal(
        "RB0", PositionRankConfig(lookback_days=1), provider
    )
    assert sig is not None
    # 高集中 → 高拥挤 → 反转信号 -1
    assert (sig <= 0).all()

    provider_low = _FakeProvider(_make_rank_df(n_days=5, concentrate_first=0))
    sig_low = position_rank_crowding_signal(
        "RB0",
        PositionRankConfig(lookback_days=1, high_crowding=0.95),
        provider_low,
    )
    assert sig_low is not None
    # 无集中 → 净持仓≈0、多空比≈1 → 低拥挤 → 趋势延续 +1
    assert (sig_low >= 0).all()


# ── 5. 降级路径 ────────────────────────────────────────────


def test_signal_none_on_provider_exception():
    sig = position_rank_crowding_signal(
        "RB0", PositionRankConfig(), _RaisingProvider()
    )
    assert sig is None


def test_signal_none_on_empty_data():
    sig = position_rank_crowding_signal(
        "RB0", PositionRankConfig(), _EmptyProvider()
    )
    assert sig is None


# ── 6. 列映射归一化 ────────────────────────────────────────


def test_normalize_chinese_columns():
    raw = pd.DataFrame(
        {
            "日期": ["2026-06-01", "2026-06-01"],
            "会员简称": ["A", "B"],
            "持买单量": [1000, 800],
            "持卖单量": [600, 900],
            "买变化": [10, -5],
            "卖变化": [3, 2],
        }
    )
    norm = _normalize_rank_columns(raw)
    assert list(norm.columns) == [
        "date", "member", "long_position", "short_position",
        "long_change", "short_change",
    ]
    assert norm["long_position"].iloc[0] == 1000


def test_normalize_missing_required_columns_returns_empty():
    raw = pd.DataFrame({"foo": [1], "bar": [2]})
    assert _normalize_rank_columns(raw).empty


# ── 7. 交易所路由 ──────────────────────────────────────────


def test_route_exchange():
    assert _route_exchange("RB2610") == "shfe"
    assert _route_exchange("M2609") == "dce"
    assert _route_exchange("TA609") == "czce"
    assert _route_exchange("IF2609") == "cffex"
    assert _route_exchange("UNKNOWN") is None


# ── 8. AKShare Provider 降级 ───────────────────────────────


def test_akshare_provider_degrades_on_missing_api(monkeypatch):
    """接口不存在 → 空 DataFrame（不抛错）。"""
    monkeypatch.setattr(
        "fts.factor_engine.position_rank_crowding._AKSHARE_RANK_API",
        {"shfe": "nonexistent_api"},
    )
    provider = AKSharePositionRankProvider()
    assert provider.get_rank("RB0").empty


def test_akshare_provider_degrades_on_exception(monkeypatch):
    """接口抛异常 → 空 DataFrame（注入假 akshare 模块，避免依赖真实接口名）。"""

    class _FakeAk:
        def futures_shfe_position_rank(self, *args, **kwargs):
            raise RuntimeError("网络不可用")

    monkeypatch.setitem(sys.modules, "akshare", _FakeAk())
    provider = AKSharePositionRankProvider()
    assert provider.get_rank("RB0").empty


def test_akshare_provider_skips_unrouted_symbol():
    provider = AKSharePositionRankProvider()
    assert provider.get_rank("UNKNOWN123").empty
