# B.3 因子审计流程标准化 — 详细技术设计

> 版本: v2.85.0
> 关联: [11-factor-mining-optimization-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/11-factor-mining-optimization-plan.md) → Phase B.3
> 状态: **已实现**（实现文件与接口与原设计不同）
> 实现说明: 实际实现为 `fts/factor_engine/audit.py`（v0.1.0）的 `FactorAuditor`，6 项审计（`causal_validity`/`oos_consistency`/`cross_symbol`/`stress_resilience`/`multiple_testing`/`snooping_check`）采用**渐进式**接口（每项独立传入数据，缺失时标记 `skipped`，非 skipped 项须全部通过），并新增 `FailureClassifier` 失败模式分类与改善建议。`factor_audit_reports` 表未实现（审计报告以 `FactorAuditReport.to_dict()` 结构化输出）；原设计的 `factor_auditor.py` 文件名、`run_check`/`get_audit_history` 等接口均未实现。

---

## 1. 目标与范围

建立**系统化的因子审计清单**与自动化流程：
- 定义 6 项审计检查，因子入库前必须全部通过
- 审计结果生成标准化报告，可查询和追溯
- 审计结果作为 A.1 因子质量评分卡的输入项

**范围**:
- 审计清单设计（因果检验、样本外、跨品种、压力测试、多重检验、数据窥探）
- `FactorAuditor` 类及接口契约
- 审计报告生成
- 与现有评估链的集成

**不在范围**:
- 审计算法细节实现（复用现有 `causal_validator.py`、`robustness.py` 等）
- 审计流程的人工审核环节

---

## 2. 审计清单设计

### 2.1 六项审计检查

> **实现现状**: 审计项名称与 `fts/factor_engine/audit.py` 的 `FactorAuditor.ITEM_NAMES` 一致。原设计名称映射如下:

| # | 审计项 | 实际名称（audit.py） | 原设计名称 | 依赖模块 |
|---|--------|----------------------|------------|----------|
| 1 | 因果检验 | `causal_validity` | `causal_check` | `causal_validator.py`（CausalValidator） |
| 2 | 样本外验证 | `oos_consistency` | `oos_check` | `walk_forward.py`（WalkForwardOptimizer） |
| 3 | 跨品种验证 | `cross_symbol` | `cross_symbol_check` | 符号级 IC 映射（`symbol_ic_map`） |
| 4 | 压力测试 | `stress_resilience` | `stress_check` | `stress_test.py`（StressTester） |
| 5 | 多重检验 | `multiple_testing` | `multiple_test_check` | `_bh_fdr_correction`（内置） |
| 6 | 数据窥探检验 | `snooping_check` | `look_ahead_check` | 滞后相关分析（内置） |

**审计判定规则**（实际）: 每项可标记 `passed`/`failed`/`skipped`；**所有非 skipped 项必须全部通过**（`passed = bool(non_skipped) and all(passed)`），缺失数据项自动 `skipped` 不阻塞流程。新增 `FailureClassifier`：审计失败时自动识别失败模式（negative_ic/ic_decay/oos_instability/cross_symbol_failure/multiple_testing/snooping_suspected/stress_vulnerable/causal_weak/sharpe_low/high_turnover）并给出改善建议。

### 2.2 审计项详细设计

#### Check 1: 因果检验

```python
class CausalCheck(TypedDict, total=False):
    """因果检验结果。"""
    passed: bool
    granger_f_statistic: float
    granger_p_value: float
    reverse_causation_check: bool          # 反向因果检查
    confound_check: bool                   # 混淆变量检查
    description: str
```

**检验方法**:
- **Granger 因果检验**: 检验因子值是否 Granger-cause 收益
- **反向因果**: 检验收益是否 Granger-cause 因子（排除反向因果）
- **混淆变量**: 控制市场指数、行业收益后因子是否仍然有效

**通过标准**:
- Granger p-value < 0.05
- 反向因果 p-value > 0.10
- 混淆变量控制后 IC 仍显著

#### Check 2: 样本外验证

```python
class OOSCheck(TypedDict, total=False):
    """样本外验证结果。"""
    passed: bool
    oos_ic_mean: float
    oos_sharpe_mean: float
    oos_coverage_ratio: float              # 样本外窗口占比
    min_oos_windows_passed: int            # 最少通过窗口数
    total_windows_evaluated: int
    description: str
```

**检验方法**:
- 复用 `WalkForwardOptimizer` 结果
- 检查每个样本外窗口的 IC 和 Sharpe

**通过标准**:
- OOS IC 均值 > 0.02
- OOS Sharpe 均值 > 1.0
- OOS 窗口占比 ≥ 30%
- 至少 50% 的窗口 IC > 0

#### Check 3: 跨品种验证

```python
class CrossSymbolCheck(TypedDict, total=False):
    """跨品种验证结果。"""
    passed: bool
    total_symbols_evaluated: int
    positive_ic_symbols: int
    coverage_ratio: float                  # 正 IC 品种占比
    avg_ic_all_symbols: float
    min_coverage_threshold: float          # 最低覆盖率阈值 (默认 0.80)
    symbol_details: dict[str, SymbolICDetail]
    description: str


class SymbolICDetail(TypedDict, total=False):
    symbol: str
    ic: float
    icir: float
    n_observations: int
```

**检验方法**:
- 在所有活跃期货品种上独立回测因子
- 计算每个品种的 IC 和 ICIR

**通过标准**:
- 正 IC 品种占比 ≥ 80%
- 平均 IC > 0.02
- 至少覆盖 10 个品种

#### Check 4: 压力测试

```python
class StressCheck(TypedDict, total=False):
    """压力测试结果。"""
    passed: bool
    drawdown_in_stress_periods: float      # 压力期最大回撤
    recovery_time_days: float              # 恢复天数
    stress_periods_evaluated: list[StressPeriod]
    max_drawdown_threshold: float          # 最大回撤阈值 (默认 0.15)
    description: str


class StressPeriod(TypedDict, total=False):
    name: str                              # 如 "2020 COVID", "2022 美联储加息"
    start_date: str
    end_date: str
    drawdown: float
    return_during: float
    ic_during: float
```

**检验方法**:
- 在历史极端行情区间（如 2020 年 3 月、2022 年 Q4）独立评估因子表现
- 检查回撤和恢复速度

**通过标准**:
- 压力期最大回撤 < 15%
- 恢复时间 < 60 天
- 压力期 IC 仍为正（或至少不显著为负）

#### Check 5: 多重检验校正

```python
class MultipleTestCheck(TypedDict, total=False):
    """多重检验校正结果。"""
    passed: bool
    bonferroni_corrected_p: float
    fdr_corrected_p: float
    original_p_value: float
    n_tests: int                           # 总检验数
    bonferroni_threshold: float            # 0.05 / n_tests
    fdr_threshold: float                   # Benjamini-Hochberg
    description: str
```

**检验方法**:
- Bonferroni 校正: `α' = α / n_tests`
- FDR (Benjamini-Hochberg) 校正

**通过标准**:
- Bonferroni 校正后 p < 0.05
- FDR 校正后 q < 0.05

#### Check 6: 数据窥探检验

```python
class LookAheadCheck(TypedDict, total=False):
    """数据窥探检验结果。"""
    passed: bool
    has_future_leakage: bool
    leakage_description: str
    code_analysis_result: CodeAnalysisResult
    data_lineage_check: DataLineageResult
    description: str


class CodeAnalysisResult(TypedDict, total=False):
    """因子代码分析结果。"""
    uses_future_data: bool
    suspicious_patterns: list[str]          # 如 ".shift(-1)", "future_data", "look_ahead"
    code_complexity: int
    sandbox_validation: bool


class DataLineageResult(TypedDict, total=False):
    """数据血缘检查结果。"""
    data_sources_used: list[str]
    has_timestamp_validation: bool
    data_snapshot_consistency: bool
```

**检验方法**:
- 静态代码分析：检查因子代码中是否有未来函数模式
- 运行时验证：沙箱中执行因子代码，检查数据时间戳
- 数据血缘：验证因子计算使用的数据时间戳不包含未来信息

**通过标准**:
- 无未来函数模式
- 数据时间戳验证通过
- 数据血缘可追溯

---

## 3. 接口契约

### 3.1 `FactorAuditor` 类

> **实现现状**: 实际接口如下（`fts/factor_engine/audit.py` v0.1.0）。原设计的 `run_check`/`run_all_checks`/`generate_report`/`get_audit_history`/`get_audit_statistics` 均未实现。

```python
@dataclass
class FactorAuditConfig:
    """因子审计配置。"""
    min_cross_symbol_ratio: float = 0.8   # ≥80% 品种 IC 为正
    bonferroni_alpha: float = 0.05        # Bonferroni 校正显著性
    fdr_alpha: float = 0.05               # FDR 校正显著性
    lookback_max_lag: int = 5             # 最大滞后阶数
    snooping_alpha: float = 0.05          # 窥探检验显著性
    stress_max_drawdown: float = 0.40     # 压力场景最大回撤上限
    min_oos_pass_ratio: float = 0.5       # OOS 最小窗口通过率

class FactorAuditor:
    """因子审计执行器。

    Usage:
        auditor = FactorAuditor()
        report = auditor.audit(
            factor=factor_program,
            data=ohlcv_data,
            forward_returns=future_returns,
            symbol_ic_map={"RB": 0.05, "HC": 0.03, ...},
        )
        assert report.passed
    """

    def __init__(self, config: FactorAuditConfig | None = None) -> None: ...

    def audit(self,
              factor: dict[str, Any] | None = None,
              data: pd.DataFrame | None = None,
              forward_returns: np.ndarray | None = None,
              symbol_ic_map: dict[str, float] | None = None,
              signals_by_symbol: dict[str, np.ndarray] | None = None,
              ohlcv_by_symbol: dict[str, pd.DataFrame] | None = None,
              oos_result: dict[str, Any] | None = None,
              p_values: list[float] | None = None,
              **kwargs) -> FactorAuditReport:
        """执行完整审计流程。各审计项独立传入所需数据，缺失时该项标记 skipped。"""

    # 内部实现: _check_causal_validity / _check_oos_consistency /
    #           _check_cross_symbol / _check_stress_resilience /
    #           _check_multiple_testing / _check_snooping
```

**输出类型**（实际为 dataclass，替代原设计 TypedDict）:

```python
AuditItemStatus = Literal["passed", "failed", "skipped"]

@dataclass
class AuditItemResult:
    name: str
    status: AuditItemStatus
    evidence: str = ""
    score: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

@dataclass
class FactorAuditReport:
    factor_id: str
    factor_name: str
    audited_at: str
    items: list[AuditItemResult]
    passed: bool
    pass_rate: float
    summary: dict[str, Any]
    failure_analysis: dict[str, Any] | None = None   # 失败模式分类（FailureClassifier）
    # 便捷查询: item(name) / failed_items / to_dict()
```

### 3.2 核心类型

```python
class AuditCheckName(str, Enum):
    """审计检查名称枚举。"""
    CAUSAL = "causal_check"
    OOS = "oos_check"
    CROSS_SYMBOL = "cross_symbol_check"
    STRESS = "stress_check"
    MULTIPLE_TEST = "multiple_test_check"
    LOOK_AHEAD = "look_ahead_check"


class AuditCheckResult(TypedDict, total=False):
    """单个审计检查的结果。"""
    check_name: AuditCheckName
    passed: bool
    score: float                           # 0-100 分
    details: AuditCheckDetail              # 详细结果（CausalCheck/OOSCheck 等）
    execution_time_ms: float
    error_message: str | None
    timestamp: str


class AuditConfig(TypedDict, total=False):
    """审计配置。"""
    checks_to_run: list[AuditCheckName]    # 默认全部 6 项
    min_pass_rate: float                   # 通过率阈值 (默认 1.0 = 全通过)
    oos_config: WalkForwardConfig
    stress_periods: list[StressPeriod]
    cross_symbol_min_count: int            # 跨品种最少数量 (默认 10)
    bonferroni_alpha: float                # Bonferroni 全局 α (默认 0.05)


class AuditReport(TypedDict, total=False):
    """完整审计报告。"""
    report_id: str
    factor_id: str
    factor_version_id: str
    timestamp: str
    passed: bool
    overall_score: float
    results: dict[AuditCheckName, AuditCheckResult]
    summary: AuditSummary
    recommendations: list[str]
    generated_by: str                      # 'FactorAuditor v1.0'


class AuditSummary(TypedDict, total=False):
    total_checks: int
    passed_checks: int
    failed_checks: int
    pass_rate: float
    weakest_check: AuditCheckName
    strongest_check: AuditCheckName


class AuditStatistics(TypedDict, total=False):
    """审计统计。"""
    total_audits: int
    pass_rate: float
    failure_rate_by_check: dict[str, float]
    avg_audit_time_ms: float
    common_failure_reasons: list[tuple[str, int]]
```

---

## 4. 流程设计

### 4.1 审计执行流程

```mermaid
flowchart TD
    A[FactorAuditor.audit] --> B[加载因子及其评估结果]
    B --> C[并行执行审计检查]
    C --> C1[causal_check]
    C --> C2[oos_check]
    C --> C3[cross_symbol_check]
    C --> C4[stress_check]
    C --> C5[multiple_test_check]
    C --> C6[look_ahead_check]
    C1 & C2 & C3 & C4 & C5 & C6 --> D[汇总 AuditCheckResult]
    D --> E{所有检查通过?}
    E -->|是| F[passed=True, overall_score=加权分]
    E -->|否| G[passed=False, 标记失败项]
    F & G --> H[生成 AuditReport]
    H --> I[写入 factor_audit_reports 表]
    I --> J[返回 AuditReport]
```

### 4.2 审计准入 Gate

```mermaid
flowchart TD
    A[因子完成 L3 评估] --> B[A.1 质量评分卡计算]
    B --> C{A.2 衰减状态检查}
    C -->|active| D{执行审计检查}
    C -->|decaying| E[拒绝准入]
    D --> F{AuditReport.passed?}
    F -->|是| G[A.3 自适应权重分配 → 入库]
    F -->|否| H[拒绝准入, 记录失败原因]
    G & H --> I[审计结果写入 factor_audit_reports]
```

### 4.3 审计结果与评分卡联动

```python
# 审计结果作为评分卡的输入
class FactorQualityCard:
    def evaluate(self, ..., audit_report: AuditReport | None = None):
        # 审计通过率作为"逻辑性"维度的加分项
        if audit_report:
            logic_score += (audit_report.pass_rate * 5.0)  # 审计通过率贡献最多 5 分
            # 审计失败扣分
            if not audit_report.passed:
                total_score -= 5.0  # 审计未通过扣 5 分
```

---

## 5. 数据模型设计

### 5.1 审计报告存储

> **实现现状**: **未实现**。审计报告未落 DuckDB `factor_audit_reports` 表，以 `FactorAuditReport.to_dict()` 结构化字典输出并记录日志。以下表为原设计预留。

```sql
CREATE TABLE IF NOT EXISTS factor_audit_reports (
    report_id       VARCHAR(36) PRIMARY KEY,
    factor_id       VARCHAR(36) NOT NULL,
    factor_version_id VARCHAR(36),
    passed          BOOLEAN NOT NULL,
    overall_score   DOUBLE NOT NULL,
    total_checks    INT NOT NULL,
    passed_checks   INT NOT NULL,
    results_json    JSON NOT NULL,          # AuditCheckResult 详情
    summary_json    JSON NOT NULL,          # AuditSummary
    recommendations  JSON,
    audited_at      TIMESTAMP NOT NULL,
    audit_version   VARCHAR(20) NOT NULL DEFAULT 'v1',
    FOREIGN KEY (factor_id) REFERENCES factor_catalog(factor_id)
);

CREATE INDEX IF NOT EXISTS idx_far_factor_id ON factor_audit_reports(factor_id);
CREATE INDEX IF NOT EXISTS idx_far_passed ON factor_audit_reports(passed);
CREATE INDEX IF NOT EXISTS idx_far_audited_at ON factor_audit_reports(audited_at);
```

---

## 6. 技术约束

| 约束 | 说明 |
|------|------|
| **并行执行** | 六项审计检查并行执行，总耗时 = 最慢的单项检查 |
| **超时保护** | 单项检查超时 5 分钟自动标记为 `incomplete` |
| **不可变** | 审计报告写入后不可修改，只可追加新报告 |
| **可追溯** | 每次审计记录因子版本 ID，支持版本关联 |
| **幂等性** | 同一因子版本重复审计结果一致 |
| **性能** | 完整审计 < 10 分钟（单因子） |
| **容错** | 单项审计失败不影响其他项执行 |

---

## 7. 文件改动清单

| 文件 | 动作 | 现状 | 说明 |
|------|------|------|------|
| `fts/factor_engine/factor_auditor.py` | **新增** | ⬜ 未实现 | 实际实现于 `fts/factor_engine/audit.py`（`FactorAuditor` + `FactorAuditConfig` + `FailureClassifier`） |
| `fts/factor_engine/factor_db/schema.py` | **修改** | ⬜ 未实现 | `factor_audit_reports` 表未新增 |
| `fts/factor_engine/factor_db/repository.py` | **修改** | ⬜ 未实现 | 审计报告 CRUD 未实现 |
| `fts/factor_engine/evolution_loop.py` | **修改** | ✅ 已实现 | L3 评估后插入审计 Gate（审计失败阻止晋升 elite，`evolution_loop.py:392-405`） |
| `fts/factor_engine/evaluation_chain.py` | **修改** | ⬜ 未实现 | 审计结果作为评分卡输入未实现 |
| `tests/factor_engine/test_factor_auditor.py` | **新增** | ✅ 已实现 | 审计器单元测试（对应测试文件存在） |
| `tests/factor_engine/test_audit_causal.py` | **新增** | ⬜ 未实现 | 原设计拆分测试未实现（覆盖于审计器测试） |
| `tests/factor_engine/test_audit_look_ahead.py` | **新增** | ⬜ 未实现 | 原设计拆分测试未实现（覆盖于审计器测试） |

---

## 8. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | 六项审计检查可独立和整体执行 | 单元测试 |
| 2 | 因子入库前必须通过全部审计项 | 集成测试 |
| 3 | 审计报告正确生成，包含所有检查详情 | 接口测试 |
| 4 | 审计结果可在数据库中查询 | 集成测试 |
| 5 | 因果检验正确识别 Granger 因果关系 | 单元测试 |
| 6 | 跨品种验证正确计算品种覆盖率 | 单元测试 |
| 7 | 数据窥探检验正确识别未来函数 | 单元测试 |
| 8 | 多重检验校正（Bonferroni/FDR）正确 | 单元测试 |
| 9 | 并行执行总耗时 < 10 分钟 | 性能测试 |
| 10 | 审计结果作为评分卡输入正确影响等级 | 集成测试 |

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | [11-factor-mining-optimization-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/11-factor-mining-optimization-plan.md) → Phase B.3 |
| 依赖模块 | `causal_validator.py`（因果）、`walk_forward.py`（OOS）、`stress_test.py`（压力）、`factor_program.py`（代码分析）、`evaluation_chain.py`（多重检验） |
| 前置条件 | A.1 质量评分卡、A.2 衰减追踪已实施 |
| 后置影响 | 因子准入增加审计 Gate，审计数据可追溯 |
| 与其他计划关联 | B.2 回测流水线的结果作为审计输入；C.3 反馈闭环使用审计数据 |