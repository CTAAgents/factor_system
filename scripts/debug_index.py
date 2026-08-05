"""Debug: what if df has no date index?"""
import numpy as np
import pandas as pd
from fts.factor_engine.contracts import FactorProgram, FactorSignature, EconomicLogic
from fts.factor_engine.factor_program import FactorExecutor

# 模拟 debug_cs_corr.py 中的数据创建方式 — 没有设置 index！
rng = np.random.default_rng(42)
n_dates = 60
closes = 100 + np.cumsum(rng.standard_normal(n_dates) * 0.5)
df = pd.DataFrame({
    "open": closes - np.abs(rng.normal(0, 0.5, n_dates)),
    "high": closes + np.abs(rng.normal(0, 1.0, n_dates)),
    "low": closes - np.abs(rng.normal(0, 1.0, n_dates)),
    "close": closes,
    "volume": rng.integers(1000, 10000, n_dates),
})
# 注意: df.index 是 RangeIndex(0..59)，不是 DatetimeIndex！
print(f"df.index type: {type(df.index)}")
print(f"df.index[:3]: {df.index[:3]}")

code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
f1 = FactorProgram(
    factor_id="id_1", name="test", code=code, params={},
    signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
    economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="A"),
    source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
)

executor = FactorExecutor(f1)
sig = executor.execute(df, {})
print(f"sig len: {len(sig)}, first3: {sig[:3]}")

sig_series = pd.Series(sig, index=df.index)
print(f"sig_series index type: {type(sig_series.index)}")

common_dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
aligned = sig_series.reindex(common_dates)
print(f"aligned len: {len(aligned)}, has nan: {aligned.isna().any()}")
# 因为 index 不匹配 (RangeIndex vs DatetimeIndex)，reindex 会产生全 NaN！
