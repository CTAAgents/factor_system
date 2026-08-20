# E.3 L4 状态库 SQLite 化详细设计（S2）

> 版本: v3.0.0+11
> 关联: [E.2-storage-backend-comparison.md](./E.2-storage-backend-comparison.md)（S2 方案定位）、[E.1-duckdb-concurrency-design.md](./E.1-duckdb-concurrency-design.md)（现状基线）、[29-storage-convergence-plan.md](../plans/29-storage-convergence-plan.md)（P2 迁移、连接生命周期遗留登记）
> 状态: **已实施（2026-08-13）**
> 定位: 存储层后端替换（`state.duckdb` → SQLite WAL），API 契约不变，调用方零改动

---

## 1. 目标与范围

### 1.1 背景

E.2 评估确认：L4 运行状态库（`state.duckdb`）是 FTS **冲突最频繁**的库——演化/组合/meta_loop/extractors 5 个模块进程级共享，每 `save()` 一次写，属高频小写 + KV 语义，且无 OLAP 查询需求。

**现实证据（2026-08-13 实测）**：`data/state.duckdb` 被 PID 12220 以写连接持有期间，外部 `duckdb.connect(..., read_only=True)` 直接报 `IO Error: File is already open` —— 印证 plans/29 登记的「演化进程存活期持有写锁，连只读亦被锁」。该现象不随 DuckDB 版本缓解，只能通过换库或根治连接生命周期解决。

### 1.2 目标

| 指标 | 现状（DuckDB） | 目标（SQLite WAL） |
|:-----|:-----|:-----|
| 写连接存活期间外部只读 | 被文件锁阻塞（实测报错） | **不阻塞**（WAL 多读单写） |
| 跨进程写冲突 | 文件锁互抢 + 重试 | `busy_timeout` 等待 + 短事务 |
| 写原子性 | upsert 双表操作无显式事务 | **单事务包裹**（state_kv + history 原子） |
| 依赖 | duckdb（第三方） | sqlite3（标准库，零新增依赖） |
| 调用方 API | `upsert/get/get_all/snapshot/history` | 完全不变 |

### 1.3 范围

- `fts/store/state_db.py`：`StateKVStore` 后端重写为 SQLite（保持构造签名 `db_path` 与全部方法契约）
- `scripts/migrate_state_to_sqlite.py`（新增）：`state.duckdb` → `state.db` 数据迁移 + 校验
- `docs/harness/_data/storage_landscape.yaml`：`run_state` 域 `backend: sqlite`、`path: data/state.db`
- 测试：`tests/store/test_state_db.py` 改造 + `tests/scripts/test_migrate_state_to_sqlite.py` 新增
- 文档同步（architecture/configuration/resilience/testing/operations/gap-analysis/advancement-plan + pyproject + README）

### 1.4 不在范围

- **S1 连接生命周期根治**（写连接短生命周期 + filelock 跨进程写互斥）——L2/L3 库仍需 DuckDB，属独立后续设计，本阶段不动 `data_futures.py`
- L2 行情库 / L3 因子目录库后端变更（保留 DuckDB）
- `StateKVStore` 双后端抽象层（只实现 SQLite 一个后端，不做运行时后端切换——见 §3.1）

---

## 2. 架构设计

### 2.1 总体架构（SQLite WAL 单文件 + 进程级单连接）

```
                     ┌──────────────────────────────┐
                     │       data/state.db          │
                     │      (SQLite, WAL mode)      │
                     │  state_kv   state_history    │
                     └──────────────┬───────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │  WAL: 写不阻塞读，读不阻塞写（写事务短促）           │
          ├─────────────────────────┼─────────────────────────┤
   演化进程（高频写）               │                  外部只读（CLI/对账/归档）
   StateKVStore 单例               │                  StateKVStore 独立实例
   - 写事务 BEGIN IMMEDIATE        │                  - read 直连，不被写阻塞
   - upsert 双表原子               │
   - 线程锁内串行                   │
          └─────────────────────────┴─────────────────────────┘
```

核心约束：
1. **WAL 模式**：`PRAGMA journal_mode=WAL` —— 写连接长期存活不再阻塞外部读（相对 DuckDB 的关键改进，直接解决根因 1 的读阻塞面）
2. **短促写事务**：每次 `upsert` 一个 `BEGIN IMMEDIATE ... COMMIT`，双表原子；跨进程写冲突由 `busy_timeout` 等待而非立即失败
3. **进程内单连接 + 线程锁**：与 DuckDB 时代同类（进程级单例连接复用，`get_state_store()` 不变），线程锁内串行写

### 2.2 Schema 映射（DuckDB → SQLite 方言）

| DuckDB DDL | SQLite DDL | 说明 |
|:-----------|:-----------|:-----|
| `namespace VARCHAR NOT NULL` | `namespace TEXT NOT NULL` | |
| `key VARCHAR NOT NULL` | `key TEXT NOT NULL` | |
| `value JSON` | `value TEXT` | JSON 字符串存储，读写 `json.dumps`/`json.loads`（与现实现一致） |
| `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | `updated_at TEXT DEFAULT CURRENT_TIMESTAMP` | 显式传 `ts`（ISO）时按字符串存 |
| `run_id VARCHAR DEFAULT ''` | `run_id TEXT DEFAULT ''` | |
| `PRIMARY KEY (namespace, key)` | `PRIMARY KEY (namespace, key)` | 表级复合主键，UPSERT 冲突目标一致 |
| `seq BIGINT PRIMARY KEY` | `seq INTEGER PRIMARY KEY` | SQLite `INTEGER PRIMARY KEY` 即 rowid 别名 |
| `recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | `recorded_at TEXT DEFAULT CURRENT_TIMESTAMP` | |
| `CREATE INDEX idx_state_history_ns_key ON state_history(namespace, key, seq)` | 同构 | SQLite 支持同语法 |

UPSERT 语句（SQLite 3.24+ 与 DuckDB 语法兼容，仅列类型不同）：

```sql
INSERT INTO state_kv (namespace, key, value, updated_at, run_id)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (namespace, key)
DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, run_id = excluded.run_id
```

### 2.3 seq 语义（改进）

| 现状（DuckDB） | 设计（SQLite） |
|:---------------|:---------------|
| `SELECT COALESCE(MAX(seq),0)+1` 后插入，并发下同连接串行无竞态，但历史有删除时可能复用 seq | `INSERT INTO state_history (seq, ...)` 显式不写 seq，`seq INTEGER PRIMARY KEY AUTOINCREMENT` 自动分配，`last_insert_rowid()` 取回返回给调用方 |

- `AUTOINCREMENT` 保证 seq 全局单调、删除后不重用（比现状更强）
- 迁移时按原 seq 顺序插入，`AUTOINCREMENT` 的 sqlite_sequence 自动记录最大 id，后续新增从 max+1 继续，**与历史 seq 不冲突**
- `history()` 查询语义（`ORDER BY seq DESC`）不变

### 2.4 并发与线程模型

```python
import sqlite3, threading

class StateKVStore:
    def __init__(self, db_path=None):
        self._db_path = Path(db_path or DEFAULT_STATE_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")     # 多读单写不互斥
        self._conn.execute("PRAGMA busy_timeout=5000")    # 跨进程写冲突等待而非失败
        self._conn.execute("PRAGMA synchronous=NORMAL")   # WAL 下推荐
        self.init_schema()
```

- **进程内**：单连接 + `threading.Lock`，写操作（含 seq 分配、双表 upsert）在锁内 + 显式事务中串行执行
- **跨进程**：多个进程各持一个 SQLite 连接访问同一 `state.db`，WAL 下读写不互斥；写冲突由 `busy_timeout` 等待 + 事务短促控制
- **`check_same_thread=False`**：与 DuckDB 时代同类（进程级单例被多模块共享），线程安全由 `threading.Lock` 保证

---

## 3. 详细设计

### 3.1 StateKVStore 重构（单后端直改，不做抽象）

**决策**：直接以 SQLite 实现替换 DuckDB 实现，**不引入后端抽象层**。理由（简单至上）：
- 仅一个后端，无多后端并存需求；抽象层增加无谓复杂度
- 迁移与回退通过「旧 `state.duckdb` 保留为只读备份（冻结期）+ 迁移脚本」实现，无需运行时后端开关
- 构造签名 `StateKVStore(db_path: str | Path | None = None)` 与全部方法签名**保持完全不变**，调用方零改动

方法级改动：

| 方法 | 现状 | SQLite 实现 |
|:-----|:-----|:-----------|
| `__init__` | duckdb.connect | sqlite3.connect + PRAGMA（§2.4） |
| `init_schema` | 建两表 + 索引 | 同构 DDL（§2.2） |
| `upsert` | 双语句无事务 | `BEGIN IMMEDIATE` 包裹：UPSERT state_kv + INSERT history（AUTOINCREMENT seq），`COMMIT`；异常 `ROLLBACK` 后抛出；返回 seq（`last_insert_rowid()`） |
| `get` / `get_all` / `snapshot` | SQL 查询 + json.loads | 同构（仅占位符 `?` 一致） |
| `history` | 带过滤 ORDER BY seq DESC LIMIT | 同构 |
| `close` | conn.close | conn.close（幂等） |

### 3.2 API 契约（不变，SSOT）

```python
class StateKVStore:
    def __init__(self, db_path: str | Path | None = None) -> None: ...
    def init_schema(self) -> None: ...
    def upsert(self, namespace: str, key: str, value: dict | list,
               run_id: str = "", ts: str | None = None) -> int: ...   # 返回 history seq
    def get(self, namespace: str, key: str) -> dict | list | None: ...
    def get_all(self, namespace: str) -> dict[str, Any]: ...
    def snapshot(self) -> dict[str, dict[str, Any]]: ...
    def history(self, namespace: str | None = None, key: str | None = None,
                limit: int = 1000) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...
    # __enter__ / __exit__ / 模块级 get_state_store() 单例：不变
```

### 3.3 全局路径与存储域登记

| 项 | 现状 | 变更 |
|:---|:-----|:-----|
| `DEFAULT_STATE_DB` | `data/state.duckdb` | `data/state.db` |
| `get_state_store()` | 懒加载单例连接 `state.duckdb` | 同构指向 `state.db` |
| `storage_landscape.yaml` `run_state` | `backend: duckdb, path: data/state.duckdb` | `backend: sqlite, path: data/state.db`（StorageBackend 枚举已有 SQLITE） |

### 3.4 迁移脚本 `scripts/migrate_state_to_sqlite.py`

**用法**：`python scripts/migrate_state_to_sqlite.py [--source data/state.duckdb] [--target data/state.db] [--force]`，trace_id `fts.state_migrate.{ts}`。

**流程**：
1. **前置检查**：`--force` 未指定且 target 已存在非空 → 拒绝退出（幂等保护）；source 不存在 → 报错退出
2. **读源库**：`duckdb.connect(source, read_only=True)`；若因写锁被占用而打不开 → **降级拒绝**并输出明确提示「source 被进程 PID xxx 持有写锁，请关闭写入进程后重试」（与 `scripts/archive_history_cold.py` 同策略，不强行操作）
3. **建目标**：SQLite `state.db`（WAL + 两表 + 索引，§2.2 DDL）
4. **复制**：`state_kv` 全量（保序）；`state_history` 全量按 seq 升序插入（触发 AUTOINCREMENT 记录最大 seq）
5. **校验**：两表行数逐一比对 source == target；抽查值 JSON 可解析；输出报告（ns 分布/行数/seq 范围）
6. **收尾**：不删除 source（保留只读备份，冻结期后按 plans/29 约定清理）；提示用户确认后由 `get_state_store()` 切换至 `state.db`

### 3.5 回退方案

- 迁移后 `state.duckdb` **不删除**，冻结期内保留为只读备份
- 回退操作：恢复 `DEFAULT_STATE_DB` 指向 `state.duckdb`（或反向迁移脚本），既有数据完整
- SQLite 后端上线 ≥1 发布周期（冻结期）验证稳定后，再按 plans/29 冻结期约定退役旧库

---

## 4. 配置设计

不新增运行时配置项（保持简单）：

| 项 | 值 |
|:---|:---|
| 库路径 | 常量 `DEFAULT_STATE_DB = <root>/data/state.db`（构造参数可覆盖，测试已用 tmp_path） |
| WAL / busy_timeout | 硬编码 PRAGMA（无需可配置——固定最优值，符合"不为假设场景加配置"） |

---

## 5. 测试计划

### 5.1 `tests/store/test_state_db.py`（改造：fixture 换 `.db` 路径，语义不变）

| 用例 | 断言 |
|:-----|:-----|
| upsert_and_get / get_missing_returns_none / upsert_overwrites_current / upsert_appends_history / get_all_by_namespace / snapshot_roundtrip / list_value_supported / history_filter / 重开连接可读 | 与 DuckDB 时代行为一致（round-trip） |

### 5.2 新增 SQLite 特性用例（并入 test_state_db.py）

| 用例 | 断言 |
|:-----|:-----|
| WAL 生效 | `PRAGMA journal_mode` 返回 `wal` |
| **写连接存活期间外部只读** | 持写连接实例时，另开独立连接可打开且可读（对照：DuckDB 场景报 File is already open） |
| upsert 原子性 | 注入 history 插入失败 → 事务回滚，state_kv 与 state_history 均无该次写入残留 |
| seq 单调 | 连续 upsert 的返回 seq 严格递增，history 中无重复 seq |
| 并发写串行 | 多线程并发 upsert 不产生 sqlite3.OperationalError，seq 不重复（进程内锁 + 事务） |

### 5.3 `tests/scripts/test_migrate_state_to_sqlite.py`（新增）

| 用例 | 断言 |
|:-----|:-----|
| 迁移闭环 | 源库（DuckDB 建两表填充）→ 迁移 → 目标行数与源一致，值 JSON 可解析 |
| seq 保序 | 目标 history 按 seq 升序，新增 upsert 从 max+1 继续 |
| 幂等保护 | target 已存在非空且未 `--force` → 拒绝退出 |
| 源库锁占用降级 | 源库被写连接占用 → 明确报错提示，不崩溃、不破坏目标 |
| source 缺失 | 报错退出 |

### 5.4 调用方回归（受 S2 影响的既有测试）

| 测试 | 影响 |
|:-----|:-----|
| `tests/factor_engine/extractors/test_base.py` / `test_futures_pipeline.py` | 构造 `StateKVStore(tmp_path/...)`，fixture 路径后缀改 `.db` 即可 |
| `tests/factor_engine/test_evolution_loop.py` / `test_dynamic_pool.py` | `get_state_store` monkeypatch 隔离，无需改 |
| `tests/factor_engine/test_meta_loop.py` / `test_portfolio_loop.py` 等 | 同上 |

---

## 6. 文件改动清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `fts/store/state_db.py` | **修改** | SQLite 后端重写（§3.1/3.2/3.3），API 不变 |
| `scripts/migrate_state_to_sqlite.py` | **新增** | 迁移脚本（§3.4） |
| `docs/harness/_data/storage_landscape.yaml` | **修改** | `run_state` 域 backend/path |
| `tests/store/test_state_db.py` | **修改** | fixture 换 SQLite + 新增特性用例（§5.1/5.2） |
| `tests/scripts/test_migrate_state_to_sqlite.py` | **新增** | 迁移测试（§5.3） |
| `docs/harness/01-architecture.md` | **修改** | L4 存储层架构（SQLite WAL） |
| `docs/harness/03-configuration.md` | **修改** | 状态库路径 |
| `docs/harness/04-resilience.md` | **修改** | 并发模型/锁策略（L4 换 SQLite 后说明） |
| `docs/harness/06-testing.md` | **修改** | 测试文件/用例数 |
| `docs/harness/07-operations.md` | **修改** | 版本历史 |
| `docs/harness/08-gap-analysis.md` | **修改** | GAP 登记（连接生命周期遗留 L4 侧消除） |
| `docs/harness/09-advancement-plan.md` | **修改** | 里程碑 |
| `pyproject.toml` / `README.md` | **修改** | 工程指标（按版本纪律，落地完成后 bump） |

> 注：`fts/config/settings.py` 不新增配置项（§4）；`fts/store/registry.py` 无需改（SQLITE 枚举已存在）。

## 7. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | StateKVStore 全部 API 在 SQLite 后端行为与 DuckDB 一致 | `pytest tests/store/test_state_db.py -v` |
| 2 | 写连接存活期间外部只读连接可打开可读（对比 DuckDB 复现被锁） | 新增特性用例 |
| 3 | upsert 双表原子：异常回滚不留半写入 | 原子性用例 |
| 4 | seq 严格单调、删除后不重用 | seq 用例 |
| 5 | 迁移脚本行数一致 + 幂等 + 源库锁占用降级 | `pytest tests/scripts/test_migrate_state_to_sqlite.py -v` |
| 6 | 既有调用方（extractors/evolution/portfolio/meta_loop/dynamic_pool）回归无新增失败 | 受影响模块测试 |
| 7 | 文档一致性 13/13 | `python scripts/verify_doc_consistency.py` |

---

## 8. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/store/state_db.py`（SQLite 后端）；`scripts/migrate_state_to_sqlite.py`（迁移）；`docs/harness/_data/storage_landscape.yaml`（run_state 域）；`tests/store/test_state_db.py`、`tests/scripts/test_migrate_state_to_sqlite.py`（测试）；`fts/factor_engine/{state,meta_loop,portfolio_loop}.py`、`fts/factor_engine/extractors/base.py`、`fts/data_futures.py`（调用方，零改动） |
| 可验证断言 | API 行为一致性；WAL 下写连接存活不阻塞外部读；upsert 原子性；seq 单调；迁移行数一致 + 幂等 + 锁占用降级；受影响模块回归全绿；一致性 13/13 |
| 检验方式 | `pytest tests/store/ -v`；`pytest tests/scripts/test_migrate_state_to_sqlite.py -v`；受影响模块测试；`python scripts/verify_doc_consistency.py` |

---

## 9. 实施记录（2026-08-13）

- ① **代码落地**：`fts/store/state_db.py` 后端重写为 SQLite（WAL + busy_timeout=5000 + synchronous=NORMAL）；upsert 单事务（`BEGIN IMMEDIATE`）双表原子；seq `INTEGER PRIMARY KEY AUTOINCREMENT` + `last_insert_rowid()`；`DEFAULT_STATE_DB` → `data/state.db`；`get_state_store()` 单例不变；API 契约完全不变。
- ② **迁移脚本**：新建 `scripts/migrate_state_to_sqlite.py`（`--source`/`--target`/`--force`/`--json`，trace_id `fts.state_migrate.{ts}`）——行数校验/幂等保护（目标非空拒绝）/`--force` 覆盖重建（DROP 重建清脏）/源库写锁占用降级拒绝（明确提示 PID）/DuckDB TIMESTAMP datetime → str 转换（消除 Python 3.12 sqlite3 datetime adapter 弃用告警）/不删源（冻结期保留）。
- ③ **测试**：`tests/store/test_state_db.py` 改造为 SQLite fixture + 9 API 用例全绿 + 5 SQLite 特性用例（WAL 生效/写连接存活外部只读不阻塞（对照 DuckDB File is already open）/upsert 原子回滚（SQLite 触发器 RAISE FAIL 注入 history 失败）/seq 单调/8 线程并发写串行 seq 不重复）；新增 `tests/scripts/test_migrate_state_to_sqlite.py` 6 用例（迁移闭环/seq 接续/幂等保护/force 覆盖/锁占用降级/源缺失）。store + 迁移 **33 passed**。
- ④ **回归**：调用方 74 passed（extractors test_base/test_futures_pipeline + test_dynamic_pool + test_migrate_state_to_duckdb）+ ruff check 全绿 + mypy 2 文件 Success。
- ⑤ **文档同步**：storage_landscape（run_state backend=sqlite/path=data/state.db，version=2026-08-13）；01（state_db 模块行 + P2/E.3 断言行）；04（L4 SQLite WAL 并发行）；06（test_state_db 11→14 + test_migrate_state_to_sqlite 6，合计 5311→5317）；07（版本历史追加）；08（GAP-090 追加 E.3 S2 根治记录，S1 仍远期遗留）；09（E.3 S2 完成项登记）；E.2（S2 已实施标注）。
- ⑥ **遗留**：旧 `data/state.duckdb` 冻结期（≥1 发布周期）后清理；**S1**（L2/L3 DuckDB 库写连接短生命周期 + filelock 跨进程写互斥）为下一阶段，待设计。
