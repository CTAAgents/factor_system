# FTS 五层逻辑审查实施计划

> 版本: v2.104.0+77
> 创建: 2026-08-04
> 最后更新: 2026-08-05
> 状态: ✅ 已完成 — Phase A/B/C 全部完成

---

## 1. 背景与动机

FTS 因子演化系统是端到端的黑箱系统：输入海量数据 → 无数非线性组合 → 输出预测信号。传统的"看因子公式"方式行不通，必须转向**行为验证**和**因果推断**。

基于五层逻辑审查框架，按实施成本与发现问题概率排序：

| 优先级 | 审查层级 | 成本 | 发现问题概率 | 与现有系统的关联 |
|:-------|:---------|:-----|:-------------|:-----------------|
| **P0** | 输入敏感性消融实验 | 低 | 中 | 直接复用 EvaluationChain，与刚实现的 risk_tag 闭环 |
| **P0** | 宏观行为场景测试 | 低 | 高 | 需要构造场景数据集，可嵌入演化后验证流水线 |
| **P1** | 鲁棒性（对抗样本/缺失值） | 中 | 中 | 需要数据扰动层，可复用现有测试基础设施 |
| **P1** | 局部可解释性（SHAP） | 中 | 高 | 需要引入 SHAP 依赖，对极端样本分析 |
| **P2** | 因果结构（自然实验） | 高 | 低 | 需要计量经济学方法，依赖历史事件数据 |
| **P0** | 持续监控仪表盘 | 中 | — | 整合到现有健康检查，新增报警规则 |

---

## 2. 实施路线图

```
Phase A（当前，v1.10.0）          Phase B（短期，v1.11.0）          Phase C（中期，v2.0.0）
├── A.1 输入敏感性消融实验         ├── B.1 局部可解释性分析           ├── C.1 因果结构审查
├── A.2 宏观行为场景测试           ├── B.2 鲁棒性审查                ├── C.2 持续监控仪表盘
├── A.3 风险标签闭环验证           └── B.3 文档 + 测试更新            └── C.3 文档 + 测试更新
└── A.4 文档 + 测试更新
```

---

## 3. Phase A — 立即实施（v1.10.0）

### A.1 输入敏感性消融实验

**目标**: 实现自动化特征消融实验框架，检测因子是否依赖不该依赖的变量。

**设计**:
- 新增 `AblationExperiment` 类，对输入特征做系统化扰动
- 五种消融模式：
  1. **成交量置零**（volume → 0）— 检测量价因子是否真实依赖成交量
  2. **VWAP 替换为 close** — 检测 VWAP 近似是否引入冗余信息
  3. **VWAP 替换为 settle** — 检测结算价是否等价于 VWAP
  4. **时间戳打乱**（shuffle dates）— 检测是否依赖时序因果而非截面相关
  5. **单特征归零** — 逐个特征置零，检测 IC 变化

**改动文件**:
- `fts/factor_engine/ablation.py` — 新增消融实验模块
- `fts/factor_engine/__init__.py` — 导出 AblationExperiment

**核心接口**:
```python
class AblationResult(TypedDict):
    factor_id: str
    baseline_ic: float
    baseline_sharpe: float
    ablations: list[SingleAblation]  # 每种消融模式的 IC/Sharpe 变化

class AblationExperiment:
    def run(self, factor: FactorProgram, data: pd.DataFrame) -> AblationResult: ...
    def run_batch(self, factors: list[FactorProgram], data: pd.DataFrame) -> list[AblationResult]: ...
    def report(self, results: list[AblationResult]) -> str: ...  # 生成可读报告
```

**验证标准**:
- 消融实验可对任意 FactorProgram 执行
- 输出包含 baseline IC 和每种消融后的 IC 变化
- 对 vwap 相关因子，成交量置零后 IC 应显著下降
- 测试覆盖所有 5 种消融模式

### A.2 宏观行为场景测试

**目标**: 构造 20~30 个手工标注的"典型市场片段"，验证模型预测是否与经济学直觉一致。

**设计**:
- 新增场景数据集 `tests/scenarios/` 目录，包含 JSON 描述的场景
- 每个场景包含：市场状态描述、输入数据、期望行为、容差范围
- 实现 `ScenarioValidator` 验证器，对每个场景运行因子并检查输出是否符合预期

**场景类型**:

| 场景 | 输入特征 | 期望行为 | 容差 |
|:-----|:---------|:---------|:-----|
| 连续上涨后放量 | 5 日涨幅 > 5% + 成交量 > 均值 2 倍 | 信号应回落（均值回归）或保持（动量），不能无变化 | 信号变化 > 0.1 |
| 突发利空（价格跳空低开） | 当日开盘 < 前日收盘 2% | 信号应转负或降低 | 信号 < 0.3 |
| 低流动性品种 | 成交量 < 10 分位 | 信号绝对值应偏低 | 信号 < 0.5 |
| 换月日前后 | 换月日标记 | 信号不应因换月剧烈突变 | 前后信号差 < 0.3 |
| 横盘震荡 | 20 日波动率 < 20 分位 | 信号应接近 0 或小幅波动 | 信号绝对值 < 0.3 |

**改动文件**:
- `tests/scenarios/__init__.py` — 场景包
- `tests/scenarios/definitions.py` — 场景定义（20+ 场景）
- `tests/scenarios/validator.py` — 场景验证器
- `tests/scenarios/test_scenarios.py` — 场景测试用例

**验证标准**:
- 每个场景可独立运行
- 场景验证器输出通过/失败统计
- 测试覆盖所有场景类型

### A.3 风险标签闭环验证

**目标**: 验证刚实现的 `risk_tag` 机制（vwap_approx 因子 IC 阈值提升至 0.08）是否有效工作。

**设计**:
- 构造一个已知包含 vwap 表达式的种子因子
- 构造一个不含 vwap 的种子因子
- 验证 loader 正确设置 risk_tag
- 验证 evolution_loop 晋升时对 vwap_approx 因子施加更高阈值

**改动文件**:
- `tests/factor_engine/test_risk_tag.py` — 新增风险标签闭环测试

**验证标准**:
- loader 对含 vwap 表达式因子标记 `risk_tag="vwap_approx"`
- loader 对不含 vwap 表达式因子不标记 risk_tag
- `_evaluate_and_promote_seeds` 对 vwap_approx 因子跳过 abs(IC) < 0.08 的晋升

### A.4 文档与测试更新

**改动文件**:
- `docs/harness/01-architecture.md` — 新增消融实验模块架构
- `docs/harness/06-testing.md` — 更新测试用例数（新增消融测试 + 场景测试 + 风险标签测试）
- `docs/harness/07-operations.md` — 追加版本历史 v1.10.0
- `docs/harness/08-gap-analysis.md` — 登记新差距（如有）
- `docs/harness/09-advancement-plan.md` — 追加 v1.10.0 里程碑
- `docs/harness/plans/11-logic-review-plan.md` — 更新本文件状态为"执行中"
- `pyproject.toml` — 版本号 bump 至 1.10.0
- `README.md` — 同步测试数和版本号

---

## 3.5 Phase A 完成情况

| 任务 | 文件 | 状态 | 完成日期 |
|:-----|:-----|:-----|:---------|
| A.1 输入敏感性消融实验 | `fts/factor_engine/ablation.py` | ✅ 完成 | 2026-08-04 |
| A.2 宏观行为场景测试 | `tests/scenarios/definitions.py`, `validator.py`, `test_scenarios.py` | ✅ 完成 | 2026-08-04 |
| A.3 风险标签闭环验证 | `tests/factor_engine/test_risk_tag.py` | ✅ 完成 | 2026-08-04 |
| A.4 文档 + 测试更新 | 架构/测试/运营文档，pyproject.toml v1.10.0 | ✅ 完成 | 2026-08-04 |

**验证结果**:
- 消融实验框架可运行，支持 5 种消融模式
- 23 个宏观行为场景定义，场景验证器通过
- risk_tag 闭环验证通过（vwap_approx 因子 IC 阈值 0.08）
- 所有测试全绿（1700+ passed）

---

## 3.6 Phase B 完成情况

| 任务 | 文件 | 状态 | 完成日期 |
|:-----|:-----|:-----|:---------|
| B.1 局部可解释性分析（SHAP） | `fts/factor_engine/shap_analyzer.py` | ✅ 完成 | 2026-08-04 |
| B.2 鲁棒性审查 | `fts/factor_engine/robustness.py` | ✅ 完成 | 2026-08-04 |
| B.3 文档 + 测试更新 | 架构/测试/运营文档，pyproject.toml v1.11.0 | ✅ 完成 | 2026-08-04 |

**验证结果**:
- SHAP 分析器可对任意 FactorProgram 执行 KernelExplainer 分析，输出 top-5 特征及 JSON 报告持久化
- 鲁棒性审查覆盖 4 种对抗样本扰动 + 3 种缺失值比例 + 4 种分布外场景
- 34 个新测试用例（SHAP 14 + 鲁棒性 20），1750+ 测试全绿
- robustness.py 100% 覆盖率

---

## 3.7 Phase C 完成情况

| 任务 | 文件 | 状态 | 完成日期 |
|:-----|:-----|:-----|:---------|
| C.1 因果结构审查 | `fts/factor_engine/causal_validator.py` + `tests/scenarios/natural_experiments.py` | ✅ 完成 | 2026-08-04 |
| C.2 持续监控仪表盘 | `fts/monitor/logic_monitor.py` | ✅ 完成 | 2026-08-04 |
| C.3 文档 + 测试更新 | 架构/测试/运营文档，pyproject.toml v2.0.0 | ✅ 完成 | 2026-08-04 |

**验证结果**:
- 因果验证器通过自然实验事件验证因子因果关系，支持 3σ 异常检测和方向一致性校验
- 逻辑监控覆盖因子行为漂移检测、极端预测占比报警、换月日信号异常检测
- 114 个逻辑审查相关测试全绿通过（消融 30 + 场景 20 + SHAP 14 + 鲁棒性 20 + 因果 10 + 监控 14 + 风险标签 6）
- ablation.py 98% 覆盖率，robustness.py 100% 覆盖率，shap_analyzer.py 96% 覆盖率
- 已知问题修复：vwap_approx 因子 IC 阈值判断 mock 目标修正、消融实验 volume_zero 依赖修正、空数据场景异常保护、forward_returns 长度匹配

---

## 4. Phase B — 短期（v1.11.0）

### B.1 局部可解释性分析

**目标**: 引入 SHAP 分析，对极端预测样本进行特征归因。

**设计**:
- 新增 `fts/factor_engine/shap_analyzer.py`
- 找出因子预测收益最高的前 100 个样本和最低的前 100 个样本
- 对这些样本计算 SHAP 值，列出 top-5 贡献特征
- 输出可读报告到 `reports/{date}/shap_analysis_{factor_id}.json`

**改动文件**:
- `fts/factor_engine/shap_analyzer.py` — 新增 SHAP 分析器
- `pyproject.toml` — 添加 `shap` 依赖

**验证标准**:
- 对任意 FactorProgram 可执行 SHAP 分析
- 输出包含 top-5 特征及其 SHAP 值
- 极端样本的 SHAP 归因具有经济意义

### B.2 鲁棒性审查

**目标**: 测试因子在边缘情况下是否崩溃。

**设计**:
- **对抗样本测试**: 对输入施加微小扰动（价格 × 1.0001），观察 IC 变化
- **缺失值测试**: 随机删除 5%/10%/20% 数据，观察预测稳定性
- **分布外测试**: 将因子应用到不同品种/市场，观察 IC 保持性

**改动文件**:
- `fts/factor_engine/robustness.py` — 新增鲁棒性测试模块
- `tests/factor_engine/test_robustness.py` — 鲁棒性测试用例

**验证标准**:
- 微小扰动下 IC 变化 < 0.01
- 缺失 10% 数据时 IC 保持 > 80%
- 分布外 IC 下降不超过 50%

---

## 5. Phase C — 中期（v2.0.0）

### C.1 因果结构审查

**目标**: 通过自然实验和反事实测试，验证因子是否捕捉到真正的因果关系。

**设计**:
- 在历史上标记"自然实验"事件（熔断、涨跌停板打开、主力合约切换日）
- 在这些事件前后计算因子预测误差的异常程度
- 若 |预测误差| > 3σ 且与事件方向一致，标记为"事件敏感"

**改动文件**:
- `fts/factor_engine/causal_validator.py` — 新增因果验证器
- `tests/scenarios/natural_experiments.py` — 自然实验事件定义

**验证标准**:
- 对每个自然实验事件输出预测误差分析
- 事件敏感因子通过 risk_tag 标记

### C.2 持续监控仪表盘

**目标**: 建立一套逻辑监控仪表盘，每天自动运行检查。

**设计**:
- 复用现有 `monitor/` 模块，扩展以下检查项：
  1. **因子行为漂移检测** — 计算因子输出与经典逻辑基准（简单动量、均值回归）的相关性变化
  2. **极端预测占比** — 连续信号：z-score 归一后 |z|>2 样本占比超阈值报警；离散信号（唯一值 ≤20，如突破因子 {-1,0,+1}）：主导档位占比 ≥95% 退化报警（v2.104.0+72 双口径，修复离散信号 z-score 系统性误报）
  3. **换月日信号异常报警** — 换月日前后信号均值/方差与历史对比，超 3σ 报警
- 接入现有 APScheduler 调度任务，每日收盘后运行

**改动文件**:
- `fts/monitor/logic_monitor.py` — 新增逻辑监控模块
- `fts/monitor/__init__.py` — 导出 LogicMonitor
- `fts/scheduler/tasks.py` — 添加逻辑监控定时任务

**验证标准**:
- 监控项可独立运行
- 报警阈值可配置
- 换月日异常检测覆盖所有主力合约切换

#### C.2.1 极端预测检测 · 双口径对比（v2.104.0+72）

> 修复背景：离散三态信号（如突破因子 {-1,0,+1}）经 z-score 归一后全部非零档位天然落入
> |z|>2 极端区（std≈0.47 而档位间距=1.0），产生系统性误报（2026-08-16 能源链
> `fct_211b96d7 fut_price_volatility_breakout` 实测 22.1% 误报，根因为离散信号固有属性
> 而非因子失效）。修复后按信号类型分派检测口径，离散信号不再用 σ 距离衡量。

| 维度 | 连续信号因子 | 离散信号因子 |
|------|:---:|:---:|
| **信号特征** | 连续取值（收益率、动量、回归残差等）| 有限档位（如突破因子 `{-1,0,+1}`、二元信号）|
| **判定条件** | 唯一值数量 > 20（`discrete_nunique_threshold`）| 唯一值数量 ≤ 20 |
| **检测口径** | z-score 归一化 | 主导档位退化检测 |
| **核心指标** | \|z\|>2 的样本占比 | 占比最高的单一档位占比（`dominant_ratio`）|
| **告警条件** | `extreme_ratio > 5%`（`extreme_ratio_threshold`）| `dominant_ratio ≥ 95%`（`discrete_dominant_threshold`）|
| **告警语义** | 信号产生异常极端预测（偏离正常波动）| 信号退化为近常数（因子失效/失去区分度）|
| **正常态** | \|z\|>2 占比 ≈ 4.6%（正态本底）| 档位合理分布（有触发有沉寂），如 0 档 78% |
| **阈值默认值** | 0.05 | 0.95 |
| **可配置性** | `LogicMonitor(extreme_ratio_threshold=...)` | `LogicMonitor(discrete_dominant_threshold=...)` |
| **结果字段** | `method="zscore"`, `extreme_positive/negative`, `extreme_ratio` | `method="discrete"`, `dominant_ratio`, `discrete_nunique` |
| **典型误报场景** | — | 离散信号 z-score 后全部非零档位天然 \|z\|>2（旧口径 22.1% 误报）|
| **阈值类型** | 超阈值告警（>）| 达阈值告警（≥）|

**检测流程对照**:

| 步骤 | 连续信号 | 离散信号 |
|------|:---:|:---:|
| 1. 信号有效样本提取 | 去除 NaN | 去除 NaN |
| 2. 类型判定 | `nunique > 20` → 连续分支 | `nunique ≤ 20` → 离散分支 |
| 3. 计算 | z = (x−μ)/σ，统计 \|z\|>2 占比 | 统计各档位占比，取 max 为 dominant_ratio |
| 4. 告警判定 | `ratio > threshold(0.05)` | `dominant_ratio ≥ threshold(0.95)` |
| 5. 结果标注 | `method="zscore"` | `method="discrete"` |
| 6. 报告展示 | 极端正/负向数量 + 占比 | 主导档位占比 + 唯一值数量 |

**告警处置对照**:

| 场景 | 连续信号告警处置 | 离散信号告警处置 |
|------|------|------|
| 触发条件 | 极端占比 >5%（真实极端行情/因子结构突变）| 主导档位 ≥95%（信号退化）|
| 归因 | 检查极端行情窗口、因子方差突变 | 检查是否输出恒定（失效/参数不触发）|
| 处置 | 归因 → 调参 / 衰减跟踪 | 归因 → 重估 / 退役（`auto_retire`）|
| 跟踪登记 | `logic_alerts` 写入 EliteTracker 快照 | 同左 |

**口径细节说明**（归档注意）:
- 离散口径的 `extreme_ratio` 字段承载的是 **dominant_ratio**（主导档位占比）而非极端占比，`threshold` 字段为 0.95（退化阈值）——与连续口径字段名相同但语义不同。
- 双阈值（`discrete_nunique_threshold=20` / `discrete_dominant_threshold=0.95`）均为构造可覆盖参数，非硬编码；检测逻辑读 `self._discrete_*` 实例属性。

---

## 6. 验收标准

| 阶段 | 标准 |
|:-----|:-----|
| Phase A | 消融实验框架可运行，20+ 场景测试通过，risk_tag 闭环验证通过，所有测试全绿 |
| Phase B | SHAP 分析可执行并输出可读报告，鲁棒性测试覆盖对抗样本/缺失值/分布外，所有测试全绿 |
| Phase C | 因果验证器对自然实验事件输出分析，持续监控仪表盘每日自动运行，所有测试全绿 |

---

## 7. 依赖与风险

| 依赖 | 类型 | 风险级别 | 缓解措施 |
|:-----|:-----|:---------|:---------|
| SHAP 库 | 外部 Python 包 | 低 | `pip install shap`，已广泛使用 |
| 场景数据真实性 | 内部 | 中 | 场景数据从真实历史片段提取，非人工构造 |
| 自然实验事件标记 | 内部 | 中 | 从历史数据自动标记（熔断日、涨跌停等） |
| 回测性能 | 内部 | 低 | 消融实验复用现有 EvaluationChain，性能可接受 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件定义 FTS 五层逻辑审查路线图（Phase A/B/C），对应 `ablation.py`、`tests/scenarios/`、`shap_analyzer.py`、`robustness.py`、`causal_validator.py`、`monitor/logic_monitor.py` 的实施计划 |
| 可验证断言 | Phase A: 消融实验 + 场景测试 + 风险标签闭环；Phase B: SHAP 分析 + 鲁棒性审查；Phase C: 因果验证 + 逻辑监控；全部 114 个相关测试全绿 |
| 检验方式 | 运行 `python -m pytest tests/factor_engine/test_ablation.py tests/scenarios/ tests/factor_engine/test_shap_analyzer.py tests/factor_engine/test_robustness.py tests/factor_engine/test_causal_validator.py tests/monitor/test_logic_monitor.py tests/factor_engine/test_risk_tag.py -v` |