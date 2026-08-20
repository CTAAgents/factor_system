# 量化交易项目｜因子挖掘·投研·回测·实盘 全场景生产级 AGENTS.md

> **适用场景**：量化因子挖掘、多因子投研、策略开发、策略迭代回测、自动化实盘交易、风控治理、AI量化智能体开发（覆盖股票/期货/ETF/加密货币全品类）
>
> **核心原则（四场景统一）**：因子可解释优先（黑箱因子须经统计验证）、回测可复现、实盘可落地、零未来函数、零数据泄露、零数据窥探、统计严谨（多重检验）、全链路可追溯、风控压倒一切。

## 一、项目基础规范

- **技术栈**：Python 3.11+, Pandas, NumPy, SciPy, Statsmodels, TA-Lib, Numba, DuckDB, Pydantic V2，可选：LangGraph（交易智能体）
- **Python路径**：'C:\Program Files\Python312\python.exe'
- **K线主路径**（唯一数据源 QuantData，统一路由引擎含熔断器+读取缓存）：`DUCKDB_CACHE`（QuantData 读取缓存）→ `QUANTDATA`（唯一权威源，D:\QuantData duckdb 只读短连接直读 continuous_daily/continuous_map/kline_daily，FTS 对 QuantData 采集方式无感）→ `SYNTHETIC`（合成数据兜底保证可运行）；天勤（TQSDK 分钟·tick·增强）、通达信实时快照（TDX_LOCAL）、AKSHARE 已从默认聚合器移除（显式扩展场景 opt-in）；`get_realtime_prices` 保留供显式调用；`WIND`/`IFIND` 仅用于字段增强层（宏观/基本面），多源交叉验证优先（≥2 源同日数据差异 >0.5% 记录分歧）
- **数据持久化（六层存储架构，SSOT 单一事实源）**：L1 配置层（`seeds/*.yaml` + `config/`）→ L2 行情库（`data/fts_history.duckdb`，kline/minute/tick，追加式 + 按年冷热归档）→ L3 因子资产库（`data/factor_catalog_{stock,futures}.duckdb`，因子/评估/版本/审查/血缘，**DuckDB 为权威存储**）→ L4 运行状态库（`data/state.db` **SQLite WAL**，`state_kv` + `state_history` 可回放）→ L5 信号缓存（Parquet 列式 + checksum，`memory/cache/factor_signals/`）→ L6 日志血缘（JSONL）。**核心约束**：结构化数据统一进 DuckDB/SQLite 且一数一源，禁止同类数据双写漂移；JSON 仅作只读快照/兼容回退；`fts/store/`（StorageRegistry + storage_landscape.yaml）为唯一读写入口契约，新增数据域必须先登记后落库；写连接一律短生命周期（filelock 跨进程串行），读连接 `read_only=True`；SQLite 备份必须 `VACUUM INTO` 导出一致快照
- **四大核心场景**：因子挖掘（离线）、策略开发与回测（仿真）、自动化实盘（生产）、风控治理（贯穿），场景代码完全解耦、逻辑严格对齐；环境隔离：研发/回测/实盘配置独立，禁止混用
- **通用开发准则**：向量化优先、参数配置化、逻辑极简、分层解耦、兼容离线与生产双模式

## 二、标准化目录结构

```text
src/
├── research/        # 【场景1：因子挖掘｜离线专属】data/factors/labeling/analysis/visualize
├── backtest/        # 【场景2：策略回测｜仿真专属】engine/simulation/metrics/report
├── live_trade/      # 【场景3：实盘交易｜生产专属】gateway/strategy/risk_control/monitor
├── common/          # 全场景公共能力：data/logger/utils/exception
├── config/          # 场景化配置（research/backtest/live 独立）
└── agent/           # 可选：LangGraph 交易智能体流程编排
tests/  scripts/  docs/
```

## 三、全局编码通用规范

- 全代码强制类型注解；业务参数/阈值/约束全部使用 Pydantic V2 配置模型管理，禁止硬编码。
- 禁用原生 print，统一使用分级日志；数值计算强制兜底（NaN/Inf/零值/极端行情）；时间与序列统一 `datetime64`/`Timestamp`，禁止字符串比较排序。
- 文件小写下划线、类名大驼峰、函数语义化命名，核心代码补充完整 docstring；量化计算优先向量化；依赖管理用 requirements.txt/pyproject.toml 锁定版本。
- 提交前自查：未来函数、数据窥探、硬编码问题。

## 四、分场景强制红线规则

### 4.1 因子挖掘场景红线（离线）
- 绝对禁止未来函数（因子计算仅用当前及历史数据）；严格数据时间切割（训练/验证/测试按时间分割，禁止泄露、样本复用）。
- 零数据窥探（禁止用全样本统计量选因子/定参）；多重检验校正（Bonferroni/FDR）；因子去冗余（入库前相关性筛查，共线只留代表）。
- 因子可解释性优先：确需黑箱/降维复杂因子时须满足统计验证 + 血缘登记 + 显著优于可解释替代；固定随机种子；单因子职责单一；因子统一入库 `factor_catalog_*.duckdb`（SSOT），禁止落散文件绕过存储层。

### 4.2 策略回测场景红线（仿真）
- 回测与实盘强对齐；全真市场模拟（滑点/手续费/涨跌停/停牌/容量）；时序绝对单向，禁止回溯修正、事后优化。
- 禁止曲线拟合（跨周期、跨标的通用）；幸存者偏差过滤（标的池为当期实际可交易标的）；回测结果可复现；样本外验证（独立未参与优化的冷启动数据）。

### 4.3 实盘交易场景红线（生产最高优先级）
- 风控优先于交易（前置多层风控校验，不通过直接拦截）；异常容错兜底（重试/兜底/告警，不爆仓、不重仓）；禁止无止损持仓、逆势加仓、超限额重仓。
- 全链路日志留存（行情→因子→信号→风控→订单→成交）；实盘参数独立隔离；人工干预通道（紧急暂停/一键平仓，权限高于自动化）；灰度发布（小资金/模拟盘观察后逐步放大）。

## 五、分场景开发规范

### 5.1 因子挖掘
挖掘流程：假设驱动 → 数据准备 → 因子构建 → 单因子检验 → 中性化/正交化 → 去冗余 → 入库，逐步留痕。标签构建前向收益与持有周期严格对齐。因子输出统一标准化结构化结果，必须配套有效性检验（IC/IR/单调性/分位数收益/换手/IC 衰减）。支持多周期复用；中性化处理（市值/行业/风格）；复合因子前分析相关性。

### 5.2 策略开发与回测
策略链路：因子信号 → 信号合成/加权 → 组合构建 → 成本与容量评估 → 回测验证。事件驱动回测；标准化输出评价指标（累计/年化收益、最大回撤、胜率、盈亏比、夏普、最大连续亏损、交易频次、换手率）；成本敏感性分析；样本外验证；自动生成报告（收益/回撤曲线、交易明细、因子有效性）；压力测试（2020-03、2015 股灾）。

### 5.3 实盘交易
行情、信号、下单时序严格对齐；多层风控体系（单笔限额/单标的仓位/总仓位/单日最大亏损/连续亏损暂停）；订单状态全生命周期管理；实盘禁止动态随意改参（留痕可回退）；交易成本监控。

### 5.4 LangGraph 智能交易Agent规范（可选）
区分研发/回测/实盘模式（配置切换）；交易状态统一由 State 管理；节点单一职责；异常捕获写状态日志、实盘自动熔断；Agent 决策步骤全记录。

## 六、分场景测试规范

- 因子测试：正常/极端/空/停牌数据，无未来、数值稳定、结果可控；因子逻辑修改后重跑 IC/单调性基准。
- 回测测试：固定数据集验证指标无异常突变、无数据泄露；实盘逻辑测试：模拟网络异常/行情缺失/风控触发/订单失败，验证兜底。
- **回归测试分级执行**：日常任务仅跑受影响的模块/集成测试；**全量回归**只在发布前（里程碑 bump/晋级里程碑）与每月底例行巡检执行；日常 build bump 不触发。
- slow 分级：重量级真实演化/回测测试统一标记 `@pytest.mark.slow`，日常回归用 `-m "not slow"` 跳过；DuckDB 嵌入式单进程写约束下 xdist 并发写会锁冲突，锁冲突类测试单进程定向复核。
- 性能基准测试：关键计算路径设定性能基准（149 品种×3000 日数据集）。所有新增功能必须配套单元测试。

## 七、项目运维命令规范

```text
ruff format src/ tests/           # 代码格式化
ruff check src/ tests/            # 静态校验
mypy src/                         # 严格类型检查
pytest tests/ -v                  # 全量（仅发布前/月度巡检）
pytest tests/<模块>/ -v           # 模块测试
pytest tests/ -m "not slow" -q -o addopts="" -p no:cacheprovider   # 日常回归
pytest tests/benchmarks/ -v --benchmark-only                       # 性能基准
python scripts/bump_version.py --build --message "..."             # 版本 bump
python scripts/verify_doc_consistency.py                           # 文档一致性
```

## 八、受保护文件/目录（禁止擅自修改）

AI仅可读，如需修改需专项确认：`config/`、`common/data/`、`live_trade/risk_control/`、`backtest/simulation/`、已上线稳定因子与实盘运行策略核心算法、`docs/architecture_decisions.md`。

## 九、AI标准化开发工作流

1. 需求理解与澄清（目标/验收标准/风险点）→ 2. 确认当前开发场景 → 3. 数据可用性确认 → 4. 前置合规校验（未来函数/数据泄露/窥探/时序/风控漏洞）→ 5. 设计评审 → 6. 按目录分层开发 → 7. 补全类型注解与注释 → 8. 编写对应场景单元测试 → 9. 格式化/静态检查/模块测试 → 10. 样本外/回测验证 → 11. 输出修改说明与使用注意事项 → 12. 代码审查与合并。

## 十、Claude Code 兼容规则

在项目根目录 CLAUDE.md 中添加 @AGENTS.md 即可自动继承所有规范，实现多 AI 工具规则统一。

## 十一、AI 行为反模式与质量护栏

以下 10 条反模式由 AI/开发者提交前必须自查，命中即整改：

| ID | 反模式 | 严重度 |
|:--:|--------|:------:|
| AP01 | 巨型 Prompt（AGENTS.md > 300 行） | P1 |
| AP02 | 跳过审核直接编码（无 plan/spec） | P0 |
| AP03 | Rules 不维护（> 30 天未改） | P1 |
| AP04 | MCP 过度接入（> 10 个） | P2 |
| AP05 | Skill 不原子化（> 200 行） | P1 |
| AP06 | 盲目信任 AI 输出（无独立验证） | P0 |
| AP07 | 循环无停止条件（Loop stop 为空） | P0 |
| AP08 | 多循环共写 STATE | P1 |
| AP09 | Chat 历史当文档（知识仅在对话历史） | P2 |
| AP10 | 一个 PR 改所有（> 20 文件） | P1 |

**文档一致性校验（必跑）**：涉及 `docs/harness/` 变更后必须运行 `python scripts/verify_doc_consistency.py`——校验各文档版本头与 pyproject.toml 一致、含一致性元数据表格，报告 PASS/FAIL。

**知识库参考**：回答 FTS 相关问题优先参考本机个人知识库 `D:\Knowledge`，按相关性读取引用其中的内容，禁止凭空推断。历史计划/设计/验收文档已归档至 `docs/archive/`（索引见 `docs/archive/README.md`）。
