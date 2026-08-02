"""
seed_data/loader.py — 外部种子因子加载器

将 WQ 101 / Qlib 158 因子定义转换为 FactorProgram 对象。
通过模板生成可执行 Python 代码，统一注入 SeedPool。

版本: v1.1.0
"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    EconomicLogic,
    FactorProgram,
    FactorSignature,
)
from ..factor_program import create_factor_program

# ─── 代码生成模板 ─────────────────────────────────────────

_ALPHA_OPS_SOURCE = """
    import numpy as np
    import pandas as pd

    _EPS = 1e-10

    def _to_series(x):
        return x if isinstance(x, pd.Series) else pd.Series(x)

    def _to_array(x):
        return x.values if isinstance(x, pd.Series) else np.asarray(x)

    def rank(x):
        n = len(x)
        if n <= 1: return np.zeros_like(x)
        return np.argsort(np.argsort(x)).astype(float) / (n - 1)

    def scale(x, a=1.0):
        s = np.sum(np.abs(x))
        return x * a / s if s > _EPS else x

    def ifelse(cond, a, b):
        return np.where(cond, a, b)

    def ts_sum(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).sum())

    def ts_mean(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).mean())

    def ts_stddev(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).std(ddof=0))

    def ts_corr(x, y, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).corr(_to_series(y)))

    def ts_covariance(x, y, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).cov(_to_series(y)))

    def ts_argmax(x, d):
        def _f(v): return np.argmax(v) if len(v) > 0 else 0
        return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_f, raw=True))

    def ts_argmin(x, d):
        def _f(v): return np.argmin(v) if len(v) > 0 else 0
        return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_f, raw=True))

    def ts_rank(x, d):
        def _f(v):
            if len(v) <= 1: return 0.5
            return np.argsort(np.argsort(v))[-1] / (len(v) - 1)
        return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_f, raw=True))

    def ts_min(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).min())

    def ts_max(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).max())

    def ts_product(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).apply(np.prod, raw=True))

    def signed_power(x, a):
        return np.sign(x) * np.abs(x) ** a

    def decay_linear(x, d):
        w = np.arange(1, d + 1, dtype=float)
        w = w / w.sum()
        def _f(v):
            if len(v) < d: return np.nan
            return np.sum(v[-d:] * w)
        return _to_array(_to_series(x).rolling(d, min_periods=d).apply(_f, raw=True))

    def delta(x, d):
        return x - delay(x, d)

    def delay(x, d):
        return _to_array(_to_series(x).shift(d))

    def log(x):
        return np.log(np.maximum(x, _EPS))

    def sign(x):
        return np.sign(x)

    def abs(x):
        return np.abs(x)

    def neg(x):
        return -x

    def highday(x, d):
        def _f(v):
            if len(v) <= 1: return 0.0
            return float(len(v) - 1 - np.argmax(v))
        return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_f, raw=True))

    def lowday(x, d):
        def _f(v):
            if len(v) <= 1: return 0.0
            return float(len(v) - 1 - np.argmin(v))
        return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_f, raw=True))
"""

_FACTOR_CODE_TEMPLATE = '''\
def factor_program(data, params):
    """Alpha: {name} — {narrative}"""
{alpha_ops}
    close = data['close'].values if hasattr(data, 'close') else data['close']
    high = data['high'].values if hasattr(data, 'high') else data['high']
    low = data['low'].values if hasattr(data, 'low') else data['low']
    open_ = data['open'].values if hasattr(data, 'open') else data['open']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    vwap = (data.get('vwap', data['close']).values if hasattr(data, 'vwap')
            else data.get('vwap', data['close']))
    returns = np.zeros_like(close)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], _EPS)

    score = {expression}
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
'''


# ─── 因子定义 → FactorProgram 转换 ────────────────────────

def _tier_from_i(i: int) -> int:
    """根据 alpha 序号分配经济逻辑理论评分（WQ 早期公式更经典）。"""
    if i < 20:
        return 5
    if i < 50:
        return 4
    return 3


def _estimate_input_fields(expression: str) -> list[str]:
    """从表达式推断所需输入字段。"""
    fields = {"close"}
    expr_lower = expression.lower()
    if "volume" in expr_lower:
        fields.add("volume")
    if "high" in expr_lower and "highday" not in expr_lower:
        fields.add("high")
    if "low" in expr_lower and "lowday" not in expr_lower:
        fields.add("low")
    if "open" in expr_lower:
        fields.add("open")
    if "vwap" in expr_lower:
        fields.add("vwap")
    return sorted(fields)


def _estimate_lookback(expression: str) -> int:
    """从表达式中的 d 参数估算最大回看窗口。"""
    import re
    lookbacks = re.findall(r'\b(\d{1,3})\b', expression)
    ints = [int(x) for x in lookbacks if 2 <= int(x) <= 252]
    if not ints:
        return 20
    return max(ints) + 5


def make_factor_program(
    name: str,
    expression: str,
    narrative: str,
    theory: int = 4,
    behavioral: int = 3,
    microstructure: int = 3,
    institutional: int = 3,
    params: dict[str, Any] | None = None,
    lookback: int | None = None,
    input_fields: list[str] | None = None,
    trace_id: str | None = None,
) -> FactorProgram:
    """将因子定义表达式转换为 FactorProgram。

    Args:
        name: 因子名称（如 alpha_001）。
        expression: Python 表达式，使用 alpha_ops 函数操作 close/high/low/open_/volume/vwap/returns。
        narrative: 经济逻辑描述。
        theory/behavioral/microstructure/institutional: 四维评分（0-5）。
        params: 因子参数（默认 {}）。
        lookback: 最小回看窗口（自动估算）。
        input_fields: 输入字段（自动推断）。
        trace_id: 全链路 trace_id。

    Returns:
        FactorProgram — 可注入 SeedPool 的因子程序。
    """
    params = params or {}
    if lookback is None:
        lookback = _estimate_lookback(expression)
    if input_fields is None:
        input_fields = _estimate_input_fields(expression)

    code = _FACTOR_CODE_TEMPLATE.format(
        name=name,
        narrative=narrative,
        alpha_ops=_ALPHA_OPS_SOURCE,
        expression=expression,
    )

    signature = FactorSignature(
        input_fields=input_fields,
        output_type="signal",
        frequency="daily",
        lookback=lookback,
    )

    economic_logic = EconomicLogic(
        theory=theory,
        behavioral=behavioral,
        microstructure=microstructure,
        institutional=institutional,
        narrative=narrative,
    )

    return create_factor_program(
        name=name,
        code=code,
        params=params,
        signature=signature,
        economic_logic=economic_logic,
        source="seed",
        parent_id=None,
        generation=0,
        trace_id=trace_id,
    )


# ─── 批量加载器 ───────────────────────────────────────────

def _load_definitions(
    definitions: list[dict[str, Any]],
    trace_id: str | None = None,
) -> list[FactorProgram]:
    """批量加载因子定义列表。"""
    result: list[FactorProgram] = []
    for i, defn in enumerate(definitions):
        fp = make_factor_program(
            name=defn["name"],
            expression=defn["expression"],
            narrative=defn.get("narrative", "外部因子"),
            theory=defn.get("theory", _tier_from_i(i)),
            behavioral=defn.get("behavioral", 3),
            microstructure=defn.get("microstructure", 3),
            institutional=defn.get("institutional", 3),
            params=defn.get("params", {}),
            lookback=defn.get("lookback"),
            input_fields=defn.get("input_fields"),
            trace_id=trace_id,
        )
        result.append(fp)
    return result


def load_wq101_seeds(trace_id: str | None = None) -> list[FactorProgram]:
    """加载 WQ 101 Alpha 因子种子。"""
    from .wq101 import WQ101_DEFINITIONS
    return _load_definitions(WQ101_DEFINITIONS, trace_id)


def load_qlib158_seeds(trace_id: str | None = None) -> list[FactorProgram]:
    """加载 Qlib 158 因子种子。"""
    from .qlib158 import QLIB158_DEFINITIONS
    return _load_definitions(QLIB158_DEFINITIONS, trace_id)


def load_all_external_seeds(trace_id: str | None = None) -> list[FactorProgram]:
    """加载所有外部种子因子。"""
    return load_wq101_seeds(trace_id) + load_qlib158_seeds(trace_id)


def get_external_seed_count() -> tuple[int, int, int]:
    """返回 (wq101_count, qlib158_count, total_count)。"""
    from .wq101 import WQ101_DEFINITIONS
    from .qlib158 import QLIB158_DEFINITIONS
    return (len(WQ101_DEFINITIONS), len(QLIB158_DEFINITIONS),
            len(WQ101_DEFINITIONS) + len(QLIB158_DEFINITIONS))