"""
fts.ml — ML 模型集成层（Phase 24，v2.38.0）。

封装 LightGBM / XGBoost / Ensemble 三种传统 ML 模型，供 L3 信号合成
（ml_ensemble 模式）与独立训练管线使用。

角色边界: 本层只负责信号预测与模型训练，不涉及交易执行。

可选依赖: 安装 `pip install .[ml]` 后可用；未安装时模型创建函数返回
``None``（调用方走降级回退路径），不抛异常。

用法:
    from fts.ml import create_signal_model, SignalModelTrainer

    model = create_signal_model("lightgbm")
    if model is not None:
        model.fit(X, y)
        pred = model.predict(X_test)

版本: v1.0.0
"""

from .models import (
    MLPFactorModel,
    MLSignalModel,
    ModelKind,
    ModelNotAvailableError,
    create_mlp_model,
    create_signal_model,
)
from .trainer import (
    TrainMode,
    SignalModelTrainer,
    TrainResult,
)

__all__ = [
    "MLPFactorModel",
    "MLSignalModel",
    "ModelKind",
    "ModelNotAvailableError",
    "create_mlp_model",
    "create_signal_model",
    "TrainMode",
    "SignalModelTrainer",
    "TrainResult",
]
