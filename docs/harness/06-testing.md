# FTS 测试策略

> 版本: v3.1.0+7
> 最后更新: 2026-08-20

---

## 1. 测试金字塔

```
         ┌──────────┐
         │  E2E 测试 │    ← 全流程端到端验证
        ┌┴──────────┴┐
        │ 集成测试    │    ← 策略层/演化循环/数据源聚合集成验证
       ┌┴────────────┴┐
       │ 单元测试      │    ← 各模块独立测试（主体）
       └──────────────┘
```

| 层级 | 说明 |
|:-----|:-----|
| 单元测试 | 各模块独立测试（因子引擎/数据源/调度/风控/QA/脚本等，主体） |
| 集成测试 | 策略层 + 演化循环集成 + 数据源聚合 + 期货同步 |
| E2E | test_e2e.py 全流程闭环 |

**当前规模**：**8469 tests collected**（2026-08-20 收集口径）；最近一次全量回归 v3.1.0 = **8454 passed**（评审质检体系优化）；测试文件 118+；覆盖率 **94%**（v2.88.0 GAP-F16 补齐后 TOTAL，`--cov-fail-under=90` 达标）。slow 重量级测试 26 个（集中于 test_evolution_loop.py 真实演化/回测）。

---

## 2. 测试目录结构

```text
tests/
├── conftest.py                      # 全局 fixture（含 GAP-129 因子库隔离）
├── factor_engine/                   # 因子引擎（主体，含 qa/ expr_dsl/ operator_evolution/ 子包）
│   ├── test_evolution_loop.py       # L2 主循环（含 slow 重量级真实演化）
│   ├── test_l3_signal_service.py    # L3 信号矩阵服务（3D 矩阵/增量窗口追加）
│   ├── test_panel_vector.py         # 横截面全矩阵化（IC 矩阵化/缺口面板回退）
│   ├── test_rolling_native.py       # 算子 native 向量化对照（批 1/2/3）
│   ├── test_numba_kernels.py        # numba 算子内核对照
│   ├── test_subchain_*.py           # 子链画像/权重/质量矩阵/放行
│   ├── test_qa_gate.py / qa/        # 评审质检门禁 + Q1-Q10/月度/季度复检
│   ├── test_meta_loop.py            # L1 Meta-Loop
│   └── ...（其余模块单测）
├── data_sources/                    # 数据源（quantdata_provider/aggregator/roll_calendar）
├── scheduler/                       # 调度引擎与任务
├── monitor/  / risk/  / store/      # 监控 / 风控 / 存储域注册表
├── live_trade/                      # 模拟仓/撮合/仿真偏差/资金爬坡
├── workflow/                        # CTA 手册 WorkFlow 端到端工作流
├── scripts/                         # 运维脚本测试（rhi/cleanup_push_scope/seeds_yaml 等）
└── test_cli.py / test_config_settings.py / test_data*.py / ...（项目级）
```

---

## 3. pytest 配置

定义在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=fts --cov-report=term-missing -v"
markers = ["slow: 重量级真实演化/回测测试（日常回归默认跳过，全量验收必跑）"]
```

执行命令：

```bash
# 日常回归：跳过 slow 重量级测试，单进程执行规避 DuckDB 写锁冲突
python -m pytest tests/ -m "not slow" -q -o addopts="" -p no:cacheprovider

# 全量验收（含 slow 真实演化测试）：xdist 并行加速（锁冲突类测试单进程定向复核）
python -m pytest tests/ -q -o addopts="" --timeout=600 --tb=line -p no:cacheprovider -n 4 --dist loadfile

# 运行指定模块测试
python -m pytest tests/factor_engine/ -v

# 收集统计（不执行）
python -m pytest --collect-only -q -o addopts="" -p no:cacheprovider
```

**slow 分级说明（2026-08-13）**：`@pytest.mark.slow` 标记重量级真实演化/回测测试（当前 26 个，集中于 `test_evolution_loop.py`），单进程下单个测试可达数分钟。日常回归用 `-m "not slow"` 跳过；全量验收必跑。**DuckDB 并发约束**：xdist 多 worker 并发写 `factor_catalog_futures.duckdb`（L3 因子资产库）会锁冲突，锁冲突类测试单进程定向复核。

**测试因子库隔离（GAP-129，v2.104.0+79）**：根 `tests/conftest.py` 注册 `uses_real_factor_db` 标记 + autouse fixture `_isolated_factor_db`——单挂载点 `get_db_path` 将 futures/energy 全市场重定向至每测试独立 tmp DuckDB，常规测试不再写真实 `data/factor_catalog_*.duckdb`（零污染）。真实路由断言 / 真实存量因子数据依赖测试须显式标记 `@pytest.mark.uses_real_factor_db` 豁免。

**CI 零污染护栏（v2.104.0+80）**：`scripts/verify_factor_db_untouched.py`（`--mode snapshot|check`，三表 COUNT + 行级 md5 指纹对比）已接入 `.github/workflows/ci.yml` test job——任何测试写真实因子库 → 差异 → CI 失败。

---

## 4. 覆盖统计

| 指标 | 值 |
|:-----|:---|
| Overall coverage | **94%**（v2.88.0 GAP-F16 补齐后，`--cov-fail-under=90` 通过） |
| 测试用例数 | **8469 collected**（2026-08-20）；最近全量回归 8454 passed |
| 测试文件数 | 118+ |
| 种子因子数 | 期货 YAML **185**（含 fut_macro_import，按 style_tags 分类）+ 硬编码兜底 81 |
| 精英因子数 | DuckDB 因子资产库存储（`factor_catalog_futures.duckdb`，SSOT） |

---

## 5. 测试原则

- **回归测试分级执行**：日常任务仅跑受影响的模块/集成测试；**全量回归**仅在发布前（里程碑 bump/晋级里程碑）与每月底例行巡检执行；日常 build bump 不触发全量回归。
- **因子测试**：覆盖正常/极端/空/停牌数据，验证因子无未来函数、数值稳定、结果可控；因子逻辑修改后必须重跑 IC/单调性基准（无逻辑漂移、无数据泄露）。
- **回测测试**：固定测试数据集，验证迭代后指标无异常突变、逻辑无漂移。
- **实盘逻辑测试**：模拟网络异常、行情缺失、风控触发、订单失败，验证兜底机制生效。
- **性能基准**：`pytest tests/benchmarks/ -v --benchmark-only`（149 品种×3000 日数据集基准）。
- **所有新增功能必须配套单元测试**。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `pyproject.toml [tool.pytest.ini_options]` → §3 pytest 配置；`tests/conftest.py` → §3 因子库隔离；`scripts/verify_factor_db_untouched.py` → §3 CI 护栏 |
| 可验证断言 | 测试数 **8469**（2026-08-20 收集口径）；覆盖率 **94%**；`--cov-fail-under=90` 门禁通过 |
| 检验方式 | `python -m pytest --collect-only -q -o addopts="" -p no:cacheprovider`（8469 tests collected）；`python scripts/verify_doc_consistency.py`（版本号一致性 PASS） |
