# 36. 因子选择与组合构建改进计划（GAP-122 延伸，v2.104.0+42 规划）

> 版本: v2.105.0（实施完成）
> 状态: ✅ 已实施（2026-08-15 v2.104.0+50 全部落地，见 07-operations 版本条目）
> 关联: GAP-122（L3 Verifier 判定口径修复，已关闭）、35-gap-closure-plan、plans/29 存储收敛、config/settings.yaml
> 范围: 主系统 L3 Portfolio Loop 因子选择与组合构建链路（`fts/factor_engine/portfolio_loop.py`），适用通用期货 + 能源链双 universe

---

## 1. 背景与动机

### 1.1 现状：三层漏斗选因子

当前 L3 组合构建按「三层漏斗」从精英因子库选入因子（能源链实例，2026-08-15 第三轮运行）：

| 层 | 环节 | 现状 | 能源链实测 |
|:---|:-----|:-----|:-----|
| L1 | 精英池 + 质量门槛 | DuckDB `market=energy, is_elite=True`，min_ic=0.03 / min_sharpe=1.5 | 38 因子 |
| L2 | ACTIVE_FACTOR_CAP=20 | 按 **原始 Sharpe 降序** 截断前 20 | 38→20 |
| L3 | P1 相关性聚类（threshold=0.70） | 每簇取 **最高原始 Sharpe 代表** | 20→4 |
| 定权 | 等权 1/N → Regime×G1 风控缩放 | SHARPE_CAP=2.0 截断后等权 | 各 0.25 → exposure_scale=0.2686 |

### 1.2 已识别问题（GAP-122 复盘，2026-08-15 评审结论）

1. **选入标准与定权标准脱节**：选入按原始 Sharpe 排序（fut_bias=17.09、fut_cross_carry=12.91），定权却按 SHARPE_CAP=2.0 截断后等权——最高质量因子与最低质量入选因子权重相同，且组合对单因子 Sharpe 排名噪声敏感。
2. **聚类"每簇仅取 1 个 + 阈值 0.7"有信息损失与阈值敏感性**：簇内因子（如 fut_bias 与 seed_spread 相关>0.7 但可能互补）被一刀切；0.7 为经验值未做敏感性检验。
3. **组合层缺乏样本外/滚动验证**：因子级有 OOS/多重检验，但最终因子组合本身无滚动样本外 IC/夏普输出；另 Step 7.5 质量报告（40 因子）与 Step 1a 加载（38 因子）口径不一致（小瑕疵）。
4. **组合层对历史统计量过度依赖**：无组合层面的增量信息/拥挤度约束（详见调研 §2.4，crowding 是机构主流关注点）。

---

## 2. 业内同行做法调研（改进项 1 —— 已调研，2026-08-15）

### 2.1 因子筛选：综合评分 + 分层漏斗（共识）

- **HL Hunt Research《Quantitative Factor Model Construction》（2026-04）**：独立因子约 5–10 个即可解释大部分横截面收益；约 50% 已发表因子样本外失败；筛选须含点及时数据（point-in-time）、横截面标准化、Winsorize、行业中性化。来源: <https://www.hlhunt.org/uncategorized/quantitative-factor-model-construction-a-systematic-framework-for-alpha-generation-hl-hunt-research/>
- **ifisme《多因子选股策略》**：漏斗模型分四层——① 有效性检验（IC/ICIR）→ ② 相关性去冗余（相关系数>0.7 剔除）→ ③ 信息增量性检验（与已有因子高度相关者不纳入）→ ④ 样本外验证（未参与筛选的数据验证，防过拟合）；**综合评分用 IC 均值、ICIR、多空收益、T 值**（而非单一 Sharpe）排序选入。来源: <https://www.ifisme.cn/archives/1308>
- **知识库《450 因子→24 种子》瘦身革命（2026-08-04）**：期货两两相关 >85% 因子对 |ρ|>0.85；先聚类去重（|ρ|>0.85 归簇、每簇留代表），再剔除期货不可用字段，后精简窗口参数变体；主张"因子数量不是竞争力，因子质量才是""100 个 ρ=0.9 不如 10 个 ρ=0.3"。来源: D:\Knowledge\quant\method\2026-08-04_450_factors_to_24_seed_factors_slimming_revolution.md

**结论 A（选入标准）**：业界主流以 **ICIR / IR / T 值综合评分**（风险调整后质量）而非原始 Sharpe 选因子；原始 Sharpe 对单一历史窗口敏感、未做波动率/衰减调整，属边缘做法。

### 2.2 因子合成与定权：等权为稳健默认，加权需谨慎（共识）

- HL Hunt：等权稳健可解释、是最佳默认；优化加权（IR 最大化）可提升 IR 但**高风险过拟合**，须样本外验证后才可上线；组合因子层面（composite）整合优于因子组合层面（portfolio）整合。
- 知识库 450→24：明确「等权为默认最优，避免组合层面 MVO 优化」（呼应 Feng(2026)《When Simplicity Beats Optimization》SSRN:7005278）。
- ifisme：常见合成方法——等权 / IC 加权 / ICIR 加权 / IR 最大化优化 / 机器学习融合，由简到繁。

**结论 B（定权标准）**：等权作为基线保留，但若采用加权，须用 **cap 后/风险调整口径（ICIR 或截断 Sharpe）** 且必须过样本外；禁止直接用原始 Sharpe 权重。

### 2.3 冗余处理：相关性阈值 + 敏感性分析（有改进空间）

- 知识库 450→24 自述局限：**阈值（0.85）主观、对结果敏感且无敏感性分析；相关性≠冗余的充分条件**（非线性/regime 切换下可能误删互补因子）——与本项目 P1 聚类 threshold=0.70 问题同构。
- ifisme：>0.7 剔除为普遍参考值，但同样为经验值。

**结论 C（聚类）**：阈值须做敏感性分析（如 0.6/0.7/0.8），并允许簇内保留低相关互补代表（top-2 且互相关<0.5），而非每簇硬取 1 个。

### 2.4 组合层风险：拥挤度与样本外（机构关注点）

- 知识库《Crowded Spaces and Anomalies》（2026-08-06，知识库 paper 目录）：因子拥挤导致超额收益消失，拥挤度是机构组合构建的必查维度。
- HL Hunt：~50% 发表因子 OOS 失败 → 组合层须滚动样本外验证。

**结论 D（组合层）**：组合层加滚动 OOS（滚动 IC/夏普/衰减）+ 拥挤度/集中度监控（HHI 已有，可加暴露拥挤）。

---

## 3. 改进方案（用户采纳项 2/3/4 + 调研结论 A 落地）

### 改进项 2（采纳）：选入标准稳健化 —— 综合评分替代裸 Sharpe

- **问题**：L2 截断与 L3 簇代表均按原始 Sharpe 排序（§1.2-①、§2.1 结论 A）。
- **方案**：
  1. 定义**因子综合评分** `score = w_icir·rank(ICIR) + w_ir·rank(IR) + w_t·rank(|t|) + w_sharpe·rank(Sharpe_cap)`（默认等权 0.25，权重配置化于 `config/settings.yaml` 的 `l3.factor_score` 段）；
  2. L2 ACTIVE_FACTOR_CAP 截断与 L3 簇代表选择改用综合评分排序（`_sharpe_raw` 保留审计）；
  3. 评分输入统一为**衰减调整/风险调整后口径**（ICIR=IC/IC 波动、IR=IC 均值/IC 标准差，均取滚动 1y 窗口，防单点噪声）。
- **改动点**：`portfolio_loop.py`（新增 `_factor_composite_score()`；L2/L3 调用点替换排序键）；`config/settings.yaml`（`l3.factor_score` 权重段）；测试 +N。
- **验收**：能源链重算后入选 4 因子与旧法结果对照（可保留旧法对照输出）；评分排序与原始 Sharpe 排序的 Spearman 相关>0.8 时提示"口径收敛"。

### 改进项 3（采纳）：定权差异化 —— cap 后 Sharpe/ICIR 加权替代纯等权

- **问题**：定权等权导致最高质量与最低质量入选因子权重相同（§1.2-①、§2.2 结论 B）。
- **方案**：
  1. 新增 `synthesis_mode="quality_weight"`：权重 ∝ cap 后综合评分（同改进项 2 的 score，`w_i = score_i / Σscore`），默认 **cap 下限 0.5×等权权重** 防极端分化；
  2. **等权保留为默认**（`equal_weight` 不动），`quality_weight` 作为可选模式经 `--synthesis-mode` 开启并附样本外对照；
  3. 风控缩放（Regime×G1）在加权后照常生效，链路不变。
- **改动点**：`portfolio_loop.py`（`synthesize_signals` 新增分支）；`fts/cli.py`（`--synthesis-mode` choices + quality_weight）；测试 +N。
- **验收**：quality_weight 模式下 signal_sharpe ≥ 等权基线（样本内），且滚动 OOS（改进项 4）不劣于等权——否则维持等权默认。

### 改进项 4（采纳）：聚类阈值敏感性 + 簇内 top-2 + 组合层滚动 OOS

- **问题**：threshold=0.70 一刀切、每簇仅取 1 个（§1.2-②）、组合层无滚动 OOS（§1.2-③、§2.3/2.4 结论 C/D）。
- **方案**：
  1. **阈值敏感性分析**：`scripts/l3_cluster_sensitivity.py` 对 0.60/0.65/0.70/0.75/0.80 扫描，输出入选因子集 Jaccard 重合度 + 组合 OOS 指标表（写入 `reports/energy_chain/l3_cluster_sensitivity_*.md`），验证 0.70 邻域稳定性；
  2. **簇内 top-2**：每簇保留 Sharpe/score 前 2 且互相关<0.5 的代表（`cluster_top_n=2`，配置化），超出则只保留 1；
  3. **组合层滚动 OOS**：`build_combo` 输出滚动 6 窗口（步长 60 交易日）组合 IC/夏普/衰减，写入 combo 契约 + 日志 `[L3-OOS]`，纳入 Step 6 Verifier 可选项（`oos_min_ic` 配置，默认关闭仅告警）；
  4. 质量报告口径对齐：Step 7.5 与 Step 1a 统一 `active+elite` 口径。
- **改动点**：`portfolio_loop.py`（聚类参数化、OOS 输出）、新脚本 `scripts/l3_cluster_sensitivity.py`、`contracts.py`（`L3VerifierConfig.oos_min_ic` 可选）、测试 +N。
- **验收**：敏感性报告显示 0.70 邻域入选因子集重合度≥80%；滚动 OOS 输出首份报告；口径统一后 Step 1a 与 7.5 因子数一致。

---

## 4. 实施阶段与验收

| 阶段 | 内容 | 交付 | 验收 |
|:---|:-----|:-----|:-----|
| P0（已完） | 业内调研 | 本章 §2 | 引用来源可溯、结论 A–D 明确 |
| P1 | 改进项 2：综合评分选入 | `_factor_composite_score` + settings 段 + 测试 | 能源链重算，score 与旧法对照输出 |
| P2 | 改进项 3：quality_weight 定权 | `synthesize_signals` 新分支 + CLI + 测试 | 样本内 signal_sharpe≥等权；OOS 不劣 |
| P3 | 改进项 4：聚类敏感性 + 簇内 top-2 + 滚动 OOS | 敏感性脚本 + 报告 + OOS 输出 + 口径统一 | 敏感性报告重合度≥80%；首份 OOS 报告 |
| P4 | 全量回归 + 文档 | 06-testing / 07-operations / 08-gap / 01-architecture | 回归全绿 + HARNESS 13 项通过 |

- 每阶段遵循 HARNESS：契约先行（先 TypedDict/配置段）、测试随重构、阶段末 bump build 版本号。
- 组合行为变化一律**等权基线 + 新法对照**双路输出，防回归不可见。

## 5. 风险与依赖

- **依赖**：因子库需含 ICIR/IR/t 统计量（评估链已产，确认回填完整）；`_sharpe_raw` 审计字段保留（已有）。
- **风险**：
  1. 综合评分加权可能被解读为"优化过头"→ 以等权为默认 + OOS 为准入闸（结论 B 纪律）；
  2. 簇内 top-2 可能提升组合换手（更多因子）→ 由既有 G1/G3 换手预算约束 + Verifier max_turnover 复核；
  3. 阈值敏感性若显示 0.70 不稳 → 择稳定解并登记调整依据（GAP 登记）。**实测（2026-08-15 能源链 38 elite 因子）**：0.70/0.75 邻域 Jaccard=1.000（完全重合，5 因子），0.80=0.800（少 fut_macd_hist），0.60/0.65=0.714（多 fut_price_volume_coupling/fut_hf_historical_return 2 因子）——0.70 邻域**上界稳定、下界 0.60/0.65 会额外引入 2 个低相关因子**，判定 ⚠️ 敏感；维持默认 0.70 不变（0.70-0.75 区间稳定），0.60 以下扩容选项记录于 `reports/energy/l3_cluster_sensitivity_20260815.md`，供后续结合 OOS 权衡。
- **不改变**：GAP-122 的 Verifier 判定口径（signal_sharpe）、风控缩放链路、状态存储（StateKVStore）。

## 6. 关联与后续

- 实施完成后登记/关闭对应 GAP（选入标准/定权/聚类/OOS 各一项，P1/P2 分级），更新 docs/harness（01/06/07/08）与 README。
- 本计划仅规划 L3 组合构建；信号管道消费侧（20:00）无需改动。
