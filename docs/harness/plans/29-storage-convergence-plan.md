# FTS 数据持久化与存储架构渐进式收敛计划（Storage Persistence Convergence Plan）

> 版本: v3.0.0+6
> 最后更新: 2026-08-11
> 状态: 执行中（**P0 基建 ✅ / P1 因子资产入库 ✅ / P2 运行状态入库 ✅ / P3 信号缓存+行情库治理 ✅ / P4 归档清理部分实施（清理项待冻结期）已落地（2026-08-11/12）** — 文档先行 + fts/store 数据访问层 + elite JSON 全量 SSOT + 状态入 state.duckdb + 信号 Parquet + 行情冷热分层）
> 适用范围: 全链路数据层（行情 / 因子资产 / 运行状态 / 信号缓存 / 日志血缘）

> ⚠️ **计划定位**：本计划解决 FTS 数据"格式分散、JSON 泛滥、同类数据多处落盘"的持久化治理问题。对标头部量化机构（T2 国内头部私募 / T3 海外顶级）与数仓工程的数据持久化方案，按「先收敛资产类 → 再治理状态类 → 最后清理过程类」的渐进式节奏推进，**每阶段独立可验收、可回滚，全程不动存量读路径**。与 plans/23（机构化总纲 GAP-I 系列）互补：本计划聚焦"数据怎么存"，plans/23 聚焦"能力缺什么"。

---

## 1. 背景与目标

### 1.1 用户诉求

FTS 数据当前分散于 `data/`、`memory/`、`reports/` 等多处，格式涵盖 DuckDB / JSON / NPY / JSONL / CSV / PNG / GZ / SQLite 8+ 种，其中 JSON 文件多达 3,670 个。缺乏统一的数据目录与持久化分层，导致：数据管理困难、持久化保存无保障、同类数据双写漂移。

### 1.2 现状勘察（2026-08-11 实测量化）

| 存储域 | 位置 | 规模 | 格式 |
|:-------|:-----|:-----|:-----|
| 行情库 | `data/fts_history.duckdb` | 248 MB（kline_cache/contract_kline/minute/tick） | DuckDB |
| 因子目录 | `data/factor_catalog{,_stock,_futures}.duckdb` | 106 MB（13 表 ×3 库，v2.101 已物理分库） | DuckDB |
| 精英因子 | `memory/knowledge/factors/` | 活跃 236 + 归档/退役 545 = 781 JSON | JSON + DuckDB 双写（GAP-032） |
| 演化状态 | `memory/evolution/` | traces 1,494 + tracking 312 + failure 84 + state.json | JSON |
| 组合状态 | `memory/portfolio/` | combo_history 200 + agent_proposals 370 + 权重/动态池 40+ | JSON |
| 信号缓存 | `memory/cache/factor_signals/` | 283 个 .npy + signal_index.json | NPY |
| 日志血缘 | `data/` + `reports/` + `memory/logs/` | jsonl 6 文件 66 MB + experiments-*.json 72 + lineage gz 8 | JSONL / GZ |
| 配置 | `seeds/` + `config/` | 股票 10 + 期货 20 YAML | YAML |

### 1.3 核心问题诊断

1. **格式横向分散**：8+ 种存储形态并存，无统一数据目录（catalog），读写入口散落各模块。
2. **同类数据多处落盘（违反 SSOT）**：elite 因子 JSON 快照 + DuckDB catalog 双写（GAP-032）；factor_catalog 拆 3 库却每库重复建 13 张表。
3. **状态与资产混放**：`memory/` 下"可重建的运行时状态"（traces/agent_proposals）与"不可重建的业务资产"（elite 因子）无隔离，过程类 JSON 每轮演化爆量。
4. **缓存无 schema/校验**：信号缓存为裸 .npy + 索引 JSON，无版本、无校验和、无血缘。
5. **单文件长占用**：`fts_history.duckdb` 单写者连接被进程长期持有，只读亦被锁（只读勘察时复现）。

### 1.4 目标

1. 建立**分层存储架构**（配置 / 行情 / 因子资产 / 运行状态 / 信号缓存 / 日志血缘六层），每个数据域有唯一权威存储（SSOT）。
2. **收敛格式**：结构化数据统一进 DuckDB（行情、因子资产、运行状态）；信号/大面板与归档层走 Parquet 列式；JSON 收敛为"只读快照 + 配置"，过程类归档压缩。
3. **渐进迁移**：P0 基建（数据访问层 + 盘点登记）→ P1 因子资产入库 → P2 状态入库 → P3 信号缓存与行情治理 → P4 归档清理；每阶段双写→切换→退役，可回滚。
4. 全程遵循 HARNESS：文档先行、契约优先、测试随重构、trace_id 贯穿迁移脚本、迁移脚本幂等可续跑。

---

## 2. 机构对标（数据持久化方案要点）

| 机构实践 | 本计划落地映射 |
|:---------|:---------------|
| 分层数仓（ODS→DWD→DWS→ADS） | 六层存储分层（§3），新增数据域先登记后落库 |
| 一数一源 SSOT，禁止双写 | P1 反转 elite 写方向（先 DuckDB 后 JSON 快照）；catalog 按市场单库 |
| 状态与数据分离（状态可重建、资产持久化） | P2 运行状态入 `state.duckdb`，过程类（traces/experiments）gz 归档 |
| 列式存储 + 分区 + 冷热归档 | P3 信号缓存转 Parquet（列式 + schema + 校验和）；行情按年分区/冷层归档 |
| 元数据与血缘管理 | 数据访问层 `storage_catalog.yaml` 登记域→库/表/分区键/保留策略；迁移脚本写 lineage |
| 事件追加 + 版本可回放 | 状态类走"当前状态表 + 历史追加表"；迁移脚本幂等 + 可续跑 + 回滚快照 |

---

## 3. 目标分层存储架构

| 层 | 内容 | 存储 | 迁移源 |
|:---|:-----|:-----|:-------|
| L1 配置层 | `seeds/*.yaml` + `config/`（属配置非数据） | YAML 文件 | 现状不变 |
| L2 行情库 | kline / minute / tick / sentiment | `data/fts_history.duckdb`（追加式 + 分区 + 冷热） | 治理 + 冷热归档 |
| L3 因子资产库 | 因子 / 评估 / 版本 / 审查 / 血缘（股票期货统一 catalog） | `data/factor_catalog_*.duckdb` | elite JSON ×781 |
| L4 运行状态库 | 演化 / 组合 / combo 历史 / 权重 | `data/state.duckdb`（新增） | 状态 JSON ×2,500 |
| L5 信号缓存 | 因子信号面板 / 信号缓存 | Parquet 湖式目录 | .npy ×283 |
| L6 日志血缘 | lineage / disagreements / experiments | JSONL（保留追加 + 摘要入库） | 保留 + 汇总 |

> 选型（2026-08-11 用户确认）：**DuckDB + Parquet 双轨** — 行情/因子资产/运行状态入 DuckDB（单机 OLAP，契合 GAP-056 单写者架构），信号/大面板与归档层走 Parquet。

---

## 4. 分阶段路线图

### Phase 0 — 基建与契约（零迁移，纯加固）✅ 本期落地

- [x] 新建 `fts/store/` 数据访问层：统一读写入口、存储路由与 schema 契约（`storage_landscape.yaml` 登记每个数据域 → 后端/路径/保留策略）。
- [x] 目录盘点登记：将 §1.2 勘察的存储域登记到 `docs/harness/_data/storage_landscape.yaml`（Layer 3 数据，文档一致性校验引用）。
- [x] 单元测试：storage 注册表加载/契约校验/未知域报错；`pytest tests/store/ -v` + `ruff check` 通过。
- 验收：**未动任何存量数据**；盘点表齐全；注册表可被后续 Phase 消费。

### Phase 1 — 因子资产入库（SSOT 第一步，最高优先）✅ 已实施（2026-08-11）

- 迁移对象：`memory/knowledge/factors/` 下 elite JSON ×781（含 _archive/_retired）→ factor_catalog DuckDB，**DuckDB 为单一事实源**，JSON 降级为只读快照。
- 动作：一次性迁移脚本 `scripts/migrate_elite_json_to_catalog.py`（比对 catalog 已有记录 → 差量补齐；迁移报告 + 逐字段校验）；repository 写路径反转为"先写 DuckDB，再同步 JSON 快照"。
- 验收：catalog 与 JSON 逐字段一致性校验通过；`fts factor list` 等读接口零改动回归。
- 回滚：保留 JSON 快照，随时回切。

**实施记录（2026-08-11）**：

- ① **对账量化**（迁移前勘察）：stock catalog 136 行 / JSON 518 个（active 129 + _archive 389）→ 差量缺失 389；futures catalog 187 行 / JSON 259 个（active 104 + _archive 132 + _retired 19 + _deprecated 5）→ 差量缺失 139；catalog 孤儿（catalog 有 JSON 无）stock 7 / futures 67。
- ② **迁移脚本** `scripts/migrate_elite_json_to_catalog.py`：`scan_elite_jsons`（active/_archive/_retired/_deprecated 全子目录扫描，`SUBDIR_STATUS` 映射）+ `build_factor_dict`（对齐 `_write_to_duckdb` 字段语义：ic/sharpe 取自 evaluation.level_1_backtest、market 强制目标市场、is_elite=True）+ `build_eval_dict` + `verify_factor`（逐字段校验：name/code/params/signature/economic_logic/source/generation/ic/sharpe/icir，浮点容差 1e-6）+ `migrate_market`（差量补齐 + 校验 + 孤儿报告，幂等可重入）+ `--dry-run`（只读预估真实缺口）/`--verify-only`（仅校验）/`--sync`（既有因子不一致时以 JSON 为准同步内容字段，不触碰 status 等 lifecycle 字段）；`--json` 输出；trace_id `fts.migrate_elite.{market}.{ts}` 贯穿。
- ③ **写路径反转** `evolution_loop.py` `_promote_to_elite`：改为**先写 DuckDB（SSOT）→ 成功后再写 JSON 快照（只读备份，写失败仅 warning 不阻断）**；DuckDB 失败不写 JSON 直接判定晋升失败（原"先 JSON 后 DuckDB 失败回滚 JSON"）。JSON 正式降级为快照。既有 GAP-032 测试（duckdb 失败回滚/成功保留 JSON）语义兼容。
- ④ **repository 扩展** `factor_db/repository.py` `add_evaluation` 新增 `update_catalog_status: bool = True` 参数：迁移归档/退役因子时传 False，避免 add_evaluation 的"按评估结果置 active/failed"覆盖归档 lifecycle 状态（根因：`add_evaluation` 无条件回写 status 导致 archived→active）。默认 True 行为不变。
- ⑤ **漂移同步**：既有 84 处 code 字段不一致（JSON 快照含 NaN 加固等更新，catalog 落后）→ `--sync` 以 JSON 为准同步 catalog 内容字段（stock 1 / futures 82）。
- ⑥ **真实迁移结果**：stock 389 补齐（136 active + 389 archived = 525 行）、futures 139 补齐（170 active + 132 archived + 18 retired + 5 deprecated + 1 deleted = 326 行）；**差量缺失归零**；778 个 JSON 因子逐字段校验 **0 不一致**；孤儿保留（catalog 有 JSON 无，SSOT 下 DuckDB 为主可接受，后续可重建快照）；迁移前已备份两库至 `data/backup/factor_catalog_{stock,futures}.duckdb.bak.p1_20260811`。
- ⑦ **测试** `tests/scripts/test_migrate_elite_json_to_catalog.py` **17 用例全绿**（差量补齐+status 映射/幂等/已存在校验零差异/字段差异报告/sync 更新漂移/dry-run 不写/verify-only 只读/坏 JSON 跳过/孤儿报告/futures 市场路由/builders/verify 一致性/CLI dry-run）；受影响回归：test_evolution_loop promote 相关 105 passed + test_factor_db 67 passed + ruff/mypy 全绿。
- ⑧ **遗留登记**：futures catalog 67 个孤儿（无 JSON 快照）与 stock 7 个孤儿未处理（DuckDB-only，SSOT 主存储视角合法）；归档因子 lifecycle 状态在 catalog 中按子目录映射（archived/retired/deprecated），不与 JSON 冲突。

### Phase 2 — 运行状态入库 ✅ 已实施（2026-08-11）

- 迁移对象：`memory/evolution/`（state/traces/tracking）、`memory/portfolio/`（state/combo_history/权重/动态池）、`memory/meta_loop|extractors|loop/` state.json → 新建 `data/state.duckdb`，按"当前状态表 + 历史追加表"建模。
- **traces 1,494 / agent_proposals 370 属过程痕迹 → gz 归档进 `data/archive/`，不逐条入库**（可重建）。
- 验收：L3 组合回放结果与迁移前一致（combo_history 抽样对账）；无 state.json 可从 DuckDB 冷启动。

**实施记录（2026-08-11）**：

- ① **状态存储层** `fts/store/state_db.py` `StateKVStore`（DuckDB 双表）：`state_kv` 当前状态表（namespace+key 复合主键，UPSERT，列：namespace/key/value(JSON)/updated_at/run_id）+ `state_history` 历史追加表（seq 自增主键 + index，可回放审计）；核心方法 `upsert`（UPSERT + 追加历史，返回 seq）/`get`/`get_all`/`snapshot`（全量 dump 供冷启动与对账）/`history(namespace,key,limit)`/上下文管理器；默认库 `data/state.duckdb`。
- ② **迁移脚本** `scripts/migrate_state_to_duckdb.py`：`STATEFUL_GLOBS` 规则表（glob → namespace/key/kind，覆盖 evolution/meta_loop/extractors/loop/portfolio 的 state.json、combo_history、drift_history、live_feedback.jsonl（jsonl 多行逐条解析）、factor_pool.json 等，key 模板 `{stem}/{parent}` 渲染）+ `ARCHIVE_DIRS` 过程痕迹目录（evolution/traces、tracking、test_elite、failure、futures/traces、futures/failure、portfolio/agent_proposals）；`discover_stateful_sources`（glob 展开 + resolve 去重）+ `migrate_state`（入库 + 读回对账）+ `archive_process_traces`（tar.gz 打包，**复制语义不删源**，输出 `data/archive/state_traces_{ts}.tar.gz`）；CLI `--dry-run`/`--verify-only`/`--archive`/`--db-path`/`--state-dir`/`--json`；trace_id `fts.migrate_state.{ts}` 贯穿。
- ③ **真实迁移结果**：权威状态条目 **231** 全部入库（`state_kv` 231 行：portfolio 225 + evolution 2 + extractors 1 + knowledge 1 + loop 1 + meta_loop 1；`state_history` 231 行）；入库后读回逐字段对账 **231/231 一致、0 mismatch、0 failed**；SQL 级独立抽样复核 combo_history 嵌套 JSON 结构完整（len=17/14）。幂等可重入（重复执行不产生重复行，UPSERT 语义）。
- ④ **过程痕迹归档**：dry-run 预估 2307 个文件 → `--archive` 打包 `data/archive/state_traces_20260811235839.tar.gz`（复制语义，源文件保留，删除留 P4 冻结期）。
- ⑤ **测试** `tests/store/test_state_db.py` **11 用例**（upsert/get/覆盖/历史追加/namespace 聚合/snapshot/persist reopen/list 值/history 过滤）+ `tests/scripts/test_migrate_state_to_duckdb.py` **8 用例**（发现规则+去重/迁移对账/幂等/过程痕迹归档）= **19 用例全绿**；受影响回归 `pytest tests/store/ -v` + ruff/mypy 全绿。
- ⑥ **遗留登记**：JSON 源文件保留（双读兼容期，P4 冻结期后按保留策略清理）；`live_feedback.jsonl` 逐条入库（每条为一条状态记录）；knowledge/factor_pool.json 作为状态登记（因子池属知识域，资产权威仍在 factor_catalog，此处登记快照）。
- ⑦ **冷启动验证**：`StateKVStore.snapshot()` 可全量 dump 重建任意 namespace 的当前状态（替代 state.json 冷启动路径），供 L4 组合回放/冷启动消费。

### Phase 3 — 信号缓存与行情库治理 ✅ 已实施（2026-08-12）

- `.npy ×283` → Parquet（列式 + schema + 版本 + 校验和），`signal_index.json` → 元数据表；读路径经统一缓存层（可回退重建）。
- 行情库治理：`fts_history.duckdb` 按年分区/冷热归档（历史 → `data/archive/history_YYYY.duckdb` 或 Parquet 冷层）；连接生命周期治理（解决单写者长期占用）。
- 验收：信号缓存重建与旧 .npy 数值逐点一致（<1e-12）；行情归档后热查询可测提速。

**实施记录（2026-08-11/12）**：

- ① **P3-A 信号缓存 Parquet 化（已落地）** `fts/factor_engine/factor_optimizer.py` `FactorSignalCache`：新增 `PARQUET_CACHE_VERSION=2` + 模块级 helper（`_write_parquet`/`_read_parquet` 用 **DuckDB 原生写/读单列 Parquet，零新增依赖**（pyarrow 未装，duckdb 原生支持）；`_signal_checksum` = float64 序列化后 sha256 前 16 位）。
- ② **读写语义**：`put()` 改为写 `{cache_id}.parquet`（不再写 .npy），`signal_index.json` 作为配套元数据表，每条目含 factor_id/factor_code_hash/symbol/data_version + `backend="parquet"`/`version=2`/`checksum`；`get()` **Parquet 优先**（读回后与 checksum 比对，不匹配判损坏→删除该文件并 miss 触发重建），缺失时 **.npy 只读兼容回退**（读到旧 .npy 自动重建为 Parquet）；`clear()`/`invalidate_factor()` 双格式（.parquet + .npy）清理。
- ③ **实现偏差登记**：信号缓存为**引用缓存（可重建）**，选择在原缓存目录 `memory/cache/factor_signals/` 内 Parquet 化（最小侵入），未搬至 plans/29 原定义的 `data/signals` 湖式目录——无需湖式，避免过度工程；storage_landscape 的 signal_parquet 域 path 同步为实际路径。
- ④ **测试**：`tests/factor_engine/test_factor_optimizer.py` 既有断言同步（.npy→.parquet）+ 新增 `TestFactorSignalCacheParquet` **5 用例**（put 写 parquet 非 npy/磁盘重开读回/checksum 篡改判 miss 并删除/.npy 兼容回退并自动重建/clear 双格式清理），文件 **51 passed** + ruff/mypy 全绿。当前 signal 缓存目录为空（无存量 .npy），无历史数据迁移负担。
- ⑤ **P3-B 行情库冷热归档（已落地）**：新建 `scripts/archive_history_cold.py`（`--dry-run`/`--verify-only`/`--archive`/`--table`/`--until-year`/`--db-path`/`--archive-root`/`--json`，trace_id `fts.history_archive.{ts}`，幂等可重入，库被写锁占用时降级拒绝；`kline_cache.date` 实为 VARCHAR 按 `year(date::DATE)` 判定）。**实际执行**（2026-08-12，期货 L2 演化任务 PID 6712 结束后无写锁窗口）：`kline_cache` 367,973 行中 **≤2013 共 44,134 行（2005-2013 共 9 年）导出至 `data/archive/history_kline_cache_{year}.parquet`（合计 1.22MB）并从热库 DELETE**，热库剩余 323,839 行（2014-2026 完整）；`--verify-only` 复核 **cold_rows=44134、hot_remaining=0、consistent=true**；归档前备份 `data/backup/fts_history.duckdb.bak.p3_20260812`（135.5MB）。
- ⑥ **连接生命周期治理（遗留登记）**：`data_futures.py` 全局单写者 `DuckDBWriter` 在演化进程存活期持有写锁（E.1 并发模型根治的固有代价），连只读亦被锁。P3-B 通过「脚本化冷热归档 + 无写锁窗口执行」缓解长期占用对归档的影响；底层连接生命周期改造（写连接按操作短生命周期开关）属受保护数据层且有风险，**登记为远期遗留**，不强行改造。
- ⑦ **测试**：`tests/scripts/test_archive_history_cold.py` **7 用例**（年份统计/min_year/dry-run 计数/空表抛错/归档-verify 闭环/幂等/不一致检测）全绿 + ruff/mypy 全绿。

### Phase 4 — 归档与清理 ✅ 部分实施（2026-08-12）

- 旧 JSON/npy 在冻结期（≥1 发布周期）后移入 `data/archive/` 并最终清理；experiments/lineage 摘要入库后按保留策略压缩。
- 收尾：`storage_landscape.yaml` 刷新终态；全量回归 + 一致性 13/13；按版本纪律 bump。

**实施记录（2026-08-12）**：

- ① **storage_landscape 终态**：8 域 active（config_seeds/market_history/factor_assets/run_state/signal_parquet/lineage_logs/sim_portfolio/reports）+ 5 域 legacy（elite_snapshots/evolution_state/portfolio_state/signal_cache/experiment_logs 标注「P4 冻结期≥1 发布周期后退役清理」）；契约校验 0 违规、13 域。
- ② **一致性 + 回归**：`verify_doc_consistency.py` 13/13；P0~P3 受影响模块回归 **90 passed**（store 24 + migrate_state 8 + archive_history 7 + factor_optimizer 51）。
- ③ **清理项受冻结期约束（登记待执行）**：P1 迁移的 elite JSON 快照、P2 迁移的状态 JSON、P3 的旧 .npy **均于 2026-08-11/12 刚完成迁移，冻结期（≥1 发布周期）未满**，按计划「冻结期后最终清理」约束，**不立即删除**；登记为待冻结期满执行（P1：elite JSON ×778 只读快照 → factor_catalog SSOT 稳定运行一周期后退役；P2：状态 JSON → state.duckdb 冷启动验证一周期后退役；P3：.npy 只读兼容 → 无存量实际文件，无清理负担）。experiments/lineage 摘要压缩同理待冻结期。
- ④ **bump 评估**：P0~P3 已构成完整数据层收敛里程碑（SSOT 达成、状态入 state.duckdb、信号 Parquet、行情冷热分层），但 P4 清理项受冻结期约束未执行，**bump 留待冻结期结束的正式发布**（届时跑全量回归 + bump）。
- ⑤ **P4 读路径切换（2026-08-12，阶段收尾）**：JSON 双读兼容期收紧为 **DuckDB SSOT 优先、JSON 仅回退**，覆盖四处读路径——(a) **动态池** `fts/data_futures.py` `get_dynamic_core_subset`：SSOT `state.duckdb`（`portfolio/futures_dynamic_pool`）→ JSON 缓存（兼容期）→ 静态池三级降级；`scripts/sync_liquidity_pool.py` 写路径同步（DuckDB 先写 + JSON 兼容写）；(b) **CLI** `fts/cli.py` `factor list/show`：默认 DuckDB 查询（无筛选走 `list_factors` 全量、精确匹配 + 模糊兜底），JSON 目录模式仅回退；`factor show` 补 `--market` 参数（默认 futures，与 list 一致）；(c) **运行状态** `fts/factor_engine/state.py` / `portfolio_loop.py` / `meta_loop.py`：state.json 读写 → `StateKVStore`（`get_state_store()` 进程级单例 + 可注入 store 支持测试隔离），JSON 仅只读快照；(d) **提取器状态** `fts/factor_engine/extractors/base.py`（`_load_state`/`_save_state` → `StateKVStore` `extractors/state`）。**测试适配**：状态类测试注入临时 `StateKVStore` fixture 隔离 SSOT（evolution/meta_loop/portfolio_loop/extractors 五文件）；`test_dynamic_pool` 注入 `_EmptySSOT` mock 使 JSON 兼容路径可测；`test_cli_extra` `factor list --json` 目录模式用例改为模拟 DuckDB 不可用回退（SSOT 语义）。**回归**：store 24 + migrate_state 8 + archive_history 7 + factor_optimizer 51 + dynamic_pool 10 + test_cli/test_cli_extra/test_data_futures 264 + factor_engine 状态套件 634（632 passed + dynamic_pool 2 修复后复跑）= **受 P0~P4 影响的定向回归全绿** + ruff/mypy 全绿（修复 portfolio_loop BL 分支 mypy 类型冲突：`bl_signals` 独立接收 Optional 后再赋 `signals`）。

---

## 5. 差距登记

| ID | 模块 | 差距描述 | 影响 | 优先级 | 状态 |
|:---|:-----|:---------|:-----|:-------|:-----|
| GAP-090 | 全链路数据层 | 数据格式分散（8+ 种）、JSON 泛滥（3,670 个）、同类数据双写漂移（elite JSON+DuckDB、catalog 3 库重复建表）、信号缓存裸 .npy 无 schema、单文件长占用 | 数据管理/持久化无保障，SSOT 未达成，状态与资产混放 | P2 | 🟡 开放（P0 基建落地，P1~P4 排期） |

---

## 6. 验收标准（量化指标）

| 阶段 | 验收指标 |
|:-----|:---------|
| P0 | 注册表加载/契约校验单测通过；storage_landscape.yaml 覆盖 §1.2 全部存储域；零存量数据变更 |
| P1 | catalog 与 JSON 逐字段一致；elite 写方向反转；读接口回归全绿 |
| P2 | 状态库替代 state.json 冷启动；combo 回放一致；traces 归档压缩 |
| P3 | 信号缓存重建一致 <1e-12；行情分区/冷层就绪 |
| P4 | 旧文件冻结期后清理；storage_landscape 终态；全量回归 + 一致性 13/13 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/store/registry.py`（StorageDomain/StorageBackend + YAML 契约加载）；`fts/store/state_db.py`（StateKVStore，P2）；`fts/factor_engine/factor_optimizer.py`（FactorSignalCache Parquet 化，P3-A）；`docs/harness/_data/storage_landscape.yaml`（存储域盘点登记，Layer 3 数据）；迁移脚本（P1 `scripts/migrate_elite_json_to_catalog.py` / P2 `scripts/migrate_state_to_duckdb.py`，随阶段落地） |
| 可验证断言 | §1.2 存储域量化（3670 JSON / 4 duckdb / 283 npy / 66MB jsonl）与 storage_landscape.yaml 登记一致；P0 阶段零存量数据变更（git 仅新增 fts/store + tests/store + yaml）；每阶段"双写→切换→退役"顺序固定；GAP-090 登记于 08-gap-analysis.md 并在 09-advancement-plan.md 排期；**P1 断言（2026-08-11）**：`python scripts/migrate_elite_json_to_catalog.py --market all --verify-only` 返回 0（778 因子逐字段校验 0 不一致）；stock catalog 525 行（active 136 + archived 389）/ futures catalog 326 行（active 170 + archived 132 + retired 18 + deprecated 5 + deleted 1）；差量缺失 0；**P2 断言（2026-08-11）**：`python scripts/migrate_state_to_duckdb.py --verify-only` 返回 0（231/231 一致）；`state_kv` 231 行 = `state_history` 231 行；`data/archive/state_traces_*.tar.gz` 存在且 ≥1（2307 文件归档）；**P3-A 断言（2026-08-11）**：`python -m pytest tests/factor_engine/test_factor_optimizer.py` 51 passed（含 TestFactorSignalCacheParquet 5）；`PARQUET_CACHE_VERSION == 2`；storage_landscape signal_parquet 域 status=active；**P3-B 断言（2026-08-12）**：`python scripts/archive_history_cold.py --verify-only --until-year 2013` 返回 0（cold_rows=44134、hot_remaining=0、consistent=true）；`data/archive/history_kline_cache_{2005..2013}.parquet` 存在（9 文件 1.22MB）；热库 kline_cache 剩余 323,839 行（2014-2026）；`python -m pytest tests/scripts/test_archive_history_cold.py` 7 passed；**P4 读路径切换断言（2026-08-12）**：`get_dynamic_core_subset` 优先读 `state.duckdb`（SSOT）→ JSON → 静态池三级；`fts factor list/show` 默认 DuckDB 查询、`--elite-dir` 目录仅回退；`state.py/portfolio_loop/meta_loop/extractors` 状态读写走 `StateKVStore`（无 state.json 冷启动）；定向回归 store 24 + migrate_state 8 + archive_history 7 + factor_optimizer 51 + dynamic_pool 10 + cli 264 + 状态套件 634 全绿；ruff/mypy 全绿 |
| 检验方式 | `python scripts/verify_doc_consistency.py`；各阶段落地时 `pytest tests/store/ -v` 定向回归（日常分级测试政策：模块定向，不跑全量） |
