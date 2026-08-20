# E.4 L2/L3 DuckDB 连接生命周期根治详细设计（S1）

> 版本: v3.1.0+3
> 关联: [E.2-storage-backend-comparison.md](./E.2-storage-backend-comparison.md)（S1 方案定位，已决策）、[E.3-sqlite-state-store-design.md](./E.3-sqlite-state-store-design.md)（S2 已完成，L4 已 SQLite 化）、[E.1-duckdb-concurrency-design.md](./E.1-duckdb-concurrency-design.md)（单写者现状基线）、[04-resilience.md](../04-resilience.md)（并发韧性）
> 状态: **已实施（2026-08-13）**
> 定位: 受保护数据层（`fts/data_futures.py` + `fts/data_sources/aggregator.py` + `fts/factor_engine/factor_db/repository.py`）连接生命周期根治——写连接短生命周期 + 跨进程写互斥 + 读路径 read_only

---

## 1. 目标与范围

### 1.1 背景

E.2 选型评估确认：L4 状态库已由 S2 迁移 SQLite WAL（2026-08-13 完成）。剩余锁痛点集中在 **L2 行情库（`fts_history.duckdb`）与 L3 因子资产库（`factor_catalog_{stock,futures}.duckdb`）**——二者仍为 DuckDB 且存在「写连接长期存活」的根因 1 与「跨进程写无互斥」的根因 2。

### 1.2 现状勘察（2026-08-13 代码实证）

| # | 位置 | 连接形态 | 持锁后果 |
|:--|:-----|:---------|:---------|
| 1a | `fts/data_futures.py` `_get_writer()` | 模块级全局常驻 `DuckDBWriter`（`_WRITER`） | 演化/同步进程一旦调写（如 `_write_contract_kline`）即持有 L2 写锁**到进程结束** |
| 1b | `fts/data_futures.py` `_get_db()` | 模块级全局常驻 `DuckDBConnection`（`_DB`） | 兼容入口同样常驻持锁 |
| 1c | `fts/data_sources/aggregator.py` `_get_cache_conn()` | 实例惰性常驻写连接（kline/minute/tick 写路径） | 行情同步长驻进程持 L2 写锁 |
| 1d | `fts/factor_engine/factor_db/repository.py` 4 类（Factor/QualityScore/Status/Audit） | `_get_conn` 惰性写连接常驻 | 演化进程 `_repo`（evolution_loop/evolution_futures L2367/2382）持有 L3 写锁**到进程结束** |
| 2 | scripts/ 与 scheduler job | 无跨进程写互斥协议 | 并发写同一 `.duckdb` 抢文件锁（实测 `IOException: 另一个程序正在使用此文件`） |
| 3 | `fts/data_futures.py` `DuckDBReader` 池 | **普通连接**（非 `read_only=True`，E.1 因旧版兼容未启用） | 读连接本身可写语义，未彻底利用锁配置隔离 |

### 1.3 目标

| 指标 | 现状 | 目标 |
|:-----|:-----|:-----|
| L2/L3 写连接存活时长 | 小时级（进程存活期） | **秒级**（写操作完成即关） |
| 跨进程写冲突 | 文件锁互抢 + 重试 | **filelock 串行化**（写窗口互斥） |
| 演化进程（长驻）持有写锁 | 是（写一次即持锁） | **否**（写点短暂开写，其余时间零写连接） |
| 读路径连接形态 | 普通可写连接 | **`read_only=True`**（读侧零写语义） |

### 1.4 范围

- `fts/store/duckdb_lock.py`（新增）：跨进程文件锁组件（标准库实现，零新增依赖）
- `fts/data_futures.py`：`_get_writer()`/`_get_db()` 常驻缓存移除 + 写点迁移 `_write_scope()` + 读池 `read_only=True`
- `fts/data_sources/aggregator.py`：写路径（kline/minute/tick）迁移短生命周期写
- `fts/factor_engine/factor_db/repository.py`（4 类）：`_get_conn` 补 `lock_configuration=true`；写库方用后 `close()` 释放写锁
- `fts/factor_engine/evolution_loop.py` / `evolution_futures.py`：晋升写库后 `repo.close()`（写锁短暂持有）
- 测试与文档同步

### 1.5 不在范围

- L4 `state.db`（S2 已完成 SQLite 化，不涉及）
- repository 全量读/写分离改造（读方法逐一改 `read_only=True` 短连接）——改动 30+ 方法，收益有限，列为后续增强
- DuckDB 替换（E.2 S5 已否决）

---

## 2. 架构设计

### 2.1 总体架构（写窗口短生命周期 + 跨进程互斥）

```
┌──────────────────────────────────────────────────────────────────────┐
│                         .duckdb 文件（L2/L3）                          │
└───────────────────────────────────┬──────────────────────────────────┘
                                    │
   ┌──────────────┬────────────────┼─────────────────┬────────────────┐
   │              │                │                 │                │
┌──▼───┐   ┌──────▼─────┐   ┌──────▼──────┐   ┌──────▼─────┐   ┌──────▼─────┐
│ 读池  │   │ 写窗口      │   │ 写窗口       │   │ 读路径      │   │ 调度写 job  │
│read_ │   │ filelock+  │   │ filelock+   │   │ read_only  │   │ filelock+  │
│only  │   │ 短连接(秒)  │   │ 短连接(秒)   │   │ 短连接      │   │ 短连接     │
│ 常驻  │   │ data_      │   │ aggregator  │   │ repository │   │ jobs       │
│      │   │ futures    │   │ 写路径       │   │ 读方法      │   │            │
└──────┘   └────────────┘   └─────────────┘   └────────────┘   └────────────┘
     │              │               │                │               │
     └──────────────┴─── filelock（data/.locks/*.duckdb.lock）串行化 ──┘
                         写窗口任意时刻至多一个（跨进程）
```

核心约束：
1. **写窗口短生命周期**：所有写操作在「filelock 持有 + 短连接 + 写完即关」内完成（秒级），进程其余时间**零写连接**
2. **跨进程写互斥**：写入口统一经 `duckdb_write_lock(db_path)`（`data/.locks/{name}.lock` 文件锁），写窗口串行化，结构上消除跨进程并发写
3. **读路径 read_only**：读连接 `read_only=True`，读侧零写语义；写窗口存在时读连接打开可能短暂失败 → 由既有降级链/重试兜底（写窗口已降至秒级，冲突概率极低）

### 2.2 跨进程文件锁组件 `fts/store/duckdb_lock.py`

标准库实现（Windows `msvcrt.locking` / POSIX `fcntl.flock`），**零新增依赖**：

```python
from contextlib import contextmanager

def duckdb_write_lock(db_path, timeout: float = 30.0):
    """跨进程写锁：持有期间独占 db_path 的写窗口。

    - 锁文件：<db_path.parent>/.locks/<db_path.name>.lock
    - Windows: msvcrt.locking(LK_LOCK) / POSIX: fcntl.flock(LOCK_EX)
    - timeout 内未获锁 → 抛 RuntimeError（失败透明）
    - 锁在写窗口结束后释放（写连接已关闭）
    """
```

设计要点：
- 锁文件路径 `data/.locks/`（自动建目录），与数据文件分离
- `timeout` 默认 30s：演化写点短暂，冲突时等待而非立即失败；超时明确报错
- 与 DuckDB 自身文件锁**互补**：filelock 协调「本系统写方」避免互抢；DuckDB 锁仍作为最终防线

### 2.3 L2 `data_futures.py` 改造

| 现状 | 改造 |
|:-----|:-----|
| `_WRITER` 全局常驻 `DuckDBWriter` | **删除全局缓存**；新增 `_write_scope()` contextmanager：`duckdb_write_lock → DuckDBWriter（短连接）→ yield → close` |
| `_DB` 全局常驻 `DuckDBConnection` | **删除全局缓存**；`_get_db()` 兼容入口改读语义 `read_only=True` 短连接（用完由调用方 close，注释标注） |
| `_get_writer()` 全局单例 | 保留函数但语义改为「创建一次性短写连接」，内部不再缓存 `_WRITER`；docstring 标注 deprecated，新代码用 `_write_scope()` |
| 写点 `_write_contract_kline`（L1954） | 迁移：`with _write_scope() as writer: ...`（DELETE+executemany 在锁窗口内一次提交） |
| `DuckDBReader` 池（L474 区域） | 连接改 `duckdb.connect(path, read_only=True)`（依赖 `lock_configuration=true`，已启用） |

### 2.4 L2 `aggregator.py` 写路径改造

| 现状 | 改造 |
|:-----|:-----|
| `_cache_conn` 实例常驻写连接（`_get_cache_conn`） | 写路径（`_write_cache`/`_write_minute_cache`/`_write_tick_cache`）改短生命周期：`with duckdb_write_lock(db_path): conn = duckdb.connect(db_path); ...; conn.close()`（批量一次提交） |
| 读路径 `_try_cache`/分钟读/`_read_*`（L300/704 等） | 改 `read_only=True` 短连接（读完成即关）；写窗口冲突 → 既有降级（返回 None 走实时源） |

> 权衡：aggregator 为**批量低频写**（同步当天数据时触发），每批一次 connect/close 成本毫秒级可接受；读路径同样短连接（每因子加载一次读）。若性能基准显示退化明显，读路径可改常驻 `read_only=True` 连接池（写窗口内降级）。

### 2.5 L3 `repository.py` 改造

| 现状 | 改造 |
|:-----|:-----|
| `_get_conn` 惰性写连接常驻（4 类） | ① 建连时补 `SET lock_configuration=true`（现有缺失）；② **写库方用后 `close()`**——写锁从"进程存活期"降到"写操作后即释放" |
| 读方法（list_factors/get_factor 等） | 保持惰性连接（**不逐一改 read_only**，列 1.5 后续增强）；连接在写库方 close 后自动释放，读侧不常驻持锁 |

调用方同步：
- `evolution_loop.py`/`evolution_futures.py`：`_promote_to_elite` 写库成功后 `self._repo.close()`（写锁短暂持有）；`_get_repo()` 下次再惰性重建
- `cli.py`/`scheduler/jobs.py`/`factor_inspector.py`：短进程/短 job，进程退出自然释放（无需改）
- `repository.py` L289 嵌套 `FactorStatusRepository(self._db_path)`：用后 close

### 2.6 调度器库级串行（双保险）

filelock 已提供跨进程写串行（§2.2）；调度器同库写 job 串行约束（E.1 §2.3 已设计）作为**双保险**登记——filelock 为首道防线，调度串行为可选项，本阶段以 filelock 落地为准。

---

## 3. 详细设计

### 3.1 跨进程写锁（`fts/store/duckdb_lock.py`）

```python
"""跨进程写锁（标准库实现，Windows msvcrt / POSIX fcntl）。"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_DIR = Path("data/.locks")  # 相对项目根，运行时解析

def _lock_path(db_path: Path) -> Path:
    return (db_path.parent / ".locks" / f"{db_path.name}.lock")

@contextmanager
def duckdb_write_lock(db_path: str | Path, timeout: float = 30.0) -> Iterator[None]:
    """获取跨进程写锁（阻塞等待至 timeout），窗口结束后释放。"""
    db = Path(db_path)
    lock_fp = _lock_path(db)
    lock_fp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_fp, "a+b")
    try:
        _acquire(fh, timeout)          # 平台相关实现
        yield
    finally:
        _release(fh)                   # 平台相关实现
        fh.close()
```

- Windows：`msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)`（阻塞直至获锁；LK_NBLCK + 轮询实现 timeout）
- POSIX：`fcntl.flock(fh.fileno(), fcntl.LOCK_EX)`（阻塞）
- timeout 实现：Windows 用 `LK_NBLCK` 轮询 + `time.sleep(0.1)`；POSIX 用 `LOCK_EX | LOCK_NB` 轮询

### 3.2 写点迁移清单（L2）

| 写点 | 迁移为 |
|:-----|:-------|
| `data_futures.py::_write_contract_kline` | `with _write_scope() as writer: DELETE + executemany` |
| `data_futures.py::_get_db()` 兼容入口 | 读语义：`duckdb.connect(path, read_only=True)` 短连接（docstring 标注调用方负责 close） |
| `aggregator.py::_write_cache/_write_minute_cache/_write_tick_cache` | `with duckdb_write_lock(db_path): conn=duckdb.connect(...); 批量写; conn.close()` |
| `aggregator.py::_try_cache/_read_minute/_read_tick` | `duckdb.connect(path, read_only=True)` 短连接 |

### 3.3 L3 写锁释放点

| 调用方 | 释放点 |
|:-------|:-------|
| `evolution_loop.py::_promote_to_elite`（写库成功分支） | `self._repo.close()`（若 `self._repo is not None`） |
| `evolution_futures.py::_promote_to_elite`（同） | 同上 |
| `repository.py::update_factor/delete_factor/retire_factor/add_evaluation/save_score/update_factor_status/save_report`（内部嵌套 repo） | 嵌套 repo 用后 close |

---

## 4. 配置设计

| 配置项 | 默认 | 说明 |
|:-------|:-----|:-----|
| `duckdb_write_lock_timeout`（`FTS_DUCKDB_WRITE_LOCK_TIMEOUT`） | 30.0 | 跨进程写锁等待上限（秒），超时抛错失败透明 |
| 锁文件目录 | `data/.locks/` | 硬编码相对路径（与数据文件同根，无需配置化——简单至上） |

---

## 5. 测试计划

### 5.1 新增 `tests/store/test_duckdb_lock.py`

| 用例 | 断言 |
|:-----|:-----|
| 锁互斥（同进程两次获取） | 第二次阻塞至释放后成功 |
| timeout 超时 | 超时抛 RuntimeError |
| 锁文件创建/清理 | `.locks/*.lock` 存在且窗口结束后可再获取 |
| 嵌套写窗口 | 同线程内不可重入（抛错或死锁防护）——设计为**不可重入**（RLock 语义不提供，写窗口必须串行） |

### 5.2 修改 `tests/test_data_futures.py`

- `_write_scope` 写入→关闭→外部可 read_only 打开（**对照改造前：写后连接仍被持锁**）
- `_get_db()` 读语义 read_only：写操作被拒
- 写点迁移回归（`_write_contract_kline` 写读一致）

### 5.3 修改 `tests/test_aggregator.py` / `tests/test_data_sources/test_aggregator*`

- 写路径短连接：写后 `_cache_conn` 不存在/已关闭；缓存读回一致
- 读路径 read_only 短连接：读后无持锁

### 5.4 修改 `tests/factor_engine/test_evolution_loop.py`（相关子集）

- `_promote_to_elite` 写库后 `repo.close()` 断言（mock repo 记录 close 调用）
- 晋升→close→下次写可重建连接

### 5.5 既有调用方回归

- `tests/test_data_futures.py` / `tests/test_aggregator.py` / `tests/factor_engine/test_factor_db.py` / `tests/test_cli*.py` / `tests/factor_engine/test_evolution_loop.py`（快速子集）

---

## 6. 文件改动清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `fts/store/duckdb_lock.py` | **新增** | 跨进程写锁组件（§3.1） |
| `fts/store/__init__.py` | **修改** | 导出 `duckdb_write_lock` |
| `fts/data_futures.py` | **修改** | 删 `_WRITER`/`_DB` 全局缓存；新增 `_write_scope()`；`_get_writer` deprecated 语义；读池 read_only；`_write_contract_kline` 迁移 |
| `fts/data_sources/aggregator.py` | **修改** | 写路径短连接 + 读路径 read_only 短连接 |
| `fts/factor_engine/factor_db/repository.py` | **修改** | `_get_conn` 补 lock_configuration；嵌套 repo 用后 close |
| `fts/factor_engine/evolution_loop.py` / `evolution_futures.py` | **修改** | 晋升写库后 `repo.close()` |
| `tests/store/test_duckdb_lock.py` | **新增** | 锁组件测试 |
| `tests/test_data_futures.py` / `tests/test_aggregator.py` | **修改** | 短连接语义适配 |
| `docs/harness/01-architecture.md`/`04-resilience.md`/`06-testing.md`/`07-operations.md`/`08-gap-analysis.md`/`09-advancement-plan.md` + storage_landscape | **修改** | 文档同步 |
| `AGENTS.md` / `README.md` | **修改** | 并发模型描述同步 |

---

## 7. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | 演化进程存活期不持有 L2/L3 写锁（写点后连接即关，外部可随时 read_only 打开） | 集成测试：演化写库后外部连接打开成功 |
| 2 | 跨进程并发写经 filelock 串行，无 `ConcurrentTransactionException` | `tests/store/test_duckdb_lock.py` + 并发压力测试 |
| 3 | 写操作完成后连接关闭（无长生命周期写连接） | `_write_scope` 测试断言关闭 |
| 4 | L2 读路径 `read_only=True` | `test_data_futures` 断言读连接写操作被拒 |
| 5 | 受影响模块回归全绿 + ruff/mypy 全绿 | `pytest tests/store/ tests/test_data_futures.py tests/test_aggregator.py tests/factor_engine/test_factor_db.py -q` |
| 6 | 文档一致性 | `python scripts/verify_doc_consistency.py`（既有 2 项失败除外） |

---

## 8. 实施记录（2026-08-13）

| # | 改动 | 文件 |
|:--|:-----|:-----|
| 1 | 新增跨进程写锁组件（msvcrt/fcntl 标准库，零依赖），含 4 用例单测 | `fts/store/duckdb_lock.py` + `tests/store/test_duckdb_lock.py` |
| 2 | 删 `_WRITER`/`_DB` 全局常驻缓存；新增 `_write_scope()`；读池 `read_only=True`；`_write_contract_kline` 迁移短连接 | `fts/data_futures.py` |
| 3 | aggregator 写路径（kline/minute/tick）迁移 `_write_scope`；读路径 `_open_read_conn`（read_only 短连接 + finally close） | `fts/data_sources/aggregator.py` |
| 4 | repository 4 类 `_get_conn` 补 `SET lock_configuration = true`（旧版静默降级）；`retire_factor` 嵌套 repo 用后 close | `fts/factor_engine/factor_db/repository.py` |
| 5 | 晋升 `_promote_to_elite` 注入 `@_release_repo_after` 装饰器（方法退出即 close repo），移除早前冗余注入块 | `fts/factor_engine/evolution_loop.py` / `evolution_futures.py` |
| 6 | 两个 TQ 同步脚本写段迁移 `_write_scope`（含 dry-run 分支释放） | `scripts/sync_tq_contract_kline.py` / `sync_tq_futures_15y.py` |
| 7 | 测试适配：test_aggregator 残留 `_cache_conn` mock 全量迁移 `_open_read_conn`/`_write_scope`；同时修复预存 Binder Error（tick_cache datetime 列 TIMESTAMP 与字符串参数比较，补 `CAST(? AS TIMESTAMP)`） | `tests/data_sources/test_aggregator.py` / `test_tick_cache_accumulate.py` 等 |
| 8 | 修复注入破坏的类结构（`_release_repo_after` 误落模块级导致 `EvolutionLoop` 类提前结束，mypy 报 100+ attr-defined；移回类内后归零） | `fts/factor_engine/evolution_loop.py` / `evolution_futures.py` |

**验收结果**：受影响模块 653 passed（`tests/store/ tests/test_data_futures.py tests/data_sources/ tests/factor_engine/test_factor_db.py`）；ruff 全绿；mypy 仅剩 2 处 HEAD 预存问题（aggregator.py:630 pandas iloc 索引类型、sync_tq_contract_kline.py:704 变量类型，均非本次改动行，按外科手术原则不顺手修）。fcnctl 分支加 `# type: ignore[attr-defined]`（Windows 平台 msvcrt 路径无碍）。

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/store/duckdb_lock.py`（跨进程写锁）；`fts/data_futures.py`（`_write_scope`/read_only 读池/写点迁移）；`fts/data_sources/aggregator.py`（写路径短连接）；`fts/factor_engine/factor_db/repository.py`（lock_configuration/close 语义）；`fts/factor_engine/{evolution_loop,evolution_futures}.py`（晋升后 close）；`tests/store/test_duckdb_lock.py` 等 |
| 可验证断言 | 演化进程零长驻写连接；filelock 串行化跨进程写；写后连接即关；读路径 read_only；受影响模块回归全绿；一致性校验通过（既有失败除外） |
| 检验方式 | `pytest tests/store/ tests/test_data_futures.py tests/test_aggregator.py tests/factor_engine/test_factor_db.py -q`；`python scripts/verify_doc_consistency.py` |
