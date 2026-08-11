"""
fts/factor_engine/microstructure_generator.py — 微观结构因子生成器（C1，2026-08-11）

从 tick 逐笔快照（DataAggregator.get_ticks，tick_cache → tick_sources 降级）计算
Level2 订单流微观结构因子（OFI/OBI/大单占比），按交易日聚合为日频因子值序列，
产出可过全套审计链的 FactorProgram（独立候选源注入 L2，不走 batch 轮换——
演化 batch 模式数据为日频 OHLCV 面板，无 tick 数据通道）。

设计约束（对齐 HARNESS 因子研发红线）:
    - 零未来函数：t 日聚合值仅由 ≤t tick 计算（数据准备层固定）；
      因子 code 为确定性日期查找，t 日信号只依赖 ≤t 日聚合值
    - 窗口自适应：code 按 data['datetime'] 对齐聚合日期序列，任意评估窗口可用
      （BacktestPipeline._execute_factor_code 在 data 为 DatetimeIndex 时注入
      datetime 列，供本类因子对齐）
    - 数据降级：tick 行数 < min_tick_rows 或有效交易日 < min_factor_rows →
      返回 None，不阻断主流程
    - 沙箱安全：生成 code 仅依赖 numpy + data dict（close/datetime）

用法:
    from fts.factor_engine.microstructure_generator import MicrostructureFactorGenerator

    gen = MicrostructureFactorGenerator()
    cand = gen.generate(symbol="RB0", trace_id="l2_xxx")   # Optional[MicrostructureFactorCandidate]
    cands = gen.generate_batch(trace_id="l2_xxx")          # 动态池 25 品种

版本: v1.0.0（C1 首期）
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from .microstructure_factors import MicrostructureConfig, compute_microstructure_factors

logger = logging.getLogger(__name__)

# tick 提供者签名：get_ticks(symbol, count, trace_id, start_time, end_time) -> DataFrame
TickProvider = Callable[..., pd.DataFrame]

# 日频聚合因子种类（每品种产出 4 个因子）
FACTOR_KINDS: list[str] = ["ofi_mean", "obi_mean", "ltr_mean", "ofi_std"]

_DEFAULT_DB_PATH = "data/fts_history.duckdb"
_TICK_FETCH_COUNT = 200_000  # 63 日 × 每日数千~数万 tick 的上限护栏


@dataclass
class MicrostructureGeneratorConfig:
    """微观结构因子生成配置（C1）。

    Attributes:
        symbols: 目标品种清单（默认动态池；显式传入优先）。
        tick_lookback_days: tick 回看天数（默认 63 ≈ 1 季度）。
        min_tick_rows: 最少 tick 行数，不足降级（默认 200）。
        min_factor_rows: 最少有效交易日，不足降级（默认 20）。
        micro_window: 透传 MicrostructureConfig.window（tick 滚动窗口，默认 20）。
        exclude_last_day: 排除最后一个聚合交易日（当日 tick 未走完，默认 True）。
        seed: 随机种子（仅符号加载，保持可复现）。
    """

    symbols: list[str] = field(default_factory=list)
    tick_lookback_days: int = 63
    min_tick_rows: int = 200
    min_factor_rows: int = 20
    micro_window: int = 20
    exclude_last_day: bool = True
    seed: int = 42


@dataclass
class MicrostructureFactorCandidate:
    """单个微观结构因子候选。

    Attributes:
        factor: FactorProgram dict（含 code/params/signature/economic_logic）。
        symbol: 来源品种（如 RB0）。
        kind: 因子种类（ofi_mean/obi_mean/ltr_mean/ofi_std）。
        n_days: 有效聚合交易日数。
        generated_at: ISO 时间戳。
    """

    factor: dict[str, Any]
    symbol: str
    kind: str
    n_days: int
    generated_at: str


class MicrostructureFactorGenerator:
    """微观结构因子生成器 — tick → 日频聚合 → FactorProgram（C1）。

    生成流程:
        1. get_ticks 拉取品种近 N 日 tick（tick_cache → tick_sources 降级）
        2. compute_microstructure_factors 计算 OFI/OBI/大单占比（契约列）
        3. 按交易日聚合日频因子值（均值/波动），排除未走完的当日
        4. dates/values 内嵌 params，生成确定性日期查找 code（零未来）
    """

    def __init__(
        self,
        config: Optional[MicrostructureGeneratorConfig] = None,
        tick_provider: Optional[TickProvider] = None,
    ) -> None:
        """初始化生成器。

        Args:
            config: 生成配置；None 使用默认。
            tick_provider: tick 提供者（测试注入）；None 时惰性构造
                DataAggregator（tick_cache → tick_sources 降级），
                无缓存/无源时返回空 → 降级 None。
        """
        self.config = config or MicrostructureGeneratorConfig()
        self._tick_provider: Optional[TickProvider] = tick_provider
        self._aggregator: Any = None

    # ─── 主入口 ──────────────────────────────────────────

    def generate(
        self,
        symbol: str,
        trace_id: Optional[str] = None,
    ) -> Optional[MicrostructureFactorCandidate]:
        """为单个品种生成微观结构因子候选集（4 个因子）。

        Args:
            symbol: 品种代码（如 "RB0"）。
            trace_id: 全链路 trace_id。

        Returns:
            首个有效因子候选（ofi_mean）；数据不足返回 None。
            完整集合经 generate_batch 获取。
        """
        daily = self._load_daily_aggregates(symbol, trace_id)
        if daily is None:
            return None
        # 每品种默认产出 4 因子；返回首个（ofi_mean）兼容单因子语义
        cands = self._build_candidates(symbol, daily, trace_id)
        return cands[0] if cands else None

    def generate_batch(
        self,
        symbols: Optional[list[str]] = None,
        trace_id: Optional[str] = None,
    ) -> list[MicrostructureFactorCandidate]:
        """批量生成：对目标品种清单逐品种聚合产出全部候选。

        Args:
            symbols: 品种清单；None 使用配置（动态池优先）。
            trace_id: 全链路 trace_id。

        Returns:
            所有有效候选列表（数据不足品种自动跳过，不抛错）。
        """
        target = symbols or self.config.symbols or self._default_symbols()
        out: list[MicrostructureFactorCandidate] = []
        for sym in target:
            daily = self._load_daily_aggregates(sym, trace_id)
            if daily is None:
                logger.info("[microstructure] [%s] 数据不足，跳过", sym)
                continue
            out.extend(self._build_candidates(sym, daily, trace_id))
        return out

    # ─── 数据准备 ────────────────────────────────────────

    def _load_daily_aggregates(
        self,
        symbol: str,
        trace_id: Optional[str],
    ) -> Optional[pd.DataFrame]:
        """拉取 tick → 计算微观结构因子 → 日频聚合（排除当日）。

        Args:
            symbol: 品种代码。
            trace_id: trace_id。

        Returns:
            DataFrame（index=date，列 ofi_mean/obi_mean/ltr_mean/ofi_std）；
            tick 不足或聚合日不足返回 None（降级）。
        """
        ticks = self._get_ticks(symbol, trace_id)
        if ticks is None or ticks.empty or len(ticks) < self.config.min_tick_rows:
            return None

        # datetime 可能在索引（aggregator.get_ticks 返回）→ 统一为列
        if not isinstance(ticks.index, pd.RangeIndex) and "datetime" not in ticks.columns:
            ticks = ticks.reset_index()
        if "datetime" not in ticks.columns:
            return None

        micro = compute_microstructure_factors(
            ticks,
            MicrostructureConfig(window=self.config.micro_window),
        )
        if micro is None or micro.empty:
            return None

        df = micro.copy()
        df["date"] = pd.to_datetime(df["datetime"]).dt.normalize()
        daily = (
            df.groupby("date")
            .agg(
                ofi_mean=("ofi", "mean"),
                obi_mean=("obi", "mean"),
                ltr_mean=("large_trade_ratio", "mean"),
                ofi_std=("ofi", "std"),
            )
            .dropna(subset=["ofi_mean"])
        )
        if self.config.exclude_last_day and len(daily) > 1:
            daily = daily.iloc[:-1]  # 排除未走完的当日
        if len(daily) < self.config.min_factor_rows:
            return None
        return daily

    def _get_ticks(self, symbol: str, trace_id: Optional[str]) -> pd.DataFrame:
        """获取 tick 数据：注入 provider 优先，否则惰性构造 DataAggregator。"""
        if self._tick_provider is not None:
            return self._tick_provider(symbol, _TICK_FETCH_COUNT, trace_id or "")
        if self._aggregator is None:
            from fts.data_sources.aggregator import DataAggregator

            self._aggregator = DataAggregator(db_path=_DEFAULT_DB_PATH)
        return self._aggregator.get_ticks(
            symbol,
            count=_TICK_FETCH_COUNT,
            trace_id=trace_id or "",
        )

    # ─── 候选构造 ────────────────────────────────────────

    def _build_candidates(
        self,
        symbol: str,
        daily: pd.DataFrame,
        trace_id: Optional[str],
    ) -> list[MicrostructureFactorCandidate]:
        """由日频聚合表构造 4 个因子候选（kinds 见 FACTOR_KINDS）。"""
        dates = [d.strftime("%Y-%m-%d") for d in daily.index]
        out: list[MicrostructureFactorCandidate] = []
        for kind in FACTOR_KINDS:
            values = daily[kind].fillna(0.0).to_numpy(dtype=float)
            factor = self._build_factor(symbol, kind, dates, values, len(daily), trace_id)
            out.append(
                MicrostructureFactorCandidate(
                    factor=factor,
                    symbol=symbol,
                    kind=kind,
                    n_days=len(daily),
                    generated_at=pd.Timestamp.now().isoformat(),
                )
            )
        return out

    def _build_factor(
        self,
        symbol: str,
        kind: str,
        dates: list[str],
        values: np.ndarray,
        n_days: int,
        trace_id: Optional[str],
    ) -> dict[str, Any]:
        """构造 FactorProgram（code 内嵌日期-值映射，零未来查找）。"""
        code = self._build_code(dates, values)
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.factor_program import create_factor_program

        unique_key = f"micro_{symbol}_{kind}_{dates[0]}_{dates[-1]}"
        factor_id = "fct_" + hashlib.md5(unique_key.encode()).hexdigest()[:8]
        factor_name = f"micro_{symbol}_{kind}"

        signature = FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        )
        kind_label = {
            "ofi_mean": "日订单流不平衡均值（买方主动性）",
            "obi_mean": "日盘口不平衡均值（买深-卖深）",
            "ltr_mean": "日大单成交量占比均值",
            "ofi_std": "日订单流不平衡波动（分歧度）",
        }[kind]
        factor: dict[str, Any] = create_factor_program(
            name=factor_name,
            code=code,
            params={
                "dates": dates,
                "values": [round(float(v), 6) for v in values],
                "kind": kind,
                "symbol": symbol,
            },
            signature=signature,
            economic_logic=EconomicLogic(
                theory=2,
                behavioral=2,
                microstructure=5,
                institutional=3,
                narrative=(
                    f"微观结构因子（C1）: {symbol} 近 {n_days} 交易日 tick 聚合的"
                    f"{kind_label}；聚合值由 ≤t tick 在数据准备层计算（零未来），"
                    f"因子 code 按日期确定性查找输出日频信号。"
                ),
            ),
            source="manual",
            market="futures",
            family="microstructure",
            symbols=[symbol],
            trace_id=trace_id,
        )
        factor["factor_id"] = factor_id
        factor["generation"] = 0
        factor["kind"] = "code"
        factor["microstructure"] = {
            "symbol": symbol,
            "kind": kind,
            "n_days": n_days,
            "date_start": dates[0],
            "date_end": dates[-1],
        }
        return factor

    # ─── code 生成 ───────────────────────────────────────

    @staticmethod
    def _build_code(dates: list[str], values: np.ndarray) -> str:
        """生成确定性日期查找 code：t 日信号 = 最新 ≤t 聚合值（零未来）。"""
        dates_repr = repr(dates)
        values_repr = repr([round(float(v), 6) for v in np.asarray(values, dtype=float)])
        code = f"""\
def factor_program(data, params):
    import numpy as np
    n = len(data.get('close', []))
    out = np.zeros(n)
    dt = data.get('datetime', None)
    if dt is None or len(dt) != n:
        return out
    dates = {dates_repr}
    values = {values_repr}
    vmap = dict(zip(dates, values))
    last = 0.0
    for i in range(n):
        d = str(dt[i])[:10]
        if d in vmap:
            last = vmap[d]
        out[i] = last
    return out
"""
        return code

    # ─── 工具 ────────────────────────────────────────────

    def _default_symbols(self) -> list[str]:
        """默认品种清单：动态池（缺失/损坏回退静态核心池）。"""
        try:
            from fts.data_futures import get_dynamic_core_subset

            return get_dynamic_core_subset()
        except Exception:  # noqa: BLE001 — 降级优先
            return []


def generate_microstructure_factors(
    symbols: Optional[list[str]] = None,
    trace_id: Optional[str] = None,
    config: Optional[MicrostructureGeneratorConfig] = None,
) -> list[MicrostructureFactorCandidate]:
    """便捷入口：批量生成微观结构因子候选。

    Returns:
        有效候选列表（数据不足品种自动跳过）；无数据返回空列表。
    """
    gen = MicrostructureFactorGenerator(config)
    return gen.generate_batch(symbols=symbols, trace_id=trace_id)


__all__ = [
    "MicrostructureGeneratorConfig",
    "MicrostructureFactorCandidate",
    "MicrostructureFactorGenerator",
    "generate_microstructure_factors",
    "FACTOR_KINDS",
]
