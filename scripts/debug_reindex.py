"""Debug the reindex issue"""
import numpy as np
import pandas as pd
from fts.factor_engine.contracts import FactorProgram, FactorSignature, EconomicLogic
from fts.factor_engine.factor_program import FactorExecutor

rng = np.random.default_rng(42)
n_varieties = 3
n_dates = 60
panel = {}
for i in range(n_varieties):
    closes = 100 + np.cumsum(rng.standard_normal(n_dates) * 0.5)
    df = pd.DataFrame({
        "close": closes,
    })
    df.index = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    panel[f"V{i}"] = df

# dates
common_dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
print(f"common_dates type: {type(common_dates)}, len={len(common_dates)}")
print(f"common_dates[:3]: {common_dates[:3]}")

# 执行因子
code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
f1 = FactorProgram(
    factor_id="id_1", name="test", code=code, params={},
    signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
    economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="A"),
    source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
)

executor = FactorExecutor(f1)

# 逐品种执行
for vname, df in panel.items():
    sig = executor.execute(df, {})
    print(f"\n{vname}: sig len={len(sig)}")
    sig_series = pd.Series(sig, index=df.index)
    print(f"  sig_series type: {type(sig_series)}, index type: {type(sig_series.index)}")
    print(f"  sig_series.index[:3]: {sig_series.index[:3]}")
    
    # reindex
    aligned = sig_series.reindex(common_dates)
    print(f"  aligned type: {type(aligned)}, len={len(aligned)}")
    print(f"  aligned[:3]: {aligned[:3]}")
    print(f"  has nan: {aligned.isna().any()}")
    
    if aligned.isna().any():
        # 检查索引是否匹配
        print(f"  Index comparison:")
        print(f"    sig_series.index[:3]: {list(sig_series.index[:3])}")
        print(f"    common_dates[:3]: {list(common_dates[:3])}")
        print(f"    Index equal: {sig_series.index.equals(common_dates)}")
        print(f"    Index dtype: sig={sig_series.index.dtype}, common={common_dates.dtype}")
