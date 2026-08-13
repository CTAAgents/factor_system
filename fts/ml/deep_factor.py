"""
fts/ml/deep_factor.py — 深度因子生成器（GAP-I203，v2.73.0）

从历史行情构造滚动窗口序列样本，训练轻量纯 numpy GRU 因子模型，
将训练好的权重序列化内嵌到因子 code（零未来函数：每步只用截至 t 的
特征窗口推理），产出可过全套审计链的 FactorProgram。

设计约束（对齐 HARNESS 因子研发红线）:
    - 零未来函数：特征窗口 [t-lookback+1, t]，只含 t 及之前数据
    - 可解释性：输出 tanh 压缩为因子信号；权重固化内嵌可审计
    - 沙箱安全：生成 code 仅依赖 numpy + data dict（close/volume）
    - 数据划分：训练集占前 ``train_ratio``（默认 0.7），避免未来信息

用法:
    from fts.ml.deep_factor import DeepFactorGenerator

    gen = DeepFactorGenerator(lookback=10, horizon=5)
    factor = gen.generate(data, forward_returns, market="futures",
                          parent_name="rb_momentum", trace_id="l2_xxx")

版本: v1.0.0（GAP-I203 首期）
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional, cast

import numpy as np

from .models import GRUFactorModel, ModelNotAvailableError, TransformerFactorModel

logger = logging.getLogger(__name__)

# 特征列（生成 code 中使用的行情字段）
_FEATURE_FIELDS = ("close", "volume")


@dataclass
class DeepFactorConfig:
    """深度因子生成配置（GAP-I203 / C5）。

    Attributes:
        model_kind: 深度模型类型 "gru"（默认）| "transformer"（C5）。
        lookback: 滚动窗口步数（序列长度，默认 10）。
        horizon: 前向收益预测窗口（天，默认 5）。
        hidden: 隐藏单元数（默认 8）。
        epochs: 训练轮数（默认 120）。
        learning_rate: 学习率（默认 0.01）。
        train_ratio: 训练集占前比例（默认 0.7，剩余作验证）。
        min_samples: 最小训练样本数（低于则返回 None 降级，默认 32）。
        seed: 随机种子（可复现）。
    """

    model_kind: str = "gru"  # "gru" | "transformer"（C5）
    lookback: int = 10
    horizon: int = 5
    hidden: int = 8
    epochs: int = 120
    learning_rate: float = 0.01
    train_ratio: float = 0.7
    min_samples: int = 32
    seed: int = 42


class DeepFactorGenerator:
    """深度因子生成器 — 训练 GRU 并产出内嵌权重的 FactorProgram。

    生成流程:
        1. 由 OHLCV 构造特征（日收益率 + 成交量变化率）
        2. 构造滚动窗口样本 (n, lookback, 2) 与目标（horizon 前向收益）
        3. 前 ``train_ratio`` 训练 GRU，验证集评估（不参与训练）
        4. 权重序列化生成确定性因子 code（逐 t 窗口滚动推理）
    """

    def __init__(self, config: Optional[DeepFactorConfig] = None) -> None:
        self.config = config or DeepFactorConfig()

    # ─── 主入口 ──────────────────────────────────────────

    def generate(
        self,
        data: Any,
        forward_returns: Any,
        market: str = "futures",
        parent_name: str = "parent",
        trace_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """训练 GRU 并生成深度因子。

        Args:
            data: OHLCV DataFrame（含 close/volume 列）
            forward_returns: 前向收益序列（与 data 对齐，长度一致）
            market: 市场类型（futures/stock/etf）
            parent_name: 父因子名（用于因子命名）
            trace_id: 全链路 trace_id

        Returns:
            FactorProgram dict（含 code/params/signature/economic_logic）；
            样本不足或训练失败返回 None（调用方降级回退）
        """
        close, volume, y = self._prepare_series(data, forward_returns)
        if close is None:
            return None

        n = len(close)
        if n < self.config.lookback + self.config.horizon + self.config.min_samples:
            logger.warning(
                "[DeepFactor] 序列过短 (%d)，低于 lookback+horizon+min_samples，降级回退",
                n,
            )
            return None

        # 构造特征与窗口样本
        feat = self._build_features(close, volume)  # (n, 2)
        X, y_seq, valid_from = self._build_windows(feat, y)
        if X is None or y_seq is None or len(X) < self.config.min_samples:
            return None

        # 训练/验证划分（前 train_ratio 训练，零未来函数）
        n_train = max(int(len(X) * self.config.train_ratio), self.config.min_samples)
        if n_train >= len(X):
            n_train = len(X) - 1 if len(X) > 1 else len(X)
        X_train, y_train = X[:n_train], y_seq[:n_train]
        X_val, y_val = X[n_train:], y_seq[n_train:]

        model: Any
        try:
            if self.config.model_kind == "transformer":
                model = TransformerFactorModel(
                    hidden=self.config.hidden,
                    learning_rate=self.config.learning_rate,
                    epochs=self.config.epochs,
                    seed=self.config.seed,
                    min_samples=self.config.min_samples,
                )
            else:
                model = GRUFactorModel(
                    hidden=self.config.hidden,
                    learning_rate=self.config.learning_rate,
                    epochs=self.config.epochs,
                    seed=self.config.seed,
                    min_samples=self.config.min_samples,
                )
            model.fit(X_train, y_train)
        except ModelNotAvailableError as e:
            logger.warning("[DeepFactor] %s 训练降级: %s", self.config.model_kind, e)
            return None

        # 验证集评估（用于日志，不参与权重）
        val_ic = 0.0
        if len(X_val) >= 8:
            try:
                pred_val = model.predict(X_val)
                mask = np.isfinite(pred_val) & np.isfinite(y_val)
                if mask.sum() >= 8:
                    val_ic = float(np.corrcoef(pred_val[mask], y_val[mask])[0, 1])
                    if not np.isfinite(val_ic):
                        val_ic = 0.0
            except Exception:  # noqa: BLE001
                val_ic = 0.0
        logger.info(
            "[DeepFactor] %s 训练完成: lookback=%d hidden=%d train=%d val=%d val_ic=%.4f",
            self.config.model_kind,
            self.config.lookback,
            self.config.hidden,
            len(X_train),
            len(X_val),
            val_ic,
        )

        # 生成因子（内嵌权重）
        return self._build_factor(
            model=model,
            model_kind=self.config.model_kind,
            feat_mean=feat[:n_train].mean(axis=0),
            feat_std=feat[:n_train].std(axis=0),
            market=market,
            parent_name=parent_name,
            trace_id=trace_id,
            val_ic=val_ic,
            n_samples=n,
        )

    # ─── 因子构造 ────────────────────────────────────────

    def _build_factor(
        self,
        model: Any,
        model_kind: str,
        feat_mean: np.ndarray,
        feat_std: np.ndarray,
        market: str,
        parent_name: str,
        trace_id: Optional[str],
        val_ic: float,
        n_samples: int,
    ) -> dict[str, Any]:
        """构造 FactorProgram（code 内嵌深度模型权重 + 特征标准化统计）。"""
        params = model.get_params()
        cfg = self.config

        # 权重序列化为紧凑 numpy 字面量（repl 精度保留，可复现）
        w_reprs = {name: self._arr_repr(arr) for name, arr in params.items()}
        # 特征标准化（训练集统计，训练段内，无未来信息）
        std_safe = np.where(np.abs(feat_std) < 1e-12, 1.0, feat_std)
        mean_repr = self._arr_repr(feat_mean)
        std_repr = self._arr_repr(std_safe)

        code = self._build_code(
            model_kind=model_kind,
            lookback=cfg.lookback,
            hidden=cfg.hidden,
            w_reprs=w_reprs,
            mean_repr=mean_repr,
            std_repr=std_repr,
        )

        from fts.factor_engine.contracts import (
            EconomicLogic,
            FactorSignature,
        )
        from fts.factor_engine.factor_program import create_factor_program

        unique_key = f"deep_{parent_name}_{time.time_ns()}_{cfg.lookback}_{cfg.hidden}_{model_kind}"
        factor_id = "fct_" + hashlib.md5(unique_key.encode()).hexdigest()[:8]
        factor_name = f"deep_{model_kind}_{cfg.lookback}_{factor_id[:6]}"

        signature = FactorSignature(
            input_fields=list(_FEATURE_FIELDS),
            output_type="signal",
            frequency="daily",
            lookback=cfg.lookback,
        )
        model_label = "GRU" if model_kind == "gru" else "Transformer"
        factor: dict[str, Any] = cast(
            dict[str, Any],
            create_factor_program(
                name=factor_name,
                code=code,
                params={
                    "lookback": cfg.lookback,
                    "hidden": cfg.hidden,
                    "horizon": cfg.horizon,
                    "model_kind": model_kind,
                },
                signature=signature,
                economic_logic=EconomicLogic(
                    theory=3,
                    behavioral=3,
                    microstructure=3,
                    institutional=3,
                    narrative=(
                        f"深度时序因子（GAP-I203/C5）: 单层{model_label}对 {cfg.lookback} 日滚动窗口"
                        f"（收益率+量变化）提取时序特征，预测 {cfg.horizon} 日前向收益；"
                        f"权重由训练段固化内嵌，逐 t 窗口滚动推理（零未来函数）。"
                        f"验证集 IC={val_ic:.4f}，训练样本 {n_samples}。"
                    ),
                ),
                source="deep_evolution",
                market=market,
                family="deep",
                trace_id=trace_id,
            ),
        )
        factor["factor_id"] = factor_id
        factor["parent_id"] = parent_name
        factor["generation"] = 0
        factor["kind"] = "code"
        factor["deep_model"] = {
            "model": model_kind,
            "lookback": cfg.lookback,
            "hidden": cfg.hidden,
            "horizon": cfg.horizon,
            "val_ic": round(float(val_ic), 4),
        }
        return factor

    # ─── code 生成 ───────────────────────────────────────

    def _build_code(
        self,
        model_kind: str,
        lookback: int,
        hidden: int,
        w_reprs: dict[str, str],
        mean_repr: str,
        std_repr: str,
    ) -> str:
        """生成确定性因子 code：按模型类型分派前向 + 特征标准化 + 逐窗口滚动推理。"""
        if model_kind == "transformer":
            return self._build_code_transformer(lookback, hidden, w_reprs, mean_repr, std_repr)
        return self._build_code_gru(lookback, hidden, w_reprs, mean_repr, std_repr)

    def _build_code_gru(
        self,
        lookback: int,
        hidden: int,
        w_reprs: dict[str, str],
        mean_repr: str,
        std_repr: str,
    ) -> str:
        """生成确定性因子 code：GRU 前向 + 特征标准化 + 逐窗口滚动推理。"""
        code = f"""\
def factor_program(data, params):
    import numpy as np
    close = np.asarray(data.get('close'), dtype=float) if 'close' in data else None
    volume = np.asarray(data.get('volume'), dtype=float) if 'volume' in data else None
    n = len(close)
    lookback = {lookback}
    hidden = {hidden}
    # ── 特征: 日收益率 + 成交量变化率 ──
    ret = np.zeros(n)
    ret[1:] = np.diff(close) / np.maximum(np.abs(close[:-1]), 1e-10)
    if volume is not None:
        vol_chg = np.zeros(n)
        vol_chg[1:] = np.diff(volume) / np.maximum(volume[:-1], 1e-10)
    else:
        vol_chg = np.zeros(n)
    feat = np.stack([ret, vol_chg], axis=1)  # (n, 2)
    # ── 特征标准化（训练段统计，固定） ──
    fmean = np.array({mean_repr}, dtype=float)
    fstd = np.array({std_repr}, dtype=float)
    feat = (feat - fmean) / fstd
    # ── GRU 权重 ──
    Wz = np.array({w_reprs["Wz"]}, dtype=float)
    Uz = np.array({w_reprs["Uz"]}, dtype=float)
    bz = np.array({w_reprs["bz"]}, dtype=float)
    Wr = np.array({w_reprs["Wr"]}, dtype=float)
    Ur = np.array({w_reprs["Ur"]}, dtype=float)
    br = np.array({w_reprs["br"]}, dtype=float)
    Wh = np.array({w_reprs["Wh"]}, dtype=float)
    Uh = np.array({w_reprs["Uh"]}, dtype=float)
    bh = np.array({w_reprs["bh"]}, dtype=float)
    Wo = np.array({w_reprs["Wo"]}, dtype=float)
    bo = np.array({w_reprs["bo"]}, dtype=float)
    sig = lambda x: 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))
    out = np.zeros(n)
    if n < lookback:
        return out
    for t in range(lookback - 1, n):
        h = np.zeros(hidden)
        for k in range(lookback):
            xt = feat[t - lookback + 1 + k]
            z = sig(xt @ Wz + h @ Uz + bz)
            r = sig(xt @ Wr + h @ Ur + br)
            ht = np.tanh(xt @ Wh + (r * h) @ Uh + bh)
            h = (1.0 - z) * h + z * ht
        out[t] = float(np.tanh(h @ Wo + bo).item())
    return out
"""
        return code

    def _build_code_transformer(
        self,
        lookback: int,
        hidden: int,
        w_reprs: dict[str, str],
        mean_repr: str,
        std_repr: str,
    ) -> str:
        """生成确定性因子 code：单头自注意力 + 因果掩码 + LN + 最后步预测（C5）。"""
        code = f"""\
def factor_program(data, params):
    import numpy as np
    close = np.asarray(data.get('close'), dtype=float) if 'close' in data else None
    volume = np.asarray(data.get('volume'), dtype=float) if 'volume' in data else None
    n = len(close)
    lookback = {lookback}
    hidden = {hidden}
    # ── 特征: 日收益率 + 成交量变化率 ──
    ret = np.zeros(n)
    ret[1:] = np.diff(close) / np.maximum(np.abs(close[:-1]), 1e-10)
    if volume is not None:
        vol_chg = np.zeros(n)
        vol_chg[1:] = np.diff(volume) / np.maximum(volume[:-1], 1e-10)
    else:
        vol_chg = np.zeros(n)
    feat = np.stack([ret, vol_chg], axis=1)  # (n, 2)
    # ── 特征标准化（训练段统计，固定） ──
    fmean = np.array({mean_repr}, dtype=float)
    fstd = np.array({std_repr}, dtype=float)
    feat = (feat - fmean) / fstd
    # ── 单头自注意力权重 ──
    Wq = np.array({w_reprs["Wq"]}, dtype=float)
    Wk = np.array({w_reprs["Wk"]}, dtype=float)
    Wv = np.array({w_reprs["Wv"]}, dtype=float)
    Wo = np.array({w_reprs["Wo"]}, dtype=float)
    bo = np.array({w_reprs["bo"]}, dtype=float)
    pos = np.array({w_reprs["pos_emb"]}, dtype=float)
    # 因果掩码（上三角 −inf）：t 时刻只用 ≤t 输入（零未来函数）
    mask = np.triu(np.ones((lookback, lookback)), k=1)
    out = np.zeros(n)
    if n < lookback:
        return out
    for t in range(lookback - 1, n):
        Xw = feat[t - lookback + 1 : t + 1]  # (lookback, 2)，窗口止于 t
        Q = Xw @ Wq
        K = Xw @ Wk
        V = Xw @ Wv
        logits = Q @ K.T / np.sqrt(hidden) - 1e9 * mask
        e = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
        attn = e / np.sum(e, axis=-1, keepdims=True)
        ctx = attn @ V + pos
        m = ctx.mean(axis=-1, keepdims=True)
        s = ctx.std(axis=-1, keepdims=True) + 1e-6
        ctx_ln = (ctx - m) / s
        out[t] = float(np.tanh(ctx_ln[-1] @ Wo + bo).item())
    return out
"""
        return code

    # ─── 数据准备 ────────────────────────────────────────

    @staticmethod
    def _prepare_series(
        data: Any,
        forward_returns: Any,
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """提取 close/volume 与对齐的目标序列。返回 (close, volume, y)。"""
        if data is None:
            return None, None, None
        try:
            close = np.asarray(data["close"], dtype=float)
        except (KeyError, TypeError, ValueError):
            return None, None, None
        volume = None
        if "volume" in data:
            try:
                volume = np.asarray(data["volume"], dtype=float)
            except (TypeError, ValueError):
                volume = None
        try:
            y = np.asarray(forward_returns, dtype=float)
        except (TypeError, ValueError):
            return None, None, None
        if close.shape[0] != y.shape[0]:
            return None, None, None
        return close, volume, y

    @staticmethod
    def _build_features(
        close: np.ndarray,
        volume: Optional[np.ndarray],
    ) -> np.ndarray:
        """构造特征矩阵 (n, 2)：日收益率 + 成交量变化率。"""
        n = len(close)
        ret = np.zeros(n)
        ret[1:] = np.diff(close) / np.maximum(np.abs(close[:-1]), 1e-10)
        vol_chg = np.zeros(n)
        if volume is not None and len(volume) == n:
            vol_chg[1:] = np.diff(volume) / np.maximum(volume[:-1], 1e-10)
        return np.stack([ret, vol_chg], axis=1)

    def _build_windows(
        self,
        feat: np.ndarray,
        y: np.ndarray,
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray], int]:
        """构造滚动窗口样本。

        Returns:
            (X (m, lookback, 2), y_seq (m,), valid_from)
            m = n - lookback - horizon + 1；样本不足返回 (None, None, 0)
        """
        n = len(feat)
        lb = self.config.lookback
        hz = self.config.horizon
        m = n - lb - hz + 1
        if m < self.config.min_samples:
            return None, None, 0
        X: np.ndarray = np.zeros((m, lb, 2), dtype=float)
        y_seq: np.ndarray = np.zeros(m, dtype=float)
        for i in range(m):
            X[i] = feat[i : i + lb]
            # 目标: t+1..t+horizon 前向收益均值（对齐 forward_returns 的语义）
            y_seq[i] = float(np.nanmean(y[i + lb : i + lb + hz]))
        return X, y_seq, lb

    @staticmethod
    def _arr_repr(arr: np.ndarray) -> str:
        """numpy 数组 → 紧凑可执行字面量（保留 6 位小数）。"""
        return repr(np.round(np.asarray(arr, dtype=float), 6).tolist())


def create_deep_factor(
    data: Any,
    forward_returns: Any,
    market: str = "futures",
    parent_name: str = "parent",
    trace_id: Optional[str] = None,
    config: Optional[DeepFactorConfig] = None,
) -> Optional[dict[str, Any]]:
    """便捷入口：生成深度因子。

    Returns:
        FactorProgram dict；样本不足/训练失败返回 None（调用方降级）。
    """
    gen = DeepFactorGenerator(config)
    return gen.generate(
        data,
        forward_returns,
        market=market,
        parent_name=parent_name,
        trace_id=trace_id,
    )


__all__ = [
    "DeepFactorConfig",
    "DeepFactorGenerator",
    "create_deep_factor",
]
