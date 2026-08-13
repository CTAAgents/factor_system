"""
tests/config/test_weight_recompute.py — 权重重算日判定（GAP-072，v2.99.0）

覆盖:
    - is_weight_recompute_day: cadence=daily 每日重算
    - is_weight_recompute_day: cadence=weekly 按配置星期重算
    - is_weight_recompute_day: 未知 cadence 安全回退为每日重算
"""

from __future__ import annotations

from datetime import date

from fts.config.settings import FTSConfig, is_weight_recompute_day


def _cfg(cadence: str = "weekly", weekday: int = 4) -> FTSConfig:
    """构造指定重算频率配置。"""
    return FTSConfig(l3_weight_recompute_cadence=cadence, l3_weight_recompute_weekday=weekday)


class TestIsWeightRecomputeDay:
    """is_weight_recompute_day 判定逻辑。"""

    def test_daily_always_recompute(self):
        """cadence=daily：任意日期都重算。"""
        cfg = _cfg(cadence="daily")
        for d in [date(2026, 8, 9), date(2026, 8, 10), date(2026, 8, 14), date(2026, 8, 15)]:
            assert is_weight_recompute_day(cfg, today=d) is True

    def test_weekly_friday_only(self):
        """cadence=weekly + weekday=4（周五）：仅周五重算。"""
        cfg = _cfg(cadence="weekly", weekday=4)
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 14)) is True  # 周五
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 10)) is False  # 周一
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 11)) is False  # 周二
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 15)) is False  # 周六

    def test_weekly_custom_weekday(self):
        """cadence=weekly + weekday=0（周一）：仅周一重算。"""
        cfg = _cfg(cadence="weekly", weekday=0)
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 10)) is True  # 周一
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 14)) is False  # 周五

    def test_unknown_cadence_fallback_daily(self):
        """未知 cadence 安全回退为每日重算。"""
        cfg = _cfg(cadence="quarterly")
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 10)) is True

    def test_cadence_none_fallback_weekly(self, caplog):
        """cadence 为空时回退 weekly（默认周五重算）。"""
        cfg = FTSConfig(l3_weight_recompute_cadence="", l3_weight_recompute_weekday=4)
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 14)) is True
        assert is_weight_recompute_day(cfg, today=date(2026, 8, 10)) is False

    def test_default_cadence_is_daily(self):
        """默认配置（v2.104.0+7）cadence=daily：任意交易日都重算权重。"""
        cfg = FTSConfig()
        assert cfg.l3_weight_recompute_cadence == "daily"
        for d in [date(2026, 8, 10), date(2026, 8, 12), date(2026, 8, 14)]:
            assert is_weight_recompute_day(cfg, today=d) is True
