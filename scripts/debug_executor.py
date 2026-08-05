"""Debug executor"""
import numpy as np
import pandas as pd
from fts.factor_engine.contracts import FactorProgram, FactorSignature, EconomicLogic
from fts.factor_engine.factor_program import FactorExecutor

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

code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
f1 = FactorProgram(
    factor_id="id_1", name="test", code=code, params={},
    signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
    economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="A"),
    source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
)

executor = FactorExecutor(f1)
sig = executor.execute(df, {})
print(f"Type: {type(sig)}")
print(f"Len: {len(sig)}")
print(f"First 5: {sig[:5]}")
print(f"Std: {np.std(sig)}")
print(f"Has nan: {np.any(np.isnan(sig))}")

# 也试试直接调用
try:
    sig2 = f1.execute(df, {})
    print(f"\nf1.execute: len={len(sig2)}, first5={sig2[:5]}")
except Exception as e:
    print(f"\nf1.execute error: {e}")
