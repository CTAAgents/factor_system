"""
fts/factor_engine/shap_analyzer.py — 局部可解释性分析（SHAP）

HARNESS §11-logic-review-plan.md §B.1:
    对极端预测样本进行特征归因，输出 top-5 贡献特征。

设计:
    - 使用 shap.KernelExplainer（模型无关），对任意 FactorProgram 执行
    - 找出预测收益最高的前 N 个和最低的前 N 个极端样本
    - 对这些样本计算 SHAP 值，列出 top-5 贡献特征
    - 输出可读报告到 reports/{date}/shap_analysis_{factor_id}.json

版本: v1.0.0
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from .contracts import FactorProgram
from .factor_program import FactorExecutor


# ─── SHAP 分析结果契约 ────────────────────────────────────


class ShapFeatureImportance(dict):
    """单个特征的重要性。"""

    def __init__(
        self,
        feature_name: str,
        shap_value: float,
        impact_direction: str,
    ) -> None:
        super().__init__()
        self["feature_name"] = feature_name
        self["shap_value"] = shap_value
        self["impact_direction"] = impact_direction  # "positive" / "negative"


class ShapSampleAnalysis(dict):
    """单个样本的 SHAP 分析结果。"""

    def __init__(
        self,
        sample_index: int,
        date: str,
        signal_value: float,
        top_features: list[ShapFeatureImportance],
    ) -> None:
        super().__init__()
        self["sample_index"] = sample_index
        self["date"] = date
        self["signal_value"] = signal_value
        self["top_features"] = top_features


class ShapAnalysisResult(dict):
    """完整 SHAP 分析结果。"""

    def __init__(
        self,
        factor_id: str,
        factor_name: str,
        analysis_date: str,
        num_extreme_samples: int,
        num_features: int,
        top_samples: list[ShapSampleAnalysis],
        bottom_samples: list[ShapSampleAnalysis],
        global_top_features: list[ShapFeatureImportance],
        summary: dict[str, Any],
    ) -> None:
        super().__init__()
        self["factor_id"] = factor_id
        self["factor_name"] = factor_name
        self["analysis_date"] = analysis_date
        self["num_extreme_samples"] = num_extreme_samples
        self["num_features"] = num_features
        self["top_samples"] = top_samples
        self["bottom_samples"] = bottom_samples
        self["global_top_features"] = global_top_features
        self["summary"] = summary


# ─── SHAP 分析器 ──────────────────────────────────────────


class ShapAnalyzer:
    """SHAP 局部可解释性分析器。

    对因子程序的极端预测样本进行特征归因，
    使用 KernelExplainer（模型无关 SHAP）。

    Usage:
        analyzer = ShapAnalyzer(n_extreme=50, n_background=100)
        result = analyzer.analyze(factor, data, forward_returns)
        print(analyzer.report(result))
        analyzer.save_report(result, "reports/2026-08-04/")
    """

    def __init__(
        self,
        n_extreme: int = 50,
        n_background: int = 100,
        random_seed: int = 42,
    ):
        """
        Args:
            n_extreme: 从两端各取多少样本做 SHAP 分析（默认 50）
            n_background: KernelExplainer 的背景样本数（默认 100）
            random_seed: 随机种子
        """
        self._n_extreme = n_extreme
        self._n_background = n_background
        self._random_seed = random_seed

        self._executor: Optional[FactorExecutor] = None

    def _get_feature_cols(self, data: pd.DataFrame) -> list[str]:
        """获取用于 SHAP 分析的特征列。

        排除 date 和 index 列，只保留数值特征。
        """
        exclude = {"date", "index", "datetime", "timestamp"}
        return [c for c in data.columns if c.lower() not in exclude]

    def _make_predict_fn(
        self,
        factor: FactorProgram,
        feature_cols: list[str],
    ) -> Callable[..., Any]:
        """创建 SHAP KernelExplainer 用的预测函数。

        预测函数接收特征子集，通过 FactorExecutor 执行因子程序，
        返回因子信号值。
        """
        executor = self._executor
        if executor is None:
            executor = FactorExecutor(factor)
            self._executor = executor

        def predict_fn(X: np.ndarray) -> np.ndarray:
            """接收特征矩阵，返回信号数组。

            Args:
                X: shape (n_samples, n_features) 的特征矩阵

            Returns:
                np.ndarray: shape (n_samples,) 的信号值
            """
            n = X.shape[0]
            signals = np.zeros(n)

            for i in range(n):
                # 构造单行数据
                row_data = pd.DataFrame(
                    [X[i]],
                    columns=feature_cols,
                )
                # 补全可能缺失的列（如 vwap、settle 等）
                for col in ["open", "high", "low", "close", "volume"]:
                    if col not in row_data.columns:
                        row_data[col] = 0.0

                try:
                    sig = executor.execute(row_data, {})
                    signals[i] = float(sig[-1]) if len(sig) > 0 else 0.0
                except Exception:
                    signals[i] = 0.0

            return signals

        return predict_fn

    def analyze(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        forward_returns: Optional[np.ndarray] = None,
    ) -> ShapAnalysisResult:
        """对因子执行 SHAP 分析。

        Args:
            factor: 因子程序
            data: OHLCV 数据
            forward_returns: 可选，未来收益率，用于排序极端样本

        Returns:
            ShapAnalysisResult
        """
        import shap  # 延迟导入，避免未安装时报错

        np.random.seed(self._random_seed)

        # 执行因子程序获取全量信号
        executor = FactorExecutor(factor)
        full_signal = executor.execute(data, {})
        if len(full_signal) != len(data):
            full_signal = np.full(len(data), np.nan)

        # 用 forward_returns 排序极端样本（若无则用信号值排序）
        ranking = forward_returns if forward_returns is not None else full_signal
        valid_mask = ~np.isnan(ranking) & ~np.isnan(full_signal)
        valid_indices = np.where(valid_mask)[0]

        if len(valid_indices) < self._n_extreme * 2:
            self._n_extreme = max(1, len(valid_indices) // 4)

        # 排序
        sorted_idx = valid_indices[np.argsort(ranking[valid_indices])]
        bottom_indices = sorted_idx[: self._n_extreme]  # 最低收益
        top_indices = sorted_idx[-self._n_extreme :]  # 最高收益

        # 准备特征
        feature_cols = self._get_feature_cols(data)
        X = data[feature_cols].values.astype(np.float64)

        # 用背景样本初始化 KernelExplainer
        bg_idx = np.random.choice(len(X), min(self._n_background, len(X)), replace=False)
        X_background = X[bg_idx]
        predict_fn = self._make_predict_fn(factor, feature_cols)

        # 创建 KernelExplainer
        explainer = shap.KernelExplainer(predict_fn, X_background)

        # 分析极端样本
        top_samples: list[ShapSampleAnalysis] = []
        bottom_samples: list[ShapSampleAnalysis] = []

        # 收集所有 SHAP 值用于全局统计
        all_shap_values: list[np.ndarray] = []

        for idx_list, sample_list in [
            (top_indices, top_samples),
            (bottom_indices, bottom_samples),
        ]:
            for idx in idx_list:
                X_sample = X[idx : idx + 1]
                shap_values = explainer.shap_values(X_sample, nsamples=100)

                if isinstance(shap_values, list):
                    sv = shap_values[0]
                else:
                    sv = shap_values

                if sv.ndim > 1:
                    sv = sv.flatten()

                all_shap_values.append(sv)

                # 计算 top-5 特征
                abs_sv = np.abs(sv)
                top5_idx = np.argsort(abs_sv)[-5:][::-1]

                date_str = ""
                if "date" in data.columns:
                    date_str = str(data.iloc[idx]["date"])

                features = [
                    ShapFeatureImportance(
                        feature_name=feature_cols[j],
                        shap_value=float(sv[j]),
                        impact_direction="positive" if sv[j] > 0 else "negative",
                    )
                    for j in top5_idx
                ]

                sample_list.append(
                    ShapSampleAnalysis(
                        sample_index=int(idx),
                        date=date_str,
                        signal_value=float(full_signal[idx]),
                        top_features=features,
                    )
                )

        # 全局 top-5 特征（基于所有 SHAP 值均值的绝对值）
        if all_shap_values:
            mean_abs_shap = np.mean([np.abs(sv) for sv in all_shap_values], axis=0)
            global_top5_idx = np.argsort(mean_abs_shap)[-5:][::-1]
            global_top_features = [
                ShapFeatureImportance(
                    feature_name=feature_cols[j],
                    shap_value=float(mean_abs_shap[j]),
                    impact_direction="mixed",
                )
                for j in global_top5_idx
            ]
        else:
            global_top_features = []

        # 汇总
        summary = {
            "total_samples": len(data),
            "valid_samples": int(np.sum(valid_mask)),
            "n_extreme_analyzed": self._n_extreme,
            "n_background": min(self._n_background, len(X)),
            "n_features": len(feature_cols),
            "feature_names": feature_cols,
            "signal_range": [
                float(np.nanmin(full_signal)),
                float(np.nanmax(full_signal)),
            ],
            "signal_mean": float(np.nanmean(full_signal)),
            "signal_std": float(np.nanstd(full_signal)),
        }

        return ShapAnalysisResult(
            factor_id=factor.get("factor_id", "unknown"),
            factor_name=factor.get("name", "unknown"),
            analysis_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            num_extreme_samples=self._n_extreme,
            num_features=len(feature_cols),
            top_samples=top_samples,
            bottom_samples=bottom_samples,
            global_top_features=global_top_features,
            summary=summary,
        )

    @staticmethod
    def report(result: ShapAnalysisResult) -> str:
        """生成可读的 SHAP 分析报告。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f"SHAP 分析报告 — {result['factor_name']} ({result['factor_id']})")
        lines.append(f"分析日期: {result['analysis_date']}")
        lines.append("=" * 70)
        lines.append("\n全局 Top-5 特征（按平均 |SHAP| 排序）:")
        lines.append(f"  {'特征':<20} {'平均 |SHAP|':>12} {'影响方向':>10}")
        lines.append(f"  {'-' * 20} {'-' * 12} {'-' * 10}")
        for feat in result["global_top_features"]:
            lines.append(f"  {feat['feature_name']:<20} {feat['shap_value']:>12.4f} {feat['impact_direction']:>10}")

        lines.append(f"\n\nTop {result['num_extreme_samples']} 样本（收益最高）:")
        lines.append(f"  {'样本索引':<10} {'日期':<14} {'信号值':>10} → Top-5 特征")
        for s in result["top_samples"]:
            feat_str = ", ".join(f"{f['feature_name']}({f['shap_value']:+.3f})" for f in s["top_features"])
            lines.append(f"  {s['sample_index']:<10} {s['date']:<14} {s['signal_value']:>10.4f} → {feat_str}")

        lines.append(f"\n\nBottom {result['num_extreme_samples']} 样本（收益最低）:")
        lines.append(f"  {'样本索引':<10} {'日期':<14} {'信号值':>10} → Top-5 特征")
        for s in result["bottom_samples"]:
            feat_str = ", ".join(f"{f['feature_name']}({f['shap_value']:+.3f})" for f in s["top_features"])
            lines.append(f"  {s['sample_index']:<10} {s['date']:<14} {s['signal_value']:>10.4f} → {feat_str}")

        lines.append("\n\n汇总:")
        for k, v in result["summary"].items():
            lines.append(f"  {k}: {v}")

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)

    @staticmethod
    def save_report(
        result: ShapAnalysisResult,
        output_dir: str = "reports",
    ) -> str:
        """将 SHAP 分析结果保存为 JSON 文件。

        Returns:
            文件路径
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_dir = os.path.join(output_dir, date_str)
        os.makedirs(out_dir, exist_ok=True)

        filepath = os.path.join(
            out_dir,
            f"shap_analysis_{result['factor_id']}.json",
        )

        # 将 TypedDict 转为普通 dict 以便 JSON 序列化
        def _to_dict(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _to_dict(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_to_dict(v) for v in obj]
            return obj

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(_to_dict(result), f, ensure_ascii=False, indent=2)

        return filepath


__all__ = [
    "ShapFeatureImportance",
    "ShapSampleAnalysis",
    "ShapAnalysisResult",
    "ShapAnalyzer",
]
