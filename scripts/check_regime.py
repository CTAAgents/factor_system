"""检查当前市场制度状态。"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from fts.data import FTSDataProvider


def main():
    provider = FTSDataProvider()
    data = provider.get_ohlcv("RB0", days=500)
    print(f"数据行数: {len(data)}")
    print(f"日期范围: {data.index[0].date()} ~ {data.index[-1].date()}")
    print(f"最新收盘价: {data['close'].iloc[-1]:.2f}")

    close = data["close"]
    rets = close.pct_change().dropna()

    # 多周期收益率
    print(f"\n近5日收益率: {rets.tail(5).sum()*100:.2f}%")
    print(f"近20日收益率: {rets.tail(20).sum()*100:.2f}%")
    print(f"近60日收益率: {rets.tail(60).sum()*100:.2f}%")

    # 波动率
    vol_20 = rets.tail(20).std() * np.sqrt(252) * 100
    vol_60 = rets.tail(60).std() * np.sqrt(252) * 100
    print(f"近20日波动率(年化): {vol_20:.2f}%")
    print(f"近60日波动率(年化): {vol_60:.2f}%")

    # MA 趋势
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()
    last = close.iloc[-1]
    print(f"\n价格 vs MA20: {'↑' if last > ma20.iloc[-1] else '↓'}  (价={last:.0f}, MA20={ma20.iloc[-1]:.0f})")
    print(f"MA20 vs MA60: {'↑' if ma20.iloc[-1] > ma60.iloc[-1] else '↓'}  (MA20={ma20.iloc[-1]:.0f}, MA60={ma60.iloc[-1]:.0f})")
    print(f"MA60 vs MA120: {'↑' if ma60.iloc[-1] > ma120.iloc[-1] else '↓'}  (MA60={ma60.iloc[-1]:.0f}, MA120={ma120.iloc[-1]:.0f})")

    # 最大回撤
    peak = close.expanding().max()
    dd = (close - peak) / peak
    print(f"\n近60日最大回撤: {dd.tail(60).min()*100:.2f}%")

    # 多周期趋势投票
    trend_up_count = 0
    for ma in [ma20, ma60, ma120]:
        if last > ma.iloc[-1]:
            trend_up_count += 1

    # 波动分级
    if vol_20 > 35:
        vol_level = "高波动"
    elif vol_20 > 20:
        vol_level = "中等波动"
    else:
        vol_level = "低波动"

    # 制度判断
    if trend_up_count >= 2 and vol_20 < 25:
        regime = "趋势上涨 (bull)"
        regime_en = "bull"
        note = "对 bias 类趋势因子友好"
    elif trend_up_count >= 2 and vol_20 >= 25:
        regime = "高波动上涨 (high_vol_bull)"
        regime_en = "high_vol_bull"
        note = "趋势存在但波动大，bias 因子信号不稳定"
    elif trend_up_count <= 1 and vol_20 < 20:
        regime = "震荡 (oscillate)"
        regime_en = "oscillate"
        note = "bias 因子在震荡市中反复被打脸，应降低仓位或暂停"
    elif trend_up_count <= 1 and vol_20 >= 20:
        regime = "高波动下跌 / 恐慌 (bear)"
        regime_en = "bear"
        note = "空头趋势，bias 因子做多信号全部失效"
    else:
        regime = "震荡 (oscillate)"
        regime_en = "oscillate"
        note = "方向不明确，谨慎交易"

    print(f"\n{'='*50}")
    print(f"  当前市场制度: {regime}")
    print(f"  趋势投票: {trend_up_count}/3 周期看多")
    print(f"  波动水平: {vol_level} ({vol_20:.1f}%)")
    print(f"  对 bias 因子建议: {note}")
    print(f"{'='*50}")

    # 对 fut_bias 因子的具体分析
    print(f"\n--- bias 因子适配性分析 ---")
    print(f"  当前价格 vs MA60: {last:.0f} vs {ma60.iloc[-1]:.0f} -> {'价格在上方' if last > ma60.iloc[-1] else '价格在下方'}")
    print(f"  偏度: 乖离率 = {(last/ma20.iloc[-1]-1)*100:.2f}%")
    if regime_en == "bull":
        print(f"  ✅ 当前市场适合 bias 因子: 趋势向上，乖离率正常")
    elif regime_en == "oscillate":
        print(f"  ❌ 当前市场不适合 bias 因子: 震荡市中 bias 频繁反转")
    elif regime_en == "bear":
        print(f"  ❌ 当前市场不适合 bias 因子: 下跌趋势，做多信号被压制")
    else:
        print(f"  ⚠️ 当前市场需谨慎: 高波动环境下 bias 信号可靠性下降")


if __name__ == "__main__":
    main()