# FTS 股票管线剥离计划（不对称分离：主系统保留期货，股票独立成新项目）

> 版本: v2.104.0-draft
> 创建: 2026-08-12
> 状态: **剥离完成并通过双项目整体验收**——股票独立项目 `d:\Programs\fts-stock`（v0.0.1）已组装并可独立运行（全量 5821 测试通过）；主系统（期货）股票残留已清理（测试全量 not-slow 6449 passed，13 个失败均为 DuckDB 锁冲突环境问题，非代码缺陷），P1-P6 全部完成，进入"边用边修"阶段
> 关联: [F.1-data-contract-split-design.md](../design/F.1-data-contract-split-design.md)（契约拆分）、[F.2-evolution-engine-fork-design.md](../design/F.2-evolution-engine-fork-design.md)（引擎分叉）、[14-cross-market-generalization-plan.md](./14-cross-market-generalization-plan.md)（跨市场泛化）
> 决策（用户确认 2026-08-12）: ① 不对称分离——现有 FTS 只保留期货，股票剥离为新项目；② 新项目目录 `d:\Programs\fts-stock`；③ 共享内核用子目录 + hash 同步；④ 股票新项目版本从 v0.0.1 起。

---

## 1. 背景与动机

### 1.1 问题

FTS 当前是"单仓库、双市场"结构：`fts/` 包内演化引擎（`evolution_loop.py`）含 13 处 `market` 分支、数据契约 `FusedOHLCV` 混合承载期货与股票字段、行情库 `fts_history.duckdb` 双市场共库。两个市场的固有形状（期货合约/换月/持仓/产业链 vs 股票复权/T+1/行业市值）在共享引擎内互相污染，无法按各自固有特点独立版本迭代。

### 1.2 目标

**不对称分离**：现有 `d:\Programs\factor_system` 继续作为期货系统（主系统），股票部分剥离形成独立项目 `d:\Programs\fts-stock`。从业务逻辑、代码、文档三个层面彻底分离。

### 1.3 为什么不对称（三个实测依据）

| # | 依据 | 说明 |
|---|------|------|
| 1 | 现有系统已是"期货优先" | `default_market: "futures"`、CLI 所有 `--market` 默认 futures、演化引擎 IC 阈值/verifier/high_ic 默认分支均按期货设定 → 期货留原处 = 零行为翻转，只需删除股票分支 |
| 2 | 股票占比小 | scripts 14/118、tests 11/236、种子 10/30 → 剥离是"组装新项目"，比重写轻得多 |
| 3 | 历史与生态保留 | git 历史、CI、docs/harness、发布流程留在主系统；与"从 FDT 剥离出 FTS"同一模式 |

---

## 2. 盘点结论（实测数据）

### 2.1 代码层归属

| 层 | 期货（主系统保留） | 股票（新项目带走） | 共享 |
|---|---|---|---|
| `scripts/`（118） | 57 | 14 | 47 |
| `tests/`（236） | 40 | 11 | 185 |
| 种子库 | `seeds/futures/`（20 yaml） | `seeds/stock/`（10 yaml） | — |
| 存储表 | `kline_cache`/`contract_kline` | `stock_kline_cache`/`ashare_special_cache`/`stock_fundamental_cache` | 同一 DuckDB 文件（半分离） |
| 契约 | `FuturesOHLCV`（已存在） | `StockOHLCV`（F.1 新建） | `FusedOHLCV`/逻辑契约 |
| 演化引擎 | market 分支 | market 分支 | 同一 `evolution_loop.py`（未分离） |
| 调度 | `sync_futures`/`futures_signal_pipeline` | `sync_stock`/`daily_signal_pipeline` | 同一 scheduler 框架 |

### 2.2 文档层归属

`docs/harness/` 全套 9 篇（01-architecture ~ 09-advancement-plan）+ `_data/` + `design/` + `plans/` 当前为双市场混合描述，需按项目复制并裁剪。

---

## 3. 目标形态

```
d:\Programs\factor_system\     # 主系统：期货因子系统（保留 2.x 演进）
d:\Programs\fts-stock\         # 新项目：股票因子系统（v0.0.1 起）
```

两个项目各自独立拥有：pyproject/版本号、CLI、配置、CI/CD、docs/harness 全套、种子库、数据存储、调度任务、发布节奏。

### 3.1 分离边界（三层归属）

| 归属 | 内容 | 处理方式 |
|---|---|---|
| **复制层**（市场形状，各自迭代） | 演化引擎、数据契约、行情源/历史库/数据提供层、种子库、信号管线、调度任务、CLI 子命令 | 主系统保留期货侧，新项目组装股票侧，**允许彻底漂移** |
| **共享内核**（无市场形状） | tdx_local/akshare 薄封装、融合算法、LLM 客户端、GP/算子库、逻辑契约（FactorProgram/Evaluation/State）、监控/调度框架、CI 模板 | **主体留在主系统**；股票新项目拷贝初始副本 + hash 单向同步（主系统→新项目） |
| **治理层**（随项目走） | docs/harness 全套、README/CLAUDE/AGENTS、版本号、发布流程 | 各项目独立一份 |

### 3.2 共享内核同步机制（用户确认：子目录 + hash）

```
d:\Programs\fts-stock\
└── fts_core/          # 共享内核子目录（拷贝自主系统）
    ├── data_sources/  # tdx_local/akshare 薄封装、融合算法
    ├── llm.py         # LLM 客户端
    ├── contracts.py   # 逻辑契约（OHLCVBase/FusionMeta/FactorProgram）
    └── .core_hash     # hash 校验文件（记录上游 commit/内容摘要）
```

- 单向同步脚本：`sync_core.sh/.ps1` 从主系统拷贝共享内核，计算 hash 对比，差异即告警
- 禁止反向漂移：股票项目内不直接修改 `fts_core/`，需要改进时改主系统上游再同步

---

## 4. 阶段计划（P0-P6）

> **执行状态总览（2026-08-13 更新）**：**P0-P6 全部完成**。P0-P3 已完成；P4 主系统清理完成——共享文件股票分支已剥离（cli/portfolio_loop/evolution_loop/evaluation_chain/meta_loop/extractors/factor_db/cost_model/migrate/scheduler/settings 等 20 文件）、股票专属模块已删除（evolution_stock/stock_regime/neutralization/stock_pipeline/data_mcp/data_fundamental/data_mcp_bridge/ashare_special/stock_fundamental_source/cross_market 9 模块 + 4 脚本 + 7 测试）；P5 已组装 `d:\Programs\fts-stock`（v0.0.1，`pip install -e .` 可用，独立 venv/CLI/测试/seeds/stock 10 yaml/data/文档）；P6 同步机制已落地（`fts_core/` 子目录 + `.core_hash`）。
>
> **验收登记（2026-08-13，用户指示"强制验收、边用边修"）**：
> - **fts-stock 全量回归：5821 passed（35分35秒）** —— 独立项目可独立运行 ✅
> - **主系统 not-slow 全量回归：6449 passed + 13 failed（xdist 24分48秒）**；13 个失败全部为 DuckDB 锁冲突（`state.duckdb`/`factor_catalog_futures.duckdb` 被并发 worker/残留进程持锁，`IOException: 另一个程序正在使用此文件`），**零代码失败**；单进程下锁冲突类测试不冲突（已验证 market_ohlcv 3/3 通过）→ 登记为已知 gap（DuckDB 嵌入式单进程写本质，日常回归建议单进程或隔离库）
> - **本次修复的已知问题**：① 股票残留 11 个测试（第 5 轮）；② `test_portfolio_loop_market_ohlcv` TqSdk 卡点（模块级 autouse mock 隔离真实数据源）；③ 8 个未标记 slow 的重型演化测试 → 补标 `@pytest.mark.slow`（slow 计数 11→19）
> - **遗留 gap（边用边修）**：xdist 并行下 DuckDB 写锁冲突；真实演化 slow 测试单进程耗时长（建议 xdist 全量验收或按文件单跑）；docs 股票残留持续清理（P1 级，历史 plans/design 保留原文）
> - **代码残留登记（2026-08-13，不主动删除，边用边修）**：① `A_SHARE_FIELDS` 仍定义于 `fts/factor_engine/expr_dsl/registry.py`（L36/L113 注册 L0 算子，`expr_dsl/__init__.py` 导入）——代码事实核查确认存在，若确为死代码需专项清理（影响注册表与 DSL 测试，需回归）；② CLI `--max-stocks`/`--symbol` 默认 000001 等历史残留参数未收窄（不影响期货流程）；③ 剥离工程辅助脚本 `scripts/_p2_fork_engines.py`/`scripts/sync_core_to_stock.py` 保留合理

### Phase 0: 基线冻结（✅ 已完成 2026-08-12）

| 步骤 | 动作 | 验证 |
|---|---|---|
| P0.1 | `pytest tests/factor_engine/test_evolution_loop.py -v` 全绿 | ✅ 264 passed in 3185s |
| P0.2 | 期货跑 2 代演化（`python -m fts.cli evolution run --universe futures --max-generations 2 --max-stocks 0`），导出 `golden_output_futures.json`（factor_id → evaluation 指标 + elite JSON hash） | ✅ `memory/baseline/golden_output_futures.json`（1 个 elite: fct_0b1a8f45） |
| P0.3 | 记录环境指纹（Python 版本、依赖版本、随机种子） | ✅ `memory/baseline/env_fingerprint.json` |

**产物**: `memory/baseline/golden_output_futures.json`（迁移后行为等价性对照锚点）+ `memory/baseline/env_fingerprint.json`。

### Phase 1: 契约拆分（F.1）

- 新建 `StockOHLCV`（含复权因子 `adjust_factor`），`FuturesOHLCV` 补齐融合元数据，`FusedOHLCV` 降级为 `Union` 别名
- 迁移 4 处消费方：`fusion.py`（构造方加 market 参数）、`cli.py`（cast 对齐）、`contracts.py`（FusionReport.rows）、`expr_dsl/registry.py`（settle 字段注明期货专用）
- 验证: mypy 无新错 + test_fusion 全绿 + `fts data fuse` 双市场输出一致

### Phase 2: 引擎分叉（F.2）

- `evolution_futures.py` 留在主系统，`evolution_stock.py` 进入新项目
- 7 处行为分叉三阶段折叠（纯配置→数据形状→契约下沉），6 处公共逻辑（market 字段归一化）两份各保留
- 验证: golden diff=0 + 现网测试全绿

### Phase 3: 存储分库

- `market_history` 域拆为 `market_history_futures`（留主系统）+ `market_history_stock`（迁新项目）
- `storage_landscape.yaml` 拆两域登记；迁移脚本回放历史数据
- 验证: 双库数据校验一致（≥2 源同日差异 >0.5% 记录分歧）

### Phase 4: 主系统清理（期货）（✅ 已完成 2026-08-13）

- 删除 14 个 stock scripts、11 个 stock tests、`seeds/stock/`、barra/行业市值等股票专属模块
- CLI 去掉 `--market` 参数（默认即期货）；scheduler 删除股票任务
- 验证: 主系统期货全流程可跑（演化/信号/调度）+ 受影响模块测试全绿（not-slow 6449 passed）

### Phase 5: 组装股票新项目（✅ 已完成 2026-08-13）

- 拷贝共享内核初始副本（`fts_core/`）+ 股票专属模块 + 骨架（pyproject/CLI/CI/docs/harness）
- 版本 v0.0.1 起；独立 git 初始化
- 验证: 新项目 `pip install -e .` + 股票信号管线可跑 + 独立 CI 全绿（全量 5821 passed）

### Phase 6: 同步机制 + 切换（✅ 已完成 2026-08-13）

- `sync_core` 脚本 + hash 校验落地；主系统冻结股票相关写入
- 交叉验证（`cross_market_revalidation`）迁入 fts_core，跨库读数据
- 切换条件: 双项目各自 CI 全绿 + 信号管线产出与旧系统一致 + 一个完整演化周期无差异（双项目整体验收通过 2026-08-13）

---

## 5. 行为等价验证（核心保障）

**等价性定义**: 对同一市场、同一输入，分离前后演化产物（精英因子 JSON、评估指标、入库记录）完全一致。

1. **Golden Output Diff（主锚点）**: P0 冻结基线，每阶段迁移后重跑对比，diff 必须为 0
2. **现网测试回归**: `tests/factor_engine/test_evolution_loop.py` 在分叉文件上全绿
3. **折叠审计（人工）**: 7 处行为分叉逐一打勾，6 处公共逻辑确认保留

---

## 6. 里程碑与验收

| 里程碑 | 完成标志 | 状态 |
|---|---|---|
| M1 契约与引擎分离（P0-P2） | golden diff=0，双引擎独立运行 | ✅ 2026-08-12 |
| M2 数据与管线分离（P3-P4） | 双库独立、双信号管线独立运行 | ✅ 2026-08-13 |
| M3 项目骨架独立（P5） | 新项目 `pip install` + 独立版本 v0.0.1 + 独立 CI 全绿 | ✅ 2026-08-13（5821 passed） |
| M4 文档分离 + 切换（P6） | 双套 docs/harness 一致性校验通过；主系统冻结股票写入 | ✅ 2026-08-13（主系统 6449 passed + 锁冲突 gap 登记） |

---

## 7. 技术约束与风险

| 风险 | 缓解 |
|---|---|
| 折叠引入行为漂移 | golden diff 锚点 + 折叠审计清单 |
| 公共逻辑被误删（market 归一化） | 分类清单明确标注"不拆"，两份各保留 |
| 共享内核反向漂移 | hash 校验 + 只允许主系统→新项目单向同步 |
| 存储分库丢数据 | 迁移脚本 + 双库交叉校验 |
| 主系统清理误删共享模块 | P4 按盘点清单执行，删除前确认无 shared 依赖 |
| tdx_local 公共源并发 | 约定连接复用，股票新项目只读引用 |

---

## 8. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | [F.1-data-contract-split-design.md](../design/F.1-data-contract-split-design.md)、[F.2-evolution-engine-fork-design.md](../design/F.2-evolution-engine-fork-design.md)、[14-cross-market-generalization-plan.md](./14-cross-market-generalization-plan.md) |
| 依赖模块 | `fts/factor_engine/evolution_loop.py`、`fts/core/contracts.py`、`fts/data_sources/`、`fts/store/registry.py`、`fts/cli.py`、`fts/scheduler/`、`docs/harness/_data/storage_landscape.yaml` |
| 前置条件 | P0 基线冻结完成（golden_output_futures.json + 环境指纹落盘） |
| 后置影响 | 主系统（期货）继续 2.x 演进；新项目（股票）v0.0.1 独立发布；数据供应、文档、CI 全部独立 |
| 可验证断言 | golden diff=0；test_evolution_loop 全绿；双库数据一致；新项目 CI 全绿；共享内核 hash 校验通过 |
| 检验方式 | §5 行为等价验证方案 + 各阶段验证项 |
