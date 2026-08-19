# plans/43 测试组因子库隔离方案（GAP-129）

> 状态: 📋 方案已定，待实施
> 版本: v3.0.0
> 关联差距: GAP-129（P1，测试组写真实因子库——测试污染生产 SSOT）
> 前置: GAP-128（质检落库）已关闭；GAP-030（factor_db_path 隔离注入机制）已存在

## 1. 背景与根因

**根因**: `EvolutionLoop` 与各因子库仓储（`FactorRepository`/`FactorQualityScoreRepository`/`FactorStatusRepository`/`FactorAuditReportRepository`）在未显式传 `factor_db_path` 时，经 `fts/factor_engine/factor_db/schema.py::get_db_path(market)` 路由到**真实** `data/factor_catalog_{futures,energy}.duckdb`。GAP-030 已提供 `factor_db_path` 隔离注入机制（`_get_repo()` 对 `db_path=None` 才走 market 路由），但测试组未统一采用。

**实证**（2026-08-16，GAP-128 修复期间）:
- 全库 **107 处** `EvolutionLoop(` 测试调用，仅 **9 处** 传 `factor_db_path`（test_evolution_loop 5 + test_risk_tag 4）
- 运行 `tests/factor_engine/test_evolution_loop.py` 后，真实 `factor_catalog_futures.duckdb` 出现测试伪影行：`factor_quality_scores.factor_id='unknown'`、`factor_audit_reports.factor_id='audit_test_factor_001'`
- 涉及 16 个测试文件（test_evolution_loop 84 调 / test_uct_selection / test_coverage_edge_cases / test_gru_factor / test_transformer_factor / test_e2e 等）

**危害**: 测试数据写入 L3 因子资产库 SSOT，可被 L3/信号管线/质检看板误消费（同名因子覆盖、伪影行、统计失真）；GAP-128 质检落库后问题放大（两表也随测试写入）。

## 2. 现状审计（已核验）

| 项目 | 数值 |
|:-----|:-----|
| `EvolutionLoop(` 测试调用总数 | 107（16 文件） |
| 已隔离（传 factor_db_path） | 9（test_evolution_loop 5 / test_risk_tag 4） |
| 未隔离（走真实库） | ~98 |
| 仓储路由挂载点 | `get_db_path(market)`，被 4 个仓储类构造时**局部** `from .schema import get_db_path` 引用 |
| 仓储连接行为 | `__init__` 内 `init_database(db_path)` 幂等建表（空 tmp 库可直接用） |
| 真实路由断言测试（需豁免） | test_energy_chain.py:109-113 / test_factor_db.py:746-753 / test_cli_extra.py:571 / test_evolution_loop.py:2028 |
| 模块级 import 时取真实路径 | test_bincount_boundary.py:21 `_DB_PATH = get_db_path("futures")`（需豁免或改造） |

## 3. 隔离目标

1. **零污染**: 常规测试（`pytest tests/`）运行后，真实 `data/factor_catalog_{futures,energy}.duckdb` 零新增/零修改行
2. **零调用点改造**: 不批量改 107 处 `EvolutionLoop(` 调用（高触碰、易漏），通过根 conftest 全局兜底
3. **可豁免**: 确实需要真实路由语义/真实库数据的测试可显式标记豁免，不影响其断言
4. **路由语义保留**: futures/energy 分库映射在重定向后仍成立（仅文件位置变到 tmp）

## 4. 方案设计

### 4.1 核心机制：根 conftest 单挂载点重定向

新增 **`tests/conftest.py`**（根目录，pytest 自动加载覆盖全部子目录）autouse fixture：

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def _isolated_factor_db(request, monkeypatch, tmp_path):
    """测试因子库隔离（GAP-129）：get_db_path 全局重定向至每测试独立 tmp DuckDB。"""
    if request.node.get_closest_marker("uses_real_factor_db"):
        yield  # 豁免：真实路由断言/真实库数据测试
        return
    import fts.factor_engine.factor_db.schema as schema

    db_dir = tmp_path / "factor_db"
    db_dir.mkdir(exist_ok=True)

    real = {"futures": schema.DATABASE_PATH_FUTURES, "energy": schema.DATABASE_PATH_ENERGY}

    def _isolated_get_db_path(market: str = "futures") -> Path:
        name = real[market].name if market in real else f"factor_catalog_{market}.duckdb"
        return db_dir / name

    monkeypatch.setattr(schema, "get_db_path", _isolated_get_db_path)
    yield
```

- **为何一处生效**：4 个仓储类构造时 `from .schema import get_db_path`（局部导入），在**调用时**解析 schema 模块属性 → 替换 `schema.get_db_path` 即全量生效（FactorRepository/Quality/Status/Audit 全覆盖）
- **为何免 init**：仓储 `__init__` 内 `init_database(db_path)` 幂等建表
- **为何免触碰现有调用**：显式传 `db_path=` 的测试不调用 `get_db_path`，不受影响

### 4.2 豁免机制

注册自定义标记 `uses_real_factor_db`（`pyproject.toml` 或根 conftest `pytest_configure`），命中则 fixture 不重定向。

实施前执行**路由审计**（新脚本或 grep 核查）枚举全部需要豁免的测试：
- 已识别：test_energy_chain（get_db_path 路由断言）、test_factor_db（_db_path==DATABASE_PATH_FUTURES 断言）、test_cli_extra:571（patch DATABASE_PATH_FUTURES 后断言之类）、test_evolution_loop:2028（patch DATABASE_PATH_FUTURES）、test_bincount_boundary（模块级 `_DB_PATH` 取真实路径——**建议改造**为函数内/显式隔离，而非豁免）
- 审计工具：grep `get_db_path|DATABASE_PATH_FUTURES|DATABASE_PATH_ENERGY|factor_db_path` 于 tests/，逐处判定 真实数据依赖 vs 路由语义断言 vs 误用

### 4.3 兜底：仓库内数据恢复校验（可选加固）

隔离落地后，`backfill_factor_quality_audit.py` 的孤儿清理本就兜底真实库一致性；可在 CI 增加一步「运行测试前后真实库行数不变」断言脚本（`scripts/verify_factor_db_untouched.py`，对比 factor_catalog/quality/audit 三表 COUNT 与 checksum），作为回归护栏防未来回退。

## 5. 实施步骤

1. **路由审计**：grep 枚举 tests/ 下全部真实库访问点，逐处判定（豁免 vs 改造），产出豁免清单
2. **根 conftest**：新增 `tests/conftest.py`（`_isolated_factor_db` autouse fixture + `uses_real_factor_db` 标记注册 + `pytest_configure`）
3. **豁免标记**：对审计清单中真实路由断言测试打 `@pytest.mark.uses_real_factor_db`；对模块级取路径的（test_bincount_boundary）优先改造为显式隔离
4. **测试**：新增 `tests/test_factor_db_isolation.py`（断言隔离生效 + 豁免生效）:
   - 未标记测试内 `FactorRepository(market="futures")._db_path` 属于 tmp_path 且与真实路径不同
   - 未标记测试晋升因子后真实库三表 COUNT 不变（隔离验证）
   - 标记 `uses_real_factor_db` 的测试仍路由真实路径
5. **回归**：`pytest tests/factor_engine/factor_db/ tests/factor_engine/test_evolution_loop.py -m "not slow"` 全绿 + ruff
6. **护栏**（✅ v2.104.0+80 完成）：`scripts/verify_factor_db_untouched.py` 接入 CI（`--mode snapshot|check` 三表 COUNT + 行级 md5 指纹，路径直读 `schema.DATABASE_PATH_*` 常量防被隔离 fixture 架空；`.github/workflows/ci.yml` test job 中 pytest 前 snapshot / 后 check；tests/scripts/test_verify_factor_db_untouched.py 6 用例）
7. **文档同步**：01-arch（测试隔离策略）/ 06-testing（新测试）/ 07-operations（版本历史）/ 08-gap（GAP-129 关闭）/ 09-advancement（里程碑）
8. **版本**：`bump_version.py --build`

## 6. 验收标准

- [x] 运行 `pytest tests/factor_engine/test_evolution_loop.py -m "not slow"`（或任意含晋升的测试组）后，真实 `factor_catalog_{futures,energy}.duckdb` 的 factor_catalog/factor_quality_scores/factor_audit_reports 三表行数与运行前完全一致
- [x] 豁免标记测试仍能断言真实路由（test_energy_chain/test_factor_db 等全绿）
- [x] 受影响模块回归全绿 + ruff 通过
- [x] `verify_doc_consistency.py` 全绿

## 7. 风险与边界

| 风险 | 应对 |
|:-----|:-----|
| 全局重定向影响依赖真实库数据的测试 | 审计枚举 + `uses_real_factor_db` 豁免标记；误标可通过护栏脚本暴露 |
| 模块级 import 时取 `_DB_PATH = get_db_path(...)` 绕过 fixture | 改造为函数内取值或显式隔离；审计步骤专项排查 |
| conftest 被多目录嵌套覆盖（tests/factor_engine/conftest.py 已存在） | 根 conftest autouse 先于子目录生效（pytest 加载顺序），语义叠加不冲突 |
| 测试并发/长任务中 tmp DB 生命周期 | fixture function 作用域，随测试结束清理；无跨进程竞争（E.4 短连接设计天然适配） |

## 8. 范围外

- 不改动 `factor_db_path` 注入机制（GAP-030 已提供，仅推广使用）
- 不回溯清理历史测试污染行（GAP-128 回填脚本孤儿清理已处理现存残留；隔离落地后不再新增）
