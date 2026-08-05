"""Debug: 横截面相关性为什么检测不到相同信号"""
import numpy as np
import pandas as pd
from fts.factor_engine.contracts import FactorProgram, FactorSignature, EconomicLogic
from fts.factor_engine.factor_program import FactorExecutor
from fts.factor_engine.seed_pool import compute_cross_section_correlations

# 1. 创建面板数据
rng = np.random.default_rng(42)
n_varieties = 10
n_dates = 60
panel = {}
for i in range(n_varieties):
    closes = 100 + np.cumsum(rng.standard_normal(n_dates) * 0.5)
    panel[f"V{i}"] = pd.DataFrame({
        "open": closes - np.abs(rng.normal(0, 0.5, n_dates)),
        "high": closes + np.abs(rng.normal(0, 1.0, n_dates)),
        "low": closes - np.abs(rng.normal(0, 1.0, n_dates)),
        "close": closes,
        "volume": rng.integers(1000, 10000, n_dates),
    })
dates = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=n_dates, freq="B"))

# 2. 创建两个相同因子
code = "def factor_program(data, params):\n    import numpy as np\n    return np.array(data['close'])"
f1 = FactorProgram(
    factor_id="id_1", name="test_a", code=code, params={},
    signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
    economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="A"),
    source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
)
f2 = FactorProgram(
    factor_id="id_2", name="test_b", code=code, params={},
    signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
    economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="B"),
    source="seed", generation=0, created_at="2026-08-01T00:00:00", trace_id="test",
)

# 3. 检查每个因子的执行结果
for label, f in [("f1", f1), ("f2", f2)]:
    executor = FactorExecutor(f)
    params = f.get("params", {})
    n_valid = 0
    sig_matrix = np.zeros((n_dates, n_varieties))
    for j, (vname, df) in enumerate(panel.items()):
        try:
            sig = executor.execute(df, params)
            if len(sig) > 0:
                sig_series = pd.Series(sig, index=df.index)
                aligned = sig_series.reindex(dates).values
                if len(aligned) == n_dates:
                    sig_matrix[:, j] = aligned
                    n_valid += 1
        except Exception as e:
            print(f"  {label}/{vname}: ERROR {e}")
    print(f"{label}: {n_valid}/{n_varieties} varieties, total std={np.std(sig_matrix):.4f}")
    print(f"  Sig matrix first row (date 0): {sig_matrix[0, :]}")
    print(f"  Sig matrix std per variety: {[f'{np.std(sig_matrix[:, j]):.4f}' for j in range(n_varieties)]}")

# 4. 手动计算截面 Spearman
from scipy.stats import rankdata, spearmanr

# 使用 f1 的信号
executor = FactorExecutor(f1)
sig1_matrix = np.zeros((n_dates, n_varieties))
for j, (vname, df) in enumerate(panel.items()):
    sig = executor.execute(df, {})
    sig_series = pd.Series(sig, index=df.index)
    sig1_matrix[:, j] = sig_series.reindex(dates).values

# f2 的信号（相同代码）
executor2 = FactorExecutor(f2)
sig2_matrix = np.zeros((n_dates, n_varieties))
for j, (vname, df) in enumerate(panel.items()):
    sig = executor2.execute(df, {})
    sig_series = pd.Series(sig, index=df.index)
    sig2_matrix[:, j] = sig_series.reindex(dates).values

# 计算每期截面排名的 Spearman
corrs = []
for t in range(n_dates):
    r1 = rankdata(sig1_matrix[t, :])
    r2 = rankdata(sig2_matrix[t, :])
    c, _ = spearmanr(r1, r2)
    corrs.append(c)

print(f"\n每期截面 Spearman: min={min(corrs):.4f}, max={max(corrs):.4f}, mean={np.mean(corrs):.4f}")

# 5. 调用正式函数
result = compute_cross_section_correlations([f1, f2], panel, dates, threshold=0.95)
print(f"\n正式函数结果: {len(result)} pairs")
if result:
    for r in result:
        print(f"  {r}")
