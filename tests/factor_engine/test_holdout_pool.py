# -*- coding: utf-8 -*-
"""GAP-055 盲测品种池（FUTURES_HOLDOUT）机构标准校验。

校验盲测池满足：
1. 与核心交易池（FUTURES_CORE_SUBSET）互不重叠 —— 盲测品种不参与实盘交易决策
2. 与分层训练集（FUTURES_STRATIFIED_SUBSET）互不重叠 —— 盲测品种不参与演化训练
3. 全部位于全量品种（FUTURES_SUBSET）内
4. 规模 12~15 —— 机构标准样本量
5. 产业链覆盖 >= 8 —— 分层代表性，避免小品种系统性低估泛化
"""
from __future__ import annotations

import pytest

from fts.data_futures import (
    FUTURES_CORE_SUBSET,
    FUTURES_HOLDOUT,
    FUTURES_SECTOR_MAP,
    FUTURES_STRATIFIED_SUBSET,
    FUTURES_SUBSET,
)


class TestHoldoutPoolDesign:
    """盲测池机构标准约束。"""

    def test_size_within_12_15(self) -> None:
        assert 12 <= len(FUTURES_HOLDOUT) <= 15, f"盲测池规模 {len(FUTURES_HOLDOUT)} 应位于 12~15"

    def test_no_overlap_with_core_subset(self) -> None:
        overlap = set(FUTURES_HOLDOUT) & set(FUTURES_CORE_SUBSET)
        assert not overlap, f"盲测池与核心交易池重叠: {overlap}"

    def test_no_overlap_with_stratified_train(self) -> None:
        overlap = set(FUTURES_HOLDOUT) & set(FUTURES_STRATIFIED_SUBSET)
        assert not overlap, f"盲测池与分层训练集重叠: {overlap}"

    def test_all_within_full_universe(self) -> None:
        missing = set(FUTURES_HOLDOUT) - set(FUTURES_SUBSET)
        assert not missing, f"盲测池品种不在全量列表: {missing}"

    def test_unique(self) -> None:
        assert len(set(FUTURES_HOLDOUT)) == len(FUTURES_HOLDOUT), "盲测池存在重复品种"

    def test_sector_coverage_at_least_8(self) -> None:
        h = set(FUTURES_HOLDOUT)
        covered = {name for name, members in FUTURES_SECTOR_MAP.items() if h & set(members)}
        assert len(covered) >= 8, f"盲测池产业链覆盖仅 {len(covered)}: {covered}"

    def test_sector_coverage_includes_major_chains(self) -> None:
        """必须覆盖 黑色系/有色/农产品/煤化工 等核心产业链。"""
        h = set(FUTURES_HOLDOUT)
        for required in ("黑色系", "有色金属", "农产品", "煤化工"):
            assert h & set(FUTURES_SECTOR_MAP[required]), f"盲测池未覆盖产业链: {required}"

    def test_contains_large_liquidity_representative(self) -> None:
        """盲测池应含大流动性品种（RU0 天胶 / L0 塑料），避免仅小品种系统性低估泛化。"""
        h = set(FUTURES_HOLDOUT)
        assert {"RU0", "L0"} & h, "盲测池缺少大流动性代表品种"

    def test_l2_train_remains_sufficient(self) -> None:
        """排除盲测池后训练集仍 >= 10（L2 最低要求）。"""
        train = [s for s in FUTURES_STRATIFIED_SUBSET if s not in set(FUTURES_HOLDOUT)]
        assert len(train) >= 10, f"排除盲测池后训练品种仅 {len(train)}"
