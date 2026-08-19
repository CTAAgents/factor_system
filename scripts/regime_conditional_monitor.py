"""Regime 条件化实盘监控（plans/53 §B，v2.105.0+9 灰度）。

监控项：
  1. 当前市场制度（从 current_combo.json regime_meta 读取，L3 已检测）；
  2. 条件化降权名单（因子库 regime 画像 × 当前制度 → build_regime_conditioned_weights，
     soft 模式 m 值）；
  3. 降权因子在组合中的权重占比（影响度）；
  4. 组合漂移（drift_history 最新 weight_l1_change——条件化对换手的影响）；
  5. 与昨日监控对比（delta）。

报告落盘: reports/energy/{date}/regime_conditional_monitor.md

用法:
    & 'C:/Program Files/Python312/python.exe' scripts/regime_conditional_monitor.py [--json]

版本: v0.1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

# 动态解析项目根（禁止硬编码绝对路径）
_FTS_ROOT = Path(__file__).resolve().parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.factor_db.repository import FactorRepository
from fts.factor_engine.regime_conditional_weight import (
    RegimeConditionalConfig,
    build_regime_conditioned_weights,
)

logger = logging.getLogger("regime_conditional_monitor")

COMBO_FILE = "memory/portfolio/energy/current_combo.json"
DRIFT_DIR = "memory/portfolio/energy/drift_history"
REPORT_DIR = "reports/energy"


def _load_combo() -> dict[str, Any]:
    """读取当前组合（含 regime_meta / signals 权重）。"""
    fp = Path(COMBO_FILE)
    if not fp.exists():
        raise FileNotFoundError(f"组合文件不存在: {fp}")
    return json.loads(fp.read_text(encoding="utf-8"))


def _load_latest_drift() -> Optional[dict[str, Any]]:
    """读取最新一条组合漂移记录（weight_l1_change）。"""
    d = Path(DRIFT_DIR)
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    records = []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(data, list):
                records.extend(data)
        except (json.JSONDecodeError, OSError):
            continue
    if not records:
        return None
    return records[-1]


def _load_prev_report() -> Optional[dict[str, Any]]:
    """读取昨日监控报告（若存在），用于 delta 对比。"""
    d = Path(REPORT_DIR)
    if not d.exists():
        return None
    files = sorted(d.glob("*/regime_conditional_monitor.json"))
    if not files:
        return None
    try:
        return json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime 条件化实盘监控（plans/53 §B）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args()

    try:
        from fts.config.settings import get_config

        cfg = get_config()
        rc = (getattr(cfg, "l3", {}) or {}).get("regime_conditional") or {}
        rc_cfg = RegimeConditionalConfig(**rc)
    except Exception as e:  # noqa: BLE001
        logger.warning("配置读取失败，回退默认: %s", e)
        rc_cfg = RegimeConditionalConfig()

    combo = _load_combo()
    regime = (combo.get("regime_meta") or {}).get("regime", "unknown")
    signals = combo.get("signals", [])
    retained = [s for s in signals if s.get("retained", True)]
    weight_by_fid: dict[str, float] = {}
    name_by_fid: dict[str, str] = {}
    for s in retained:
        if s.get("factor_id"):
            weight_by_fid[s["factor_id"]] = float(s.get("weight", 0.0))
            name_by_fid[s["factor_id"]] = s.get("name", s["factor_id"])

    # 因子库 regime 画像 → 当前制度下降权名单（soft 模式 m 值）
    repo = FactorRepository(market="energy")
    try:
        rows = repo.list_factors(market="energy", status="active", is_elite=True, limit=10000)
    finally:
        repo.close()
    factors = []
    for r in rows:
        md = r.get("metadata") or {}
        if md.get("regime_scope") and md.get("regime_ic_profile"):
            factors.append(
                {
                    "factor_id": r.get("factor_id"),
                    "name": r.get("name", ""),
                    "regime_scope": md.get("regime_scope"),
                    "regime_ic_profile": md.get("regime_ic_profile"),
                }
            )
    mod = build_regime_conditioned_weights(factors, regime, rc_cfg)
    down = {k: round(v, 4) for k, v in mod.items() if v != 1.0}

    # 降权因子在组合中的影响度（权重占比）
    impact: dict[str, float] = {}
    for fid, m in down.items():
        w = weight_by_fid.get(fid)
        if w is not None:
            impact[fid] = round(w * m / max(w, 1e-12), 4)  # 降权后剩余比例

    drift = _load_latest_drift()
    prev = _load_prev_report()

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "regime_conditional_enabled": rc_cfg.enabled,
        "decay_mode": rc_cfg.decay_mode,
        "current_regime": regime,
        "n_factors_with_profile": len(factors),
        "n_downgraded": len(down),
        "downgraded": [
            {
                "factor_id": fid,
                "name": name_by_fid.get(fid, fid),
                "m": m,
                "in_combo_weight": weight_by_fid.get(fid),
                "in_combo_retained_ratio": impact.get(fid),
            }
            for fid, m in sorted(down.items(), key=lambda kv: kv[1])
        ],
        "drift": (
            {
                "date": drift.get("date"),
                "member_overlap_rate": drift.get("member_overlap_rate"),
                "weight_l1_change": drift.get("weight_l1_change"),
            }
            if drift
            else None
        ),
        "vs_prev": (
            {
                "prev_regime": prev.get("current_regime"),
                "prev_n_downgraded": prev.get("n_downgraded"),
                "regime_changed": prev.get("current_regime") != regime,
                "n_downgraded_delta": (len(down) - (prev.get("n_downgraded") or 0)),
            }
            if prev
            else None
        ),
    }

    ts = date.today().isoformat()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        lines: list[str] = [
            "# Regime 条件化实盘监控（plans/53 §B）",
            "",
            f"- **生成时间**: {report['generated_at']}",
            f"- **开关**: enabled={rc_cfg.enabled} decay_mode={rc_cfg.decay_mode}",
            f"- **当前制度**: {regime}",
            f"- **画像因子数**: {report['n_factors_with_profile']}",
            f"- **降权因子数**: {report['n_downgraded']}",
            "",
            "## 降权因子",
            "",
            "| 因子 | m（剩余比例） | 组合权重 | 降权后剩余 |",
            "|:-----|:------------|:--------|:----------|",
        ]
        if report["downgraded"]:
            for d_ in report["downgraded"]:
                lines.append(
                    f"| {d_['name']} | {d_['m']} | "
                    f"{d_['in_combo_weight'] if d_['in_combo_weight'] is not None else '-'} | "
                    f"{d_['in_combo_retained_ratio'] if d_['in_combo_retained_ratio'] is not None else '（不在组合）'} |"
                )
        else:
            lines.append("| （当前制度下无因子被降权） | - | - | - |")
        if report["drift"]:
            dr = report["drift"]
            lines += [
                "",
                "## 组合漂移（条件化对换手影响）",
                "",
                f"- **漂移日期**: {dr['date']}",
                f"- **成员重合率**: {dr['member_overlap_rate']}",
                f"- **权重 L1 变化率**: {dr['weight_l1_change']}",
            ]
        if report["vs_prev"]:
            vp = report["vs_prev"]
            lines += [
                "",
                "## 与昨日对比",
                "",
                f"- **昨日制度**: {vp['prev_regime']} → 今日 {regime}（{'切换' if vp['regime_changed'] else '不变'}）",
                f"- **降权因子数**: {vp['prev_n_downgraded']} → {report['n_downgraded']}（Δ{vp['n_downgraded_delta']:+d}）",
            ]
        md = "\n".join(lines) + "\n"
        out_dir = Path(REPORT_DIR) / ts
        out_dir.mkdir(parents=True, exist_ok=True)
        Path(out_dir / "regime_conditional_monitor.md").write_text(md, encoding="utf-8")
        Path(out_dir / "regime_conditional_monitor.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(md)
        print(f"[monitor] 报告已落盘: {out_dir / 'regime_conditional_monitor.md'}")

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    raise SystemExit(main())
