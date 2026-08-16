# A.2 因子衰减追踪与自动淘汰 — 详细技术设计

> 版本: v2.104.0+97
> 关联: [11-factor-mining-optimization-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/11-factor-mining-optimization-plan.md) → Phase A.2
> 状态: **部分实现**（`fts/monitor/elite_tracker.py`）
> 实现说明: 生命周期管理以 `EliteFactorTracker` + `AutoRetireManager`（`fts/monitor/elite_tracker.py`）实现，持久化为 JSON 快照（`memory/tracking/{factor_id}.json`），**未**采用原设计的 DuckDB `factor_status_history` 表与 `FactorDecayTracker` 类；`factor_catalog` 状态字段扩展未实现。

---

## 1. 目标与范围

建立因子**生命周期管理**机制：
- 自动识别衰减因子，触发降级或淘汰
- 为每个因子维护状态机（active → decaying → critical_deprecated）
- 定期增量重评估，输出因子衰减报告
- 支持被淘汰因子的历史查询和回滚恢复

**范围**:
- 因子状态机设计
- 衰减判定算法
- 自动淘汰工作流
- 与 `elite_tracker.py` 和 `factor_db` 的集成

---

## 2. 数据模型设计

### 2.1 因子状态枚举

> **实现现状**: `EliteFactorTracker`（`fts/monitor/elite_tracker.py`）定义了 7 状态生命周期，状态枚举为 `Literal` 类型而非 `str Enum`。**缺少**原设计中的 `DELETED` 状态；新增 `RETIRED`（自动淘汰）与 `REJECTED`（准入被拒）状态。

```python
FactorStatus = Literal[
    "active",           # 活跃
    "observing",        # 观察期 (B级因子)
    "decaying",         # 衰减中
    "critical_decay",   # 严重衰减
    "retired",          # 已淘汰
    "deprecated",       # 已废弃 (保留历史)
    "rejected",         # 被拒绝准入
]
"""因子生命周期状态。"""
```

**实际状态转移规则**（`EliteFactorTracker._check_state_transition`）:

```mermaid
stateDiagram-v2
    [*] --> active: A级准入 (score>=40)
    [*] --> observing: B级准入 (30<=score<40)
    [*] --> rejected: C级 (score<30)
    
    observing --> active: 观察期结束且 quality_score>=B 阈值
    observing --> decaying: 观察期结束未达标
    
    active --> decaying: 连续3月IC<0 或 周度连续4周零IC
    active --> critical_decay: 连续6月Sharpe降>50%
    decaying --> critical_decay: 连续6月Sharpe降>50%
    
    active/decaying --> retired: AutoRetireManager 自动淘汰
    retired/deprecated --> active: 冷却期后可重新评估
```

**分级准入阈值**（`GradeThreshold`）: A级≥40 / B级≥30 / 观察期 3 个月 / 连续 IC<0 3 个月判定衰减 / 连续 Sharpe 下降 6 个月判定严重衰减。

### 2.2 Schema 扩展

> **实现现状**: **未实现**。实际持久化为 JSON 快照：`memory/tracking/{factor_id}.json`（`TrackingSnapshot` dict，含 `status`/`consecutive_zero_months`/`consecutive_sharpe_decline_months`/`observation_end` 等字段），由 `EliteFactorTracker` 通过 `atomic_read`/`atomic_write` 维护。以下 DuckDB 表扩展为预留设计。

在 `factor_catalog` 表上扩展状态追踪字段：

```sql
-- factor_catalog 表扩展
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active';
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMP;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS consecutive_ic_negative_months INT NOT NULL DEFAULT 0;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS consecutive_sharpe_drop_months INT NOT NULL DEFAULT 0;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS last_incremental_eval_at TIMESTAMP;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS decay_rate_3m DOUBLE;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS decay_rate_6m DOUBLE;

-- 索引
CREATE INDEX IF NOT EXISTS idx_fc_status ON factor_catalog(status);
```

新增 `factor_status_history` 表记录状态变迁历史：

```sql
CREATE TABLE IF NOT EXISTS factor_status_history (
    history_id      VARCHAR(36) PRIMARY KEY,
    factor_id       VARCHAR(36) NOT NULL,
    from_status     VARCHAR(20) NOT NULL,
    to_status       VARCHAR(20) NOT NULL,
    reason          VARCHAR(200) NOT NULL,
    changed_at      TIMESTAMP NOT NULL,
    snapshot       JSON NOT NULL,
    FOREIGN KEY (factor_id) REFERENCES factor_catalog(factor_id)
);

CREATE INDEX IF NOT EXISTS idx_fsh_factor_id ON factor_status_history(factor_id);
CREATE INDEX IF NOT EXISTS idx_fsh_changed_at ON factor_status_history(changed_at);
```

### 2.3 核心类型

```python
class DecayConfig(TypedDict, total=False):
    """衰减追踪配置。"""
    eval_interval_days: int                # 增量评估间隔 (默认 30)
    ic_negative_threshold: float           # IC 负值阈值 (默认 0)
    ic_negative_months_decay: int         # 连续几月 IC < 0 标为 decaying (默认 3)
    sharpe_drop_ratio_threshold: float     # Sharpe 下降比例 (默认 0.5)
    sharpe_drop_months_critical: int       # 连续几月 Sharpe 降标为 critical (默认 6)
    no_improvement_months_deprecated: int  # 无改善几月淘汰 (默认 12)
    retention_months_deprecated: int       # 淘汰保留期 (默认 24)
    min_observance_months: int             # 最少观察期 (默认 3)


class DecayEvalResult(TypedDict, total=False):
    """单次衰减评估结果。"""
    factor_id: str
    current_status: FactorStatus
    proposed_status: FactorStatus
    ic_trend: Literal['improving', 'stable', 'deteriorating']
    sharpe_trend: Literal['improving', 'stable', 'deteriorating']
    decay_rate_3m: float
    decay_rate_6m: float
    actions: list[str]                     # ['降级为 decaying', '触发淘汰流程']
    reason: str
    snapshot: dict
```

---

## 3. 衰减判定算法

### 3.1 IC 趋势分析

```python
def analyze_ic_trend(monthly_ics: list[float],
                     negative_threshold: float = 0) -> dict:
    """分析 IC 趋势。

    Returns:
        {
            'trend': 'improving' | 'stable' | 'deteriorating',
            'consecutive_negative_months': int,
            'avg_ic_3m': float,
            'avg_ic_6m': float,
        }
    """
    if len(monthly_ics) < 3:
        return {'trend': 'insufficient_data', ...}

    # 计算连续 IC < 0 的月份数
    consecutive_negative = 0
    for ic in reversed(monthly_ics):
        if ic < negative_threshold:
            consecutive_negative += 1
        else:
            break

    # 近 3/6 月平均
    avg_3m = sum(monthly_ics[-3:]) / 3
    avg_6m = sum(monthly_ics[-6:]) / min(6, len(monthly_ics))

    # 趋势判定
    if consecutive_negative >= 3:
        trend = 'deteriorating'
    elif avg_3m > avg_6m * 1.1:
        trend = 'improving'
    elif avg_3m < avg_6m * 0.9:
        trend = 'deteriorating'
    else:
        trend = 'stable'

    return {...}
```

### 3.2 Sharpe 衰减率计算

```python
def compute_sharpe_decay_rate(monthly_sharpes: list[float]) -> dict:
    """计算 Sharpe 环比衰减率。

    Returns:
        {
            'decay_rate_3m': float,    # 近 3 月 Sharpe 环比
            'decay_rate_6m': float,    # 近 6 月 Sharpe 环比
            'consecutive_drop_months': int,
            'cumulative_drop': float,
        }
    """
    if len(monthly_sharpes) < 2:
        return {'decay_rate_3m': 0.0, 'decay_rate_6m': 0.0,
                'consecutive_drop_months': 0, 'cumulative_drop': 0.0}

    # 环比变化
    deltas = []
    for i in range(1, len(monthly_sharpes)):
        prev = max(monthly_sharpes[i - 1], 0.001)
        delta = (monthly_sharpes[i] - prev) / prev
        deltas.append(delta)

    # 近 3/6 月平均衰减率
    decay_3m = abs(sum(deltas[-3:]) / max(1, len(deltas[-3:])))
    decay_6m = abs(sum(deltas[-6:]) / max(1, len(deltas[-6:])))

    # 连续下降月数
    consecutive_drop = 0
    for d in reversed(deltas):
        if d < 0:
            consecutive_drop += 1
        else:
            break

    # 累计下降
    cumulative = 1.0
    for d in deltas:
        cumulative *= (1.0 + d)
    cumulative_drop = 1.0 - cumulative

    return {...}
```

### 3.3 状态转移判定

```python
def evaluate_decay(factor: FactorCatalog,
                   monthly_metrics: list[MonthlyMetric],
                   config: DecayConfig) -> DecayEvalResult:
    """综合判定因子衰减状态。"""
    ic_trend = analyze_ic_trend(monthly_metrics.ics)
    sharpe_decay = compute_sharpe_decay_rate(monthly_metrics.sharpes)

    proposed = factor.status
    actions = []
    reason = ""

    # 规则 1: 连续 3 月 IC < 0 → 标记 decaying
    if ic_trend['consecutive_negative_months'] >= config['ic_negative_months_decay']:
        if factor.status == FactorStatus.ACTIVE:
            proposed = FactorStatus.DECAYING
            actions.append('降级为 decaying')
            reason = f"连续 {config['ic_negative_months_decay']} 月 IC < 0"

    # 规则 2: 连续 6 月 Sharpe 下降 > 50% → 标记 critical
    if sharpe_decay['consecutive_drop_months'] >= config['sharpe_drop_months_critical']:
        if sharpe_decay['cumulative_drop'] >= config['sharpe_drop_ratio_threshold']:
            proposed = FactorStatus.CRITICAL
            actions.append('触发 critical 状态')
            reason += f"; 累计 Sharpe 下降 {sharpe_decay['cumulative_drop']:.1%}"

    # 规则 3: 连续 12 月无改善 → 淘汰
    if proposed in (FactorStatus.DECAYING, FactorStatus.CRITICAL):
        if _no_improvement_for_months(factor, monthly_metrics, config):
            proposed = FactorStatus.DEPRECATED
            actions.append('触发淘汰流程')
            reason += "; 长期无改善"

    # 规则 4: 衰减恢复 → 回升
    if factor.status == FactorStatus.DECAYING:
        if ic_trend['consecutive_negative_months'] == 0 and sharpe_decay['decay_rate_3m'] < 0.1:
            proposed = FactorStatus.ACTIVE
            actions.append('恢复为 active')
            reason = "IC 回升且 Sharpe 稳定"

    return DecayEvalResult(
        factor_id=factor.factor_id,
        current_status=factor.status,
        proposed_status=proposed,
        ic_trend=ic_trend['trend'],
        sharpe_trend=sharpe_decay['trend'],
        decay_rate_3m=sharpe_decay['decay_rate_3m'],
        decay_rate_6m=sharpe_decay['decay_rate_6m'],
        actions=actions,
        reason=reason,
        snapshot={...}
    )
```

---

## 4. 接口契约

### 4.1 `EliteFactorTracker` 类（实际实现，替代原设计 `FactorDecayTracker`）

> **实现现状**: 生命周期管理实际由 `fts/monitor/elite_tracker.py` 的 `EliteFactorTracker` 与 `AutoRetireManager` 承担。原设计中的 `FactorDecayTracker` 类未实现。

```python
class EliteFactorTracker:
    """精英因子样本外跟踪器。

    Usage:
        tracker = EliteFactorTracker(tracking_dir="memory/tracking")
        tracker.init_tracker(factor_id, quality_score=42.0, grade="A")
        tracker.update(factor_id, ic=0.03, sharpe=1.8, ...)   # 月度指标更新
        decaying = tracker.get_decaying()                      # 衰减边缘因子
        retired = tracker.auto_retire(...)                     # 自动淘汰
        report = tracker.run_monthly_evaluation()              # 月度评估
    """

    def __init__(self, tracking_dir: str = "memory/tracking",
                 grade_threshold: GradeThreshold | None = None) -> None: ...

    def init_tracker(self, factor_id: str, quality_score: float,
                     grade: FactorGrade, ...) -> None:
        """初始化跟踪记录（晋升精英池时调用）。"""

    def update(self, factor_id: str, ic: float, sharpe: float,
               quality_score: float, ...) -> dict:
        """更新月度指标，触发状态机检查。"""

    def determine_grade(self, quality_score: float) -> FactorGrade:
        """按阈值分级 A/B/C。"""

    def get(self, factor_id: str) -> dict | None: ...
    def list_all(self) -> list[dict]: ...
    def get_decaying(self, max_consecutive: int = 4) -> list[dict]: ...
    def get_by_status(self, status: FactorStatus) -> list[dict]: ...
    def run_monthly_evaluation(self) -> dict:
        """对所有跟踪因子执行月度增量评估。"""
    def report(self) -> dict:
        """生成全量衰减报告。"""


class AutoRetireManager:
    """自动淘汰管理器（替代原设计 permanent_delete_elapsed）。"""

    def run(self) -> list[str]:
        """扫描并淘汰连续零 IC / 6 月衰减率超限的因子。"""
    def can_reevaluate(self, factor_id: str) -> bool:
        """冷却期内不可重新评估（默认 7 天）。"""
```

> **与原设计的差异**:
> - 存储: JSON 快照（`memory/tracking/`）替代 DuckDB `factor_status_history` 表。
> - 淘汰: `AutoRetireManager`（周度零 IC 4 次 / 6 月衰减率 > 30%）替代"连续 12 月无改善"规则。
> - 恢复: 淘汰后经冷却期（7 天）可重新评估，替代"保留期 24 个月 + 手动恢复"。
> - Prometheus 衰减指标（第 6 节）未实现。

### 4.2 `DecayReport` 类型

```python
class DecayReport(TypedDict, total=False):
    """因子衰减报告。"""
    report_id: str
    generated_at: str
    summary: DecaySummary
    factor_changes: list[DecayEvalResult]
    deprecated_factors: list[dict]
    grade_distribution: dict[str, int]


class DecaySummary(TypedDict, total=False):
    total_factors: int
    active_count: int
    observing_count: int
    decaying_count: int
    critical_count: int
    deprecated_count: int
    avg_decay_rate_3m: float
    avg_decay_rate_6m: float
    new_deprecated_this_month: int
    promoted_this_month: int
```

---

## 5. 流程设计

### 5.1 月度增量评估流程

```mermaid
flowchart TD
    A[定时触发: 每月 1 日] --> B[加载所有 active/observing 因子]
    B --> C{遍历每个因子}
    C --> D[计算当月 IC, Sharpe]
    D --> E[更新月度指标序列]
    E --> F[evaluate_decay 判定]
    F --> G{状态变化?}
    G -->|是| H[写 factor_status_history]
    H --> I[更新 factor_catalog 状态]
    I --> J[写 decay 快照到 factor_quality_scores]
    G -->|否| K[跳过]
    J & K --> C
    C -->|完成| L[生成 DecayReport]
    L --> M[发送告警: decaying/critical 因子]
    M --> N[结束]
```

### 5.2 自动淘汰流程

```mermaid
flowchart TD
    A[因子进入 DEPRECATED 状态] --> B[因子从组合池中移除]
    B --> C[保留在 factor_catalog 中（status=deprecated）]
    C --> D[保留期 24 个月]
    D --> E{超过保留期?}
    E -->|是| F[permanent_delete_elapsed 清理]
    F --> G[状态变更为 DELETED]
    E -->|否| H[等待恢复]
    H --> I{手动恢复 or 表现回升?}
    I -->|是| J[promote_deprecated → ACTIVE]
    I -->|否| E
```

### 5.3 与现有系统集成

```
scheduler/  (每月 1 日)
  └── factor_decay_tracker.run_monthly_eval()
        ├── factor_db.repository.load_active_factors()
        ├── data_futures 获取月度数据
        ├── evaluation_chain.run_l1_only()    # 仅 L1 回测，轻量
        ├── evaluate_decay()                 # 衰减判定
        ├── 更新 factor_catalog.status
        ├── 写 factor_status_history
        └── generate_decay_report()
            ├── 推送 Prometheus 指标
            └── 发送告警
```

---

## 6. Prometheus 指标

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `fts_factor_decay_active_count` | Gauge | - | 活跃因子数 |
| `fts_factor_decay_decaying_count` | Gauge | - | 衰减中因子数 |
| `fts_factor_decay_critical_count` | Gauge | - | 严重衰减因子数 |
| `fts_factor_decay_deprecated_count` | Gauge | - | 已淘汰因子数 |
| `fts_factor_decay_rate_3m` | Gauge | `factor_id` | 单因子 3 月衰减率 |
| `fts_factor_decay_evaluations_total` | Counter | `status_before, status_after` | 状态变更次数 |

---

## 7. 技术约束

| 约束 | 说明 |
|------|------|
| **增量评估轻量** | 月度评估仅运行 L1 回测（IC/Sharpe），不运行 L2/L3 |
| **幂等性** | 同一月份重复运行结果一致 |
| **不可变历史** | 状态变更通过 `factor_status_history` 记录，不可修改 |
| **恢复支持** | 废弃因子在保留期内可恢复为 active |
| **性能** | 100 个因子的月度增量评估 < 5 分钟 |
| **告警** | decaying 因子发送 Warning，critical 发送 Critical |

---

## 8. 文件改动清单

| 文件 | 动作 | 现状 | 说明 |
|------|------|------|------|
| `fts/monitor/elite_tracker.py` | **新增** | ✅ 已实现 | `EliteFactorTracker`（状态机/分级准入/月度评估）+ `AutoRetireManager`（自动淘汰） |
| `fts/factor_engine/factor_db/schema.py` | **修改** | ⬜ 未实现 | `factor_catalog` 状态字段 + `factor_status_history` 表未新增 |
| `fts/factor_engine/factor_db/repository.py` | **修改** | ⬜ 未实现 | 衰减追踪 CRUD 未实现（改用 JSON 快照） |
| `fts/monitor/prometheus_metrics.py` | **修改** | ⬜ 未实现 | 衰减追踪 Prometheus 指标未实现（当前为 `prometheus_setup.py`，未含衰减指标） |
| `fts/scheduler/schedules.py` | **修改** | ⬜ 未实现 | 月度增量评估定时任务未实现（月度评估由演化循环 `finally` 块触发） |
| `tests/factor_engine/test_factor_decay_tracker.py` | **新增** | ⬜ 未实现 | 对应测试以 `tests/factor_engine/test_elite_tracker.py` 等实现 |
| `tests/factor_engine/test_decay_state_machine.py` | **新增** | ⬜ 未实现 | 状态机转移测试未单独存在（覆盖于 elite_tracker 测试） |

---

## 9. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | IC 趋势分析正确识别连续负值月份和改善趋势 | 单元测试 |
| 2 | Sharpe 衰减率正确计算 3 月和 6 月环比 | 单元测试 |
| 3 | 状态转移按规则正确执行（active→decaying→critical→deprecated） | 状态机测试 |
| 4 | 状态变更完整记录在 `factor_status_history` 表 | 集成测试 |
| 5 | 废弃因子在保留期内可恢复为 active | 单元测试 |
| 6 | 超过保留期的废弃因子被永久清理 | 单元测试 |
| 7 | 月度增量评估在 5 分钟内完成 | 性能测试 |
| 8 | 衰减报告正确生成，Prometheus 指标正常 | 集成测试 |

---

## 10. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | [11-factor-mining-optimization-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/11-factor-mining-optimization-plan.md) → Phase A.2 |
| 依赖模块 | `factor_db/repository.py`（数据持久化）、`evaluation_chain.py`（L1 回测）、`scheduler/`（定时触发） |
| 前置条件 | `factor_catalog` 表存在，`evaluation_chain.run_l1_only()` 可用 |
| 后置影响 | 因子准入从静态 pass/fail 变为动态生命周期管理 |