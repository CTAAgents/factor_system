# FTS 因子管理优化实施计划


> 版本: v2.105.0+2

**版本**: v1.0  
**创建日期**: 2026-08-05  
**目标**: 种子因子数据驱动化 + Elite 因子数据库化，支撑因子规模从 563 → 5000+  
**约束**: 保持 API 向后兼容，分阶段灰度迁移，JSON/YAML 双路径兜底  
**状态**: Phase 0 进行中

---

## 总体进度

| Phase | 名称 | 状态 | 进度 | 产出物 | 验收标准 |
|-------|------|------|------|--------|----------|
| **Phase 0** | 准备工作 | 🔄 进行中 | 0% | 现状分析 + 数据模型 + 迁移脚本设计 | 模型评审通过 |
| **Phase 1** | 种子 YAML 化 | ⏳ 待启动 | 0% | YAML 种子库 + SeedLoader + 迁移脚本 | 563 种子全量加载 + 与硬编码逻辑等价 |
| **Phase 2** | Elite DuckDB 化 | ⏳ 待启动 | 0% | factor_catalog 表 + FactorRepository + 迁移脚本 | 300+ JSON 全量导入 + SQL 查询验证 |
| **Phase 3** | 读写链路改造 | ⏳ 待启动 | 0% | 双路径读取 + 写入改造 + 回归测试 | 全量测试通过 + 端到端演化/组合可运行 |

**总测试目标**: 新增 80+ 测试，全量 709+ 测试通过  
**新增文件**: 预计 15-20 个（含种子数据文件）

---

## Phase 0: 准备工作

### 0.1 现状分析

#### 种子因子现状

| 维度 | 现状 | 问题 |
|------|------|------|
| 存储形式 | Python 代码字符串硬编码 | 单文件膨胀 > 2000 行 |
| 修改方式 | 手动编辑 .py 文件 | 易引入语法错误 |
| 新增因子 | 需改 Python + 代码生成模板 | 非开发者无法贡献 |
| 测试方式 | 通过 Mock 加载 | 难以单测单个因子 |
| 版本管理 | 随 Python 文件 git 提交 | 因子版本与代码版本耦合 |

**涉及文件**:
- [seed_pool.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_pool.py#L48-L382) — 9 个股票内置种子
- [seed_data_futures_full.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_data_futures_full.py) — 81 个期货种子（~2000 行）
- [seed_data/wq101.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_data/wq101.py) — 101 个 WQ Alpha
- [seed_data/qlib158.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_data/qlib158.py) — 158 个 Qlib
- [seed_data/gtja191.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_data/gtja191.py) — 191 个国泰君安
- [seed_data/fundamental_seeds.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_data/fundamental_seeds.py) — 23 个基本面

#### Elite 因子现状

| 维度 | 现状 | 问题 |
|------|------|------|
| 存储形式 | 单文件 JSON（300+ 文件） | 文件系统遍历 O(n) |
| 查询方式 | 全量加载 + 内存过滤 | 无法按条件筛选 |
| 去重方式 | 文件名 hash | 内容去重不支持 |
| 并发安全 | 无锁机制 | 读写可能冲突 |
| 版本历史 | 无 | 无法回溯演化谱系 |
| 备份方式 | 逐文件拷贝 | 操作繁琐 |

**涉及目录**: [memory/knowledge/factors/stocks_elite/](file:///d:/Programs/factor_system/memory/knowledge/factors/stocks_elite)

**涉及文件**:
- [evolution_loop.py#L480-L520](file:///d:/Programs/factor_system/fts/factor_engine/evolution_loop.py#L480-L520) — Elite 写入
- [portfolio_loop.py#L892-L948](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py#L892-L948) — Elite 读取

### 0.2 数据模型设计

#### 种子因子 YAML Schema

```yaml
# seeds/futures/momentum.yaml
family: momentum          # 因子家族
version: "1.0"            # 版本号
market: futures           # 适用市场
factors:
  - name: fut_xsmom       # 因子唯一名称
    description: 截面动量因子
    params:
      lookback: 20
      holding: 1
    input_fields: [close]
    lookback: 25
    output_type: signal
    frequency: daily
    economic_logic:
      theory: 4
      behavioral: 3
      microstructure: 3
      institutional: 3
      narrative: "截面动量：过去J日收益率"
    code: |
      def factor_program(data, params):
          import numpy as np
          close = data['close'].values if hasattr(data, 'close') else data['close']
          n = len(close)
          j = int(params.get('lookback', 20))
          ...
          return np.clip(sig, -1.0, 1.0)
```

#### Elite 因子 DuckDB Schema

```sql
CREATE TABLE IF NOT EXISTS factor_catalog (
    -- 主键与标识
    factor_id        VARCHAR(16) PRIMARY KEY,    -- fct_xxxxxxxx
    name             VARCHAR(128) NOT NULL,
    family           VARCHAR(64),                -- 动量/期限结构/流动性/...
    source           VARCHAR(32) NOT NULL,        -- seed/evolved/bootstrapping
    market           VARCHAR(16) DEFAULT 'futures',
    generation       INTEGER DEFAULT 0,
    parent_id        VARCHAR(16),                -- 演化谱系
    code_hash        VARCHAR(64),                -- SHA256 去重

    -- 因子程序
    code             TEXT NOT NULL,
    params           JSON,
    input_fields     JSON,
    lookback         INTEGER,
    output_type      VARCHAR(16) DEFAULT 'signal',
    frequency        VARCHAR(16) DEFAULT 'daily',

    -- 经济逻辑
    logic_theory        TINYINT,
    logic_behavioral    TINYINT,
    logic_microstructure TINYINT,
    logic_institutional TINYINT,
    logic_narrative     TEXT,

    -- 评估指标（L2）
    ic                 DOUBLE,
    icir               DOUBLE,
    sharpe             DOUBLE,
    max_drawdown        DOUBLE,
    turnover_monthly    DOUBLE,
    monotonicity       BOOLEAN,
    oos_ratio          DOUBLE,
    t_stat             DOUBLE,
    passed             BOOLEAN DEFAULT TRUE,
    failure_reasons    JSON,

    -- 相关性元数据
    corr_flags         JSON,

    -- 时间戳
    created_at         TIMESTAMP,
    updated_at         TIMESTAMP
);

-- 索引（按查询模式设计）
CREATE INDEX IF NOT EXISTS idx_factor_family   ON factor_catalog(family);
CREATE INDEX IF NOT EXISTS idx_factor_source   ON factor_catalog(source);
CREATE INDEX IF NOT EXISTS idx_factor_sharpe   ON factor_catalog(sharpe DESC);
CREATE INDEX IF NOT EXISTS idx_factor_ic       ON factor_catalog(ic DESC);
CREATE INDEX IF NOT EXISTS idx_factor_code_hash ON factor_catalog(code_hash);
CREATE INDEX IF NOT EXISTS idx_factor_market   ON factor_catalog(market);
CREATE INDEX IF NOT EXISTS idx_factor_passed   ON factor_catalog(passed);

-- 版本历史表
CREATE TABLE IF NOT EXISTS factor_versions (
    version_id    INTEGER PRIMARY KEY,
    factor_id     VARCHAR(16) REFERENCES factor_catalog(factor_id),
    version       VARCHAR(32),
    generation    INTEGER,
    change_type   VARCHAR(32),   -- creation/mutation/crossover/evaluation
    parent_id     VARCHAR(16),
    code          TEXT,
    sharpe        DOUBLE,
    ic            DOUBLE,
    note          TEXT,
    created_at    TIMESTAMP
);
```

### 0.3 迁移脚本设计

#### 种子迁移脚本

```
scripts/migrate_seeds_to_yaml.py
  ├── Task 1: 解析 seed_pool.py 中 _SEED_DEFINITIONS → seeds/stock/builtin.yaml
  ├── Task 2: 解析 seed_data_futures_full.py 81 因子 → seeds/futures/*.yaml（按家族拆分）
  ├── Task 3: 解析 seed_data/wq101.py → seeds/stock/wq101.yaml
  ├── Task 4: 解析 seed_data/qlib158.py → seeds/stock/qlib158.yaml
  ├── Task 5: 解析 seed_data/gtja191.py → seeds/stock/gtja191.yaml
  ├── Task 6: 解析 seed_data/fundamental_seeds.py → seeds/stock/fundamental.yaml
  └── Task 7: 验证 YAML 种子与原硬编码等价（执行结果 diff）
```

#### Elite 迁移脚本

```
scripts/migrate_elite_to_db.py
  ├── Task 1: 连接 DuckDB（复用 data/fts_history.duckdb 或新建 data/factor_catalog.duckdb）
  ├── Task 2: 创建 factor_catalog 表 + 索引
  ├── Task 3: 扫描 memory/knowledge/factors/stocks_elite/*.json
  ├── Task 4: 解析 JSON → INSERT INTO factor_catalog
  ├── Task 5: 解析相关性元数据 → corr_flags JSON 字段
  ├── Task 6: 代码哈希去重（INSERT OR IGNORE ON code_hash）
  ├── Task 7: 验证迁移完整性（SELECT COUNT + 抽样对比）
  └── Task 8: 生成迁移报告（成功数/跳过数/失败数）
```

### 0.4 验收标准

- [x] 数据模型文档评审通过
- [x] 迁移脚本设计评审通过
- [x] 与现有 DuckDB 实例兼容性确认（复用 vs 新建）
- [x] 双路径读取策略评审通过

---

## Phase 1: 种子因子 YAML 化

### 目标
将 563 个种子因子从 Python 硬编码迁移到 YAML 数据文件，实现数据驱动加载。

### 实施任务清单

#### Task 1.1: 创建种子数据目录结构

- [ ] 创建 `seeds/` 根目录
- [ ] 创建 `seeds/futures/` 子目录
- [ ] 创建 `seeds/stock/` 子目录
- [ ] 创建 `seeds/README.md` 说明文件（格式规范 + 添加新因子指南）

#### Task 1.2: 编写迁移脚本

- [ ] 创建 `scripts/migrate_seeds_to_yaml.py`
- [ ] 实现 Python AST 解析器，从 `_SEED_DEFINITIONS` 提取因子定义
- [ ] 实现因子代码提取（去除变量赋值，保留 `factor_program` 函数）
- [ ] 实现 YAML 输出（按家族分组）
- [ ] 实现等价性验证（加载 YAML 种子 vs 原硬编码种子，执行结果 diff）

#### Task 1.3: 迁移期货因子（81 个）

- [ ] 解析 `seed_data_futures_full.py`
- [ ] 按 14 大家族拆分 YAML 文件：
  - [ ] `seeds/futures/momentum.yaml` (5 因子)
  - [ ] `seeds/futures/term_structure.yaml` (3 因子)
  - [ ] `seeds/futures/position_flow.yaml` (3 因子)
  - [ ] `seeds/futures/liquidity.yaml` (3 因子)
  - [ ] `seeds/futures/higher_moments.yaml` (3 因子)
  - [ ] `seeds/futures/volatility.yaml` (2 因子)
  - [ ] `seeds/futures/fundamental.yaml` (4 因子)
  - [ ] `seeds/futures/crowding.yaml` (6 因子)
  - [ ] `seeds/futures/alpha_behavior.yaml` (4 因子)
  - [ ] `seeds/futures/high_frequency.yaml` (6 因子)
  - [ ] `seeds/futures/options.yaml` (3 因子)
  - [ ] `seeds/futures/market_regime.yaml` (8 因子)
  - [ ] `seeds/futures/cta_registry.yaml` (7 因子)
  - [ ] `seeds/futures/operator_dict.yaml` (24 因子)
- [ ] 验证每个因子 YAML 可正确加载执行

#### Task 1.4: 迁移股票因子（9 + 473 个）

- [ ] 解析 `seed_pool.py` 内置种子 → `seeds/stock/builtin.yaml` (9 因子)
- [ ] 解析 `wq101.py` → `seeds/stock/wq101.yaml` (101 因子)
- [ ] 解析 `qlib158.py` → `seeds/stock/qlib158.yaml` (158 因子)
- [ ] 解析 `gtja191.py` → `seeds/stock/gtja191.yaml` (191 因子)
- [ ] 解析 `fundamental_seeds.py` → `seeds/stock/fundamental.yaml` (23 因子)

#### Task 1.5: 实现 SeedLoader（双路径读取）

- [ ] 创建 `fts/factor_engine/seed_loader.py`
- [ ] 实现 `load_from_yaml(path)` — 从 YAML 文件加载因子定义
- [ ] 实现 `load_from_dir(dir_path)` — 批量加载目录下所有 YAML
- [ ] 实现 `factor_def_to_program(def)` — YAML 定义 → FactorProgram 转换
- [ ] 保留原硬编码路径作为 fallback（`--use-hardcoded` 开关）
- [ ] 在 SeedPool 中集成新加载器

#### Task 1.6: 更新 SeedPool 加载逻辑

- [ ] 修改 `SeedPool.load_all_seeds()` 优先从 YAML 加载
- [ ] 添加 fallback 机制：YAML 加载失败 → 回退到硬编码
- [ ] 保持对外 API 不变

#### Task 1.7: 测试与验证

- [ ] 创建 `tests/factor_engine/test_seed_loader.py`
- [ ] 测试 YAML 加载正确性（14 个测试用例）
- [ ] 测试双路径一致性（YAML vs 硬编码结果等价）
- [ ] 测试 fallback 机制（YAML 缺失时回退）
- [ ] 测试边界条件（空文件/格式错误/代码异常）
- [ ] 全量种子执行结果 diff 验证

### 验收标准

- [ ] 563 个种子因子全量从 YAML 加载成功
- [ ] YAML 加载结果与原硬编码执行结果完全一致
- [ ] 新增因子只需编辑 YAML 文件，无需修改 Python
- [ ] 双路径 fallback 机制正常工作
- [ ] 单元测试 ≥ 14 项全通过
- [ ] 不修改现有对外 API

---

## Phase 2: Elite 因子 DuckDB 化

### 目标
将 300+ Elite 因子从 JSON 文件迁移到 DuckDB 数据库，支持 SQL 查询、去重、版本管理。

### 实施任务清单

#### Task 2.1: 设计并创建数据库层

- [ ] 创建 `fts/factor_engine/factor_repository.py`
- [ ] 实现 `FactorRepository` 类（DuckDB 操作封装）
- [ ] `connect(db_path)` — 连接/创建 DuckDB 实例
- [ ] `init_schema()` — 创建 factor_catalog + factor_versions 表
- [ ] 复用现有 DuckDB 实例（`data/fts_history.duckdb`）或独立数据库（`data/factor_catalog.duckdb`）

#### Task 2.2: 实现核心 CRUD 操作

- [ ] `insert_factor(factor)` — 插入因子（`INSERT OR IGNORE ON code_hash`）
- [ ] `get_by_id(factor_id)` — 按 ID 查询
- [ ] `get_all(market, source, passed)` — 条件查询
- [ ] `get_top_n_by_sharpe(n, market)` — 按 Sharpe 排序取前 N
- [ ] `get_families(market)` — 获取所有因子家族及计数
- [ ] `update_factor(factor_id, updates)` — 更新因子评估结果
- [ ] `delete_factor(factor_id)` — 删除因子
- [ ] `count(filters)` — 统计查询
- [ ] `detect_duplicates()` — 代码哈希重复检测

#### Task 2.3: 迁移现有 JSON 因子

- [ ] 创建 `scripts/migrate_elite_to_db.py`
- [ ] 扫描 `memory/knowledge/factors/stocks_elite/*.json`
- [ ] 解析 JSON → factor_catalog 记录
- [ ] 计算 `code_hash`（SHA256）用于去重
- [ ] 处理 `correlation_metadata` → `corr_flags` JSON 字段
- [ ] 导入 `_l2_seed_correlation_index.json` → 版本历史
- [ ] 生成迁移报告（成功/跳过/失败统计）

#### Task 2.4: 实现版本历史追踪

- [ ] `insert_version(factor_id, generation, change_type, parent_id, code, metrics)`
- [ ] `get_version_history(factor_id)` — 获取因子完整演化谱系
- [ ] `get_lineage(factor_id)` — 追溯因子所有祖先

#### Task 2.5: 测试与验证

- [ ] 创建 `tests/factor_engine/test_factor_repository.py`
- [ ] 测试 CRUD 操作正确性（15 个测试用例）
- [ ] 测试 SQL 查询条件过滤
- [ ] 测试代码哈希去重
- [ ] 测试版本历史查询
- [ ] 测试数据完整性（JSON vs DB 字段映射）
- [ ] 性能测试（300+ 因子查询 < 100ms）

### 验收标准

- [ ] 300+ JSON 因子全量导入 DuckDB
- [ ] `code_hash` 去重机制正确工作
- [ ] SQL 查询支持按 family/source/sharpe/ic 等条件筛选
- [ ] 版本历史可追溯因子演化谱系
- [ ] 单元测试 ≥ 15 项全通过
- [ ] 查询性能满足要求（300 因子全表扫描 < 100ms）

---

## Phase 3: 读写链路改造

### 目标
将演化/组合/注入等所有因子读写链路切换到新的 YAML + DuckDB 存储，保留 JSON 兜底路径。

### 实施任务清单

#### Task 3.1: 读取链路改造

- [ ] 改造 [portfolio_loop.py#L892-L948](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py#L892-L948) `load_elite_factors()` — 优先从 DuckDB 读取
- [ ] 改造 `load_l2_correlation_index()` — 从 DuckDB 查询
- [ ] 添加 fallback：DuckDB 读取失败 → 回退到 JSON 文件读取
- [ ] 改造 [meta_loop.py#L277-L352](file:///d:/Programs/factor_system/fts/factor_engine/meta_loop.py#L277-L352) `FactorPoolManager` — 同步 DuckDB

#### Task 3.2: 写入链路改造

- [ ] 改造 [evolution_loop.py#L480-L540](file:///d:/Programs/factor_system/fts/factor_engine/evolution_loop.py#L480-L540) Elite 保存逻辑
- [ ] 因子通过评估后写入 DuckDB `factor_catalog`
- [ ] 同时保留 JSON 写入作为备份
- [ ] 改造 L1 注入逻辑 — `inject_from_l1()` 同时写入 DuckDB
- [ ] 版本历史自动追加到 `factor_versions`

#### Task 3.3: CLI 命令改造

- [ ] `fts factor list` — 支持 SQL 过滤（`--family`, `--source`, `--min-sharpe`）
- [ ] `fts factor show <id>` — 展示因子详情（含版本历史）
- [ ] `fts factor migrate` — 执行迁移脚本入口
- [ ] `fts factor stats` — 统计报告（因子家族分布、Sharpe 分布等）

#### Task 3.4: 查询 API 增强

- [ ] `FactorRepository.get_by_family(family, market)` — 按家族查询
- [ ] `FactorRepository.get_eligible(market, min_ic, min_sharpe)` — 筛选合格因子
- [ ] `FactorRepository.get_diverse_factors(market, max_per_family)` — 因子多样性选择
- [ ] `FactorRepository.get_factor_lineage(factor_id)` — 演化谱系可视化

#### Task 3.5: 回归测试

- [ ] 创建 `tests/factor_engine/test_dual_path.py` — 双路径一致性测试
- [ ] 测试端到端演化流程（L1→L2→L3）
- [ ] 测试端到端组合构建流程
- [ ] 测试 CLI 新命令
- [ ] 全量回归：现有 709 测试 + 新增 30 测试全通过

### 验收标准

- [ ] 演化流程可从 YAML 种子启动
- [ ] Elite 因子正确写入 DuckDB
- [ ] 组合构建可从 DuckDB 加载因子
- [ ] JSON fallback 正常工作
- [ ] 全量回归测试通过（709 + 30 ≥ 739）
- [ ] CLI 新命令可用

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| YAML 迁移遗漏因子 | 中 | 高 | 迁移脚本含全量校验（数量 + name 集合对比） |
| DuckDB 数据损坏 | 低 | 高 | 保留 JSON 文件作为备份，双路径读取 |
| 性能下降 | 低 | 中 | DuckDB 列式存储 + 索引，查询优于 JSON 全量加载 |
| 因子代码兼容问题 | 中 | 高 | Phase 1 每个因子执行结果 diff 验证 |
| 演化流程中断 | 低 | 高 | 先在测试环境验证，确认稳定后再生产部署 |

---

## 差距登记（待 Phase 0 完成后正式登记）

| ID | 优先级 | 描述 | 关联 Phase |
|----|--------|------|------------|
| GAP-018 | P1 | 种子因子硬编码导致文件膨胀、修改困难 | Phase 1 |
| GAP-019 | P1 | Elite 因子 JSON 文件无索引、查询慢、去重弱 | Phase 2 |
| GAP-020 | P2 | 因子演化无版本历史，无法追溯谱系 | Phase 2 |
| GAP-021 | P2 | 因子管理无数据血缘，无法审计因子来源 | Phase 3 |

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-08-05 | 创建实施计划文档（Phase 0-3 全阶段设计） |

## 一致性元数据

| 代码位置 | 文档位置 | 验证方式 |
|----------|----------|----------|
| `fts/factor_engine/seed_pool.py` | 本文档 §Phase 1 | 种子加载逻辑对照 |
| `fts/factor_engine/portfolio_loop.py#L892-L948` | 本文档 §Phase 2 | Elite 读取逻辑对照 |
| `fts/factor_engine/evolution_loop.py#L480-L540` | 本文档 §Phase 3 | Elite 写入逻辑对照 |
| `../08-gap-analysis.md` | 本文档 §风险 | 差距编号交叉引用 |