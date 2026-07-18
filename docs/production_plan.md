# FTS 生产就绪实施计划

> 版本: v0.1.0（草案）
> 当前基线: v0.2.0（778 测试全绿，89% 覆盖率，7 项差距全部关闭）
> 目标: v1.0.0（生产环境可部署的因子智能系统）

---

## 0. 总览

### 0.1 晋级路线

```
v0.2.0 ───→ v0.3.0 ───→ v0.4.0 ───→ v0.5.0 ───→ v1.0.0
  当前     数据基建     因子养护     策略增强     生产部署
```

### 0.2 版本里程碑

| 版本 | 主题 | 核心产出 | 预估工期 |
|:-----|:-----|:---------|:---------|
| **v0.2.0** | ✅ 已完成 | CLI 引擎、Scheduler、Config、89% 覆盖 | — |
| **v0.3.0** | 数据基建 | Data-Core 生产集成、FDT 残留清除、覆盖补齐 | 2-3 周 |
| **v0.4.0** | 因子养护 | EliteFactorTracker、AutoRetire、WalkForward | 2-3 周 |
| **v0.5.0** | 策略增强 | Regime 感知、交易成本、压力测试 | 1-2 周 |
| **v1.0.0** | 生产部署 | 监控告警、容器化、CI/CD、E2E 测试 | 2-3 周 |

**合计预估**: 8-11 周（全时单人开发）

### 0.3 总的待解决问题清单

| # | 问题 | 严重程度 | 解决版本 |
|:-:|:-----|:---------|:---------|
| 1 | Data-Core 未经过真实数据流验证 | 阻塞 | v0.3.0 |
| 2 | meta_loop.py 残留 FDT 依赖 | 阻塞 | v0.3.0 |
| 3 | 覆盖率短板（data.py 46%, engine 22%, config 64%） | 中 | v0.3.0 |
| 4 | memory JSON 写入非原子 | 中 | v0.3.0 |
| 5 | elite 因子无样本外跟踪 | 高 | v0.4.0 |
| 6 | OOS 固定 30% 切片，无走航优化 | 高 | v0.4.0 |
| 7 | 无因子自动淘汰机制 | 中 | v0.4.0 |
| 8 | 无市场制度感知 | 中 | v0.5.0 |
| 9 | 交易成本仅算换手率，无滑点/冲击 | 中 | v0.5.0 |
| 10 | 无极端行情压力测试 | 低 | v0.5.0 |
| 11 | 无实时监控/告警（Prometheus/HTTP） | 中 | v1.0.0 |
| 12 | Scheduler 无守护进程模式 | 中 | v1.0.0 |
| 13 | 无 Docker 容器化 | 低 | v1.0.0 |
| 14 | 无 CI/CD 流水线 | 低 | v1.0.0 |
| 15 | 无 Grafana 可视化 | 低 | v1.0.0 |
| 16 | 无 E2E 测试 | 高 | v1.0.0 |
| 17 | 无生产部署文档 | 低 | v1.0.0 |

---

## 1. Phase A — 数据基础设施（v0.3.0）

> 目标: FTS 能通过 Data-Core 获取真实市场数据，工程基础扎实

### 1.1 Data-Core 生产集成

**问题**: `fts/data.py` 已定义接口，但 Data-Core 的 `UnifiedDataProvider` 在生产环境下返回的 `DataPayload` 格式未经验证。当前测试全部走合成数据。

**任务清单**:

| # | 任务 | 文件 | 说明 |
|:-:|:-----|:-----|:------|
| A-01 | Data-Core 端到端数据流验证 | `fts/data.py` | 接入真实行情/基本面/新闻，验证 payload 解析 |
| A-02 | 添加多源降级策略 | `fts/data.py` | PRIMARY → DAILY → CACHED → STALE 分级，每级可配置 |
| A-03 | 数据缓存层 | `fts/data.py` | 本地 DuckDB 缓存，避免重复请求，支持离线回放 |
| A-04 | 符号映射兼容性 | `fts/data.py` | 解决 FTS 内部符号 ↔ Data-Core SymbolRegistry 差异 |
| A-05 | mock 测试补齐 | `tests/test_data.py` | 用 mock 覆盖 data.py 全部路径（当前 46%） |

**验收标准**:
- [ ] `pip install datacore` 后 `fts data check —symbol RB` 返回真实 K 线
- [ ] Data-Core 不可用时优雅降级到合成数据
- [ ] `fts/data.py` 覆盖率 ≥ 85%
- [ ] 所有数据接口都有对应的 mock 测试

---

### 1.2 清除 FDT 残留依赖

**问题**: `meta_loop.py:1121` 仍 import `futures_data_core.f10.web_collector`，虽被 try/except 保护，但 FTS 不应依赖 FDT。

**任务清单**:

| # | 任务 | 文件 | 说明 |
|:-:|:-----|:-----|:------|
| B-01 | 替换为 Data-Core 等效接口 | `fts/factor_engine/meta_loop.py` | 用 `FTSDataProvider.get_news()` 替代 web_collector |
| B-02 | 或者完全移除 L1 感知的 web 采集 | `fts/factor_engine/meta_loop.py` | 在 v2.2 边界中，数据采集归 Data-Core |

**验收标准**:
- [ ] `grep -r "futures_data_core" fts/` 返回空
- [ ] `meta_loop.py` 中无外部数据源导入
- [ ] 测试全部通过

---

### 1.3 覆盖率补齐

**问题**: 部分模块覆盖率不足，影响重构信心。

| 模块 | 当前 | 目标 | 主要缺失 |
|:-----|:-----|:-----|:---------|
| `fts/config/settings.py` | 64% | 85%+ | YAML 加载、环境变量覆盖、异常路径 |
| `fts/data.py` | 46% | 85%+ | DataPayload 解析、多源降级、错误回退 |
| `fts/scheduler/engine.py` | 22% | 70%+ | APScheduler 集成、cron 解析、任务执行 |
| `fts/factor_engine/meta_loop.py` | 84% | 90%+ | 剩余 69 行复杂路径 |

**验收标准**:
- [ ] 总体覆盖率 ≥ 90%
- [ ] 各模块覆盖率达到上表目标
- [ ] 全部测试通过（预计 ~820+）

---

### 1.4 状态持久化原子性

**问题**: memory/*.json 使用直接写入，进程崩溃产生残缺文件。

```python
# ❌ 当前写法
Path("memory/evolution/state.json").write_text(json.dumps(state))

# ✅ 原子写入
tmp = Path("memory/evolution/state.json.tmp")
tmp.write_text(json.dumps(state))
tmp.rename("memory/evolution/state.json")  # 原子 rename
```

**验收标准**:
- [ ] 所有 state.json/pool.json/combo.json 写入使用临时文件+rename
- [ ] 读取时校验 JSON 合法性，非法时使用备份

---

## 2. Phase B — 因子养护（v0.4.0）

> 目标: elite 因子入库后有持续的样本外跟踪、自动淘汰和更可靠的验证

### 2.1 EliteFactorTracker

```python
# fts/monitor/elite_tracker.py（新增）

class TrackingSnapshot(TypedDict):
    factor_id: str
    name: str
    entry_ic: float               # 入库时的样本外 IC
    entry_sharpe: float           # 入库时的样本外夏普
    entry_at: str                 # ISO 时间
    weekly_ic: list[float]        # 每周滚动 IC 序列
    monthly_ic: list[float]       # 每月滚动 IC 序列
    current_ic: float             # 最近一期样本外 IC
    current_sharpe: float
    consecutive_zero_ic: int      # 连续 IC ≤ 0 的周数
    decay_6m: float               # 6 个月滚动衰减率
    status: str                   # active / decaying / decayed / retired
```

| 方法 | 职责 | 调用方 |
|:-----|:------|:-------|
| `init_tracker(factor, snapshot)` | 因子入库时创建跟踪记录 | `EvolutionLoop._promote_to_elite()` |
| `update(factor_id, new_ic)` | 每周/每日更新样本外 IC | Scheduler 定时任务 |
| `get_decaying(max_consecutive=4)` | 返回正在衰减的因子列表 | PortfolioLoop / Monitor |
| `auto_retire()` | 自动标记 decayed 因子为 retired | 每日维护任务 |
| `report()` | 生成因子健康报告 | CLI `factor health` |

**退役流程**:
```
elite/factor.json → status=decayed
  → 移至 memory/knowledge/factors/retired/
    → PortfolioLoop 因子池移除
      → 退役原因记录到经验链
```

**存储位置**: `memory/tracking/{factor_id}.json`

**测试**: ~40 用例

---

### 2.2 AutoRetireManager

| 参数 | 默认值 | 说明 |
|:-----|:-------|:------|
| `max_consecutive_zero_ic` | 4周 | 连续 IC ≤ 0 → decayed |
| `max_decay_6m` | 0.30 | 6 月衰减率 > 30% → decayed |
| `min_active_days` | 30天 | 最短观察期，防过早淘汰 |
| `cooldown_days` | 7天 | 退役后冷却期，可重新评估 |

**测试**: ~20 用例

---

### 2.3 WalkForwardOptimizer

**问题**: 当前 `evaluate_backtest()` 使用固定 30% 尾部切片作为样本外（单窗口），无法评估因子在不同市场环境下的稳定性。

```python
# fts/factor_engine/walk_forward.py（新增）

class WalkForwardConfig(TypedDict):
    window_years: int = 3          # 训练窗口长度
    step_months: int = 6           # 滚动步长
    min_oos_months: int = 3        # 最小样本外长度
    n_windows: int = 4             # 一次运行评估几个窗口
    min_ic_consistency: float = 0.5  # 至少 50% 窗口 IC > 0
    max_ic_volatility: float = 0.3   # IC 跨窗口波动率上限
```

**多窗口验证示意**:
```
窗口1: [2019-01 ~ 2021-12] 训练 → [2022-01 ~ 2022-03] OOS
窗口2: [2019-07 ~ 2022-06] 训练 → [2022-07 ~ 2022-09] OOS
窗口3: [2020-01 ~ 2022-12] 训练 → [2023-01 ~ 2023-03] OOS
窗口4: [2020-07 ~ 2023-06] 训练 → [2023-07 ~ 2023-09] OOS
```

**输出**: `WalkForwardResult` — 每个窗口的 IC/夏普 + 跨窗口稳定性评分

| 评分维度 | 计算方法 | 阈值 |
|:---------|:---------|:-----|
| IC 一致性 | IC > 0 的窗口占比 | ≥ 50% |
| IC 稳定性 | 跨窗口 IC 标准差 | ≤ 0.3 |
| 夏普稳定性 | 跨窗口夏普标准差 | ≤ 1.0 |
| 综合评分 | 以上三项加权 | ≥ 60 分通过 |

**集成点**:
- 改造 `EvaluationChain.evaluate()` → 可选走航模式
- `EvolutionLoop.run()` → 走航通过后才进入 verifier
- `Verifier.check()` → 走航评分作为附加判定维度

**测试**: ~30 用例

---

## 3. Phase C — 策略增强（v0.5.0）

> 目标: 策略能感知市场制度、考虑真实交易成本、通过极端行情压力测试

### 3.1 RegimeAwareSelector

```python
# fts/factor_engine/regime.py（新增）

class MarketRegime(TypedDict):
    regime: str                    # bull / bear / 震荡 / 高波 / 低波
    confidence: float              # 置信度 0~1
    detected_at: str
    features: dict                 # 检测特征

class RegimeFactorProfile(TypedDict):
    factor_id: str
    regime_performance: dict[str, RegimePerformance]  # regime → performance
```

| 方法 | 职责 |
|:-----|:------|
| `detect(ohlcv) -> MarketRegime` | 从 OHLCV 检测当前市场制度 |
| `profile_factor(factor_id, history)` | 记录因子在各 regime 下的历史表现 |
| `select_factors(regime, elite_pool)` | 只选出在当前制度下有效的因子 |
| `regime_report()` | 当前制度 + 各因子得分报告 |

**检测维度**:
| 特征 | 计算方式 | bull 特征 | bear 特征 |
|:------|:---------|:----------|:----------|
| 趋势强度 | MA20/MA120 斜率 | 正且陡 | 负且陡 |
| 波动率 | ATR/价格比率 | 中低 | 高 |
| 成交量 | 相对 20 日均值 | 温和放大 | 异常放大 |
| 广度 | 品种内相关性 | 低 | 高 |

**集成点**:
- `PortfolioLoop.run()` → 先检测 regime，再选因子，再构建组合
- `RegimeAwareSelector` 与 `EliteFactorTracker` 联动：因子在各 regime 下的表现存入 tracking 记录

**测试**: ~25 用例

---

### 3.2 TransactionCostModel

```python
# fts/factor_engine/cost_model.py（新增）

class CostConfig(TypedDict):
    slippage_bps: float = 1.0       # 滑点（基点，按品种可配）
    commission_bps: float = 0.3     # 手续费
    impact_bps_per_pct: float = 2.0 # 冲击成本（每 1% 日成交量占比）
    min_cost_bps: float = 0.5       # 最低成本
    market: str = "futures"         # 按市场配置不同参数
```

**改造点**:

| 位置 | 改造内容 |
|:-----|:---------|
| `evaluation_chain.py` — `BacktestMetrics` | 新增 `cost_adjusted_sharpe`, `net_turnover` 字段 |
| `evaluation_chain.py` — `evaluate_backtest()` | 在夏普计算前扣除成本 |
| `Verifier.check()` | `cost_adjusted_sharpe` 替代 `sharpe` 做判定 |
| `PortfolioSignal` | 新增 `cost_adjusted_sharpe` 字段 |
| `strategies/multi_factor_strategy.py` | 信号分档前考虑净收益 |

**各市场成本参数参考**:

| 市场 | 滑点(bps) | 手续费(bps) | 冲击成本(bps) |
|:-----|:----------|:------------|:--------------|
| 期货（主力） | 0.5 | 0.1~0.3 | 1.0 |
| 期货（非主力） | 2.0 | 0.1~0.3 | 5.0 |
| A 股 | 1.0 | 0.5~1.0 | 2.0 |
| ETF | 0.5 | 0.3 | 1.0 |

**测试**: ~15 用例

---

### 3.3 StressTester

```python
# fts/factor_engine/stress_test.py（新增）

class StressScenario(TypedDict):
    name: str                      # 场景名称
    symbols: list[str]             # 涉及品种
    date_range: tuple[str, str]    # 时间窗口
    price_shock: float             # 最大价格冲击（%）
    vol_multiplier: float          # 波动率倍数

class StressTestResult(TypedDict):
    scenario: str
    max_drawdown: float            # 该场景下最大回撤
    sharpe: float                  # 该场景下夏普
    recovery_days: int             # 恢复天数
    passed: bool                   # 是否通过（回撤 ≤ 40%）
```

**内置场景库**:

| 场景 | 品种 | 时间 | 关键特征 |
|:-----|:------|:-----|:---------|
| 原油暴跌 | SC/CL | 2020-03 ~ 2020-05 | -300% 极端行情 |
| 双十一闪崩 | 商品期货 | 2016-11-11 | 单日夜盘 -5%~+5% |
| 股灾 | 沪深300 | 2015-06 ~ 2015-09 | -45% 系统性下跌 |
| 疫情冲击 | 全品种 | 2020-02 ~ 2020-03 | 恐慌性抛售 |
| 供给侧改革 | 黑色系 | 2016 | 趋势性大涨 |

**集成点**:
- `PortfolioLoop.run()` 末尾 → 对当前组合跑全部场景
- 任一场景回撤 > 40% → 打印告警 + 记录到监控报告
- 压力测试结果存入 `memory/portfolio/stress_report.json`

**测试**: ~20 用例

---

## 4. Phase D — 生产部署（v1.0.0）

> 目标: 可部署、可运维、可监控

### 4.1 实时监控与告警

| 能力 | 实现方式 | 说明 |
|:-----|:---------|:------|
| Prometheus 指标导出 | `prometheus_client` HTTP 端点 (:9100/metrics) | 因子数、Token 消耗、循环状态、IC 分布 |
| 健康检查端点 | FastAPI/Flask HTTP :9100/health | K8s liveness/readiness probe |
| 文件告警 | 超过阈值时写入 `memory/alerts/` | 当前熔断状态、因子退役事件 |
| Webhook 通知 | 可配置飞书/企业微信/邮件 | 因子退役、熔断、异常 |

**指标清单**:

| 指标名 | 类型 | 标签 | 说明 |
|:-------|:-----|:-----|:------|
| `fts_elite_factor_count` | Gauge | status | elite/decayed/retired 各状态因子数 |
| `fts_daily_ic` | Gauge | factor_id | 各因子当日 IC |
| `fts_loop_status` | Gauge | loop | 0=stopped, 1=running, 2=broken |
| `fts_tokens_consumed` | Counter | — | 累计 Token 消耗 |
| `fts_combo_sharpe` | Gauge | — | 当前组合夏普 |
| `fts_stress_test_result` | Gauge | scenario | 1=passed, 0=failed |

---

### 4.2 Scheduler 守护进程

```bash
# 前台守护模式（Linux systemd / Windows 服务）
fts scheduler run --daemon

# 运行一次所有任务
fts scheduler run --once

# 查看任务执行历史
fts scheduler history --last 20
```

**systemd 单元文件示例**:
```ini
[Unit]
Description=FTS Factor Intelligence System
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/fts scheduler run --daemon
Restart=always
RestartSec=30
Environment=FTS_CONFIG_FILE=/etc/fts/settings.yaml
Environment=OPENAI_API_KEY=...
```

---

### 4.3 容器化

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install .[evolution,llm,data]
CMD ["fts", "scheduler", "run", "--daemon"]
```

```yaml
# docker-compose.yml
services:
  fts-scheduler:
    build: .
    ports: ["9100:9100"]      # Prometheus metrics
    environment:
      - FTS_CONFIG_FILE=/config/settings.yaml
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - ./memory:/app/memory
      - ./config:/config
    restart: always
```

---

### 4.4 CI/CD 流水线

```yaml
# .github/workflows/ci.yml（新增）
name: FTS CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install .[dev,evolution]
      - run: python -m pytest tests/ --cov=fts --cov-report=xml
      - run: python -m pylint fts/ --fail-under=9.5
```

---

### 4.5 E2E 测试套件

**必须覆盖的 10 个场景**:

| # | 场景 | 路径 | 通过条件 |
|:-:|:-----|:-----|:---------|
| 1 | 完整因子演化 | SeedPool → MacroEvolver → EvalChain → Verifier → Elite 入库 | 1 个 elite 因子产出 |
| 2 | Meta-Loop 知识补给 | L1 运行 + 种子注入 | 因子池更新 |
| 3 | 组合构建 | loading elite → orthogonalize → decay → synthesize | 组合信号产出 |
| 4 | 走航验证 | WalkForward 多窗口 | 稳定性评分产出 |
| 5 | 因子跟踪 | tracker init → update → retire | 正确标记 decayed |
| 6 | 市场制度检测 | OHLCV → regime 分类 | 至少 3 种 regime |
| 7 | 交易成本调整 | 净夏普 < 毛夏普 | 成本正确扣除 |
| 8 | 压力测试 | 已知场景下组合不崩溃 | 回撤 ≤ 40% |
| 9 | Data-Core 降级 | 数据源不可用 → 合成数据 | 不抛异常，打印警告 |
| 10 | 完整 scheduler | 任务注册 → 调度 → 执行 | 任务日志正确 |


### 4.6 生产部署文档

**文档清单**:

| 文档 | 内容 |
|:-----|:------|
| `docs/deploy/INSTALL.md` | 环境要求、安装步骤、依赖检查 |
| `docs/deploy/CONFIG.md` | 配置项说明、环境变量清单、示例配置文件 |
| `docs/deploy/OPS.md` | 日常运维操作、故障排查、数据备份 |
| `docs/deploy/SECURITY.md` | API Key 管理、网络策略、日志脱敏 |
| `docs/deploy/SCALING.md` | 多市场多品种场景下的扩容方案 |

---

## 5. 版本路线总图

### 5.1 版本发布时间线

```
v0.2.0 ───────────────────────────────────────────────────────────── 已完成
    │
    └── 2-3 周 ──→ v0.3.0 ─── 1-2 周 ──→ v0.4.0 ─── 1-2 周 ──→ v0.5.0 ─── 2-3 周 ──→ v1.0.0
                    │                   │                   │                   │
                    ├ Data-Core 集成     ├ EliteTracker       ├ RegimeAware        ├ 监控告警
                    ├ FDT 清除          ├ AutoRetire         ├ CostModel          ├ 容器化
                    ├ 覆盖补齐          ├ WalkForward        ├ StressTest         ├ CI/CD
                    └ 原子持久化        └ 测试 ~90 新增      └ 测试 ~60 新增       ├ E2E 测试
                                                                                 └ 部署文档
```

### 5.2 各版本测试与覆盖目标

| 版本 | 测试用例数 | 总体覆盖率 | 差距关闭数 | 核心模块覆盖 |
|:-----|:----------|:-----------|:-----------|:-------------|
| v0.2.0 | 778 | 89% | 7/7 | evolution_loop 99%, llm 77% |
| v0.3.0 | ~820 | ≥90% | 7/7 + 4 新增 | data.py ≥85%, config 85%+, engine 70%+ |
| v0.4.0 | ~910 | ≥91% | 全部 | tracker 100%, walk_forward 90%+ |
| v0.5.0 | ~970 | ≥92% | 全部 | regime 90%+, cost 90%+, stress 90%+ |
| v1.0.0 | ~1000+ | ≥92% | 全部 | 全部 ≥85% |

### 5.3 前置依赖

| 版本 | 前置条件 | 说明 |
|:-----|:---------|:------|
| v0.3.0 | Data-Core v0.3.0+ | UnifiedDataProvider 接口稳定，`pip install datacore` 可用 |
| v0.4.0 | v0.3.0 全部门禁通过 | 数据流就绪后才能做样本外跟踪 |
| v0.5.0 | v0.4.0 全部门禁通过 | 因子池稳定后才能做 regime 感知 |
| v1.0.0 | v0.5.0 全部门禁通过 | 策略验证通过后才能上线 |

---

## 6. 实施建议

### 6.1 并行策略

在等待 Data-Core 生产就绪期间，可以并行推进：

```
Data-Core 团队 ──→ A-01 ~ A-04（数据层集成）
                                  ↘
FTS 团队 ────────→ B-01 ~ B-02（FDT 清除）
                    C-01 ~ C-02（Config 覆盖补齐）
                    D-01（原子持久化）
```

### 6.2 风险点

| 风险 | 概率 | 影响 | 缓解措施 |
|:-----|:-----|:-----|:---------|
| Data-Core 接口变更 | 中 | v0.3.0 阻塞 | FTSDataProvider 做适配层，接口变更时只改适配层 |
| 走航优化拖慢演化速度 | 高 | v0.4.0 性能 | 走航设为可选模式，默认关闭，生产环境启用 |
| regime 检测效果不佳 | 中 | v0.5.0 有效性 | 先用规则基线（MA/ATR），后续再上 ML |
| 交易成本参数校准 | 高 | v0.5.0 准确性 | 初始用保守参数（高估成本），后续用回测校准 |

### 6.3 决策记录

| 决策 | 方案 | 理由 |
|:-----|:-----|:------|
| EliteTracker 独立模块 vs 合并到 monitor | 独立模块 | monitor 职责是系统级监控，tracker 是因子级，分离更清晰 |
| WalkForward 替代 vs 并行 | 并行 | 保留现有单窗口模式作为快速验证，走航作为可选强化验证 |
| Regime 检测用规则 vs ML | 规则 | 初始版本需要可解释性，ML 模型在数据充分后引入 |
| 成本模型配置在 pyproject.toml vs settings.yaml | settings.yaml | 成本参数是运行时配置，不是包配置 |

---

## 7. 门禁条件

### 进入 v0.3.0

- [ ] `python -m pytest tests/` 全部通过
- [ ] 总体覆盖率 ≥ 90%
- [ ] `grep -r "futures_data_core" fts/` 空
- [ ] `fts/data.py` 覆盖率 ≥ 85%
- [ ] `fts/config/settings.py` 覆盖率 ≥ 85%
- [ ] 所有 state.json 写入使用原子操作
- [ ] version 更新为 v0.3.0

### 进入 v0.4.0

- [ ] 全部 v0.3.0 门禁通过
- [ ] EliteFactorTracker 全部测试通过（~40 用例）
- [ ] AutoRetireManager 全部测试通过（~20 用例）
- [ ] WalkForwardOptimizer 全部测试通过（~30 用例）
- [ ] EvolutionLoop 走航集成测试通过
- [ ] 总体覆盖率 ≥ 91%

### 进入 v0.5.0

- [ ] 全部 v0.4.0 门禁通过
- [ ] RegimeAwareSelector 测试通过
- [ ] TransactionCostModel 测试通过
- [ ] StressTester 测试通过
- [ ] PortfolioLoop 集成测试通过
- [ ] 总体覆盖率 ≥ 92%

### 进入 v1.0.0

- [ ] 全部 v0.5.0 门禁通过
- [ ] Prometheus HTTP 端点可访问
- [ ] Docker 镜像构建成功
- [ ] GitHub Actions CI 流水线通过
- [ ] 10+ E2E 测试全部通过
- [ ] 部署文档完整
- [ ] 总体覆盖率 ≥ 92%

---

## 8. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:------|
| **v0.1.0** | 2026-07-18 | 初版，汇总 v0.3.0 ~ v1.0.0 全部生产就绪工作 |
