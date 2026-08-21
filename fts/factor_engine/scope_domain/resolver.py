"""
fts/factor_engine/scope_domain/resolver.py — scope 解析注册表

统一加载产业链级品种映射（SSOT：config/futures_universe.yaml）：
  - futures：sector_map（17 产业链）；
  - energy：workflows.energy.sub_symbols（四大化工子链，自 ENERGY_CHAIN_SUB_SYMBOLS
    迁移 YAML 化，消除 portfolio_loop 代码硬编码）。

独立轻量加载（不复用 data_futures 模块级常量，避免顶层循环依赖）；
懒加载 + 校验：链名必须存在于 sector_map，未知链告警不阻断。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fts.factor_engine.scope_domain.types import FactorScope

logger = logging.getLogger(__name__)

_UNIVERSE_YAML = Path(__file__).resolve().parent.parent.parent.parent / "config" / "futures_universe.yaml"


@lru_cache(maxsize=1)
def _load_universe_cfg() -> dict[str, Any]:
    """懒加载 futures_universe.yaml（缓存；缺失/损坏返回空 dict 并告警）。"""
    if not _UNIVERSE_YAML.exists():
        logger.warning("[scope] %s 缺失，scope 链映射为空", _UNIVERSE_YAML.name)
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        cfg = yaml.safe_load(_UNIVERSE_YAML.read_text(encoding="utf-8")) or {}
        return dict(cfg)
    except Exception as e:  # noqa: BLE001
        logger.warning("[scope] %s 解析失败: %s", _UNIVERSE_YAML.name, e)
        return {}


def resolve_chain_map(market: str = "futures") -> dict[str, list[str]]:
    """返回 {链名: [品种]} 映射（按市场路由）。

    - futures：sector_map 17 产业链（不含 energy 训练池聚合组"炼化聚酯链"，
      该组为训练池集合而非产业链划分）；
    - energy：workflows.energy.sub_symbols（四大化工子链）；未配置时回退
      chemical_sectors 对应的 sector_map 子集。
    """
    cfg = _load_universe_cfg()
    sector_map: dict[str, list[str]] = {
        k: list(v) for k, v in (cfg.get("sector_map") or {}).items()
    }
    if market == "energy":
        ew = cfg.get("workflows", {}).get("energy") or {}
        sub = ew.get("sub_symbols")
        if isinstance(sub, dict) and sub:
            out: dict[str, list[str]] = {k: list(v) for k, v in sub.items()}
            # energy 子链为短名（如"聚酯"），与 futures 17 产业链名（"聚酯链"）并不
            # 逐一对名——做品种级校验：子链成员必须存在于 sector_map 任一产业链。
            _warn_unknown_symbols(out, sector_map)
            return out
        chem = list(ew.get("chemical_sectors") or [])
        return {c: list(sector_map[c]) for c in chem if c in sector_map}
    _warn_unknown_chains(sector_map, sector_map)
    return sector_map


def _warn_unknown_chains(chain_map: dict[str, list[str]], sector_map: dict[str, list[str]]) -> None:
    """链名不在 sector_map 中 → 告警不阻断。"""
    for c in chain_map:
        if c not in sector_map:
            logger.warning("[scope] 链 [%s] 不存在于 sector_map，映射可能失效", c)


def _warn_unknown_symbols(chain_map: dict[str, list[str]], sector_map: dict[str, list[str]]) -> None:
    """子链成员必须存在于 sector_map 任一产业链；未知品种告警不阻断。"""
    known = {s for syms in sector_map.values() for s in syms}
    for chain, syms in chain_map.items():
        unknown = [s for s in syms if s not in known]
        if unknown:
            logger.warning("[scope] 子链 [%s] 含未知品种 %s，映射可能失效", chain, unknown)


def symbols_for_chain(market: str, chain: str) -> list[str]:
    """单链品种列表（未知链返回空）。"""
    return resolve_chain_map(market).get(chain, [])


def resolve_scope(scope_def: Optional[dict[str, Any]] = None, market: str = "futures") -> FactorScope:
    """由 scope 定义 dict（None → 全链）构造 FactorScope。

    scope_def 支持:
      {"kind": "all"} / None
      {"kind": "chain", "chains": ["黑色系", "有色金属"]}
      {"kind": "symbol", "symbols": ["RB0"]}
    """
    if not scope_def:
        return FactorScope(kind="all")
    kind = scope_def.get("kind", "all")
    if kind == "all":
        return FactorScope(kind="all")
    if kind == "chain":
        chains = [c for c in scope_def.get("chains") or [] if c in resolve_chain_map(market)]
        return FactorScope(kind="chain", chains=chains)
    if kind == "symbol":
        return FactorScope(kind="symbol", symbols=list(scope_def.get("symbols") or [])[:1])
    logger.warning("[scope] 未知 scope kind=%s，回退全链", kind)
    return FactorScope(kind="all")


__all__ = ["resolve_chain_map", "symbols_for_chain", "resolve_scope"]
