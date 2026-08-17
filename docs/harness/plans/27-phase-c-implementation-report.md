# GAP-083 期货持仓/结算数据源接入 — 阶段 C 完整实施报告


> 版本: v2.105.0+7

> 计划文档：`docs/harness/plans/27-futures-hold-settle-integration-plan.md`
> 版本：v2.101.0（日常开发追加，不 bump）｜ 实施日期：2026-08-11
> 关联：GAP-083 ✅ 关闭 / GAP-085 ✅ 关闭

---

## 1. 背景与目标

**GAP-083（P0）**：期货持仓量 `hold` 与结算价 `settle` 在 TDX_LOCAL（17709）主路径恒为 `pd.NA`（通达信 TQ 不返回），期货因子体系缺资金与持仓维度。

**目标**：实现"真实优先、代理兜底"的持仓/结算字段链路：

| 字段 | 修复前 | 目标 |
|:-----|:-------|:-----|
| `hold`（持仓量） | TDX 恒 NA / 代理（20 日均量） | ✅ 真实数据优先 |
| `settle`（结算价） | TDX 恒 NA / 代理（典型价 (H+L+C)/3） | ✅ 真实数据优先 |
| `oi_change`（日增仓） | 无真实源 | ✅ 增强层提供（TQSDK 差分） |
| `pre_settle`（昨结算） | 无真实源 | ✅ 零依赖派生（`pre_settle[t]=settle[t-1]`，见 §3.5） |

## 2. 数据源决策

| 候选源 | 实测结论 |
|:-------|:---------|
| **AKShare `futures_zh_daily_sina`** | ✅ 主连真实 hold/settle，用于**历史回填**（阶段 B） |
| **天勤 TQSDK**（.env 账号） | ✅ free-api 登录成功，K 线含 `close_oi`/`open_oi`；**无 settle/pre_settle/amount**；用于**增强层 hold/oi_change**（阶段 C） |
| TRAE iFinD MCP（7 server） | ❌ 无期货持仓/结算接口（`index_data` 仅返回申万商品指数点位、具体合约查询空） |
| 东财 mx `mx_comprehensive_finance_data` | ❌ 期货持仓/结算/最新价全部返回空 data |

**定版**：历史回填走 AKShare；实时增强层走天勤 TQSDK；pre_settle 由零依赖派生解决（§3.5）；iFinD/Wind 保留可选通路（方案 A 框架待权限，§6）。

## 3. 四阶段实施

### 阶段 A — 读路径修复（`fts/data_futures.py` `_from_kline_cache`）

- SQL SELECT 增加 `hold/settle` 列（kline_cache 表 16/17 列早已存在但从未被查询）
- 双格式对齐：`symbol IN (RB, RB0)` + ORDER BY 0 后缀优先 → drop_duplicates 保留 RB0（TQ 15 年），修复此前主查询只命中 RB 老数据
- 真实优先/代理兜底：无效值（NULL/≤0，TQ 0.0 占位）才走代理（settle=典型价、hold=volume 20 日均量）
- 公开输出 8 列契约不变

### 阶段 B — AKShare 历史回填（`scripts/backfill_futures_hold.py`）

- `fetch_hold_settle_from_akshare`：拉主连真实 hold/settle（`_safe_float` 兜底）
- `write_backfill`：按日期 UPDATE kline_cache 双格式（RB/RB0），仅更新 `hold>0 或 settle>0` 行，幂等可重入
- CLI：`--symbols` / `--universe core|all` / `--dry-run` / `--json`，0.5s 限速、异常品种跳过、trace_id 贯穿
- **实施中发现并修复**：pandas RangeIndex 源与 datetime index 目标 `.map()` 赋值全 NaN → `.to_numpy()`（补 3 回归用例）

### 阶段 C — 增强层落地（数据源定版天勤 TQSDK）

- 新建 `fts/data_sources/tqsdk_enhance_source.py`（`TQSDKEnhanceSource`）：
  - 天勤主连 K 线 `close_oi`（缺失回退 `open_oi`）→ `hold`；一阶差分 → `oi_change`
  - **不输出** settle/pre_settle/amount（天勤无，防覆盖回填值）
  - 复用 `tqsdk_source._SYMBOL_MAP` 映射（RB0 / RB 补 0 / 未知透传）；无账号/无映射/异常全降级 None
- `_enhance_fields` 改**有效值覆盖**（`fts/data_sources/aggregator.py`）：
  - 新增 `_apply_enrich_column`：hold/settle/pre_settle 仅覆盖 >0 行、oi_change 仅覆盖非 NaN 行（iloc 位置赋值防索引对齐错位）
  - 修复原无条件覆盖污染主路径回填值的问题
- `_init_default_aggregator` **默认注册** TQSDKEnhanceSource（天勤账号已在 .env，零额外依赖）；`futures_enhance_enabled` 仍控制 iFinD/Wind 追加

### 阶段 D — 监控配套修复（`fts/monitor/data_level_monitor.py`）

- `key_fields` 修正 `("close","volume","open_interest")` → `("close","volume","hold")`（GAP-085：open_interest 仅存在于 tick 层，持仓量监控此前实际被跳过）

### 3.5 pre_settle 零依赖派生（2026-08-11 收尾，GAP-083 决策变更）

**背景**：方案 A（iFinD SDK）因用户无 iFinDPy 使用权限（iFinDPy/thsdata 不在公开 PyPI）无法真实接入 → 按"真实优先、代理兜底"原则改**零外部依赖派生**。

- **运行时派生**（`fts/data_sources/aggregator.py` `_derive_pre_settle`）：
  - 公式 `pre_settle[t] = settle[t-1]`（财务定义：昨结算=前一交易日结算价），缺失回退 `close[t-1]`、首行无前日回退当日 close
  - 仅覆盖无效行（NaN/≤0），不覆盖增强层权威值（`_enhance_fields` 先行）
  - **内部按 date 升序派生后还原原行序**——兼容缓存路径（`ORDER BY date DESC` 倒序）与源路径（升序）两种聚合器输出，不改变输出行序契约
  - 接入 `get_ohlcv` 两条路径：缓存命中 + 源拉取
- **库内幂等回写**（`scripts/backfill_futures_hold.py --derive-presettle`）：
  - 按日期升序遍历 kline_cache，pre_settle 无效行 ← 最近有效 settle[t-1]；双格式 RB/RB0 均处理；settle 无效不推进前值；dry-run 只读统计
  - 修复 main() 派生分支 dry-run 时 `conn=None` 无法读库统计的缺陷（dry-run 同样打开连接，仅 UPDATE 受保护）
- **方案 A 框架保留**：`fts/data_sources/ifind_sdk_source.py`（20 测试）待用户获得 iFinDPy 权限/安装包后按 plans/27 §12.4 冒烟启用

### 配套修复 — tick_cache 去重删除（`fts/data_sources/aggregator.py`）

- `_write_tick_cache` 去重删除用多列 IN 子查询，DuckDB 报 `Binder Error: Subquery returns 2 columns` → 改 EXISTS 相关子查询（等价语义，兼容 DuckDB/SQLite）
- 修复 `tests/data_sources/test_aggregator.py` 2 个既有失败用例（stash 验证确为既有问题）

## 4. 数据落地结果

| 指标 | 结果 |
|:-----|:-----|
| 预检（dry-run 全量 82 品种） | 416,360 行待更新 / 0 失败 |
| 正式回填核心 25 品种 | 167,904 行更新 / 0 失败（先验证） |
| **正式回填全量 82 品种** | **416,360 行更新 / 0 跳过 / 0 失败**（trace_id `fts.backfill_hold.bd92764a`，核心重刷幂等） |
| 全库 hold 覆盖 | **97.9%**（360,174/367,973） |
| 全库 settle 覆盖 | **86.6%**（318,616/367,973） |
| 抽样验证 | RB 2015-06-15 hold=2,866,710/settle=2,293 量级吻合；JD0 2020-09-15 hold=185,785/settle=3,374 |
| 增强层冒烟 | RB0 天勤 hold=2,039,904~2,401,075，与 AKShare 回填**交叉一致（<2%）**；oi_change 差分正确；settle 保留主路径 |
| pre_settle 回写（RB0） | **7,074 行**（`--derive-presettle`，dry-run 预检一致） |
| pre_settle 全量回写（82 品种） | dry-run 预检 330,662 行待更新 → 正式执行两轮（首轮完成约 83% 后被并发任务干扰中断，幂等重跑补齐 28,582 行，0 失败）→ **全库有效 340,676 / 367,973（92.58%）**，无效 27,297 行为无有效 settle 前值的边界行（各品种历史最早段 + 股指 settle 源缺失） |
| pre_settle 勾稽校验 | **RB0 / CU0 抽查一致率 100%**（有效 settle 行 `pre_settle[t]==settle[t-1]` 零不一致）；RB0 有效 100%（3,154/3,155） |

## 5. 测试与质量

| 项 | 结果 |
|:---|:-----|
| `tests/test_data_futures_hold.py` | **15/15**（阶段 A 7 + 增强注册 4 + 有效值覆盖 4） |
| `tests/data_sources/test_tqsdk_enhance_source.py`（新） | **14/14** |
| `tests/scripts/test_backfill_futures_hold.py` | **23/23**（含索引对齐回归 3 + 派生 TestDerivePreSettle 10） |
| `tests/monitor/test_data_level_monitor.py` | **24**（hold 缺失检测 +2） |
| `tests/data_sources/test_aggregator.py` | **97/97**（tick_cache 2 个既有失败已修复 + pre_settle 派生 8） |
| 模块/集成定向回归 | 两文件 **119/119 passed**（aggregator 97 + backfill 23）+ 阶段 A/C/D 相关模块全绿（按分级测试政策未跑全量） |
| ruff check / verify_doc_consistency | 全部通过（13/13） |

## 6. 遗留事项与后续

| 项 | 状态 | 说明 |
|:---|:-----|:-----|
| **pre_settle 接入** | ✅ 零依赖派生（2026-08-11） | 方案 A（iFinD SDK）因用户无 iFinDPy 权限（不在公开 PyPI）**不真实接入**；改零依赖派生 `pre_settle[t]=settle[t-1]`（缺失回退 close[t-1]）：`aggregator._derive_pre_settle` 运行时派生（接入 get_ohlcv 两路径，内部按 date 升序派生后还原原行序，兼容缓存倒序/源升序）+ `backfill_futures_hold.py --derive-presettle` 库内幂等回写（RB0 已回写 7,074 行）；测试 +18 用例、勾稽校验通过（plans/27 §12 决策变更） |
| iFinD 增强源 | 🔄 可选（框架保留） | `IFindSDKSource` 框架（20 用例）保留：待用户获得 iFinDPy 权限/安装包后按 `FTS_FUTURES_ENHANCE_ENABLED=true` 启用并冒烟；当前派生方案零外部依赖不阻塞 |
| settle 权威来源 | ✅ AKShare 回填 | 天勤无结算价，未覆盖行（股指期货 sina 源限制等）走代理兜底 |
| 公开 8 列输出 | ⚠️ 不含 oi_change/pre_settle | 增强层在 aggregator 内部 17 列提供；无消费方暂不扩张输出契约 |

## 7. 文件清单

| 类型 | 文件 |
|:-----|:-----|
| 新建 | `fts/data_sources/tqsdk_enhance_source.py`、`fts/data_sources/ifind_sdk_source.py`（方案 A 框架，待权限）、`scripts/backfill_futures_hold.py`、`tests/data_sources/test_tqsdk_enhance_source.py`、`tests/data_sources/test_ifind_sdk_source.py`、`tests/test_data_futures_hold.py`、`tests/scripts/test_backfill_futures_hold.py` |
| 修改 | `fts/data_futures.py`、`fts/data_sources/aggregator.py`、`fts/monitor/data_level_monitor.py`、`fts/config/settings.py` |
| 文档 | `docs/harness/plans/27-*.md`、01/03/06/07/08、`docs/factor_data_dict/futures_factor_fields.md` |

---

**结论**：GAP-083 四阶段 + 回填 + 增强层 + tick_cache 修复全链路闭环，期货持仓/结算字段从"恒 NA/代理"升级为"真实优先、多源交叉、代理兜底"。
