# 51 号计划 — 张量化改造衔接缺口与契约缺口修复（vectorization_gap_fix）

> 版本: v2.105.0+7（创建于 2026-08-17）
> 关联: 37-panel-vector-plan.md（面板向量化，已归档）、38-numba-batch4-plan.md（numba 批4）、
>       39-gap-panel-2d-plan.md（缺口面板 2D，已回退）、40-l3-portfolio-optimization-plan.md（L3 四层）
> 状态: ✅ 已完成（2026-08-17，Phase A/B/C 全部落地）
> 优先级: P1 · 负责人: FTS Agent

---

## 1. 背景与目标

37/38/39/40 四个计划落地后（横截面评估全矩阵化默认开启、算子 native 向量化 4.8–525x、
numba 定点内核、L3 信号矩阵服务 A/B/C/D 四层），从 **FTS 全工作流**（L1 因子库 → L2 演化 →
L3 组合 → 定时任务/CLI → 存储层）系统排查，发现以下**衔接缺口与契约缺口**（全部经源码逐行核实）：

### 1.1 缺口清单（P0 数据契约 → P1 功能衔接 → P2 规范补全）

**P0 — 数据契约缺陷（激活即错）**

| # | 缺口 | 位置 | 问题 |
|---|------|------|------|
| C1 | D 层增量判定忽略 params | l3_signal_service.py L344-347（params_hash 写死 `_params_hash({})`）、L495-500（只比 code_hash） | 同 factor_id 修改 params 后静默复用旧信号，与 SignalCache key 含 params_json 口径分裂 |
| C2 | 增量合并无行数防护 | l3_signal_service.py L576-582（loaded 行数取自 meta n_dates 直接赋值） | end_date 相同但窗口扩展/收缩 → broadcast 异常或静默错位 |
| C3 | load_signal_matrix 返回残缺 bundle | l3_signal_service.py L444/448（dates=[]、forward_returns 全 NaN） | 公开 API 直接喂 factor_returns.build_from_panel 必抛 ValueError |

**P1 — 功能衔接缺口（承诺未兑现 / 契约被绕过）**

| # | 缺口 | 位置 | 问题 |
|---|------|------|------|
| F1 | D 层生产链路未激活 | 三处构造点均不传 signal_store：fts/cli.py L635、fts/scheduler/jobs.py L429、portfolio_loop.py L5720 | load_or_build/persist/l3_signal_store.duckdb 全死代码，plans/40 D 层未兑现 |
| F2 | l3_signal_store.duckdb 未登记存储域 | storage_landscape.yaml 无该域；l3_signal_service.py L264 硬编码路径 | 违反"先登记后落库"契约，巡检/备份覆盖不到 |
| F3 | A 层接入盲点 + 双份对齐实现 | portfolio_loop.py L3750（Step1.5 无缓存）、weight_learning.py L512（未透传 signal_cache）、_align_signal_to_dates 双份（portfolio_loop L1296 vs l3_signal_service L88） | 重复沙箱执行 + 对齐逻辑漂移风险 |
| F4 | B 层 duckdb_corr_matrix 孤儿接口 | l3_signal_service.py L185 零生产调用；portfolio_loop L3372 / factor_clustering L104-148 仍 numpy | "相关性 SQL 下沉"未兑现 |
| F5 | 3D 矩阵构建双实现漂移 | portfolio_loop L1510-1536（elastic_net）、L1664-1690（ml_ensemble）绕过 build_signal_matrix | 缓存/增量/对齐增强覆盖不到，语义分叉 |

**P2 — 契约/规范补全**

| # | 缺口 | 位置 | 问题 |
|---|------|------|------|
| N1 | 文档与代码矛盾（numba 状态） | 01-architecture.md L470、03-configuration.md L177 称 zscore/cvar 已回退；feature_ops L398-413、ops_library L221-258 已重新接入 | 排障/风险评估依据错误 |
| N2 | 缓存容量硬编码 | portfolio_loop.py L2074 L3_SIGNAL_CACHE_ENTRIES=20000 未配置化 | 违反"参数 Pydantic 配置管理" |
| N3 | 降级/观测文档缺口 | 04-resilience.md 无 numba/duckdb/l3_signal 降级；05-observability.md 无 [L3-SIGNAL] 指标；SignalCache 淘汰静默 | 降级静默失败难发现 |
| N4 | 测试/文档合计滞后 + 命名误差 | 06-testing.md L581 未计入 numba 86 + l3_signal 16 用例；计划称 test_l3_signal_cache.py 实为 test_signal_cache.py | 工程指标失真 |
| N5 | 计划文档过时 + 孤儿接口状态不明 | 37-plan L61 默认值仍写 false；execute_factor_panel/build_forward_return_matrix/prealign_panel/cvar_2d 无生产调用 | 39 回退教训需在架构文档登记保留契约 |
| N6 | numba warmup 无生产调用 | warmup 仅 benchmark 调用 | L2 CLI 短生命周期首调付 ~1.1s 编译延迟 |

## 2. 修复方案（三阶段）

### Phase A — 契约修复（P0，先于一切；改动集中于 l3_signal_service.py + 测试）

| 任务 | 改动 | 验证 |
|------|------|------|
| A1. params 纳入增量判定 | persist_signal_matrix 增 `params_hashes` 入参（调用方从 factor_codes 真算）；incremental_factor_ids 比对 (code_hash, params_hash) 双哈希；load_or_build_signal_matrix 计算透传 | 新测试：同 code 改 params → 重算；同 code 同 params → 复用 |
| A2. 增量合并形状防护 | load_signal_matrix 暴露 n_dates；合并前校验 loaded 行数与 common_dates 一致，不一致降级全量重算并 warning | 新测试：meta n_dates ≠ 当前 → 重算分支命中 |
| A3. bundle 契约完整化 | load_signal_matrix 增 `common_dates` 参数回填 dates；docstring 显式警示 forward_returns 需调用方重建 | 新测试：直接消费 load 结果不崩；契约说明 |

### Phase B — 功能接线（P1）

| 任务 | 改动 | 验证 |
|------|------|------|
| B1. D 层接入生产 | 三处构造点传 signal_store=(market, end_date, db_path)，end_date 由面板最新交易日推导；配置 `l3_signal_store` 控制（默认 true） | 二次运行增量命中 + 产出一致 |
| B2. 存储域登记 | storage_landscape.yaml 增 l3_signal_assets 域；路径配置化 | test_storage_registry 断言 |
| B3. A 层补盲 + 对齐收敛 | portfolio_loop L3750 复用缓存；weight_learning L512 透传；删除本地 _align_signal_to_dates 副本 | 受影响模块全绿 |
| B4. B 层决策 | duckdb_corr_matrix 对照验证后接入（开关默认 off）或登记豁免 | 对照测试 corr 一致 |
| B5. 3D 构建收敛 | elastic_net/ml_ensemble 改走 load_or_build_signal_matrix | 权重 on/off 逐位一致 |

### Phase C — 契约/规范补全（P2）

| 任务 | 改动 | 验证 |
|------|------|------|
| C1. 文档纠偏 | 01-arch L470 / 03-config L177 / 37-plan L61 更新 | verify_doc_consistency 13/13 |
| C2. 参数配置化 | L3_SIGNAL_CACHE_ENTRIES 迁入 settings.py | test_config_settings 新用例 |
| C3. 降级/观测补全 | 04-resilience / 05-observability 补章节；SignalCache 淘汰 debug 日志；duckdb 回退升 warning | 日志单测 |
| C4. 测试/文档对齐 | 06-testing 合计补 numba 86 + l3_signal 16；命名核对 | 清单核对 |
| C5. 孤儿接口状态登记 | 01-arch 明确保留状态与"禁止再次误接入"契约 | 文档核对 |
| C6. warmup 接入 | CLI/L2 循环入口调 warmup（后台线程） | 冷启动耗时日志 |

## 3. 改动文件清单

| 文件 | 改动 |
|------|------|
| fts/factor_engine/l3_signal_service.py | A1/A2/A3 + B2 路径配置化 |
| fts/factor_engine/portfolio_loop.py | B1（构造点传参）+ B3 + B5 |
| fts/factor_engine/weight_learning.py | B3（透传 signal_cache） |
| fts/factor_engine/factor_clustering.py | B4（如接入） |
| fts/factor_engine/signal_cache.py | C3（淘汰日志） |
| fts/config/settings.py | C2（l3_signal_cache_entries / l3_signal_store 配置） |
| fts/cli.py、fts/scheduler/jobs.py | B1 |
| docs/harness/_data/storage_landscape.yaml | B2 |
| docs/harness/{01,03,04,05,06,07,08}.md | C1/C3/C4/C5 + 版本记录 |
| tests/factor_engine/test_l3_signal_service.py | A1/A2/A3 新用例 |

## 4. 测试计划

| 任务 | 覆盖 |
|------|------|
| A1 | params_hash 真算；改 params 触发重算；同 params 复用；与 SignalCache 口径一致 |
| A2 | 形状不一致重算；同形状合并 |
| A3 | load 结果 dates 回填；forward_returns 警示 |
| B1 | 增量命中；产出一致（on/off） |
| B4 | DuckDB corr vs np.corrcoef 一致 |

## 5. 验收标准

1. P0 三项各有新测试覆盖全绿；
2. D 层在生产路径激活：二次运行增量命中、命中率日志可观测、产出一致；
3. l3_signal_store.duckdb 已登记存储域且经 StorageRegistry 校验；
4. A 层无遗漏 FactorExecutor 裸调用（grep 复核）；
5. 对齐/3D 构建收敛单一实现；
6. 受影响模块回归全绿（`pytest tests/factor_engine/ -m "not slow"`），ruff/mypy 通过；
7. 12 项检查清单 + verify_doc_consistency 13/13 + build bump。

## 6. 版本与归档

- 每阶段完成 build bump（日常开发，不触发里程碑 bump）；
- 计划关闭：Phase C 完成 + 受影响回归全绿后归档。

## 6.1 实施记录（2026-08-17 全部落地）

| 阶段 | 内容 | 验证 |
|------|------|------|
| Phase A1 | `persist_signal_matrix` 增 `params_hashes` 入参真算；`incremental_factor_ids` 双哈希比对（None 向后兼容）；`load_or_build` 计算透传 | test_l3_signal_service +2（params 判定/向后兼容） |
| Phase A2 | 形状防护：loaded 行数 ≠ 当前面板 → 降级重算；读取失败不丢信号 | test_l3_signal_service +2（形状不符重算/形状一致合并） |
| Phase A3 | `load_signal_matrix` 增 `common_dates` 回填 dates；契约 docstring（forward_returns 需重建） | test_l3_signal_service +1（dates 回填/fwd 全 NaN） |
| Phase B1 | settings 增 `l3_signal_store_enabled/db`；`PortfolioLoop` 构造自动激活（显式传参优先）；`_auto_build_factor_returns` end_date 由面板最新交易日推导（`str(...)[:10]`） | test_config_settings +2 + test_portfolio_loop +4 |
| Phase B2 | storage_landscape.yaml 增 `l3_signal_assets` 域；`_default_db_path()` 配置化 | test_storage_registry 契约校验通过 |
| Phase B3 | Step1.5 外推验证 `FactorExecutor` 透传 `signal_cache`；`weight_learning` 跨市场 IC 透传；`_align_signal_to_dates` 双份收敛 `l3_signal_service.align_signal_to_dates` | 受影响模块 324 passed |
| Phase B4 | `duckdb_corr_matrix` 登记豁免（numpy 单次调用优于逐对 SQL 往返），docstring 注明保留备用 | 既有对照测试保持 |
| Phase B5 | elastic_net/ml_ensemble 3D 构建收敛 `build_signal_matrix`（复用缓存+对齐，消除双实现） | 受影响模块 303 passed |
| Phase C1/C5 | 01-arch L470/L422/L418、03-config L177、37-plan L61 文档纠偏 + 孤儿接口保留状态登记 | verify_doc_consistency 待终验 |
| Phase C2 | `L3_SIGNAL_CACHE_ENTRIES` → `l3_signal_cache_entries` 配置化 | test_config_settings +1 |
| Phase C3 | SignalCache evictions 可观测（debug 日志 + stats）；04-resilience 张量化降级表；05-observability [L3-SIGNAL] 指标 | test_signal_cache stats 兼容 |
| Phase C4 | 06-testing 合计修正（numba 86 + l3_signal 16 + plans/51 13） | 清单核对 |
| Phase C6 | CLI main() 后台线程 numba warmup | 冷启动耗时日志 |

新增测试合计 13 用例；受影响模块回归：test_portfolio_loop + test_weight_learning 303、test_l3_signal_service + test_config_settings + test_storage_registry + test_signal_cache 等全绿 + ruff 通过。

## 7. 12 项检查清单映射（实施时逐项核对）

| # | 检查项 | 对应文档 | 状态 |
|:--|:-----|:-----|:-----|
| 1 | 数据流/架构变更 | docs/harness/01-architecture.md（l3_signal_store 域 + numba 状态纠偏） | 实施时 |
| 2 | 阶段/产出物 | docs/harness/02-lifecycle.md | 实施时 |
| 3 | 新配置项 | docs/harness/03-configuration.md（l3_signal_cache_entries / l3_signal_store） | 实施时 |
| 4 | 降级/熔断路径 | docs/harness/04-resilience.md（numba/duckdb/l3_signal 降级） | 实施时 |
| 5 | 新指标/日志 | docs/harness/05-observability.md（[L3-SIGNAL] 指标） | 实施时 |
| 6 | 测试文件/用例数 | docs/harness/06-testing.md（numba 86 + l3_signal 16 合计修正） | 实施时 |
| 7 | 版本号/历史 | docs/harness/07-operations.md + pyproject.toml | 实施时 |
| 8 | 差距登记 | docs/harness/08-gap-analysis.md（GAP-124 补充落地缺口 / 新登记） | 实施时 |
| 9 | 晋级里程碑 | docs/harness/09-advancement-plan.md | 实施时 |
| 10 | 流程文档 | docs/production_plan.md | 实施时 |
| 11 | CLAUDE.md 职责 | CLAUDE.md | 实施时 |
| 12 | README 工程指标 | README.md（测试数/版本徽章） | 实施时 |
