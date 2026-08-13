# E.1 DuckDB 并发模型根治 — 详细技术设计（GAP-056）

> 版本: v2.104.0+12
> 关联: [08-gap-analysis.md](../08-gap-analysis.md) GAP-056、`fts/data_futures.py`（DuckDBConnection/AsyncWriteQueue/retry_on_conflict）、`fts/scheduler/`（多任务并发写入）
> 状态: **已实施（含分库方案扩展）**
> 定位: 数据基础设施层架构决策变更（单写者 + 多只读 + 批量导入），改动集中在 `fts/data_futures.py` 与配置层，不改动业务模块

---

## 1. 目标与范围

### 1.1 问题

DuckDB 是嵌入式 OLAP 引擎，设计上**单进程内单写多读**。FTS 当前通过 `retry_on_conflict`（写冲突重试）、`AsyncWriteQueue`（单进程内串行化写）、`DuckDBConnection` + `SET lock_configuration = true` 缓解并发写冲突，但存在三类遗留问题：

| # | 问题 | 表现 |
|:--|:-----|:-----|
| P0 | 跨进程并发写 | `scripts/` 脚本与调度器 job 并行时对同一 `.duckdb` 文件抢文件锁，`ConcurrentTransactionException` 重试放大延迟，极端下失败 |
| P0 | 写时读阻塞 | checkpoint/写事务期间只读查询被文件锁阻塞，回测/因子加载路径出现随机卡顿 |
| P1 | 重试治标 | 重试是概率规避而非结构消除，高写入负载下冲突频率上升，重试次数耗尽仍抛错 |

### 1.2 目标

| 指标 | 现状 | 目标 |
|:-----|:-----|:-----|
| 进程内写路径数 | 多路径（各模块直接调 `_get_db()` 写） | 收敛为**单写者**（writer 层统一入口） |
| 读连接形态 | 读走共享可写连接 | 显式 `read_only=True`，与写连接解耦 |
| 跨进程写 | 无约束，任一方可写 | 全部写入汇聚到调度器单一写 job / IPC 转发 |
| 高频写入 | 逐条 INSERT | 批量 `COPY` + 调大 commit 粒度 |
| 冲突异常 | 靠重试兜底 | 结构上消除（单写者 + 只读分离后冲突不可达） |

### 1.3 范围

- `fts/data_futures.py`：新增/重构 `DuckDBWriter`（单写者入口，进程内写锁 + 批量提交）；`_get_db()` 拆分读写路径（读 `read_only=True`）
- `fts/config/settings.py`：新增并发模型配置项（见 §4）
- 调度器：写入类 job 串行化约束（`scheduler/jobs.py`）
- 测试与文档同步（design/01-architecture/03-configuration/04-resilience/06-testing/07-operations/08-gap-analysis/09-advancement-plan + pyproject + README）

### 1.4 不在范围

- 存储分层重构（方案 C：SQLite/PostgreSQL 替换）——改动大、链路复杂化，FTS 当前阶段不引入
- 分布式 DuckDB（motherduck/delta）——远期
- 业务模块内 SQL 语句重写（不改变查询逻辑，仅改连接形态）

---

## 2. 架构设计

### 2.1 总体架构（单写者 + 多只读）

```
                        ┌────────────────────────────────────────────┐
                        │              DuckDB 文件（.duckdb）          │
                        └──────────────┬─────────────────────────────┘
                                       │
              ┌────────────────────────┴─────────────────────────┐
              │                                                  │
     ┌────────▼─────────┐                              ┌─────────▼──────────┐
     │  WRITER（唯一可写）│                              │   READER（只读）     │
     │  DuckDBWriter     │                              │  read_only=True     │
     │  - 进程内写锁      │                              │  - 回测引擎          │
     │  - 批量 COPY      │                              │  - 因子加载          │
     │  - 串行提交        │                              │  - CLI/报告查询      │
     └────────┬─────────┘                              └────────────────────┘
              │
   ┌──────────┴───────────┐
   │   写入来源汇聚        │
   │  - 调度器写 job（唯一）│
   │  - 数据同步脚本（受限） │
   │  - IPC/队列转发（可选） │
   └──────────────────────┘
```

核心约束：
1. **每库单写者**：对同一个 `.duckdb` 文件，任意时刻至多一个可写连接。进程内由写锁保证；跨进程由"写 job 单一化 + 脚本受限"保证
2. **读写分离**：所有读路径显式 `read_only=True`，与写连接互不阻塞（DuckDB 支持同进程单写连接 + 多只读连接）
3. **批量写入**：高频写入（kline_cache/tick）批量 `COPY`，降低 commit/checkpoint 频率

### 2.2 进程内写锁设计

`DuckDBWriter` 持有一个 `threading.Lock`，所有写操作（`execute`/`executemany`/`copy`）在锁内执行。锁的粒度是**整个写事务**（`BEGIN ... COMMIT` 包裹），避免锁内多次提交造成读中断。

```python
class DuckDBWriter:
    def __init__(self, path: str, batch_size: int = 1000, commit_every: int = 100):
        self._lock = threading.Lock()
        self._conn = duckdb.connect(path)          # 唯一可写连接
        self._batch_buffer: list = []              # 批量缓冲

    def write(self, sql: str, params: Optional[list] = None) -> None:
        with self._lock:
            # BEGIN ... EXECUTE ... COMMIT 整事务串行
            ...

    def copy_from_records(self, table: str, records: list, columns: list) -> None:
        with self._lock:
            # 批量 COPY，整批一次提交
            ...
```

### 2.3 跨进程约束

- **调度器**：写入类 job（`sync_futures_data_job`/`data_level_monitor_job` 等）登记为"库级串行"，调度引擎按 `duckdb_path` 维度串行调度（同库 job 不并行）
- **脚本**：`scripts/` 写入型脚本统一改用 writer 入口；`--readonly` 显式标注只读脚本直接走 read_only 连接
- **IPC 转发（可选，v2 阶段）**：跨进程写需求集中时，通过本地队列（文件/Unix socket）转发到写 job，脚本侧不直接写库

### 2.4 分库扩展（Phase 2）

为解决跨市场文件锁竞争，按市场（股票/期货）拆分 DuckDB 文件，实现物理隔离：

```
┌──────────────────────────────────────────────────┐
│               因子目录数据库                        │
│  ┌─────────────────────┐  ┌─────────────────────┐ │
│  │ factor_catalog_stock│  │factor_catalog_futures│ │
│  │  .duckdb            │  │  .duckdb             │ │
│  │                     │  │                      │ │
│  │ market='stock'      │  │ market='futures'     │ │
│  │ market='multi'      │  │ market='multi'       │ │
│  └──────────┬──────────┘  └──────────┬───────────┘ │
│             │                        │              │
│      ┌──────▼──────┐          ┌──────▼──────┐      │
│      │ 股票因子管道  │          │ 期货因子管道  │      │
│      │ (L2/L3)      │          │ (L2/L3)      │      │
│      └─────────────┘          └─────────────┘      │
└──────────────────────────────────────────────────┘
```

### 2.4.1 路由规则

| 市场 | 数据库文件 | 写入方 | 读取方 |
|:-----|:-----------|:-------|:-------|
| `stock` | `factor_catalog_stock.duckdb` | 股票 L2 演化、股票 L3 组合 | 股票因子加载、CLI 查询 |
| `futures` | `factor_catalog_futures.duckdb` | 期货 L2 演化、期货 L3 组合 | 期货因子加载、CLI 查询 |
| `multi`（通用因子） | 同时写入两个库 | 种子因子、通用因子 | 按市场读取 |

### 2.4.2 实现细节

- `fts/factor_engine/factor_db/schema.py`：新增 `DATABASE_PATH_STOCK`/`DATABASE_PATH_FUTURES` 常量 + `get_db_path(market)` 路由函数
- `fts/factor_engine/factor_db/repository.py`：`FactorRepository`/`FactorQualityScoreRepository`/`FactorStatusRepository`/`FactorAuditReportRepository` 四类构造器均新增 `market` 参数（默认 `"stock"`），通过 `get_db_path(market)` 解析对应路径
- `fts/factor_engine/evolution_loop.py`/`portfolio_loop.py`/`cli.py`/`lineage.py`：10+ 个调用方按市场传递 `market` 参数
- 通用因子（`market='multi'`）写入时两库各写一份，确保跨市场可用性

### 2.4.3 迁移

- `scripts/migrate_factor_catalog_split.py`：将 `factor_catalog.duckdb` 按 `market` 字段拆分为 `factor_catalog_stock.duckdb`（`market='stock' + 'multi'`）和 `factor_catalog_futures.duckdb`（`market='futures' + 'multi'`），关联表（`factor_versions`/`factor_correlations`/`factor_evaluations`/`factor_status_history`/`factor_quality_scores`/`factor_audit_reports`/`factor_reviews`）按因子 ID 归属同步拆分
- `scripts/verify_split.py`：验证分库后数据完整性，检查两库记录数之和与原始库一致、因子元数据完整、关联表行数正确
- `scripts/concurrent_test.py`：并发压力测试，验证分库后股票与期货管道可同时读写各自数据库，无跨库文件锁冲突

### 2.4.4 与 GAP-056 的组合效果

| 场景 | GAP-056 单写者 | 分库扩展 | 组合效果 |
|:-----|:---------------|:---------|:---------|
| 同市场多线程写 | 单写者 + 写锁串行化 | — | 结构消除 `ConcurrentTransactionException` |
| 同市场写时读 | 读写分离 + MVCC | — | 写不阻塞读 |
| 跨市场并发写（股票 vs 期货） | 跨文件锁竞争仍存在 | 物理隔离至独立文件 | 完全消除跨市场锁冲突 |
| 跨市场写时读 | 单写者 + 读池 | 分库隔离 | 双重保障，零阻塞 |

## 2.5 批量写入

高频数据（kline_cache 分钟/tick）改 `copy_from_records`：
- 缓冲至 `batch_size`（默认 1000）或 `commit_every` 周期后 `COPY` + 单次 commit
- 相比逐条 INSERT，commit 频率降低 2~3 个数量级，checkpoint 阻塞读的概率大幅下降

---

## 3. 详细设计

### 3.1 现有实现处置

| 现有组件 | 处置 |
|:--------|:-----|
| `retry_on_conflict` 装饰器 | **保留**作最后兜底（防御性，不依赖它解决并发）；单写者 + 只读分离后冲突理论上不可达 |
| `AsyncWriteQueue` | **保留**用于异步协程写入路径，但底层改为经 `DuckDBWriter` 串行执行（消除双队列） |
| `DuckDBConnection` + `lock_configuration=true` | **保留**写连接侧；新增独立 `read_only=True` 读连接池 |
| `_get_db()` 模块级单例 | **重构**：`_get_reader()`（read_only，多连接池）+ `_get_writer()`（单写者单例），现有调用方按读写语义迁移 |

### 3.2 读连接池

```python
class DuckDBReader:
    """只读连接池 — 读操作与写连接解耦，互不阻塞。"""
    def __init__(self, path: str, max_connections: int = 4):
        self._pool: list[duckdb.DuckDBPyConnection] = []
        self._lock = threading.Lock()

    def acquire(self) -> Any:
        with self._lock:
            if self._pool:
                return self._pool.pop()
            return duckdb.connect(self._path, read_only=True)   # 显式只读

    def release(self, conn: Any) -> None:
        with self._lock:
            if len(self._pool) < self._max_connections:
                self._pool.append(conn)
            else:
                conn.close()
```

> 注意：DuckDB 的 `read_only=True` 仅在**无其他连接持有写锁时**可打开。同进程单写者连接 + 只读连接共存依赖 DuckDB 1.1+ 的 `lock_configuration=true`（已启用），跨进程场景由 §2.3 约束保证。

### 3.3 迁移规则

调用方按语义迁移（外科手术式，不改业务逻辑）：

| 当前调用 | 迁移为 | 示例 |
|:---------|:-------|:-----|
| `_get_db().execute("SELECT ...")` | `_get_reader()` | 回测、因子加载、catalog 查询 |
| `_get_db().execute("INSERT/UPDATE ...")` | `_get_writer().write(...)` | 晋升写库、数据同步 |

---

## 4. 配置设计

`fts/config/settings.py` `FTSConfig` 新增（Pydantic V2 + env 覆盖）：

| 配置项 | 默认 | 说明 |
|:-------|:-----|:-----|
| `duckdb_read_pool_size` | 4 | 只读连接池大小 |
| `duckdb_batch_size` | 1000 | 批量 COPY 缓冲行数 |
| `duckdb_commit_every` | 100 | 批量写入 commit 周期（秒） |
| `duckdb_single_writer` | true | 是否启用单写者模式（false 回退旧多路径行为） |
| `duckdb_readonly_scripts` | [] | 标注为只读的脚本清单（`--readonly` 声明） |

环境变量：`FTS_DUCKDB_READ_POOL_SIZE`/`FTS_DUCKDB_BATCH_SIZE`/`FTS_DUCKDB_COMMIT_EVERY`/`FTS_DUCKDB_SINGLE_WRITER`。

---

## 5. 测试计划

| 测试文件 | 覆盖点 |
|:---------|:-------|
| `tests/test_duckdb_writer.py`（新增） | 写锁互斥、批量 COPY 正确性、commit 粒度、写事务内异常回滚、缓冲刷新 |
| `tests/test_duckdb_reader.py`（新增） | 只读连接池 acquire/release、池满关闭、read_only 语义（写操作被拒） |
| `tests/test_data_futures.py`（修改） | `_get_reader`/`_get_writer` 迁移后既有功能回归 |
| `tests/test_scheduler.py`（修改） | 同库写 job 串行调度断言 |
| 并发压力测试 | 多线程读写混合：写 job 与读查询并发，断言读不被写阻塞（性能基准） |

---

## 6. 文件改动清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `fts/data_futures.py` | **修改** | 新增 `DuckDBWriter`/`DuckDBReader`；`_get_db` 拆分为 `_get_reader`/`_get_writer` |
| `fts/config/settings.py` | **修改** | 新增 §4 配置项 |
| `fts/scheduler/jobs.py` | **修改** | 写入类 job 库级串行约束 |
| `tests/test_duckdb_writer.py` | **新增** | writer 单测 |
| `tests/test_duckdb_reader.py` | **新增** | reader 单测 |
| `docs/harness/design/E.1-duckdb-concurrency-design.md` | **新增** | 本设计文档 |
| `docs/harness/01-architecture.md` | **修改** | 数据层架构图（单写者 + 多只读） |
| `docs/harness/03-configuration.md` | **修改** | 新配置项 |
| `docs/harness/04-resilience.md` | **修改** | 并发模型/锁策略/降级说明 |
| `docs/harness/06-testing.md` | **修改** | 测试用例数 |
| `docs/harness/07-operations.md` | **修改** | 版本历史 |
| `docs/harness/08-gap-analysis.md` | **修改** | GAP-056 登记/关闭 |
| `docs/harness/09-advancement-plan.md` | **修改** | 里程碑 |
| `pyproject.toml` / `README.md` | **修改** | 版本/工程指标 |

---

## 7. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | 单写者模式下，同进程多线程并发写不产生 `ConcurrentTransactionException`（结构消除） | 并发压力测试 |
| 2 | 读操作全部走 `read_only=True` 连接，读与写并发不互相阻塞 | 并发压力测试 |
| 3 | 批量 COPY 与逐条 INSERT 数据一致（同输入同结果） | 单元测试 |
| 4 | 写事务内异常回滚，不留半写入状态 | 单元测试 |
| 5 | 调度器同库写 job 不并行执行 | 调度测试 |
| 6 | `duckdb_single_writer=false` 回退旧行为，兼容降级路径 | 配置测试 |
| 7 | 既有功能全量回归无新增失败 + 一致性 13/13 | pytest + verify_doc_consistency |

---

## 8. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/data_futures.py`（DuckDBWriter/DuckDBReader/_get_reader/_get_writer）；`fts/config/settings.py`（并发配置）；`fts/scheduler/jobs.py`（库级串行）；`tests/test_duckdb_writer.py`、`tests/test_duckdb_reader.py`（新增测试） |
| 可验证断言 | 单写者模式多线程并发写零冲突异常、读全部 read_only 且与写互不阻塞、批量 COPY 与逐条 INSERT 一致、写异常回滚、同库写 job 串行、`single_writer=false` 回退兼容、GAP-056 关闭 |
| 检验方式 | `python scripts/verify_doc_consistency.py`；`pytest tests/test_duckdb_writer.py tests/test_duckdb_reader.py -v`；并发压力测试基准 |
