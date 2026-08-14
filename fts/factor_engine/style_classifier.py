"""
fts.factor_engine.style_classifier — 因子风格分类器（A.3 / v2.56.0）。

将因子归类为 ``FactorStyle`` 风格标签（momentum/mean_reversion/carry/value/
low_vol/high_beta/defensive/growth/quality/sentiment/volatility/open_interest/
cross_section/intraday/other），作为 L3 自适应权重 style 维度的依据。

分类依据（优先级从高到低）:
    1. 因子元数据 ``style_tags`` 字段（显式指定，直接采用）
    2. 名称关键词（name 包含 momentum/trend/breakout → momentum 等）
    3. 代码关键词（code 含 open_interest → open_interest 等）
    4. 签名输入字段（含 volume 且 name 含 ratio → volume 等）
    5. 缺省 "other"

与因子分组（按信号相关性聚类）相互独立，风格与分组是正交的两个维度，
可同时存在。

用法:
    from fts.factor_engine.style_classifier import FactorStyleClassifier

    clf = FactorStyleClassifier()
    styles = clf.classify(factor)          # list[str]
    style = clf.classify_primary(factor)   # str（主风格）

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .contracts import FactorStyle

logger = logging.getLogger(__name__)


# 名称关键词 → 风格映射（按命中优先级排序）
_NAME_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("intraday", "minute", "tick"), "intraday"),
    (("open_interest", "oi_change", "position_change"), "open_interest"),
    (("momentum", "trend", "breakout", "follow", "roc", "rsi"), "momentum"),
    (("reversion", "mean", "reversal", "bounce", "regression"), "mean_reversion"),
    (("carry", "spread", "arbitrage", "basis", "roll"), "carry"),
    (("pe_", "_pe", "pb_", "_pb", "value", "dividend", "book_to"), "value"),
    (("lowvol", "low_vol", "lowvolatility", "low_volatility"), "low_vol"),
    (("highbeta", "high_beta", "beta"), "high_beta"),
    (("defensive", "defense", "quality_minus_junk"), "defensive"),
    (("growth", "earnings", "revenue", "sales_growth"), "growth"),
    (("quality", "roe", "roa", "profit_margin"), "quality"),
    (("sentiment", "analyst", "revision", "media", "news"), "sentiment"),
    (("volatility", "vol", "atr", "bollinger", "stddev"), "volatility"),
    (("cross_section", "cs_", "rank", "crosssection"), "cross_section"),
]

# 代码关键词 → 风格映射
_CODE_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("open_interest", "openinterest", "oi_change"), "open_interest"),
    (("ts_rank", "cs_rank", "cross_section"), "cross_section"),
    (("tick", "intraday", "minute_data"), "intraday"),
]

# 签名输入字段 → 风格映射（与代码/名称都不命中时的兜底）
_SIGNATURE_FIELDS: list[tuple[tuple[str, ...], str]] = [
    (("open_interest", "hold", "position"), "open_interest"),
    (("volume", "amount", "money"), "momentum"),
]


class FactorStyleClassifier:
    """因子风格分类器。

    将因子归类到 ``FactorStyle`` 风格标签，供 L3 自适应权重 style 维度使用。
    """

    def __init__(self) -> None:
        self._name_keywords = _NAME_KEYWORDS
        self._code_keywords = _CODE_KEYWORDS
        self._signature_fields = _SIGNATURE_FIELDS

    def classify(self, factor: dict[str, Any]) -> list[str]:
        """分类因子，返回风格标签列表（至少含一个主风格）。

        Args:
            factor: 因子元数据（含 name/code/signature/style_tags 可选）

        Returns:
            list[str] — 风格标签列表。显式 style_tags 优先；
            否则按名称→代码→签名依次推断。
        """
        # 1. 显式 style_tags 优先
        explicit = factor.get("style_tags")
        if explicit and isinstance(explicit, list):
            valid = [s for s in explicit if s in _VALID_STYLES]
            if valid:
                return valid

        # 2. 名称关键词
        name = (factor.get("name", "") or "").lower()
        for kws, style in self._name_keywords:
            if any(kw in name for kw in kws):
                return [style]

        # 3. 代码关键词
        code = (factor.get("code", "") or "").lower()
        for kws, style in self._code_keywords:
            if any(kw in code for kw in kws):
                return [style]

        # 4. 签名输入字段
        sig = factor.get("signature", {}) or {}
        fields = [str(f).lower() for f in (sig.get("input_fields", []) or [])]
        for kws, style in self._signature_fields:
            if any(kw in f for f in fields for kw in kws):
                return [style]

        # 5. 缺省
        return ["other"]

    def classify_primary(self, factor: dict[str, Any]) -> str:
        """返回主风格（列表首个元素）。"""
        styles = self.classify(factor)
        return styles[0] if styles else "other"


_VALID_STYLES: frozenset[str] = frozenset(FactorStyle.__args__)  # type: ignore[attr-defined]


def classify_style_tags(factor: dict[str, Any]) -> Optional[list[str]]:
    """便捷函数：推断因子风格标签（供 schema/load_elite_factors 复用）。"""
    return FactorStyleClassifier().classify(factor)


__all__ = [
    "FactorStyleClassifier",
    "classify_style_tags",
]
