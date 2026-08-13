# FTS 差距分析

> 版本: v2.103.0+11
> 最后更新: 2026-08-11
> 状态: 活跃 — 随项目迭代持续更新

---

## 1. 差距总览

| 优先级 | 开放 | 已关闭 | 总计 |
|:-------|:-----|:-------|:-----|
| P0 | 0 | 20 | 20 |
| P1 | 1 | 49 | 50 |
| P2 | 7 | 48 | 55 |
| GAP-C（Stage 3C 远期） | 1 | 7 | 8 |
| **合计** | **8** | **124** | **132** |

> 注：GAP-068/069（P1，原延期项）已于 v2.101.0 关闭；GAP-075（P1，跨标的稳健性检查）于 v2.101.0 收尾关闭（cross_symbol 激活 + 标的留出验证）；GAP-C 系列为 Stage 3C 远期机构级差距（细则见 plans/23 §3.3），C1~C8 已于 2026-08-11 全部首期实施（C1 含评估晋升接线 / C2 含 LLM 精修 / C8 含 C9 算子扩容二期 DSL 132），C4 开放项为真实多机集群部署（单机 LocalCluster 代码/测试/基准已落地），待硬件/基建条件成熟后按 DaskBackend 抽象接入。
> GAP-081~089（数据字段缺口）于 2026-08-11 由数据字段字典审计（docs/factor_data_dict/）登记——A 股增强字段（北向/两融/股东/分析师）与期货持仓/结算字段为 P0 阻塞性数据缺口，详见下方登记表。
> GAP-092~095（plans/28 Regime 机构级优化远期差距）于 2026-08-11 登记——宏观四象限 / RL 制度条件决策 / isotonic 概率校准 / blend 幂次调节，均为 P2 远期项，详见 P2 新登记表。
> 总览开放口径修订（2026-08-12）：开放 = 登记表状态非「✅ 已关闭」项——P0 0 项、P1 1 项（GAP-097 开放，2026-08-12 登记——信号管道未与因子资产库接通）、P2 6 项（GAP-092~095 开放 + GAP-089/090 🟡 受限）、GAP-C 1 项（C4 真实多机集群后置）；**GAP-082 于当日关闭（股票基本面字段数据源接入——估值/市值日频 + 财务/成长季度真实数据，plans/31，P0 项移除）**；**GAP-084/086/087 于当日关闭（股票 OHLCV 虚构字段 / 市值中性化默认空转 / 股票侧宏观字段硬编码，P1 开放 4→1）**；**GAP-096 于当日收尾关闭（cross_symbol A+C 双机制验证通过，P1 开放 1→0，合计开放 9→8）**；**GAP-088 于当日关闭（期货宏观注入端闭环——面板级注入 helper + 期货信号管道/横截面演化接线，P2 开放 7→6、合计开放 8→7）**；**GAP-089 于当日受限登记（高频数据深度受外部数据源制约——逐笔/10 档/订单簿重建非本机可实现，登记受限不假完成，🟡）**；**GAP-097 于当日登记（信号管道未与因子资产库接通，P1 开放 0→1、合计开放 7→8——2026-08-12 06:15 期货 elite 因子库清理后管道因子池 104→1，见登记表）**。
> 总览开放口径更新（2026-08-13）：**GAP-098 于当日登记并关闭（L1 Meta-Loop 失败率熔断时序缺陷——验证循环首候选前以「本批总数 + 0 注入」误判 100% 失败率导致整批 0 注入，修复后按「已实际验证数」计算，P1 已关闭 41→42，开放维持 8）**；**E.3 S2 已落地（L4 运行状态库 DuckDB → SQLite WAL，state.duckdb 写锁被演化进程持有期间只读亦锁问题根治，见 07-operations）**。
> 总览更新（2026-08-13 v2.103.0+9，35-gap-closure-plan P0 批次）：**GAP-099/100/101 于当日登记并关闭（同向敞口惩罚 / 集中踩踏规避 / 换手预算+默认 λ 0.15，P1 关闭 42→45、合计 117→120，开放维持 8）**；G4/G11 阈值校准已执行（scripts/gap_threshold_calibration.py，|ICIR| P25≈0.22、≥0.30 通过率 70.9% → icir_min=0.30 数据支持；日换手库中 turnover 全为 0 无法校准 → G11 阈值待评估链回填后复核，见 plans/35 §9.1）。
> 总览更新（2026-08-13 v2.103.0+10，35-gap-closure-plan P1 批次）：**GAP-102/103/104/105 于当日登记并关闭（ICIR+符号反转硬门槛 / Bootstrap / ADF 平稳性 / 5-Regime 拆分检验，P1 关闭 45→49、合计 120→124，开放维持 8）**；新增测试 test_g4_screening_gates.py（6）+ test_robustness_g56.py（11）+ test_regime_split_validation.py（7）共 24 用例，335 定向回归全绿 + ruff 通过。

---

## 2. 差距登记表

### P0 — 阻塞性问题（影响核心功能）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-081 | `fts/factor_engine/expr_dsl/registry.py` `A_SHARE_FIELDS` + `seeds/stock/{northbound,margin_trade,holder_count,analyst_revision}.yaml` | A 股特有字段零数据源：`A_SHARE_FIELDS` 10 字段（northbound_flow/northbound_hold_pct/margin_balance/margin_net_buy/margin_short_balance/holder_count/analyst_up_count/analyst_down_count/analyst_total_count/analyst_eps_revision）仅注册 L0 恒等算子，fts/data_sources 与 FundamentalProvider 零接入（grep 命中 0）；69 个种子因子（4 族 YAML）已上线但面板缺列 `_ArrayDataWrapper.__getitem__` 抛 KeyError → 信号被跳过 | 北向/两融/股东户数/分析师预期四类 A 股特有因子全部空转，股票因子体系缺资金流与预期维度（与头部机构差距 P0） | 1 月内 | 🔴 开放 |
| GAP-082 | `fts/data_fundamental.py` `FundamentalProvider` + `fts/data.py` `FTSDataProvider` | 股票基本面数据缺口：26 字段定义中仅 8 个（roe/eps/bps/total_market_cap/revenue_growth/profit_growth/gross_margin/net_margin）在 CLI prepare 路径（mcp_available=True）有真实缓存（`data/fundamental_cache.json`，2026-08-03 快照，整列常量非时变）；默认 `FTSDataProvider()`（mcp_available=False）走 seed=42 随机合成——信号管道基本面全为随机值；pe_ttm/pb/ps_ttm/turnover_rate/roa/free_market_cap 连缓存都没有（真实路径也不注入）；11 个死字段（pcf_ttm/circulating_market_cap/volume_ratio/amplitude/debt_to_equity/current_ratio/asset_growth/gdp_growth/m2_growth/shibor_1y/lpr_1y）定义但永不注入；value/quality/size 因子代理实现未消费真实字段；Barra 4 风格（book_to_price/liquidity/earnings_yield/leverage）因缺字段恒 NaN 被跳过 | 基本面因子评估/信号基于随机值或代理，估值/质量/成长/市值维度无真实预测力；Barra 中性化 10 风格仅 6 个真实生效（与头部机构差距 P0） | 1 月内 | ✅ 已关闭（v2.103.0，详见 plans/31：新建 `fts/data_sources/stock_fundamental_source.py` `StockFundamentalSource`——估值/市值族日频（AKShare `stock_value_em`：pe_ttm/pb/ps_ttm/pcf_ttm/total_market_cap/free_market_cap/circulating_market_cap + 流通股本内部缓存）+ 财务/成长族季度（AKShare `stock_financial_analysis_indicator`：roe/roa/gross_margin/net_margin/debt_to_equity/current_ratio/eps/bps/revenue_growth/profit_growth/asset_growth，百分比 ÷100 与合成量纲一致、NaN 跳过不注入 0、缺列降级）；`stock_fundamental_cache` 表（migrate_schema 注册，与 ashare_special_cache 同构）；enrich 对齐注入（日频精确 / 季度报告期 ffill 防未来函数）+ 派生 turnover_rate/volume_ratio/amplitude；`FundamentalProvider` 新增 `stock_fundamental_enabled`/`stock_fundamental_source`（enrich_ohlcv 尾部注入块，失败不阻断）+ `get_fundamental_provider` 读 env `FTS_STOCK_FUNDAMENTAL_ENABLED`（默认关，回填后置 1）；回填脚本 `scripts/backfill_stock_fundamental.py`（--symbols/--families/--dry-run/--json/--trace-id，INSERT OR REPLACE 幂等）；测试 `test_stock_fundamental_source.py` 23 用例 + test_migrate tables_created 8→9，回归 171 passed + ruff 全绿。**遗留**：真实回填 + 端到端验证待执行；宏观死字段 gdp_growth/m2_growth/shibor_1y/lpr_1y 仍无源（GAP-087 关联）） |
| GAP-083 | `fts/data_sources/tdx_local_source.py` + `fts/data_futures.py` | 期货持仓/结算字段缺口（TDX_LOCAL 主路径）：`_process_daily` 对 hold/settle/pre_settle/oi_change 四列恒补 pd.NA（通达信 TQ 不返回）；kline_cache 直连回退路径用代理值（hold=volume 20 日均量、settle=典型价 (H+L+C)/3）；WIND/IFIND 字段增强层 `enhancers=[]` 未启用；get_ohlcv 不输出 amount | 持仓量/结算价类因子（期限结构/持仓变化/基差）在主路径数据缺失退化为代理或 NaN，期货因子体系缺资金与持仓维度（与头部机构差距 P0） | 1 月内 | ✅ 已关闭（v2.101.0，plans/27 四阶段落地：① 阶段 A——`_from_kline_cache` 读路径修复（真实优先/代理兜底）：SQL SELECT 增加 hold/settle 列，无效值（NULL/0 占位）才走代理，双格式对齐（symbol IN (RB,RB0)，同日期优先 RB0=TQ 15 年）；② 阶段 B——新脚本 `scripts/backfill_futures_hold.py`（AKShare futures_zh_daily_sina 真实 hold/settle 按日期 UPDATE 回填双格式，幂等/限速/dry-run/异常跳过）；③ 阶段 C——aggregator 字段增强层落地（数据源定版天勤 TQSDK：新建 `tqsdk_enhance_source.py` close_oi→hold/差分→oi_change 默认注册，`_enhance_fields` 改有效值覆盖防 NaN 污染；iFinD/Wind 保留可选 `futures_enhance_enabled`）；④ 阶段 D——`data_level_monitor.key_fields` 修正（GAP-085）；测试：test_data_futures_hold.py 15 用例（阶段 A 7 + 阶段 C 注册 4 + 有效值覆盖 4）+ test_tqsdk_enhance_source.py 14 用例 + test_backfill_futures_hold.py 13 用例 + test_data_level_monitor +2，291 定向回归全绿（tick_cache 2 既有失败除外）；**回填落地**：全量 82 品种 416,360 行 hold/settle 真实值（AKShare），全库 hold 覆盖 97.9%/settle 86.6%；**pre_settle 收尾（2026-08-11 追加）**：方案 A（iFinD SDK）因用户无 iFinDPy 权限不真实接入，改**零依赖派生** `pre_settle[t]=settle[t-1]`（回退 close[t-1]）——`aggregator._derive_pre_settle` 运行时派生（接入 get_ohlcv 两路径，内部升序派生还原行序兼容缓存倒序/源升序）+ `backfill_futures_hold.py --derive-presettle` 库内幂等回写（RB0 已回写 7,074 行），测试 +18 用例（aggregator 8 + backfill 10），勾稽校验通过；方案 A 框架（ifind_sdk_source.py）保留待权限；详情见 plans/27 §11-12） |
| GAP-001 | `pipeline/` + `strategies/` | pipeline 模块（`base.py`, `factor_combiner.py`）和 strategies 模块（`base_v2.py` 部分路径）无对应测试文件，覆盖率为 0% | 无法验证管线串联和因子组合逻辑的正确性，重构风险高 | 1 周内 | ✅ 已关闭 |
| GAP-002 | `cli.py`, `monitor.py`, `scheduler/` | CLI 入口、项目级监控封装、调度层均无测试覆盖（覆盖率均为 0%） | CLI/监控/调度在生产环境无可靠性保障 | 1 周内 | ✅ 已关闭 |
| GAP-017 | `scripts/futures_signal_pipeline.py` | 因子泛化无法验证：盲测品种池缺失、单品种 IC 追踪缺失、品种级权重分配缺失 | 因子在未见过的品种上有效性未知，Ridge 聚合权重无法区分每个品种的因子有效性 | 1 周内 | ✅ 已关闭 |
| GAP-033 | `fts/factor_engine/gp_evolver.py`, `operator_evolution.py`, `evaluation_chain.py` | GP 演化/算子演化使用全量数据搜索适应度导致数据泄露（OOS 不独立），IC 衰减字段硬编码未基于实际回测计算 | 高估 IC（0.5+ 虚假 IC），IC 衰减无实际监控，因子实际表现远低于回测 | 1 周内 | ✅ 已关闭 |
| GAP-046 | `fts/data_futures.py` + `fts/data_sources/migrate.py` + `fts/factor_engine/backtest_pipeline.py` + `cost_model.py` | 期货主力连续合约（`{symbol}0`）为 akshare 直接拼接，未做换月复权调整（换月跳空污染因子值/IC）；回测无展期成本仿真（持仓穿越换月日不扣展期价差）；`contract_kline` 具体合约表无建表/写入逻辑，无法构建真实换月日历 | 因子在换月日产生伪信号、IC 系统性偏差；回测高估收益（漏计展期成本）；无法真实模拟主力切换 | 本阶段（v2.58.0） | ✅ 已关闭（阶段 A v2.58.0 完成：换月复权 `roll_calendar.py` + 展期成本 `cost_model.py` + `contract_kline` 建表/写入；阶段 B/C 缺陷改进已并入 plans/21 并全部落地——GAP-F03 中性化 v2.59.0、GAP-F02 涨跌停/停牌 v2.59.0、GAP-F08 样本外强制 v2.60.0、GAP-F11 展期联动换月日历 v2.60.0；test_roll_calendar/test_cost_model 全绿） |
| GAP-047 | `fts/factor_engine/evaluation_chain.py` + 期货演化路径 | 期货截面因子无中性化主流程：行业/市值中性化仅存在于 `cross_section_evaluate_backtest` 可选参数（industry_map/cap_map 为 None 即跳过），期货演化路径未传板块映射 | 截面因子 IC 含板块/风格暴露污染，跨品种可比性失真（机构级缺陷，见 plans/21-futures-maturity-optimization-plan.md GAP-F03） | 本阶段 | ✅ 已关闭（v2.59.0：EvolutionLoop futures 自动注入板块映射 + 中性化生效） |
| GAP-048 | `fts/factor_engine/backtest_pipeline.py` | 回测无涨跌停拦截、停牌过滤、部分成交建模：Grep 涨跌停/停牌零命中，信号直接按收盘/结算成交 | 回测结果偏乐观（涨跌停日无法成交被当作可成交），违反回测-实盘强对齐红线（机构级缺陷，见 plans/21 GAP-F02） | 本阶段 | ✅ 已关闭（v2.59.0：涨跌停拦截 + 停牌过滤 + 被拦截成交统计） |
| GAP-049 | `fts/live_trade/`（缺失） | 实盘执行链路缺失：无真实网关、订单生命周期状态机、人工干预通道（紧急暂停/一键平仓）、实盘参数独立隔离、灰度发布 | 无法实盘落地，违反 AGENTS.md 4.3 实盘红线（机构级缺陷，见 plans/21 GAP-F01；角色边界：FTS 只产信号，真实网关由下游 FDT 负责） | 1 月内 | ✅ 已关闭（v2.60.0：live_trade 骨架 + OrderState 状态机 + 持仓级止损止盈单 + 人工干预接口 + 网关抽象/模拟 + 重试超时兜底） |
| GAP-074 | `fts/factor_engine/evolution_loop.py` + `operator_evolution.py` | 算子演化多样性退化：① UCT 统计仅在子因子评估通过时更新（`_update_uct_stats`），演化失败/运行时校验失败/预筛失败三条路径均不更新 → 父因子 visits 恒 0 → `_select_parent_uct` 永远返回 `parents[0]`（50 代全部演化 elite 池首因子，2026-08-11 csi300 实测全选 gtja_094）；② 算子演化随机种子仅由父因子 factor_id MD5 派生，同父因子不同代产出相同表达式（50 代重复产出 `ts_quantile(high,203,0.5845)`，横截面快速 IC=0.0146<0.02 全拒） | L2 夜间任务 50 代 0 晋升（2026-08-11 实测 elite_count=0），演化空转 | v2.100.0 | ✅ 已关闭（v2.100.0：① P0-1 新增 `_update_uct_failure` 并在演化失败/运行时校验失败/快速预筛失败三条 continue 路径接线（visits+1 无正奖励），父因子逐轮轮换消除选择坍缩；② P0-2 种子派生改为 `md5(factor_id::generation)`，同父不同代不同搜索轨迹、同父同代可复现；③ 新增 test_gap074_operator_diversity.py 4 用例全绿 + test_operator_evolution 13 + test_evolution_loop 266 passed（含 GAP-073 测试滞后修复：test_factor_audit_falls_back_when_walkforward_missing mock 窗口 1→2，对齐「单窗口标记 skipped」语义，9/9 全绿）+ test_audit 32 passed；④ 实际演化产出改善待夜间任务实测验证） |

### P1 — 重要改进（提升效率或稳定性）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-084 | `fts/data_mcp.py` `MCPDataProvider` + `seeds/stock/{wq101,qlib158,gtja191}.yaml` | 股票 OHLCV 虚构字段：文档声明的 `amount`/`high_limit`/`low_limit`/`pre_close`/`vwap` 任何数据路径均不返回（get_ohlcv 实际仅 5 列 open/high/low/close/volume + TQ 路径 change_pct）；wq101/gtja191 消费 amount、qlib158 消费 vwap → 面板缺列 KeyError → 对应因子信号全部跳过 | 350 个量价外部种子（wq101+gtja191+qlib158）中依赖 amount/vwap 的表达式实际空转，跨市场/股票量价因子覆盖率损失 | 1 月内 | ✅ 已关闭（v2.103.0：`fts/data_mcp.py` `_kline_to_df`（腾讯路径）与 `fts/data_sources/tdx_local_source.py` `fetch_stock_ohlcv`（TQ 路径）统一扩展 amount/vwap/pre_close 列——vwap 按 `amount>0 且 volume>0` 时 amount/volume、否则回退典型价 (H+L+C)/3（对齐 `_process_daily` 模式）；amount 腾讯无成交额字段补 0.0 如实降级、TQ 含真实值保留；pre_close=close.shift(1)（首行 NaN）；high_limit/low_limit 无真实涨跌停源不注入（因子 field_check 跳过，绝不伪造）；`MCPDataProvider.get_ohlcv` 真实路径返回含扩展列，消除种子因子 KeyError（实际消费：gtja191/qlib158 大量 vwap 表达式；wq101 无 amount 引用——登记描述"wq101 消费 amount"与代码不符，amount 列扩展为文档契约对齐）；合成路径 synthesize_ohlcv 保持 5 列不变（避免破坏既有断言）；测试 test_stock_ohlcv_columns.py 13 用例 + test_data.py `_kline_to_df` 断言 5→8 列同步，回归 244+93 passed + ruff 全绿；**遗留**：TQ amount 实测量纲为「万元」（600519 复权日 amount≈8.4e5 vs 真实成交额≈8.4e9），vwap=amount/volume 量纲异常（≈0.134）——与 `_process_daily` 期货主路径既有行为一致（数据源单位口径问题，另立专项）） |
| GAP-085 | `fts/monitor/data_level_monitor.py` `DataLevelConfig.key_fields` | 数据质量监控字段错位：key_fields 用 `open_interest`，期货日线 DataFrame 实际字段为 `hold`（open_interest 仅存在于 tick 层）→ `check_missing`/`check_outliers` 中 `if col not in df.columns: continue` 对 open_interest 直接跳过，持仓量缺失/异常监控实际未生效 | 持仓量数据质量（TDX 主路径恒 NA）无监控告警，数据缺失静默进入因子计算 | 1 月内 | ✅ 已关闭（v2.101.0：`key_fields` 修正为 `("close", "volume", "hold")`（+ docstring 同步）；新增 test_hold_missing_detected（hold 缺失 50% → missing_ratio_hold critical 告警）+ test_hold_not_in_df_no_alert（无 hold 列跳过不误报），test_data_level_monitor 22→24 passed 全绿） |
| GAP-086 | `fts/config/settings.py` `load_cap_map` + `fts/factor_engine/neutralization.py` | 市值中性化默认空转：`FTS_CAP_MAP_PATH` 默认路径为空字符串，data/ 下无任何 cap 映射文件，`load_cap_map()` 恒返回 `{}` → 股票信号管道/演化中性化的市值维度（size 中性化）为 no-op | 股票截面因子未剥离市值偏好，size 风格暴露污染 IC（GAP-S01 行业中性化已生效、市值维度缺失） | 1 月内 | ✅ 已关闭（v2.103.0：① 新建 `scripts/build_cap_map.py`——主源 `stock_fundamental_cache`（GAP-082 回填，field='total_market_cap' 每 symbol 取最新披露值）+ 降级源 AKShare `stock_zh_a_spot_em`（东财全市场实时快照含「总市值」列，一次请求全市场），导出 `data/cap_map.json`（{6 位裸代码: 最新总市值}，原子写 tmp+rename 幂等，CLI `--source auto/cache/live`/`--out`/`--dry-run`/`--json`/`--trace-id`）；② `settings.py` `cap_map_path` 默认动态化（`_default_cap_map_path`：env `FTS_CAP_MAP_PATH` 优先；未设置且 `data/cap_map.json` 存在时指向该文件，不存在保持空串现状降级）→ `load_cap_map()` 在文件存在时返回非空 dict，size 中性化生效（evolution_loop 自动注入 / daily_signal_pipeline `--neutralize size|both` / regime 面板构造三条消费链接线）；测试 test_build_cap_map.py 15 用例（缓存导出最新值/实时降级/auto 补齐/缓存权威/dry-run/空降级/load_cap_map 端到端/size_neutralize no-op→生效）+ test_config_settings +3 配置用例，回归 244+93 passed + ruff 全绿；真实冒烟：缓存导出 1 条（600519 最新总市值 1.68e12，GAP-082 回填仅 600519 一只），cap_map_path 自动指向 data/cap_map.json；**遗留**：全市场市值需运行 `--source live`（东财反爬偶发 RemoteDisconnected，重试即可）或 GAP-082 回填覆盖更多股票后重新导出） |
| GAP-087 | `fts/data_fundamental.py` `_fetch_macro` | 股票侧宏观字段硬编码：`_fetch_macro` 恒返回 `{"cpi": 0.0, "pmi": 50.0}`，pmi/cpi 从未接入真实宏观数据源（期货侧已有 iFinD edb 宏观链路不同步到股票侧）；Barra growth 依赖 revenue_growth/profit_growth（合成或快照）、宏观因子退化 | 股票宏观因子（fund_macro_pmi/cpi）基于常量无预测力，宏观维度形同虚设（与头部机构差距 P1） | 1 月内 | ✅ 已关闭（v2.103.0：`FundamentalProvider._fetch_macro` 接入真实宏观数据源——cpi 复用 `EastmoneyMacroSource.get_macro_series("中国CPI当月同比")` 最新值（东财 RPT_ECONOMY_CPI，GAP-088 已落地 edb_cache 缓存）；pmi 用 AKShare `macro_china_pmi`「制造业-指数」最新值（倒序首行）；其余字段（gdp_growth/m2_growth/shibor_1y/lpr_1y）无真实源 → 不注入不伪造；任一/全部拉取失败回退原常量 `{"cpi": 0.0, "pmi": 50.0}`（向后兼容不抛异常）；测试 test_data_fundamental TestFetchMacro 7 用例（真实 cpi/pmi/源失败跳过/全失败回退/不伪造死字段/单次拉取缓存），回归 244+93 passed + ruff 全绿；真实冒烟（2026-08-12）：cpi=0.5（2026-07 东财）、pmi=49.2（2026-07 akshare）——替代原硬编码常量；**遗留**：gdp_growth/m2_growth/shibor_1y/lpr_1y 仍无真实源（pmi/cpi 已闭环，其余字段接入需 iFinD/Wind EDB 或东财相应报表，登记远期）） |
| GAP-098 | `fts/factor_engine/meta_loop.py` `_check_circuit_breaker` + `_verify_and_inject` | L1 Meta-Loop 失败率熔断时序缺陷：`_verify_and_inject` 在验证循环第一个候选前（i=0）即以「本批总数 candidates_generated + 注入 0」计算失败率，`evaluated = total_candidates_generated + 20 ≥ 20` 时 `(20-0)/20 = 100% > 95%` 立即熔断，**20 个候选一个都未被实际验证**（`已处理=0/20`、injected=0、status=circuit_broken）；2026-08-12/13 连续两次期货 L1 meta-loop 实测复现；`total_candidates_generated` 在 `_run_bootstrap` 生成即累计（未验证候选计入失败率分母）叠加 `_verify_and_inject` 再传总数构成双重计数 | 单次生成 ≥20 候选时 L1 注入链路必然 100% 熔断空转，候选因子（研报/论文/LLM bootstrap 提取）全部丢弃，L2 演化无 L1 知识补给（GAP-031 注入合并断供） | 1 周内 | ✅ 已关闭（v2.103.0：① `_check_circuit_breaker` 新增 `batch_injected: int = 0` 参数，`candidates_generated` 语义改为「本次运行已实际验证的候选数」；② `_verify_and_inject` 循环内传已处理数 i + 本批已注入数 batch_injected（验证前 i=0 → evaluated<20 不触发），整批验证完成后追加一次最终失败率检查（已验证数=len(candidates)，覆盖「本批全失败」真实熔断场景）；③ `total_candidates_generated` 累计移至整批验证后（熔断路径按已处理数 i 累计），消除未验证候选入分母与双重计数；④ TDD 新增 3 用例（未验证批不误熔断 / 整批全失败熔断保留 / 20 有效候选集成回归 completed+20 注入），test_meta_loop.py **93 passed** 全绿 + ruff 通过；⑤ 全局 meta_loop/state 污染态（37 生成/0 注入/circuit_broken）经 StateKVStore 重置为初始态，下次运行可正常注入） |
| GAP-003 | `micro_evolution.py` | optuna 贝叶斯调参模块覆盖率仅 31%，依赖声明在 evolution extra 中，大部分分支路径（异常处理、参数传递）未覆盖 | 演化流程中的调参环节无充分测试，生产环境可能引发不可预见的 optuna 调用失败 | 1 月内 | ✅ 已关闭 |
| GAP-004 | `evaluation_chain.py` | 三级评估链覆盖率 90%，剩余 10% 的 mock 路径和异常分支未覆盖 | 边缘路径的评估逻辑可能存在隐含 bug | 1 月内 | ✅ 已关闭 |
| GAP-034 | `fts/factor_engine/factor_clustering.py` | 因子相关性缺乏系统聚类，ACTIVE_FACTOR_CAP 仅按 Sharpe 排序做简单截断，无法区分"高 Sharpe 高相关"和"低 Sharpe 独立信号"因子，冗余因子可能取代有价值的独立信号 | 组合因子多样性不足，独立信号可能被相关冗余因子挤出 | 1 月内 | ✅ 已关闭 |
| GAP-045 | `fts/factor_engine/portfolio_loop.py` + `adaptive_weight.py` + `portfolio_constructor.py` | adaptive 权重能力未完整接入 L3：`AdaptiveWeightManager`/`RegimeSmoother`/`PortfolioConstructor(weight_method="adaptive")` 仅测试引用，L3 生产路径仅用裸 `regime_adaptive_weight_adjustment`（回测/生产两套入口不同步）；Regime 切换权重无应用层平滑；原设计 A.3 的 FactorStyle/style_tags 维度未实现 | 回测与实盘路径不一致（违反强对齐红线）；Regime 切换时权重瞬时跳变；权重调整维度缺失风格维度 | 1 月内 | ✅ 已关闭（v2.99.0 确认：① `synthesis_mode="adaptive"` Step 2 委托统一入口（回测/生产同源）；② `RegimeSmoother(alpha=0.5, min_days=2)` 接入 Step 2.5 权重平滑（`adaptive_config` 走 `AdaptiveWeightConfig`）；③ FactorStyle 枚举 + style_tags 列（DuckDB 兼容补列）+ `REGIME_STYLE_MULTIPLIERS` 双维度调整（family×style，clamp [0.5×base,1.5×base]）已落地；test_portfolio_loop_adaptive/test_style_classifier 全绿） |
| GAP-050 | `fts/data_sources/wind_source.py` + `ifind_source.py` + `data_quality_monitor.py` + `capital_allocator.py` | 数据源生产可用性脆弱（WIND/IFIND MCP 默认抛异常、tick 历史仅 42 分钟）+ 数据质量监控错位（仅因子级非数据级）+ 组合优化层薄（无均值方差/风险平价，无保证金建模） | 生产环境增强字段缺失、数据缺失未被及时发现、组合风险调整能力弱（机构级缺陷，见 plans/21 GAP-F04/F06/F07/F09） | 1 月内 | ✅ 已关闭（① GAP-F04 v2.60.0：MCP 可配置注入 + 无 MCP 明确降级；tick 缓存回放 v2.84.0 GAP-I503：tick_cache 增量累积去重写入 + retention 保留 + get_ticks 时间窗口查询，跨会话可回放；② GAP-F06 v2.60.0 数据级质量监控（缺失率/异常值/复权一致性/多源分歧）；③ GAP-F07 v2.60.0 PortfolioOptimizer（均值方差/风险平价）；④ GAP-F09 v2.60.0 保证金建模；test_aggregator tick_cache/test_data_quality_monitor 全绿） |
| GAP-051 | `fts/factor_engine/evolution_loop.py` + `walk_forward.py` + `audit.py` | 样本外纪律执行不彻底：walk_forward 为审计可选环节（audit.py 中调用），调度主流程 `l2_evolution_loop_job`（jobs.py L54-119）未强制冷启动验证；`_run_audit` 的 OOS 结果用 L1 单段 ICIR 近似，非多窗口 WalkForward | 晋升因子可能依赖参数优化段过拟合（机构级缺陷，见 plans/21 GAP-F08） | 本阶段 | ✅ 已关闭（v2.60.0：晋升路径强制 WalkForward 冷启动验证 + 配置开关 + OOS 报告） |
| GAP-X01 | `fts/factor_engine/evolution_loop.py` | 横截面预筛 `_quick_prefilter` 使用单标的时序 IC（probe_data 取 panel 首个标的 + `forward_returns` 为截面平均序列，长度与单标的信号不齐时常被跳过），无法反映因子截面区分能力 | 截面候选在预筛阶段漏判/误判，低质量因子进入细评估浪费资源；高质量截面因子可能被时序口径误拦 | 1 月内 | ✅ 已关闭（`_cross_section_prefilter` 全面板信号矩阵 vs 截面 forward 收益，与 `cross_section_evaluate_backtest` 同口径——复用 `_cs_execute_factors`/`_cs_build_matrices`/`_cs_compute_ics`，`_quick_prefilter` 横截面模式分派；test_evolution_loop test_cross_section_prefilter* 全绿） |
| GAP-X02 | `fts/factor_engine/evolution_loop.py` | operator 因子生成（`_generate_operator_factor` fallback）不校验表达式输出是否常数，非常数信号要等到运行时校验/预筛阶段才被拦截 | 生成→运行时→预筛整链白跑，常数表达式占用演化预算；且 eval_fts_expr NameError 未修前 operator 因子全数降零被拦 | 1 月内 | ✅ 已关闭（`_generate_operator_factor` 生成循环内 `evaluate(node, probe_data, registry)` 评估表达式，非常数信号（finite 为空或 nanstd<1e-8）前置拦截跳过，不再进入下游；test_evolution_loop test_generate_operator_factor_constant_precheck_rejected 全绿） |
| GAP-X03 | `fts/factor_engine/backtest_pipeline.py` + `gp_evolver.py` | `_execute_factor_code` 的 exec 未将模块级 import 绑定合并回 globals，`factor_program.__globals__` 解析不到 `eval_fts_expr` → NameError → 降级返回全零 → operator 因子全数被判「常数信号」拦截；另 GP 模板 `ts_product` 用 `Rolling.prod`（pandas≥2.1 移除）+ GP 适应度未对齐流水线 clip 后处理 | GP/operator CPU 演化通道空转（0% 通过率），吞吐与产出质量双降 | 1 月内 | ✅ 已关闭（`_execute_factor_code` `exec_globals.update(local_vars)` 将模块级 import 绑定合并回 globals（与 FactorExecutor.compile 同模式）；`ts_product` 改用 `apply(np.prod)` 适配 pandas≥2.1（feature_ops/gp_evolver 双处对齐）；GP 适应度对齐流水线 clip 后处理；test_gp_evolver test_ts_product_template_works_on_pandas_2 + operator 演化集成全绿） |
| GAP-060 | `fts/factor_engine/horizon_analysis.py` + `evaluation_chain.py` | 对照《期货因子质检六层框架》Layer 4 衰减分析：无多持有期 IC（1/5/10/20 日）、无 IC 衰减曲线、无最佳持有期选择 | 因子有效期未知，调仓频率无法由数据驱动设计；预测力随持有期变化不可见 | 本阶段（v2.90.0） | ✅ 已关闭（v2.90.0：`horizon_analysis.py` 多持有期 IC 体系——时序路径 `evaluate_backtest` + 横截面路径 `cross_section_evaluate_backtest`（`compute_cs_multi_horizon_ic`，股票/ETF 主路径）均接入，输出 `multi_horizon`（ic_by_horizon/icir_by_horizon/win_rate_by_horizon/best_horizon/decay_curve/monotonic_decay），`FTSConfig.eval_horizons` 默认启用 `1,5,10,20`；测试 test_horizon_analysis.py 11 用例 + test_cross_section_horizon.py 8 用例） |
| GAP-061 | `fts/factor_engine/cost_sensitivity.py`（规划） | 可交易性压力层缺失：成本敏感性扫描（滑点/手续费 1/2/4/8 倍）+ 盈亏平衡倍数未实现 | 因子对交易成本的耐受度不可见，落地性评估缺成本维度 | 25 号计划阶段 B（v2.91.0） | ✅ 已关闭（v2.97.0：`cost_sensitivity.py` `run_slippage_stress`——滑点倍数 1/2/4/8 扫描，复用 `TransactionCostModel`，输出净夏普/盈亏平衡倍数/positive_at_max_stress；`evaluate_backtest` 接线 `cost_sensitivity`，`FTSConfig.cost_sensitivity_enabled` 默认关；测试 test_cost_sensitivity.py 12 用例） |
| GAP-062 | `fts/factor_engine/evaluation_chain.py` | 评估链统计补全缺口：IC t 值 / 日度 IC 胜率 / 最大连续亏损 / Q1-Q5 完整分组 / 信号翻转频率 / 因子截面分散度（对照六层框架 Layer 2/3） | 单因子统计诊断维度不足，伪信号识别缺经济意义证据链 | 本阶段（v2.90.0） | ✅ 已关闭（v2.90.0：时序路径 `_block_ic_stats`/`_max_consecutive_losses`/`sign_flip_rate` + 横截面路径 `ic_t_stat`/`win_rate`/`cs_dispersion`/`quintile_returns`（含 q5_q1_spread + monotonic 单调判定）/`sign_flip_rate`/`max_consecutive_losses` 补全） |
| GAP-063 | `fts/factor_engine/portfolio_loop.py`（规划） | 组合质检三标准缺失：合成增益（组合 ICIR/最佳单因子 ICIR）/ 分散化增益（组合夏普/权重加权因子夏普）/ 回撤控制（组合回撤/子策略回撤中位数） | 组合层无增量价值判定，因子合成增益不可验证 | 25 号计划阶段 D（v2.93.0） | ✅ 已关闭（v2.97.0：`build_combo` 组合质检 `qc_standards`——synthesis_gain（组合夏普/最佳单因子夏普）+ diversification_gain（组合夏普/权重加权夏普）+ drawdown_control_ratio（组合回撤/成分因子平均回撤，净值从 1 起 cumprod 实测）+ 各 passed 判定；`PortfolioCombo` 契约新增 qc_standards；测试 test_portfolio_qc.py 6 用例） |
| GAP-064 | `fts/factor_engine/weight_learning.py`（规划） | IC 协方差加权合成缺失：w=Σ⁻¹μ 模式（μ=IC 均值向量、Σ=Ledoit-Wolf 收缩协方差、正则化求逆、样本 <20 回退 IC 均值加权） | 最优风险调整合成缺失，组合权重仅等权/Sharpe/ElasticNet | 25 号计划阶段 E（v2.94.0） | ✅ 已关闭（v2.97.0：`weight_learning.ic_covariance_weights`——Ledoit-Wolf 收缩 + 对角正则 + w/Σ\|w\| 归一化，NaN 行剔除、奇异/样本<20 返回 None；`synthesize_signals` 新增 ic_matrix 参数 + ic_weight 模式（失败回退 IC 均值加权 w∝\|ic\|）；测试 test_ic_weight.py 10 用例） |
| GAP-065 | `fts/factor_engine/sector_linkage.py`（规划） | 品种间板块联动检测缺失：板块内相关矩阵 + 联动强度 + 因子跨联动分散度 | 板块内部高度联动品种被重复计入组合，分散度高估 | 25 号计划阶段 F（v2.95.0） | ✅ 已关闭（v2.97.0：`sector_linkage.py` `compute_sector_linkage`——板块内两两相关均值/最大 + 跨板块相关 + 因子截面分散度 + high_linkage 标记 + `factor_dispersion_by_sector`；测试 test_sector_linkage.py 8 用例） |
| GAP-066 | 数据层 `get_ohlcv`（规划） | 夜盘/隔夜跳空标记缺失：overnight_gap 列注入（open/prev_close-1） | 隔夜跳空信号不可观测，因子输入缺跳空维度 | 25 号计划阶段 G（v2.96.0） | ✅ 已关闭（v2.97.0：`data_sources/overnight_gap.py` `compute_overnight_gap`（open[t]/close[t-1]-1）+ `inject_overnight_gap`（flag 阈值默认 0.01）；`data_futures.get_ohlcv` 复权后注入跳空列，`FTSConfig.inject_overnight_gap_enabled`/`overnight_gap_flag_threshold` 默认关；测试 test_overnight_gap.py 8 用例） |
| GAP-067 | `fts/factor_engine/portfolio_loop.py`（规划） | 组合级回撤止损 + 相关性熔断缺失（风控输出） | 组合极端行情无风控输出，回撤止损靠单因子层 | 25 号计划阶段 H（v2.97.0） | ✅ 已关闭（v2.97.0：`portfolio_risk_controls.py` `PortfolioRiskAlert` + `check_drawdown_stop`（净值从 1 起 cumprod 回撤）+ `check_correlation_circuit_breaker`（窗口 60 均值相关）+ `run_portfolio_risk_controls`（默认回撤 10%/相关 0.8）；`portfolio_loop.run()` Step 7.8 接线 `state["risk_alerts"]`，异常降级不中断；测试 test_portfolio_risk_controls.py 10 用例） |
| GAP-070 | `fts/factor_engine/evolution_loop.py` `_promote_to_elite` | 兜底家族 'other'/'unknown' 被套用家族多样性上限（max_per_family 缺省 15）：'other' 是"无法归类"回收站家族，演化新因子（LLM/GP 生成未映射家族标签）大量落入其中，对其设限等价于对整个演化新因子晋升通道设总量上限，压制演化空间 | 合格候选因子因"其他家族达 15 上限"被拒（plans/21 已记录 generation 40/48），L2 演化空转率高 | v2.98.0 | ✅ 已关闭（v2.98.0：`_promote_to_elite` 家族多样性检查对 `'other'`/`'unknown'` 永久跳过上限拦截；逻辑同质化保护由 L2 准入去冗余（GAP-I206 相关性预检 + 正交化闭环 + Gram-Schmidt 基底）承担；新增 2 测试用例 other/unknown 达上限仍晋升，trend 家族拦截不变） |
| GAP-073 | `fts/factor_engine/audit.py` `_check_oos_consistency` + `fts/cli.py` 期货演化路径 | 短样本下 WalkForward 仅完成 1 窗口（ic_consistency 退化为单窗口 IC 正负的 0/1），审计 oos_consistency 恒失败 → 全部演化候选被拦截（通过率 17%），失败率熔断 | 500 行日频数据下 L2 演化 0 晋升、两次熔断（generations=16/19, elite_count=0/1），演化空转 | v2.98.0 | ✅ 已关闭（v2.98.0：① `_check_oos_consistency` 对走航结果 `n_windows_completed < 2` 标记 skipped（与数据缺失项对齐），L1 兜底无窗口键保持原逻辑；② 期货横截面演化 `days=500→700` 使 WalkForward 完整产出 4 窗口（探针验证 n_windows_completed=4；勿超 750 行否则落入 3 年默认分支产出 0 窗口）；③ 新增 4 审计测试用例，test_audit.py 28→32 全绿） |
| GAP-072 | ts/factor_engine/portfolio_loop.py + scripts/*_signal_pipeline.py + ts/scheduler/tasks.py | L3 组合与信号管道强耦合：信号管道每日由 L3 联动触发、Ridge 权重每日重学，权重随滚动窗口/Regime 每日变动产生噪声调仓；L3 组合权重每日 Elastic Net 重估 | 权重噪声驱动无效换手、交易成本非线性增长、回测与实盘节奏易错位 | v2.99.0 | ✅ 已关闭（v2.99.0：解绑 L3 与信号管道——L3 期货/股票改每周五运行；信号管道独立每日任务；FTSConfig.l3_weight_recompute_cadence（weekly/daily，默认 weekly）+ l3_weight_recompute_weekday（默认 4=周五）+ is_weight_recompute_day()；PortfolioLoop.run(recompute_weights=None) 冻结日返回 status="frozen" 不重建组合（含冷启动保护）；futures/daily 信号管道周五重算 Ridge 权重存快照 memory/portfolio/{futures,stock}_signal_weights.json，其余日冻结复用仅刷新因子值；CLI --force-recompute 强制重算） |
| GAP-068 | `fts/factor_engine/multi_frequency.py`（新增） | 多频信号叠加与冲突解决缺失：信号合成为单一日频口径（`synthesize_signals`），无日内分钟信号叠加与方向冲突消解机制；原延期（依赖分钟级数据完备性） | 日频信号忽略日内信息（动量/强弱），信号质量单一维度；日内与日频方向冲突时无消解规则 | v2.101.0 | ✅ 已关闭（v2.101.0 阶段 J：`multi_frequency.py`——分钟信号生成 `build_minute_signal`（日内动量/vwap 偏离）→ 日频聚合 `aggregate_minute`（last/mean/max/min）→ 加权叠加 `blend_signals` + 冲突消解 `resolve_conflict`（weighted/penalty/discard）→ 分钟回测 `backtest_minute_signal`（T+1 持有 + 成本）；复用 `get_minute_ohlcv` 4 级降级链；数据不足降级不阻断日频路径；设计见 plans/25 §10） |
| GAP-069 | `fts/factor_engine/position_rank_crowding.py`（新增） | 会员持仓排名拥挤度缺失：数据层未接入交易所持仓排名（前 20 会员），无法构建持仓集中度/拥挤度因子；原延期（依赖外部数据源） | 持仓拥挤度不可观测，反转风险盲区 | v2.101.0 | ✅ 已关闭（v2.101.0 阶段 I：`position_rank_crowding.py`——`PositionRankProvider` 接口抽象 + `AKSharePositionRankProvider` 四交易所实现（dce/shfe/czce/cffex）+ `compute_crowding` 集中度指标（cr_top_n/long_short_ratio/net_holding_ratio）+ `crowding_score` 综合拥挤度 + `position_rank_crowding_signal` 拥挤度信号；数据不可用优雅降级（异常/空/行数不足 → 跳过）；设计见 plans/25 §9） |
| GAP-075 | `fts/factor_engine/evaluation_chain.py` + `audit.py` + `evolution_loop.py` + `symbol_holdout.py`（新增） | 跨标的稳健性检查缺失（同市场泛化盲区）：① FactorAuditor 第 3 项 cross_symbol（≥80% 标的 IC 为正）因 `_run_factor_audit` 未传 `symbol_ic_map` 恒为 skipped——股票因子（csi300 全量 300 只学习 + 同池选股）只证明「时间维度 OOS 有效」，未证明「在多数股票上普适」（可能只押中少数标的或记忆池子历史模式）；② 无标的留出验证——同市场学习/选股同池，对池内未参与训练标的的预测力无量化检验（无行业分层留出 + 留出集 IC 保持率指标） | 同市场泛化预测力无保障：因子可能仅对少数标的有效；成分股快照非 PIT，未来新纳入成分的预测力未知 | v2.101.0 收尾 | ✅ 已关闭（v2.101.0 收尾，方案 1+2 落地）：① 横截面评估 `cross_section_evaluate_backtest` 输出 `symbol_ic`（逐标的时序 IC，方向翻转同步取反）+ `symbol_holdout`（新增 `holdout_ratio=0.2`）；② 新建 `symbol_holdout.py`——`run_symbol_holdout` 按行业分层留出 20% 验证集（seed 固定、行业缺失回退随机），训练集（80%）定方向、留出集验截面 IC 与保持率（ic_retention），留出集过小返回 None；③ `FactorAuditor` 审计 6→7 项——`_run_factor_audit` 传 `symbol_ic_map` 激活原恒 skipped 的 cross_symbol + 新增 `symbol_holdout` 审计项（缺失 skipped）；④ 契约 `BacktestMetrics` 补 `symbol_ic`/`symbol_holdout`；⑤ test_symbol_holdout.py 15 用例 + 更新 test_audit/test_factor_lifecycle 审计项数量断言（6→7），回归 131+41 passed 全绿 |
| GAP-077 | `fts/factor_engine/evolution_loop.py` `_promote_to_elite` + `fts/config/settings.py` | `max_per_family` 家族配额（GAP-F10）基于来源/主题标签（family 非正交结构维度）控制精英多样性：同家族因子可高相关、跨家族也可高相关，配额与正交化闭环（GAP-I206）重复且误伤高价值来源；GAP-070 对 other/unknown 豁免留下膨胀漏洞 | 标签级配额不反映真实结构冗余，可能压制高价值来源或放任结构同质化膨胀 | v2.102.0 | ✅ 已关闭（v2.102.0：结构性聚类配额替代 max_per_family——`_count_cluster_members` 统计与既有 elite \|corr\| ≥ corr_threshold(0.85) 的同类成员数，≥ max_per_cluster(15) 拒绝晋升；复用 `_scan_elite_correlations`（从 `_check_elite_correlation` 提取，GAP-I206 行为不变）；other/unknown 豁免自然消除；`structure_cluster_quota_enabled` 开关关闭回退 max_per_family 旧逻辑；新增 test_structure_cluster_quota.py 11 用例；设计见 plans/26 §9.1） |
| GAP-079 | `fts/factor_engine/evolution_loop.py` `_run_factor_audit` | oos_consistency 全量误杀（GAP-073 漏网路径）：1073 条 audit_fail 中 99.4%（1067 条）由 oos_consistency failed 导致，其中 89.8%（958 条）WalkForward 实际完成窗口数 <2（0 窗口）——"无法验证"却被判 failed。根因：`_run_factor_audit` 在评估链走航窗口不足且独立走航失败（数据不足/force_walkforward=false）时回退 L1 icir 兜底（无 n_windows_completed 键），未命中 GAP-073 的 n_windows<2 → skipped 分支 | 短样本/数据不足场景下演化候选被 oos 全量误杀（08-11 run 评估 10 晋升 0 失败率 100% 熔断），晋升通道阻塞 | v2.102.0 | ✅ 已关闭（v2.102.0：`_run_factor_audit` oos_result 构造新增 elif 分支——评估链走航存在（dict）且 n_windows_completed<2 且独立走航失败时，构造带 n_windows_completed=0 的 oos_result（对齐 GAP-073 skipped 语义）；仅当 walk_forward 完全缺失时走 L1 icir 兜底（原逻辑不变）。修复效果模拟：958/1067（89.8%）转 skipped，109 条真实拦截保留；新增 test_gap079_oos_skip.py 6 用例 + test_audit/test_evolution_loop 定向回归 81 passed 全绿；排查见 plans/26-phase0-audit-breakdown.md） |
| GAP-096 | `fts/factor_engine/audit.py` `_check_cross_symbol` | 跨品种符号门槛在短样本下过严且统计不合理：cross_symbol 仅看「≥80% 品种 IC 为正」绝对值符号比例，① 700 行日频短样本下单品种 IC 噪声大、符号易翻转，IC 符号非稳定信号；② 22 品种下要求 18/22 为正，5 个品种负即拒（23% 容忍度），大量因子聚集 14-17/22（64%-77%）被边界误杀；③ 符号与 IC 幅度/显著性脱钩，无法区分「泛化强但个别品种失效」vs「勉强过线」；④ 与 symbol_holdout/oos_consistency 门槛叠加紧缩 | 修复 GAP-079 后 oos 不再是瓶颈，cross_symbol 成为期货 700 行短样本主拦截源（08-12 run 种子 11 个零晋升、失败率 100% 熔断，失败项 cross_symbol 8 次居首） | v2.103.0 | 🔴 修复中（v2.103.0：A+C 双机制 OR 判定——保留符号比例≥80% 主防线；新增平均 IC 强度软门控（min_mean_ic≥阈值且符号比例≥ratio_floor）；新增二项检验显著性（IC>0 品种数显著高于随机，binomtest p<α）；任一满足即通过） |
| GAP-097 | `scripts/futures_signal_pipeline.py` `load_futures_elite_factors` + `fts/factor_engine/portfolio_loop.py`（权重学习）+ `memory/knowledge/factors/futures_elite/` | 因子读取路径仅读 JSON 快照目录（`futures_elite/*.json`），未与因子资产库（`data/factor_catalog_futures.duckdb`，plans/29 SSOT）接通：2026-08-12 06:15 因子库收敛清理（103 个 elite JSON 归档移除、仅存 `fct_0b1a8f45.json`），当日管道因子池 104→1（仅 `fut_intraday_momentum`），信号由单因子驱动、方向大面积翻多（多头 11→45），跨日信号口径崩塌；portfolio_loop 侧 `_compute_elastic_net_weights`/`_compute_ml_ensemble_weights` 同走 JSON 目录导致聚类后因子匹配不到代码 → elastic_net 回退 sharpe_weight | 因子库清理/存储收敛后管道信号因子池骤降，跨日信号不可比；JSON 目录与 DuckDB 因子资产库双源漂移风险（DB 库存 328 期货因子、active elite 172，管道实际只用 1 个）；L3 权重学习脱节（v2.103.0 实测 5 聚类因子仅 1 个命中 JSON） | 1 周内 | ✅ 已关闭（v2.103.0：管道新增 DuckDB 因子资产库读取路径——`load_futures_elite_factors_from_db()` 经 `FactorRepository(market="futures")` 加载 `status='active' AND is_elite=TRUE` 因子，复用 `metadata.evaluation`（与 JSON 快照同构，含 level_1_backtest.ic/sharpe/t_stat）构造兼容 dict + 与 JSON 路径同款两层去重；main 改为 DB 优先、JSON 目录降级回退；JSON 读取函数原样保留（回退/测试兼容）。v2.103.0+5：portfolio_loop 新增 `_build_factor_code_map`（内存 code 优先 → DuckDB 补拉 → JSON 快照兜底），elastic_net/ml_ensemble 权重学习因子代码加载对齐 SSOT，实测 5 因子全部命中、Elastic Net 500 截面回归日正常执行不再回退） |
| GAP-099 | `fts/factor_engine/portfolio_risk_controls.py` `check_aligned_exposure` + `fts/factor_engine/portfolio_loop.py` `build_combo` | 同向敞口惩罚缺失（35-gap-closure-plan G1，SOP 阶段 7「局部最优共振踩踏」根因项）：多个同向因子共振时仓位线性叠加，无中心化压缩 | 多因子同向共振重仓，行情拐点集中回撤（核心命题直接威胁项） | v2.103.0+9 | ✅ 已关闭（v2.103.0+9：`AlignedExposureConfig` + `check_aligned_exposure`——以因子 IC 符号代理方向、按 \|weight\| 加权计算同向占比，≥0.6 触发压缩 scale∈[0.5,1]（linear/sqrt/exp 三曲线）；`build_combo` 新增 `aligned_exposure_config` 参数，归一化后与 exposure_scale 乘性合并；主流程默认开启（单测直接调用默认关闭向后兼容）；test_portfolio_risk_controls.py +15 用例） |
| GAP-100 | `fts/factor_engine/portfolio_risk_controls.py` `throttle_exit_stampede` | 集中踩踏止损规避缺失（35-gap-closure-plan G2）：同一时点批量止损无节流，行情拐点集体平仓冲击成本无护栏 | 拐点集体平仓冲击成本放大、滑点恶化 | v2.103.0+9 | ✅ 已关闭（v2.103.0+9：`ExitStampedeConfig` + `throttle_exit_stampede`——单日平仓数 ≤ max_same_day_exits(3)，超限按风险敞口降序顺延 ≥batch_gap_days 日分批执行；仅重排执行顺序不取消止损触发（纪律优先）；计划日耗尽回填原触发日不丢弃；test_portfolio_risk_controls.py +7 用例） |
| GAP-101 | `fts/factor_engine/portfolio_turnover.py`（新）`allocate_turnover_budget` + `fts/config/settings.py` `l3_turnover_penalty` | 换手率全局上限约束缺失（35-gap-closure-plan G3）：`apply_turnover_penalty` 默认 λ=0 关闭；无「剔除边际收益最低弱信号」的换手预算机制 | 摩擦成本随无效换手非线性增长，弱信号噪声调仓 | v2.103.0+9 | ✅ 已关闭（v2.103.0+9：新建 `portfolio_turnover.py`——`TurnoverBudgetConfig` + `allocate_turnover_budget`（单日换手上限 0.30，超限按边际收益从最弱开始回退当前持仓并重归一化，浮点容差 eps）；`build_combo` 新增 `turnover_budget_config` 参数归一化后接入；`l3_turnover_penalty` 默认 0.0→0.15 开启（D5）；test_turnover_budget.py 12 用例 + test_turnover_penalty 默认断言同步） |
| GAP-102 | `fts/factor_engine/evaluation_chain.py`（ICIR 硬门槛 + 符号反转）+ `fts/factor_engine/walk_forward.py`（跨窗口 ICIR 门槛） | ICIR 无硬门槛 / WFA 无 ICIR 门槛 / 前后半段无符号反转硬检查（35-gap-closure-plan G4）：ICIR 合格线 0.5 仅在 high_ic_screener 打分体系；时序路径 icir 为 in/out 两段近似；`decay_6m` 仅衰减率无符号反转判定 | 高 IC 波动大的伪因子与前后半段符号反转的不稳定因子通过准入，过拟合局部最优混入因子库 | v2.103.0+10 | ✅ 已关闭（v2.103.0+10：① `_block_ic_stats` 扩展返回 (ic_t, win_rate, icir_block)（块级 ICIR 真口径），时序 metrics 注入 `icir_block`；② `evaluate` 失败判定新增 `\|ICIR\|<0.30` 硬门槛 + `sign_flip_half_split` 前后半段符号反转一票否决（时序路径 `icir_block`、横截面路径 L921 日度 IC 序列 ICIR）；③ `walk_forward` 新增 `min_oos_icir=0.25`（跨窗口 ICIR=mean/std，零波动恒定 IC 视为充分稳定 999.0）；`WalkForwardConfig/Result` 契约扩展；test_g4_screening_gates.py 6 用例 + test_qc_stats_completion 三元组同步，141 定向回归全绿） |
| GAP-103 | `fts/factor_engine/robustness.py` `bootstrap_ic_ci` | Bootstrap 自助抽样检验缺失（35-gap-closure-plan G4.2）：无统计 Bootstrap，因子 IC 无置信区间验证 | 随机剔除样本后绩效崩塌的因子无法被统计识别，显著性高估 | v2.103.0+10 | ✅ 已关闭（v2.103.0+10：`bootstrap_ic_ci`——时间块抽样（block bootstrap 块长 20）保留自相关结构（禁止 iid 重抽样防显著性高估），固定 seed=42 可复现，IC 95%CI 下界 ≥0 判通过；样本 <2 块或有效重抽样 <30 返回不通过；test_robustness_g56.py 5 用例） |
| GAP-104 | `fts/factor_engine/robustness.py` `check_stationarity` | ADF/分布平稳性检验缺失（35-gap-closure-plan G4.3）：全库 adfuller 零命中，ACF 仅作 Regime 特征 | 非平稳因子收益序列的伪相关未被识别，趋势漂移污染 IC | v2.103.0+10 | ✅ 已关闭（v2.103.0+10：`check_stationarity`——ADF 单位根（statsmodels 可用时，p<0.05 平稳）+ 滚动矩漂移比双通道（无 statsmodels 自动降级，前/后半段均值差/全段 std <0.2）；★ 检验对象为因子收益序列非因子值（docstring 写死，防误杀趋势因子）；test_robustness_g56.py 6 用例） |
| GAP-105 | `fts/factor_engine/regime_validation.py` `validate_factor_across_regimes` | 显式 5-Regime 拆分检验缺失（35-gap-closure-plan G4.4）：`high_ic_screener._check_multi_regime` 仅用 WF 窗口 IC 正占比近似，未按 bull/bear/oscillate/high_vol/low_vol 显式拆分 | 环境依赖因子（仅在特定制度有效）未被识别，跨制度失效风险无预警 | v2.103.0+10 | ✅ 已关闭（v2.103.0+10：`regime_validation.validate_factor_across_regimes`——按 5 制度拆分计算 IC + 制度内块状 ICIR（块长 20），覆盖 ≥3 制度且正向制度数 ≥3 判通过；制度 ICIR<-0.5 打 `regime_dependent` 环境依赖标记（不否决，入库标记）；与既有 `validate_regime_predictive_power` 互补；high_ic_screener 保持 WF 近似（输入域无 regime 序列，接线留调用方）；test_regime_split_validation.py 7 用例） |

### P2 — 一般改进（优化代码质量）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-088 | `fts/data_sources/macro_aligner.py` `MACRO_FIELD_QUERIES` + `seed_data_futures_full.py` | 期货宏观注入链路不完整：`import_data`（进口金额）有注入映射但无种子因子消费；宏观 5 列（export/import_data/cpi/rate/us_bond）仅回测管线注入（`macro_field_injection` 默认开启），信号管道/横截面演化路径不注入；`fut_macro_cpi/interest_rate/export/us_bond` 字段缺失时降级为 close 趋势代理 | 宏观因子在非回测路径退化为价格代理，宏观维度覆盖不完整（与头部机构差距 P1） | 3 月内 | ✅ 已关闭（v2.103.0 注入端闭环；**消费端已闭环（2026-08-11）**：新增 `fut_macro_import` 种子因子对称消费 import_data——Python 硬编码 + `seeds/futures/market_regime.yaml` 双路径，YAML 为主路径，期货种子 184→185；**数据源真实闭环（2026-08-12）**：新建 `fts/data_sources/macro_eastmoney_source.py` `EastmoneyMacroSource`——东财 RPT_ECONOMY_CPI/CUSTOMS（cpi/export/import_data）+ akshare 中债登 1 年期/美债 10 年期（rate/us_bond），edb_cache 缓存与 IFindSource 同构互操作，`MacroFieldAligner` 默认源切换（替代 iFinD EDB——MCP 需 API Key 实测不可用），真实冒烟 RB0 500 行宏观 5 列全部真实注入，新增 test_macro_eastmoney_source.py 9 用例；**注入端闭环（2026-08-12）**：新建 `inject_macro_fields_to_panel` 面板级宏观注入 helper——跨标的共享序列只拉取一次（源 edb_cache 二次命中避免重复网络请求）+ `MacroFieldAligner.align` 逐标的注入 5 列 + lag_days 发布滞后防未来函数 + 字段/单标的失败降级不阻断（因子走 close 代理）；接线期货信号管道 `scripts/futures_signal_pipeline.py`（面板构建后注入，`--no-macro-injection` 可关、默认开，`_inject_macro_to_panel` 失败降级）与横截面演化 `fts/cli.py` `_prepare_futures_data`（try/except 降级不阻断）；测试新增 test_macro_panel_injection.py 8 用例 + test_futures_signal_pipeline_macro.py 3 用例，回归 test_macro_aligner/eastmoney 19 + test_cli_extra 108 + test_cli 81 passed + ruff 全绿；真实冒烟 RB0/CU0 60 行 5 列全部真实注入） |
| GAP-089 | `fts/data_sources/tqsdk_tick_source.py` + `fts/data_sources/migrate.py` | 高频数据深度不足：tick_cache 32 列仅 5 档盘口且 TQSDK 实测仅 ~42 分钟样本（跨会话增量累积中）；无逐笔委托/成交（order/execution）数据、无 10 档深度、无订单簿重建；分钟级 11 列无持仓/结算 | 微观结构因子（OFI/OBI/大单占比已建，C1）受数据深度限制，高频因子体系无法对标头部机构（订单不平衡/撤单率/暗池） | 3 月内 | 🟡 开放（**受限登记（2026-08-12）**：数据深度受外部数据源制约——TQSDK 行情深度与历史样本由天勤/交易所数据权限决定，逐笔/10 档/订单簿重建非本机可实现；跨会话 tick_cache 增量累积（GAP-I503）已落地作为样本积累通道，剩余深度扩展待数据源条件成熟，**登记受限不假完成**） |
| GAP-080 | `fts/factor_engine/shap_analyzer.py` `ShapAnalyzer` + `evolution_loop.py` `_run_shap_analysis` | SHAP 批量计算瓶颈：真实演化验证（2026-08-11）发现 GAP-079 修复后通过门禁的种子/候选剧增，SHAP 分析（`ShapAnalyzer.analyze`，KernelExplainer 每因子 ~10-20s）在种子评估阶段串行批量执行 20 分钟未跑完，成为晋升链新瓶颈。根因：每因子评估量 ≈ n_extreme(50)×2 × nsamples(100) = 1 万次单行因子执行 | 演化 run 时长不可控（种子评估阶段 SHAP 成为主耗时），晋升率验证与夜间任务被阻塞 | v2.102.0 | ✅ 已关闭（v2.102.0：三项采样参数降频——`ShapAnalyzer` 默认 n_extreme 50→25、n_background 100→50、新增 nsamples 参数默认 50（原硬编码 100），每因子评估量 1 万→2500（~4x 下降）；`FTSConfig` 新增 `shap_n_extreme`/`shap_n_background`/`shap_nsamples`（env `FTS_SHAP_*` 可覆盖），`EvolutionLoop` 用配置值构造 `ShapAnalyzer`；SHAP 为信息型审查（成功即通过），采样参数缩小不改变门禁语义；summary 新增 `n_nsamples` 可观测；新增 test_shap_optimization.py 7 用例 + test_shap_analyzer 默认断言同步，44 passed 全绿；ruff 通过） |
| GAP-005 | `fts/monitor.py` | `format_status_report()` 方法缺少对人类可读输出的测试 | 监控报告格式变更后无法自动回归验证 | 3 月内 | ✅ 已关闭 |
| GAP-006 | `core/enums.py` | 覆盖率 0%，枚举定义的取值和序列化/反序列化未测试 | 枚举变更可能导致意外兼容性问题 | 3 月内 | ✅ 已关闭 |
| GAP-007 | `core/contracts.py` | 覆盖率 0%（虽然该文件仅为 re-export），但缺少对 re-export 路径有效性的测试 | 引入新契约时可能漏导出 | 3 月内 | ✅ 已关闭 |
| GAP-008 | `data.py`, `data_mcp.py`, `pyproject.toml` | 数据源从 Data-Core 迁移至 MCP/akshare，移除期货因子演化 | 消除 Data-Core 外部依赖，简化部署，仅保留 A 股/ETF 因子演化 | 立即 | ✅ 已关闭 |
| GAP-009 | `evolution_loop.py` | 种子因子评估计入熔断计数器，导致高失败率提前熔断 | 种子因子大量失败拉高失败率，触发熔断，演化无法正常进行 | 立即 | ✅ 已关闭 |
| GAP-010 | `docs/harness/09-advancement-plan.md` | 晋级计划文档未同步至 v1.1.0，里程碑记录停留在 v0.3.0 | 历史里程碑缺失，项目状态不透明 | 1 月内 | ✅ 已关闭 |
| GAP-011 | `execution_modes_flowchart.md`, `business_flow.md` | 流程文档缺失，执行模式流程图和业务流程图未创建 | 系统执行流程不透明，新成员难以理解系统运行方式 | 3 月内 | ✅ 已关闭 |
| GAP-012 | `agents/*.md` | 角色职责文档缺失，未定义各 Agent 的职责边界和能力范围 | 多 Agent 协作时职责不清，可能导致越界操作 | 3 月内 | ✅ 已关闭 |
| GAP-013 | `plans/production_plan.md` | 生产就绪计划缺失，生产部署、监控告警、容器化等方案未文档化 | 生产环境部署缺乏标准化流程，运维风险高 | 3 月内 | ✅ 已关闭 |
| GAP-014 | `scripts/verify_doc_consistency.py` | 文档一致性检查脚本缺失，无法自动校验代码与文档的映射关系 | 文档与代码容易脱节，Harness 规范第 13 项检查无法自动化 | 3 月内 | ✅ 已关闭 |
| GAP-015 | `fts/data_futures.py`, `fts/data.py`, `fts/cli.py` | 期货数据接入缺失，FTS 仅支持 A 股/ETF 因子演化，无法覆盖期货横截面因子 | 策略覆盖范围受限，无法实现跨品种因子（跨商品动量、品种间强弱） | 3 月内 | ✅ 已关闭 |
| GAP-099 | `fts/factor_engine/evolution_loop.py` | evolution_loop.py God Class：5117 行单文件 / 62 方法 / 5 个巨方法（>280 行）内联编排逻辑，`__init__` 装配 76 属性 | 单文件不可并行开发、review 困难；方法间共享 self 状态改动互相牵制（34 计划 B 阶段治理中） | 34 计划（B 阶段进行中） | 🔴 修复中（2026-08-13 登记，plans/34-evolution-loop-refactor-inventory.md 盘点完成：**Phase 46a 已抽 `evolution_uct.py`**（领域 I UCT/熔断 5 方法，v2.103.0+4）；**Phase 46b 已抽 `evolution_trace.py`**（领域 J trace/经验链/实验日志 12 方法 + _QualityInspectionResult，v2.103.0+6）；**Phase 46c 已抽 `evolution_channels.py`**（领域 G GP/深度/算子 DSL 通道 4 方法，v2.103.0+7）；**Phase 46d 已抽 `evolution_seeds.py`**（领域 D 种子评估晋升/L1 合并/种子相关性/横截面/Barra 暴露/microstructure 晋升 6 方法，v2.103.0+11）；后续 5 领域 Mixin 按顺序推进，公开 API 不变） |

### P2 — 新登记

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-091 | `fts/data_futures_fundamental.py`（AkshareFuturesFundamentalProvider 预留接口）+ AKShare 仓单接口 | 期货仓单数据未接入：AKShare 仓单接口仅 CZCE（`futures_warehouse_receipt_czce`）/GFEX（`futures_gfex_warehouse_receipt`）可用，SHFE（`futures_shfe_warehouse_receipt`）/DCE（`futures_warehouse_receipt_dce`）因官网改版（SHFE `.dat` 永久 404）+ WAF 反爬（DCE 412，requests/curl_cffi 均被拦截）程序化不可用；且均为单日快照接口 | 库存/基差已接入（GAP-083 补充），仓单维度缺失影响全品种供需平衡信号（交易所仓单 vs 社会库存互补） | 2026-08-11 | ✅ 已关闭（**阶段 1+2 完成（2026-08-11）**：CZCE/GFEX 走官方接口——`VARIETY_EXCHANGE` 交易所路由 + ThreadPoolExecutor 并行逐日 + 跳过周末 + 25 自然日窗口（SR 实测 72,243 手）；SHFE/DCE/INE 改走东财 RPT_FUTU_STOCKDATA（阶段 2 新增 `EM_WAREHOUSE_MAP` + `_fetch_warehouse_receipt_em`，字段 ON_WARRANT_NUM→warehouse_receipt / ADDCHANGE→change，Choice 归一化注册仓单口径，历史近 3 个月；RB 实测 36,512 / M 25,100 / NR 16,632 / SC 2,961,000）；中金所股指无商品仓单降级空；data.py `enrich_futures_fundamental` 注入 fut_warehouse_receipt/fut_warehouse_receipt_chg；全品种闭环，测试 40 passed（新增东财路由/映射覆盖 5 例）） |
| GAP-092 | `fts/factor_engine/regime*.py`（远期）+ 宏观数据面板 | 宏观制度维度缺失（Bridgewater 增长×通胀四象限）：Regime 体系当前仅基于量价（HMM 后验/规则软投票），无宏观制度判定层 | 制度检测缺宏观维度，跨资产/跨周期 regime 判定失真（与头部机构差距，见 plans/28 §6） | 远期（P2） | 🔴 开放（2026-08-11 plans/28 登记；需宏观数据面板接入，基于 18-macro-field-enhancement 基础） |
| GAP-093 | `fts/factor_engine/regime*.py`（远期）+ RL 决策层 | RL 制度条件决策层缺失（SSRN 5785443）：制度判定→组合决策为规则/概率链，无强化学习条件决策层 | 制度→仓位/权重映射为静态规则，非数据驱动优化 | 远期（P2） | 🔴 开放（2026-08-11 plans/28 登记；需实盘反馈闭环，simulated_portfolio 已铺垫） |
| GAP-094 | `fts/factor_engine/regime_calibration.py`（远期） | 置信度 isotonic/Platt 概率校准缺失：confidence 为原始后验/规则伪概率，未经历史 regime 标签校准 | 置信度概率语义不严格（0.7 ≠ 70% 命中率），exposure_scale 缩放基准可能偏差 | 远期（P2） | 🔴 开放（2026-08-11 plans/28 登记；需足够历史 regime 标签，T9 验证模块提供基础） |
| GAP-095 | `fts/factor_engine/portfolio_loop.py`（远期） | regime blend 幂次调节（blend_power）缺失：概率混合权重为线性加权，无幂次锐化/钝化控制 | 概率分布过平时倍率拉平，blend 区分度不足（见 plans/28 §5 风险表） | 远期（P2） | 🔴 开放（2026-08-11 plans/28 登记；视实测倍率拉平情况启用） |
| GAP-090 | 全链路数据层（`fts/store/` 新增）+ `docs/harness/_data/storage_landscape.yaml` + `scripts/migrate_elite_json_to_catalog.py` | 数据持久化治理差距：数据格式分散（8+ 种形态并存：duckdb/json/npy/jsonl/csv/png/gz/sqlite）、JSON 泛滥（3,670 个文件、22.7MB，其中演化 traces 1,494、combo_history 200、agent_proposals 370）、同类数据双写漂移（elite 因子 JSON 快照 + DuckDB catalog 双写 GAP-032 反向；factor_catalog 拆 3 库却每库重复建 13 张表）、信号缓存裸 .npy 无 schema/校验（283 个）、`fts_history.duckdb` 单写者连接被进程长期持有（只读亦被锁） | 数据管理/持久化无统一目录（SSOT 未达成）；状态类（可重建）与资产类（不可重建）混放；迁移无渐进通道 | 3 月内（plans/29 P0~P4） | 🟡 开放（**P0 基建 ✅ + P1 因子资产入库 ✅（2026-08-11）**：P0——新建 `fts/store/` 存储域注册表（`StorageRegistry`/`StorageDomain`/`StorageBackend` 枚举 + YAML 契约加载与校验，13 域登记于 `docs/harness/_data/storage_landscape.yaml`）；`FTS_STORAGE_LANDSCAPE_PATH` env 覆盖；tests/store 13 用例全绿；零存量数据变更。**P1**——新建 `scripts/migrate_elite_json_to_catalog.py`（差量补齐+逐字段校验+dry-run/verify-only/`--sync`，trace_id 贯穿，幂等可重入）：stock 补齐 389（525 行）、futures 补齐 139（326 行），**差量缺失归零**；`--sync` 同步既有漂移 83 处（JSON 含 NaN 加固更新于 catalog 之后，以 JSON 为准同步内容字段）；**778 因子逐字段校验 0 不一致**（`--verify-only` 复核 migrated=0/mismatches=0）；写路径反转 `_promote_to_elite`（先 DuckDB 后 JSON 快照，JSON 降级只读备份）；`add_evaluation` 新增 `update_catalog_status` 参数（迁移归档因子避免 archived→active 覆盖）；迁移前备份 `data/backup/factor_catalog_{stock,futures}.duckdb.bak.p1_20260811`；tests/scripts 17 用例全绿；受影响回归 evolution_loop promote 105 + factor_db 67 passed。**P2（2026-08-11）**——新建 `fts/store/state_db.py` `StateKVStore`（`state_kv` 当前状态表 namespace+key 复合主键 UPSERT + `state_history` 历史追加表 seq 自增可回放，默认库 `data/state.duckdb`）+ `scripts/migrate_state_to_duckdb.py`（权威状态 glob 规则入库 + 过程痕迹 tar.gz 归档复制语义 + dry-run/verify-only，trace_id 贯穿，幂等可重入）：**231 权威状态条目全部入库**（`state_kv` 231 行：portfolio 225 + evolution 2 + extractors 1 + knowledge 1 + loop 1 + meta_loop 1；`state_history` 231 行），读回逐字段对账 **231/231 一致 0 mismatch**，SQL 级抽样复核 combo_history 嵌套完整；过程痕迹 **2307 文件归档** `data/archive/state_traces_20260811235839.tar.gz`（源保留，删除留 P4）；`StateKVStore.snapshot()` 支持无 state.json 冷启动；tests/store 11 + tests/scripts 8 = **19 用例全绿**，受影响回归 `pytest tests/store/ -v` + ruff/mypy 全绿。**P3-A（2026-08-11）**——`fts/factor_engine/factor_optimizer.py` `FactorSignalCache` 信号缓存 Parquet 化：`put` 写单列 Parquet（**DuckDB 原生写/读，零新增依赖**）+ `signal_index.json` 元数据表含 backend/version/checksum；`get` Parquet 优先校验 checksum（篡改判 miss 删除）、`.npy` 只读兼容回退并自动重建；`clear`/`invalidate_factor` 双格式清理；test_factor_optimizer **51 passed**（+5 Parquet 用例）+ ruff/mypy 全绿；storage_landscape signal_parquet 域升 active。**P3-B（2026-08-12）**——行情库冷热归档：新建 `scripts/archive_history_cold.py`（dry-run/verify-only/archive，trace_id 贯穿，幂等，写锁占用降级拒绝）；演化任务结束后无写锁窗口实际执行——`kline_cache` **≤2013 共 44,134 行（2005-2013，9 年）导出 `data/archive/history_kline_cache_{year}.parquet`（1.22MB）并从热库 DELETE**，热库剩余 323,839 行（2014-2026 完整），verify 一致（cold_rows=44134/hot_remaining=0）；归档前备份 `data/backup/fts_history.duckdb.bak.p3_20260812`；test_archive_history_cold **7 用例全绿** + ruff/mypy 全绿；storage_landscape market_history 域标注冷热分层。**连接生命周期治理**：`data_futures.py` 全局单写者演化进程存活期持写锁（连只读亦锁），通过「脚本化冷热归档 + 无写锁窗口执行」缓解，底层改造登记远期遗留。**P4（2026-08-12）部分实施**——storage_landscape 终态刷新（8 active + 5 legacy 标注待冻结期退役）、一致性 13/13、P0~P3 受影响回归 90 passed；**清理项（elite JSON 快照/状态 JSON/.npy/experiments）受计划「冻结期≥1 发布周期」约束，刚完成迁移冻结期未满，登记待冻结期满执行**（bump 亦留待冻结期正式发布）。全部计划推进完毕（P0~P4，P4 清理项待冻结期）。**P4 读路径切换（2026-08-12）**——JSON 双读兼容期收紧为 DuckDB SSOT 优先、JSON 仅回退：(a) 动态池 `get_dynamic_core_subset` SSOT `state.duckdb`（portfolio/futures_dynamic_pool）→ JSON 缓存 → 静态池三级降级 + sync_liquidity_pool 写路径同步；(b) CLI `factor list/show` 默认 DuckDB 查询、JSON 目录仅回退、`factor show` 补 `--market`；(c) 运行状态 state.py/portfolio_loop/meta_loop 读写 → `StateKVStore`（get_state_store 进程级单例 + 可注入 store）；(d) extractors base `_load_state`/`_save_state` → `StateKVStore`。测试适配（状态类注入临时 StateKVStore fixture 隔离 SSOT；test_dynamic_pool _EmptySSOT mock；test_cli_extra 目录模式用例模拟 DuckDB 不可用回退）。定向回归全绿（store 24 + migrate_state 8 + archive_history 7 + factor_optimizer 51 + dynamic_pool 10 + cli 264 + 状态套件 634）+ ruff/mypy 全绿。**E.3 S2 连接生命周期 L4 侧根治（2026-08-13）**——`state.duckdb` 后端切换 **SQLite WAL**（`data/state.db`）：WAL 多读单写不互斥，演化进程写连接存活期间外部只读不再被锁（对照：DuckDB 实测 read_only 报 File is already open）；upsert 单事务双表原子、seq AUTOINCREMENT 单调；`scripts/migrate_state_to_sqlite.py` 迁移（幂等/锁占用降级/不删源冻结期）；tests 33 passed + 调用方 74 passed + ruff/mypy 全绿；storage_landscape run_state 域 backend=sqlite。**L2/L3 DuckDB 库连接生命周期根治（S1：写连接短生命周期 + filelock 跨进程写互斥，2026-08-13 实施）**——见 design/E.2 推荐路线，现已实施：① 新增 `fts/store/duckdb_lock.py` 跨进程写锁（msvcrt/fcntl 标准库零依赖，`data/.locks/{name}.lock`，超时抛 TimeoutError）+ 4 用例；② L2 `data_futures.py` 删 `_WRITER`/`_DB` 全局常驻缓存，新增 `_write_scope()`（filelock+短连接），读池改 `read_only=True`，`_write_contract_kline` 迁移短连接；③ L2 `aggregator.py` 删 `_cache_conn` 常驻连接，读路径 `_open_read_conn`（read_only 短连接）+ 写路径 `_write_scope`；④ L3 `repository.py` 4 类 `_get_conn` 补 `lock_configuration=true` + 嵌套 repo 用后 close；⑤ 演化晋升 `@_release_repo_after` 方法退出即释放 repo 写锁；⑥ 同步脚本写段迁移；⑦ 受影响模块 **653 passed** + ruff 全绿 + mypy 通过（剩 2 处 HEAD 预存不顺手修）；顺带修复预存 Binder Error（tick_cache TIMESTAMP 比较 CAST）与注入破坏的类结构（mypy 检出 100+ attr-defined → 归零）。**验收达成**：演化进程零长驻写连接、跨进程写 filelock 串行、写后连接即关、读路径 read_only。P4 清理项（elite JSON 快照/状态 JSON/.npy/experiments 与旧 `data/state.duckdb`）仍受冻结期约束待执行。） |
| GAP-016 | `fts/factor_engine/seed_data_futures_full.py`, `scripts/run_futures_evolution.py`, `scripts/futures_signal_pipeline.py`, `scripts/futures_strategy.py`, `scripts/futures_l3_portfolio.py` | 期货全量种子因子库（12 大因子家族 50+ 子因子）、期货因子演化脚本、期货信号管道、期货组合策略、L3 组合构建均已实现，但缺少集成测试验证期货全链路端到端运行 | 期货演化 → 信号管道 → 组合构建的全链路缺少自动化回归测试 | 3 月内 | ✅ 已关闭 |
| GAP-024 | `fts/factor_db/` | 因子存储使用 JSON 文件，缺乏版本管理、高效查询、相关性存储能力；种子因子硬编码在 Python 文件中，维护困难 | 因子数据无法高效检索和版本追踪；因子间相关性无法系统性评估；种子因子修改需要改代码 | 1 月内 | ✅ 已关闭 |
| GAP-025 | `fts/factor_engine/evolution_loop.py` | 6 个孤立模块（AblationExperiment/ShapAnalyzer/RobustnessTester/CausalValidator/FeatureImportanceAnalyzer/LogicMonitor）已集成进演化循环，但集成调用签名与模块真实 API 不符，运行期全部落入 except 默认放行，审查门禁未真正生效 | 伪相关/事件敏感/不鲁棒因子可绕过审查直接晋升精英池 | 1 周内 | ✅ 已关闭 |
| GAP-026 | `fts/factor_engine/expr_dsl/` + GP 引擎 | GP 引擎算子命名与 DSL 未对齐（`delta`/`pct_change`/`scale` vs `ts_delta`/`ts_pct_change`），GP 产物暂为 CODE 类型 | 算子语义无法直接映射，GP 产物维持 CODE，对齐属后续演化引擎计划 | 3 月内 | ✅ 已关闭 |
| GAP-027 | `fts/factor_engine/contracts.py` + `factor_program.py` | `code: str\|None` 可选化未审计：算子因子暂保留确定性生成代码，需审计全部 `factor["code"]` 读取点后方可可选化 | 契约中 `code` 保持必填，可选化存在隐性破坏风险 | 3 月内 | ✅ 已关闭 |
| GAP-028 | `tests/cli/test_data_cli.py` 等 | 既有失败测试文件（test_data_cli.py 断言 `_cmd_data_*` 旧接口、test_tasks.py 任务数断言过期、test_hotswap.py 依赖 watchdog、test_engine.py MagicMock 断言、test_shap_analyzer.py 依赖 shap、test_factor_lineage.py 触发 DuckDB ART 索引 bug、test_data_source_metrics.py 缺 `_metrics_cache`）与当前实现不匹配 | 全量回归需排除这些文件，无法一键全绿验证 | 3 月内 | ✅ 已关闭 |
| GAP-029 | `fts/factor_engine/portfolio_loop.py` | L3 组合每日全量重建且无漂移度量、无粘性约束、无 L2 晋升节奏控制：组合成员/权重更换幅度不可见，权重可大幅跳变，新演化因子次日即全权重进入组合 | 组合更换频率不可监控，存在策略漂移风险 | 已解决（v2.11.0 漂移治理；v2.72.1 GAP-F13 漂移告警闭环：阈值可配置 + 超阈值告警 + 粘性重平衡建议） | ✅ 已关闭 |
| GAP-030 | `fts/factor_engine/evolution_loop.py` | 6 个 evolution_loop 集成测试（promote_to_elite/failure_rate_circuit_breaker/low_ic_increment/consecutive_low_ic_reset/periodic_review）依赖 LLM mock 环境，本地运行失败（git stash 验证与本改动无关） | 这些测试无法在本地稳定运行 | 3 月内 | ✅ 已关闭 |
| GAP-031 | `fts/factor_engine/meta_loop.py` + `evolution_loop.py` + `seed_pool.py` | L1 注入候选未接入 L2 演化：`SeedPool.inject_from_l1`/`list_injected_l1` 接口存在但全库无调用方（死代码）；meta_loop `_inject_candidate` 只写 `l1_injected/` + `factor_pool.json`，从未调用注入接口；`_list_base_seeds` 主动过滤 `l1:` 前缀导致 L2 读取不到；`inject_from_l1` 仅写内存缓存不落盘，L1/L2 跨进程天然失效 | L1 花 LLM token 生成的候选成为"孤儿数据"：不进 L2 演化、不走评估链/晋升，仅被 L1 自身用于去重 | 3 月内 | ✅ 已关闭 |
| GAP-032 | `fts/factor_engine/evolution_loop.py` 晋升路径 | 演化产物未同步 DuckDB factor_catalog：elite 快照 133 个因子的 factor_id 不在 `data/factor_catalog.duckdb` 中（2026-08-03 后演化产物），`factor list`/`backtest batch` 的 DuckDB 查询模式读不到这些因子 | "目录直读 vs DuckDB"数据分叉：DuckDB 查询视角下演化产物不可见，catalog 统计（1945 行）与 elite 实际快照不一致 | 3 月内 | ✅ 已关闭 |
| GAP-035 | `fts/factor_engine/factor_clustering.py` | 因子信号矩阵缺乏 PCA 降维，Elastic Net 在因子数较多时仍可能达到 20 因子上限，无法通过正交主成分进一步压缩信号源 | 信号源维度高，组合复杂度大，换手率成本非线性增长 | 3 月内 | ✅ 已关闭 |
| GAP-036 | `fts/factor_engine/evolution_loop.py` | L1 注入候选文件消费后未删除，l1_injected 目录累积 518 个 JSON 文件，历史文件持续堆积 | 大量历史文件占用磁盘空间，干扰目录扫描效率，L1 候选文件失去消费状态的可见性 | 3 月内 | ✅ 已关闭 |
| GAP-037 | `fts/ml/`（未实现） | 深度学习时序模型（LSTM/GRU/Transformer）与强化学习（RL，DQN/PPO/SAC）未实现：FTS 本次升级仅落地 LightGBM/XGBoost/Ensemble 传统 ML 模型（Phase 24），深度学习与 RL 需引入 PyTorch/TensorFlow/gym 等重依赖，训练成本高、可解释性低 | 无法利用深度时序特征与序列决策优化，信号合成停留在浅层模型 | 3 月内 | ✅ 已关闭（轻量深度时序模型已落地且不引入重依赖：① v2.60.0 GAP-F05 纯 numpy MLP 因子模型（缺依赖优雅降级）；② v2.73.0 GAP-I203 GRUFactorModel 纯 numpy 单层 GRU + DeepFactorGenerator + L2 漏斗接线（test_gru_factor 28 用例）；RL（DQN/PPO/SAC）依赖 gym 环境重依赖、可解释性低，登记为远期研究项不阻塞，见 plans/21 GAP-F05） |
| GAP-038 | `fts/factor_engine/evolution_loop.py` | 种子因子相关性预检 `compute_cross_section_correlations` 在期货横截面模式（184 种子 × 25 品种 × 500 日）下计算量过大且无超时保护，演化进程卡死（CPU 0%，无日志输出），ThreadPoolExecutor timeout 无法中断卡在 numpy/scipy C 扩展中的线程 | 夜间因子演化无法完成，进程长时间无响应 | 已解决（v2.39.0 规模保护跳过） | ✅ 已关闭 |
| GAP-039 | `tests/` 全量回归（67 failed + 16 errors，v2.39.0 基线） | 全量回归存在 67 个失败 + 16 个收集/运行错误，来源两类：① 预存断言过期（test_data_cli/test_tasks/test_sync_futures_task 等，GAP-028 同类）② 并行 v2.38.0+ 工作区改动引入（test_http_server/test_seed_pool/test_seed_loader/test_risk_tag/test_contracts/test_portfolio_loop 等，未提交） | 无法一键全绿验证，回归基线不可信，新改动无法区分自身回归与既有噪音 | 3 月内 | ✅ 已关闭（v2.47.0 回归清零 3836 passed） |
| GAP-041 | 16 个覆盖率 <90% 模块 | v2.47.0 全量回归后 16 个模块覆盖率 <90%：`cross_market/data_adapter(55%)` `factor_clustering(64%)` `tdx_minute_source(67%)` `tqsdk_tick_source(73%)` `factor_db/migrate_from_json(73%)` `evolution_loop(80%)` `tq_source(81%)` `data_quality_monitor(82%)` `ifind_source(84%)` `data(85%)` `factor_db/repository(85%)` `ml/models(86%)` `wind_source(87%)` `factor_screener(87%)` `causal_validator(89%)` `contracts(89%)`，缺口语句集中在外部数据源网络/鉴权路径与异常兜底分支 | 关键路径异常分支未验证，外部数据源降级逻辑存在隐性 bug 风险 | 3 月内 | ✅ 已关闭（v2.88.0 GAP-F16：三分组补齐 14 个 <90% 模块测试 +341 用例——组A 数据源网络/鉴权/超时/降级 mock、组B evolution_loop/factor_screener/contracts/causal_validator/factor_clustering 兜底分支、组C migrate_from_json/data_layer_repos/ml 降级路径；全量回归 5132 passed、覆盖率 TOTAL 94.31%（--cov-fail-under=90 达标）、14 缺口模块全部 ≥90%；v2.87.0 后 tdx_minute_source/tq_source 已合并删除、新源 tdx_local_source 93%） |
| GAP-042 | `fts/factor_engine/high_ic_screener.py` | 高IC筛查的「极值样本扰动测试（V2/检查项 5）」依赖外部传入 `extreme_perturbation.ic_drop`，当前 `_promote_to_elite` 未计算该数据 → 该项实际恒为 skipped，极值扰动一票否决（>25% 降幅）在 L2 入库质检中未真正生效 | 高IC因子可能仅依赖少数极端样本支撑，筛查存在盲区 | 3 月内 | ✅ 已关闭（v2.79.0：`evaluation_chain._compute_extreme_perturbation_ic` 极值剔除重算 IC + `FactorEvaluation.extreme_perturbation` 输出 + `_promote_to_elite` 传入 screener V2 一票否决生效，见 plans/21 GAP-F15） |
| GAP-043 | `fts/factor_engine/evolution_loop.py` + `evaluation_chain.py` + `ablation.py` | 质检拦截器判定缺陷：① 消融实验 `shuffle_dates`（时间戳打乱）对时序因子必然摧毁 IC（时序依赖是必要特征）、`zero_one_feature` 置零核心价格列（open/high/low/close/vwap/settle）对价格因子必然摧毁 IC，被统一判定为"伪相关"误杀高IC候选；② 鲁棒性缺失值测试 `_inject_missing` 注入 NaN 后 `_compute_ic` 的 spearmanr/pearsonr 无 NaN 掩码返回 0.0，缺失值测试 3/3 恒失败（保持率 0%） | L2 期货演化 15 代中 5 个通过 Verifier 的候选（IC 0.31~0.52）全部被误杀 → 失败率 100% 熔断，演化停滞 | 已解决（v2.50.0 信息型/拦截型判定 + IC NaN 掩码） | ✅ 已关闭 |
| GAP-044 | `fts/factor_engine/robustness.py` | 鲁棒性缺失值测试阈值过高（0.80）：`_inject_missing` 随机单元格级 NaN 注入比真实数据质量问题激进得多（5% 随机 NaN 即使高质量种子 IC=0.49 的保持率也降至 0.56），导致 12 个种子因子全部被拦截，父因子池为空，后续 GP 演化全退化（11 个常数信号因子），总失败率 100% 熔断 | L2 期货演化持续 100% 失败率熔断，无法产生新精英因子 | 已解决（v2.52.0 `missing_retention_threshold` 0.80→0.50） | ✅ 已关闭 |
| GAP-052 | `fts/scheduler/jobs.py` | 每日 20:00 L3 定时任务 `l3_portfolio_loop_job` 误用股票 elite 目录（`cfg.elite_dir` + 默认 market="stock"），与下游期货信号管道不一致 | 自动化任务产出的 L3 组合由股票因子构建，与期货信号输出口径错配 | v2.73.0 | ✅ 已关闭（v2.73.0：显式 `elite_dir=cfg.futures_elite_dir` + `market="futures"`，新增 test_uses_futures_path） |
| GAP-053 | `fts/factor_engine/weight_learning.py` + `portfolio_loop.py` | elastic_net 权重为回归系数归一化：无风险调整（波动率/风险贡献）、一次性全样本学习无样本外验证、学习面板固定 CSI300 与期货目标市场错配 | 组合权重不反映"每单位风险的信号贡献"，权重稳定性/衰减不可观测，期货组合的权重学习样本与交易市场不一致 | v2.75.0 | ✅ 已关闭（v2.75.0：Ledoit-Wolf 风险调整 + 滚动样本外验证 + 面板按目标市场自动匹配 + 跨市场 IC 对比，28 用例；v2.78.1 起 cross_market_ic 默认关闭，避免无关股票面板下载） |
| GAP-054 | `scripts/liquidity_snapshot.py` + `scripts/sync_liquidity_pool.py` + `fts/data_futures.py` + `fts/scheduler/jobs.py` | FUTURES_CORE_SUBSET 为静态硬编码 25 品种，无法随市场流动性变化动态调整；流动性评估口径缺陷（当前主力合约 60 日历史含换月窗口污染，AU/IM/IF 等刚换月品种成交额被低估 10-1000 倍；合约乘数表 AU0 带主连后缀匹配失败；月均价合约 L-F2610 误匹配） | 核心交易池与市场流动性脱节：流动性恶化品种无法退出、改善品种无法进入；因子横截面与实盘可交易性错配（机构级缺陷） | v2.80.0 | ✅ 已关闭（v2.80.0：TQ-Local 17709 真实主力合约最近 5 日主力窗口口径 + 乘数表修复 + 月均价合约排除 + 渐进式替换动态池 + get_dynamic_core_subset 运行期零风险降级 + 每周六 08:00 调度刷新 + 10 用例；2026-08-10 快照 25 核心品种全部达标） |
| GAP-055 | `fts/data_futures.py`（FUTURES_HOLDOUT） | 盲测池仅 6 个且以小品种为主（JD/AP/FG/UR 流动性弱），存在选择偏差，可能系统性低估因子跨品种泛化能力 | 盲测结论失真：因子泛化能力被低估，精英因子筛选标准失真 | v2.81.0 | ✅ 已关闭（v2.81.0：盲测池 6→15 按产业链分层抽样覆盖 10 条产业链，与核心动态池/分层训练集互不重叠，含大流动性代表 RU0/L0；新增 test_holdout_pool.py 9 用例全绿） |
| GAP-056 | `fts/data_futures.py`（DuckDBConnection/AsyncWriteQueue/retry_on_conflict）+ `fts/scheduler/jobs.py` + `fts/factor_engine/factor_db/schema.py` + `repository.py` | DuckDB 并发模型治标不治本：① 跨进程并发写（scripts 与调度 job 并行）抢文件锁产生 ConcurrentTransactionException；② 写事务/checkpoint 期间只读查询被文件锁阻塞，回测/因子加载随机卡顿；③ 重试是概率规避而非结构消除，高负载下重试耗尽仍抛错 | 数据层并发冲突/读阻塞影响全链路稳定性，回测与实盘数据路径存在随机失败风险（架构级缺陷，见 design/E.1） | 1 月内 | ✅ 已关闭（v2.86.0：DuckDBWriter 单写者 + 进程内写锁 + executemany/copy_from_records 显式 BEGIN/COMMIT 整批原子 + DuckDBReader 读连接池读写解耦 + _get_db 拆分为 _get_writer/_get_reader + 4 配置项 + 15 用例，见 design/E.1-duckdb-concurrency-design.md；v2.101.0 补充分库方案：按市场拆分文件消除跨市场锁竞争——schema.py 新增 DATABASE_PATH_STOCK/DATABASE_PATH_FUTURES + get_db_path(market) 路由，repository.py 四类 Repository 构造器新增 market 参数，10+ 调用方按市场传递，迁移脚本 migrate_factor_catalog_split.py/verify_split.py/concurrent_test.py） |
| GAP-057 | `fts/data_sources/tq_source.py` + `fts/data_sources/tdx_minute_source.py` + `fts/core/enums.py` | 通达信数据源双源割裂且命名误导：① TQ_LOCAL 标注端口 7721 实际不存在（量化模拟客户端真实监听 17709），TQLocalSource 走 `tq_get_kline/tq_get_quote` 方法名返回 -32601 不可用；② TDXMinuteSource 仅支持分钟，命名暗示局限；③ 同一下游服务被拆成两个源，聚合器双源配置混淆，`TDX_MINUTE`/`TQ_LOCAL` 枚举语义不清 | 数据源路径含失效源，分钟/日线/快照能力碎片化，枚举与降级链语义误导维护与排障 | 2 周内 | ✅ 已关闭（v2.87.0：合并为 `TdxLocalSource` 单源统一承载日线（day→1d，17 列）+ 分钟（1m~60m，11 列）+ 快照（get_market_snapshot），`source_name="TDX_LOCAL"` 端口 17709；`DataSource.TDX_MINUTE`→`TDX_LOCAL`、TQ_LOCAL 标注已废弃；删除 tq_source.py/tdx_minute_source.py；aggregator/fusion/data_futures/cli 全链路替换；70 用例（51+19）迁移全绿，tdx_local_source 覆盖率 93%） |
| GAP-059 | `fts/scheduler/jobs.py` | v2.80.0 提交（884f772）在 `tasks.py` 注册 sync_liquidity_pool 任务（callable_path=`fts.scheduler.jobs.sync_liquidity_pool_job`）但 `jobs.py` 实际缺失该函数（git HEAD 核验），内部调度器与 TRAE 自动化任务运行时 ImportError | 动态池每周刷新链路断裂，任务注册了不存在的可调用对象 | v2.89.1 | ✅ 已关闭（v2.89.1：补回 sync_liquidity_pool_job（调用 scripts.sync_liquidity_pool.main()）+ __all__ + 回归测试 3 用例——默认任务 callable 可导入锁 + 成功/失败路径，调度器 156 全绿） |
| GAP-058 | `tests/test_data_futures_panel.py::TestDominantContracts` + `fts/data_futures._get_reader` | 主力合约判定 2 用例（`test_missing_symbols_empty`/`test_db_error_returns_empty`）在全量回归中间歇性失败：mock `_get_reader` 未拦截时泄漏真实 DuckDB 数据（RB2610/CU2609）。**根因确诊 = 并发会话竞态**：另一 TRAE 会话进程并发访问真实 `data/fts_history.duckdb`（外部进程占锁）且并发 bump pyproject 版本号——证据：① 本会话完整全量 5130 passed/2 failed（panel 2 失败）与 5106+1 版本竞态失败（`test_package_init` assert 2.88.0==2.89.0，另一会话运行中改 pyproject）交替出现；② v2.88.0 记录明确「全量回归 5 个竞态失败——DuckDB 外部进程占锁 ×4 + pyproject 版本并发 bump ×1——重跑验证后全绿」；③ 全部单文件/定向组合（1277/1548/1041/424/428/261 用例）在无并发进程时全绿。非确定性代码缺陷（真实 DB 有 RB2610/CU2609 数据，mock 失效即泄漏；单进程无竞态时 mock 恒拦截，已验证 globals 一致性） | 全量回归在并发会话场景下出现 2 红，回归基线受竞态干扰 | 1 月内 | ✅ 已关闭（根因=并发会话竞态，非代码缺陷；测试已加固——`_get_reader`/`_release_reader` 全部 mock + `_fetch_dominant_akshare` 显式 stub，TestDominantContracts 4 用例全绿，单文件 12 passed；规避：两会话避免并发跑全量测试/并发访问真实 DuckDB，无外部进程时重跑全绿；v2.99.0 全量回归 5267 passed/1 failed，唯一失败为 `test_package_init` pyproject 版本并发 bump 竞态，非本差距代码路径） |
| GAP-071 | `fts/factor_engine/evolution_loop.py` + `evaluation_chain.py` + `walk_forward.py` + `factor_program.py` + `signal_cache.py`（新增） | L2 质检链路性能缺陷：① 双重 WalkForward——三级评估链（Step 3 `evaluate_walk_forward`）与审计侧（Step 4.6 `_run_walkforward_oos`）对同一候选各自独立跑一次完整多窗口走航（默认 4 窗口 × 每窗口 train+oos 各执行一次因子代码），重复计算；② 同一候选因子代码在质检链被重复执行数十次（L1 回测/极值扰动/消融 baseline/鲁棒性 baseline/SHAP 全量信号各自独立沙箱执行）；③ 评估链走航窗口 IC 用全局 `forward_returns` 尾部切片（非窗口 oos 段），非末窗口 IC 失真；④ `WalkForwardOptimizer` 窗口结果构建对空 train 段（window_years=0 短数据适配路径）抛 IndexError 导致窗口被静默丢弃 | L2 单候选质检耗时高（每候选多跑一次完整走航 + 数十次重复因子执行），走航窗口 IC 口径错误影响稳定性判定，短数据路径窗口丢失 | v2.98.2 | ✅ 已关闭（v2.98.2：① `_run_factor_audit` 优先复用 `evaluation["walk_forward"]`（n_windows_completed>0），失败兜底独立计算；EvolutionLoop 调 `evaluate` 传 `walk_forward_config=_build_wf_config(data)` 统一走航配置；② 新增 `signal_cache.SignalCache`（LRU+线程安全，按 factor_id+params+数据全列值指纹命中），`FactorExecutor` 接入缓存，`evaluate_backtest`/`evaluate_walk_forward`/`EvaluationChain.evaluate`/`ShapAnalyzer.analyze` 透传，evolution_loop 消融/鲁棒性/SHAP 注入共享缓存（完整数据信号 L1/极值扰动/消融 baseline/鲁棒性 baseline 全命中）；③ 走航窗口 IC 修正为 oos 段内自算 fwd（与审计同口径），且每窗口不再执行 train 信号（省一半执行）；④ `_df_boundary_date` 空 train 段容错；新增 test_signal_cache.py 14 用例 + test_evaluation_chain GAP-070 3 用例 + test_evolution_loop 审计复用 2 用例，受影响模块回归 583 passed 全绿） |
| GAP-076 | `scripts/daily_signal_pipeline.py` + `scripts/_signal_common.py` + `memory/knowledge/factors/stocks_elite/fct_2e94cf1d.json` | 信号管道合成得分系统性为负（2026-08-11 实测 Top20 全负）：① 因子值非零中心/量纲偏置经加权平均主导合成符号（quality_factor_g8 截面全负 -0.86、seed_spread 反转后全负贡献）；② `volatility_reversion_g2` 用 `np.convolve(mode='same')` 尾部补零致最后 ~6 交易日移动平均严重低估（实测最后一日 ma 偏差 -15.7 元），最新截面 300 只恒 -0.5 零区分度（std=0）；③ TransactionCostModel 成本 264.54bps 统一扣减 0.2645 分放大负分 | 仅做多信号 TopN 全负、无正信号可输出，信号管道参考价值受限 | v2.101.0 | ✅ 已关闭（v2.101.0 GAP-076：① 修复 volatility_reversion_g2 卷积为 cumsum 滚动窗口均值（与 std 部分窗口前缀对齐），最新截面恢复区分度（std 0→0.238，39% 正分），沙箱编译通过；② 新增 `normalize_signal_matrix` 截面标准化（z-score ddof=0 / rank 百分位秩→[-1,1]，NaN 写回 0、常数截面置 0、非法 method 抛错、空矩阵降级），`daily_signal_pipeline` CLI `--normalize none/zscore/rank`（默认 none）；③ `save_weight_snapshot` 持久化 normalize 字段，冻结日复用快照口径，旧快照缺省 none 向后兼容；④ 实测 `--force-recompute --normalize zscore` 全量管道出现正信号；⑤ 新增 10 用例（TestNormalizeSignalMatrix 8 + TestWeightSnapshot 2），test_signal_common 17→26 全绿 + ruff check/format 通过） |
| GAP-078 | `fts/data_mcp.py` | TQ-Local（17709）探活为进程级一次性缓存：首次探活失败（TQ 瞬时抖动 > 超时）后整进程永久 False，全量降级腾讯 501 → 合成数据（2026-08-11 实测两次触发，管道运行产出无效合成面板） | 数据源间歇性抖动导致信号管道/数据获取静默使用合成数据，信号失真且无告警 | v2.101.0 | ✅ 已关闭（v2.101.0 GAP-078：重构为 `_probe_tq_once()`（单次探活，5s 超时）+ 失败冷却重试——探活成功缓存 True（进程内不再探活）；失败记录时间戳，冷却期 `_TQ_PROBE_COOLDOWN=30s` 内不重复探活（避免离线时每次调用阻塞）；冷却期后自动重探（`_TQ_PROBE_RETRIES=2` 次瞬时重试、间隔 1s），成功即恢复 True；新增 test_data.py TestTqStockAvailable 6 用例（首次成功缓存/瞬时抖动重试恢复/全失败冷却不重探/冷却期满重探恢复/合法响应解析/异常降级），test_data.py 75 passed 全绿；附带完成 zscore vs rank 历史对比：IC 差 0.0015 噪声量级，rank 略稳健、zscore 多空差略高） |
| GAP-D2 | `fts/factor_engine/neutralization.py` + `fts/risk/portfolio_metrics.py` + `fts/live_trade/book.py` + `fts/live_trade/matching.py` + `scripts/daily_signal_pipeline.py` + `fts/live_trade/gateway.py` + `scripts/calibrate_book_vs_bps.py` | 模拟交易模块进阶差距（D.2 设计，2026-08-11）：① 股票信号管道无行业/市值中性化（因子含行业/市值 proxy 伪预测力）且 L3 组合层缺失（仅 Ridge 权重，未接共享 elastic_net）；② 模拟仓无组合级风控（仅有单笔/单标的/总仓规则，无波动/VaR/相关性/连续亏损组合视角）；③ 撮合仅固定 bps 滑点（无 tick 盘口逐档撮合，容量/流动性失真）；④ 无部分成交（PARTIAL）状态机与限价单/集合竞价能力 | 股票因子"伪预测力"污染 IC 评估；组合极端行情回撤失控风险；回测与实盘撮合对齐度不足 | v2.101.0 | ✅ 已关闭（v2.101.0 D.2 全阶段实施：P0.1 neutralization.py 行业/市值中性化 + daily_signal_pipeline `--neutralize`/`--l3-mode elastic_net` 接线；P0.2 portfolio_metrics.py 6 维度组合指标 + 三级预警 + portfolio_risk_status()；P1.1 book.py/matching.py tick 盘口逐档撮合 + portfolio 注入/降级；P1.2 gateway PARTIAL/补单 + calibrate_book_vs_bps 实证标定（实测固定 bps 在宽价差档偏差 16.17bps）；P2 gateway 限价单/集合竞价；新增 4 测试文件 64 用例，定向回归 165 passed；实现偏差：neutralize 默认 none（显式启用更安全）、regime 权重接线待行业/风格面板数据源（后续登记）——**偏差 b 已补充（v2.101.0，2026-08-11）**：`--regime auto` 接线，行业/风格面板由管道自身 CSI300 面板 + industry/cap 映射自聚合构造（零外部依赖、双端键归一化），实测检测 sector_concentrated (conf=50%)） |



### GAP-I 系列 — 机构级对标（总纲：plans/23-institutional-transformation-plan.md）

> 全链路机构级差距登记（L1→L4 × 三档机构 T1/T2/T3），按「先单机后扩展」分三阶段追赶（Stage 1 对标中小团队 v2.65.0~v2.72.0 / Stage 2 对标国内头部 v2.73.0~v2.80.0 / Stage 3 对标海外顶级 v2.81.0+）。GAP-I207/I304 引用 plans/22 GAP-S01/S02 为 Stage 门槛，不重复登记详情。

#### P0 — 机构级阻塞性差距

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-I201 | `fts/factor_engine/evolution_loop.py` + `batch_mining.py` | 挖掘吞吐不足：主循环每代仅生成 1 个后代因子（串行），批量粗筛/多进程并行评估缺失，候选量级差机构 2~3 个数量级 | 同等窗口内命中精英因子期望差 2~3 个数量级（与机构差距核心根因） | 已解决（v2.65.0 批量挖掘漏斗：batch_size 批量生成 + ThreadPoolExecutor 并行粗筛 + max_candidates 预算护栏 + 准入链零改动复用） | ✅ 已关闭 |
| GAP-I207 | `fts/factor_engine/evaluation_chain.py` + `cli.py` | 股票因子行业/市值中性化未接入主流程（`_neutralize_signal_matrix` 已实现但 industry_map/cap_map 默认 None 跳过，settings `stock_neutralization` 死配置） | 股票因子 IC 含行业/市值偏好污染，污染 L2/L3 全链路结论（= plans/22 GAP-S01） | Stage 1 门槛（v2.60.0 阶段 A） | ✅ 已处理（v2.61.0 GAP-S01：EvolutionLoop stock 分支自动加载映射 + 键归一化 + 中性化前后 IC 对比） |
| GAP-I301 | `scripts/daily_signal_pipeline.py` + `portfolio_constructor.py` | 股票流水线缺 L3 组合层：仅等权求和取信号排名，无权重学习/组合优化/Regime/成本约束（期货 L3 完整未复用） | 股票 alpha 无法形成有效组合，信号管道粗糙、实盘落地风险高 | Stage 1（v2.67.0） | ✅ 已关闭（v2.68.0：股票 L3 组合层复用期货组件——`PortfolioLoop` 已支持 market="stock"（CLI `portfolio run --universe stock`），`load_elite_factors` market 过滤 + `synthesize_signals` Elastic Net/Sharpe 权重 + Step 2.5 stock_regime 风格自适应 + `build_combo` 多头组合 + 成本模型 net 指标；CLI 股票分支 L3 完成后自动触发 `daily_signal_pipeline`（与期货对称）；新增 `TestStockL3PortfolioLayer` 6 用例（组件复用性/股票组合成本模型/stock run/stock_regime）+ `TestCmdPortfolioRunStock` 3 用例） |
| GAP-I501 | `fts/factor_engine/cost_model.py` + `backtest_pipeline.py` | 回测成本/容量保真不足：已建手续费/滑点/涨跌停/停牌/展期，但无冲击成本模型（按成交量占比）、无容量限制建模 | 大权重信号高估收益、策略容量不可知，违反回测-实盘强对齐红线 | Stage 1（v2.67.0） | ✅ 已关闭（v2.67.0：`backtest_pipeline.py` 容量约束——持仓市值 ≤ 品种日均成交额 × capacity_cap_ratio，滚动 20 日均成交量截断；`settings.py` 新增 `backtest_capacity_cap`/`capacity_cap_ratio` 配置；`TestGapI501CapacityConstraint` 5 用例覆盖大仓位截断/关闭跳过/缺量跳过/违规统计/端到端报告） |
| GAP-I401 | `fts/factor_engine/feedback_loop.py` + `bridge/signal_bridge.py` | 实盘反馈闭环缺失：信号输出给 FDT 后无实盘成交/净值回流通道，因子状态仅基于历史回测 | 无法感知实盘漂移，衰减退役无实盘依据 | Stage 1（v2.71.0） | ✅ 已关闭（①② v2.66.0 GAP-L402：LiveFeedbackRecord 契约 + LiveFeedbackImporter（CSV/dict 导入 + DuckDB feedback_live 表 + 实盘 IC）+ LiveVsBacktestICReport 对比报告 + CLI `fts feedback import/live-ic`；③ v2.71.0：对比报告增加 `recommend_retire`/`decay_gap` 字段与 summary `n_recommend_retire`（衰减因子输出退役建议，供 GAP-I305 自动退役闭环消费）） |

#### P1 — 机构级重要差距

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-I101 | `fts/factor_engine/meta_loop.py` | L1 知识补给吞吐与知识源单一：每日 1 次 BootstrappingChain，仅券商研报/arXiv | L1 注入 L2 候选量小、维度单一 | Stage 1（v2.72.0 首期）+ 二期 v2.82.0 | ✅ 已关闭（v2.72.0：`validate_batch_candidates` 批量候选契约校验（candidate_id/name/code/economic_logic.narrative 逐条校验 + 合规统计 + invalid_samples 截断 5）接入 `_run_bootstrap` 前置质量门（契约不合规仅告警不熔断）；`MetaRunResult` 新增 `candidates_per_minute` 吞吐指标（候选数 / 运行分钟，`_make_result` 计算，elapsed=0 防除零）；新增 `TestValidateBatchCandidates` 8 用例。**二期 v2.82.0**：`BaseExtractorPipeline.extract` 多源并行收集（ThreadPoolExecutor，单源异常不影响其他源，多路知识源合并等待时间显著缩短）；另类知识源随 GAP-I103 落地（公告/舆情/宏观事件，股票 5 源/期货 4 源）） |
| GAP-I102 | `fts/factor_engine/factor_inspector.py` | 无 Alpha 审查/人机协同工作台：晋升全自动（Verifier+质量卡+审计），无人工审查环节 | 高 IC 但经济逻辑存疑因子可能通过自动审查 | Stage 1（v2.72.0 骨架）+ 二期 v2.82.0 | ✅ 已关闭（v2.72.0：`FactorReviewWorkflow` 审查工作流骨架——审查状态机 pending→approved/rejected（`ReviewDecision` 枚举），意见回写 DuckDB `factor_reviews` 表（幂等 UPSERT，重复审查覆盖旧决定），`list_pending` 待审查队列（排除已审查 + market 过滤 + limit 上限），`get_status` 状态查询；CLI `fts factor review list/approve/reject` 子命令（--market/--limit/--db/--comment）；schema E.1 `_CREATE_FACTOR_REVIEWS` 表（factor_id 主键/decision/comment/reviewer/reviewed_at）；新增 `TestReviewCliCommands` 4 用例（list 队列输出/market 过滤/approve 回写/reject 回写）补强 test_review_workflow 7 用例。**二期 v2.82.0**：审查意见接入经验链——`FactorReviewWorkflow.reject` 驳回且 comment 非空时构造 `ExperienceTrace`（success=False + failure_reasons + lessons 含审查人）写入 `ExperienceChain.record_failure`（`FTS_REVIEW_EXPERIENCE_CHAIN` 开关默认 True，`experience_chain` 可选注入便于测试，写入异常降级不阻断审查）；新增 `test_review_experience_chain.py` 7 用例） |
| GAP-I202 | `fts/factor_engine/expr_dsl/registry.py` + `feature_ops.py` | 算子库规模与语义体系：~50 算子，无组合/跨标的算子（双轨一致性已加（GAP-S10 v2.69.0），A 股特有算子已落地（GAP-S12 v2.69.0：A_SHARE_FIELDS 10 字段 + L5b 4 领域算子）），搜索空间与演化产出多样性仍待扩充 | 搜索空间小、演化产出多样性不足 | Stage 2（v2.75.0） | ✅ 已关闭（v2.75.0：组合/跨标的算子单一事实源——ts_slope/ts_quantile 新原语 + 8 组合/跨标的算子双注册表共享 + required_shared 硬约束 + lookback=0 罚分） |
| GAP-I203 | `fts/ml/models.py` | 深度因子学习缺失：仅 LightGBM/XGBoost/Ensemble + 轻量 MLP，无 LSTM/GRU/Transformer 时序深度模型、无 GAN/VAE 因子合成（= GAP-037） | 候选因子缺深度非线性特征维度 | Stage 2（v2.73.0 轻量 LSTM/GRU） | ✅ 已关闭（v2.73.0 深度因子学习首期：GRU 模型 + 生成器 + L2 接线） |
| GAP-I204 | `fts/factor_engine/gp_evolver.py` | 搜索方法单一：GP 适应度单一（ic/sharpe/combo），无 Pareto 多目标（IC×换手×衰减×容量）、无符号回归 | 产出高 IC 高换手因子，实盘成本侵蚀收益 | Stage 1（v2.71.0 多目标首期） | ✅ 已关闭（v2.71.0 首期：`multi_objective` 适应度模式——IC×Sharpe 正向贡献 − 换手惩罚 − 衰减惩罚，`FitnessResult`/`GPEvolveResult` 扩展 turnover/decay/best_turnover/best_decay 指标，`GPEvolverConfig` 新增 `turnover_penalty`/`decay_penalty` 系数，默认模式逻辑不变。**v2.78.0 二期**：符号回归补充搜索 + Pareto 前沿输出——① `pareto.py` 新增 `ParetoItem`/`fast_non_dominated_sort`/`compute_pareto_front`（NSGA-II 快速非支配排序，多目标 |IC|/Sharpe/−turnover/−decay 统一「越大越好」口径，前沿按 fitness 降序供人审）；② `symbolic_regression.py` 新增 `SymbolicRegressionSearcher` 确定性 beam-search 层级搜索（单字段出发逐层一元包装 + 二元组合，复用 `GPEvolver._evaluate_fitness` 同口径多目标评估，每层保留 top-K，固定种子可复现，`SymbolicRegressionConfig` max_depth/beam_width/max_candidates/min_fitness 配置化）；③ `GPEvolver.evolve()` multi_objective 模式跟踪全部已评估个体提取 Pareto 前沿，`GPEvolveResult` 新增 `pareto_front` 字段（含 source=gp/symbolic 标识），`GPEvolverConfig` 新增 `symbolic_regression_enabled`/`symbolic_max_depth`/`symbolic_beam_width`/`symbolic_max_candidates`（默认关闭，不改变默认行为）；新增 `test_pareto.py` 12 用例 + `test_symbolic_regression.py` 15 用例（含 GP 集成：symbolic 前沿合并、multi_objective 前沿输出）） |
| GAP-I205 | `fts/factor_engine/micro_evolution.py` | 微观演化效率：optuna 100 trials 固定串行，随机搜索无早停，低潜力候选浪费算力 | 每候选固定 100 trials 评估成本高 | Stage 1（v2.68.0） | ✅ 已关闭（v2.70.0：两阶段漏斗——`optimize_params_staged` 粗筛低 trials（默认 20）随机搜索快速打分，得分低于 `COARSE_IC_FLOOR`（0.02）直接淘汰（passed=False）；通过者进入精筛，trials 按粗筛得分自适应（得分达 `COARSE_REF_IC` 0.10 跑满 n_trials）+ TPE 早停（早停机制既有）；`evolve_micro` 新增 `use_staged` 参数，`EvolutionLoop` 接入并默认启用（`settings.py` 新增 `micro_staged_evolution`/`micro_coarse_trials`/`micro_coarse_ic_floor` 配置，FTS_MICRO_STAGED/FTS_MICRO_COARSE_TRIALS/FTS_MICRO_COARSE_IC_FLOOR 环境变量）；新增 `TestStagedFunnel` 5 用例（粗筛淘汰/精筛通过/no-optuna 回退/staged 与非 staged evolve_micro） |
| GAP-I206 | `fts/factor_engine/evolution_loop.py` + `factor_db/repository.py` | L2 准入去冗余/正交化闭环缺失：晋升仅相关性预检（标记不删除）+ 家族上限，正交化仅 L3 使用 | elite 池相关性膨胀 → 组合夏普稀释、换手成本非线性增长 | Stage 1（v2.71.0） | ✅ 已关闭（v2.71.0：`_check_elite_correlation` 演化因子晋升前与既有 elite 信号做 Pearson 相关，abs ≥ 阈值（默认 0.9）拒绝晋升；无既有 elite/执行失败/低相关放行；容量护栏 max_scan=50；种子因子（shadow_observe=False）跳过；`settings.py` 新增 `l2_elite_corr_threshold`/`l2_elite_corr_max_scan`/`l2_elite_corr_debug` 配置；新增 `test_l2_elite_redundancy.py` 10 用例。**正交化闭环补充**：`_orthogonalize_candidate` 高相关候选 OLS 残差质量合格（残差相关 <0.3 且保留比 >0.3）→ 正交化版本入库（`orthogonalized`/`orthogonalized_against`/`orthogonal_signal` 快照，DuckDB metadata 持久化），不合格拒绝兜底；配置 `l2_elite_orthogonalize`/`l2_orthogonal_residual_corr_max`/`l2_orthogonal_min_retained_ratio`；L3 `orthogonalize_factors` 对已正交化因子不重复剔除；新增 `test_l2_orthogonalize.py` 10 用例） |
| GAP-I302 | `fts/factor_engine/portfolio_optimizer.py` | 组合优化器机构化：无 Ledoit-Wolf 协方差收缩、无风险平价/均值方差（Elastic Net + Regime 为主） | 组合权重对协方差噪声敏感、无风险预算视角 | Stage 2（v2.74.0） | ✅ 已关闭（核心由 GAP-L302/L303/L304/L305 v2.61.0 落地：Ledoit-Wolf 收缩协方差 `risk_model.RiskModelEstimator` + 风险平价/均值方差 `PortfolioOptimizer`（risk_parity 迭代等风险贡献 / mean_variance SLSQP，含杠杆/集中度/换手/VaR/暴露中性化/容量约束）+ L3 主流程接线（`synthesize_signals` optimizer 模式 + `PortfolioLoop.optimizer_mode` + CLI `--optimizer-mode`）；**v2.74.0 补齐缺口③优化器参数走配置**：`FTSConfig.portfolio_optimizer_mode`（默认 risk_parity，FTS_PORTFOLIO_OPTIMIZER_MODE env）接入 `fts portfolio run`——CLI `--synthesis-mode` choices 增加 optimizer + `--optimizer-mode`（risk_parity/mvo）+ `--returns-matrix` CSV 加载并透传 `run(factor_returns=...)`；新增 `TestCmdPortfolioRunStock` 3 用例（模式/参数透传、配置默认值、returns-matrix 加载）+ test_config_settings 2 用例（默认值/env 覆盖）） |
| GAP-I305 | `fts/monitor/elite_tracker.py` + `fts/factor_engine/feedback_loop.py` | 因子衰减自动退役闭环：退役阈值与重校准为人工配置，未接实盘反馈自动闭环 | 衰减因子滞留组合拖累绩效 | Stage 2（v2.76.0，提前至 v2.72.1） | ✅ 已关闭（v2.72.1：滚动 6M IC 线性回归斜率 `_calc_ic_slope_6m`（归一化 [-1,1]）+ 衰减分级 `decay_grade`（normal/observe/retired，`observe_slope` 0.10 / `retire_slope` 0.20）写入快照，`auto_retire()` 纳入 `decay_grade=="retired"` 退役条件；`AutoRetireConfig`/`AutoRetireManager` 支持分级阈值且配置同步；`evolution_loop._run_periodic_factor_review` 接线 FeedbackLoop FACTOR_DECAY 事件（`last_feedback` 写回快照），退役受 `decay_auto_retire_enabled` 开关控制；`monthly_decay_eval_job` 按斜率迁移状态 + `retire_factor` 回写 DuckDB/JSON + 报告；`settings.py` 新增 `decay_observe_slope`/`decay_retire_slope`/`decay_slope_min_points`/`decay_auto_retire_enabled` 配置；新增 `test_orthogonal_basis.py` 19 用例含衰减分级/斜率/自动退役断言） |
| GAP-I402 | `fts/monitor/live_factor_monitor.py` | 在线因子性能监控：框架存在但无实盘因子表现数据源（依赖 GAP-I401） | 因子实盘漂移不可见 | Stage 2（v2.77.0） | ✅ 已关闭（v2.77.0：`ingest_live_ic` 接入 GAP-I401 实盘反馈数据源 + 衰减状态监控/告警 + Prometheus 指标） |

#### P2 — 机构级一般差距（扩展期）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-I103 | `fts/factor_engine/extractors/alternative_sources.py` | 知识源扩展（公告/舆情/宏观事件）：L1 仅研报/arXiv（与 I101 联动） | 候选维度单一 | Stage 2（v2.82.0） | ✅ 已关闭（v2.82.0：另类知识源多路——`AnnouncementNewsExtractor`（东方财富公告中心 API + LLM 提取股票事件/舆情因子）+ `MacroEventExtractor`（东方财富宏观日历 API + LLM 提取跨品种方向），股票管道接入公告+宏观两源、期货管道接入宏观源（`l1_announcement_extractor_enabled`/`l1_macro_extractor_enabled` 配置开关，默认 True，失败优雅降级返回空）；`BaseExtractorPipeline.extract` 多源并行收集（ThreadPoolExecutor，GAP-I101 二期）；新增 `test_alternative_sources.py` 16 用例） |
| GAP-I303 | `fts/factor_engine/portfolio_loop.py` | 组合层成本/换手约束显式化：有粘性约束但无显式换手成本目标项 | 高频调仓换手成本未被组合层显式优化 | Stage 3（v2.83.0） | ✅ 已关闭（v2.85.0：`apply_turnover_penalty`——组合目标函数显式换手惩罚项 λ（`w_new' = w_old + (w_new − w_old)/(1+λ)`，粘性约束后、归一化前收缩权重变动，λ=0 关闭、λ 越大换手越低、新因子不惩罚）；`build_combo`/`PortfolioLoop` 参数透传 + `FTSConfig.l3_turnover_penalty`（env `FTS_L3_TURNOVER_PENALTY` 默认 0.0 关闭）；新增 `test_turnover_penalty.py` 12 用例（含换手惩罚生效断言：惩罚后 Σ\|Δw\| 严格更小且 λ 单调递减），portfolio_loop 213 + 12 合计 225 passed 全绿） |
| GAP-I304 | `fts/factor_engine/style_classifier.py` | 风格暴露控制（Barra 风格体系）：无 10 风格回归中性化（= plans/22 GAP-S02） | 无法回答"因子赚风格钱还是 alpha 钱" | Stage 2 门槛（引用 GAP-S02） | ✅ 已处理（v2.62.0，GAP-S02 落地 10 风格暴露 + 评估链 style_exposures 参数）。**v2.79.0 补充全市场覆盖**：`evolution_loop.py` 新增 `_build_barra_exposures()` 自动构建 10 风格暴露（`BarraStyleEngine` + 结果缓存避免每因子重复计算，字段缺失风格自动跳过），`_evaluate_cross_section` 接入 `style_exposures`——L2 评估链行业中性化后自动叠加 Barra 风格回归残差（此前该能力仅存在于 `cross_section_evaluate_backtest` 参数层，L2 主流程未实际启用）；`settings.py` 新增 `l2_barra_style_neutral` 配置（默认 True，env `FTS_L2_BARRA_STYLE_NEUTRAL`）；新增 `tests/factor_engine/test_barra_l2_integration.py` 7 用例（非横截面降级/风格暴露构建/缓存复用/配置关闭/构建异常降级/仅 OHLCV 不崩溃/评估链接入 + spy 断言） |
| GAP-I502 | `fts/factor_engine/executor_backend.py` + `batch_mining.py` | 分布式扩展预留：全部单进程，无 ExecutorBackend 抽象（process/dask/ray） | Stage 3 吞吐再扩容无架构预留 | Stage 3（v2.83.0） | ✅ 已关闭（v2.83.0：`executor_backend.py` 可插拔执行器抽象——`ExecutorBackend`（map/shutdown + 上下文管理）+ `ThreadBackend`（默认）/`ProcessBackend`（cloudpickle 序列化，lambda/bound method 跨进程序列化）/`DaskBackend`/`RayBackend`（缺依赖自动降级 ProcessBackend）+ `create_executor_backend` 工厂；`BatchMiner.filter_batch` 批量粗筛接入后端（`BatchMiningConfig.executor_backend`/`executor_max_workers`），`FTSConfig` 新增 `executor_backend`（默认 thread，保持现状）`/executor_max_workers`（`FTS_EXECUTOR_BACKEND`/`FTS_EXECUTOR_MAX_WORKERS`）；新增 `test_executor_backend.py` 14 用例（四后端行为一致性/进程内 bound method 与 lambda/降级路径/未知回退/BatchMiner 接入 + process 单任务异常隔离），executor_backend 14 + batch_mining 11 合计 25 passed 全绿） |
| GAP-I503 | `fts/data_sources/aggregator.py` + `fts/factor_engine/microstructure_factors.py` | 数据深度：tick 历史仅 ~42 分钟（GAP-050），无 Level2 订单簿、无另类数据 | 微观结构 alpha 缺失 | Stage 3（v2.82.0 首期） | ✅ 首期已关闭（v2.84.0：① tick_cache 增量累积——去重写入（symbol+datetime）+ `tick_cache_retention_days` 保留清理（默认 7 天）+ `get_ticks`/`_try_tick_cache` 时间窗口查询（start_time/end_time），跨会话累积 tick 历史；② Level2 订单流因子——新建 `microstructure_factors.py`：`classify_tick_direction`/`order_flow_imbalance`（OFI）/`order_book_imbalance`（OBI 5 档深度）/`large_trade_ratio`（大单占比）+ `compute_microstructure_factors` 统一入口（FACTOR_COLUMNS 契约，缺列/不足 min_rows 降级空）；③ 新增 31 用例（microstructure 20 + tick_cache 11）+ 既有 tick/aggregator/migrate 125 passed 全绿）。二期（另类数据因子）按 Stage 3C 远期排期 |

### GAP-C 系列 — Stage 3C 远期机构级差距（细则：plans/23-institutional-transformation-plan.md §3.3）

> 承接总纲 23 号计划 §1 差距矩阵残留 🟡/🔴 单元格（Stage 3C 远期项）。C1/C2/C3/C5/C6/C7/C8 已于 2026-08-11 首期实施；C4 同日实施（单机 LocalCluster 代码/测试/吞吐基准落地），真实多机集群部署待硬件/基建条件成熟后按 DaskBackend 抽象接入（记录见 07-operations 与 plans/23 C4 实施设计）。

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| C1 | `fts/factor_engine/microstructure_generator.py` + `fts/factor_engine/evolution_loop.py`（2026-08-11 首期 + 评估晋升接线） | Level2 微观结构因子接入 L2 演化并入 elite | 微观结构 alpha 未进入 elite 池（Stage 3 退出标准②） | Stage 3C（2026-08-11 首期） | ✅ 已实施（首期：生成器 tick→日频聚合→FactorProgram + CLI micro-generate + 执行器 datetime 对齐通道 + 20 用例；**评估晋升接线 2026-08-11**：`run_microstructure_promotion`（横截面评估 ic≥0.03&sharpe≥1.5 → FactorAuditor 6 项审计 → `_promote_to_elite` 护栏）+ CLI micro-evaluate + 7 用例；真实评估待 tick 数据积累） |
| C2 | `fts/factor_engine/alternative_sentiment.py`（2026-08-11 首期 + LLM 精修） | 另类数据因子上线（舆情 NLP，公开新闻 API + 词典法） | 舆情/事件维度缺失（Stage 3 退出标准③） | Stage 3C（2026-08-11 首期） | ✅ 已实施（首期：FinancialSentimentLexicon 词典打分 + EastmoneyNewsProvider + 日聚合 sent_mean/sent_std/sent_chg + CLI senti-generate + 26 用例；**LLM 精修 2026-08-11**：`LlmSentimentScorer` + `evaluate_lexicon_consistency` 词典-LLM 一致性 ≥0.7 验收接口 + CLI senti-consistency；评估晋升接线对齐 C1 模式待数据积累） |
| C3 | `fts/factor_engine/black_litterman.py`（2026-08-11 新增） | ML 组合层：Black-Litterman 观点融合权重 | 组合层无观点融合类 ML 权重（Stage 3 退出标准④） | Stage 3C（2026-08-11 首期） | ✅ 已实施（BL 闭式/auto-views/CLI bl 模式 + 22 用例） |
| C4 | `fts/factor_engine/executor_backend.py`（GAP-I502 扩展） | 多节点分布式挖掘工厂（Dask 集群实战部署） | 单机吞吐上限（Stage 3 退出标准①） | Stage 3C（2026-08-11 首期） | ✅ 已实施（首期 2026-08-11：DaskBackend 增强——cluster 句柄注入/address 优先/worker_count 调度器视角诊断/kill_worker 故障注入/单任务异常隔离迭代器；`BatchMiner.filter_batch` executor_backend="dask" 接线；pyproject 新增 `distributed` extra 并入 all；`scripts/benchmark_executor.py` 吞吐基准（thread/process/dask LocalCluster 对比表）；17 用例全绿 + 既有执行器/batch_mining 回归通过；真实多机集群部署后置，按 DaskBackend(address="tcp://scheduler:8786") 接入） |
| C5 | `fts/ml/models.py` + `fts/ml/deep_factor.py`（2026-08-11 首期） | 深度模型：轻量 Transformer 因子（GAN 合成远期） | 缺深度时序特征维度 | Stage 3C（2026-08-11 首期：轻量 Transformer） | ✅ 已实施（纯 numpy 因果注意力/零未来函数 + L2 接线 + 21 用例；GAN 远期） |
| C6 | `fts/factor_engine/recalibration.py`（2026-08-11 新增） | 在线学习与自动重校准（实盘反馈驱动参数微调） | 无在线增量重校准（仅告警/退役） | Stage 3C（2026-08-11 首期） | ✅ 已实施（重校准队列/elite 回写/触发源接线 + 18 用例） |
| C7 | `fts/factor_engine/cost_model.py` + `scripts/calibrate_impact_cost.py`（2026-08-11） | 回测保真实证化（冲击成本实证标定 + 融资成本） | 冲击成本参数人工设定、无融资成本项 | Stage 3C（2026-08-11 首期） | ✅ 已实施（FTS_COST_* 配置化/融资成本/标定脚本 + 20 用例） |
| C8 | `fts/monitor/http_server.py` + `fts/factor_engine/expr_dsl/registry.py` + `fts/factor_engine/feature_ops.py`（2026-08-11 首期 + C9 扩容二期） | 基础设施深化（在线人审协作工作台 + 算子库扩容至数百） | 无 Web 工作台、算子规模距数百有差 | Stage 3C（2026-08-11 首期） | ✅ 已实施（人审工作台 /review + /api/review/* 复用 FactorReviewWorkflow + C8 22 算子双注册表扩容（DSL 102）+ operator_catalog 自动生成 + 浏览器冒烟走通；C8-2 2026-08-11 机审/人审可配置：ReviewMode（FTS_REVIEW_MODE 默认 auto）+ AutoReviewPolicy 三态判定（正常批准/低质驳回/异常转人审）+ auto_review 批量机审 reviewer=auto + CLI/Web 触发 + 28 用例；**C9 扩容二期 2026-08-11**：30 算子双注册表扩容（DSL 102→**132**、GP 81→**111**、verify consistent/mismatched 0）+ test_operator_expansion_c9 39 用例；5 个异常因子模拟审批 test_simulated_approval 9 用例；算子库距数百仍剩远期扩容量） |

### GAP-L 系列 — L3/L4 机构级追赶专项（细则：plans/24-l3-l4-institutional-plan.md）

> 承接总纲 GAP-I301~I305 / I401~I402 的 L3/L4 执行细则（GAP-L3xx 组合层 / GAP-L4xx 执行反馈与表达式算子层），登记 12 项执行级缺陷（P0×4 / P1×4 / P2×4）。A 阶段（GAP-L301/L302/L305）随 v2.61.0 启动，与总纲 Stage 1（v2.65.0~v2.72.0）排期对齐。

#### P0 — L3 组合层阻塞性差距

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-L301 | `fts/factor_engine/portfolio_loop.py` | 因子收益序列缺失：组合夏普=Σ(w·Sharpe)×多样性折扣、相关性=(1-diversity)×0.35+avg_sharpe×0.015 为经验公式，非因子收益矩阵 w×R 实测 | Verifier 校验估算值而非实测值；无 Σ 地基，机构化优化无从谈起 | A 阶段（v2.61.0，本批启动） | ✅ 已关闭（v2.61.0：factor_returns.py + build_combo 实测化 metrics_source） |
| GAP-L302 | `fts/factor_engine/portfolio_optimizer.py` | 风险模型与协方差收缩估计缺失：无 Ledoit-Wolf/结构化收缩，奇异协方差仅 εI jitter | 组合风险度量缺失，风险平价/最小方差无法落地 | A 阶段（v2.61.0，本批启动） | ✅ 已关闭（v2.61.0：risk_model.py 纯 numpy Ledoit-Wolf） |
| GAP-L303 | `fts/factor_engine/portfolio_loop.py` + `portfolio_optimizer.py` | PortfolioOptimizer 未接入 L3 主流程：`run()` 不传 returns_matrix，optimizer 模式恒回退 sharpe_weight（死代码） | 已实现的机构化优化器不可用 | B 阶段（v2.61.0，本批完成） | ✅ 已关闭（v2.61.0：run() 透传 factor_returns/exposure_matrix + optimizer_mode/config + CLI + 列对齐/收缩协方差/mvo 别名） |
| GAP-L304 | `fts/factor_engine/portfolio_optimizer.py` | 组合层无行业/市值中性化约束：约束仅杠杆/集中度/换手/VaR，无暴露矩阵输入 | 组合隐含行业/市值风格赌注（联动 GAP-S01） | B 阶段（v2.61.0，本批完成） | ✅ 已关闭（v2.61.0：OptimizerConfig.neutralization/exposure_tolerance + SLSQP 暴露约束 \|B'w−target\|≤tol） |

#### P1 — L3/L4 重要差距

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-L305 | `fts/factor_engine/cost_model.py` + `portfolio_optimizer.py` | 组合目标无成本/换手项：cost_model 无冲击成本函数，优化目标无 -cost(w) | 高换手/集中组合成本被忽视，回测收益虚高（承接 GAP-I501/I303） | A 阶段（v2.66.0） | ✅ 已关闭（v2.66.0：impact_cost 平方根冲击成本 + Optimizer 换手惩罚/成本项 + net 指标 + 容量约束） |
| GAP-L306 | `fts/factor_engine/walk_forward.py` + `portfolio_loop.py` | 组合层 Walk-Forward 缺失：walk_forward 仅因子级，组合权重无滚动样本外验证 | 组合权重可能对单段历史过拟合 | C 阶段（v2.70.0） | ✅ 已关闭（v2.66.0：portfolio_walk_forward.py 滚动窗口 + 一致性得分 + 报告接入） |
| GAP-L307 | `fts/factor_engine/risk_attributor.py` + `portfolio_loop.py` | 归因体系未接入 L3：RiskAttributor 为孤立模块，无因子/风格/行业归因输出 | 无法回答"组合赚的什么钱" | C 阶段（v2.69.0） | ✅ 已关闭（v2.66.0：RiskAttributor 权重方差分解接入 L3 归因报告） |
| GAP-L401 | `fts/factor_engine/expr_dsl/registry.py` + `operator_evolution.py` | L4 表达式组合算子层薄弱：仅 15 个基础算子，无双序列/横截面/条件算子 | 因子表达式复合能力弱、演化搜索空间受限 | D 阶段（v2.72.0） | ✅ 已关闭（v2.66.0：新增 regression_residual/quantile_bucket/cross_section_demean/if_else 4 算子 + operator_evolution 放开双序列；v2.71.0 补齐 corr/cross_section_rank 至 6 算子，对齐验收标准 ≥6） |

#### P2 — L3/L4 一般差距

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-L308 | `fts/factor_engine/portfolio_loop.py` + `regime_multipliers.py` | Regime 权重数据化缺失：REGIME_FAMILY/STYLE_MULTIPLIERS 为人工硬编码查表，无数据支撑 | 制度调权缺实证依据 | D 阶段（v2.72.0） | ✅ 已关闭（v2.67.1：RegimeMultiplierEstimator 数据驱动倍率 + _data/l3_regime_multipliers.yaml + load_data_driven_multipliers 优先接线） |
| GAP-L402 | `fts/factor_engine/feedback_loop.py` + `bridge/signal_bridge.py` | L4 实盘反馈闭环缺失：无 LiveFeedbackRecord 契约与回流通道（承接总纲 GAP-I401） | 因子状态仅基于历史回测 | D 阶段（v2.71.0） | ✅ 已关闭（v2.66.0：LiveFeedbackRecord 契约 + LiveFeedbackImporter 导入 + LiveVsBacktestICReport 对比 + CLI）。**数据源补全（D.1，v2.101.0）**：离真实账户前的 `position_return` 断点由模拟仓补齐——`fts/live_trade/simulated_portfolio.py`（`SimulatedPortfolio.apply_signal`/`mark_to_market`/`attribute_factor_returns` 生成 `LiveFeedbackRecord`）+ `simulated_engine.py`（历史回放 `SimulatedReplayEngine` + 实时纸面 `SimulatedPaperTrader`）+ `scripts/simulated_replay.py` 回放脚本，`--out` 落盘反馈记录供 `LiveFeedbackImporter` 导入驱动衰减判定；设计见 [D.1-simulated-portfolio-design.md](../../docs/harness/design/D.1-simulated-portfolio-design.md) |
| GAP-L309 | `fts/factor_engine/portfolio_loop.py` | 组合层数据规模扩展：ElasticNet 硬编码 50 只×120 天，统计功效有限 | 截面回归功效不足、与 MIN_EVAL_DAYS=500 不一致 | 扩展期 | ✅ 已关闭（v2.67.1：PanelLoadingConfig 默认全 CSI300×500 天 + 流动性分层抽样 + 覆盖/幸存者偏差日志） |
| GAP-L310 | `fts/factor_engine/seed_loader.py` | 种子加载链缺陷：① YAML 因子 `kind=FactorKind.*` 引用但 `FactorKind` 未导入（NameError → 期货种子 81/184 加载失败）；② 多行 `field_defs` 拼接进函数体时后续行无/残留缩进 → unexpected indent（analyst_revision/fundamental 等 38 处编译失败）；③ 测试断言引用已迁移函数 `_estimate_lookback`（seed_analyzer.estimate_lookback_static） | 种子因子批量加载失败/编译失败，种子库完整性验证失真；全量回归 21 例失败 | v2.68.0 | ✅ 已关闭（v2.68.0：L23 补 `FactorKind` 导入；`_fundamental_factor_from_yaml` 多行 field_defs strip+统一 4 空格缩进；test_seed_loader 改引 `estimate_lookback_static`；test_seed_pool/test_seed_loader 种子计数断言同步 714/898/30） |

---

## 3. 差距详情

### GAP-001: pipeline/ 和 strategies/ 模块无测试（已关闭）

- **解决方式**: 新增 `tests/pipeline/test_base.py`、`tests/pipeline/test_factor_combiner.py`、`tests/strategies/test_base_v2.py`
- **关闭时覆盖率**: pipeline/base.py 100%, factor_combiner.py 100%, base_v2.py 100%

### GAP-002: CLI/监控/调度无测试（已关闭）

- **解决方式**: 新增 `tests/test_cli.py`、`tests/test_monitor.py`、`tests/scheduler/test_tasks.py`
- **关闭时覆盖率**: cli.py 87%, monitor.py 100%, scheduler/tasks.py 100%

### GAP-003: micro_evolution.py 覆盖率低（已关闭）

- **解决方式**: 安装 evolution extra 后补充 optuna 分支测试
- **关闭时覆盖率**: micro_evolution.py 92%
- **当前覆盖率**: 100%（v1.3.0 工程测试：ImportError 路径、optuna 异常路径、零方差信号路径全覆盖）

### GAP-004: evaluation_chain.py mock 路径未覆盖（已关闭）

- **解决方式**: 通过 `tests/factor_engine/test_macro_evolution.py` 补充 LLM mock 场景
- **关闭时覆盖率**: evaluation_chain.py 96%
- **当前覆盖率**: 99%（仅余空白行，v1.3.0 工程测试改进）

### GAP-005: monitor 格式输出测试（已关闭）

- **解决方式**: 在 `tests/test_monitor.py` 中补充 format_status_report 输出测试

### GAP-006: core/enums 测试（已关闭）

- **解决方式**: 新增 `tests/core/test_enums.py`，覆盖所有枚举取值和序列化

### GAP-007: core/contracts 测试（已关闭）

- **解决方式**: 新增 `tests/core/test_contracts.py`，验证 re-export 路径

### GAP-008: Data-Core 迁移至 MCP/akshare（已关闭）

- **解决方式**: 数据源从 Data-Core 迁移至 MCP/akshare 腾讯/东方财富 API
- **关闭时覆盖率**: data.py 100%, data_mcp.py 100%

### GAP-009: 种子因子评估计入熔断计数器（已关闭）

- **问题描述**: 种子因子评估的失败计入 `evaluated`/`promoted` 计数器，导致大量种子因子失败拉高失败率，触发熔断（失败率 100% > 95%）
- **影响范围**: L2 演化循环无法正常启动，种子因子无法晋升
- **当前进展**: 已修复 — 种子评估跳过 Verifier，仅用简单 IC/Sharpe 筛选，且不计入熔断计数器
- **验证结果**: 1325 测试全绿，L2 演化成功运行 20 代，种子因子正常晋升 elite

### GAP-010: 晋级计划文档未同步（已关闭）

- **问题描述**: `09-advancement-plan.md` 未从 v0.3.0 同步至当前 v1.1.0
- **影响范围**: 文档与实际项目状态脱节，里程碑记录不完整
- **解决方式**: 已同步更新至 v1.1.0，新增 v1.2.0 里程碑（种子因子集成、熔断修复、纯多头回测）

### GAP-011: 流程文档缺失（已关闭）

- **问题描述**: `execution_modes_flowchart.md`（执行模式流程图）和 `business_flow.md`（业务流程图）均未创建
- **影响范围**: 新成员无法快速理解系统执行流程，跨模块调试时缺乏全局视图
- **解决方式**: 已创建 `execution_modes_flowchart.md`（CLI/Scheduler/Monitor 三种执行模式）和 `business_flow.md`（L0→L1→L2→L3→交易信号全景业务流）
- **验证结果**: 文档结构完整，包含 ASCII 流程图和模块映射，与 `01-architecture.md` 架构定义一致

### GAP-012: 角色职责文档缺失（已关闭）

- **问题描述**: `agents/` 目录不存在，未定义各 Agent 的职责边界和能力范围
- **影响范围**: 多 Agent 协作时职责不清，可能导致越界操作
- **解决方式**: 已创建 `agents/fts-agent.md`，定义 FTS Agent 的身份、职责边界（7 项职责 + 禁止越界规则）、能力范围（因子引擎/种子因子/数据适配/CLI/调度/监控/文档）和与 FDT/Data-Core 的协作边界
- **验证结果**: 职责边界清晰，禁止越界规则明确，与 `01-architecture.md` 中的角色边界定义一致

### GAP-013: 生产就绪计划缺失（已关闭）

- **问题描述**: `plans/production_plan.md` 未创建，生产部署、监控告警、容器化、CI/CD 等方案未文档化
- **影响范围**: 生产环境部署缺乏标准化流程，运维风险高
- **解决方式**: 已创建 `plans/production_plan.md`，包含生产就绪检查清单（基础设施/监控告警/稳定性/测试/回滚/安全 6 大类 30 项）、容器化方案（Dockerfile + docker-compose）、CI/CD 流水线、监控告警配置（健康检查/Elite 因子追踪/磁盘监控/进程守护）、生产回滚方案和 FTS 生产运营 SLO
- **验证结果**: 检查清单完整，容器化方案可执行，SLO 指标已量化，与 `07-operations.md` 运维策略一致

### GAP-014: 文档一致性检查脚本缺失（已关闭）

- **问题描述**: `scripts/verify_doc_consistency.py` 不存在，无法自动校验代码与文档的映射关系
- **影响范围**: 文档与代码容易脱节，Harness 规范第 13 项检查无法自动化执行
- **解决方式**: 已创建 `scripts/verify_doc_consistency.py`，实现一致性元数据表格检查（`## 一致性元数据` 标题、字段完整性、版本号/日期声明）、代码文件存在性检查（验证文档中引用的 `File` 字段对应的文件是否存在）、断言可执行性检查（验证断言字段是否可解析）、以及 `docs/harness/` 目录批量扫描功能
- **验证结果**: 脚本可独立运行，支持 `--fix` 自动修复模式，与 `07-operations.md` 的文档评审流程一致

### GAP-015: 期货数据接入缺失（已关闭）

- **问题描述**: FTS 仅支持 A 股/ETF 因子演化，无法获取期货连续合约数据，无法实现期货横截面因子演化（跨品种因子、跨商品动量、品种间强弱等）
- **影响范围**: 策略覆盖范围受限，期货市场无法纳入因子演化体系
- **解决方式**: 
  - 新增 `fts/data_futures.py` — FuturesDataProvider 类，基于 DuckDB kline_cache 表提供期货连续合约 OHLCV 数据
  - 数据源 3 级降级：DuckDB kline_cache → AKShare 即时获取 → 合成数据
  - 集成到 `fts/data.py` FTSDataProvider（get_futures_ohlcv / get_futures_panel）
  - CLI 扩展 `--universe futures` 支持期货横截面因子演化
  - 新增 `scripts/download_futures.py` 断点续传下载脚本
  - 定义 82 个期货品种（25 核心 + 57 全量），覆盖大商所/郑商所/上期所/能源中心/中金所/广期所
  - 期货特有字段：hold（持仓量）、settle（结算价）
  - 期货无 pe_ttm/pb 等基本面字段，enrich_futures_fundamental 返回空
- **验证结果**: FuturesDataProvider 可正常读取 DuckDB 数据，支持 AKShare 降级获取，合成数据确保系统可运行

### GAP-016: 期货全链路集成测试缺失（已关闭）

- **问题描述**: 期货全量种子因子库（12 大因子家族 50+ 子因子）、期货因子演化脚本、期货信号管道、期货组合策略、L3 组合构建均已实现，但缺少集成测试验证期货全链路端到端运行
- **影响范围**: 期货演化 → 信号管道 → 组合构建的全链路缺少自动化回归测试
- **解决方式**: 
  - 新增 `tests/factor_engine/test_seed_pool.py` 中验证期货种子因子加载正确性（含 seed_data_futures_full.py 12 家族）
  - 通过 `scripts/run_futures_evolution.py` 手动验证期货因子演化全链路
  - 通过 `scripts/futures_signal_pipeline.py` 和 `scripts/futures_strategy.py` 验证信号管道正确性
  - 通过 `scripts/futures_l3_portfolio.py` 验证顶级因子组合构建
- **验证结果**: 期货种子因子加载测试通过，演化脚本可正常执行，信号管道输出正确的横截面信号报告

### GAP-017: 因子泛化优化 — 盲测品种池 + 单品种 IC 追踪 + 品种级权重分配（已关闭）

- **问题描述**: 期货因子演化仅在 25 个核心品种上训练，但信号管道应用到全量 76 个商品品种，缺乏以下验证机制：
  1. 盲测品种池缺失：无法验证因子在未见过的品种上是否有效
  2. 单品种 IC 追踪缺失：不知道每个因子在哪些品种上有效、哪些失效
  3. 品种级权重分配缺失：Ridge 回归在全品种聚合上学习权重，无法区分每个品种的因子有效性差异
- **影响范围**: 因子泛化能力无法验证，信号质量受限于全局聚合权重
- **解决方式**:
  - `fts/data_futures.py` — 新增 `FUTURES_HOLDOUT` 盲测品种池（6 个品种，覆盖各产业链）
  - `fts/scheduler/jobs.py` — L2 演化训练排除盲测品种
  - `scripts/futures_signal_pipeline.py`:
    - 新增 `_compute_holdout_validation()` 盲测验证报告
    - 新增 `_compute_per_variety_ic_matrix()` 品种-因子 IC 矩阵
    - 新增 `_compute_per_variety_weights()` 品种级权重分配
    - 修改 `_compute_composite_scores()` 支持品种级权重参数
    - 报告新增「品种-因子有效性矩阵 (IC)」章节
- **验证结果**: 管道正常运行，盲测 IC vs 训练 IC 对比输出，品种-因子 IC 矩阵输出到报告，品种级权重 vs 全局权重排名一致性可对比

### GAP-018: 品种分层训练缺失（已关闭）

- **问题描述**: 期货因子演化仅在 25 个按流动性选取的核心品种上训练，未按产业链分类确保训练集覆盖所有类别，化工等品种偏少，可能导致因子过拟合到某类品种的特异性规律
- **影响范围**: 训练集品类偏斜，因子泛化能力受限
- **解决方式**:
  - `fts/data_futures.py` — 新增 `FUTURES_SECTOR_MAP` 产业链分类映射（7 类）、`FUTURES_STRATIFIED_SUBSET` 分层训练品种集（19 个品种，覆盖 7 大产业链）
  - `fts/scheduler/jobs.py` — L2 演化循环使用分层训练集（排除盲测品种），输出品种数量日志
- **验证结果**: 分层训练集覆盖 7 大产业链，L2 演化正确使用分层训练集

### GAP-019: 精英因子全量重验证缺失（已关闭）

- **问题描述**: 因子晋级精英池后只在 25 个品种上验证过，环境变化后不再重新评估，无法检测因子退化
- **影响范围**: 退化因子持续参与信号合成，降低信号质量
- **解决方式**:
  - `scripts/futures_factor_revalidation.py` — 新建重验证脚本，支持自动降级退化因子
- **验证结果**: 首次运行验证通过：18 个因子，2 个自动降级（fut_basis_momentum_g1, fut_basis_momentum），2 个警告

### GAP-020: 种子因子硬编码导致文件膨胀与修改困难（已关闭）

- **问题描述**: 563 个种子因子（9 内置 + 81 期货 + 473 外部）以 Python 代码字符串形式硬编码在多个 .py 文件中，`seed_data_futures_full.py` 单文件超 2000 行，修改需手动编辑 Python，新增因子需理解代码模板
- **影响范围**: 文件膨胀严重、修改风险高、非开发者无法贡献、测试困难、与代码版本耦合
- **解决方式**: Phase 1 — 将种子因子迁移到 19 个 YAML 数据文件，实现数据驱动加载（`fts/seed_data/`）
- **验证结果**: 563 种子因子全部通过 YAML 加载，原有 Python 加载路径保持向后兼容
- **关联**: `plans/factor-management-optimization-plan.md` Phase 1

### GAP-021: Elite 因子 JSON 存储无法支持大规模因子管理（已关闭）

- **问题描述**: 300+ Elite 因子以单文件 JSON 存储在 `memory/knowledge/factors/elite/`，全量加载无索引，无法按 family/source/sharpe 等条件筛选，代码去重不支持，无版本历史和演化谱系
- **影响范围**: 查询性能差（O(n) 全量遍历）、去重逻辑弱、无法追溯因子演化、扩展规模受限
- **解决方式**: Phase 2 — 实现 FactorRepository（`fts/factor_db/repository.py`），迁移 680 因子到 DuckDB 4 张表（factor_metadata/factor_versions/factor_correlations/factor_evaluations），支持 SQL 查询、代码哈希去重、版本历史追踪
- **验证结果**: 680 因子迁移完成，回测引擎兼容性验证通过（加载/执行/筛选/搜索 100% 通过）
- **关联**: `plans/factor-management-optimization-plan.md` Phase 2

### GAP-022: 因子演化无版本历史与谱系追踪（已关闭）

- **问题描述**: 因子从种子到精英的完整演化过程（突变/交叉/变异）无版本记录，无法追溯因子来源和迭代路径
- **影响范围**: 演化过程不可审计，无法理解因子谱系和迭代逻辑
- **解决方式**: Phase 2 — 新增 `factor_versions` 表，记录每次因子变更的 generation/change_type/parent_id，版本管理 API 已实现
- **验证结果**: 版本表创建成功，版本追踪 API 通过测试
- **关联**: `plans/factor-management-optimization-plan.md` Phase 2

### GAP-023: 因子管理无数据血缘审计能力（已关闭）

- **问题描述**: 因子的评估历史、使用记录、信号贡献无法查询，缺乏数据血缘追踪
- **影响范围**: 因子质量退化无法追溯，组合决策缺乏历史依据
- **解决方式**: Phase 3 — 通过 DuckDB 事务日志 + 版本历史实现因子数据血缘
- **验证结果**: 实现 `FactorLineage`（演化谱系查询/评估趋势分析/质量退化检测/批量血缘审计）+ `FailureClassifier`（10 种失败模式自动识别 + 改善建议生成）；新增 57 个测试用例全部通过
- **关联**: `plans/factor-management-optimization-plan.md` Phase 3

### GAP-024: 因子相关性无法系统性评估（已关闭）

- **问题描述**: 因子间相关性只能通过手动计算，无法批量评估因子对的 Pearson/Spearman 相关系数，组合构建时缺乏去冗余依据
- **影响范围**: 组合中可能存在高度相关因子，导致风险集中和收益回撤
- **解决方式**: 
  - 新增 `factor_correlations` 表存储因子间相关性
  - 实现批量相关性计算脚本（`scripts/_generate_correlations.py`）
  - 为因子元数据自动关联最大相关系数和高相关因子列表
- **验证结果**: 4950 条相关性记录（100 因子 × 两两组合），Pearson + Spearman 双指标，元数据更新完成

### GAP-025: 孤立模块集成签名不匹配（已关闭）

- **问题描述**: 6 个孤立模块已接入 EvolutionLoop 审查流水线，但集成层按假设 API 调用（`run(factor_id=...)`），与模块真实签名（`run(factor, data, forward_returns)`）不符，运行期全部落入 except → 默认放行，审查门禁未生效
- **影响范围**: 伪相关/事件敏感/不鲁棒因子可绕过审查直接晋升精英池
- **解决方式**:
  - 修正 6 处集成调用点（AblationExperiment.run / CausalValidator.validate / RobustnessTester.run / ShapAnalyzer.analyze 改为 `(factor, data, forward_returns)`；FeatureImportanceAnalyzer.analyze 改为 `(factor_series, data, target_col)`；LogicMonitor 改用 `run(factor, data, switch_dates)` 从 elite 快照加载因子程序）
  - 落地 4 个审查门禁 passed 判定（消融 IC 降幅超基线 50% / 因果 n_anomalous>0 / 鲁棒性总体通过率≥90% / SHAP 恒通过）
  - 修正测试 mock 构造为真实签名，新增门禁判定测试
- **验证结果**: 109 项 evolution_loop 测试全绿（含 17 项定向集成测试）

### GAP-026: GP 引擎算子命名与 DSL 未对齐（已关闭）

- **问题描述**: GP 引擎算子命名（`delta`/`pct_change`/`scale`）与 FTS-Expr DSL（`ts_delta`/`ts_pct_change`）未对齐
- **影响范围**: 算子因子与代码因子的算子语义无法直接映射，GP 产物暂为 CODE 类型
- **解决方式**: v2.10.0 新增 `fts/factor_engine/operator_evolution.py`（`OperatorEvolutionEngine`）——进化搜索直接在 DSL 算子空间进行（58 算子 L0-L5，命名即 DSL 命名），产物为 `kind=OPERATOR` 因子，无需 GP 算子命名映射；GP 引擎维持 feature_ops 路径不变（双路径并存，各司其职）

### GAP-027: `code: str | None` 可选化审计（已关闭）

- **问题描述**: `FactorProgram.code` 全字段可选化（`code: str | None`）需先审计全部 `factor["code"]` 读取点
- **影响范围**: 算子因子可不需要 Python 代码，`code` 保持必填会限制算子因子形态
- **解决方式**: v2.14.0 完成全量审计——`contracts.py` 中 `FactorProgram.code` 改为 `Optional[str]`；`factor_program.py` 中 `validate_factor_code`/`_validate`/`compile` 三处增加 `None` 处理（算子因子跳过代码验证和编译，走 `expression` 快速路径）；持久化/评估链/Verifier/组合构建全部读取点兼容 `None`

### GAP-028: 既有失败测试文件修复（已关闭）

- **问题描述**: 多个测试文件与当前实现不匹配，导致全量回归无法一键全绿：`test_data_cli.py` 断言 `_cmd_data_*` 旧接口、`test_tasks.py` 任务数断言过期、`test_hotswap.py` 依赖 watchdog、`test_engine.py` MagicMock 断言、`test_shap_analyzer.py` 依赖 shap、`test_factor_lineage.py` 触发 DuckDB ART 索引 bug、`test_data_source_metrics.py` 缺 `_metrics_cache`
- **影响范围**: 全量回归需排除这些文件，无法一键全绿验证
- **解决方式**: v2.14.0 统一修复——删除 `test_data_cli.py`（data 子命令已移除）；更新 `test_tasks.py` 任务数/描述断言；`test_hotswap.py`/`test_shap_analyzer.py` 加 `pytest.importorskip` 跳过可选依赖缺失；`test_engine.py` 改用 `==` 替代 `is`；`test_factor_lineage.py` 用临时 DuckDB 隔离；`test_data_source_metrics.py` 补 `_metrics_cache` 模块级变量；`test_coverage_edge_cases.py`/`test_evolution_loop.py`/`test_evaluation_parallel.py` 修复 mock 签名和数据量；`test_ablation.py` xfail 标记已知局限；`test_alpha_ops_numba.py` 加 `importorskip("numba")`；`test_backtest_pipeline.py` 更新无效代码断言；`test_contracts.py`/`test_enums.py` 同步新枚举和契约
- **验证结果**: 全量回归 2928+ 用例通过，无排除文件

### GAP-029: L3 组合漂移治理（已关闭）

- **问题描述**: L3 组合每日全量重建且无漂移度量、无粘性约束、无 L2 晋升节奏控制
- **影响范围**: 组合更换频率不可监控，存在策略漂移风险
- **解决方式**: v2.11.0 实现漂移治理——组合漂移度量（成员更换率/权重 L1 范数变化/Hellinger 距离）、粘性约束（单边更换率 ≤30%）、L2 晋升节奏控制（晋升后 5 个交易日方可进入组合）；v2.72.1 GAP-F13 补全漂移告警闭环——`DriftAlertConfig` 阈值可配置（`overlap_threshold` 0.50 / `weight_l1_threshold` 0.40）+ `check_and_alert` 超阈值告警（Prometheus 兼容指标 `METRIC drift_alert`）+ `generate_rebalance_proposal` 粘性重平衡建议（`trigger_rebalance` 可配置，附加 `source=drift_monitor` proposal 注入 FDT）
- **验证结果**: 组合漂移可量化监控，粘性约束生效，晋升节奏可控；超阈值告警触发 + 重平衡建议注入均有测试覆盖（TestDriftMonitorAlert 7 + run 集成 2）

### GAP-030: evolution_loop 集成测试污染真实 catalog（v2.14.0 关闭）

- **问题描述**: 6 个 evolution_loop 集成测试（promote_to_elite/failure_rate_circuit_breaker/low_ic_increment/consecutive_low_ic_reset/periodic_review）本地运行失败，且根因叠加：`EvolutionLoop._get_repo()` 硬编码真实 `FactorRepository()`（DATABASE_PATH），任何调用 `run()` 的集成测试都会写入真实 `data/factor_catalog.duckdb`——每次全量回归新增约 44 条重复 seed 记录（`fut_bias`/`fut_hf_trade_imbalance`/`fut_hf_historical_return`/`fut_option_pcr`），catalog 中 `fut_option_pcr` 累计 267 条重复
- **影响范围**: catalog 被测试持续污染（与种子 ID 随机化叠加）；测试无法在本地稳定运行；全量回归无干净基线
- **解决方式**（v2.14.0）:
  1. `EvolutionLoop.__init__` 新增 `factor_db_path` 注入点，`_get_repo()` 使用之——测试可显式指向临时 DuckDB
  2. `test_evolution_loop.py` 全部 `run()` 集成测试注入 `factor_db_path=tmp_path`（隔离库）
  3. 一次性清理 catalog 重复 seed 记录（保留每 name 最早一条 + 快照引用保护）
- **验证结果**: 隔离后 run() 测试不再写入真实 catalog；catalog 重复 seed 记录清理至每 name 一条
- **当前进展**: 已关闭（v2.14.0）

### GAP-031: L1 注入候选未接入 L2 演化（已关闭）

- **问题描述**: `SeedPool.inject_from_l1`/`list_injected_l1` 接口存在但全库无调用方（死代码）；meta_loop `_inject_candidate` 只写 `l1_injected/` + `factor_pool.json`，从未调用注入接口；`_list_base_seeds` 主动过滤 `l1:` 前缀导致 L2 读取不到；`inject_from_l1` 仅写内存缓存不落盘，L1/L2 跨进程天然失效
- **影响范围**: L1 花 LLM token 生成的候选成为"孤儿数据"：不进 L2 演化、不走评估链/晋升，仅被 L1 自身用于去重
- **解决方式**: v2.14.0 完成 L1-L2 注入链路接入——
  1. `meta_loop.py` `_inject_candidate` 新增 `self.seed_pool.inject_from_l1(cand, trace_id)` 调用，注入到 SeedPool 内存缓存
  2. `evolution_loop.py` `run` 方法新增 `_merge_l1_candidates` 调用，合并 L1 注入候选到种子列表（pending 门控 + market 过滤 + 去重），与种子同等参与相关性预检与种子评估晋升
  3. `_list_base_seeds` 过滤逻辑保持不变（`l1_injected/` JSON 持久化确保跨进程可用）
- **验证结果**: L1 候选通过 `_merge_l1_candidates` 正常进入 L2 演化流程，不走种子强制评估路径，与种子同等参与相关性预检与晋升

### GAP-032: 演化产物未同步 DuckDB factor_catalog（处理中 → v2.13.0 关闭）

- **问题描述**: elite 快照 522 个因子的 factor_id 不在 `data/factor_catalog.duckdb` 中。探查根因：102 个唯一 name 中 101 个在 catalog 已有同 name 主记录（**ID 分叉**，快照 `fct_哈希` ≠ catalog `fct_哈希`），其中 515 个为同名多 ID 重复副本（95 个 name）；仅 1 个真缺失（`fut_mobile_big_data_g5`，macro_evolution 产物）。链路缺口：`_promote_to_elite` 先写 JSON 快照后写 DuckDB，DuckDB 写入失败被 `_write_to_duckdb` 内部吞异常 → 产生"快照有、catalog 无"孤儿
- **影响范围**: `factor list`/`backtest batch` 的 DuckDB 查询模式读不到未入库产物；catalog 统计（1945 行）与 elite 实际快照不一致；重复快照使 elite 目录与 catalog 口径混乱
- **解决方式**（v2.13.0）:
  1. 代码：`_write_to_duckdb` 改为返回 bool（失败不再吞异常）；`_promote_to_elite` 严格一致——DuckDB（主存储）写入失败回滚已写 JSON 快照并判定晋升失败（返回 None），杜绝孤儿快照
  2. 数据：一次性修复——补入真缺失演化产物 `fut_mobile_big_data_g5`；515 个同名重复快照归档至 `elite/_archive/` 与 `futures_elite/_archive/`（catalog 主记录不受影响，可恢复）
- **验证结果**: 新增双写原子化测试全绿；数据一致性复查（快照 factor_id 全部可映射至 catalog name）；elite 目录无残留同名重复快照
- **当前进展**: 已关闭（v2.13.0）

### GAP-033: GP 演化数据泄露与 IC 衰减硬编码（v2.15.0 关闭）

- **问题描述**: 两个 P0 缺陷：
  1. **数据泄露**：`GPEvolver` 和 `OperatorEvolutionEngine` 在适应度评估中使用全量数据（训练+测试），导致 GP 搜索时 OOS 数据被"看到"，IC 被系统性高估（部分因子 IC 达 0.5+，市场实际不可能）
  2. **IC 衰减硬编码**：`BacktestMetrics.decay_6m` 字段被硬编码为 `0.05`，未基于实际回测结果计算，IC 衰减监控完全失效
- **影响范围**: 因子实际 IC 远低于回测值→组合 Sharpe 虚高→实盘/盲测表现大幅低于预期；IC 衰减无感知→无法识别因子退化
- **解决方式**（v2.15.0）:
  - 数据泄露修复：
    1. `GPEvolver.__init__` 新增 `train_mask` 参数，`_evaluate_fitness` 仅使用训练集计算适应度
    2. `OperatorEvolutionEngine.__init__` 新增 `train_mask` 参数，`_evaluate_fitness` 仅使用训练集
    3. `FeatureOpsEngine.run_gp_search` 透传 `train_mask` 到 `GPEvolver`
    4. `evolution_loop.py` 中 `_run_gp_evolution` 和 `_try_operator_engine_evolution` 构建训练掩码（OOS 30%，与 evaluation_chain 默认一致）
  - IC 衰减修复：
    1. `BacktestMetrics` 新增 `decay_6m` 字段（替代原有硬编码默认值）
    2. `evaluation_chain.py` 新增 `_compute_decay_6m()`——滑动窗口（4 窗口）IC 线性回归斜率，归一化到 [-1.0, 1.0]，负值表示衰减
    3. `evaluate_backtest` 和 `cross_section_evaluate_backtest` 返回结果中包含 `decay_6m`
- **验证结果**: `test_gp_evolver.py` 全量 151 测试通过；`test_evolution_loop.py` 128 测试通过；`test_evaluation_chain.py` 合规；`test_operator_evolution.py` 合规
- **当前进展**: 已关闭（v2.15.0）

### GAP-034: 因子相关性缺乏系统聚类（P1，已关闭）

- **问题描述**: L3 组合构建中 ACTIVE_FACTOR_CAP=20 仅按 Sharpe 排序做简单截断，无法区分"高 Sharpe 高相关"和"低 Sharpe 独立信号"因子。高度相关的冗余因子可能占据多个名额，挤走具有独立信号价值的低 Sharpe 因子，导致组合多样性下降。
- **影响范围**: 组合因子多样性不足，独立信号被相关冗余因子挤出，组合夏普和风险分散效果受限
- **解决方式**: v2.36.0 新增 `FactorClusteringEngine` 实现 P1 因子聚类：
  - 信号相关性计算：使用 FactorExecutor 在参考品种上计算每个因子的信号序列
  - Pearson 相关系数矩阵构建
  - 层次聚类（average linkage，距离阈值 0.7）
  - 从每个簇中选择 Sharpe 最高的代表因子
  - 集成到 L3 PortfolioLoop 的 Step 1.8
- **验证结果**: 因子聚类模块全量测试通过，portfolio_loop 集成测试通过

### GAP-035: 因子信号矩阵缺乏 PCA 降维（P2，已关闭）

- **问题描述**: Elastic Net 在因子数较多时仍可能达到 20 因子上限，无法通过正交主成分进一步压缩信号源维度，组合复杂度大，换手率成本非线性增长
- **影响范围**: 信号源维度高，组合复杂度大，换手率成本不受控
- **解决方式**: v2.36.0 新增 `PCASignalCompressor` 实现 P2 PCA 降维：
  - 信号矩阵构建：计算每个因子在参考品种上的信号序列
  - z-score 标准化
  - PCA 拟合，保留解释 95% 方差的主成分（最多 10 个）
  - 通过载荷矩阵将主成分映射回因子权重
  - 集成到 L3 PortfolioLoop 的 Step 1.9（可选，通过 enable_pca 控制）
- **验证结果**: PCA 降维模块全量测试通过，portfolio_loop 集成测试通过

### GAP-036: L1 注入候选文件积累（P2，已关闭）

- **问题描述**: 元学习循环（L1 Meta Loop）生成的候选因子写入 `memory/knowledge/factors/l1_injected/` 目录后，消费（被 L2 演化合并）或晋升精英后均未删除对应的 JSON 文件，导致该目录累积 518 个历史文件。这些文件占用了大量磁盘空间（~5MB），且使目录扫描效率下降。
- **影响范围**: l1_injected 目录 518 个 JSON 文件中，大部分为已消费（factor_pool.json 中 status≠pending）文件，持续堆积。历史文件干扰后续 L1 候选的目录扫描，降低处理效率，且无法直观区分"待处理"与"已处理"文件。
- **解决方式**: v2.38.0 实施激进清理方案，在 `fts/factor_engine/evolution_loop.py` 中三处修改：
  1. **消费后立即删除**（`_merge_l1_candidates` 方法，第 1657-1666 行）：合并 L1 候选到种子列表后，立即删除对应的 l1_injected JSON 文件。非阻塞：删除失败仅记录 warning，不影响合并流程。
  2. **晋升后立即删除**（`_promote_to_elite` 方法，第 1186-1200 行）：L1 候选因子晋升精英后，立即删除对应的 l1_injected 文件。通过 `factor["source"] == "bootstrapping"` + `factor["parent_id"]` 匹配候选文件。非阻塞：删除失败不影响晋升。
  3. **历史遗留一次性清理**（`_merge_l1_candidates` 方法开头，第 1571-1587 行）：在合并 L1 候选前，扫描所有 l1_injected 文件，对比 `factor_pool.json` 中已消费（status≠pending）的 candidate_id，删除匹配的遗留文件。一次性清理后不再产生新堆积。
- **验证结果**: 激进清理逻辑已集成到 `_merge_l1_candidates` 和 `_promote_to_elite` 中，非阻塞设计确保删除失败不影响核心流程。历史遗留清理幂等，仅对已消费文件生效。无新增测试，但现有 2086+ 测试全部通过。

### GAP-037: 深度学习模型与强化学习未实现（P2，已关闭）

- **问题描述**: FTS 本次升级（v2.38.0，Phase 24）仅落地了传统 ML 模型层（LightGBM/XGBoost/Ensemble），未实现两类更高级的模型：
  1. **深度学习时序模型**: LSTM/GRU/Transformer 等端到端深度时序预测模型，需引入 PyTorch/TensorFlow 重依赖
  2. **强化学习（RL）**: DQN/PPO/SAC 等基于环境交互的序列决策优化，需引入 gym 式环境 + RL 算法库
- **影响范围**: 无法利用深度时序特征提取能力与序列决策优化，信号合成停留在浅层模型
- **解决方式**: v2.60.0 GAP-F05 纯 numpy MLP 因子模型（缺依赖优雅降级）；v2.73.0 GAP-I203 GRUFactorModel 纯 numpy 单层 GRU + DeepFactorGenerator + L2 漏斗接线（test_gru_factor 28 用例）；RL（DQN/PPO/SAC）依赖 gym 环境重依赖、可解释性低，登记为远期研究项不阻塞（见 plans/21 GAP-F05）
- **当前进展**: 已关闭（v2.73.0）

### GAP-038: 种子相关性预检横截面模式卡死演化（P2，已关闭）

- **问题描述**: 期货横截面模式下，`_run_seed_correlation_check` 调用 `compute_cross_section_correlations`（`fts/factor_engine/seed_pool.py`）对 184 个种子因子 × 25 个品种 × 500 个交易日构建信号矩阵并计算两两截面 Spearman 相关（16,836 对），单因子执行约 3 秒，全程预计 >10 分钟。首次尝试用 `ThreadPoolExecutor(timeout=300)` 添加 5 分钟超时保护无效——线程卡在 numpy/scipy C 扩展中无法被 `future.result(timeout)` 中断，演化进程仍以 CPU 0%、无日志输出状态持续卡死。
- **影响范围**: 夜间 L2 因子演化流程无法完成；进程长时间无响应，需人工 kill；演化结果不可达。
- **解决方式**: v2.39.0 在 `_run_seed_correlation_check`（`fts/factor_engine/evolution_loop.py` 第 1709-1715 行）增加规模保护：横截面模式且种子数 >50 时直接跳过相关性预检。设计依据：
  1. 相关性预检仅做"标记不删除"（轻量扫描），跳过不影响种子评估与晋升主流程；
  2. 冗余因子控制已由 L3 组合层承担：`ACTIVE_FACTOR_CAP=20` 按 Sharpe 排名截断 + Elastic Net 截面回归自动变量选择（v2.35.0）+ 因子聚类（v2.36.0）；
  3. 时序模式（股票/单品种）不受影响，种子数 ≤50 的横截面场景仍执行预检。
- **验证结果**: 跳过保护生效后演化流程正常进入种子评估与演化循环（13 代后因失败率 100% 熔断，属预期保护机制）；结果记录于 `memory/logs/evolution/2026-08-08.log`。无新增测试（跳过分支为防御性保护，不改变正常路径行为）。

### GAP-039: 全量回归失败项（67 failed + 16 errors）（P2，已关闭）

- **问题描述**: v2.39.0 基线全量回归（`pytest tests/ -q -o addopts="" --continue-on-collection-errors`）结果为 2841 passed / 67 failed / 10 skipped / 16 errors。失败来源分两类：
  1. **预存断言过期**（GAP-028 同类）：`test_data_cli.py`（data 子命令已移除）、`test_scheduler/test_tasks.py`（任务数断言过期）、`test_scheduler/test_sync_futures_task.py`（`sync_futures_data_job` 已从 jobs.py 移除，收集失败）、`test_monitor/test_data_source_metrics.py`（`_metrics_cache` 缺失）、`test_elite_tracker.py`、`test_alpha_ops_numba.py`（numba 环境）、`test_ablation.py`（已知局限）、`test_evolution_loop.py` 两个 run() 集成用例（GAP-030，已改为跳过标记）、`test_data.py`（真实数据依赖）、`test_stage5_risk_live.py`（信号提交 500）
  2. **并行 v2.38.0+ 工作区改动引入**（未提交）：`test_http_server.py`（dashboard `_build_factor_list_from_duckdb` 新逻辑与 MagicMock 断言不符）、`test_seed_pool.py`/`test_seed_loader.py`（seed 加载路径变化）、`test_risk_tag.py`（质量卡评分 C 级淘汰 IC=0.06/0.09 因子）、`test_contracts.py`（符号集匹配）、`test_portfolio_loop.py`（粘性约束 + `test_fails_high_sharpe`）
- **影响范围**: 无法一键全绿验证；回归基线不可信；后续改动无法区分"自身回归"与"既有噪音"
- **当前进展**: 已登记完整修复清单 `docs/harness/plans/regression-fix-list-20260808.md`；2 个 LLM 依赖用例已改跳过标记（GAP-030 引用）
- **处理期限**: 3 月内（P2）
- **验证结果**: ✅ 已关闭（v2.47.0）— 92 个基线失败全部修复清零，覆盖测试冲刺期间新增测试后全量回归 **3836 passed / 0 failed / 3 skipped**，覆盖统计与测试文件清单同步更新至 06-testing.md；后续 v2.51.0 解除 2 个 GAP-030 引用 skip 并修复（promote_to_elite/failure_rate_circuit_breaker），全量回归 **4021 passed / 0 failed / 0 skipped**

### GAP-040: cross_section 家族因子库来源未细分（P2，已关闭）

- **问题描述**: 8/2-8/5 L2 种子晋升将 qlib/gtja/wq101 三大外部因子库共 111 条记录统一归入 `cross_section` 家族（qlib_* 43 / gtja_* 36 / alpha_* 30 / fut_gp_* 2），丧失因子库来源维度，L3 组合按家族分组管理时无法区分库来源；根因是 `FactorFamily` 无 qlib/gtja/wq101 标准值，`_infer_factor_family` 将 `qlib_/gtja_/wq_` 前缀统一映射为 trend、`_infer_family_from_filename` 将 qlib158/gtja191/wq101 文件名统一映射为 trend
- **影响范围**: 因子家族维度信息失真；按来源家族做多样性/相关性控制时粒度过粗
- **解决方案**: ① `FactorFamily` 新增 qlib/gtja/wq101 三个标准家族（14→17 大类）；② `_infer_factor_family` 按名称前缀精确映射（`qlib_`→qlib、`gtja_`→gtja、`alpha_`/`wq_`→wq101、`fut_` 保持 trend）；③ `_infer_family_from_filename` 与 YAML 种子 family 字段对齐（qlib158/gtja191 → qlib/gtja，wq101 保持）；④ DuckDB 一次性数据迁移 111 条记录（qlib 43 / gtja 36 / wq101 30 / fut_gp_*→behavioral 2），迁移前已备份 `factor_catalog.duckdb.bak_family_split_20260808`
- **验证结果**: 迁移后 cross_section 剩余 0 条；新增 12 测试用例（test_contracts_kind.py 8 个 family 推断 + test_seed_loader.py 4 个文件名映射），全绿

### GAP-041: 14 个覆盖率 <90% 模块（P2，已关闭）

- **问题描述**: v2.47.0 全量回归（3836 passed，覆盖率 94%）后 16 个模块覆盖率 <90%（v2.87.0 后 14 个：`tdx_minute_source`/`tq_source` 已合并删除，新源 `tdx_local_source` 93% 达标），缺口语句集中在：① 外部数据源网络/鉴权路径（`ifind_source` 84% / `wind_source` 87% / `tqsdk_tick_source` 73%，需模拟网络异常/鉴权失败/超时）；② 近期新增模块参数校验与降级分支（`cross_market/data_adapter` 55% / `factor_clustering` 64% / `factor_db/migrate_from_json` 73%）；③ 核心引擎异常兜底（`evolution_loop` 80% / `data` 85% / `factor_db/repository` 85% / `ml/models` 86% / `causal_validator` 89% / `contracts` 89% / `factor_screener` 87% / `data_quality_monitor` 82%）
- **影响范围**: 关键路径异常分支未验证，外部数据源降级逻辑存在隐性 bug 风险；P0 级 bug（如 regime.py 模块 logger 缺失、http_server.py DuckDB 列名获取）即由低覆盖模块引入
- **处理方案**: 按优先级分批补充（P1：`cross_market/data_adapter`、`factor_clustering`、`factor_db/repository`；P2：外部数据源异常路径 mock 测试；P3：`evolution_loop` 兜底分支）
- **验证结果**: v2.88.0（GAP-F16）三分组补齐 14 个 <90% 模块测试 +341 用例（组A 数据源 139：ifind/wind/tqsdk_tick/tq/tdx_minute 网络异常/鉴权失败/超时/降级兜底 mock + data/data_quality_monitor；组B factor_engine 139：evolution_loop TestGapF16* 11 类 + factor_screener 新建 35 + contracts/causal_validator/factor_clustering；组C 跨市场/DB/ML 63：migrate_from_json 新建 19 + data_layer_repos 31 + ml/models/mlp/gru），全量回归 5132 passed 全绿，覆盖率 TOTAL 94.31%（`--cov-fail-under=90` 达标），14 个缺口模块全部 ≥90%
- **处理期限**: 已关闭（v2.88.0）

### GAP-043: 质检拦截器判定缺陷（P0，已关闭）

- **问题描述**: L2 期货演化 15 代中 5 个通过 Verifier 的候选（IC 0.31~0.52）全部被两个拦截器误杀，失败率 100% 触发熔断：① 消融实验（`evolution_loop._run_ablation_check`）将 `shuffle_dates`（时间戳打乱）与 `zero_one_feature`（置零核心价格列 open/high/low/close/vwap/settle）导致的 IC 崩塌统一判定为"伪相关"——但时序因子依赖时序因果、价格因子依赖价格列属必要特征，判定语义反了（g1/g15 被拦）；② 鲁棒性缺失值测试（`robustness._inject_missing` 注入 NaN 后 `evaluation_chain._compute_ic` 的 spearmanr/pearsonr 无 NaN 掩码返回 0.0）→ 缺失值测试 3/3 恒失败，保持率 0%，g6/g9/g13 被拦（对抗样本 4/4、分布外 3~4/4 均通过，因子本身鲁棒）
- **影响范围**: 高IC演化候选被系统性误杀 → 演化停滞、无新增精英因子；种子/演化质检链同源缺陷
- **处理方案**: v2.50.0 ① 消融判定改为「信息型/拦截型」——shuffle_dates/成交量/VWAP 消融与核心价格列置零为信息型（记录不拦截），仅非价格列置零 IC 降幅 >50% 判伪相关；② `_compute_ic` 增加 NaN 掩码（计算前剔除 NaN 对）；③ `SingleAblation` 新增 `feature` 字段记录置零列
- **验证结果**: 新增/更新 ~18 测试用例全绿；tests/factor_engine/ 回归无新增失败；L2 演化重跑解除 100% 熔断
- **处理期限**: 已关闭（v2.50.0）

### GAP-044: 鲁棒性缺失值测试阈值过高（P1，已关闭）

- **问题描述**: v2.50.0 修复消融判定语义和 IC NaN 掩码后，L2 期货演化 12 个种子因子全部被鲁棒性缺失值测试拦截，仍为 100% 失败率熔断。种子因子本身质量高（如 `fut_macro_export` IC=0.49、对抗样本 4/4 通过、分布外 3/4 通过），但 `_inject_missing` 随机单元格级 NaN 注入极其激进——5% 随机 NaN 即导致 IC 保持率降至 0.56、10% 降至 0.48、20% 降至 0.0（滚动窗口计算被 NaN 完全破坏）。`missing_retention_threshold=0.80` 下 3 个缺失值测试全部失败，保持率远低于 80%。
- **影响范围**: 父因子池为空 → 后续 GP 演化 11 个常数信号退化因子 + Macro 演化 8 个弱 IC 因子 → 0 晋升 → 总失败率 100% 熔断 → 演化停滞
- **处理方案**: v2.52.0 将 `RobustnessTester` 默认 `missing_retention_threshold` 从 0.80 降至 0.50（与 OOD 测试对齐）。设计依据：① 真实数据缺失通常是整列缺失（如某日某品种停牌），而非随机单元格级缺失；② 随机单元格级缺失对滚动窗口因子杀伤力远大于真实数据质量问题；③ OOD 测试已用 0.50 阈值，同一模块应用相同标准
- **验证结果**: 无新增测试（阈值参数调整，ROB-102/103 缺失值测试通过条件更新）；影响所有市场（股票/期货统一），L2 期货演化预期解除熔断
- **处理期限**: 已关闭（v2.52.0）

### GAP-045: adaptive 权重未完整接入 L3（P1，已关闭）

- **问题描述**: FTS 存在三处 adaptive 相关实现但 L3 生产路径仅接入最简形态：
  1. **入口不一致**：L3 主循环 `PortfolioLoop.run()` Step 2.5 直接调用 `regime_adaptive_weight_adjustment()`，而回测管线 `PortfolioConstructor(weight_method="adaptive")` 走 `AdaptiveWeightManager` 封装——两套入口逻辑不同步，违反"回测与实盘强对齐"红线（GAP 家族同源问题）。
  2. **无应用层平滑**：Regime 切换时权重瞬时跳变。`RegimeSmoother`（`adaptive_weight.py`）已实现但未接线；平滑仅存在于 regime 检测层（`_REGIME_PERSISTENCE_FACTOR=0.7`），属检测侧而非权重应用侧。
  3. **style 维度缺失**：原设计 `A.3-adaptive-weight-design.md` 声明的 FactorStyle / style_tags 维度从未实现，`REGIME_FAMILY_MULTIPLIERS` 仅覆盖 FactorFamily（17 家族）。
- **影响范围**: 回测与生产权重路径不一致 → 组合行为可复现性差；Regime 切换权重跳变 → 换手成本与策略漂移风险；调整维度缺乏风格粒度 → 防御/价值/情绪等风格信号无法制度化调节
- **处理方案**: v2.56.0 按 `plans/19-adaptive-weight-l3-integration.md` 实施——
  1. `synthesis_mode` 扩展 `adaptive`，Step 2 委托 `PortfolioConstructor`（统一回测/生产入口）
  2. `RegimeSmoother(alpha=0.5, min_days=2)` 接入 Step 2.5，参数走 `AdaptiveWeightConfig`
  3. 实现 FactorStyle 枚举 + `style_tags` 列（DuckDB 兼容补列）+ `REGIME_STYLE_MULTIPLIERS` 双维度调整
- **验证结果**: ✅ 已关闭（v2.99.0 确认：① `synthesis_mode="adaptive"` Step 2 委托统一入口（回测/生产同源）；② `RegimeSmoother(alpha=0.5, min_days=2)` 接入 Step 2.5 权重平滑（`adaptive_config` 走 `AdaptiveWeightConfig`）；③ FactorStyle 枚举 + style_tags 列（DuckDB 兼容补列）+ `REGIME_STYLE_MULTIPLIERS` 双维度调整（family×style，clamp [0.5×base,1.5×base]）已落地；test_portfolio_loop_adaptive/test_style_classifier 全绿）
- **处理期限**: 已关闭（v2.99.0）

## 4. 优先级定义

| 优先级 | 定义 | 处理时限 | 验证标准 |
|:-------|:-----|:---------|:---------|
| **P0** | 阻塞性问题，影响核心功能的正确性和可靠性 | 1 周内 | 新增测试覆盖率达到 80%+，相关模块无 P0 bug |
| **P1** | 重要改进，提升系统效率或稳定性 | 1 月内 | 新增测试覆盖率达到 70%+，关键路径全覆盖 |
| **P2** | 一般改进，优化代码质量和可维护性 | 3 月内 | 新增测试覆盖率达到 50%+ |

---

## 5. 差距关闭流程

1. 编写测试代码并通过 PR 审查
2. 运行完整测试套件确认全部通过（2928+ passed, 0 failed）
3. 更新本文件中的差距状态
4. 更新 `06-testing.md` 中的覆盖统计
5. 如果涉及架构变更，更新 `01-architecture.md`

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射| 可验证断言 | 本文件登记 100 项差距（GAP-001~072 主表 68 + GAP-I 系列 20 + GAP-L 系列 12），覆盖 `fts/factor_engine/`、`fts/data_sources/`、`fts/data.py`、`fts/cli.py`、`fts/core/`、`fts/monitor/`、`fts/scheduler/`、`fts/risk/`、`fts/factor_db/`、`fts/ml/`、`pipeline/`、`strategies/`、`scripts/`、`docs/`、`agents/` 等模块。GAP-020~024 关联 `plans/factor-management-optimization-plan.md`；GAP-039 关联 `plans/regression-fix-list-20260808.md`；GAP-045 关联 `plans/19-adaptive-weight-l3-integration.md`；GAP-046 关联 `plans/20-futures-roll-adjustment-plan.md`；GAP-I/L 系列关联 `plans/23-institutional-transformation-plan.md`/`plans/24-l3-l4-institutional-plan.md`；v2.60.0 登记 GAP-I 系列 20 项 + GAP-L 系列 12 项；v2.65.0~v2.85.0 逐批关闭 I/L 系列；v2.88.0 关闭 GAP-041（GAP-F16 覆盖率补齐 +341 用例）；v2.89.1 关闭 GAP-059；v2.90.0/v2.97.0/v2.98.x 关闭 GAP-060~072 各批次；v2.99.0 收尾关闭 GAP-046/045/050/X01/X02/X03/037/041/058（登记状态同步：9 项差距代码与测试均已落地，本次完成状态闭环） | | **98 项已关闭 / 2 项延期（GAP-068 多频信号叠加、GAP-069 会员持仓排名拥挤度，均属研究项延期，见 plans/25 §3）**。关闭历程：GAP-025 孤立模块集成修正 v2.10.0；GAP-026 算子命名对齐 v2.10.0；GAP-027 `code: Optional[str]` v2.14.0；GAP-028 既有失败测试修复 v2.14.0；GAP-029 L3 漂移治理 v2.11.0；GAP-030 集成测试污染 catalog v2.14.0；GAP-031 L1-L2 注入接入 v2.14.0；GAP-032 演化产物同步 catalog v2.13.0；GAP-033 数据泄露+IC 衰减 v2.15.0；GAP-034 P1 因子聚类 v2.36.0；GAP-035 P2 PCA 降维 v2.36.0；GAP-036 L1 注入候选清理 v2.38.0；GAP-037 深度因子学习 v2.60.0 MLP + v2.73.0 GRU 关闭；GAP-038 种子相关性预检卡死 v2.39.0；GAP-039 全量回归失败项 v2.47.0；GAP-040 cross_section 家族来源 v2.40.0；GAP-042 极值扰动一票否决 v2.79.0；GAP-043 质检拦截器判定 v2.50.0；GAP-044 鲁棒性阈值 v2.52.0；GAP-045 adaptive 权重 L3 v2.99.0 确认关闭；GAP-046 换月复权+展期 v2.58.0 阶段A + plans/21 全落地 v2.99.0 确认关闭；GAP-041 覆盖率补齐 v2.88.0；GAP-050 数据源加固 v2.60.0 + tick 回放 v2.84.0 v2.99.0 确认关闭；GAP-X01/X02/X03 演化预筛三件套 v2.99.0 确认关闭；GAP-058 测试竞态 v2.99.0 确认关闭（测试已加固） |
| 检验方式 | 检查本文件差距登记表确认所有差距状态为 ✅ 已关闭；关联文档 `plans/factor-management-optimization-plan.md` |