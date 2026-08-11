"""算子扩容核验脚本（loop 任务收尾）— 双注册表计数 + 一致性 + -W error 冒烟。

用法: python scripts/verify_operator_expansion.py
"""
from __future__ import annotations

import inspect
import warnings

import numpy as np
import pandas as pd

from fts.factor_engine.expr_dsl.registry import (
    build_registry,
    verify_registry_consistency,
)
from fts.factor_engine.feature_ops import OperatorRegistry

RNG = np.random.default_rng(20260811)


def _mk(n: int = 120, seed: int | None = None) -> pd.Series:
    rng = np.random.default_rng(seed) if seed is not None else RNG
    return pd.Series(rng.standard_normal(n) + np.linspace(0, 2, n))


def main() -> int:
    dsl = build_registry()
    gp = OperatorRegistry()
    dsl_n, gp_n = len(dsl), len(gp._operators)
    print(f"DSL 算子数: {dsl_n} | GP 算子数: {gp_n}")

    check = verify_registry_consistency()
    print(
        f"一致性: consistent={check.get('consistent')} "
        f"mismatched={check.get('mismatched')} errors={check.get('errors')}"
    )
    mm = check.get("mismatched")
    assert check.get("consistent") is True
    assert not mm or mm == 0
    assert not check.get("errors")

    # -W error::RuntimeWarning 冒烟 — 范围: 本次扩容新增 D10~D17 族（380 算子）
    # DSL 侧 category 为 L0-L5 分层，新族名集合从 GP 注册表 category=d10~d17 提取
    new_families = {f"d{i}" for i in range(10, 18)}
    new_names = {
        name
        for name, (info, _f) in gp._operators.items()
        if info.category in new_families
    }
    smoke_ops = {n: m for n, m in dsl.items() if n in new_names}
    print(f"冒烟算子数: {len(smoke_ops)} (D10~D17, 未匹配 {len(new_names) - len(smoke_ops)})")
    assert len(smoke_ops) == len(new_names), "GP 新族算子在 DSL 中缺失（双注册表断裂）"
    series = _mk(seed=7)
    high = series + 0.5
    low = series - 0.5
    volume = pd.Series(np.abs(RNG.standard_normal(120)) + 0.5)
    amount = series.abs() * volume
    fs = pd.Series(RNG.uniform(1e6, 1e8, 120))
    const = pd.Series([5.0] * 120)
    cvol = pd.Series([3.0] * 120)
    camt = pd.Series([30.0] * 120)

    _SERIES_NAMES = {"series", "x", "y", "close", "spot", "near", "open_p",
                     "future", "bench", "other"}

    def _make_args(fn, s: pd.Series, v: pd.Series, h: pd.Series, lo: pd.Series,
                   a: pd.Series, fsh: pd.Series) -> list:
        args = []
        for pname, p in inspect.signature(fn).parameters.items():
            if pname in _SERIES_NAMES:
                args.append(s)
            elif pname == "volume":
                args.append(v)
            elif pname == "high":
                args.append(h)
            elif pname == "low":
                args.append(lo)
            elif pname == "amount":
                args.append(a)
            elif pname == "float_shares":
                args.append(fsh)
            elif p.default is not inspect.Parameter.empty:
                args.append(p.default)
            else:
                args.append(20)  # 无默认值的数值参数兜底
        return args

    bad: list[str] = []

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        for name, meta in smoke_ops.items():
            fn = meta.func
            try:
                args = _make_args(fn, series, volume, high, low, amount, fs)
                out = fn(*args)
                if not isinstance(out, pd.Series) or len(out) != len(series):
                    bad.append(f"{name}: 返回类型/长度异常")
                elif np.any(np.isinf(out.to_numpy())):
                    # ±inf 属于 bug 类（如常数/零方差序列除以 0），禁止
                    bad.append(f"{name}: 含 ±inf")
            except RuntimeWarning as exc:  # noqa: PERF203
                bad.append(f"{name}: RuntimeWarning -> {exc}")
            except Exception as exc:  # noqa: BLE001,PERF203
                bad.append(f"{name}: 异常 {type(exc).__name__}: {exc}")

    # 常数序列不抛异常；口径与测试一致：dropna 后全有限且非全 NaN（无 ±inf）
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        for name, meta in smoke_ops.items():
            try:
                args = _make_args(meta.func, const, cvol, const, const, camt, fs)
                out = meta.func(*args)
                finite = bool(np.isfinite(out.dropna()).all()) and not out.isna().all()
                if not finite:
                    bad.append(f"{name}: 常数序列输出非有限/全 NaN")
            except RuntimeWarning as exc:  # noqa: PERF203
                bad.append(f"{name}: 常数序列 RuntimeWarning -> {exc}")
            except Exception as exc:  # noqa: BLE001,PERF203
                bad.append(f"{name}: 常数序列异常 {type(exc).__name__}: {exc}")

    if bad:
        print(f"冒烟失败 {len(bad)} 项:")
        for b in bad[:30]:
            print("  -", b)
        return 1
    print(f"冒烟通过: {len(smoke_ops)} 个新算子 (D10~D17) 随机序列 + 常数序列均无 RuntimeWarning/bug (bad: NONE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
