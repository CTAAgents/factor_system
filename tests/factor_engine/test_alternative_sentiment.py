"""
tests/factor_engine/test_alternative_sentiment.py — C2 舆情情感因子生成器测试

覆盖：词典打分（积极/消极/中性/混合/否定反转/无命中/空）/ 聚合（均值/离散/变化率
手算对照）/ 契约字段 / 命名与家族 / 零未来截断一致性 / 窗口自适应 / 降级（空新闻/
少记录/少日/缺列）/ 批量生成 / 坏品种跳过 / CLI 3 用例。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.alternative_sentiment import (
    FACTOR_KINDS,
    FinancialSentimentLexicon,
    SentimentFactorGenerator,
    SentimentGeneratorConfig,
)
from fts.factor_engine.backtest_pipeline import BacktestPipeline


# ─── helpers ────────────────────────────────────────────────────


def _make_news(
    days: int = 5,
    per_day: int = 3,
    seed: int = 1,
    positive: bool = True,
) -> pd.DataFrame:
    """合成新闻：每日 per_day 条，标题含积极/消极词。"""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-01-05")
    rows: list[dict] = []
    for d in range(days):
        date = (start + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        for t in range(per_day):
            if positive:
                title = rng.choice(["业绩增长", "需求旺盛", "价格突破上涨", "行业回暖"])
            else:
                title = rng.choice(["业绩亏损", "需求疲软下滑", "价格下跌", "风险预警"])
            rows.append({"date": date, "title": title, "summary": ""})
    return pd.DataFrame(rows)


def _make_mixed_news(days: int = 4, per_day: int = 2) -> pd.DataFrame:
    """每日固定 1 积极 + 1 消极 → 情感均值为正（字典权重不对称可调）。"""
    rows: list[dict] = []
    for d in range(days):
        date = (pd.Timestamp("2026-01-05") + pd.Timedelta(days=d)).strftime("%Y-%m-%d")
        rows.append({"date": date, "title": "业绩增长", "summary": ""})
        rows.append({"date": date, "title": "需求疲软", "summary": ""})
    return pd.DataFrame(rows)


def _make_panel(n: int = 50, start: str = "2026-01-01") -> pd.DataFrame:
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


def _make_gen(news: pd.DataFrame, **cfg_kwargs) -> SentimentFactorGenerator:
    kwargs = {"min_records": 3, "min_factor_rows": 2}
    kwargs.update(cfg_kwargs)
    cfg = SentimentGeneratorConfig(**kwargs)
    return SentimentFactorGenerator(
        config=cfg,
        news_provider=lambda symbol, lookback_days, trace_id: news,
    )


# ─── 词典打分 ────────────────────────────────────────────────────


class TestFinancialSentimentLexicon:
    def test_positive_word(self):
        assert FinancialSentimentLexicon.score_text("公司业绩增长") > 0
        assert FinancialSentimentLexicon.score_text("价格突破上涨") > 0

    def test_negative_word(self):
        assert FinancialSentimentLexicon.score_text("公司业绩亏损") < 0
        assert FinancialSentimentLexicon.score_text("需求疲软下滑") < 0

    def test_neutral_no_hit_returns_zero(self):
        assert FinancialSentimentLexicon.score_text("今天天气不错") == 0.0

    def test_empty_text_returns_zero(self):
        assert FinancialSentimentLexicon.score_text("") == 0.0
        assert FinancialSentimentLexicon.score_text(None) == 0.0

    def test_negation_reverses_polarity(self):
        """否定词反转：'不增长' 应 < 0（原本增长为正）。"""
        assert FinancialSentimentLexicon.score_text("需求不增长") < 0
        assert FinancialSentimentLexicon.score_text("未亏损") > 0

    def test_mixed_sentiment_within_range(self):
        score = FinancialSentimentLexicon.score_text("业绩增长但风险预警")
        assert -1.0 <= score <= 1.0

    def test_score_bounded(self):
        """多次命中不越界 [-1,1]。"""
        s = "增长" * 50
        score = FinancialSentimentLexicon.score_text(s)
        assert -1.0 <= score <= 1.0


# ─── 生成器 ──────────────────────────────────────────────────────


class TestSentimentGenerator:
    def test_generate_returns_candidate_with_contract(self):
        gen = _make_gen(_make_news(days=5, positive=True))
        cand = gen.generate("RB0", trace_id="t_senti_001")
        assert cand is not None
        f = cand.factor
        assert f["factor_id"].startswith("fct_")
        assert f["name"] == "sent_RB0_sent_mean"
        assert f["family"] == "behavioral"
        assert f["market"] == "futures"
        assert f["signature"]["frequency"] == "daily"
        assert f["signature"]["output_type"] == "signal"
        assert f["economic_logic"]["behavioral"] == 5
        assert f["economic_logic"]["narrative"].strip()
        assert f["symbols"] == ["RB0"]

    def test_params_embedded(self):
        gen = _make_gen(_make_news(days=5, positive=True))
        cand = gen.generate("RB0")
        assert cand is not None
        params = cand.factor["params"]
        assert params["kind"] == "sent_mean"
        assert params["symbol"] == "RB0"
        assert len(params["values"]) == len(params["dates"])

    def test_positive_news_means_positive(self):
        """全积极新闻 → 每交易日 sent_mean > 0。"""
        gen = _make_gen(_make_news(days=4, positive=True))
        cand = gen.generate("RB0")
        assert cand is not None
        assert all(v > 0 for v in cand.factor["params"]["values"])

    def test_negative_news_means_negative(self):
        gen = _make_gen(_make_news(days=4, positive=False))
        cand = gen.generate("RB0")
        assert cand is not None
        assert all(v < 0 for v in cand.factor["params"]["values"])

    def test_aggregate_mean_matches_manual(self):
        """sent_mean = 每日两条情感分均值（对照手算）。"""
        news = _make_mixed_news(days=3)
        gen = _make_gen(news)
        cand = gen.generate("RB0")
        assert cand is not None
        pos = FinancialSentimentLexicon.score_text("业绩增长")
        neg = FinancialSentimentLexicon.score_text("需求疲软")
        expected = (pos + neg) / 2.0
        assert all(v == pytest.approx(expected, abs=1e-6) for v in cand.factor["params"]["values"])

    def test_sent_chg_first_day_zero(self):
        """sent_chg 首日 = 0（diff 填充）。"""
        news = _make_mixed_news(days=4)
        gen = _make_gen(news)
        cands = gen.generate_batch(["RB0"])
        chg = next(c for c in cands if c.kind == "sent_chg")
        assert chg.factor["params"]["values"][0] == 0.0

    def test_generate_batch_all_kinds(self):
        gen = _make_gen(_make_news(days=5, positive=True))
        cands = gen.generate_batch(["RB0", "CU0"])
        assert len(cands) == 2 * len(FACTOR_KINDS)
        assert {c.kind for c in cands} == set(FACTOR_KINDS)
        assert {c.symbol for c in cands} == {"RB0", "CU0"}

    def test_generate_batch_skips_bad_symbols(self):
        good = _make_news(days=5, positive=True)

        def provider(symbol, lookback_days, trace_id):
            return good if symbol == "RB0" else pd.DataFrame()

        cfg = SentimentGeneratorConfig(min_records=3, min_factor_rows=2)
        gen = SentimentFactorGenerator(config=cfg, news_provider=provider)
        cands = gen.generate_batch(["RB0", "BAD"])
        assert {c.symbol for c in cands} == {"RB0"}


# ─── 降级 ──────────────────────────────────────────────────────


class TestSentimentDegradation:
    def test_empty_news_returns_none(self):
        gen = _make_gen(pd.DataFrame())
        assert gen.generate("RB0") is None

    def test_too_few_records_returns_none(self):
        news = pd.DataFrame({"date": ["2026-01-05"], "title": ["业绩增长"], "summary": [""]})
        gen = _make_gen(news, min_records=5)  # 1 条 < 5
        assert gen.generate("RB0") is None

    def test_too_few_days_returns_none(self):
        gen = _make_gen(_make_news(days=2, per_day=3), min_factor_rows=5)  # 2 日 < 5
        assert gen.generate("RB0") is None

    def test_missing_columns_returns_none(self):
        news = pd.DataFrame({"date": ["2026-01-05"], "summary": [""]})  # 缺 title
        gen = _make_gen(news)
        assert gen.generate("RB0") is None


# ─── code 执行与零未来 ──────────────────────────────────────────


class TestSentimentCode:
    def test_code_executes_on_panel(self):
        gen = _make_gen(_make_news(days=5, positive=True))
        cand = gen.generate("RB0")
        assert cand is not None
        panel = _make_panel(n=50)
        sig = BacktestPipeline._execute_factor_code(
            cand.factor["code"], panel, cand.factor.get("params") or {}
        )
        assert isinstance(sig, np.ndarray)
        assert len(sig) == 50

    def test_window_adaptive(self):
        gen = _make_gen(_make_news(days=5, positive=True))
        cand = gen.generate("RB0")
        assert cand is not None
        for n in (10, 30, 120):
            panel = _make_panel(n=n)
            sig = BacktestPipeline._execute_factor_code(
                cand.factor["code"], panel, cand.factor.get("params") or {}
            )
            assert len(sig) == n

    def test_zero_future_tail_consistency(self):
        """零未来：截断新闻与全量新闻在重叠日值一致。"""
        full = _make_news(days=5, per_day=3, seed=3, positive=True)
        truncated = full[full["date"] < "2026-01-08"]  # 前 3 日
        gen_full = _make_gen(full)
        gen_trunc = _make_gen(truncated)
        c_full = gen_full.generate("RB0")
        c_trunc = gen_trunc.generate("RB0")
        assert c_full is not None and c_trunc is not None
        v_full = dict(zip(c_full.factor["params"]["dates"], c_full.factor["params"]["values"]))
        v_trunc = dict(zip(c_trunc.factor["params"]["dates"], c_trunc.factor["params"]["values"]))
        for d in v_trunc:
            assert v_trunc[d] == pytest.approx(v_full[d], abs=1e-6)

    def test_code_zero_after_coverage(self):
        gen = _make_gen(_make_news(days=4, positive=True))
        cand = gen.generate("RB0")
        assert cand is not None
        params = cand.factor["params"]
        panel = _make_panel(n=6, start="2020-01-01")  # 早于聚合区间
        sig = BacktestPipeline._execute_factor_code(cand.factor["code"], panel, params)
        assert np.allclose(sig, 0.0)


# ─── CLI senti-generate ──────────────────────────────────────────


class TestCliSentiGenerate:
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
        from fts.factor_engine import alternative_sentiment as alt

        class _NoopGen:
            def generate_batch(self, symbols=None, trace_id=None):
                return []

        monkeypatch.setattr(alt, "SentimentFactorGenerator", _NoopGen)
        rc = cli._cmd_factor_senti_generate(self._make_args())
        assert rc == 1
        assert "无候选生成" in capsys.readouterr().err

    def test_candidates_output_names(self, monkeypatch, capsys):
        from fts import cli
        from fts.factor_engine import alternative_sentiment as alt

        cand = _make_gen(_make_news(days=5, positive=True)).generate("RB0")
        assert cand is not None

        class _FakeGen:
            def generate_batch(self, symbols=None, trace_id=None):
                return [cand]

        monkeypatch.setattr(alt, "SentimentFactorGenerator", _FakeGen)
        rc = cli._cmd_factor_senti_generate(self._make_args())
        assert rc == 0
        assert "sent_RB0_sent_mean" in capsys.readouterr().out

    def test_json_output(self, monkeypatch, capsys):
        import json as _json

        from fts import cli
        from fts.factor_engine import alternative_sentiment as alt

        cand = _make_gen(_make_news(days=5, positive=True)).generate("RB0")
        assert cand is not None

        class _FakeGen:
            def generate_batch(self, symbols=None, trace_id=None):
                return [cand]

        monkeypatch.setattr(alt, "SentimentFactorGenerator", _FakeGen)
        rc = cli._cmd_factor_senti_generate(self._make_args(json=True))
        assert rc == 0
        payload = _json.loads(capsys.readouterr().out)
        assert payload[0]["name"] == "sent_RB0_sent_mean"
