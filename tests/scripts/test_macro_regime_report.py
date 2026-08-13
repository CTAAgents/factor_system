"""macro_regime_report.py 脚本测试（GAP-092 四象限报告入口）。

覆盖:
  - main() 合成数据端到端：象限判定 + 画像输出
  - --json 结构化输出
  - 数据缺失 → quadrant=unavailable（如实标注不伪造）
  - 自定义阈值改变判定
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

import scripts.macro_regime_report as mod  # noqa: E402


def _pmi_series(values: list[float]) -> pd.Series:
    idx = pd.to_datetime([f"2026-{m:02d}-01" for m in range(1, len(values) + 1)])
    return pd.Series(values, index=idx, name="pmi")


def _cpi_series(values: list[float]) -> pd.Series:
    idx = pd.to_datetime([f"2026-{m:02d}-01" for m in range(1, len(values) + 1)])
    return pd.Series(values, index=idx, name="cpi")


@pytest.fixture()
def _mock_macro(monkeypatch):
    """注入合成宏观时序，避免真实网络请求。"""
    monkeypatch.setattr(mod, "_fetch_growth_series", lambda trace_id: _pmi_series([50.5, 51.2]))
    monkeypatch.setattr(mod, "_fetch_inflation_series", lambda trace_id: _cpi_series([3.0, 2.8]))
    return mod


def test_main_report_overheat(_mock_macro, capsys) -> None:
    """PMI 51.2（高增长）CPI 2.8（高通胀）→ overheat。"""
    sys.argv = ["macro_regime_report.py", "--trace-id", "t1"]
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "当前象限: overheat" in out
    assert "偏好: 周期/商品" in out


def test_main_json_output(_mock_macro, capsys) -> None:
    """--json 输出结构化报告。"""
    sys.argv = ["macro_regime_report.py", "--json", "--trace-id", "t2"]
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quadrant"] == "overheat"
    assert payload["data"]["growth_pmi"] == pytest.approx(51.2)
    assert payload["data"]["inflation_cpi"] == pytest.approx(2.8)
    assert abs(sum(payload["quadrant_probs"].values()) - 1.0) < 1e-6


def test_main_missing_data_unavailable(monkeypatch, capsys) -> None:
    """数据缺失 → quadrant=unavailable（不伪造）。"""
    monkeypatch.setattr(mod, "_fetch_growth_series", lambda trace_id: None)
    monkeypatch.setattr(mod, "_fetch_inflation_series", lambda trace_id: _cpi_series([2.0]))
    sys.argv = ["macro_regime_report.py", "--json", "--trace-id", "t3"]
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quadrant"] == "unavailable"
    assert payload["confidence"] is None


def test_main_custom_thresholds(_mock_macro, capsys) -> None:
    """自定义增长荣枯线 55 → PMI 51.2 判低增长 + CPI 2.8 高通胀 → stagflation。"""
    sys.argv = [
        "macro_regime_report.py",
        "--json",
        "--trace-id",
        "t4",
        "--growth-threshold",
        "55",
    ]
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["quadrant"] == "stagflation"
