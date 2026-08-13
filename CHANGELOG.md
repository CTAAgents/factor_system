# Changelog

本文件记录 FTS（因子交易系统）的累积变更日志。版本号遵循 SemVer + build 段纪律
（`scripts/bump_version.py` 统一管理），正式版本历史见 `docs/harness/07-operations.md`。

## [v2.103.0+9] - 2026-08-13

> 注：v2.103.0+8 为本会话 bump（ruff/mypy 修复），+9 为并发会话同步推进
> （35-gap-closure-plan P0 批次，GAP-099/100/101 落地 + 版本头全量同步），
> 两者同属本次累积变更，统一记录于此。

### 修复（代码质量全量清零）

- **ruff lint 全量清零**：50 errors（34 自动 `--fix` + 16 手动）。
  - F841 未使用变量清理（`mhf_backtest.py`、`test_black_litterman.py` 等）。
  - E741 歧义变量名：OHLC 低价 `l` 属领域标准命名，4 个 operator 扩展测试
    文件采用文件级 `# ruff: noqa: E741` 豁免。
  - F541 f-string 无占位符（`scripts/gap_threshold_calibration.py`）。
- **mypy 全量清零**：198 errors（60 文件分类处理），188 源文件类型检查全绿
  （`--no-incremental` 复核确认，绕过增量缓存假象）。
  - TypedDict `total=False` 包内属性访问怪癖：改用 `.get("passed", False)` 绕开
    `"FactorEvaluation" has no attribute "passed"` 误报（`evolution_loop.py`、
    `evolution_futures.py`）。
  - `**` 展开 TypedDict 不兼容：`cast(CostConfig, ...)` 显式转换
    （`cost_model.py`、`cost_sensitivity.py`）。
  - 隐式 `Optional` 显式化、`dict[str, Any]` 配置容器化、变量遮蔽修正
    （`prometheus_metrics.py`）、私用 Protocol 替代 `object`（`success_pattern.py`）。
- **回归修复**：
  - `regime_probs` 双路径冲突：构造期单键 `{regime: conf}` 与 features 完整分布
    提升逻辑冲突，改为构造 `{}` 占位 + 提升条件 `is None` → `not`
    （`regime.py`，对齐 `test_detect_promotes_hmm_regime_probs_to_top_level`）。
  - microstructure 晋升测试 mock 形状对齐：`MagicMock` → `FactorEvaluation` dict，
    `_promote_to_elite` mock 签名对齐真实签名（`test_microstructure_promotion.py`）。

### 新增

- **演化引擎**：`evolution_futures.py`、`evolution_channels.py`、`evolution_uct.py`、
  `evolution_trace.py`（演化循环重构清单 plans/34 落地）；GAP-079 走航不足分支重构。
- **MHF 交易链路**：`mhf_backtest.py`、`mhf_evaluation.py`、`mhf_factors.py`、
  `mhf_signals.py`、`portfolio_turnover.py`、`live_trade/paper_trader_mhf.py`、
  `live_trade/tqsdk_mhf_executor.py`（plans/33 MHF 交易计划）。
- **存储层**：`store/duckdb_lock.py` 跨进程 filelock 写锁；`state_db.py` 迁移
  SQLite WAL 多读单写（E.3）；L2/L3 DuckDB 短连接生命周期（E.4）。
- **工具脚本**：`apply_reaudit.py`、`gap_threshold_calibration.py`、
  `migrate_state_to_sqlite.py`、`mhf_signal_pipeline.py`、`run_mhf_paper.py`、
  `run_mhf_tqsdk_exec.py`、`ar_factor_experiment.py` 等。
- **测试**：MHF 回测/信号/实盘、`test_duckdb_lock.py`、SQLite 迁移、
  宏观面板注入等用例。

### 清理（股票剥离 32-stock-extraction-plan 遗留）

- `fts/cross_market/`、`fts/data_mcp*.py`、`fts/data_fundamental.py`、
  `factor_engine/neutralization.py`、`factor_engine/stock_regime.py`、
  `factor_engine/extractors/stock_pipeline.py` 移除。
- 对应测试移除：`test_barra*.py`、`test_stock_regime.py`、`test_neutralization.py`、
  `test_cross_market*.py`、`test_data*.py`、`test_stock_pipeline.py`、
  `test_sync_stock_task.py` 等。
- 脚本移除：`build_fundamental_cache.py`、`cross_market_revalidation.py`、
  `daily_signal_pipeline.py`、`portfolio_analysis.py` 等。

### 文档

- 版本 bump v2.103.0+7 → v2.103.0+8，66 篇文档版本头同步。
- 新增设计文档 E.2/E.3/E.4（存储后端对比 / SQLite 状态库 / DuckDB 连接生命周期）、
  F.1/F.2（数据契约拆分 / 演化引擎分叉）。
- 新增计划文档 30-35（A股字段 / 股票基本面 / 股票剥离 / MHF 交易 /
  演化循环重构清单 / GAP 收尾）。
- plans/35 gap 阈值校准执行结果回填 §9.1：G4 `icir_min=0.30` 定值，
  G11 `turnover_daily_max` 暂缓（库中换手字段未回填，待 evaluation_chain 落地后复核）。
- 35-gap-closure-plan P0 批次（v2.103.0+9）：GAP-099 同向敞口惩罚
  （`AlignedExposureConfig` + `check_aligned_exposure`）、GAP-100 集中踩踏规避
  （`ExitStampedeConfig` + `throttle_exit_stampede`）、GAP-101 换手预算
  （`portfolio_turnover.py` + `l3_turnover_penalty` 默认 0.15）登记关闭；
  01-architecture.md 记录 `EvolutionSeedsMixin`（34 计划领域 D）接入。

### 提交记录

| Commit | 主题 | 文件数 |
|---|---|---|
| `85e4e83` | docs: v2.103.0+8 版本同步 + 累积计划/设计文档落库 | 72 |
| `88aecea` | fix(fts): ruff+mypy 全量修复 + 累积功能收敛 | 67 |
| `c2cf19a` | chore(scripts): 累积工具脚本入库 + lint 修复 | 50 |
| `acc0bb2` | test: 回归测试对齐 + 新增用例 | 77 |
| `52fcfb5` | docs(plan35): gap 阈值校准执行结果回填（§9.1） | 1 |
| `2609c4c` | docs: 新增 CHANGELOG.md（本文件） | 1 |
| `96b9a61` | docs: GAP-099/100/101 登记关闭 + evolution_seeds 架构记录 | 2 |

累计 269 文件变更，工作区已干净。

### 验证结果

- `ruff check fts/ scripts/ tests/`：**All checks passed**（全绿）。
- `mypy fts/ --no-incremental`：**Success: no issues found in 188 source files**。
- 受影响模块回归：712 passed / 26 deselected（`-m "not slow"`）。
- `verify_doc_consistency.py`：**全部通过**（版本号一致、流程文档完整）。
