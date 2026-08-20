# F.4 因子定义分发接口技术文档（FTS → 下游消费方）— 详细设计

> 版本: v3.1.0+3-draft
> 创建: 2026-08-20
> 状态: **设计中**（随 plans/57 双轨并存的"定义分发通道"落地，阶段 0 契约先行）
> 关联: [F.3-signal-contract-v1-design.md](F.3-signal-contract-v1-design.md)（信号矩阵直读契约，双轨并存的对账/降级通道）、[57-dual-system-factor-strategy-split-plan.md](../plans/57-dual-system-factor-strategy-split-plan.md)、FTS `http_server.py`（服务端实现）、RD `external_adapter.py`（消费端适配器实现）
> 读者: **下游消费系统（Regime-Driven）开发人员**——本文档是定义分发接口的唯一技术规格，按此开发即可对接，无需阅读 FTS 内部实现。
> 背景: 双系统切分（因子生产 / 策略合成）的第二条通道——FTS **只下发因子定义（code+params），消费方用本地数据自行组装计算**，与 F.3 信号矩阵直读通道**双轨并存**（自组装为主、信号矩阵作对账基准与降级）。

---

## 1. 目标与范围

**目标**: 固化"因子定义分发"接口契约，使下游消费方（RD）仅凭本契约即可：
- 拉取 FTS 已治理的 active+elite 因子定义（code / params / 元数据）；
- 用本地 DataLoader 数据执行因子，产出与 FTS 信号矩阵**同口径**的因子信号；
- 完成因子注册、状态映射、生命周期刷新、对账验证，全程无需 import FTS 任何模块。

**范围**: 传输方式（REST 主 / duckdb 兜底）、响应 schema、code 契约、数据契约、portable 语义、状态映射、增量/幂等/版本、适配器接入指引、对账验证、错误与降级。

**不在范围**: 因子生产/演化策略本身（FTS 内部）、组合合成算法（RD strategy_synthesis）、信号矩阵直读通道（见 F.3）。

**架构定位**:

```
FTS (因子工厂)                    RD (消费方)
factor_catalog_futures.duckdb → ExternalFactorAdapter
  code: 自包含函数                 ├─ 只同步 active+elite 因子
  params/sharpe/status            ├─ code→Factor 包装
  REST /api/factors/definitions   ├─ DataLoader 数据注入
  (duckdb 直读兜底)                └─ 注册进 FactorRegistry
                                        ↓
                              sector_rules 因子池（按 factor_id 引用）
```

---

## 2. 传输方式与端点规格

### 2.1 获取方式（双源）

| 源 | 优先级 | 说明 |
|:--|:--|:--|
| REST | **默认** | `GET /api/factors/definitions`，见 §2.2 |
| DuckDB 直读 | 兜底 | FTS 服务不可用时，以 `read_only` 打开 `factor_catalog_futures.duckdb` 查询 `factor_catalog` 表（SQL 见 §2.4） |

### 2.2 REST 端点

- **URL**: `GET http://<fts_host>:<port>/api/factors/definitions`
- **默认端口**: 8080（`fts ui --port 8080`）
- **Content-Type**: `application/json; charset=utf-8`
- **方法**: GET（只读，无副作用）

### 2.3 响应 Schema（REST 与 duckdb 直读同构）

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-20T15:00:00+08:00",
  "count": 85,
  "factors": [
    {
      "factor_id": "fct_71372ef2",
      "name": "roll_curvature_energy",
      "code": "def factor_program(data, params):\n    import numpy as np\n    ...",
      "code_hash": "a1b2c3d4e5f67890",
      "params": {"window": 20},
      "params_hash": "1122334455667788",
      "market": "futures",
      "status": "active",
      "is_elite": true,
      "portable": true,
      "non_portable_reason": "",
      "factor_scope": {"subchain_scope": "all", "subchain_specific": []},
      "sharpe": 1.32,
      "ic": 0.041,
      "icir": 0.55,
      "max_drawdown": -0.18,
      "turnover_monthly": 0.62,
      "economic_logic": {"story": "展期收益曲率不对称"},
      "style_tags": ["term_structure"]
    }
  ]
}
```

### 2.4 duckdb 直读 SQL（兜底）

```sql
SELECT factor_id, name, code, params, market, status, is_elite,
       sharpe, ic, icir, max_drawdown, turnover_monthly,
       economic_logic, style_tags
FROM factor_catalog
WHERE status = 'active' AND is_elite = TRUE
ORDER BY sharpe DESC;
```

### 2.5 字段说明

| 字段 | 类型 | 必填 | 说明 |
|:--|:--|:--|:--|
| `schema_version` | int | 是 | 契约版本。FTS 在契约变更时递增；消费方校验，不兼容 → 告警 + 降级（§8） |
| `generated_at` | str(ISO8601) | 是 | 服务端生成时间 |
| `count` | int | 是 | `factors` 长度 |
| `factors[].factor_id` | str | 是 | 因子唯一 ID（消费方注册 key） |
| `factors[].name` | str | 是 | 因子名（人读） |
| `factors[].code` | str | **portable=true 时必填** | 自包含因子代码，见 §3 |
| `factors[].code_hash` | str | 是 | `sha256(code)` 前 16 位；消费方用于增量编译判定 |
| `factors[].params` | object | 是 | 因子参数（注入 `factor_program(data, params)` 第二参） |
| `factors[].params_hash` | str | 是 | 参数指纹；与 code_hash 组合判定"定义是否变更" |
| `factors[].market` | str | 是 | 因子市场（`futures`） |
| `factors[].status` | str | 是 | FTS 状态：`active` / `degraded` / `shadow` / `retired` |
| `factors[].is_elite` | bool | 是 | 精英因子标记（本接口固定为 active+elite） |
| `factors[].portable` | bool | 是 | **true=code 可独立执行**；false=仅下发元数据、code 为空（见 §3.3） |
| `factors[].non_portable_reason` | str | 否 | portable=false 时的原因（如 `depends_on_fts_runtime`） |
| `factors[].factor_scope` | object | 否 | 子链范围：`{subchain_scope: "all"\|["链1",...], subchain_specific: [...]}`。`all`=通用因子；否则为产业链特异因子（仅对 `subchain_specific` 品种集合有效） |
| `factors[].sharpe/ic/icir/max_drawdown/turnover_monthly` | float | 否 | 质量画像（消费方可用于加权/筛选） |
| `factors[].economic_logic/style_tags` | object/array | 否 | 经济学故事 / 风格标签 |

---

## 3. 因子 Code 契约

### 3.1 函数签名（唯一入口）

```python
def factor_program(data, params):
    """计算因子信号。

    :param data: pd.DataFrame，列契约见 §3.2；行序 = 时间升序
    :param params: dict，因子参数（来自响应 factors[].params）
    :return: np.ndarray，float64，长度与 data 行数严格一致；NaN 表示缺失
    """
```

**输出对齐规则**（与 FTS 信号矩阵口径一致）：
- 返回值是 `np.ndarray`（float64），`len == len(data)`；
- 数据不足时返回全 0 或含 NaN 数组（因子内部自行处理，如 `n < 40 → zeros(n)`）；
- 消费方将 ndarray 包装为 `pd.Series(index=data['trade_date'])` 后注册。

### 3.2 data 列契约

| 列 | 必填 | 说明 |
|:--|:--|:--|
| `trade_date` | 是 | datetime64 交易日期（输出 Series 的 index） |
| `open` / `high` / `low` / `close` | 是 | 价格四要素 |
| `volume` | 是 | 成交量 |
| `open_interest` | 否 | 持仓量（当前 active 因子未使用，契约预留） |
| `settle` | **因子用到时必填** | 结算价。QuantData 无权威 settle（GAP-158），**统一代理公式 `settle = (high+low+close)/3`**（与 FTS 执行口径一致），无效值（NA/≤0）一律替换为代理值 |

**数据口径**: 与 FTS 一致使用 QuantData **主力复权连续序列**（`continuous_daily`，`series_type='main'`），消费方 `DataLoader.load_main_continuous` 已覆盖。**禁止**使用未复权原始合约数据，否则对账必失败。

> 实测: active 86 因子仅消费 `close/high/low/open/volume` 五列（`fct_91eb37e6` 经 DSL 额外使用 `settle`）。消费方 DataLoader 提供上述全部列即满足全部因子。

### 3.3 portable 语义（FTS 预校验）

FTS 服务端在生成响应时对每个因子做 AST 预校验，命中以下任一规则 → `portable=false`，**code 不下发**（置空字符串），仅下发元数据：

| 规则 | 检测 | 处理 |
|:--|:--|:--|
| R1 依赖 FTS 内部模块 | `import fts` / `from fts` / `eval_fts_expr` | 转译为纯 numpy 后重新入库（见 §3.4），转译完成前不下发 code |
| R2 白名单外 import | `import`/`from` 目标不在 `{numpy, pandas, math, statistics}` | 拒绝下发，登记 gap |

当前 active+elite 仅 `fct_91eb37e6` 命中 R1，已登记转译（§3.4）。

### 3.4 fct_91eb37e6 转译登记（DSL → 纯 numpy）

原始表达式: `zscore(ts_open_close_diff(close, ts_open_close_diff(close, ts_hpm_2(settle, 171))))`

| 算子 | FTS 语义 | 转译 numpy |
|:--|:--|:--|
| `ts_hpm_2(settle, 171)` | `r = settle.pct_change().fillna(0)`；`pos = r.where(r>0, 0)`；`(pos**2).rolling(171, min_periods=2).mean().fillna(0)` | 上偏二阶矩滚动均值，`min_periods=2` |
| `ts_open_close_diff(a, b)` | `(b - a).fillna(0)`（签名 `(open_p, close)` 返回 `close-open_p`） | 组合后：`B = A - close`；`C = B - close = A - 2*close` |
| `zscore(x)` | `(x - x.mean()) / x.std()`，`std==0` 时原样（**全序列**，非滚动；`std` 默认 ddof=1） | `(C - C.mean()) / C.std()`（`np.nanstd` 不适用，须复刻 pandas 语义） |

转译要点:
1. `settle` 按 §3.2 代理公式注入（`(high+low+close)/3`）后再算收益；
2. 严格复刻 pandas 语义：`pct_change().fillna(0)`（首日 NaN→0）、`rolling` 的 min_periods、zscore 的 ddof=1；
3. 转译产物作为**新版 code** 更新入库（`code_hash` 变化 → 消费方自动增量重编译）。

---

## 4. 状态映射（FTS → RD）

消费方将 FTS `status` 映射为本地 `FactorStatus` 并驱动加权/熔断:

| FTS status | RD FactorStatus | 决策语义 |
|:--|:--|:--|
| `active` | `ACTIVE` | 全权参与合成 |
| `shadow` | `ACTIVE`（降权标记） | 对照实验，合成半权 |
| `degraded` | `ACTIVE`（零权标记） | 退化（子链失效收缩），不参与合成但保留定义 |
| `retired` | 剔除（不注册） | 强制退出因子池 |

> F.3 §2.1 的三列（`schema_version`/`factor_status`/`factor_scope`）与信号矩阵直读共用同一状态源；**定义分发通道的状态刷新**：消费方每次 `sync()` 全量比对 `status`/`is_elite` 变化，`retired`/非 elite 因子从注册表移除。

---

## 5. 增量 / 幂等 / 版本

- **增量**: 消费方按 `(code_hash, params_hash)` 判定定义是否变更；未变 → 复用已编译函数（不重复 exec）；变更 → 重编译重注册。
- **幂等**: `sync()` 可重复执行；注册以 `factor_id` 为 key，重复同步无副作用。
- **版本**: `schema_version` 由 FTS 递增；消费方拉取时校验，不兼容 → 告警 + 降级（§8）。

---

## 6. 消费方适配器接入指引（ExternalFactorAdapter）

本节给出 RD 侧实现的最小契约，代码示例为**规范实现**（阶段 2 落地）。

### 6.1 处理流程

```
sync():
  defs = source.fetch()                      # REST(默认) 或 duckdb 兜底
  for d in defs.factors:
      if not d.portable: skip(reason)        # R1/R2 拦截
      if (d.code_hash, d.params_hash) 未变: continue
      ast_check(d.code)                      # 复核校验，见 6.3
      fn = sandbox_exec(d.code)              # 受限 exec，见 6.4
      factor = wrap(fn, d.params, d)         # Factor 子类，注入 params
      registry.register(d.factor_id, factor) # key = factor_id
      status_map(d.status)                   # §4 映射 + lifecycle 联动
refresh(): sync() 增量更新 + 每日状态刷新
```

### 6.2 Factor 包装（对齐 RD `Factor._compute` 契约）

```python
class ExternalFactor(Factor):
    def __init__(self, factor_id, code_fn, params, meta):
        super().__init__()
        self.name = factor_id          # 注册 key = factor_id
        self._code_fn = code_fn
        self._params = params
        self._meta = meta              # sharpe/ic/status/scope...

    def _compute(self, data):
        arr = self._code_fn(data, self._params)      # factor_program 契约
        arr = np.asarray(arr, dtype=np.float64)
        if len(arr) != len(data):
            raise ValueError("输出长度与 data 不一致")
        return pd.Series(arr, index=pd.DatetimeIndex(data["trade_date"]))
```

### 6.3 AST 复核规则（消费方独立防线，与 FTS 预校验叠加）

拒绝条件（任一命中即丢弃该因子并告警）:
1. `Import` / `ImportFrom` 目标不在白名单 `{numpy, pandas, math, statistics}`；
2. `Call` 中出现 `eval` / `exec` / `compile` / `__import__` / `open` / `getattr`；
3. `Attribute` 访问 `__subclasses__` / `__globals__` / `__builtins__` 等双下划线魔方。

### 6.4 受限 exec 沙箱（globals 白名单）

```python
SAFE_GLOBALS = {
    "__builtins__": {k: __builtins__[k] for k in
                     ("abs","min","max","sum","len","range","enumerate",
                      "zip","sorted","reversed","isinstance","round","int",
                      "float","str","bool","list","dict","tuple","set",
                      "map","filter","any","all","repr")},
    "numpy": np, "np": np, "pandas": pd, "pd": pd,
    "math": math, "statistics": statistics,
}
# exec(code, SAFE_GLOBALS, local_ns); 取 local_ns["factor_program"]
```

> 白名单与 FTS `factor_program._SANDBOX_ALLOWED_MODULES` 保持一致；FTS 新增算子/模块时必须同步本文档 §6.4（两侧漂移是已知风险，阶段 3 对账兜底）。

### 6.5 sector_rules 引用

`config/regime_routing_rules.yaml` 的 `active_factors` 支持 `fts:<factor_id>` 前缀引用（与现有命名因子并存）:

```yaml
demand_driven:
  active_factors:
    - "fts:fct_71372ef2"          # FTS 因子（按 factor_id）
    - "rebar_delivery_volume"     # 本地命名因子（存量）
```

---

## 7. 对账验证（双轨成立的前提证据）

消费方必须执行对账，证明"自组装信号 == FTS 信号矩阵"（F.3 双轨并存的核心断言）:

| 项 | 要求 |
|:--|:--|
| 基准 | FTS `l3_signal_matrix`（`signal_client.fetch_training` 拉历史切片，F.3 训练模式） |
| 自算 | 同一 (factor_id, symbol, date)，本地 `DataLoader` 数据 + 因子 code 计算 |
| 断言 | 逐 (factor_id, symbol, date) 差异 `< 1e-8`（绝对/相对容差） |
| 输出 | 对账报告（通过率 / 偏差 TOP 因子 / 系统性偏差归类） |
| 系统性偏差排查 | 优先查数据口径（复权、主力连续、`settle` 代理公式、列名），非 code 逻辑 |

---

## 8. 错误处理与降级语义

| 场景 | 消费方行为 |
|:--|:--|
| REST 拉取失败 | 连续失败 N 次（默认 3）触发熔断 → 冷却 5 分钟自动重试 → 熔断期降级 duckdb 直读 |
| duckdb 直读也失败 | 降级 RD 本地 11 因子规则法（现有全链路，纯本地可运行）；报告注明 `degraded: fts_definition_unavailable` |
| 单因子 code 编译失败 | 丢弃该因子 + 告警，不影响其余因子；登记 gap |
| `schema_version` 不兼容 | 告警 + 降级本地规则法（同 F.3 §7） |
| 对账偏差超限 | 阻断该因子进入生产因子池，退回信号矩阵直读通道（F.3），直至偏差消除 |

---

## 9. 验收断言（可验证）

| 断言 | 验证方式 |
|:--|:--|
| REST 响应含 §2.3 全部字段 | 消费端单测 / `curl` 断言 |
| `fct_91eb37e6` 转译后 `portable=true` 且 code 无 `fts` import | 服务端单测 + 库内 `code` 检查 |
| 非 portable 因子 code 为空串 + `non_portable_reason` 非空 | 服务端单测 |
| 消费方 AST 复核拒绝黑名单 code | 适配器单测（构造恶意 code 断言拒绝） |
| `(code_hash, params_hash)` 未变不重编译 | 适配器单测（mock 定义变更前后编译次数） |
| 自组装信号与 FTS 信号矩阵差异 < 1e-8 | 对账脚本断言 |
| `fts:<factor_id>` 引用被合成层正确解析 | RD 路由/合成层单测 |

---

## 一致性元数据

| 代码 → 文档映射 | 可验证断言 | 检验方式 |
|:--|:--|:--|
| FTS `http_server._build_factor_definitions_from_duckdb` → §2/§3.3 | 响应含 §2.5 字段；portable 规则生效 | 服务端单测 + `curl` |
| FTS 预校验函数（R1/R2）→ §3.3 | fct_91eb37e6 转译后 portable=true | 库内 code 断言 + 单测 |
| RD `external_adapter.py` → §6 | 编译/校验/注册/状态映射全流程 | RD 适配器单测 |
| RD AST 复核 → §6.3 | 黑名单 code 被拒绝 | 恶意 code 单测 |
| RD 对账脚本 → §7 | diff < 1e-8 | 对账报告 |
| RD `sector_rules` 解析 → §6.5 | `fts:` 前缀解析 | 合成层单测 |
