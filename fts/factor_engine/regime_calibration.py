"""
fts.factor_engine.regime_calibration — Regime 置信度标定与规则伪概率（28 计划 T1/T5）。

机构实践：低确定性（高后验熵）时折扣置信度，进而降低暴露。
当前包含规则方法伪概率构造（T1）与熵标定器 RegimeConfidenceCalibrator（T5）。

版本: v0.1.1
"""

from __future__ import annotations

import numpy as np

_ALL_REGIMES = ("bull", "bear", "oscillate", "high_vol", "low_vol")


class RegimeConfidenceCalibrator:
    """置信度熵标定器。

    思路（对标知识库 VAE 重构误差 / 后验熵）：
      校准置信度 = confidence × (1 − entropy_penalty × 归一化熵)
    归一化熵 H_norm = H / ln(N)：均匀分布 → 1（最大折扣），单点分布 → 0（不折扣）。
    """

    def __init__(self, entropy_penalty: float = 0.5, scale_min: float = 0.3) -> None:
        self.entropy_penalty = float(entropy_penalty)
        self.scale_min = float(scale_min)

    def calibrate(self, confidence: float, regime_probs: dict[str, float] | None = None) -> float:
        """返回熵标定后的置信度（∈[scale_min, 1.0]）。"""
        if regime_probs is None or len(regime_probs) < 2:
            return float(np.clip(confidence, self.scale_min, 1.0))
        p = np.array([max(v, 1e-12) for v in regime_probs.values()], dtype=float)
        p = p / p.sum()
        n = len(p)
        entropy = -float(np.sum(p * np.log(p)))
        h_norm = entropy / np.log(n) if n > 1 else 0.0
        scaled = confidence * (1.0 - self.entropy_penalty * h_norm)
        return float(np.clip(scaled, self.scale_min, 1.0))


def build_rule_regime_probs(
    trend_score: float,  # -1 ~ 1
    vol_score: float,  # 0 ~ 1
) -> dict[str, float]:
    """由软投票得分构造全制度伪概率（和=1）。

    逻辑与 _detect_by_rule 软投票一致:
      - 趋势得分贡献 bull/bear，波动得分贡献 high_vol，
        低波无趋势贡献 low_vol，余量归 oscillate。

    参数:
        trend_score: 趋势得分（-1 熊 ~ 1 牛）。
        vol_score:   波动率得分（0 低波 ~ 1 高波）。

    返回:
        覆盖全部 5 制度的概率分布 dict，和为 1；
        全零时返回 {"oscillate": 1.0}（无信息分布）。
    """
    raw: dict[str, float] = {
        "bull": max(0.0, trend_score) * (1.0 - vol_score),
        "bear": max(0.0, -trend_score) * (1.0 - vol_score),
        "high_vol": vol_score * 0.6,
        "low_vol": (1.0 - vol_score) * (1.0 - abs(trend_score)) * 0.5,
        "oscillate": 0.0,
    }
    total = sum(raw.values())
    if total <= 1e-12:  # 全零 → 无信息分布
        return {"oscillate": 1.0}
    probs = {k: v / total for k, v in raw.items()}
    probs["oscillate"] = max(0.0, 1.0 - sum(v for k, v in probs.items() if k != "oscillate"))
    return probs
