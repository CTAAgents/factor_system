# 41 — OWL 因子分组筛选计划（方案 A：L3 Step 1.8 旁路验证）


> 版本: v2.104.0+113

> 状态: 已实施（2026-08-17） · 优先级: P2 · 负责人: FTS Agent
> 关联: plans/36, plans/40, GAP-I206, factor_clustering, l3_signal_service
> 上游: docs/harness/design/OWL-factor-screening-evaluation.md（评估报告，2026-08-16）

## 一、背景与目标

### 1.1 背景

FTS 现有 4 条因子去冗余链路（L2 相关性预检+正交化 / L3 Step 1.8 信号聚类 / Step 1.8b 子链去冗余 / Step 1.9 PCA）**全部基于信号序列相关性/方差结构**，没有使用横截面收益-载荷信息。OWL（Ordered Weighted L1）正则化估计器能在回归估计过程中同时完成"稀疏化 + 分组化"：把强相关因子的系数拉平（自动分组），并直接回答"哪组因子对横截面收益有独立解释力"（Cochrane 2011 问题）。

评估报告（`docs/harness/design/OWL-factor-screening-evaluation.md`）结论：技术可行（cvxpy 1.9.2 已装）、数据就绪（plans/40 `l3_signal_service` 已产出 2D 信号矩阵 + 前向收益矩阵）、推荐**方案 A（L3 Step 1.8 旁路验证，开关化，P2）**。

### 1.2 目标

按方案 A 落地 OWL 因子分组筛选的**旁路验证**能力：

1. 蒙特卡洛对照实验（`scripts/owl_sim_validation.py`）：构造已知分组的相关因子，验证 OWL 分组还原率——对齐文章 §2.4 结论（OWL 是唯一能把强相关因子聚成组并给相似系数的估计器）。
2. OWL 求解器封装（`fts/factor_engine/owl_factor_selector.py`）：cvxpy 建模 + 权重向量生成 + 两阶段组内正交检验。
3. L3 Step 1.8 旁路接入：`l3.owl.enabled` 开关（默认 false），启用时 OWL 输出"定价因子簇"，与信号聚类结果交叉比对。
4. 配套测试（`tests/factor_engine/test_owl_factor_selector.py` + `tests/factor_engine/test_owl_l3_integration.py`）。

## 二、方案设计

### 2.1 模块结构

```
fts/factor_engine/owl_factor_selector.py   # 新增：OWL 求解器 + 权重生成 + 两阶段检验
scripts/owl_sim_validation.py              # 新增：蒙特卡洛分组还原率验证
fts/factor_engine/portfolio_loop.py        # 修改：Step 1.8 后接入 OWL 旁路（开关控制）
config/settings.yaml                       # 修改：l3.owl.* 配置段
tests/factor_engine/test_owl_factor_selector.py   # 新增：OWL 单元/边界测试
tests/factor_engine/test_owl_l3_integration.py    # 新增：L3 旁路集成测试
```

### 2.2 OWL 求解器（`owl_factor_selector.py`）

```python
class OwlFactorSelector:
    """OWL（Ordered Weighted L1）因子分组筛选器（plans/41 方案 A）。

    OWL 正则项 Ω_w(x) = Σ_i w_i |x|_[i]（|x|_[i] 为系数绝对值降序第 i 个）：
    大的系数承受更大惩罚，强 Schur 凸性使强相关变量系数拉平 → 自动分组。
    cvxpy 建模（依赖已装 1.9.2）；cvxpy/scipy 缺失时回退 None（零漂移）。
    """

    def __init__(
        self,
        weight_scheme: str = "linear",     # linear / exp / log 权重衰减
        weight_tuning: float = 0.5,        # 衰减强度（0 退化全等权=LASSO 变体）
        train_frac: float = 0.7,           # 样本外切割：训练窗拟合 OWL
        corr_group_threshold: float = 0.5, # 系数分组：|corr|>=0.5 视为同组
    ):
        ...

    def fit_group(
        self,
        signal_matrix: np.ndarray,   # X (n_dates, n_factors) 标准化后
        forward_returns: np.ndarray, # y (n_dates,) 横截面平均前向收益
    ) -> dict[str, Any]:
        """OWL 拟合 + 系数分组。

        Returns:
            dict: beta（OWL 系数）、groups（[[因子索引]...]）、
                  nonzero_factors、train_frac 等
        """
        # 1. 时间切割：train_frac 训练窗拟合，预留检验窗
        # 2. 截面标准化（z-score，NaN 置 0，与 PCA 同口径）
        # 3. cvxpy 建模：
        #      minimize 0.5*||X@beta - y||² + λ * Σ_i w_i * sort_abs(beta)[i]
        #      sort_abs 用 cvxpy.sum_largest(cp.abs(beta), k) 差分实现有序加权
        # 4. 按 |beta|>0 得非零因子；对非零因子两两 |corr| 聚类成组
        ...

    def group_orthogonal_test(
        self,
        groups: list[list[int]],
        signal_matrix: np.ndarray,
        forward_returns: np.ndarray,
    ) -> dict[str, Any]:
        """第二阶段：组内正交检验（Cochrane 2011 思路）。

        对每组因子做多变量回归 y ~ X_group，检验联合解释力 F 统计量
        + 各组间正交化残差相关；输出显著组（保留）与非显著组（剔除建议）。
        多组比较 Bonferroni/FDR 校正（AGENTS.md §4.1 多重检验铁律）。
        """
        ...
```

**关键实现要点**：

1. **有序加权正则的 cvxpy 建模**：`Σ_i w_i |x|_[i]`（降序）可用 `cp.sum(cp.multiply(w_sorted_desc, cp.sort(cp.abs(beta), descending=True)))` 直接表达（cvxpy 支持 `sort` 算子 + 凹/凸性自动推导）。需验证 cvxpy 1.9.2 对该表达式的 DCP 支持；不支持则用 `cp.sum_largest(cp.abs(beta), k)` 差分：`Σ_{i=1}^{k} x_[i] = sum_largest(x, k)`，`w` 递减差分 `w_i - w_{i+1} >= 0` 时等价表达。
2. **样本外纪律**：OWL 系数估计只用 `train_frac` 前段数据，检验窗数据仅用于验证分组稳定性——禁止全样本拟合（数据窥探红线）。
3. **λ 选择**：`lambda_` 参数化（默认与 `weight_tuning` 联动），或用简单的 BIC/信息准则近似；不做网格搜索过拟合。
4. **零漂移回退**：cvxpy 导入失败 / 求解异常 / 输入非法 → 返回 None 或空结构，调用方跳过（与 PCA/聚类既有"非致命"模式一致）。

### 2.3 蒙特卡洛验证（`scripts/owl_sim_validation.py`）

| 实验 | 构造 | 断言目标 |
|------|------|---------|
| 强相关组还原 | 3 组相关因子（组内 corr 0.7，组间 0.1）+ 噪声 | OWL 组内系数相近、组间分离；分组还原率 ≥ 0.8 |
| 稀疏筛选 | 90 候选（部分冗余）+ 少量真因子 | 非零因子数显著 < 候选数，真因子全保留 |
| 与 LASSO/EN 对比 | 同上 | OWL 分组误差 < LASSO/ElasticNet（对齐文章 §2.4） |
| 样本外稳定性 | 前后段分裂 | 训练/检验窗分组 Jaccard 重合度 ≥ 0.7 |

### 2.4 L3 旁路接入（`portfolio_loop.py`）

- **位置**：Step 1.8（信号聚类）之后、Step 1.9（PCA）之前。
- **配置**：`config/settings.yaml` 新增：

```yaml
l3:
  owl:
    enabled: false          # 默认关闭（旁路验证，不改现行为）
    weight_scheme: linear
    weight_tuning: 0.5
    train_frac: 0.7
    group_corr_threshold: 0.5
    lambda_: 0.05           # 0.5/n 归一尺度（与 sklearn Lasso 对齐）
    report_only: true       # true=仅输出交叉比对报告，不改 factors 列表
```

- **行为**（`enabled=false` 时零开销零行为变更）：
  - 复用 `l3_signal_service.build_signal_matrix`（plans/40 B 层）产出 2D 信号矩阵 + 前向收益 → 构造 OWL 输入（复用 A 层缓存，不重复重算）。
  - `report_only=true`（默认）：OWL 结果仅写日志 + 落盘报告 `memory/portfolio/{universe}/owl_report_{date}.json`，**不修改 factors 列表**。
  - `report_only=false`（可选进阶）：OWL 判为非显著组的因子从 factors 剔除（**本期不实现**，仅预留契约，避免越界改动主链路）。
- **交叉比对输出**：信号聚类剔除∩OWL 保留 → 提示人工复核（"信号相关但定价独立"）；两套都剔除 → 强冗余。

### 2.5 契约（HARNESS §契约优先）

```python
@dataclass
class OwlSelectionResult:
    applied: bool                    # OWL 是否成功执行
    beta: np.ndarray | None          # OWL 系数 (n_factors,)
    groups: list[list[int]]          # 因子分组（按 |beta|>0 且 |corr|>=thr）
    significant_groups: list[list[int]]  # 组内正交检验显著的组
    nonsignificant_factors: list[str]    # 建议剔除因子 ID
    train_frac: float
    report_path: str | None          # 落盘报告路径
```

## 三、测试计划

| 文件 | 覆盖 |
|------|------|
| `test_owl_factor_selector.py` | OWL 求解正确性（构造已知 beta 的小样本，系数与解析/数值解一致或相对误差 ≤1e-3）；有序加权降序惩罚生效（大系数被压更多）；分组还原（组内 corr 高 → 同组）；组内正交检验显著/非显著判定；多重检验 Bonferroni 校正；样本外切割 train_frac 生效；空/全 NaN/单因子/超短样本回退 None；cvxpy 缺失降级（monkeypatch 导入失败）；w=全等权退化行为 |
| `test_owl_l3_integration.py` | L3 旁路：`enabled=false` 零调用零行为变更；`enabled=true+report_only` 走 OWL 不修改 factors、产出报告、复用信号缓存；OWL 求解失败回退不阻断；配置契约加载（settings.yaml l3.owl） |

## 四、验收标准

1. 蒙特卡洛：强相关组还原率 ≥0.8；稀疏筛选真因子全保留；OWL 分组误差 < LASSO/ElasticNet（`scripts/owl_sim_validation.py` 输出报告）。
2. OWL 求解器单测全绿（含零漂移回退路径）。
3. L3 旁路 `enabled=false` 时现行为零变更（既有 `test_portfolio_loop` 全绿）。
4. 交叉比对报告落盘 `memory/portfolio/{universe}/owl_report_{date}.json`。
5. 受影响模块回归通过 + ruff/mypy 通过。

## 五、风险与回退

- **cvxpy 有序加权表达不支持**：DCP 校验失败 → 用 `cp.sum_largest` 差分等价式，仍失败则回退 None（旁路不阻断）。
- **OWL 结果与信号聚类冲突**：默认 `report_only=true` 只报告不裁决，避免越界改动主链路。
- **计算开销**：旁路仅在小因子池（≤ 数百）上运行，秒级可接受；不做 Safe Screening（本期不需要）。
- **数据窥探**：OWL 系数严格只用训练窗；检验窗仅验证稳定性。

## 六、版本记录

| 版本 | 变更 | 日期 |
|------|------|------|
| v1.0.0 | 计划创建（方案 A：OWL 求解器 + 蒙特卡洛验证 + L3 旁路接入） | 2026-08-16 |
| v1.1.0 | 实施完成：求解器/蒙特卡洛/旁路/测试全绿；修复 2 处集成 bug（模块级函数显式传 self、state 写局部变量）；计划配置段对齐实现（group_corr_threshold + lambda_） | 2026-08-17 |
