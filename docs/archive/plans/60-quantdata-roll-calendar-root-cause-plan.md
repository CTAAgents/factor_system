# FTS 换月日历根治：统一消费 QuantData continuous_map/daily（plans/60）

> 版本: v3.1.0+3
> 最后更新: 2026-08-20
> 状态: ✅ 阶段 A/B 已完成（v3.1.0+3 落地）；C 阶段文档同步与版本 bump 完成
> 适用范围: FTS 数据层（roll_calendar / data_futures / quantdata_provider / aggregator）+ 因子重算验证
> 前置: plans/20（GAP-046 换月复权）历史记录；v3.0.0+1 QuantData 唯一权威源

---

## 0. 背景与根因判定

### 0.1 问题现象

FTS 换月日历生成存在两类异常：
1. **缺价跳过换月事件**（已修复为回溯取价，backfill_days=20，仍有 76 个无法救回）
2. **幽灵主力/假换月**（如 BB2018 年本地有 18 行幽灵数据而 QuantData 为 0 行）

### 0.2 根因判定：FTS 端问题，QuantData 端可根治

| 证据 | 说明 |
|:-----|:-----|
| QuantData 已有权威换月数据 | `continuous_map` 121,100 行/88 品种（2016-01~2026-08），逐日 `main_contract`，88 品种全部有换月事件；`continuous_daily` 自带 `adj_factor`（后复权），**0 个无效** |
| FTS 端重复造轮子 | `data_futures.get_ohlcv(adjusted=True)` 用本地 `contract_kline` + `RollCalendar` 重新判定主力并复权，与 QuantData 已复权序列**双重复权**（quantdata_provider.py L22-23 明确"二选一，避免双重复权"） |
| 本地数据脏 | `contract_kline` 有幽灵数据（BB2018 年 18 行，QuantData kline_daily 为 0 行）→ 幽灵主力/假换月 |
| 口径不一致 | QuantData：**OI 最大 + 3 日防抖 + 未到期过滤**；FTS RollCalendar：**volume 最大 + 无防抖**。RB 换月次数 QuantData=19 vs FTS=22 |

**结论**：FTS 用过期脏本地数据重建换月日历并二次复权，QuantData 侧已有权威数据。根治 = FTS 换月/复权统一消费 QuantData。

---

## 1. 历史因子重算影响范围评估（已完成）

### 1.1 资产规模

| 资产 | 规模 |
|:-----|:-----|
| futures_elite 因子（JSON 快照） | 346 个 |
| factor_catalog_futures.duckdb | 346 条（active 86 / retired 122 / archived 132 / deprecated 5 / deleted 1） |
| 信号缓存 | memory/cache/factor_signals/ 12,161 个 parquet |
| L3 信号矩阵库 | l3_signal_store.duckdb 未生成（依赖首次构建） |

### 1.2 实证：FTS 主链路数据现状

| 检查项 | 结果 |
|:-----|:-----|
| QuantData continuous_daily RB adj_factor 范围 | 1.0731~1.2018（**非 1.0，已复权**） |
| FTS kline_cache adj_factor | **全部 1.0**（未缓存 QuantData 复权序列；kline_cache 无 QUANTDATA source） |
| FTS get_ohlcv RB0 近 160 日 vs QuantData 复权 | **0/160 日不一致**（主链路实际已从 QuantData 取复权序列） |
| 双重复权风险点 | get_ohlcv 在 QuantData 已复权序列上再套 RollCalendar.apply_adjustment → **当 contract_kline 有幽灵换月时二次污染** |

### 1.3 影响范围结论

- **直接影响时段**：2018-2022 幽灵合约数据段（BB/CY/FB/JR/LR/RI/RS/WH 早期）
- **受影响因子**：346 个中依赖日线复权序列的全部量价类因子（IC/换手评估需重算）
- **影响性质**：
  - 近端（2023+）数据已走 QuantData，几乎无影响
  - 历史段双重复权/幽灵换月 → 因子历史信号、评估指标（IC/IR/回撤）需重算
  - **重算可自动化**：`SignalCache`/`l3_signal_service` 支持增量重算（params/code 变更触发），因子计算确定性（固定种子）

---

## 2. QuantData 换月口径 vs FTS 因子研究口径一致性确认（已完成）

| 维度 | QuantData continuous_map | FTS RollCalendar（现） | 一致性 |
|:-----|:-----|:-----|:-----|
| 主力判定 | **OI（持仓量）最大** + 3 日防抖 + 未到期过滤 | **volume（成交量）最大** + 无防抖 | ❌ 不一致 |
| 复权方法 | 后复权，换月日重叠窗口（±5 日）平均价格比率平滑 | 单日收盘价比率 | ⚠️ 近似但非同口径 |
| 数据覆盖 | 88 品种，2016-01 起，0 无效因子 | contract_kline 84 品种（本地，含幽灵） | ❌ 不一致 |
| 换月事件数（RB 实证） | 19 | 22 | ❌ 不一致 |

### 2.1 口径差异影响评估

- **OI vs volume 判定差异**：持仓量是主流期货主力判定标准（官方/行情商一致口径），volume 受日内投机成交干扰大。QuantData 口径**更权威**。
- **对因子研究的影响**：换月日期不同 → 复权序列历史段不同 → 因子值/IC 历史段差异。**研究口径应统一到 QuantData**（消除双口径分歧，符合 AGENTS.md 一数一源）。
- **结论**：QuantData 口径更优，FTS 应消费之。切换后 FTS 与 QuantData 换月日历零偏差。

---

## 3. 根治实施规划

### 阶段 A：RollCalendar 数据源切换（核心改造）

| # | 模块 | 改动 | 验证 |
|:--|:-----|:-----|:-----|
| A1 | `fts/data_sources/roll_calendar.py` | `build_roll_calendar` 数据源从本地 `contract_kline` 自判 → 读 QuantData `continuous_map`（`main_contract` 逐日变化即换月事件）；`old_contract`/`new_contract` 直接取权威映射 | 单测：构造 continuous_map → 事件序列正确；幽灵合约不再出现 |
| A2 | `fts/data_sources/roll_calendar.py` | `compute_adjust_factors` 直接消费 `continuous_daily.adj_factor`（或复用 `_overlap_ratio` 重叠窗口比率），废弃自算单日比率 | 单测：adj_factor 与 QuantData 逐值一致 |
| A3 | `fts/data_sources/roll_calendar.py` | 降级路径保留：QuantData `continuous_map` 缺失品种/日期 → 回退 contract_kline 回溯逻辑（backfill_days=20） | 单测：QuantData 缺失时回退行为 |
| A4 | `fts/data_futures.py` | `get_ohlcv(adjusted=True)`：QuantData 主链路**不再二次复权**（返回 Provider 已复权序列）；仅非 QuantData 降级链（kline_cache/SYNTHETIC）套 RollCalendar | 集成测试：RB0 复权 close 与 QuantData 0 偏差 |
| A5 | `fts/data_sources/quantdata_provider.py` | `fetch_ohlcv` 透传 `adj_factor` 列（当前未返回，供 A2/A4 消费） | 单测：17 列 schema 追加 adj_factor |
| A6 | `fts/monitor/data_level_monitor.py` | adj_factor 豁免逻辑核对（GAP-148）：确认 data_level_monitor 不再依赖 contract_kline 复权 | 单测：监控通过 |

### 阶段 B：换月日历一致性校验（回测验证）

| # | 内容 | 验证 |
|:--|:-----|:-----|
| B1 | 全 84 品种换月日历对比：QuantData continuous_map vs FTS RollCalendar（切换后） | 逐品种事件序列一致；幽灵事件归零；跳过事件归零 |
| B2 | 复权序列对比：FTS get_ohlcv(adjusted=True) vs QuantData continuous_daily | 全品种 0 偏差 |
| B3 | 历史因子信号重算：346 因子（active 86 优先）走 `SignalCache`/`l3_signal_service` 增量重算 | 重算信号与旧信号对比报告；差异率统计 |
| B4 | 评估指标回归：重算后 IC/IR/回撤 vs 历史记录 | 指标漂移报告（区分幽灵时段/近端） |
| B5 | 样本外一致性：随机抽 10 因子，新旧链路 IC 相关性 | 相关性 ≥ 0.95 视为通过（近端），历史段允许差异 |

### 阶段 C：落库与收尾

| # | 内容 | 验证 |
|:--|:-----|:-----|
| C1 | L3 信号矩阵重建（依赖 B3 重算信号） | l3_signal_store 构建成功，矩阵与信号缓存一致 |
| C2 | 文档同步：01-architecture（数据链/复权口径）/03-configuration（若有新配置）/04-resilience（降级链）/06-testing（用例数）/07-operations（版本历史）/AGENTS.md | verify_doc_consistency 通过 |
| C3 | 版本 bump（build）：v3.1.0+2 → v3.1.0+3 | bump 脚本 + 单测 |
| C4 | 全量回归（发布前必跑） | pytest tests/ -m "not slow" 全绿 |

---

## 4. 回测验证步骤（可执行）

```powershell
# Step 1: 换月日历一致性（阶段 B1）
cd d:\Programs\factor_system
python -u scripts/verify_roll_calendar_consistency.py   # 84 品种 QuantData vs FTS 事件序列对比

# Step 2: 复权序列一致性（阶段 B2）
python -u scripts/verify_roll_adjust_consistency.py     # 全品种 get_ohlcv(adjusted) vs continuous_daily

# Step 3: 历史因子信号重算（阶段 B3，active 86 优先）
python -u scripts/recompute_factor_signals.py --market futures --scope active   # 增量重算
python -u scripts/recompute_factor_signals.py --market futures --scope all      # 全量重算

# Step 4: 评估指标回归报告（阶段 B4）
python -u scripts/compare_factor_metrics.py --baseline memory/knowledge/factors/futures_elite

# Step 5: 样本外一致性抽检（阶段 B5）
python -u scripts/verify_oos_consistency.py --sample 10

# Step 6: L3 信号矩阵重建（阶段 C1）
python -u -c "from fts.scheduler.jobs import l3_signal_matrix_job; l3_signal_matrix_job()"

# Step 7: 文档一致性 + 版本
python scripts/verify_doc_consistency.py
python scripts/bump_version.py --build --message "换月日历根治：统一消费 QuantData continuous_map/daily"
```

---

## 5. 风险与降级

| 风险 | 应对 |
|:-----|:-----|
| 历史因子重算导致 IC 漂移（口径变化） | B4/B5 对比报告留档；漂移集中幽灵时段属预期修正，近端应稳定 |
| QuantData continuous_map 维护频率（Data-Core 侧） | 明确由 Data-Core 负责增量维护；FTS 只读消费（角色边界）；缺失时回退 contract_kline |
| 双重复权遗留的缓存脏数据 | kline_cache 不落 QuantData 复权（只读直连）；无需清缓存 |
| 回滚需求 | 保留 A4 开关（如 `roll_use_quantdata`，默认 true），可回退旧逻辑 |

---

## 6. 待用户确认项

1. ✅/❌ 按本方案落地（阶段 A→B→C 顺序执行）
2. QuantData continuous_map 维护方确认：Data-Core 增量维护（FTS 只读消费）
3. 历史因子重算范围：仅 active 86 / 全量 346？（建议先 active 后全量）
4. 是否保留 contract_kline 回退路径作为长期降级（建议保留）
