# 宏观字段增强层接入实施计划（Phase 21 + 21b / v2.33.0）

> 版本: v2.104.0+69
> 最后更新: 2026-08-08
> 状态: 已完成（宏观注入 v2.32.0 完成；降级/重设计/边界审计 v2.33.0 完成）
> 适用范围: FTS 数据层 + 因子执行链路

---

## 0. 背景与目标

### 0.1 现状

`fut_macro_export` / `fut_macro_export_g4/g10/g11/g13` 等宏观因子依赖
`data.get('export')` / `data.get('import_data')` 字段，但 K 线主路径
（`get_ohlcv` / `get_minute_ohlcv`）返回的 DataFrame **不含任何宏观列**，
因子执行时走到 close 代理降级分支，宏观语义完全丢失。

已具备的基础设施：
- `IFindSource.fetch_edb()`（`fts/data_sources/ifind_source.py`）已实现，
  通过 `get_edb_data` MCP 工具拉取 EDB 宏观/行业指标。
- `edb_cache` 表 DDL 已建（`fts/data_sources/migrate.py`），
  但 **fetch_edb 不落库、无缓存读取**。
- `FuturesDataAggregator._enhance_fields()` 硬编码 settle/oi_change/hold，
  不处理宏观字段。

### 0.2 目标

1. **EDB 缓存读写**：`IFindSource.get_macro_series()` 查 `edb_cache` →
   miss 调 `fetch_edb` → 幂等写回。
2. **时序对齐器**：新增 `fts/data_sources/macro_aligner.py`，
   将月度宏观序列按交易日 ffill 对齐到 K 线 index，支持发布滞后防未来函数。
3. **执行链路注入**：`backtest_pipeline` 因子执行前注入宏观列，
   使 `data.get('export')` 能读到真实宏观数据。
4. **配置开关**：`Settings` 新增宏观字段注入开关与滞后天数。

### 0.3 不在范围

- 宏观因子本身的质量评估/审计
- 宏观数据的人工录入与维护
- 实时宏观数据订阅

---

## 1. 契约设计

### 1.1 指标映射表（`MacroFieldAligner.MACRO_FIELD_QUERIES`）

```python
MACRO_FIELD_QUERIES: dict[str, str] = {
    "export":      "中国出口金额当月值",
    "import_data": "中国进口金额当月值",
    "cpi":         "中国CPI当月同比",
    "rate":        "中国1年期国债收益率",
    "us_bond":     "美国10年期国债收益率",
}
```

### 1.2 对齐器接口

```python
def align(
    df: pd.DataFrame,
    macro: pd.Series,
    field: str,
    lag_days: int = 0,
) -> pd.DataFrame:
    """将宏观月度序列对齐到 K 线 index 并注入为列。

    Args:
        df: OHLCV DataFrame（DatetimeIndex）
        macro: DatetimeIndex Series（date → value）
        field: 注入列名（如 "export"）
        lag_days: 发布滞后天数（防未来函数，默认 0）

    Returns:
        注入 macro 列后的 df 副本
    """
```

### 1.3 注入函数（执行链路入口）

```python
def inject_macro_fields(
    df: pd.DataFrame,
    aligner: MacroFieldAligner,
    fields: list[str] | None = None,
    trace_id: str = "",
) -> pd.DataFrame:
    """批量注入宏观字段。某字段失败 → 保留缺失列（因子走代理），不阻断主路径。"""
```

---

## 2. 模块变更清单

| 文件 | 变更内容 |
|:----|:---------|
| `fts/data_sources/ifind_source.py` | 新增 `get_macro_series()`（edb_cache 查 + 写） |
| `fts/data_sources/macro_aligner.py` | 新增：`MACRO_FIELD_QUERIES` + `MacroFieldAligner.align()` + `inject_macro_fields()` |
| `fts/factor_engine/backtest_pipeline.py` | 因子执行前调用 `inject_macro_fields` |
| `fts/config/settings.py` | 新增 `macro_field_injection` / `macro_lag_days` 配置 |
| `tests/data_sources/test_macro_aligner.py` | 对齐/滞后/降级测试 |

---

## 3. 执行计划

### Step 1: 契约 + 缓存读写
- `IFindSource.get_macro_series()`：查 `edb_cache`（indicator + date 区间）→
  miss 调 `fetch_edb` → INSERT（幂等 upsert）。

### Step 2: 对齐器实现
- `MacroFieldAligner.align()`：月度序列重采样 ffill 到交易日，
  `lag_days` 滞后（报告期 → 发布日期平移）。

### Step 3: 执行链路注入
- `backtest_pipeline` 中因子计算前调用 `inject_macro_fields`，
  注入失败不阻断（catch 后保留原 df）。

### Step 4: 测试 + 文档
- 单元测试（对齐/滞后/缺数据降级/缓存读写）
- 更新 06-testing / 07-operations / 02-lifecycle

---

## 3.1 宏观因子降级与适用场景重设计（Phase 21b / v2.33.0）

### 3.1.1 背景：代理模式假象

真实 EDB 数据对比（`reports/2026-08-08/macro_export_g4_real_edb_compare.md`）证实：

| 模式 | Sharpe | IC | 结论 |
|:-----|:------|:---|:-----|
| close 代理（历史默认） | 7.68 | 0.4147 | 假象：close 代理使宏观因子退化为价格动量 |
| 真实 EDB 无滞后 | -0.46 | -0.01 | 未来函数泄漏被移除后失效 |
| 真实 EDB 滞后 30 天 | 0.84 | 0.0301 | 发布滞后后 IC≈0，无稳健预测力 |

结论：**fut_macro_export 家族因子在单品种（RB0）时序上不具备真实预测力**，
历史 Sharpe 高企源于 close 代理（退化为动量因子）与宏观发布滞后未处理。

### 3.1.2 降级范围（精英池 → 淘汰）

> 状态：✅ 已执行（2026-08-08，v2.33.0）。6 因子 DuckDB 状态全部 retired，5 个 JSON 已归档至 `_retired/`。

| factor_id | 名称 | 处理 |
|:----------|:-----|:-----|
| `fct_01f132dc` | fut_macro_export | retire ✅ |
| `fct_5d783863` | fut_macro_export_g4 | retire ✅（同时含 np.bincount 漏洞，随 retire 消除） |
| `fct_e10560e2` | fut_macro_export_g10 | retire ✅ |
| `fct_0591e8e3` | fut_macro_export_g11 | retire ✅ |
| `fct_1fad8dfc` | fut_macro_export_g13 | retire ✅ |
| `fct_2bcd330b` | fut_macro_export（重复无 JSON） | DuckDB 状态更新 ✅ |

执行方式：`FactorRepository.retire_factor(factor_id, reason, elite_dir)`，
reason 统一为 `"宏观代理假象：真实EDB数据IC≈0，单品种时序无预测力"`。

### 3.1.3 角色边界重设计：宏观因子的适用场景

**角色边界原则（§5.6）**：FTS 专注因子发现/评估/组合/演化，宏观数据属于
Data-Core 职责；宏观因子只应在**跨品种/板块层面**评估，不应在单品种时序上
直接产生交易信号。

重设计后的适用场景（后续单独立项，不在本次执行范围内）：

| 场景 | 载体 | 用途 |
|:-----|:-----|:-----|
| 板块/产业链 regime 选择 | SectorRegimeSelector（跨品种截面） | 宏观状态决定板块敞口而非单品种信号 |
| 组合层面的风险预算 | 宏观风险因子（组合归因） | 利率/汇率/流动性对组合整体 beta 的贡献 |
| 跨市场泛化验证 | `fts/cross_market/` | 宏观因子在 futures→ETF 方向的通用性研究 |

**约束**：宏观因子不得进入单品种时序回测/信号管道；宏观数据注入层
（§1-§3 已实现）保留，作为跨品种/组合层面的数据供给，不作为单品种因子输入。

### 3.1.4 np.bincount 输入边界漏洞审计（4 因子）

> 状态：✅ 已执行（2026-08-08，v2.33.0）。审计发现仅 `fct_5d783863` 真实崩溃
> （NaN→astype(int)→负整数→bincount 抛异常→静默返回全零），已随 retire 消除；
> 其余 3 因子 bincount 输入有 digitize/clip 隐式防护不崩溃，但存在 NaN 传播导致
> 输出非有限，已做入口 NaN 清理 + bincount 输入防御，新增 9 测试用例全绿。

**漏洞**：`np.bincount(x.astype(int))` 在 x 含 NaN 时产生极大负整数 →
`'list' argument must have no negative elements` → 异常被顶层 catch 捕获后
返回全零（静默失败）。

| factor_id | 名称 | 触发位置 | 处理 |
|:----------|:-----|:---------|:-----|
| `fct_5d783863` | fut_macro_export_g4 | state 熵计算 | 随家族 retire ✅ |
| `fct_70d783d1` | fut_bias_g17 | seg 分桶 | 入口 NaN 清理 + bincount 防御 ✅ |
| `fct_71372ef2` | seed_spread_g19 | states 分桶 | 入口 NaN 清理 + bincount 防御 ✅ |
| `fct_7b251afa` | fut_basis_momentum_g20 | bins/local_bins | 入口 NaN 清理 + bincount 防御 ✅ |

**修复方式**：对 bincount 输入先 `np.nan_to_num` + `np.clip(x, 0, None)` 再
`.astype(int)`，保证非负；入口 close NaN 用首个有效值填充阻断传播。修复后
重新回测（RB0，500 日）验证指标正常：fut_bias_g17 Sharpe 0.32/IC 0.197、
seed_spread_g19 Sharpe 0.07/IC 0.072、fut_basis_momentum_g20 Sharpe 0.24。✅

**同步扩展**：同一入口 NaN 防护逻辑同步到活跃池 g10/g11/g13 同族因子
（fut_hf_trade_imbalance_g10 / fut_bias_g11 / fut_option_pcr_g10 /
fut_gp_alpha1_g13）——其代码不含 np.bincount，但同样存在 NaN 传播导致
输出非有限的风险；统一在入口对 close/volume/high/low 做 NaN 首个有效值
填充。新增 12 测试用例（4 因子 × 3：NaN 输入有限输出/确定性/防护存在），
RB0 回测正常（fut_bias_g11 Sharpe 10.23/IC 0.584、fut_hf_trade_imbalance_g10
Sharpe 3.70/IC 0.171、fut_option_pcr_g10 Sharpe -0.46、fut_gp_alpha1_g13
Sharpe -3.39，与防护前一致）。✅

---

## 4. 反模式检查

| 检查项 | 状态 |
|:------|:-----|
| AP01 巨型 Prompt | 通过（本计划 < 300 行） |
| AP02 跳过审核 | 通过（v2.32.0 与 v2.33.0 均已实现并验证） |
| AP06 无独立验证 | 通过（Step 4 测试 + bincount 边界 9 用例 + repository 事务 3 用例） |
| AP10 一个 PR 改所有 | 7 文件 + 2 新增测试，< 20 文件 |

---

## 5. 一致性元数据

| 代码映射 | 可验证断言 | 检验方式 |
|:--------|:----------|:--------|
| `fts/data_sources/macro_aligner.py` → §1.2 | `align()` 按交易日对齐且支持 lag_days | `pytest tests/data_sources/test_macro_aligner.py` |
| `fts/data_sources/ifind_source.py` → §1.1 | `get_macro_series()` 走 edb_cache 缓存 | 同上 |
| `fts/factor_engine/backtest_pipeline.py` → §2 | 因子执行前注入宏观列 | `pytest tests/factor_engine/test_backtest_pipeline.py` |
| `docs/harness/plans/18-macro-field-enhancement-plan.md` → 本文件 | 计划与实现一致 | `python scripts/verify_doc_consistency.py` |
