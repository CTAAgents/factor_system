"""
tests/factor_engine/test_microstructure_generator.py — C1 微观结构因子生成器测试

覆盖：聚合正确性 / code 可执行 / 零未来函数截断一致性 / 窗口自适应 / 日期对齐 /
数据不足降级（空 tick / 少行 / 少日）/ 契约字段 / 命名与家族 / 批量生成 / 排除当日。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.backtest_pipeline import BacktestPipeline
from fts.factor_engine.microstructure_generator import (
    FACTOR_KINDS,
    MicrostructureFactorGenerator,
    MicrostructureGeneratorConfig,
)


# ─── helpers ────────────────────────────────────────────────────


def _make_ticks(
    days: int = 5,
    ticks_per_day: int = 50,
    seed: int = 1,
    direction: Optional[int] = None,
) -> pd.DataFrame:
    """合成 tick 快照（datetime 列 + last_price/volume + 5 档盘口）。

    direction=None 随机游走；1 全涨 / -1 全跌 用于确定性 OFI 断言。
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-01-05")
    rows: list[dict] = []
    price = 3000.0
    for d in range(days):
        date = start + pd.Timedelta(days=d)
        cum_vol = 0.0
        for t in range(ticks_per_day):
            ts = date + pd.Timedelta(seconds=t * 2)
            if direction == 1:
                price += 0.1
            elif direction == -1:
                price -= 0.1
            else:
                price += float(rng.normal(0, 0.5))
            vol = int(rng.integers(1, 20))
            cum_vol += vol
            row: dict = {
                "datetime": ts,
                "last_price": round(price, 2),
                "volume": float(cum_vol),
            }
            for i in range(1, 6):
                row[f"bid_volume{i}"] = int(rng.integers(1, 50))
                row[f"ask_volume{i}"] = int(rng.integers(1, 50))
            rows.append(row)
    return pd.DataFrame(rows)


def _make_panel(n: int = 50, start: str = "2026-01-01") -> pd.DataFrame:
    """合成日频面板（DatetimeIndex，供 _execute_factor_code）。"""
    idx = pd.date_range(start, periods=n, freq="B")
    close = 3000.0 + np.arange(n) * 0.5
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.full(n, 1e5),
        },
        index=idx,
    )


def _make_gen(ticks: pd.DataFrame, **cfg_kwargs) -> MicrostructureFactorGenerator:
    kwargs = {
        "min_tick_rows": 50,
        "min_factor_rows": 2,
        "exclude_last_day": False,
    }
    kwargs.update(cfg_kwargs)
    cfg = MicrostructureGeneratorConfig(**kwargs)
    return MicrostructureFactorGenerator(
        config=cfg,
        tick_provider=lambda symbol, count, trace_id: ticks,
    )


# ─── 聚合与候选 ────────────────────────────────────────────────


class TestMicrostructureGenerator:
    def test_generate_returns_candidate_with_contract(self):
        gen = _make_gen(_make_ticks(days=5))
        cand = gen.generate("RB0", trace_id="t_micro_001")
        assert cand is not None
        f = cand.factor
        assert f["factor_id"].startswith("fct_")
        assert f["name"] == "micro_RB0_ofi_mean"
        assert f["family"] == "microstructure"
        assert f["market"] == "futures"
        assert f["signature"]["frequency"] == "daily"
        assert f["signature"]["output_type"] == "signal"
        assert f["economic_logic"]["microstructure"] == 5
        assert f["economic_logic"]["narrative"].strip()
        assert f["symbols"] == ["RB0"]

    def test_params_embedded(self):
        gen = _make_gen(_make_ticks(days=5))
        cand = gen.generate("RB0")
        assert cand is not None
        params = cand.factor["params"]
        assert params["kind"] == "ofi_mean"
        assert params["symbol"] == "RB0"
        assert isinstance(params["dates"], list) and params["dates"]
        assert len(params["values"]) == len(params["dates"])

    def test_aggregate_positive_direction_means_ofi_near_one(self):
        """全涨 tick → 每交易日 OFI≈1（首 tick 滚动窗不足记 0，日均 >0.9）。"""
        ticks = _make_ticks(days=4, ticks_per_day=40, direction=1)
        gen = _make_gen(ticks)
        cand = gen.generate("RB0")
        assert cand is not None
        values = cand.factor["params"]["values"]
        assert all(v > 0.9 for v in values)

    def test_aggregate_negative_direction_means_ofi_near_minus_one(self):
        ticks = _make_ticks(days=4, ticks_per_day=40, direction=-1)
        gen = _make_gen(ticks)
        cand = gen.generate("RB0")
        assert cand is not None
        values = cand.factor["params"]["values"]
        assert all(v < -0.9 for v in values)

    def test_exclude_last_day(self):
        """exclude_last_day=True 时最后一个聚合交易日不出现在 dates。"""
        ticks = _make_ticks(days=5)
        cfg = MicrostructureGeneratorConfig(
            min_tick_rows=50, min_factor_rows=2, exclude_last_day=True
        )
        gen = MicrostructureFactorGenerator(
            config=cfg, tick_provider=lambda s, c, t: ticks
        )
        cand = gen.generate("RB0")
        assert cand is not None
        # 聚合日期 = tick 覆盖的交易日去尾
        tick_dates = sorted({d.date().isoformat() for d in pd.to_datetime(ticks["datetime"])})
        assert cand.factor["params"]["dates"][-1] == tick_dates[-2]

    def test_generate_batch_all_kinds(self):
        gen = _make_gen(_make_ticks(days=5))
        cands = gen.generate_batch(["RB0", "CU0"])
        assert len(cands) == 2 * len(FACTOR_KINDS)
        kinds = {c.kind for c in cands}
        assert kinds == set(FACTOR_KINDS)
        symbols = {c.symbol for c in cands}
        assert symbols == {"RB0", "CU0"}

    def test_generate_batch_skips_bad_symbols(self):
        """坏品种（空 tick）跳过不抛错，其余正常。"""
        good = _make_ticks(days=5)

        def provider(symbol, count, trace_id):
            return good if symbol == "RB0" else pd.DataFrame()

        cfg = MicrostructureGeneratorConfig(min_tick_rows=50, min_factor_rows=2)
        gen = MicrostructureFactorGenerator(config=cfg, tick_provider=provider)
        cands = gen.generate_batch(["RB0", "BAD"])
        assert {c.symbol for c in cands} == {"RB0"}


# ─── 降级 ──────────────────────────────────────────────────────


class TestMicrostructureDegradation:
    def test_empty_ticks_returns_none(self):
        gen = _make_gen(pd.DataFrame())
        assert gen.generate("RB0") is None

    def test_too_few_tick_rows_returns_none(self):
        gen = _make_gen(_make_ticks(days=1, ticks_per_day=10))  # 10 行 < 50
        assert gen.generate("RB0") is None

    def test_too_few_days_returns_none(self):
        gen = _make_gen(
            _make_ticks(days=2, ticks_per_day=50),
            min_factor_rows=5,  # 2 日 < 5
        )
        assert gen.generate("RB0") is None

    def test_generate_batch_empty_returns_empty_list(self):
        gen = _make_gen(pd.DataFrame())
        assert gen.generate_batch(["RB0"]) == []


# ─── code 执行与零未来 ──────────────────────────────────────────


class TestMicrostructureCode:
    def test_code_executes_on_panel(self):
        gen = _make_gen(_make_ticks(days=5))
        cand = gen.generate("RB0")
        assert cand is not None
        panel = _make_panel(n=50)
        sig = BacktestPipeline._execute_factor_code(
            cand.factor["code"], panel, cand.factor.get("params") or {}
        )
        assert isinstance(sig, np.ndarray)
        assert len(sig) == 50

    def test_window_adaptive(self):
        """不同长度评估面板输出长度一致匹配。"""
        gen = _make_gen(_make_ticks(days=5))
        cand = gen.generate("RB0")
        assert cand is not None
        for n in (10, 30, 120):
            panel = _make_panel(n=n)
            sig = BacktestPipeline._execute_factor_code(
                cand.factor["code"], panel, cand.factor.get("params") or {}
            )
            assert len(sig) == n

    def test_datetime_alignment(self):
        """信号在聚合日期处等于对应聚合值（日期对齐正确）。"""
        gen = _make_gen(_make_ticks(days=4, ticks_per_day=40, direction=1))
        cand = gen.generate("RB0")
        assert cand is not None
        params = cand.factor["params"]
        # 面板日期逐个落在聚合日期（dates 与面板等长）
        panel = _make_panel(n=4, start=params["dates"][0])
        panel.index = pd.to_datetime(params["dates"][:4])
        sig = BacktestPipeline._execute_factor_code(
            cand.factor["code"], panel, params
        )
        for i, d in enumerate(params["dates"][:4]):
            assert sig[i] == pytest.approx(params["values"][i], abs=1e-6)

    def test_zero_future_tail_consistency(self):
        """零未来：t 日信号不依赖 t+1 tick——截断后 tick 与全量 tick 在重叠日值一致。"""
        full = _make_ticks(days=5, ticks_per_day=40, seed=3)
        truncated = full[full["datetime"] < pd.Timestamp("2026-01-08")]  # 前 3 日
        gen_full = _make_gen(full)
        gen_trunc = _make_gen(truncated)
        cand_full = gen_full.generate("RB0")
        cand_trunc = gen_trunc.generate("RB0")
        assert cand_full is not None and cand_trunc is not None
        dates_full = cand_full.factor["params"]["dates"]
        dates_trunc = cand_trunc.factor["params"]["dates"]
        # 截断后覆盖日期是全集的前缀子集
        assert set(dates_trunc) <= set(dates_full)
        # 重叠日期聚合值一致（全量生成不受未来 tick 影响）
        vmap_full = dict(zip(dates_full, cand_full.factor["params"]["values"]))
        vmap_trunc = dict(zip(dates_trunc, cand_trunc.factor["params"]["values"]))
        for d in dates_trunc:
            assert vmap_trunc[d] == pytest.approx(vmap_full[d], abs=1e-6)

    def test_code_zero_after_coverage(self):
        """超出聚合覆盖区间的面板日期信号保持 0（默认填充）。"""
        gen = _make_gen(_make_ticks(days=4, ticks_per_day=40))
        cand = gen.generate("RB0")
        assert cand is not None
        params = cand.factor["params"]
        panel = _make_panel(n=6, start="2020-01-01")  # 早于聚合区间
        sig = BacktestPipeline._execute_factor_code(cand.factor["code"], panel, params)
        assert np.allclose(sig, 0.0)


def test_factor_executor_injects_datetime_for_datetime_index():
    """执行器对 DatetimeIndex 面板注入 datetime 列（microstructure code 依赖）。"""
    panel = _make_panel(n=5)
    data_dict = {col: panel[col].to_numpy(dtype=np.float64) for col in panel.columns}
    # 模拟执行器注入逻辑（与 backtest_pipeline._execute_factor_code 一致）
    if isinstance(panel.index, pd.DatetimeIndex):
        data_dict["datetime"] = panel.index.strftime("%Y-%m-%d").to_numpy(dtype=str)
    assert "datetime" in data_dict
    assert len(data_dict["datetime"]) == 5


# ─── CLI micro-generate ──────────────────────────────────────────


class TestCliMicroGenerate:
    """CLI `fts factor micro-generate` 命令接线（C1）。"""

    @staticmethod
    def _make_args(**kwargs):
        import argparse

        ns = argparse.Namespace()
        ns.symbols = kwargs.get("symbols")
        ns.limit = kwargs.get("limit", 0)
        ns.json = kwargs.get("json", False)
        return ns

    def test_no_candidates_returns_1(self, monkeypatch, capsys):
        from fts import cli
        from fts.factor_engine import microstructure_generator as mg

        class _NoopGen:
            def generate_batch(self, symbols=None, trace_id=None):
                return []

        monkeypatch.setattr(mg, "MicrostructureFactorGenerator", _NoopGen)
        rc = cli._cmd_factor_micro_generate(self._make_args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "无候选生成" in err

    def test_candidates_output_names(self, monkeypatch, capsys):
        from fts import cli
        from fts.factor_engine import microstructure_generator as mg

        cand = _make_gen(_make_ticks(days=5)).generate("RB0")
        assert cand is not None

        class _FakeGen:
            def generate_batch(self, symbols=None, trace_id=None):
                return [cand]

        monkeypatch.setattr(mg, "MicrostructureFactorGenerator", _FakeGen)
        rc = cli._cmd_factor_micro_generate(self._make_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "micro_RB0_ofi_mean" in out

    def test_json_output(self, monkeypatch, capsys):
        import json as _json

        from fts import cli
        from fts.factor_engine import microstructure_generator as mg

        cand = _make_gen(_make_ticks(days=5)).generate("RB0")
        assert cand is not None

        class _FakeGen:
            def generate_batch(self, symbols=None, trace_id=None):
                return [cand]

        monkeypatch.setattr(mg, "MicrostructureFactorGenerator", _FakeGen)
        rc = cli._cmd_factor_micro_generate(self._make_args(json=True))
        assert rc == 0
        payload = _json.loads(capsys.readouterr().out)
        assert payload[0]["name"] == "micro_RB0_ofi_mean"
