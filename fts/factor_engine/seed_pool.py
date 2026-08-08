"""
loop_engine/seed_pool.py — 种子池

HARNESS §11-loop-engineering.md §2.2:
    从 multi_factor_strategy.py 提取 9 个内置因子作为种子，启动 L2 演化。

种子因子清单（来自 multi_factor_strategy.py FACTOR_WEIGHTS + A 股补充）:
    1. momentum              动量因子（全市场）
    2. volatility_reversion  波动率回归（全市场）
    3. volume_flow           资金流（全市场）
    4. macro_regime          宏观制度（全市场）
    5. rate_proxy            利率代理（全市场）
    6. pmi_proxy             PMI 代理（全市场）
    7. value_factor          价值因子（A 股）
    8. quality_factor        质量因子（A 股）
    9. size_factor           市值因子（A 股）

外部种子因子（473 个）:
    - WQ 101 Alpha         101 个
    - Qlib 158             158 个
    - 国泰君安 191 Alpha   191 个
    - 基本面/另类/宏观      23 个

版本: v1.2.0（新增种子因子相关性预检）
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .contracts import EconomicLogic, FactorCorrelation, FactorProgram, FactorSignature
from .factor_program import FactorExecutor, create_factor_program
from .seed_data import load_all_external_seeds

logger = logging.getLogger(__name__)


# ─── 种子因子代码模板 ─────────────────────────────────────

# 每个种子因子以可执行 Python 代码形式提供，符合 factor_program() 接口约束。
# 参数空间用于 optuna 微观演化搜索。

_SEED_MOMENTUM_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 价格变化率
    chg = (close - np.roll(close, window)) / np.maximum(np.roll(close, window), 1e-10)
    chg[:window] = 0
    # MA 斜率
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    ma_slope = np.zeros(n)
    if n > 1:
        ma_slope[1:] = (ma[1:] - ma[:-1]) / np.maximum(ma[:-1], 1e-10)
    score = 0.5 * np.tanh(chg / 0.05) + 0.3 * np.tanh(ma_slope * 30) + 0.2 * np.tanh(chg / 0.1)
    return np.clip(score, -1.0, 1.0)
"""

_SEED_VOLATILITY_REVERSION_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    bb_width_threshold = float(params.get('bb_width_threshold', 0.05))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 布林带
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    std = np.array([np.std(close[max(0,i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
    upper = ma + 2 * std
    lower = ma - 2 * std
    bb_pos = (close - lower) / np.maximum(upper - lower, 1e-10)
    bb_pos = np.clip(bb_pos, 0, 1)
    # 高波动回归：bb_pos 接近 1 偏空，接近 0 偏多
    score = (0.5 - bb_pos) * 1.0
    return np.clip(score, -1.0, 1.0)
"""

_SEED_VOLUME_FLOW_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 10))
    n = len(close)
    if n < window + 1:
        return np.zeros(n)
    # 量比
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    vol_ratio = volume / np.maximum(avg_vol, 1e-10)
    # 价格变化
    chg = np.zeros(n)
    chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    # 放量上涨 → 多头；放量下跌 → 空头
    score = np.where(
        vol_ratio > 1.3,
        np.tanh(chg / 0.02) * 0.5,
        np.where(vol_ratio < 0.7, np.tanh(chg / 0.05) * 0.3, 0)
    )
    return np.clip(score, -1.0, 1.0)
"""

_SEED_MACRO_REGIME_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'macro_signal' in (data.columns if hasattr(data, 'columns') else data):
        macro = data['macro_signal'].values if hasattr(data, 'macro_signal') else data['macro_signal']
        score = np.where(macro == 'bull', 0.5,
                np.where(macro == 'bear', -0.5, 0))
        return np.clip(score, -1.0, 1.0)
    else:
        window = 60
        if n < window:
            return np.zeros(n)
        trend = np.zeros(n)
        trend[window:] = (close[window:] - close[:-window]) / np.maximum(close[:-window], 1e-10)
        score = np.tanh(trend * 10) * 0.3
        return np.clip(score, -1.0, 1.0)
"""

_SEED_RATE_PROXY_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'rate_mom' in (data.columns if hasattr(data, 'columns') else data):
        rate_mom = data['rate_mom'].values if hasattr(data, 'rate_mom') else data['rate_mom']
        score = -np.tanh(rate_mom / 0.25)
        return np.clip(score, -1.0, 1.0)
    else:
        window = 30
        if n < window:
            return np.zeros(n)
        vol_std = np.array([np.std(close[max(0,i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
        score = -np.tanh(vol_std / np.maximum(np.mean(vol_std), 1e-10) * 2) * 0.3
        return np.clip(score, -1.0, 1.0)
"""

_SEED_PMI_PROXY_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = len(close)
    if 'pmi' in (data.columns if hasattr(data, 'columns') else data):
        pmi = data['pmi'].values if hasattr(data, 'pmi') else data['pmi']
        pmi_mom = data['pmi_mom'].values if hasattr(data, 'pmi_mom') in (data.columns if hasattr(data, 'columns') else data) else None
        level = np.tanh((pmi - 50.0) / 5.0)
        if pmi_mom is not None:
            mom = np.tanh(pmi_mom / 1.0) * 0.5
            score = level * 0.6 + mom * 0.4
        else:
            score = level
        return np.clip(score, -1.0, 1.0)
    else:
        window = 20
        if n < window:
            return np.zeros(n)
        ma = np.convolve(close, np.ones(window)/window, mode='same')
        ma_slope = np.zeros(n)
        if n > 1:
            ma_slope[1:] = (ma[1:] - ma[:-1]) / np.maximum(ma[:-1], 1e-10)
        score = np.tanh(ma_slope * 50) * 0.4
        return np.clip(score, -1.0, 1.0)
"""

# ─── A 股种子因子 ─────────────────────────────────────────

_SEED_VALUE_FACTOR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 用价格/成交量比值近似估值（价格低+放量=价值凸显）
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    pct_rank = np.argsort(np.argsort(close)) / max(n - 1, 1)  # 0~1 价格分位
    vol_ratio = volume / np.maximum(avg_vol, 1e-10)
    # 低价+放量 → 价值信号
    score = (1 - pct_rank) * np.tanh(vol_ratio * 0.5) - 0.3
    return np.clip(score, -1.0, 1.0)
"""

_SEED_QUALITY_FACTOR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 用价格稳定性近似质量（低波动+稳定上升=高质量）
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    rolling_vol = np.array([
        np.std(returns[max(0, i-window+1):i+1]) if i >= 1 else 0
        for i in range(n)
    ])
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    ma_slope = np.zeros(n)
    if n > 1:
        ma_slope[1:] = (ma[1:] - ma[:-1]) / np.maximum(ma[:-1], 1e-10)
    # 低波动+正斜率=高质量
    quality_score = np.tanh(-rolling_vol * 20 + 0.5) + np.tanh(ma_slope * 30)
    return np.clip(quality_score, -1.0, 1.0)
"""

_SEED_SIZE_FACTOR_CODE = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window:
        return np.zeros(n)
    # 用成交量/价格近似市值效应（小市值效应代理）
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    vol_deviation = volume / np.maximum(avg_vol, 1e-10)  # 成交量偏离
    price_level = close / np.maximum(np.mean(close[:window]), 1e-10)  # 价格水平
    # 小市值代理：低成交量+低价 = 偏小盘
    size_proxy = np.tanh(1.0 / (price_level + 0.1)) * np.tanh(1.0 / (vol_deviation + 0.1))
    # 小盘溢价：做多小盘
    score = size_proxy * 0.5
    return np.clip(score, -1.0, 1.0)
"""


# ─── 种子因子定义 ─────────────────────────────────────────

_SEED_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "momentum",
        "code": _SEED_MOMENTUM_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=3, institutional=5,
            narrative="动量因子：投资者过度反应/反应不足导致价格延续。理论支撑=行为金融学动量效应。",
        ),
    },
    {
        "name": "volatility_reversion",
        "code": _SEED_VOLATILITY_REVERSION_CODE,
        "params": {"window": 20, "bb_width_threshold": 0.05},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="波动率回归：高波动后均值回归。理论支撑=波动率锥与均值回归现象。",
        ),
    },
    {
        "name": "volume_flow",
        "code": _SEED_VOLUME_FLOW_CODE,
        "params": {"window": 10},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=15,
        ),
        "economic_logic": EconomicLogic(
            theory=3, behavioral=4, microstructure=5, institutional=4,
            narrative="资金流：放量方向反映知情交易者意图。理论支撑=微观结构信息不对称。",
        ),
    },
    {
        "name": "macro_regime",
        "code": _SEED_MACRO_REGIME_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["macro_signal"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=2, institutional=5,
            narrative="宏观制度：bull/bear/neutral 三态。理论支撑=宏观周期理论。",
        ),
    },
    {
        "name": "rate_proxy",
        "code": _SEED_RATE_PROXY_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["rate_mom"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=5,
            narrative="利率代理：LPR1Y 环比。理论支撑=利率平价与融资成本理论。",
        ),
    },
    {
        "name": "pmi_proxy",
        "code": _SEED_PMI_PROXY_CODE,
        "params": {},
        "signature": FactorSignature(
            input_fields=["pmi", "pmi_mom"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=5,
            narrative="PMI 代理：制造业景气度。理论支撑=景气周期理论。",
        ),
    },
    # ── A 股种子因子 ──
    {
        "name": "value_factor",
        "code": _SEED_VALUE_FACTOR_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=3, microstructure=3, institutional=4,
            narrative="价值因子：低价+放量近似估值安全边际。理论支撑=价值投资理论。",
        ),
    },
    {
        "name": "quality_factor",
        "code": _SEED_QUALITY_FACTOR_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=4, behavioral=3, microstructure=4, institutional=4,
            narrative="质量因子：低波动+稳定上涨代理盈利能力。理论支撑=质量溢价理论。",
        ),
    },
    {
        "name": "size_factor",
        "code": _SEED_SIZE_FACTOR_CODE,
        "params": {"window": 20},
        "signature": FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=30,
        ),
        "economic_logic": EconomicLogic(
            theory=5, behavioral=4, microstructure=3, institutional=3,
            narrative="市值因子：成交量+价格代理市值大小。理论支撑=小市值效应。",
        ),
    },
]


# ─── 种子池管理器 ─────────────────────────────────────────

class SeedPool:
    """种子池管理器 — 加载/查询/注入种子因子。

    种子数据来源:
        - 股票内置: _SEED_DEFINITIONS (9 个)
        - 股票外部: WQ101 (101) + Qlib158 (158) + GTJA191 (191) + 基本面 (23) = 473 个
        - 期货专用: seed_data_futures_full._FUTURES_FULL_DEFINITIONS (81 个, 14 大因子家族)

    Args:
        trace_id: 全链路 trace_id。
        market: 市场类型 ("futures" 或 "stock")。
            - "futures"（默认）: 加载 81 个期货专用种子（14 大因子家族）。
            - "stock": 加载 9 内置 + 473 外部股票种子。

    Usage:
        pool = SeedPool()  # 默认 futures
        all_seeds = pool.load_all_seeds()

        stock_pool = SeedPool(market="stock")
        stock_seeds = stock_pool.load_all_seeds()
    """

    def __init__(
        self,
        trace_id: Optional[str] = None,
        market: Optional[str] = None,
        use_yaml: bool = True,
    ):
        self._trace_id = trace_id
        if market is None:
            from fts.config.settings import get_config
            market = get_config().default_market
        self._market = market
        self._cache: dict[str, FactorProgram] = {}
        self._use_yaml = use_yaml

        # ── 关键日志: 确认默认市场配置 ──
        if market == "futures":
            logger.info(
                "[SeedPool.init] ★ 期货模式 (默认) market=futures, trace_id=%s, "
                "use_yaml=%s, 将加载 81 个期货专用种子 (14 大因子家族)",
                trace_id, use_yaml,
            )
        elif market == "stock":
            logger.info(
                "[SeedPool.init] ★ 股票模式 market=stock, trace_id=%s, "
                "use_yaml=%s, 将加载 9 内置 + 473 外部股票种子",
                trace_id, use_yaml,
            )
        else:
            logger.warning(
                "[SeedPool.init] ⚠ 未知市场类型 market=%s, trace_id=%s",
                market, trace_id,
            )

    @classmethod
    def get_seed_counts(cls) -> dict[str, int]:
        """返回各市场种子因子的最新动态数量（消除硬编码）。"""
        from .seed_data_futures_full import get_futures_full_seed_count
        from .seed_data.loader import get_external_seed_count

        wq, ql, gj, fd, jq, ext_total = get_external_seed_count()
        futures_total = get_futures_full_seed_count()
        stock_internal = len(_SEED_DEFINITIONS)
        stock_total = stock_internal + ext_total

        counts = {
            "stock_internal": stock_internal,
            "stock_wq101": wq,
            "stock_qlib158": ql,
            "stock_gtja191": gj,
            "stock_fundamental": fd,
            "stock_jq": jq,
            "stock_external_total": ext_total,
            "stock_total": stock_total,
            "futures_total": futures_total,
        }

        # ── 关键日志: 动态种子统计快照 ──
        logger.info(
            "[SeedPool.get_seed_counts] 动态统计: 期货=%d (14 家族) | "
            "股票=9 内置 + %d 外部 (WQ101=%d, Qlib158=%d, GTJA191=%d, 基本面=%d, JQ=%d) = %d",
            futures_total, ext_total, wq, ql, gj, fd, jq, stock_total,
        )
        return counts

    def load_all_seeds(
        self,
        include_external: bool = True,
    ) -> list[FactorProgram]:
        """加载全部种子因子。

        优先从 YAML 文件加载，失败时回退到硬编码路径。

        Args:
            include_external: 仅 market="stock" 时有效。
                是否加载 WQ 101 / Qlib 158 / 国泰君安 191 外部种子（默认 True）。
            use_yaml: 是否优先使用 YAML 加载（默认 True）。

        Returns:
            list[FactorProgram] — 所有种子因子列表（不含 L1 注入）。
        """
        if self._cache:
            logger.debug("[SeedPool.load] 使用缓存, count=%d", len(self._cache))
            return self._list_base_seeds()

        # ── 路径 1: YAML 加载（优先） ──
        if self._use_yaml:
            try:
                from .seed_loader import load_all_yaml_seeds
                logger.info("[SeedPool.load] 尝试 YAML 种子加载 (market=%s, include_external=%s)...", self._market, include_external)
                yaml_seeds = load_all_yaml_seeds(
                    trace_id=self._trace_id,
                    market=self._market,
                    include_external=include_external,
                )
                if yaml_seeds:
                    for fp in yaml_seeds:
                        self._cache[fp["name"]] = fp
                    logger.info(
                        "[SeedPool.load] ✅ YAML 种子加载成功: count=%d, sample=%s",
                        len(yaml_seeds), [s["name"] for s in yaml_seeds[:5]],
                    )
                    return self._list_base_seeds()
                else:
                    logger.warning("[SeedPool.load] YAML 种子为空，回退到硬编码路径")
            except Exception as e:
                logger.warning("[SeedPool.load] YAML 加载失败: %s，回退到硬编码路径", e)

        # ── 路径 2: 硬编码兜底 ──
        if self._market == "futures":
            logger.info("[SeedPool.load] 加载期货专用种子 (14 大因子家族, 81 个, 硬编码路径)...")
            from .seed_data_futures_full import load_futures_seeds_full
            futures_seeds = load_futures_seeds_full(self._trace_id)
            for fp in futures_seeds:
                self._cache[fp["name"]] = fp
            logger.info(
                "[SeedPool.load] 期货种子加载完成: total=%d, sample_names=%s",
                len(futures_seeds), [s["name"] for s in futures_seeds[:5]],
            )
        else:
            logger.info("[SeedPool.load] 加载股票内置种子 (9 个, 硬编码路径)...")
            for defn in _SEED_DEFINITIONS:
                fp = create_factor_program(
                    name=defn["name"],
                    code=defn["code"],
                    params=defn["params"],
                    signature=defn["signature"],
                    economic_logic=defn["economic_logic"],
                    source="seed",
                    parent_id=None,
                    generation=0,
                    trace_id=self._trace_id,
                )
                self._cache[defn["name"]] = fp

            if include_external:
                logger.info("[SeedPool.load] 加载外部种子 (WQ101 + Qlib158 + GTJA191 + 基本面, 硬编码路径)...")
                ext_count = 0
                for ext_fp in load_all_external_seeds(self._trace_id):
                    self._cache[ext_fp["name"]] = ext_fp
                    ext_count += 1
                logger.info("[SeedPool.load] 外部种子加载完成: external=%d", ext_count)

        all_seeds = self._list_base_seeds()
        logger.info(
            "[SeedPool.load] 全部种子加载完成: market=%s, total=%d, names_sample=%s",
            self._market, len(all_seeds), [s["name"] for s in all_seeds[:3]],
        )
        return all_seeds

    def _list_base_seeds(self) -> list[FactorProgram]:
        """返回非 L1 注入的种子因子（内置 + 外部）。"""
        return [
            fp for k, fp in self._cache.items()
            if not k.startswith("l1:")
        ]

    def get_seed(self, name: str) -> Optional[FactorProgram]:
        """按名称获取种子因子（包括外部种子）。"""
        if not self._cache:
            self.load_all_seeds()
        return self._cache.get(name)

    def count(self) -> int:
        """返回种子因子总数（不含 L1 注入）。"""
        if not self._cache:
            self.load_all_seeds()
        return len(self._list_base_seeds())

    def list_names(self) -> list[str]:
        """返回种子因子名称列表（不含 L1 注入）。"""
        if not self._cache:
            self.load_all_seeds()
        return [fp["name"] for fp in self._list_base_seeds()]

    def inject_from_l1(
        self,
        candidate: dict[str, Any],
        trace_id: Optional[str] = None,
    ) -> FactorProgram:
        """L1 注入接口 — 将 L1 Bootstrapping 产出的候选因子注入种子池。

        HARNESS §11-loop-engineering.md §15: L1 → L2 种子池入口。

        Args:
            candidate: SeedCandidate 字典（必须包含 name/code/params/signature/
                       economic_logic 字段）
            trace_id: 全链路 trace_id（None 时使用 candidate 中的 trace_id）

        Returns:
            FactorProgram — 注入后的因子程序（source="bootstrapping"）

        Raises:
            ValueError: candidate 缺少必需字段
        """
        import time
        t0 = time.time()
        required = ("name", "code", "params", "signature", "economic_logic")
        missing = [k for k in required if k not in candidate]
        if missing:
            logger.error(
                "[inject_from_l1] 缺少必需字段, missing=%s, candidate_keys=%s, trace_id=%s",
                missing, list(candidate.keys()), trace_id or candidate.get("trace_id"),
            )
            raise ValueError(f"SeedCandidate 缺少必需字段: {missing}")

        injected_trace = trace_id or candidate.get("trace_id") or self._trace_id
        cand_name = candidate["name"]
        cand_id = candidate.get("candidate_id", cand_name)
        logger.info(
            "[inject_from_l1] 开始注入, trace_id=%s, candidate_id=%s, name=%s, source=%s, code_len=%d",
            injected_trace, cand_id, cand_name,
            candidate.get("source", "unknown"), len(candidate["code"]),
        )

        try:
            fp = create_factor_program(
                name=cand_name,
                code=candidate["code"],
                params=candidate["params"],
                signature=candidate["signature"],
                economic_logic=candidate["economic_logic"],
                source="bootstrapping",
                parent_id=cand_id,
                generation=0,
                trace_id=injected_trace,
            )
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            logger.error(
                "[inject_from_l1] create_factor_program 异常, trace_id=%s, candidate_id=%s, name=%s, elapsed_ms=%.1f, error=%s",
                injected_trace, cand_id, cand_name, elapsed, e, exc_info=True,
            )
            raise

        # 注入到缓存（按 candidate_id 索引，避免与内置种子碰撞）
        cache_key = f"l1:{cand_id}"
        self._cache[cache_key] = fp
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "[inject_from_l1] 注入成功, trace_id=%s, candidate_id=%s, name=%s, cache_key=%s, elapsed_ms=%.1f, cache_size=%d",
            injected_trace, cand_id, cand_name, cache_key, elapsed, len(self._cache),
        )
        return fp

    def list_injected_l1(self) -> list[FactorProgram]:
        """列出所有从 L1 注入的种子因子。"""
        return [
            fp for k, fp in self._cache.items()
            if k.startswith("l1:") and fp.get("source") == "bootstrapping"
        ]

    def compute_correlations(
        self,
        data: pd.DataFrame | dict[str, pd.DataFrame],
        threshold: float = 0.95,
        max_factors: Optional[int] = None,
        common_dates: Optional[pd.DatetimeIndex] = None,
    ) -> list[FactorCorrelation]:
        """对种子因子执行相关性预检（L2 阶段轻量扫描）。

        自动检测数据模式:
        - 单 DataFrame → 股票时序模式 (Pearson/Spearman)
        - dict[str, DataFrame] + common_dates → 期货横截面模式 (截面排名 Spearman)

        Args:
            data: OHLCV 数据 (单标的) 或 面板数据 (品种名→DataFrame)
            threshold: 高相关性阈值（默认 0.95）
            max_factors: 最多处理的因子数（None = 全部）
            common_dates: 横截面模式的共同日期索引

        Returns:
            list[FactorCorrelation] — 超过阈值的高相关因子对列表
        """
        seeds = self.load_all_seeds()
        if max_factors is not None:
            seeds = seeds[:max_factors]

        if isinstance(data, dict):
            # 横截面模式
            if common_dates is None:
                return []
            return compute_cross_section_correlations(seeds, data, common_dates, threshold)
        else:
            # 时序模式
            return compute_seed_correlations(seeds, data, threshold)


def compute_seed_correlations(
    seeds: list[FactorProgram],
    data: pd.DataFrame,
    threshold: float = 0.95,
) -> list[FactorCorrelation]:
    """执行种子因子相关性预检。

    流程:
    1. 逐一执行种子因子获取信号数组
    2. 构建 (n_dates × n_factors) 信号矩阵
    3. 计算 Pearson + Spearman 相关矩阵
    4. 标记 abs(corr) >= threshold 的因子对

    Args:
        seeds: 种子因子列表
        data: OHLCV 数据
        threshold: 高相关性阈值（默认 0.95）

    Returns:
        list[FactorCorrelation] — 超过阈值的高相关因子对
    """
    if len(seeds) < 2:
        return []

    n_factors = len(seeds)
    n_dates = len(data)
    signal_matrix = np.zeros((n_dates, n_factors))

    for i, seed in enumerate(seeds):
        try:
            executor = FactorExecutor(seed)
            signal = executor.execute(data, seed.get("params", {}))
            if len(signal) == n_dates:
                signal_matrix[:, i] = signal
            else:
                signal_matrix[:, i] = 0.0
        except Exception:
            signal_matrix[:, i] = 0.0

    # 剔除全零信号和常数信号（零方差 → 无意义相关）
    valid_mask = (np.any(signal_matrix != 0, axis=0) &
                  (np.std(signal_matrix, axis=0) > 1e-10))
    valid_indices = np.where(valid_mask)[0]
    valid_seeds = [seeds[i] for i in valid_indices]
    valid_signals = signal_matrix[:, valid_indices]

    if len(valid_seeds) < 2:
        return []

    # 计算相关矩阵
    pearson_matrix = np.corrcoef(valid_signals, rowvar=False)
    spearman_matrix = np.zeros((len(valid_seeds), len(valid_seeds)))
    for i in range(len(valid_seeds)):
        for j in range(i + 1, len(valid_seeds)):
            if np.std(valid_signals[:, i]) > 1e-10 and np.std(valid_signals[:, j]) > 1e-10:
                sp_corr, _ = sp_stats.spearmanr(valid_signals[:, i], valid_signals[:, j])
                spearman_matrix[i, j] = sp_corr
                spearman_matrix[j, i] = sp_corr

    # 收集高相关对
    high_corr_pairs: list[FactorCorrelation] = []
    for i in range(len(valid_seeds)):
        for j in range(i + 1, len(valid_seeds)):
            pearson_val = float(pearson_matrix[i, j])
            spearman_val = float(spearman_matrix[i, j])
            max_abs = max(abs(pearson_val), abs(spearman_val))
            if max_abs >= threshold:
                high_corr_pairs.append(FactorCorrelation(
                    factor_id_a=valid_seeds[i]["factor_id"],
                    factor_id_b=valid_seeds[j]["factor_id"],
                    pearson=pearson_val,
                    spearman=spearman_val,
                ))

    return high_corr_pairs


def compute_cross_section_correlations(
    seeds: list[FactorProgram],
    panel_data: dict[str, pd.DataFrame],
    common_dates: pd.DatetimeIndex,
    threshold: float = 0.95,
) -> list[FactorCorrelation]:
    """期货横截面种子因子相关性预检 — 截面排名 Spearman 相关。

    流程:
    1. 对每个因子执行所有品种，构建 signal_matrix (n_dates × n_varieties)
    2. 剔除全零信号的因子（执行失败或常数信号）
    3. 在每个时间点对各品种信号做 rank
    4. 计算因子间 rank 的 Spearman 相关（跨时间取均值）
    5. 标记 abs(corr) >= threshold 的因子对

    与股票时序版本的区别:
    - 股票: 直接计算时序信号的 Pearson/Spearman
    - 期货: 先计算每期的截面排名，再比较排名序列的 Spearman 相关
      这反映因子在横截面选股（品种）上的信息重叠程度

    Args:
        seeds: 种子因子列表
        panel_data: 面板数据 {品种名: DataFrame(含OHLCV)}
        common_dates: 共同日期索引
        threshold: 高相关性阈值（默认 0.95）

    Returns:
        list[FactorCorrelation] — 超过阈值的高相关因子对
    """
    if len(seeds) < 2:
        return []

    n_dates = len(common_dates)
    varieties = list(panel_data.keys())
    n_varieties = len(varieties)

    if n_varieties < 5:
        return []

    # Step 1: 执行每个因子，构建信号矩阵 (n_dates, n_varieties)
    n_factors = len(seeds)
    # 每个因子存 signal_matrix: (n_dates, n_varieties)
    factor_signals: dict[int, np.ndarray] = {}

    for i, seed in enumerate(seeds):
        seed_name = seed.get("name", "?")
        if i % 10 == 0:
            print(f"[corr] 种子 {i}/{len(seeds)}: {seed_name}", flush=True)
        try:
            executor = FactorExecutor(seed)
            params = seed.get("params", {})
            signal_matrix = np.zeros((n_dates, n_varieties))
            valid_varieties = 0
            for j, variety in enumerate(varieties):
                df = panel_data.get(variety)
                if df is None or len(df) == 0:
                    continue
                try:
                    sig = executor.execute(df, params)
                    if len(sig) > 0:
                        # 位置对齐: 截断或填充到 n_dates 长度
                        sig_arr = np.asarray(sig, dtype=float)
                        if len(sig_arr) >= n_dates:
                            aligned = sig_arr[:n_dates]
                        else:
                            aligned = np.zeros(n_dates)
                            aligned[:len(sig_arr)] = sig_arr
                        signal_matrix[:, j] = aligned
                        valid_varieties += 1
                except Exception:
                    continue

            # 要求至少 5 个品种有有效信号
            if valid_varieties >= 5:
                # 检查因子是否有足够方差
                overall_std = np.std(signal_matrix)
                if overall_std > 1e-10:
                    factor_signals[i] = signal_matrix
        except Exception:
            continue

    if len(factor_signals) < 2:
        return []

    # Step 2: 构建因子列表和信号矩阵
    valid_indices = sorted(factor_signals.keys())
    valid_seeds = [seeds[i] for i in valid_indices]
    valid_matrices = [factor_signals[i] for i in valid_indices]
    n_valid = len(valid_seeds)

    # Step 3: 计算每期截面 rank，然后计算因子间 rank 的 Spearman 相关
    # 先计算每个因子在每期的截面排名
    # rank_matrices: list of (n_dates, n_varieties) — 每期截面 rank
    from scipy.stats import rankdata

    rank_matrices: list[np.ndarray] = []
    for mat in valid_matrices:
        rank_mat = np.zeros_like(mat)
        for t in range(n_dates):
            row = mat[t, :]
            valid_mask = ~np.isnan(row) & (row != 0)
            if np.sum(valid_mask) >= 3:
                ranks = np.full(n_varieties, np.nan)
                valid_indices_in_row = np.where(valid_mask)[0]
                valid_values = row[valid_mask]
                ranks[valid_indices_in_row] = rankdata(valid_values)
                rank_mat[t, :] = ranks
            else:
                rank_mat[t, :] = np.nan
        rank_matrices.append(rank_mat)

    # Step 4: 计算因子间截面 rank 的 Spearman 相关
    high_corr_pairs: list[FactorCorrelation] = []
    for i in range(n_valid):
        for j in range(i + 1, n_valid):
            rank_i = rank_matrices[i]  # (n_dates, n_varieties)
            rank_j = rank_matrices[j]

            # 每期计算两个因子截面排名的 Spearman 相关
            corr_per_date: list[float] = []
            for t in range(n_dates):
                r_i = rank_i[t, :]
                r_j = rank_j[t, :]
                valid = ~(np.isnan(r_i) | np.isnan(r_j))
                if np.sum(valid) < 3:
                    continue
                r_i_valid = r_i[valid]
                r_j_valid = r_j[valid]
                if np.std(r_i_valid) < 1e-10 or np.std(r_j_valid) < 1e-10:
                    continue
                corr_val, _ = sp_stats.spearmanr(r_i_valid, r_j_valid)
                if not np.isnan(corr_val):
                    corr_per_date.append(float(corr_val))

            if len(corr_per_date) == 0:
                continue

            # 跨时间取均值
            mean_corr = float(np.mean(corr_per_date))
            if abs(mean_corr) >= threshold:
                high_corr_pairs.append(FactorCorrelation(
                    factor_id_a=valid_seeds[i]["factor_id"],
                    factor_id_b=valid_seeds[j]["factor_id"],
                    pearson=mean_corr,  # 复用 pearson 字段存储截面 Spearman
                    spearman=mean_corr,
                ))

    return high_corr_pairs


def get_default_seed_pool(market: str = "futures") -> SeedPool:
    """获取默认种子池实例。"""
    return SeedPool(market=market)


__all__ = [
    "SeedPool",
    "compute_seed_correlations",
    "get_default_seed_pool",
]
