"""
tests.test_futures_fundamental_sync — 期货基本面每日同步（Stage 2）单元测试。

覆盖: 计量单位换算、现货价格 WebSearch 补充三项校验、单品种面板构建、
Parquet upsert、sync_fundamental_fields 集成（mock provider / filler / 临时目录）。
全部通过 monkeypatch 隔离网络与真实缓存库。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import fts.data_futures_fundamental_sync as mod


# ─── 计量单位换算 ─────────────────────────────────────────


class TestUnitConversion:
    def test_same_unit_identity(self) -> None:
        assert mod._convert_to_futures_unit(3800.0, "元/吨", "元/吨") == 3800.0

    def test_gram_to_ton(self) -> None:
        # AU 现货价 元/克 → 期货单位 元/克（相同单位直接通过）
        assert mod._convert_to_futures_unit(600.0, "元/克", "元/克") == 600.0

    def test_kilogram_to_ton(self) -> None:
        # AG 现货价 元/千克 → 期货单位 元/千克（相同单位直接通过）
        assert mod._convert_to_futures_unit(7800.0, "元/千克", "元/千克") == 7800.0

    def test_ton_to_gram_conversion(self) -> None:
        # 若源为 元/吨，目标 元/克 → 换算（1 元/吨 = 0.001 元/克）
        assert mod._convert_to_futures_unit(600000.0, "元/吨", "元/克") == 600.0

    def test_index_point(self) -> None:
        assert mod._convert_to_futures_unit(3950.0, "点", "点") == 3950.0

    def test_unit_mismatch_returns_none(self) -> None:
        # 股指目标单位为"点"，来源为"元/吨"无法换算
        assert mod._convert_to_futures_unit(3950.0, "元/吨", "点") is None

    def test_get_futures_unit(self) -> None:
        assert mod.get_futures_unit("AU0") == "元/克"
        assert mod.get_futures_unit("AG0") == "元/千克"
        assert mod.get_futures_unit("IF0") == "点"
        assert mod.get_futures_unit("RB0") == "元/吨"


# ─── 现货价格 WebSearch 补充（三项校验）──────────────────


class _FakeFiller:
    """可控的 SpotPriceFiller 替身。"""

    def __init__(self, result: mod.SpotFillResult):
        self._result = result
        self.calls: list[tuple] = []

    def fill(self, symbol: str, variety_cn: str, ref_price: float, latest_date: str) -> mod.SpotFillResult:
        self.calls.append((symbol, variety_cn, ref_price, latest_date))
        return self._result


class TestSpotPriceFiller:
    def _make_filler(self, search_text: str = "螺纹钢现货价格 3800 元/吨", llm: object | None = None) -> mod.SpotPriceFiller:
        f = mod.SpotPriceFiller(timeout=1, llm_client=llm)
        f._websearch = lambda q: search_text  # type: ignore[method-assign]
        return f

    def test_fill_success(self, monkeypatch) -> None:
        f = self._make_filler()
        monkeypatch.setattr(mod, "_latest_ref_close", lambda symbol: 3850.0)
        r = f.fill("RB0", "螺纹钢", ref_price=3850.0, latest_date="2026-08-14")
        assert r.ok
        assert r.spot_price == 3800.0

    def test_sanity_violation_rejected(self) -> None:
        # 现货价 5500 与参考价 3850 偏离 42% > 阈值 30% → 拒绝
        f = self._make_filler(search_text="螺纹钢现货价格 5500 元/吨")
        r = f.fill("RB0", "螺纹钢", ref_price=3850.0, latest_date="2026-08-14")
        assert not r.ok
        assert "sanity_violation" in r.error

    def test_unit_mismatch_rejected(self) -> None:
        # 股指目标单位为点，搜索到 元/吨 → 拒绝
        f = self._make_filler(search_text="沪深300现货价格 3950 元/吨")
        r = f.fill("IF0", "沪深300", ref_price=3950.0, latest_date="2026-08-14")
        assert not r.ok
        assert "unit_mismatch" in r.error

    def test_stale_rejected(self) -> None:
        # 结果日期 2026-01-01 与最新交易日 2026-08-14 gap>3 天 → 拒绝
        f = self._make_filler(search_text="螺纹钢现货价格 2026-01-01 3800 元/吨")
        r = f.fill("RB0", "螺纹钢", ref_price=3850.0, latest_date="2026-08-14")
        assert not r.ok
        assert "stale" in r.error

    def test_no_price_parse_failed(self) -> None:
        f = self._make_filler(search_text="无价格信息的搜索页")
        r = f.fill("RB0", "螺纹钢", ref_price=3850.0, latest_date="2026-08-14")
        assert not r.ok
        assert "parse_failed" in r.error


# ─── 单品种面板构建 ───────────────────────────────────────


class _FakeProvider:
    """模拟 AkshareFuturesFundamentalProvider 三接口。"""

    def __init__(self, inventory: pd.DataFrame, basis: pd.DataFrame, wr: pd.DataFrame):
        self._inv = inventory
        self._basis = basis
        self._wr = wr

    def get_inventory(self, symbol: str) -> pd.DataFrame:
        return self._inv.copy()

    def get_basis(self, symbol: str, days: int) -> pd.DataFrame:
        return self._basis.copy()

    def get_warehouse_receipt(self, symbol: str, days: int) -> pd.DataFrame:
        return self._wr.copy()


def _mk_inv() -> pd.DataFrame:
    df = pd.DataFrame(
        {"inventory": [90221.0, 93856.0], "change": [None, 3635.0]},
        index=pd.to_datetime(["2026-08-13", "2026-08-14"]),
    )
    df.index.name = "date"
    return df


def _mk_basis() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "spot_price": [3790.0, 3800.0],
            "near_basis": [10.0, 15.0],
            "dom_basis": [8.0, 12.0],
            "near_basis_rate": [0.0026, 0.0040],
            "dom_basis_rate": [0.0021, 0.0032],
        },
        index=pd.to_datetime(["2026-08-13", "2026-08-14"]),
    )
    df.index.name = "date"
    return df


def _mk_wr() -> pd.DataFrame:
    df = pd.DataFrame(
        {"warehouse_receipt": [120000.0, 121000.0], "change": [None, 1000.0]},
        index=pd.to_datetime(["2026-08-13", "2026-08-14"]),
    )
    df.index.name = "date"
    return df


class TestBuildFundamentalPanel:
    def test_merge_columns(self) -> None:
        panel = mod._build_fundamental_panel(_FakeProvider(_mk_inv(), _mk_basis(), _mk_wr()), "RB0", 60, "t1")
        assert panel.index.is_monotonic_increasing
        assert list(panel.columns) == [*mod.FUNDAMENTAL_COLUMNS, "trace_id"]
        assert len(panel) == 2
        assert panel["fut_spot_price"].iloc[-1] == 3800.0
        assert panel["fut_inventory"].iloc[-1] == 93856.0
        assert panel["fut_warehouse_receipt"].iloc[-1] == 121000.0

    def test_provider_failure_yields_nan_columns(self) -> None:
        """provider 接口抛异常时面板仍保持列结构（NaN 填充）。"""
        class Boom:
            def get_inventory(self, symbol): raise RuntimeError("net down")
            def get_basis(self, symbol, days): raise RuntimeError("net down")
            def get_warehouse_receipt(self, symbol, days): raise RuntimeError("net down")

        panel = mod._build_fundamental_panel(Boom(), "RB0", 60, "t1")
        assert list(panel.columns) == [*mod.FUNDAMENTAL_COLUMNS, "trace_id"]
        assert panel.empty


# ─── sync_fundamental_fields 集成 ─────────────────────────


class TestSyncFundamentalFields:
    @pytest.fixture
    def tmp_cache(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setattr(mod, "FUNDAMENTAL_CACHE_DIR", tmp_path)
        return tmp_path

    def test_success_flow(self, tmp_cache: Path, monkeypatch) -> None:
        monkeypatch.setattr(mod, "_latest_ref_close", lambda symbol: 3850.0)
        result = mod.sync_fundamental_fields(
            ["RB0", "CU0"],
            days=60,
            trace_id="t_sync",
            provider=_FakeProvider(_mk_inv(), _mk_basis(), _mk_wr()),
        )
        assert result["success"] == 2
        assert result["failure"] == 0
        assert result["rows"] >= 2
        assert (tmp_cache / "RB0.parquet").exists()
        assert (tmp_cache / "CU0.parquet").exists()
        assert result["missing_spot"] == []

    def test_spot_missing_uses_filler(self, tmp_cache: Path, monkeypatch) -> None:
        """现货价全部缺失时触发 filler 补充；校验通过写入最新日。"""
        monkeypatch.setattr(mod, "_latest_ref_close", lambda symbol: 3850.0)
        basis = _mk_basis()
        basis["spot_price"] = float("nan")  # 现货价缺失
        filler = _FakeFiller(mod.SpotFillResult(True, spot_price=3800.0))
        result = mod.sync_fundamental_fields(
            ["RB0"],
            days=60,
            trace_id="t_sync",
            filler=filler,
            provider=_FakeProvider(_mk_inv(), basis, _mk_wr()),
        )
        assert result["success"] == 1
        assert result["missing_spot"] == []
        assert filler.calls, "现货价缺失时必须调用 filler"
        panel = pd.read_parquet(tmp_cache / "RB0.parquet")
        assert panel["fut_spot_price"].iloc[-1] == 3800.0

    def test_spot_fill_failed_records_missing(self, tmp_cache: Path, monkeypatch) -> None:
        monkeypatch.setattr(mod, "_latest_ref_close", lambda symbol: 3850.0)
        basis = _mk_basis()
        basis["spot_price"] = float("nan")
        filler = _FakeFiller(mod.SpotFillResult(False, error="websearch_failed: 404"))
        result = mod.sync_fundamental_fields(
            ["RB0"],
            days=60,
            trace_id="t_sync",
            filler=filler,
            provider=_FakeProvider(_mk_inv(), basis, _mk_wr()),
        )
        # 面板仍成功落盘，但缺失记录上报（失败透明）
        assert result["success"] == 1
        assert len(result["missing_spot"]) == 1
        assert result["missing_spot"][0]["symbol"] == "RB0"
        assert "websearch_failed" in result["missing_spot"][0]["error"]

    def test_upsert_dedup(self, tmp_cache: Path, monkeypatch) -> None:
        """重复同步同一日期不产生重复行（date 去重 upsert）。"""
        monkeypatch.setattr(mod, "_latest_ref_close", lambda symbol: 3850.0)
        provider = _FakeProvider(_mk_inv(), _mk_basis(), _mk_wr())
        mod.sync_fundamental_fields(["RB0"], days=60, trace_id="t1", provider=provider)
        mod.sync_fundamental_fields(["RB0"], days=60, trace_id="t2", provider=provider)
        df = pd.read_parquet(tmp_cache / "RB0.parquet")
        assert df["date"].duplicated().sum() == 0
