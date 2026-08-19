"""plans/54 P0-1 — invalid_when 失效条件声明（契约 + 命中判定）单元测试。

覆盖：
  - 因子顶层 invalid_when.regimes 含当前 regime → 命中
  - metadata.invalid_when 兼容形式
  - 未命中（regimes 不含当前）/ 无声明 / current_regime=None → 不命中
  - 字符串容错不崩溃
  - FactorProgram 契约支持 invalid_when 字段
"""

from __future__ import annotations

from fts.factor_engine.contracts import FactorProgram, InvalidWhen
from fts.factor_engine.energy_qa_review import check_invalid_when


def _factor(invalid_when: dict | None = None) -> dict:
    f: dict = {"factor_id": "fct_00000001", "name": "test_factor", "code": "x"}
    if invalid_when is not None:
        f["invalid_when"] = invalid_when
    return f


class TestCheckInvalidWhen:
    """check_invalid_when 命中判定（纯函数）。"""

    def test_hit_top_level(self) -> None:
        """顶层 invalid_when.regimes 含当前 regime → 命中 + 详情。"""
        f = _factor({"regimes": ["high_vol"], "conditions": ["波动率突破区间"], "notes": "退出"})
        hit, detail = check_invalid_when(f, "high_vol")
        assert hit is True
        assert detail["regime"] == "high_vol"
        assert detail["declared_regimes"] == ["high_vol"]
        assert detail["conditions"] == ["波动率突破区间"]

    def test_miss_regime_not_declared(self) -> None:
        """regimes 不含当前 regime → 不命中。"""
        f = _factor({"regimes": ["bear"], "conditions": ["下跌趋势"]})
        hit, _ = check_invalid_when(f, "bull")
        assert hit is False

    def test_no_declaration(self) -> None:
        """无 invalid_when 声明 → 不命中。"""
        hit, _ = check_invalid_when(_factor(), "bull")
        assert hit is False

    def test_metadata_form(self) -> None:
        """metadata.invalid_when 兼容形式 → 命中。"""
        f = {"factor_id": "fct_00000002", "name": "m", "metadata": {"invalid_when": {"regimes": ["oscillate"]}}}
        hit, detail = check_invalid_when(f, "oscillate")
        assert hit is True

    def test_none_current_regime(self) -> None:
        """current_regime=None（无法检测）→ 不命中。"""
        f = _factor({"regimes": ["high_vol"]})
        hit, _ = check_invalid_when(f, None)
        assert hit is False

    def test_string_form_no_crash(self) -> None:
        """字符串容错：regimes 空 → 不命中但不崩溃。"""
        f = _factor("波动率突破区间")
        hit, _ = check_invalid_when(f, "high_vol")
        assert hit is False

    def test_multi_regime_declared(self) -> None:
        """声明多制度命中其一。"""
        f = _factor({"regimes": ["bear", "high_vol"]})
        hit, detail = check_invalid_when(f, "bear")
        assert hit is True
        assert detail["declared_regimes"] == ["bear", "high_vol"]


class TestInvalidWhenContract:
    """FactorProgram 契约支持 invalid_when 字段。"""

    def test_factor_program_accepts_invalid_when(self) -> None:
        """构造含 invalid_when 的 FactorProgram 不报错。"""
        iw: InvalidWhen = {"regimes": ["high_vol"], "conditions": ["波动率突破"], "notes": "降权观察"}
        fp: FactorProgram = {
            "factor_id": "fct_12345678",
            "name": "f",
            "code": "x",
            "params": {},
            "signature": {"inputs": [], "outputs": ["signal"]},
            "economic_logic": {"narrative": "机制说明"},
            "source": "seed",
            "parent_id": None,
            "generation": 0,
            "created_at": "2026-08-19",
            "trace_id": "t",
            "risk_tag": None,
            "market": "futures",
            "style_tags": None,
            "symbols": [],
            "factor_version": "v1",
            "is_multi_symbol": False,
            "kind": "code",
            "expression": None,
            "operator_depth": None,
            "operator_count": None,
            "max_lookback": None,
            "evaluation": None,
            "invalid_when": iw,
        }
        assert fp["invalid_when"]["regimes"] == ["high_vol"]
