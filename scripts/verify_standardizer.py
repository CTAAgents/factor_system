"""验证标准化模块 — 6 种标准化方法功能正确性。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from fts.factor_engine.standardizer import (
    Standardizer, standardize, SUPPORTED_METHODS,
    StandardizerConfig, StandardizeMethod,
)

np.random.seed(42)

# 生成测试数据
raw_1d = np.random.randn(100) * 2 + 1  # mean=1, std=2
raw_2d = np.random.randn(100, 5) * 2 + 3  # 5 factors, 100 obs

errors = []


def check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        errors.append(f"{name}: {detail}")


# ─── 1. 模块导入检查 ──────────────────────────────────────
print("=== 1. 模块导入 ===")
check("Standardizer 可导入", Standardizer is not None)
check("standardize 函数可导入", callable(standardize))
check("SUPPORTED_METHODS 包含 6 种方法", set(SUPPORTED_METHODS) == {
    "zscore", "rank", "quantile", "minmax", "winsorize_then_zscore", "none"
})
check("StandardizerConfig 可实例化", StandardizerConfig() is not None)

# ─── 2. zscore 测试 ────────────────────────────────────────
print("\n=== 2. zscore ===")
result = standardize(raw_1d, "zscore")
check("zscore 均值≈0", abs(np.mean(result)) < 0.1)
check("zscore 标准差≈1", abs(np.std(result) - 1.0) < 0.1)

result_clip = standardize(raw_1d, "zscore", clip=2.0)
check("zscore clip 上限", np.max(result_clip) <= 2.0)
check("zscore clip 下限", np.min(result_clip) >= -2.0)

# fit/transform 模式
std = Standardizer("zscore")
std.fit(raw_1d)
result_ft = std.transform(raw_1d)
check("zscore fit_transform 一致性", np.allclose(result, result_ft))

# 2D 截面
result_2d = standardize(raw_2d, "zscore", axis=0)
check("zscore 2D 截面均值≈0", np.all(np.abs(np.mean(result_2d, axis=0)) < 0.1))

# NaN 处理
data_nan = raw_1d.copy()
data_nan[0] = np.nan
result_nan = standardize(data_nan, "zscore", skipna=True)
check("zscore NaN 置零", result_nan[0] == 0.0 and not np.isnan(result_nan).any())

# ─── 3. rank 测试 ─────────────────────────────────────────
print("\n=== 3. rank ===")
result = standardize(raw_1d, "rank")
check("rank 范围 [0, 1]", 0 <= np.min(result) <= 1 and 0 <= np.max(result) <= 1)
check("rank 最大值≈1", abs(np.max(result[~np.isnan(raw_1d)]) - 1.0) < 0.01)
check("rank 最小值≈1/N", abs(np.min(result[~np.isnan(raw_1d)]) - 1.0/len(raw_1d)) < 0.01)

# 2D 截面 rank
result_2d = standardize(raw_2d, "rank", axis=0)
check("rank 2D 截面范围", np.all(result_2d >= 0) and np.all(result_2d <= 1))

# NaN 处理
data_nan = raw_1d.copy()
data_nan[0] = np.nan
result_nan = standardize(data_nan, "rank", skipna=True)
check("rank NaN 置零", result_nan[0] == 0.0)

# ─── 4. quantile 测试 ─────────────────────────────────────
print("\n=== 4. quantile ===")
result = standardize(raw_1d, "quantile")
check("quantile 范围 [0, 1]", 0 <= np.min(result) <= 1 and 0 <= np.max(result) <= 1)
check("quantile 最大值=1", abs(np.max(result[~np.isnan(raw_1d)]) - 1.0) < 0.01)
check("quantile 最小值=0", abs(np.min(result[~np.isnan(raw_1d)]) - 0.0) < 0.01)

# 2D
result_2d = standardize(raw_2d, "quantile", axis=0)
check("quantile 2D 截面范围", np.all(result_2d >= 0) and np.all(result_2d <= 1))

# ─── 5. minmax 测试 ───────────────────────────────────────
print("\n=== 5. minmax ===")
result = standardize(raw_1d, "minmax")
check("minmax 范围 [0, 1]", 0 <= np.min(result) <= 1 and 0 <= np.max(result) <= 1)
check("minmax 最大值≈1", abs(np.max(result[~np.isnan(raw_1d)]) - 1.0) < 0.01)
check("minmax 最小值≈0", abs(np.min(result[~np.isnan(raw_1d)]) - 0.0) < 0.01)

# fit/transform
std = Standardizer("minmax")
std.fit(raw_1d)
result_ft = std.transform(raw_1d)
check("minmax fit_transform 一致性", np.allclose(result, result_ft))

# ─── 6. winsorize_then_zscore 测试 ─────────────────────────
print("\n=== 6. winsorize_then_zscore ===")
# 构造含极端值的数据
data_extreme = raw_1d.copy()
data_extreme[0] = 100.0
data_extreme[1] = -100.0
result = standardize(data_extreme, "winsorize_then_zscore", clip=3.0)
check("winsorize 后均值≈0", abs(np.mean(result[2:])) < 0.5)
check("winsorize 后 clip 上限", np.max(result) <= 3.0)
check("winsorize 后 clip 下限", np.min(result) >= -3.0)
# 验证极端值已被缩尾（极端值位置的 zscore 应在合理范围）
check("winsorize 极端值处理", abs(result[0]) <= 3.0 and abs(result[1]) <= 3.0)

# ─── 7. none 测试 ─────────────────────────────────────────
print("\n=== 7. none ===")
result = standardize(raw_1d, "none")
check("none 原值返回", np.allclose(result, raw_1d, equal_nan=True))

# ─── 8. Standardizer 类测试 ────────────────────────────────
print("\n=== 8. Standardizer 类 ===")
std = Standardizer("zscore", clip=3.0)
check("Standardizer 初始化", std.method == "zscore")
check("Standardizer 未拟合", not std.is_fitted)
std.fit(raw_1d)
check("Standardizer 已拟合", std.is_fitted)
check("Standardizer config 可访问", std.config.clip == 3.0)

# 无效方法
try:
    Standardizer("invalid")
    check("无效方法应抛异常", False, "未抛异常")
except ValueError:
    check("无效方法抛 ValueError", True)

# ─── 9. 边界情况 ──────────────────────────────────────────
print("\n=== 9. 边界情况 ===")
# 空数组
result = standardize(np.array([]), "zscore")
check("空数组 zscore", len(result) == 0)

# 全相同值
result = standardize(np.array([5.0, 5.0, 5.0]), "zscore")
check("全相同值 zscore=0", np.allclose(result, [0, 0, 0]))

# 单值
result = standardize(np.array([3.0]), "zscore")
check("单值 zscore=0", np.allclose(result, [0]))

# 全 NaN
result = standardize(np.array([np.nan, np.nan]), "zscore", skipna=True)
check("全 NaN zscore=0", np.all(result == 0))

# ─── 10. 从 __init__ 导入测试 ──────────────────────────────
print("\n=== 10. __init__ 导出 ===")
from fts.factor_engine import Standardizer as Std2, standardize as std2, SUPPORTED_METHODS as sm2
check("从 factor_engine 导入 Standardizer", Std2 is not None)
check("从 factor_engine 导入 standardize", callable(std2))
check("从 factor_engine 导入 SUPPORTED_METHODS", sm2 == SUPPORTED_METHODS)

# ─── 结果汇总 ──────────────────────────────────────────────
print(f"\n{'='*50}")
if errors:
    print(f"FAILED: {len(errors)} 项测试失败")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
    sys.exit(0)