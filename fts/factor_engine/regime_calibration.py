"""
fts.factor_engine.regime_calibration — Regime 置信度标定与规则伪概率（28 计划 T1/T5 + GAP-094）。

机构实践：低确定性（高后验熵）时折扣置信度，进而降低暴露。
当前包含规则方法伪概率构造（T1）与熵标定器 RegimeConfidenceCalibrator（T5）；
GAP-094 新增统计概率校准器 StatisticalRegimeCalibrator（isotonic/Platt/binning，
基于历史 regime 标签拟合，使校准后置信度具备频率语义）。

版本: v0.2.0
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

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


class StatisticalRegimeCalibrator:
    """统计概率校准器（GAP-094，28 计划远期，对标机构概率校准实践）。

    对历史 (confidence, 实际命中) 样本拟合校准映射，使校准后置信度具备频率语义：
        校准置信度 ≈ P(实际命中 | 原始置信度)
    即输出 0.7 意味着 ~70% 的命中率——校准前 HMM 后验/规则伪概率仅具序数语义，
    exposure_scale 的缩放基准（0.7 ≠ 70% 命中）存在系统偏差。

    支持方法:
      - "isotonic": 保序回归（单调不减、最小假设；sklearn 缺失时降级 binning）
      - "platt":    逻辑回归 S 形映射 logit(p) = a·confidence + b（sklearn 缺失降级 binning）
      - "binning":  等频分箱 + 组内命中率（纯 numpy，零依赖兜底）

    未拟合 / 样本不足 / 标签非法时 predict 透传 clip 后的原始置信度（安全默认，
    不引入校准误差）。校准映射经 save/load 持久化为 JSON（跨进程复用；
    load 以 numpy 重建映射，不依赖 sklearn）。

    Args:
        method: 校准方法 "isotonic" | "platt" | "binning"（默认 "isotonic"）。
        min_samples: 拟合所需最少样本数（默认 30；样本不足不拟合、透传）。
        n_bins: binning 分箱数（默认 10；仅 binning 与降级路径使用）。
    """

    def __init__(self, method: str = "isotonic", min_samples: int = 30, n_bins: int = 10) -> None:
        method = (method or "isotonic").lower()
        if method not in ("isotonic", "platt", "binning"):
            method = "binning"  # 未知方法安全降级
        self.method = method
        self.min_samples = int(min_samples)
        self.n_bins = int(n_bins)
        self._fitted = False
        self._n_samples = 0
        self._mapping: dict[str, Any] | None = None

    # ── 拟合 ──────────────────────────────────────────────

    def fit(self, confidences: Sequence[float], hits: Sequence[int]) -> "StatisticalRegimeCalibrator":
        """以历史 (confidence, hit) 样本拟合校准映射。

        Args:
            confidences: 历史置信度序列（[0,1]）。
            hits: 对应实际命中标签（1=命中/0=未命中）。

        Returns:
            自身（链式调用）；样本不足/标签非法/长度不齐时标记未拟合（predict 透传）。
        """
        c = np.asarray(list(confidences), dtype=float)
        h = np.asarray(list(hits), dtype=float)
        if len(c) != len(h) or len(c) < self.min_samples or len(c) == 0:
            self._reset_unfitted()
            return self
        if np.isnan(c).any() or np.isnan(h).any():
            self._reset_unfitted()
            return self
        unique_h = np.unique(h)
        if not np.all(np.isin(unique_h, (0.0, 1.0))):
            self._reset_unfitted()
            return self

        # 按 confidence 升序（保序回归/分箱前提）；h 取组均值作单调回归目标
        order = np.argsort(c, kind="stable")
        cs, hs = c[order], h[order]
        try:
            if self.method == "platt":
                self._fit_platt(cs, hs)
            elif self.method == "isotonic":
                self._fit_isotonic(cs, hs)
            else:
                self._fit_binning(cs, hs)
        except Exception:  # sklearn 缺失/拟合异常 → 降级 binning（零依赖兜底）
            self._fit_binning(cs, hs)
        self._fitted = self._mapping is not None
        self._n_samples = int(len(cs))
        return self

    def _reset_unfitted(self) -> None:
        self._fitted = False
        self._n_samples = 0
        self._mapping = None

    def _fit_isotonic(self, cs: np.ndarray, hs: np.ndarray) -> None:
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(cs, hs)
        self._mapping = {
            "kind": "isotonic",
            "x": [float(v) for v in iso.X_thresholds_],
            "y": [float(v) for v in iso.y_thresholds_],
        }

    def _fit_platt(self, cs: np.ndarray, hs: np.ndarray) -> None:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(max_iter=1000)
        model.fit(cs.reshape(-1, 1), hs)
        self._mapping = {
            "kind": "platt",
            "a": float(model.coef_[0][0]),
            "b": float(model.intercept_[0]),
        }

    def _fit_binning(self, cs: np.ndarray, hs: np.ndarray) -> None:
        """等频分箱 + 组内命中率（零依赖，isotonic/platt 的降级兜底）。"""
        n_bins = max(1, min(int(self.n_bins), len(cs) // 5))
        edges = np.unique(np.quantile(cs, np.linspace(0.0, 1.0, n_bins + 1)))
        if len(edges) < 2:
            self._mapping = None
            return
        bin_idx = np.clip(np.searchsorted(edges, cs, side="right") - 1, 0, len(edges) - 2)
        rates = []
        for i in range(len(edges) - 1):
            mask = bin_idx == i
            rates.append(float(hs[mask].mean()) if mask.any() else 0.5)  # 空箱中性 0.5
        self._mapping = {
            "kind": "binning",
            "edges": [float(v) for v in edges],
            "rates": rates,
        }

    # ── 预测 ──────────────────────────────────────────────

    @property
    def calibrated(self) -> bool:
        """是否已完成有效拟合（predict 可应用校准映射）。"""
        return self._fitted and self._mapping is not None

    def predict(self, confidence: float) -> float:
        """返回统计校准后的置信度（∈[0,1]）；未拟合时透传 clip 后的原始值。"""
        v = float(np.clip(confidence, 0.0, 1.0))
        if not self.calibrated or self._mapping is None:
            return v
        kind = self._mapping.get("kind")
        if kind == "platt":
            logit = self._mapping["a"] * v + self._mapping["b"]
            p = 1.0 / (1.0 + float(np.exp(-logit)))
        elif kind == "isotonic":
            x = np.asarray(self._mapping["x"], dtype=float)
            y = np.asarray(self._mapping["y"], dtype=float)
            p = float(np.interp(v, x, y))
        else:  # binning
            edges = np.asarray(self._mapping["edges"], dtype=float)
            rates = np.asarray(self._mapping["rates"], dtype=float)
            idx = int(np.searchsorted(edges, v, side="right") - 1)
            p = float(rates[min(max(idx, 0), len(rates) - 1)])
        return float(np.clip(p, 0.0, 1.0))

    # ── 校准质量评估 ──────────────────────────────────────

    def brier_score(self, confidences: Sequence[float], hits: Sequence[int]) -> float:
        """Brier 得分 = mean((predict - hit)^2)，越小越准（0 完美，≤0.25 优于随机）。
        未拟合时以原始置信度为预测（等价于无校准基线）。"""
        c = np.asarray(list(confidences), dtype=float)
        h = np.asarray(list(hits), dtype=float)
        if len(c) != len(h) or len(c) == 0:
            return float("nan")
        pred = np.asarray([self.predict(x) for x in c], dtype=float)
        return float(np.mean((pred - h) ** 2))

    # ── 持久化 ────────────────────────────────────────────

    def save(self, path: str | os.PathLike[str]) -> None:
        """原子写校准映射 JSON（tmp + rename，幂等）。"""
        payload: dict[str, Any] = {
            "gap": "GAP-094",
            "schema_version": 1,
            "method": self.method,
            "fitted": self.calibrated,
            "n_samples": self._n_samples,
            "min_samples": self.min_samples,
            "n_bins": self.n_bins,
            "mapping": self._mapping,
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, target)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "StatisticalRegimeCalibrator":
        """从 JSON 加载校准器；文件缺失/损坏/版本不符时返回未拟合实例（predict 透传）。"""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            cal = cls(
                method=payload.get("method", "isotonic"),
                min_samples=payload.get("min_samples", 30),
                n_bins=payload.get("n_bins", 10),
            )
            if payload.get("schema_version") == 1 and payload.get("fitted"):
                cal._mapping = payload.get("mapping")
                cal._n_samples = int(payload.get("n_samples", 0))
                cal._fitted = True
            return cal
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return cls(method="isotonic")  # 未拟合实例，predict 透传


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
