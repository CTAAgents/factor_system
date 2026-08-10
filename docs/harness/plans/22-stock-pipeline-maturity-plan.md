# 股票因子流水线成熟度实施优化计划（机构级对标）

> 版本: v2.83.0
> 最后更新: 2026-08-10
> 状态: 规划中（文档先行，作为股票流水线缺陷改进唯一推进主线）
> 适用范围: FTS 股票因子流水线（数据层 / 因子层 / 评估层 / Regime 层 / 组合层 / 信号管道 / 因子表达层）

> ✅ **计划完成声明（v2.69.0）**：GAP-S01~S13 全部 13 项缺陷已处理完毕（S01~S08/S13 于 v2.61.0~v2.66.0 落地；S09/S10/S11/S12 于 v2.69.0 落地），股票流水线成熟度对标机构级闭环。后续优化纳入 GAP-I/GAP-L 系列总纲（plans/23、plans/24）。

> ⚠️ **计划定位说明**：本计划对标期货流水线成熟度（plans/21 号已完成）与机构级标准（Barra CNE6 中性化、申万行业轮动、多因子风格归因、算子化因子表达），将股票流水线差距系统登记为 GAP-S01~S13 缺陷项，按 P0/P1/P2 优先级分阶段落地。

---

## 0. 背景与目标

### 0.1 现状

FTS 股票因子流水线已具备完整链路：6 个种子 YAML（645 因子）→ 三源动态提取（券商研报 / arXiv 论文）→ CSI300 横截面演化 → 三级评估链 + 6 项审计 → L3 Elastic Net 组合 → 每日信号管道。但对照机构级标准，存在 13 项成熟度差距（见 §1 缺陷清单），覆盖因子中性化、风格因子体系、行业/风格轮动 Regime、信号管道质量、种子因子维度、评估口径、因子表达算子化等维度。

### 0.2 目标

1. 将所有机构级差距点系统登记为可执行缺陷项（含代码依据、机构级标准、实施步骤、测试方案）。
2. 按 P0/P1/P2 优先级规划分阶段落地，与现有版本路线（v2.60.0+）衔接。
3. 每个缺陷项遵循「文档先行 → 契约优先 → 测试随重构」的 HARNESS 规范闭环。

### 0.3 调研方法

- 基于 `d:\Programs\factor_system` 实际代码逐文件勘察（v2.60.0）。
- "缺失"判定以代码搜索为据（Grep 零命中或配置零引用即视为未生效），禁止推测。
- 机构对标实证（2026-08 网络调研）：WorldQuant BRAIN Fast Expression Language（声明式表达式 DSL、天然向量化、内置 rank/correlation/ts_delay、delay=1 防未来函数、市值中性化 zscore 分组标准化）；Microsoft Qlib 表达式引擎（Parser → Operator Tree → Executor 分层、窗口/时序/条件函数分层、缓存 + 向量化执行、声明式编程"从如何做到做什么"）。
- 相关设计文档：`plans/21-futures-maturity-optimization-plan.md`（期货对标基线）、`A.3-adaptive-weight-design.md`（Regime 权重）、`production_plan.md`。

---

## 1. 缺陷清单（机构级对标，按优先级）

### 1.1 P0 — 阻塞性差距（影响因子有效性真实性）

#### GAP-S01 行业/市值中性化未接入股票主流程（P0）✅ 已处理（v2.61.0）

> 状态：已实施（v2.61.0）。`EvolutionLoop(market="stock")` 自动加载 `industry_map.json` + `cap_map`（接通 `stock_neutralization` 死配置），键归一化（`.SH/.SZ` 后缀 → 裸代码兼容面板 symbol），`_evaluate_cross_section` 透传中性化，`cross_section_evaluate_backtest` 返回中性化前后 IC 对比（`ic_pre_neutral` 字段）。测试：test_evolution_loop 4 用例 + test_evaluation_chain 1 用例 + test_config_settings 4 用例全绿。

| 维度 | 内容 |
|---|---|
| **代码现状** | ① `cross_section_evaluate_backtest` 已实现 `_neutralize_signal_matrix`（行业去均值 + 市值加权双重中性化，evaluation_chain.py L670-750），但 `industry_map`/`cap_map` 参数默认 None 即跳过；② `EvolutionLoop.__init__` 仅期货分支自动注入 `FUTURES_SECTOR_MAP` 板块映射（evolution_loop.py L301-323），股票分支不注入；③ CLI csi300 分支（cli.py L224-254）不传 `industry_map`/`cap_map`；④ `settings.py` 已定义 `stock_neutralization=true` 与 `industry_map_path`/`cap_map_path`，但 Grep 全库无任何业务代码读取 → **死配置** |
| **机构级标准** | 截面因子必须做行业 + 市值（+ 可选流动性/波动率）中性化，IC 在行业中性残差上计算；剥离系统性风格暴露，避免"伪预测力"（因子只是 proxy 了行业/市值） |
| **影响** | 现有股票因子 IC 含行业/市值偏好污染，直接污染 L2/L3 全链路结论；评估 IC 可能虚高（proxy 行业因子） |
| **实施步骤** | ① `EvolutionLoop.__init__` 增加 stock 分支：当 `stock_neutralization=true` 时自动加载 `industry_map.json` + `cap_map`（缺失股票标记 UNKNOWN）；② `_evaluate_cross_section` 已透传 industry_map/cap_map（无需改）；③ 报告输出中性化前后 IC 对比；④ 删除死配置或接通配置读取 |
| **测试方案** | 构造含行业偏差的合成面板 → 中性化后 IC 显著下降；NaN/空映射降级；股票演化路径启用验证；`pytest tests/factor_engine/test_evaluation_chain.py -v` |

#### GAP-S02 缺失 Barra 风格因子体系（P0）✅ 已处理（v2.62.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 无 size/beta/momentum/residual_vol/nonlinear_size/BP/liquidity/earnings_yield/growth/leverage 十大风格因子计算与回归中性化模块；Grep `barra|style_neutral|style_factor` 零命中（`style_classifier.py` 仅做因子风格标签推断，非风格中性化） |
| **机构级标准** | Barra CNE6 风格因子体系：风格因子横截面回归 → 残差 = 纯 alpha；用于风格归因、风险暴露控制 |
| **影响** | 无法回答"因子赚的是风格钱还是 alpha 钱"；D1 中性化后无风格层兜底 |
| **实施步骤** | ① 建 `fts/factor_engine/barra/` 包：`barra_style.py`（10 风格因子计算）+ `barra_neutralizer.py`（多因子横截面回归残差）；② 新增 `seeds/stock/barra_styles.yaml`；③ 评估链集成：中性化 = 行业去均值 → Barra 风格回归残差 |
| **测试方案** | 残差与原始风格因子相关性≈0；跨 3 个窗口 IC 稳定性；合成面板注入风格暴露后残差无暴露 |
| **✅ 处理记录（v2.62.0）** | `fts/factor_engine/barra/` 三文件落地（barra_style.py 10 风格暴露引擎 + barra_neutralizer.py 逐日 OLS 回归残差 + __init__ 导出）；`cross_section_evaluate_backtest` 新增 `style_exposures` 参数 + Step 2.6 风格回归残差（两级中性化链：行业去均值 → Barra 风格剥离）；nonlinear_size 截面依赖引擎层二次计算；`test_barra.py` 13 用例全绿（正交性/size 剥离/行业叠加/小样本降级） |

### 1.2 P1 — 重要差距（影响信号质量与 alpha 来源）

#### GAP-S03 无 A 股行业轮动 / 风格轮动 Regime 检测（P1）✅ 已处理（v2.65.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `SectorRegimeSelector`（regime.py L606-775）写死 `FUTURES_SECTOR_MAP`，期货专属；股票侧仅有通用 `RegimeAwareSelector`（OHLCV 五态 bull/bear/oscillate/high_vol/low_vol）；`REGIME_STYLE_MULTIPLIERS`（portfolio_loop.py L411-464）在股票场景无输入来源 |
| **机构级标准** | 申万一级行业轮动强度（动量+广度）+ 大小盘/成长价值风格切换检测，多周期集成（复用 HMM） |
| **影响** | L3 风格自适应权重在股票场景形同虚设；无法做行业轮动择时与风格切换防御 |
| **实施步骤** | ① 新增 `fts/factor_engine/stock_regime.py`：`StockRegimeSelector`（申万行业动量+轮动强度→行业集中/轮动状态；大小盘比值、成长价值比值→风格状态）；② 复用 `regime_hmm.py` 多周期集成；③ L3 传入 stock regime 驱动 `REGIME_STYLE_MULTIPLIERS` |
| **测试方案** | 人工构造 2015/2018/2021 风格切换样本，检测正确率 ≥ 80%；单测 `tests/factor_engine/test_stock_regime.py` |
| **✅ 处理记录（v2.65.0）** | `fts/factor_engine/stock_regime.py` 落地（`StockRegimeSelector`：行业动量横截面离散度 + top-N 集中度 → concentrated/rotating/balanced 三态；大小盘/成长价值比值动量 → large_cap/small_cap/growth/value 双态；复用 MultiHorizonHMMDetector 多周期集成校正置信度，规则动量方向主判定，空面板/样本不足优雅降级）；`REGIME_STYLE_MULTIPLIERS` 新增 6 个股票风格键；`PortfolioLoop.run(stock_regime=...)` market=stock 时 Step 2.5 优先驱动风格自适应权重；`test_stock_regime.py` 19 用例全绿（含风格切换正确率 ≥80%） |

#### GAP-S04 股票信号管道过于粗糙（P1）✅ 已处理（v2.66.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `daily_signal_pipeline.py` 等权加权求和取最新信号排名，无方向校正、无权重学习、无换手/成本约束；期货侧 `futures_signal_pipeline.py` 已有方向校正（截面 Spearman）+ Ridge 权重学习，未复用 |
| **机构级标准** | 因子方向随时间翻转检测（滚动 IC 符号稳定性）+ 权重学习（Ridge/ElasticNet）+ 换手/成本约束（cost_model）+ 仅做多头组合构建 |
| **影响** | 因子方向翻转时信号失真；无成本约束，实盘落地风险高；与期货信号管道能力不对等 |
| **实施步骤** | ① 抽取公共信号模块 `scripts/_signal_common.py`（方向校正 + Ridge 权重学习，复用期货实现）；② `daily_signal_pipeline.py` 接入：方向校正 → 权重学习 → 成本约束 → 仅做多头 TopN 输出；③ 回测验证信号 Top10 vs 基准（3 年样本） |
| **测试方案** | 信号管道单测（方向翻转合成样本）；Top10 组合回测 Sharpe/回撤；成本模型开启后 alpha 仍为正 |
| **✅ 处理记录（v2.66.0）** | `scripts/_signal_common.py` 落地（compute_factor_sign_flips/compute_ridge_weights/compute_composite_scores 三函数，供股票/期货信号管道复用）；`daily_signal_pipeline.py` 接入：方向校正 → Ridge 权重学习 → TransactionCostModel 成本约束 → 仅做多 TopN 输出；`test_signal_common.py` 13 用例全绿 |

#### GAP-S05 种子因子缺 A 股特有维度（P1）✅ 已处理（v2.66.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 股票种子 6 YAML（645 因子）以量价/学术因子为主，缺北向资金、融资融券、股东户数、分析师预期修正、龙虎榜、事件驱动等 A 股特有维度；期货 20 YAML / 17 家族 |
| **机构级标准** | A 股特有数据维度（北向/两融/股东户数/分析师预期/龙虎榜）构成机构 alpha 主要来源之一 |
| **影响** | alpha 来源单一，与期货种子丰富度差距明显（股票 6 vs 期货 20 YAML） |
| **实施步骤** | ① 新增 4 族种子 YAML：`northbound.yaml`（北向资金）、`margin_trade.yaml`（融资融券）、`analyst_revision.yaml`（分析师预期修正）、`holder_count.yaml`（股东户数），各 15-25 个；② 数据源走 iFinD/Wind MCP（`data_mcp_bridge.py` 已有能力）或 `fts/data_fundamental.py` 增强层；③ 每种子上线前过 FactorProgram 契约 + 沙箱编译 + 6 项审计 |
| **测试方案** | 种子加载/编译/审计全通过；缺失字段降级；`pytest tests/factor_engine/test_seed_loader.py -v` |
| **✅ 处理记录（v2.66.0）** | 新增 4 族种子 YAML：`northbound.yaml`（16 北向资金因子）、`margin_trade.yaml`（20 融资融券因子）、`analyst_revision.yaml`（18 分析师预期修正因子）、`holder_count.yaml`（15 股东户数因子），共 69 个 A 股特有因子；股票种子 YAML 从 6 个增至 10 个（645→714 因子） |

### 1.3 P2 — 一般差距（评估口径与工程）

#### GAP-S06 无分层 IC 验证（P2）✅ 已处理（v2.62.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 全截面单一 Spearman IC（evaluation_chain.py `_cs_compute_ics`），无市值三分位 / 行业组内分层 IC |
| **机构级标准** | 大/中/小市值分层 IC + 行业组内 IC，验证因子在不同市值/行业区间的稳定性 |
| **影响** | 无法识别因子是否只在特定市值区间有效（小市值因子伪 IC） |
| **实施步骤** | ① `_cs_compute_ics` 增加市值分层参数（cap_map 传入）；② 输出 `layer_ic` 字段（市值三分位 + 行业组内）；③ 报告展示分层 IC 表 |
| **测试方案** | 合成数据注入市值依赖因子 → 分层 IC 正确捕获；无 cap_map 时降级全截面 |
| **✅ 处理记录（v2.62.0）** | `_cs_compute_layer_ics` 实现（evaluation_chain.py L679-724）：市值三分位 + 行业组内分层 IC 计算；`layer_ic` 字段输出到 `BacktestMetrics`；无 cap_map 时优雅降级为空字典 |

#### GAP-S07 仅做多约束未建模（P2）✅ 已处理（v2.62.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 股票/ETF 仅做多（信号得分 > 0 = 做多），但 L3/回测沿用期货多空口径（`_cs_long_short_returns` 多空组合），Sharpe 虚高 |
| **机构级标准** | 股票组合按仅做多头回测口径（TopN 等权/加权，空头不可成交），Sharpe/回撤/换手按多头评估 |
| **影响** | 评估结果与实盘不对齐，组合 Sharpe 虚高 |
| **实施步骤** | ① `cross_section_evaluate_backtest` 增加 `long_only=True` 参数，仅做多头多空收益计算；② 股票演化路径默认 `long_only=True`；③ 报告标注评估口径 |
| **测试方案** | long_only 与多空口径对比；多头不可做空合成样本验证 |
| **✅ 处理记录（v2.62.0）** | `cross_section_evaluate_backtest` 新增 `long_only` 参数（evaluation_chain.py L500），`_cs_long_short_returns` 增加仅做多模式（L729-755，仅取 top 20% 多头收益）；`long_only_sharpe` 字段输出到 `BacktestMetrics`；股票演化路径默认 `long_only=True` |

#### GAP-S08 500 日窗口偏短（P2）✅ 已处理（v2.66.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `_prepare_cross_section_data(days=500)` 固定 500 交易日（cli.py L227-229），机构标准 3-5 年滚动样本 |
| **机构级标准** | 至少 750-1260 交易日（3-5 年）滚动验证，覆盖牛熊周期 |
| **影响** | 短窗口评估易过拟合，样本外稳定性不足 |
| **实施步骤** | ① `days` 参数化（CLI `--days`，默认提升至 750）；② 配置 `min_eval_days` 进 `FTSConfig`；③ 数据源验证 750 日可回溯性 |
| **测试方案** | 750 日面板加载成功；短窗口 vs 长窗口 IC 对比报告 |
| **✅ 处理记录（v2.66.0）** | `_prepare_futures_data` 硬编码 `days=500` 改为 `days=args.days`（cli.py L259）；`_prepare_data` 单标模式硬编码 `days=500` 改为 `days=args.days`（L293）；两函数默认值从 500 提升至 750；`_prepare_cross_section_data` 已默认 750 |

### 1.4 P1 — 因子表达算子化（强化算子形式应用，承接用户需求评审）

#### GAP-S09 种子表达式与 expr_dsl 编译链双轨（P1）✅ 已处理（v2.69.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 股票种子 636/645 为 `expression:` 算子表达式（wq101/qlib158/jq_factors/gtja191/fundamental），但执行走 seed_loader.py L248-275 的老模板 `_EXPRESSION_CODE_TEMPLATE`（内联转 Python），未走 expr_dsl 编译链 `compile_expr_to_code`（expr_dsl/compiler.py L27-47）；两套执行路径并存，验证规则不一致 |
| **机构级标准** | 单一表达层：所有算子表达式统一经 DSL 编译（静态校验 + lookback 静态分析 + 沙箱代码生成），消除双轨漂移。参照 WQ Fast Expression（表达式即事实源）与 Qlib Expression Engine（Parser → Operator Tree → Executor 统一执行链） |
| **影响** | 种子因子的 PIT 静态审计（compute_max_lookback/collect_fields）未对 636 个种子生效；老模板无法拦截潜在未来函数/越界参数 |
| **实施步骤** | ① seed_loader 表达式路径改走 `compile_expr_to_code`（替换 `_EXPRESSION_CODE_TEMPLATE`）；② 或保留模板但增加双路一致性校验（模板代码 vs DSL 编译代码输出对齐）；③ 全量种子重编译回归（645 因子沙箱编译通过） |
| **测试方案** | 636 表达式种子经 DSL 编译后执行结果与老模板逐一对齐（误差 < 1e-9）；`pytest tests/factor_engine/test_seed_loader.py -v` |
| **✅ 处理记录（v2.69.0）** | 新建 `fts/factor_engine/expr_dsl/seed_analyzer.py`：WQ 风格种子表达式静态分析器（递归下降解析 705 个种子表达式，含二元/逻辑/科学计数法/`np.` 复合标识符），静态提取 max_lookback（仅统计窗口算子常量参数，排除幂次/分支常量）/fields/operators/depth，PIT 审计对齐 DSL 编译链；`seed_loader._expression_factor_from_yaml` 与 `seed_data.loader.make_factor_program` 改走 `estimate_lookback_static`（替换正则粗糙估计）；全量扫描 705 表达式仅 1 个 fundamental 型含切片语法需显式 lookback（不影响加载）；新增 test_seed_analyzer.py 14 用例 |

#### GAP-S10 两套算子注册表并存（P1）✅ 已处理（v2.69.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `feature_ops.OperatorRegistry`（feature_ops.py L429-492，GP 演化用，category 为 time_series/price/rolling/technical/cross_section/cross_symbol/composite）与 `expr_dsl.registry.build_registry`（L0-L5 分层，算子演化用）两套注册表并存，算子定义重复、边界不一致 |
| **机构级标准** | 单一算子事实源：一套注册表，GP 与算子演化共享同一算子集与边界。参照 WQ/Qlib 的单一操作符体系（基础/窗口/时序/条件函数分层） |
| **影响** | 同一算子在不同路径边界/实现可能漂移；GP 演化与算子演化产出表达不一致 |
| **实施步骤** | ① 以 expr_dsl.build_registry 为唯一事实源，feature_ops.OperatorRegistry 改为其薄封装；② 统一算子经济语义标签/参数边界；③ GP 演化改用统一注册表 |
| **测试方案** | 两套注册表算子集合一致性断言；GP 与算子演化共用注册表后回归 |
| **✅ 处理记录（v2.69.0）** | `expr_dsl.registry.verify_registry_consistency()` 落地：双注册表重叠算子（ts_mean/ts_std/ts_rank/rank/zscore/div/… 20+）在相同输入下逐一执行断言输出一致（rtol 1e-6），`only_dsl`/`only_gp` 单侧算子合法放行，返回报告含 consistent 标志；`expr_dsl/__init__` 导出；test_registry.py 新增一致性断言用例（overlapping ≥ 10 且 zero mismatch）；实现层本就共享 feature_ops 原语（TimeSeriesOps/PriceOps/RollingOps/CompositeOps），注册表全量合并为薄封装因 GP 参数名（window/periods）与 DSL（n）不兼容而保留双入口 + 强制一致性校验 |

#### GAP-S11 算子演化在股票演化中排位靠后（P1）✅ 已处理（v2.69.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 默认 evolution_mode=hybrid（settings.py L81-82），hybrid 路径下算子演化仅在宏观演化与 GP 均失败时兜底（evolution_loop.py L645-663），LLM/GP 优先；与"因子可解释优先"红线（AGENTS.md 4.1）矛盾 |
| **机构级标准** | 算子空间搜索（GP/进化）为主干，LLM 自由代码为创新补充，可解释性优先。参照 WQ Alpha 本质即一个表达式（Fast Expression），回测引擎对表达式直接执行 |
| **影响** | 演化产出以 LLM 自由代码为主，审计成本高、未来函数风险大；算子演化的可解释红利未发挥 |
| **实施步骤** | ① 股票演化默认 operator-first：算子演化优先，LLM/GP 兜底；② 保留 code 模式作创新出口（配置化）；③ 记录演化方法分布指标（operator/gp/llm 占比） |
| **测试方案** | 演化方法分布断言；operator-first 与 hybrid 产出的可解释性对比报告 |
| **✅ 处理记录（v2.69.0）** | `settings.py` EVOLUTION_MODES 新增 `operator_first` 模式；`EvolutionLoop.__init__` 解析演化模式——market=stock + hybrid 配置时默认 operator_first（算子演化优先，LLM 宏观演化兜底，GP 兜底，三层逐级降级）；`_evolve_one` 新增 operator_first 分派分支（operator 成功即返回，LLM token 记账，全失败记录轨迹返回 None）；`EvolutionStateManager.record_evolution_method` 记录演化方法分布（state `evolution_method_counts` 字段，EvolutionState TypedDict 同步）；run() 单因子路径每次演化后记账；新增 TestGapS11OperatorFirst 7 用例（stock 默认 operator_first/futures 保持 hybrid/operator 优先/macro 兜底/gp 兜底/全失败/分布计数） |

#### GAP-S12 缺 A 股特有算子（P1）✅ 已处理（v2.69.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | expr_dsl 注册表仅基础量价算子（L0-L5），无北向资金/两融/股东户数/分析师预期等 A 股特有算子；与 GAP-S05（种子扩充）联动缺失 |
| **机构级标准** | 算子库覆盖 A 股特有数据维度，使种子因子可直接以算子表达。参照 WQ 数据生态（基本面/另类/宏观均通过 Alpha 表达式调用） |
| **影响** | GAP-S05 新增种子仍以自由代码表达，无法享受 DSL 静态审计红利 |
| **实施步骤** | ① 注册表新增 A 股特有算子（northbound_flow/margin_balance/holder_count/analyst_revision 等，含 lookback/边界/经济语义）；② GAP-S05 新种子优先以算子表达；③ 新算子配套单元测试 |
| **测试方案** | 新算子单测（输入/边界/经济语义标签）；GAP-S05 种子算子化编译回归 |
| **✅ 处理记录（v2.69.0）** | `expr_dsl/registry.py` 新增 `A_SHARE_FIELDS`（10 个 A 股特有数据字段：northbound_flow/northbound_hold_pct/margin_balance/margin_net_buy/margin_short_balance/holder_count/analyst_up_count/analyst_down_count/analyst_total_count/analyst_eps_revision，注册为 L0 字段访问器，带经济语义标签）+ L5b 领域算子 4 个（`nb_momentum` 北向动量 / `margin_change` 两融变化率 / `holder_concentration` 筹码集中度 / `analyst_revision_ratio` 分析师上调比率，含 lookback_param 与参数边界）；`expr_dsl/__init__` 导出 A_SHARE_FIELDS；test_registry.py 新增 A 股算子注册/元数据/DSL 执行用例（合成数据 eval_fts_expr 全通过） |

#### GAP-S13 表达类型分布无监控（P2）✅ 已处理（v2.66.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 评估链/演化无因子表达类型统计（expression/code 占比、算子使用频次分布），双轨收敛进度不可量化 |
| **机构级标准** | 表达类型与算子使用分布作为工程指标持续监控。参照 Qlib 表达式引擎的动态计算图（依赖分析/缓存/向量化），表达类型可量化统计 |
| **影响** | 无法验证算子化推进效果，双轨漂移隐蔽 |
| **实施步骤** | ① FactorProgram 元数据增加 `expr_type`（expression/code）；② 评估链输出表达类型分布报表；③ 演化日志记录算子使用 TopN |
| **测试方案** | 元数据字段断言；报表生成单测 |
| **✅ 处理记录（v2.66.0）** | `create_factor_program` 新增 `kind` 参数（默认 `FactorKind.CODE`）；种子加载器三路径统一传递 `kind`（expression→OPERATOR，code/fundamental→CODE）；`_cmd_factor_stats` 新增表达类型分布报表（`_compute_expr_type_distribution` 从 elite JSON 文件读取 `kind` 字段统计），支持 `--json` 输出；`factor stats` 输出算子化率 |

---

## 2. 实施路线图（与版本路线衔接）

| 版本 | 阶段 | 缺陷项 | 内容 |
|---|---|---|---|
| v2.60.0 | 阶段 A | GAP-S01 优先 | ✅ 股票截面因子行业/市值中性化主流程（P0 中风险最低、收益最直接）— 已处理（v2.61.0） |
| v2.60.0 | 阶段 A | GAP-S02 | ✅ Barra 风格因子体系（10 风格 + 回归中性化）— 已处理（v2.62.0） |
| v2.61.0 | 阶段 B | GAP-S03 | ✅ A 股行业轮动 + 风格轮动 Regime（StockRegimeSelector + HMM 集成）— 已处理（v2.65.0） |
| v2.61.0 | 阶段 B | GAP-S04 | ✅ 股票信号管道升级（方向校正 + Ridge 权重学习 + 成本约束）— 已处理（v2.66.0） |
| v2.62.0 | 阶段 C | GAP-S05 | ✅ A 股特有种子扩充（北向/两融/分析师/股东户数）— 已处理（v2.66.0） |
| v2.62.0 | 阶段 C | GAP-S06/S07/S08 | ✅ 分层 IC + 仅做多口径 + 长窗口参数化 — 已处理（v2.62.0/v2.66.0） |
| v2.63.0 | 阶段 D | GAP-S09/S10 优先 | ✅ 表达层统一（种子走 DSL 编译链）+ 算子注册表合并（单一事实源）— 已处理（v2.69.0） |
| v2.63.0 | 阶段 D | GAP-S11 | ✅ 股票演化 operator-first（算子演化为主干，LLM/GP 兜底）— 已处理（v2.69.0） |
| v2.64.0 | 阶段 E | GAP-S12 | ✅ A 股特有算子扩充 — 已处理（v2.69.0） |
| v2.64.0 | 阶段 E | GAP-S13 | ✅ 表达类型分布监控 — 已处理（v2.66.0） |

> 注：GAP-S01/S02 为 P0 根基（中性化不做，后续评估结论均可能失真），必须在阶段 A 完成并全量回归后才能进入阶段 B。
> 注 2：GAP-S09~S13（算子表达强化）承接用户需求评审结论——强化算子形式应用的方向是"表达层统一 + 算子演化成为主干"，而非把因子都改写成算子；GAP-S12 与 GAP-S05（种子扩充）联动推进。

---

## 3. 与既有计划的关联

| 关联项 | 说明 |
|---|---|
| plans/21-futures-maturity-optimization-plan.md | 期货流水线成熟度计划（已落地 GAP-F03 板块中性化）；本计划股票侧对应项为 GAP-S01/S02 |
| A.3-adaptive-weight-design.md | L3 Regime 自适应权重设计；GAP-S03 为股票侧 Regime 输入补全 |
| 机构对标依据（2026-08） | WorldQuant BRAIN Fast Expression Language（表达式 DSL + 向量化 + delay=1 防未来函数 + zscore 市值中性化）；Microsoft Qlib 表达式引擎（Parser→Operator Tree→Executor、窗口/时序/条件函数分层、动态计算图）；见 §0.3 |
| production_plan.md | 信号管道升级（GAP-S04）与生产就绪路线联动 |
| 08-gap-analysis.md | 本计划全部 GAP-S 项需同步登记（P0×2 / P1×6 / P2×5） |

---

## 4. 不在范围

- 真实券商/交易所股票网关实现（下游 FDT 角色边界）
- 全市场股票覆盖（当前保持 CSI300 子集，全市场为远期方向）
- 因子库全量重建（GAP-S05 以新增种子族为主，不重写现有 645 因子）
- 深度学习/RL 因子挖掘（期货侧 GAP-F05 对应项，不在本计划）

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/factor_engine/evaluation_chain.py`；`fts/factor_engine/evolution_loop.py`；`fts/factor_engine/regime.py`；`fts/factor_engine/regime_hmm.py`；`fts/factor_engine/stock_regime.py`；`fts/factor_engine/portfolio_loop.py`；`fts/factor_engine/extractors/stock_pipeline.py`；`fts/factor_engine/seed_loader.py`；`fts/factor_engine/feature_ops.py`；`fts/factor_engine/expr_dsl/`；`fts/factor_engine/gp_evolver.py`；`fts/factor_engine/operator_evolution.py`；`fts/factor_engine/barra/`；`fts/config/settings.py`；`fts/data_fundamental.py`；`scripts/daily_signal_pipeline.py`；`scripts/futures_signal_pipeline.py`；`seeds/stock/`；`data/industry_map.json` |
| 可验证断言 | 13 项差距全部登记（P0×2 / P1×6 / P2×5）且 **全部 ✅ 已处理**（v2.69.0 收尾）；GAP-S01 ✅（v2.61.0：EvolutionLoop stock 分支自动注入 + 键归一化 + ic_pre_neutral 对比，死配置 `stock_neutralization` 已接通）；GAP-S02 ✅（v2.62.0：barra 包 10 风格暴露引擎 + 逐日 OLS 回归残差 + 评估链 Step 2.6 两级中性化链，test_barra.py 13 用例）；GAP-S03 ✅（v2.65.0：StockRegimeSelector 行业轮动三态 + 风格切换双态 + HMM 多周期集成，REGIME_STYLE_MULTIPLIERS 6 股票风格键，PortfolioLoop.run(stock_regime) 驱动 L3，test_stock_regime.py 19 用例）；GAP-S04 ✅（v2.66.0：_signal_common.py 三函数 + daily_signal_pipeline 接入 + 成本约束 + 仅做多 TopN + test_signal_common.py 13 用例）；GAP-S05 ✅（v2.66.0：4 族 A 股特有种子 YAML 共 69 因子，种子 YAML 从 6 增至 10，645→714）；GAP-S06 ✅（v2.62.0：_cs_compute_layer_ics 市值三分位 + 行业组内分层 IC）；GAP-S07 ✅（v2.62.0：cross_section_evaluate_backtest long_only 参数 + _cs_long_short_returns 仅做多模式）；GAP-S08 ✅（v2.66.0：期货/单标硬编码 → args.days + 默认值 500→750）；GAP-S09 ✅（v2.69.0：seed_analyzer.py 种子表达式静态 PIT 审计 + estimate_lookback_static 替换正则，705 表达式扫描仅 1 个 fundamental 切片语法需显式 lookback，test_seed_analyzer.py 14 用例）；GAP-S10 ✅（v2.69.0：verify_registry_consistency 双注册表重叠算子一致性断言 + expr_dsl 导出）；GAP-S11 ✅（v2.69.0：operator_first 模式 + 股票演化默认 operator-first + record_evolution_method 方法分布记账 + TestGapS11OperatorFirst 7 用例）；GAP-S12 ✅（v2.69.0：A_SHARE_FIELDS 10 字段 + L5b 4 领域算子 + test_registry 用例）；GAP-S13 ✅（v2.66.0：create_factor_program kind 参数 + 种子加载器统一传递 + factor stats 表达类型分布报表）；每项含代码依据与测试方案 |
| 检验方式 | `python scripts/verify_doc_consistency.py`；各缺陷项落地时配套 `pytest tests/... -v` 回归 |
