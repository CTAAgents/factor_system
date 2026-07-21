# FTS 生产就绪实施计划

> 版本: v0.3.0（草案）
> 当前基线: v1.0.0（1231 测试全绿，96% 覆盖率，全部 Phases 完成）
> 目标: v1.0.0（本地原生部署的因子智能系统）
> 部署哲学: **边改边跑** — 本地原生部署优先，热重载开发，进程级守护

---

## 0. 总览

### 0.1 晋级路线

```
v0.2.0 ───→ v0.3.0 ───→ v0.4.0 ───→ v0.5.0 ───→ v1.0.0
  当前     数据基建     因子养护     策略增强     本地原生部署
```

### 0.2 版本里程碑

| 版本 | 主题 | 核心产出 | 预估工期 |
|:-----|:-----|:---------|:---------|
| **v0.2.0** | ✅ 已完成 | CLI 引擎、Scheduler、Config、89% 覆盖 | — |
| **v0.3.0** | ✅ 已完成 | Data-Core 集成、FDT 清除、96% 覆盖、原子持久化 | — |
| **v0.4.0** | ✅ 已完成 | EliteFactorTracker、AutoRetire、WalkForward | — |
| **v0.5.0** | ✅ 已完成 | Regime 感知、交易成本、压力测试 | — |
| **v1.0.0** | ✅ 已完成 | 进程守护/热重载/HTTP 监控/Windows 服务/CI/CD/E2E 测试/部署文档 | — |

**合计预估**: 5-8 周（全时单人开发）

### 0.3 总的待解决问题清单

| # | 问题 | 严重程度 | 解决版本 |
|:-:|:-----|:---------|:---------|
| 1 | Data-Core 未经过真实数据流验证 | 阻塞 | ✅ v0.3.0 |
| 2 | meta_loop.py 残留 FDT 依赖 | 阻塞 | ✅ v0.3.0 |
| 3 | 覆盖率短板（data.py 46%, engine 22%, config 64%） | 中 | ✅ v0.3.0 |
| 4 | memory JSON 写入非原子 | 中 | ✅ v0.3.0 |
| 5 | elite 因子无样本外跟踪 | 高 | v0.4.0 |
| 6 | OOS 固定 30% 切片，无走航优化 | 高 | v0.4.0 |
| 7 | 无因子自动淘汰机制 | 中 | v0.4.0 |
| 8 | 无市场制度感知 | 中 | v0.5.0 |
| 9 | 交易成本仅算换手率，无滑点/冲击 | 中 | v0.5.0 |
| 10 | 无极端行情压力测试 | 低 | v0.5.0 |
| 11 | 无实时监控/告警（文件+HTTP） | 中 | v1.0.0 |
| 12 | Scheduler 无守护进程模式 | 中 | v1.0.0 |
| 13 | 无热重载支持 | 中 | v1.0.0 |
| 14 | 无 CI/CD 流水线 | 低 | v1.0.0 |
| 15 | 无 E2E 测试 | 高 | v1.0.0 |
| 16 | 无本地原生部署文档 | 低 | v1.0.0 |

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
| A-05 | ✅ mock 测试补齐 | `tests/test_data.py` | 用 mock 覆盖 data.py 全部路径（当前 46%） |

**验收标准**:
- [ ] `pip install datacore` 后 `fts data check --symbol RB` 返回真实 K 线
- [ ] Data-Core 不可用时优雅降级到合成数据
- [ ] `fts/data.py` 覆盖率 ≥ 85%
- [ ] 所有数据接口都有对应的 mock 测试

---

### 1.2 清除 FDT 残留依赖

**问题**: `meta_loop.py:1121` 仍 import `futures_data_core.f10.web_collector`，虽被 try/except 保护，但 FTS 不应依赖 FDT。

**任务清单**:

| # | 任务 | 文件 | 说明 |
|:-:|:-----|:-----|:------|
| B-01 | ✅ 替换为 Data-Core 等效接口 | `fts/factor_engine/meta_loop.py` | 用 `FTSDataProvider.get_news()` 替代 web_collector |
| B-02 | ✅ 或者完全移除 L1 感知的 web 采集 | `fts/factor_engine/meta_loop.py` | 在 v2.2 边界中，数据采集归 Data-Core |

**验收标准**:
- [x] `grep -r "futures_data_core" fts/` 返回空
- [x] `meta_loop.py` 中无外部数据源导入
- [x] 测试全部通过

---

### 1.3 覆盖率补齐

**问题**: 部分模块覆盖率不足，影响重构信心。

| 模块 | 当前 | 目标 | 主要缺失 |
|:-----|:-----|:-----|:---------|
| `fts/config/settings.py` | 100% | 85%+ | ✅ 已达标 |
| `fts/data.py` | 100% | 85%+ | ✅ 已达标 |
| `fts/scheduler/engine.py` | 100% | 70%+ | ✅ 已达标 |
| `fts/factor_engine/meta_loop.py` | 84% | 90%+ | 剩余 69 行复杂路径 |

**验收标准**:
- [x] 总体覆盖率 ≥ 90%（当前 96%）
- [x] 各模块覆盖率达到上表目标
- [x] 全部测试通过（969 测试全绿）

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
- [x] 所有 state.json/pool.json/combo.json 写入使用临时文件+rename
- [x] 读取时校验 JSON 合法性，非法时使用备份

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

## 4. Phase D — 本地原生部署（v1.0.0）

> 目标: 本地进程可守护、可热重载、可监控、可运维
> 哲学: **边改边跑** — 修改代码后自动重载，进程崩溃后自动恢复

### 4.1 进程守护与热重载

#### 进程守护模型

```
┌─────────────────────────────────────────────────────────┐
│                  Supervisor/Watchdog                     │
│  重启策略: always (3 次/30 秒窗口熔断)                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────┐  ┌───────────────────────────┐     │
│  │  fts scheduler    │  │  fts monitor              │     │
│  │  ┌─────────────┐ │  │  ┌──────────────────────┐ │     │
│  │  │ L1 09:00    │ │  │  │ Prometheus 端点      │ │     │
│  │  │ L2 23:00    │ │  │  │ :9100/metrics        │ │     │
│  │  │ L3 Mon 06:00│ │  │  │ :9100/health         │ │     │
│  │  │ health 10min │ │  │  │                      │ │     │
│  │  └─────────────┘ │  │  └──────────────────────┘ │     │
│  └─────────────────┘  └───────────────────────────┘     │
│                                                         │
│  ┌─────────────────┐  ┌───────────────────────────┐     │
│  │  fts develop     │  │  fts cli                  │     │
│  │  watch + hotswap │  │  单次命令执行              │     │
│  └─────────────────┘  └───────────────────────────┘     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**推荐方案: Windows 原生进程管理**

| 方案 | 适用场景 | 说明 |
|:-----|:---------|:-----|
| `pythonw.exe` + watchdog | 开发/生产通用 | 后台无窗口运行，watchdog 进程自动重启 |
| NSSM (Non-Sucking Service Manager) | Windows 服务注册 | 将 `fts scheduler run` 注册为 Windows 服务 |
| Windows Task Scheduler | 轻量定时启动 | 按 crontab 触发 tasks，适合 L1/L2/L3 分离运行 |
| PowerShell 后台作业 | 临时/调试 | `Start-Job { fts scheduler run }` |

```powershell
# NSSM 注册为 Windows 服务
nssm install FTS-Scheduler "C:\Users\yangd\.pyenv\py310\python.exe" "d:\Programs\factor_system\fts\cli.py scheduler run"
nssm set FTS-Scheduler AppDirectory "d:\Programs\factor_system"
nssm set FTS-Scheduler AppStdout "d:\Programs\factor_system\logs\scheduler.log"
nssm set FTS-Scheduler AppStderr "d:\Programs\factor_system\logs\scheduler.err"
nssm set FTS-Scheduler Start SERVICE_AUTO_START
nssm start FTS-Scheduler
```

```powershell
# Task Scheduler 模式（推荐开发环境，每次任务独立启动）
# 每日 09:00 L1
schtasks /create /tn "FTS-L1" /tr "python d:\Programs\factor_system\fts\cli.py meta-loop run" /sc daily /st 09:00
# 每日 23:00 L2
schtasks /create /tn "FTS-L2" /tr "python d:\Programs\factor_system\fts\cli.py evolution run" /sc daily /st 23:00
# 每周一 06:00 L3
schtasks /create /tn "FTS-L3" /tr "python d:\Programs\factor_system\fts\cli.py portfolio run" /sc weekly /d MON /st 06:00
```

#### 热重载支持

```python
# fts/scheduler/hotswap.py（新增）

class HotSwapWatcher:
    """文件变更监听 + 模块热重载。
    
    开发模式下使用 `watchdog` 库监听 fts/ 目录变更，
    检测到修改后自动 reload 受影响模块，无需重启进程。
    
    使用方式:
        fts develop                       # 进入开发模式，监听 + 热重载
        fts develop --watch fts/factor_engine  # 只监听指定目录
    """
    
    def __init__(self, watch_dirs: list[str]):
        self.watch_dirs = watch_dirs
        self._observer = Observer()
        
    def start(self):
        """启动文件监听（非阻塞）。"""
        for d in self.watch_dirs:
            handler = ReloadHandler(self._reload_module)
            self._observer.schedule(handler, d, recursive=True)
        self._observer.start()
    
    def _reload_module(self, module_path: str):
        """热重载指定模块（importlib.reload）。"""
        module_name = self._path_to_module(module_path)
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.info("[hotswap] reloaded: %s", module_name)
```

**集成点**:
- `fts develop` 子命令 → 启动 scheduler + HotSwapWatcher
- 使用 `watchdog`（可选依赖，`pip install watchdog`）
- 不支持热重载的模块（C 扩展）自动跳过并打印警告
- scheduler 中的 task callable 在每次触发前重新 import，确保获取最新代码

#### 进程级 Watchdog

```python
# fts/scheduler/watchdog.py（新增）

class ProcessWatchdog:
    """进程级看门狗 — 自动重启崩溃的子进程。
    
    适用于 NSSM 服务或 `fts scheduler run --daemon` 模式下，
    监控子进程存活状态，崩溃后自动拉起。
    
    重启策略:
        连续重启 3 次且间隔 < 30 秒 → 熔断 5 分钟
        熔断期后重置计数器
    """
    
    def __init__(self, cmd: list[str]):
        self.cmd = cmd
        self._restart_count = 0
        self._last_restart = 0.0
        self._circuit_open_until = 0.0
    
    def run(self):
        """守护运行 — 自动重启直到收到 SIGTERM。"""
        while not self._should_stop():
            if self._in_circuit_break():
                time.sleep(5)
                continue
            proc = subprocess.Popen(self.cmd)
            proc.wait()
            self._handle_crash()
```

**验收标准**:
- [ ] `fts scheduler run --daemon` 以后台进程方式运行
- [ ] 进程崩溃后 5 秒内自动重启
- [ ] `fts develop` 模式下修改代码后自动重载
- [ ] 连续崩溃 3 次后熔断 5 分钟
- [ ] Windows Task Scheduler 注册的定时任务正常运行

---

### 4.2 实时监控与告警

| 能力 | 实现方式 | 说明 |
|:-----|:---------|:------|
| Prometheus 指标导出 | `prometheus_client` HTTP 端点 (:9100/metrics) | 因子数、Token 消耗、循环状态、IC 分布 |
| 健康检查端点 | `http.server` HTTP :9100/health | 进程存活 + 模块状态 |
| 文件告警 | 超过阈值时写入 `memory/alerts/` | 当前熔断状态、因子退役事件 |
| Windows Event Log | `logging.handlers.NTEventLogHandler` | Windows 原生事件日志 |
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

**HTTP 监控端点**（标准库实现，零依赖）:

```python
# fts/monitor/http_server.py（新增）

class MetricsHTTPServer:
    """轻量监控 HTTP 服务器（纯标准库，无 FastAPI/Flask 依赖）。
    
    端点:
        GET /health    → 200 OK + JSON 状态
        GET /metrics   → Prometheus 文本格式指标
        GET /          → HTML 仪表盘（可选）
    
    启动方式:
        fts monitor --http :9100       # 独立进程
        fts scheduler run --monitor    # 随 scheduler 启动
    """
    
    def __init__(self, host: str = "127.0.0.1", port: int = 9100):
        self.host = host
        self.port = port
    
    def start(self):
        """启动 HTTP 服务器（非阻塞线程）。"""
        server = HTTPServer((self.host, self.port), self._handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("[monitor] HTTP server started: http://%s:%d/metrics", self.host, self.port)
```

**验收标准**:
- [ ] `fts monitor` 启动后 `curl http://127.0.0.1:9100/health` 返回 200
- [ ] `curl http://127.0.0.1:9100/metrics` 返回 Prometheus 格式指标
- [ ] 因子退役时写入 `memory/alerts/` 告警文件
- [ ] 进程日志写入 `logs/` 目录，自动轮转

---

### 4.3 Scheduler 守护进程

```bash
# 后台守护模式（Windows 控制台）
fts scheduler run --daemon                  # 后台无窗口运行
fts scheduler run --daemon --monitor        # 同时启动 HTTP 监控

# 开发模式（热重载 + 前台运行）
fts develop                                 # 监听文件变更 + reload
fts develop --watch fts/factor_engine       # 只监听指定目录

# 单次执行（Task Scheduler / crontab 模式）
fts scheduler run --once                    # 运行一次所有任务
fts scheduler run --once --task l2_evolution_loop  # 只运行指定任务

# 查看执行历史
fts scheduler history --last 20
```

**日志管理**:

```python
# 日志文件结构
logs/
├── scheduler.log          # 调度器主日志（轮转：10MB × 5）
├── l1_meta_loop.log       # L1 执行日志
├── l2_evolution_loop.log  # L2 执行日志
├── l3_portfolio_loop.log  # L3 执行日志
├── health_check.log       # 健康检查日志
├── hotswap.log            # 热重载日志
├── monitor.log            # 监控 HTTP 服务器日志
└── crash_YYYYMMDD.log     # 崩溃转储
```

**验收标准**:
- [ ] `fts scheduler run --daemon` 后台运行，不占用终端
- [ ] `fts develop` 模式下修改 Python 文件后自动重载
- [ ] 日志自动轮转，不撑爆磁盘
- [ ] 可以通过 `fts scheduler history` 查看最近 20 次执行记录

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

**验收标准**:
- [ ] GitHub Actions CI 流水线通过
- [ ] PR 自动触发测试
- [ ] 测试报告输出到 PR 评论

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

---

### 4.6 本地原生部署文档

**文档清单**:

| 文档 | 内容 |
|:-----|:------|
| `docs/deploy/INSTALL.md` | 环境要求（Python 3.10+）、Git 克隆、pip 安装、依赖检查 |
| `docs/deploy/CONFIG.md` | settings.yaml 配置项说明、环境变量清单、示例配置文件 |
| `docs/deploy/WINDOWS.md` | Windows 服务注册（NSSM/Task Scheduler）、日志配置、自启动 |
| `docs/deploy/OPS.md` | 日常运维操作、`fts scheduler history` 查看历史、日志排查、数据备份 |
| `docs/deploy/DEV.md` | `fts develop` 热重载开发模式、修改代码后自动重载流程 |
| `docs/deploy/SECURITY.md` | API Key 管理（环境变量/加密文件）、端口绑定（仅 127.0.0.1） |

---

## 5. 版本路线总图

### 5.1 版本发布时间线

```
v0.2.0 ───────────────────────────────────────────────────────────── 已完成
    │
    └── 2-3 周 ──→ v0.3.0 ─── 2-3 周 ──→ v0.4.0 ─── 1-2 周 ──→ v0.5.0 ─── 1-2 周 ──→ v1.0.0
                    │                   │                   │                   │
                    ├ Data-Core 集成     ├ EliteTracker       ├ RegimeAware        ├ 进程守护/热重载
                    ├ FDT 清除          ├ AutoRetire         ├ CostModel          ├ HTTP 监控端点
                    ├ 覆盖补齐          ├ WalkForward        ├ StressTest         ├ Windows 服务
                    └ 原子持久化        └ 测试 ~90 新增      └ 测试 ~60 新增       ├ CI/CD
                                                                                 ├ E2E 测试
                                                                                 └ 本地部署文档
```

### 5.2 各版本测试与覆盖目标

| 版本 | 测试用例数 | 总体覆盖率 | 差距关闭数 | 核心模块覆盖 |
|:-----|:----------|:-----------|:-----------|:-------------|
| v0.2.0 | 778 | 89% | 7/7 | evolution_loop 99%, llm 77% |
| v0.3.0 | 969 | 96% | 7/7 + 4 新增 | data.py 100%, config 100%, engine 100%, meta_loop 84% |
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
| Windows 服务稳定性 | 中 | v1.0.0 可靠性 | NSSM 兜底 + watchdog 进程级自愈 + 熔断机制 |

### 6.3 决策记录

| 决策 | 方案 | 理由 |
|:-----|:-----|:------|
| EliteTracker 独立模块 vs 合并到 monitor | 独立模块 | monitor 职责是系统级监控，tracker 是因子级，分离更清晰 |
| WalkForward 替代 vs 并行 | 并行 | 保留现有单窗口模式作为快速验证，走航作为可选强化验证 |
| Regime 检测用规则 vs ML | 规则 | 初始版本需要可解释性，ML 模型在数据充分后引入 |
| 成本模型配置在 pyproject.toml vs settings.yaml | settings.yaml | 成本参数是运行时配置，不是包配置 |
| 部署方式 Docker vs 原生 | **本地原生主选** | 边改边跑需要热重载，Docker 不方便本地开发；进程由 NSSM/Task Scheduler 管理 |
| 监控 HTTP 用标准库 vs FastAPI | 标准库 | 零额外依赖，生产最小化攻击面；性能需求极低（秒级查询） |

---

## 7. 门禁条件

### 进入 v0.3.0

- [x] `python -m pytest tests/` 全部通过（969 全绿）
- [x] 总体覆盖率 ≥ 90%（96%）
- [x] `grep -r "futures_data_core" fts/` 空
- [x] `fts/data.py` 覆盖率 ≥ 85%（100%）
- [x] `fts/config/settings.py` 覆盖率 ≥ 85%（100%）
- [x] 所有 state.json 写入使用原子操作
- [x] version 更新为 v0.3.0

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
- [ ] `fts scheduler run --daemon` 后台守护正常运行
- [ ] `fts develop` 热重载模式可用
- [ ] HTTP 监控端点 `http://127.0.0.1:9100/health` 可访问
- [ ] NSSM/Task Scheduler 服务注册文档完善
- [ ] GitHub Actions CI 流水线通过
- [ ] 10+ E2E 测试全部通过
- [ ] 本地原生部署文档完整
- [ ] 总体覆盖率 ≥ 92%

---

## 8. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:------|
| **v0.3.0** | 2026-07-19 | Data-Core 集成适配层、FDT 残留清除、原子持久化、覆盖率 96%、969 测试全绿 |
| **v0.2.0** | 2026-07-19 | 调整部署策略：移除 Docker 化，改为本地原生部署；新增进程守护/热重载/Windows 服务支持 |
| **v0.1.0** | 2026-07-18 | 初版，汇总 v0.3.0 ~ v1.0.0 全部生产就绪工作 |
