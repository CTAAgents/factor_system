# 27 号计划 — 期货持仓/结算数据源接入（GAP-083 实施规划）

> 版本: v2.104.0+66（规划版，实施后不 bump）
> 最后更新: 2026-08-11
> 关联: GAP-083（期货 hold/settle TDX 主路径恒 NA）、GAP-085（监控字段错位）、GAP-088（宏观注入链路，不覆盖）
> 状态: ✅ 已实施（2026-08-11，A+B+C+D 四阶段落地，GAP-083/085 关闭，§11）

---

## 1. 背景与目标

数据字段字典审计（2026-08-11）确认：**期货持仓量 `hold` 与结算价 `settle` 在主路径 TDX_LOCAL(17709) 恒为 pd.NA**（通达信 TQ 不返回），kline_cache 直连回退路径用代理值（hold=volume 20 日均量、settle=典型价），`oi_change`（日增仓）无真实数据源。持仓/结算类因子（期限结构/持仓变化/基差/拥挤度）在主路径退化为代理或 NaN，是 FTS 与头部机构差距的 **P0 阻塞项**（GAP-083）。

**目标**：为 `hold`/`settle`（+可选 `pre_settle`/`oi_change`/`amount` 输出）接入真实数据，实现"真实优先、代理兜底"，零外部依赖阶段先落地，外部依赖（iFinD/Wind/TQSDK）阶段可选。

---

## 2. 现状分析（2026-08-11 调研结论）

### 2.1 kline_cache 库内真实覆盖（实查）

| 项 | 值 | 说明 |
|:---|:---|:-----|
| 总行数（period='daily'） | 367,973 | — |
| hold/settle 有值行 | 156,586（42.5%） | **全部为 TQ 15 年同步写入的占位 0.0**（`_safe_float` 兜底 NaN→0.0），hold>0 仅 0.8% |
| amount 有值行 | 367,973（100%） | 全量 |
| symbol 双格式 | `RB0`（TQ 同步，hold=0.0 占位）vs `RB`（AKShare 历史，hold=NULL） | **`_from_kline_cache` 查询 key 标准化 `RB0→RB`，命中 `RB` 格式 → hold 全 NULL → 恒走代理；`RB0` 格式（TQ 15 年）可能未被主查询命中** |

### 2.2 代码路径

| 路径 | hold/settle 行为 | 修复点 |
|:-----|:-----------------|:-------|
| `_from_kline_cache`（data_futures.py L995-1048） | 查询 SQL **不选 hold/settle 列** → 强制代理（L1044-1045） | ① SELECT 增加 hold/settle；② 真实值优先（>0/非 NULL）、无效走代理 |
| Aggregator → TDX_LOCAL（tdx_local_source.py L457-459） | hold/settle/pre_settle/oi_change 恒补 pd.NA | 无法从此源获取（通达信限制），需增强层/回填 |
| `_from_akshare`（data_futures.py L1092-1154） | **返回真实 hold/settle**（futures_zh_daily_sina） | ✅ 可用作回填源，零外部依赖 |
| 字段增强层 `_enhance_fields`（aggregator.py L561-594） | 已实现 settle/pre_settle/oi_change/hold 覆盖逻辑 | `enhancers=[]` 未启用 → 需 iFinD/Wind 认证 |
| `_from_tq_local` 直连 | hold/settle 恒 NA | 无 |
| SYNTHETIC | 随机假值 | 数据红线，不用于真实评估 |

### 2.3 数据源能力矩阵

| 数据源 | hold（持仓量） | settle（结算价） | pre_settle | oi_change | 外部依赖 |
|:-------|:---------------|:-----------------|:-----------|:----------|:---------|
| AKShare `futures_zh_daily_sina` | ✅ 真实 | ✅ 真实 | ❌ | ❌ | 无（已有依赖） |
| kline_cache 库内（历史） | ⚠️ 0.0 占位/NULL | ⚠️ 同左 | ⚠️ | ⚠️ | 无 |
| iFinD/Wind MCP | ✅ 真实 | ✅ 真实 | ✅ | ✅ | API Key 认证 |
| TQSDK 天勤日线 | ✅ open_interest | ⚠️ | ⚠️ | ❌ | tqsdk token |

---

## 3. 方案设计（分阶段，全部可独立交付）

### 3.1 阶段 A：读路径修复（零外部依赖，核心）

**目标**：`_from_kline_cache` 真实优先、代理兜底。

- 查询 SQL 增加 `hold`/`settle` 列（kline_cache 表已有 16/17 列 schema）：
  ```sql
  SELECT date, open, high, low, close, volume, amount,
         hold, settle,
         CASE WHEN amount > 0 AND volume > 0 THEN amount / volume
              ELSE (high + low + close) / 3.0 END AS vwap
  FROM kline_cache WHERE symbol = ? AND period = 'daily'
  ORDER BY date DESC LIMIT ?
  ```
- 读取后有效性判定：`settle` 无效（NULL 或 ≤0，0 为 TQ 占位）→ 代理 `(H+L+C)/3`；`hold` 无效（NULL 或 ≤0）→ 代理 volume 20 日均量；真实值原样保留。
- **symbol 双格式确认**（调研发现）：`_from_kline_cache` 查询 key 标准化 `RB0→RB`，TQ 15 年同步写入的是 `RB0` 格式。需核对主路径实际命中的记录（阶段 A 前置检查）：若 `RB` 记录缺失/过期，需与 sync 数据对齐（详见风险 R3）。

### 3.2 阶段 B：AKShare 历史回填（零外部依赖，脚本）

**目标**：把库内 0.0/NULL 占位的 hold/settle 用 AKShare 真实值回填，一次性提升历史覆盖率。

- 新脚本 `scripts/backfill_futures_hold.py`：
  - 遍历品种（默认 `FUTURES_SUBSET` 全量），`ak.futures_zh_daily_sina(symbol)` 拉取真实 hold/settle
  - 按 date 对齐 `UPDATE kline_cache SET hold=?, settle=? WHERE symbol=? AND period='daily' AND date=?`（幂等，`--dry-run` 支持）
  - 写入 key 与 `_from_kline_cache` 查询 key 一致（`RB` 格式；若阶段 A 确认主路径读 `RB0`，则回填 `RB0`——两个格式都回填更稳妥）
  - 限速（0.5s/次防封）+ trace_id + 异常品种跳过降级
- 可选：扩展 `sync_futures_data_job` 写入时保留 hold/settle（长期维护）。

### 3.3 阶段 C：外部增强层（可选，需认证）

**目标**：补齐 `pre_settle`/`oi_change` + 权威持仓/结算，并实时维护。

- 启用 aggregator `enhancers`：注册 iFinD/Wind 增强源（`enhancers=[...]`），`_enhance_fields` 已实现覆盖逻辑（settle/pre_settle/oi_change/hold）
- 依赖：iFinD/Wind MCP API Key（需 `RequestAuthorization` 授权）
- **决策门 C1**：认证可用才实施；不可用则维持阶段 A/B 结果，oi_change 由 hold 差分推导（见 3.4）

### 3.4 阶段 D：配套修复

1. **GAP-085**：`data_level_monitor.key_fields` 由 `open_interest` 修正为 `hold`（+`settle`），恢复持仓量监控真实生效
2. **oi_change 派生**（可选）：`hold.diff()` 作为日增仓派生列，在 `_from_kline_cache`/回填路径输出（无真实源时的工程近似，标注来源）
3. **amount 输出**（可选）：`get_ohlcv` 增加 amount 列输出（库内 100% 有值），供 amount 类因子（vwap 精确计算）消费

---

## 4. 涉及文件

| 文件 | 改动 |
|:-----|:-----|
| `fts/data_futures.py` `_from_kline_cache` | 阶段 A：SELECT + 真实优先/代理兜底 |
| `fts/data_futures.py` `get_ohlcv` | 阶段 D3：amount 列输出（可选） |
| `fts/monitor/data_level_monitor.py` | 阶段 D1：key_fields 修正 |
| `fts/data_futures.py`（或 aggregator） | 阶段 D2：oi_change 派生（可选） |
| `fts/data_sources/aggregator.py` | 阶段 C：enhancers 注册（可选） |
| `scripts/backfill_futures_hold.py`（新建） | 阶段 B：AKShare 回填 |
| 配置 `fts/config/settings.py` | 阶段 A/B 行为开关（如需） |
| 测试 `tests/` | 见 §6 |
| 文档 | 01-architecture / 03-configuration（如需）/ 06-testing / 07-operations / 08-gap-analysis（GAP-083/085 关闭）/ 本计划 / 字段字典 |

---

## 5. 契约与配置（契约先行）

```python
# contracts 无新契约；沿用既有 DataFrame 列 hold/settle（8 列输出不变，仅值质量提升）
# 可选配置（阶段 A/B，默认关闭行为不变）：
#   futures_hold_real_first: bool = True   # _from_kline_cache 真实优先（>0/非 NULL），无效走代理
```

- 输出列契约不变：`get_ohlcv` 仍输出 8 列（open/high/low/close/volume/vwap/hold/settle）+ 可选 adj_factor/overnight_gap；阶段 D3 可追加 amount。
- 数据红线：合成数据不用于真实评估；回填/增强失败降级不阻断主路径。

---

## 6. 测试计划（TDD）

| 测试文件 | 用例要点 |
|:---------|:---------|
| `tests/data/test_data_futures_hold.py`（新增） | 阶段 A：库内 hold/settle 真实值优先 / NULL 走代理 / 0.0 占位走代理 / settle 代理公式 / 无数据 None / 混合真实+缺失行 |
| `tests/scripts/test_backfill_futures_hold.py`（新增） | 阶段 B：AKShare mock 回填幂等 / 日期对齐 / 0.0 覆盖 / --dry-run / 异常品种跳过 / 限速 |
| `tests/monitor/test_data_level_monitor.py`（更新） | 阶段 D1：key_fields hold 生效（原 open_interest 跳过回归） |
| 回归 | `test_data_futures` / `test_aggregator` / `test_fusion` / `test_tdx_local_source` 定向（模块/集成，按分级测试政策不跑全量） |

---

## 7. 验证方式

1. **单元测试**：上述用例全绿（红→绿 TDD）
2. **集成验证**：阶段 A 后抽查 `RB/CU/IF` 等 `get_ohlcv` 输出 hold/settle 覆盖与真实值；阶段 B 回填后全品种覆盖率统计（hold>0 行占比目标显著提升），与 AKShare 源逐日比对
3. **回归**：受影响模块/集成定向回归全绿（不跑全量）
4. **文档一致性**：`verify_doc_consistency.py` 全绿
5. **GAP 关闭**：GAP-083/085 登记关闭，字段字典 §11 缺口表状态更新（hold/settle ⚠️→✅，pre_settle/oi_change 视阶段 C）

---

## 8. 风险与回滚

| 风险 | 控制 |
|:-----|:-----|
| R1：真实 hold 与代理口径切换导致因子信号历史断裂 | 阶段 A 仅读路径（不重写历史因子）；回填前备份；`futures_hold_real_first` 开关可关回退旧代理行为 |
| R2：AKShare 回填限速/接口波动 | 0.5s 限速 + 异常品种跳过 + `--dry-run` 预检 + 幂等可重入 |
| R3：symbol 双格式（RB0 vs RB）导致回填/查询错位 | 阶段 A 前置核对主路径实际命中记录；回填同时写两个格式；若发现 TQ 15 年数据未被读取，单独登记数据链路修复 |
| R4：iFinD/Wind 认证失败 | 阶段 C 为可选，认证不可用则跳过，oi_change 走 hold 差分近似 |
| R5：monitor 字段修正误报 | 先修正 key_fields 再观察告警量；阈值沿用（缺失 5%/20%、异常 1%/5%） |

---

## 9. HARNESS 文档更新清单

| # | 文档 | 更新内容 |
|:--|:-----|:---------|
| 1 | `01-architecture.md` | 期货字段增强链路（回填/增强层状态） |
| 2 | `03-configuration.md` | `futures_hold_real_first`（如新增） |
| 3 | `06-testing.md` | 新测试文件与用例数 |
| 4 | `07-operations.md` | 实施备案 |
| 5 | `08-gap-analysis.md` | GAP-083/085 ✅ 关闭 |
| 6 | `plans/27-...` | 实施记录 + 验证结果 |
| 7 | 字段字典 `futures_factor_fields.md` | §3.2/§11 状态更新 |

---

## 10. 执行顺序

```
阶段 A（读路径修复，零依赖）→ 测试 → 集成验证
   ↓
阶段 B（AKShare 回填脚本）→ 测试 → 覆盖率统计
   ↓
阶段 D（监控修正 + oi_change/amount 可选）
   ↓
阶段 C（iFinD/Wind 增强层，决策门 C1 认证可用才做）→ 测试
   ↓
文档同步 → verify_doc_consistency → GAP-083/085 关闭
```

**建议范围**：本次实施 **阶段 A + 阶段 B + 阶段 D**（零外部依赖、立即闭环 P0）；阶段 C 视 iFinD/Wind 认证情况另行启动。

> 用户确认（2026-08-11）：**A+B+C+D 全做 + 双格式对齐**。

---

## 11. 实施记录与验证结果（2026-08-11）

### 11.1 实施记录

| 阶段 | 落地内容 |
|:-----|:---------|
| **A 读路径修复** | `fts/data_futures.py` `_from_kline_cache`：SQL SELECT 增加 hold/settle 列（kline_cache 表 16/17 列已存在）；双格式对齐（`symbol IN (RB, RB0)`，ORDER BY date DESC + 0 后缀优先 → drop_duplicates 保留 RB0）；真实优先/代理兜底（settle/hold 无效值 NULL 或 ≤0（TQ 0.0 占位）才用代理：settle=(H+L+C)/3、hold=volume 20 日均量）；输出列契约 8 列不变 |
| **B AKShare 回填** | 新脚本 `scripts/backfill_futures_hold.py`：`fetch_hold_settle_from_akshare`（futures_zh_daily_sina 真实 hold/settle，`_safe_float` 兜底）→ `write_backfill`（按日期 UPDATE kline_cache 双格式 RB/RB0，仅更新 hold>0 或 settle>0 行，幂等可重入）→ `backfill_hold_settle`（0.5s 限速/异常品种跳过/dry-run）→ CLI（--symbols/--universe core|all/--dry-run/--json/-v，trace_id 贯穿） |
| **C 增强层接线** | `fts/config/settings.py` 新增 `futures_enhance_enabled`（FTS_FUTURES_ENHANCE_ENABLED 默认 false）；`_init_default_aggregator` 按配置注册 IFindSource/WindSource 为 enhancers（`_enhance_fields` 已有 settle/pre_settle/oi_change/hold 覆盖逻辑），实例化/导入失败降级跳过不阻断；开启需 mcp_enabled=true + set_mcp_handler 认证注入 |
| **D 配套修复** | `fts/monitor/data_level_monitor.py` `key_fields` 修正 `("close","volume","open_interest")` → `("close","volume","hold")`（GAP-085）+ docstring 同步；oi_change 派生/amount 输出保留为后续（避免破坏 8 列输出契约，无明确消费方） |

### 11.2 验证结果

| 验证项 | 结果 |
|:-------|:-----|
| `tests/test_data_futures_hold.py`（新增） | **15/15 passed**（阶段 A 7：真实优先/0 占位代理/NULL 代理/混合/双格式 RB0 优先/空 None/8 列契约 + 阶段 C 注册 4：默认注册 TQSDK/启用注册 TQSDK+iFinD+Wind/实例化失败跳过/导入失败降级 + **有效值覆盖 4**：hold 正数-only/settle 正数-only/oi_change 任意有效/缺列 noop） |
| `tests/scripts/test_backfill_futures_hold.py`（新增） | **13/13 passed**（resolve_symbols 2 + write_backfill 4：双格式/无效跳过/dry-run/空 df + backfill 3：正常/异常跳过/空跳过 + CLI dry-run 1 + **TestFetchHoldSettle 3**：column_alignment 回归/缺列补 0.0/空或缺 date → None） |
| `tests/data_sources/test_tqsdk_enhance_source.py`（新增，阶段 C） | **14/14 passed**（fetch 7：close_oi→hold+差分→oi_change/open_oi 回退/无持仓字段 None/零值→NaN/无账号 None/无映射 None/天勤异常 None + resolve 3：RB0 映射/RB 补 0/未知透传 + availability 4：is_available 三态/fetch_quote None） |
| `tests/monitor/test_data_level_monitor.py`（更新） | **24 passed**（+2：hold 缺失检测 critical / 无 hold 列跳过不误报） |
| 既有 `test_data_futures.py` | 73 passed（TestFromKlineCache mock 适配 11 列） |
| ruff check | 通过 |
| 模块/集成定向回归 | **313 passed**（test_data_futures 73 + hold 15 + tqsdk_enhance 14 + ifind_sdk 20 + backfill 13 + monitor 24 + config 63 + aggregator 89 全过含 tick_cache 修复，未跑全量） |
| 文档一致性 | verify_doc_consistency 通过 |

### 11.3 回填执行与验证（2026-08-11 晚）

| 步骤 | 结果 |
|:-----|:-----|
| **预检（dry-run 全量 82 品种）** | **416,360 行待更新 / 0 跳过 / 0 失败**——期间发现并修复 `fetch_hold_settle_from_akshare` 索引对齐 bug：akshare 返回 RangeIndex 源 DataFrame，`.map()` 赋值到 datetime index 目标列导致全 NaN（预检 0 行）；修复为 `.to_numpy()` 赋值，补 3 个 TestFetchHoldSettle 回归用例 |
| **正式回填（core 25 品种，用户确认缩小范围先验证）** | **167,904 行更新 / 0 跳过 / 0 失败**（trace_id `fts.backfill_hold.2df332b5`，已落库） |
| **正式回填（全量 82 品种）** | **416,360 行更新 / 0 跳过 / 0 失败**（trace_id `fts.backfill_hold.bd92764a`，核心 25 重刷幂等，全量已落库） |
| **落库抽查验证** | 全库 hold 覆盖 **97.9%**（360,174/367,973）、settle **86.6%**（318,616/367,973）；新增品种（V/B/JD/L/PP/FU/AL/ZN）hold 98%+/settle 91~99.7%；JD0 2020-09-15 抽样 hold=185,785/settle=3,374 量级吻合；未覆盖行=股指期货 settle（sina 源限制）+少量品种尾部日期，由读路径代理兜底 |
| **剩余** | iFinD/Wind 增强层可选（`FTS_FUTURES_ENHANCE_ENABLED=true` + MCP 认证）；pre_settle 仍无真实源（天勤不提供结算价） |

### 11.4 阶段 C 实施（2026-08-11 晚，数据源定版为天勤 TQSDK）

**数据源决策**：TRAE iFinD MCP（7 server）与东财 mx 实测均无期货持仓/结算接口（index_data 仅返回申万商品指数点位、具体合约空、mx 综合查询空）→ 定版**天勤 TQSDK**（`.env` 已有账号，free-api 登录成功，K 线含 `close_oi`/`open_oi` 持仓量）。

| 落地项 | 内容 |
|:-------|:-----|
| **TQSDKEnhanceSource**（新建 `fts/data_sources/tqsdk_enhance_source.py`） | 天勤主连 K 线 `close_oi`（缺失回退 `open_oi`）→ hold、一阶差分 → oi_change；**不输出** settle/pre_settle/amount（天勤无，避免覆盖回填值）；复用 `tqsdk_source._SYMBOL_MAP` 映射（RB0/RB 补 0/未知透传）；无账号/无映射/天勤异常全部降级 None |
| **_enhance_fields 有效值覆盖**（`fts/data_sources/aggregator.py`） | 原实现无条件覆盖（`df[col] = enrich_df[col].values`），增强源 NaN 会污染主路径回填值 → 新增 `_apply_enrich_column`：hold/settle/pre_settle 仅覆盖 >0 行、oi_change 仅覆盖非 NaN 行（iloc 位置赋值防索引对齐错位） |
| **默认注册**（`fts/data_futures.py` `_init_default_aggregator`） | `TQSDKEnhanceSource` **默认注册**（天勤账号已配置，零额外依赖）；`futures_enhance_enabled` 仍控制 iFinD/Wind 追加 |
| **真实冒烟** | RB0 最近 10 日 hold=2,039,904~2,401,075，与 AKShare 回填值（2,158,773~2,254,973）**交叉一致（<2%）**；oi_change 差分正确；settle 保留主路径 |
| **回归** | 293 passed（含新增 18 用例）；`tests/data_sources/test_aggregator.py` 2 个 tick_cache 既有失败已修复（DELETE 多列 IN → EXISTS，见 §11.7） |

**边界**：增强层仅在 aggregator 非缓存路径生效（DUCKDB_CACHE 命中时走 `_from_kline_cache` 回填+代理）；天勤无 settle → settle 权威来源仍为 AKShare 回填；pre_settle 无真实源（oi_change 已由天勤补全）。

### 11.5 待执行（数据维护）

- **历史回填**：已完成（全量 82 品种 416,360 行，见 §11.3）——回填脚本保留供增量维护复用（`--symbols`/`--universe`/`--dry-run`，幂等可重入）。
- **iFinD/Wind 增强层可选**：配置 `FTS_FUTURES_ENHANCE_ENABLED=true`（+ MCP 认证）追加权威 pre_settle/oi_change；当前 oi_change 已由天勤 TQSDKEnhanceSource 补全，pre_settle 仍无真实源。

### 11.6 实施偏差

- oi_change 派生与 amount 输出（§3.4 可选）**未实施**——8 列输出契约稳定优先；oi_change 已由天勤增强源提供（aggregator 内部 17 列），公开 8 列输出仍不含该字段，无消费方暂不扩张。
- `futures_hold_real_first` 配置开关未新增——"真实优先"为目标行为直接落地（无效值判定天然兜底代理），无需开关。
- **pre_settle 无真实源**：天勤不提供结算价，settle 权威来源为 AKShare 回填，pre_settle 待 iFinD/Wind 认证或交易所数据接入（方案见 §12）。

### 11.7 tick_cache 去重删除修复（2026-08-11）

- **症状**：`tests/data_sources/test_aggregator.py` `test_get_ticks_from_source` / `test_write_tick_cache_persists_data` 失败（assert 0 == 3）
- **根因**：`_write_tick_cache` 去重删除用多列 IN 子查询 `WHERE (symbol, datetime) IN (SELECT ...)`，DuckDB 不支持（`Binder Error: Subquery returns 2 columns`），DELETE 不执行 → 写入前未清重复、测试断言 0 行
- **修复**：改 EXISTS 相关子查询（`WHERE EXISTS (SELECT 1 FROM df_dup WHERE df_dup.symbol = tick_cache.symbol AND df_dup.datetime = tick_cache.datetime)`），等价语义、兼容 DuckDB/SQLite
- **验证**：test_aggregator.py **89/89 passed**（原 87 过 + 2 修复）；stash 确认该 2 失败为既有问题（DuckDB 兼容性），非阶段 A-D 引入

---

## 12. pre_settle 接入方案（2026-08-11 决策变更：方案 A SDK → 零依赖派生）

### 12.1 现状与接线就绪度

| 项 | 状态 |
|:---|:-----|
| pre_settle 数据 | ✅ **零依赖派生**：`pre_settle[t] = settle[t-1]`（财务定义：昨结算=前一交易日结算价），settle 权威来源为 AKShare 回填（覆盖 86.6%） |
| settle 权威来源 | ✅ AKShare 回填（全库覆盖 86.6%） |
| 代码接线 | ✅ **运行时派生**：`aggregator._derive_pre_settle`（settle.shift(1) 回退 close.shift(1)，内部按 date 升序派生后还原原行序，兼容缓存倒序/源升序两种输出）已接入 `get_ohlcv` 缓存命中+源拉取两条路径；**库内回写**：`scripts/backfill_futures_hold.py --derive-presettle` 幂等回写 kline_cache.pre_settle（双格式 RB/RB0，仅覆盖无效行） |
| 方案 A 遗留 | `IFindSDKSource`（ifind_sdk_source.py，20 测试）框架保留：用户无 iFinDPy 使用权限，SDK 不在公开 PyPI，**不真实接入**；`futures_enhance_enabled=true` 注册在无 SDK/凭据时 is_available=False 自动跳过 |

### 12.2 接入路径对比

| 路径 | 认证 | 成本 | 期货字段 | 历史深度 | 依赖 |
|:-----|:-----|:-----|:---------|:---------|:-----|
| **A. iFinD 官方 SDK**（iFinDPy/thsdata） | 同花顺账号（手机号+密码）或 API token | 低（个人可申请，51ifind 官网） | preSettle/settle/hold/openInterestChg **全字段** | 完整（接口窗口） | `pip install iFinDPy` |
| **B. Wind 官方**（w.wsd） | 万得终端账号 | 高（商业授权） | 全字段 | 完整 | `pip install WindPy` + 终端 |
| **C. 交易所官网结算参数** | 无（公开下载） | 零 | settle 权威；pre_settle 由前日 settle 推导 | 各所不一（上期/大商/郑商/中金/广期） | requests 解析，逐所适配 |

### 12.3 决策变更：采用零依赖派生方案（方案 A 框架保留待有权限时启用）

**决策背景（2026-08-11）**：用户确认无 iFinDPy 使用权限，且 iFinDPy/thsdata 不在公开 PyPI（需 quantapi.51ifind.com 安装包），方案 A 无法真实接入。按"真实优先、代理兜底"原则，pre_settle 采用**零外部依赖派生**，不再阻塞。

```text
派生方案实现（2026-08-11 落地）：
  aggregator._derive_pre_settle（fts/data_sources/aggregator.py）
    ├─ 公式：pre_settle[t] = settle[t-1]（权威），缺失回退 close[t-1]
    ├─ 首行（无前日）：回退当日 close（财务定义缺口，占比 ~0.03%）
    ├─ 仅覆盖无效行（NaN/≤0），不覆盖增强层权威值（_enhance_fields 先行）
    ├─ 内部按 date 升序派生后还原原行序：兼容缓存路径（ORDER BY date DESC 倒序）
    │    与源路径（升序）两种聚合器输出，不改变输出行序契约
    └─ 接入 get_ohlcv 两条路径：缓存命中（L157）+ 源拉取（L185）

  库内幂等回写（scripts/backfill_futures_hold.py --derive-presettle）：
    ├─ 按日期升序遍历 kline_cache，pre_settle 无效行 ← 最近有效 settle[t-1]
    ├─ 双格式 RB/RB0 均处理；settle 无效不推进前值；dry-run 只读统计
    └─ main() 派生分支 dry_run 同样打开连接（读库统计），仅 UPDATE 受保护

方案 A 框架（保留，不启用）：
  IFindSDKSource（fts/data_sources/ifind_sdk_source.py，20 测试）
    └─ 待用户获得 iFinDPy 权限/安装包后：安装 + .env 凭据 + 按 §12.4 勾稽冒烟启用
```

**验证结果（2026-08-11）**：
- 单元/集成：test_aggregator.py 新增 `_derive_pre_settle` 7 用例 + 缓存接入点 1 用例；test_backfill_futures_hold.py 新增派生 10 用例 → **两文件 119/119 passed**
- 库内回写（RB0 真实数据）：`--derive-presettle` 回写 **7,074 行**（dry-run 预检一致）
- 勾稽校验：`pre_settle[t] == settle[t-1]` 一致率以 §12.4 判定

### 12.4 验证方法

| 校验 | 方法 | 判定 |
|:-----|:-----|:-----|
| **勾稽一致性**（核心） | `pre_settle[t] ≈ settle[t-1]`（财务定义：昨结算=前一交易日结算价） | 多品种逐日比对，差异率 <0.1%（保证金参数调整日除外） |
| 跨源交叉 | iFinD settle vs AKShare 回填 settle | 差异 <0.5%（对齐现有多源分歧记录线） |
| 样本抽查 | RB/CU/AU/IF 等主力品种 60 交易日 | 覆盖完整性 ≥95% |

### 12.5 风险与决策

| 风险 | 应对 |
|:-----|:-----|
| iFinD 个人 token 申请门槛 | 51ifind 官网自助申请（按 token 计费/免费额度）；当前派生方案零依赖不受影响 |
| 历史深度受接口窗口限制 | 采用**日增量维护**：首次回填接口窗口 + 每日增强层增量更新；派生方案由库内 settle 全覆盖 |
| 方案 B（Wind）商业成本高 | 仅当路径 A 可用且预算允许时启用 |
| pre_settle 缺口影响 | 当前无因子消费方（8 列公开契约不含）；派生方案已消除缺口，勾稽一致性以 §12.4 校验 |

**决策门（已关闭）**：用户无 iFinDPy 权限 → 采用零依赖派生方案（settle.shift(1)），GAP-083 主线不受阻。方案 A 框架保留，待获得权限/安装包后按 §12.4 冒烟启用。
