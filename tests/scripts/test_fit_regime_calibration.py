"""fit_regime_calibration.py 脚本测试（GAP-094 离线拟合入口）。

覆盖:
  - _hit_label 各制度方向预期判定（bull/bear/oscillate/high_vol/low_vol/未知）
  - 滚动检测生成 (date, regime, confidence) 序列
  - main() 合成数据端到端：dry-run 不落盘 / 正常保存且可被生产侧消费
  - 有效样本不足时报错
"""

import json
import sys
from pathlib import Path

import pytest

_FTS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from scripts.fit_regime_calibration import (  # noqa: E402
    _build_regime_records,
    _build_synthetic_ohlcv,
    _hit_label,
    main,
)


# ─── _hit_label 判定 ──────────────────────────────────────


def test_hit_label_regime_direction() -> None:
    assert _hit_label("bull", 0.01, 0.0, 0.005, 0.02) == 1
    assert _hit_label("bull", -0.01, 0.0, 0.005, 0.02) == 0
    assert _hit_label("bear", -0.01, 0.0, 0.005, 0.02) == 1
    assert _hit_label("bear", 0.01, 0.0, 0.005, 0.02) == 0
    assert _hit_label("oscillate", 0.001, 0.0, 0.005, 0.02) == 1  # 低振幅命中
    assert _hit_label("oscillate", 0.01, 0.0, 0.005, 0.02) == 0
    assert _hit_label("high_vol", 0.0, 0.03, 0.005, 0.02) == 1  # 高波动命中
    assert _hit_label("high_vol", 0.0, 0.01, 0.005, 0.02) == 0
    assert _hit_label("low_vol", 0.0, 0.01, 0.005, 0.02) == 1
    assert _hit_label("low_vol", 0.0, 0.03, 0.005, 0.02) == 0
    assert _hit_label("unknown", 0.0, 0.0, 0.005, 0.02) is None  # 未知制度跳过


# ─── 滚动检测 ─────────────────────────────────────────────


def test_build_regime_records_produces_series() -> None:
    ohlcv = _build_synthetic_ohlcv(n=200, seed=3)
    records = _build_regime_records(ohlcv, window=60, step=1)
    assert len(records) == 140  # 200 - 60
    assert all({"date", "regime", "confidence"} <= set(r) for r in records)
    assert 0.0 <= records[0]["confidence"] <= 1.0


# ─── main() 端到端 ────────────────────────────────────────


@pytest.fixture()
def _synthetic_loader(monkeypatch):
    """注入合成数据加载器，避免触碰真实数据源。"""
    import scripts.fit_regime_calibration as mod

    monkeypatch.setattr(mod, "_load_ohlcv", lambda data, table, trace_id: _build_synthetic_ohlcv(n=400))
    return mod


def test_main_dry_run_no_save(_synthetic_loader, tmp_path) -> None:
    """dry-run：报告正常生成、不落盘。"""
    out = tmp_path / "cal.json"
    sys.argv = ["fit_regime_calibration.py", "--dry-run", "--out", str(out), "--trace-id", "t1"]
    assert main() == 0
    assert not out.exists()


def test_main_saves_calibration(_synthetic_loader, tmp_path) -> None:
    """非 dry-run：保存校准 JSON 且可被生产侧消费（_compute_exposure_scale）。"""
    out = tmp_path / "cal.json"
    sys.argv = ["fit_regime_calibration.py", "--out", str(out), "--trace-id", "t2"]
    assert main() == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["gap"] == "GAP-094"
    assert payload["fitted"] is True

    # 生产侧接线：_compute_exposure_scale 消费校准文件（不抛异常、输出在 [0,1]）
    from fts.factor_engine.portfolio_loop import _compute_exposure_scale

    scale = _compute_exposure_scale({"regime": "bull", "confidence": 0.7}, calibration_path=str(out))
    assert 0.0 <= scale <= 1.0


def test_main_insufficient_samples(monkeypatch) -> None:
    """有效样本不足（window 过大）→ 报错退出。"""
    import scripts.fit_regime_calibration as mod

    monkeypatch.setattr(mod, "_load_ohlcv", lambda data, table, trace_id: _build_synthetic_ohlcv(n=120))
    sys.argv = [
        "fit_regime_calibration.py",
        "--window",
        "110",
        "--min-samples",
        "50",
        "--dry-run",
    ]
    with pytest.raises(SystemExit):
        main()
