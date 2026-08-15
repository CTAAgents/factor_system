"""seed_loader.py — 种子因子 YAML 加载器

从 seeds/ 目录加载 YAML 格式的种子因子定义，转换为 FactorProgram 对象。
支持三种因子类型：
  1. code-based: 直接包含完整 factor_program 代码（期货、内置）
  2. expression-based: 表达式型因子（WQ101/Qlib158/GTJA191），通过代码模板生成
  3. fundamental-based: 基本面因子，通过模板 + 字段注入生成

双路径读取: YAML 优先，硬编码兜底。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from .contracts import EconomicLogic, FactorKind, FactorProgram, FactorSignature
from .factor_program import create_factor_program

logger = logging.getLogger(__name__)

# ─── 期货 YAML 种子文件名（按文件名校验加载完整性） ──────
_FUTURES_SEED_FILES: frozenset[str] = frozenset({
    "momentum.yaml",
    "term_structure.yaml",
    "position_flow.yaml",
    "liquidity.yaml",
    "higher_moments.yaml",
    "volatility.yaml",
    "fundamental.yaml",
    "crowding.yaml",
    "alpha_behavior.yaml",
    "high_frequency.yaml",
    "options.yaml",
    "market_regime.yaml",
    "cta_registry.yaml",
    "operator_dict.yaml",
    "vnpy_cta.yaml",
    "wind_cta.yaml",
    "mc_cta.yaml",
    "academic_papers.yaml",
    "broker_reports.yaml",
    "tinysoft.yaml",
})

# ─── 路径配置 ─────────────────────────────────────────────────

_SEEDS_DIR: Optional[Path] = None


def get_seeds_dir() -> Path:
    """获取 seeds 目录路径（项目根目录下的 seeds/）。"""
    global _SEEDS_DIR
    if _SEEDS_DIR is None:
        _SEEDS_DIR = Path(__file__).resolve().parent.parent.parent / "seeds"
    return _SEEDS_DIR


def set_seeds_dir(path: str | Path | None) -> None:
    """设置 seeds 目录路径（用于测试或自定义路径）。None 重置为默认。"""
    global _SEEDS_DIR
    if path is None:
        _SEEDS_DIR = None
    else:
        _SEEDS_DIR = Path(path)


# ─── YAML 读取 ────────────────────────────────────────────────


def load_yaml_file(filepath: Path) -> dict[str, Any]:
    """读取单个 YAML 文件。"""
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_yaml_files(directory: Path) -> list[Path]:
    """列出目录下所有 YAML 文件。"""
    if not directory.exists():
        return []
    return sorted(directory.glob("*.yaml"))


# ─── 类型 1: Code-based 因子 ─────────────────────────────────


def _code_factor_from_yaml(defn: dict[str, Any], market: str) -> FactorProgram:
    """从 YAML 定义创建代码型因子（期货、内置种子）。"""
    signature = FactorSignature(
        input_fields=defn.get("input_fields", ["close"]),
        output_type=defn.get("output_type", "signal"),
        frequency=defn.get("frequency", "daily"),
        lookback=defn.get("lookback", 10),
    )
    logic = EconomicLogic(
        theory=defn.get("economic_logic", {}).get("theory", 0),
        behavioral=defn.get("economic_logic", {}).get("behavioral", 0),
        microstructure=defn.get("economic_logic", {}).get("microstructure", 0),
        institutional=defn.get("economic_logic", {}).get("institutional", 0),
        narrative=defn.get("economic_logic", {}).get("narrative", ""),
    )
    # YAML `code: |2` 块会保留额外缩进，统一 dedent + strip
    # 避免 `def` 前残留前导空白导致编译失败（如 fut_tsmom_vol_scaled）
    import textwrap

    code = textwrap.dedent(defn["code"]).strip()
    return create_factor_program(
        name=defn["name"],
        code=code,
        params=defn.get("params", {}),
        signature=signature,
        economic_logic=logic,
        source="seed",
        generation=0,
        market=defn.get("market") or market,
        symbols=defn.get("symbols", []),
        kind=FactorKind.CODE,
    )


# ─── 类型 2: Expression-based 因子 ───────────────────────────

_EXPRESSION_OPS_SOURCE = """
    import numpy as np
    import pandas as pd
    _EPS = 1e-10
    def _to_series(x):
        return x if isinstance(x, pd.Series) else pd.Series(x)
    def _to_array(x):
        return x.values if isinstance(x, pd.Series) else np.asarray(x)
    def rank(x):
        n = len(x)
        if n <= 1: return np.zeros_like(x)
        return np.argsort(np.argsort(x)).astype(float) / (n - 1)
    def scale(x, a=1.0):
        s = np.sum(np.abs(x))
        return x * a / s if s > _EPS else x
    def ifelse(cond, a, b):
        return np.where(cond, a, b)
    def ts_sum(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).sum())
    def ts_mean(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).mean())
    def ts_stddev(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).std(ddof=0))
    def ts_corr(x, y, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).corr(_to_series(y)))
    def ts_covariance(x, y, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).cov(_to_series(y)))
    def ts_argmax(x, d):
        arr = _to_array(x)
        n = len(arr)
        out = np.full(n, np.nan, dtype=float)
        if n > 0:
            m = min(d, n)
            for k in range(1, m):
                if np.count_nonzero(~np.isnan(arr[:k])) >= 1:
                    out[k - 1] = np.argmax(arr[:k])
            view = np.lib.stride_tricks.sliding_window_view(arr, m)
            valid = np.sum(~np.isnan(view), axis=-1) >= 1
            res = np.argmax(view, axis=-1)
            out[m - 1:] = np.where(valid, res, np.nan)
        return out
    def ts_argmin(x, d):
        arr = _to_array(x)
        n = len(arr)
        out = np.full(n, np.nan, dtype=float)
        if n > 0:
            m = min(d, n)
            for k in range(1, m):
                if np.count_nonzero(~np.isnan(arr[:k])) >= 1:
                    out[k - 1] = np.argmin(arr[:k])
            view = np.lib.stride_tricks.sliding_window_view(arr, m)
            valid = np.sum(~np.isnan(view), axis=-1) >= 1
            res = np.argmin(view, axis=-1)
            out[m - 1:] = np.where(valid, res, np.nan)
        return out
    def ts_rank(x, d):
        arr = _to_array(x)
        n = len(arr)
        out = np.full(n, np.nan, dtype=float)
        if n > 0:
            m = min(d, n)
            if m == 1:
                out[~np.isnan(arr)] = 0.5
            else:
                for k in range(1, m):
                    head = arr[:k]
                    if np.count_nonzero(~np.isnan(head)) >= 1:
                        out[k - 1] = 0.5 if k <= 1 else np.argsort(np.argsort(head))[-1] / (k - 1)
                view = np.lib.stride_tricks.sliding_window_view(arr, m)
                valid = np.sum(~np.isnan(view), axis=-1) >= 1
                rk = np.full(view.shape[0], np.nan, dtype=float)
                rk[valid] = np.argsort(np.argsort(view[valid], axis=-1), axis=-1)[:, -1] / (m - 1)
                out[m - 1:] = rk
        return out
    def ts_min(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).min())
    def ts_max(x, d):
        return _to_array(_to_series(x).rolling(d, min_periods=1).max())
    def ts_product(x, d):
        arr = _to_array(x)
        n = len(arr)
        out = np.full(n, np.nan, dtype=float)
        if n > 0:
            m = min(d, n)
            np.cumprod(arr, out=out)
            view = np.lib.stride_tricks.sliding_window_view(arr, m)
            out[m - 1:] = np.prod(view, axis=-1)
        return out
    def signed_power(x, a):
        return np.sign(x) * np.abs(x) ** a
    def decay_linear(x, d):
        arr = _to_array(x)
        n = len(arr)
        out = np.full(n, np.nan, dtype=float)
        if n >= d:
            w = np.arange(1, d + 1, dtype=float)
            w = w / w.sum()
            view = np.lib.stride_tricks.sliding_window_view(arr, d)
            out[d - 1:] = np.sum(view * w[None, :], axis=-1)
        return out
    def delta(x, d):
        return x - delay(x, d)
    def delay(x, d):
        return _to_array(_to_series(x).shift(d))
    def log(x):
        return np.log(np.maximum(x, _EPS))
    def sign(x):
        return np.sign(x)
    def abs(x):
        return np.abs(x)
    def neg(x):
        return -x
    def highday(x, d):
        arr = _to_array(x)
        n = len(arr)
        out = np.full(n, np.nan, dtype=float)
        if n > 0:
            m = min(d, n)
            for k in range(1, m):
                if np.count_nonzero(~np.isnan(arr[:k])) >= 1:
                    out[k - 1] = float(k - 1 - np.argmax(arr[:k]))
            view = np.lib.stride_tricks.sliding_window_view(arr, m)
            valid = np.sum(~np.isnan(view), axis=-1) >= 1
            res = (m - 1) - np.argmax(view, axis=-1)
            out[m - 1:] = np.where(valid, res, np.nan)
        return out
    def lowday(x, d):
        arr = _to_array(x)
        n = len(arr)
        out = np.full(n, np.nan, dtype=float)
        if n > 0:
            m = min(d, n)
            for k in range(1, m):
                if np.count_nonzero(~np.isnan(arr[:k])) >= 1:
                    out[k - 1] = float(k - 1 - np.argmin(arr[:k]))
            view = np.lib.stride_tricks.sliding_window_view(arr, m)
            valid = np.sum(~np.isnan(view), axis=-1) >= 1
            res = (m - 1) - np.argmin(view, axis=-1)
            out[m - 1:] = np.where(valid, res, np.nan)
        return out
"""

_EXPRESSION_CODE_TEMPLATE = (
    "def factor_program(data, params):\n"
    '    """Alpha: {name} — {narrative}"""\n'
    "{ops_source}\n"
    '    close = data["close"].values if hasattr(data, "close") else data["close"]\n'
    '    high = data["high"].values if hasattr(data, "high") else data["high"]\n'
    '    low = data["low"].values if hasattr(data, "low") else data["low"]\n'
    '    open_ = data["open"].values if hasattr(data, "open") else data["open"]\n'
    '    volume = data["volume"].values if hasattr(data, "volume") else data["volume"]\n'
    '    vwap = (data.get("vwap", data["close"]).values if hasattr(data, "vwap")\n'
    '            else data.get("vwap", data["close"]))\n'
    "    returns = np.zeros_like(close)\n"
    "    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], _EPS)\n"
    "\n"
    "    score = {expression}\n"
    "    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)\n"
)


def _estimate_input_fields(expression: str) -> list[str]:
    """从表达式推断所需输入字段。"""
    fields = {"close"}
    expr_lower = expression.lower()
    if "volume" in expr_lower:
        fields.add("volume")
    if "high" in expr_lower and "highday" not in expr_lower:
        fields.add("high")
    if "low" in expr_lower and "lowday" not in expr_lower:
        fields.add("low")
    if "open" in expr_lower:
        fields.add("open")
    if "vwap" in expr_lower:
        fields.add("vwap")
    return sorted(fields)


def _expression_factor_from_yaml(defn: dict[str, Any], market: str) -> FactorProgram:
    """从 YAML 定义创建表达式型因子。"""
    from .expr_dsl.seed_analyzer import estimate_lookback_static

    expression = defn["expression"]
    code = _EXPRESSION_CODE_TEMPLATE.format(
        name=defn["name"],
        narrative=defn.get("description", ""),
        ops_source=_EXPRESSION_OPS_SOURCE,
        expression=expression,
    )
    input_fields = defn.get("input_fields") or _estimate_input_fields(expression)
    # GAP-S09 (v2.67.0): 静态 PIT 审计对齐 DSL 编译链——lookback 仅统计
    # 窗口算子（ts_*/delay/delta）的常量参数，避免正则把幂次/分支常量误计入
    lookback = defn.get("lookback") or estimate_lookback_static(expression)

    signature = FactorSignature(
        input_fields=input_fields,
        output_type=defn.get("output_type", "signal"),
        frequency=defn.get("frequency", "daily"),
        lookback=lookback,
    )
    logic = EconomicLogic(
        theory=3,
        behavioral=3,
        microstructure=3,
        institutional=3,
        narrative=defn.get("description", defn["name"]),
    )
    return create_factor_program(
        name=defn["name"],
        code=code,
        params=defn.get("params", {}),
        signature=signature,
        economic_logic=logic,
        source="seed",
        generation=0,
        market=defn.get("market") or market,
        symbols=defn.get("symbols", []),
        kind=FactorKind.OPERATOR,
    )


# ─── 类型 3: Fundamental 因子 ─────────────────────────────────


_FUNDAMENTAL_CODE_TEMPLATE = (
    "def factor_program(data, params):\n"
    '    """Fundamental Alpha: {name} — {narrative}"""\n'
    "    import numpy as np\n"
    '    n = len(data["close"].values) if hasattr(data, "close") else len(data["close"])\n'
    "    {field_defs}\n"
    "\n"
    "    if {field_check}:\n"
    "        score = {expression}\n"
    "    else:\n"
    '        close = data["close"].values if hasattr(data, "close") else data["close"]\n'
    "        returns = np.zeros(n)\n"
    "        returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)\n"
    "        score = np.tanh(returns * 10) * 0.3\n"
    "\n"
    "    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)\n"
)


def _fundamental_factor_from_yaml(defn: dict[str, Any], market: str) -> FactorProgram:
    """从 YAML 定义创建基本面因子。"""
    import textwrap

    field_defs = defn.get("field_defs", "")
    # 多行 field_defs 统一缩进：首行由模板前缀（4 空格）缩进，后续行补 4 空格。
    # YAML `|-` 块剥离共同缩进后可能残留相对缩进，先 strip 各行再统一补缩进，
    # 否则残余缩进叠加会 unexpected indent。
    if field_defs:
        lines = [ln.strip() for ln in textwrap.dedent(field_defs).strip().split("\n") if ln.strip()]
        field_defs = lines[0] + "".join("\n    " + ln for ln in lines[1:])
    code = _FUNDAMENTAL_CODE_TEMPLATE.format(
        name=defn["name"],
        narrative=defn.get("description", ""),
        field_defs=field_defs,
        field_check=defn.get("field_check", "True"),
        expression=defn["expression"],
    )
    signature = FactorSignature(
        input_fields=defn.get("input_fields", ["close"]),
        output_type="signal",
        frequency="daily",
        lookback=defn.get("lookback", 10),
    )
    logic = EconomicLogic(
        theory=4,
        behavioral=3,
        microstructure=3,
        institutional=4,
        narrative=defn.get("description", defn["name"]),
    )
    return create_factor_program(
        name=defn["name"],
        code=code,
        params={},
        signature=signature,
        economic_logic=logic,
        source="seed",
        generation=0,
        market=defn.get("market") or market,
        symbols=defn.get("symbols", []),
        kind=FactorKind.CODE,
    )


# ─── 因子类型检测与加载 ───────────────────────────────────────


def _detect_factor_type(defn: dict[str, Any]) -> str:
    """检测 YAML 因子定义的类型。"""
    if "code" in defn:
        return "code"
    if "field_defs" in defn or "field_check" in defn:
        return "fundamental"
    if "expression" in defn:
        return "expression"
    raise ValueError(f"Unknown factor type in YAML: {defn.get('name', '?')}")


def _factor_from_yaml(defn: dict[str, Any], market: str) -> FactorProgram:
    """从 YAML 定义创建 FactorProgram（自动检测类型）。"""
    ftype = _detect_factor_type(defn)
    if ftype == "code":
        return _code_factor_from_yaml(defn, market)
    if ftype == "expression":
        return _expression_factor_from_yaml(defn, market)
    if ftype == "fundamental":
        return _fundamental_factor_from_yaml(defn, market)
    raise ValueError(f"Unknown factor type: {ftype}")


# ─── 公开 API ────────────────────────────────────────────────


def load_factors_from_yaml(
    filepath: str | Path,
    trace_id: Optional[str] = None,
) -> list[FactorProgram]:
    """从单个 YAML 文件加载因子列表。

    Args:
        filepath: YAML 文件路径
        trace_id: 可选的全链路 trace_id

    Returns:
        FactorProgram 列表
    """
    filepath = Path(filepath)
    data = load_yaml_file(filepath)
    if data is None:
        logger.warning(f"Empty YAML file: {filepath.name}")
        return []
    market = data.get("market", "stock")
    factors = data.get("factors", [])

    result: list[FactorProgram] = []
    for defn in factors:
        try:
            fp = _factor_from_yaml(defn, market)
            result.append(fp)
        except Exception as e:
            logger.warning(f"Failed to load factor '{defn.get('name', '?')}' from {filepath.name}: {e}")

    logger.info(f"Loaded {len(result)} factors from {filepath.name}")
    return result


def load_factors_from_dir(
    directory: str | Path,
    trace_id: Optional[str] = None,
) -> list[FactorProgram]:
    """从目录批量加载所有 YAML 因子文件。

    Args:
        directory: 包含 .yaml 文件的目录
        trace_id: 可选的全链路 trace_id

    Returns:
        FactorProgram 列表（按文件名排序）
    """
    directory = Path(directory)
    yaml_files = list_yaml_files(directory)
    result: list[FactorProgram] = []
    for f in yaml_files:
        result.extend(load_factors_from_yaml(f, trace_id))
    logger.info(f"Loaded {len(result)} factors from {directory} ({len(yaml_files)} files)")
    return result


_EXTERNAL_STOCK_FILES = {"wq101.yaml", "qlib158.yaml", "gtja191.yaml", "fundamental.yaml"}


def load_all_yaml_seeds(
    trace_id: Optional[str] = None,
    market: Optional[str] = None,
    include_external: bool = True,
) -> list[FactorProgram]:
    """从 seeds/ 目录加载所有 YAML 种子因子。

    Args:
        trace_id: 可选的全链路 trace_id
        market: 可选的市场过滤（'futures' / 'stock' / 'energy' / None=全部）
            - "energy": 混入加载通用期货种子（seeds/futures）+ 能化专属种子（seeds/energy）
        include_external: 是否加载外部种子（WQ101/Qlib158/GTJA191/基本面）

    Returns:
        FactorProgram 列表
    """
    seeds_dir = get_seeds_dir()
    result: list[FactorProgram] = []

    if market in (None, "stock"):
        stock_dir = seeds_dir / "stock"
        if include_external:
            result.extend(load_factors_from_dir(stock_dir, trace_id))
        else:
            builtin = stock_dir / "builtin.yaml"
            if builtin.exists():
                result.extend(load_factors_from_yaml(builtin, trace_id))

    if market == "energy":
        # 能源产业链专属市场（GAP-121）：混入加载 = 通用期货种子 + 能化专属种子
        energy_dir = seeds_dir / "energy"
        if energy_dir.exists():
            energy_factors = load_factors_from_dir(energy_dir, trace_id)
            logger.info(
                "[yaml_seed] 能化专属 YAML 种子加载完成: %d 个因子, trace_id=%s",
                len(energy_factors),
                trace_id,
            )
            result.extend(energy_factors)

    if market in (None, "futures", "energy"):
        futures_dir = seeds_dir / "futures"
        logger.info(
            "[yaml_seed] 开始加载期货 YAML 种子 (%d 个种子文件), trace_id=%s",
            len(_FUTURES_SEED_FILES),
            trace_id,
        )
        futures_files = list_yaml_files(futures_dir)
        futures_loaded: dict[str, int] = {}

        for yaml_file in futures_files:
            fp_list = load_factors_from_yaml(yaml_file, trace_id)
            result.extend(fp_list)

            # ── 按种子文件加载日志 ──
            fname = yaml_file.name
            count = len(fp_list)
            futures_loaded[fname] = count
            logger.info(
                "[yaml_seed] ★ 种子文件加载完成: %s (%d 个因子), trace_id=%s",
                fname,
                count,
                trace_id,
            )

        # ── 期货种子文件缺失校验 ──
        missing_files = sorted(_FUTURES_SEED_FILES - set(futures_loaded))
        if missing_files:
            logger.error(
                "[yaml_seed] ❌ 期货种子文件不完整: 已加载 %d 个, 缺少 %s, trace_id=%s",
                len(futures_loaded),
                missing_files,
                trace_id,
            )
        else:
            logger.info(
                "[yaml_seed] ✅ 全部 %d 个期货种子文件加载完成, 总计 %d 个因子, trace_id=%s",
                len(futures_loaded),
                sum(futures_loaded.values()),
                trace_id,
            )

    logger.info(f"Total YAML seeds loaded: {len(result)}")
    return result


def verify_yaml_integrity() -> dict[str, Any]:
    """验证所有 YAML 种子文件的完整性。

    Returns:
        验证报告字典
    """
    seeds_dir = get_seeds_dir()
    report: dict[str, Any] = {
        "total_files": 0,
        "total_factors": 0,
        "errors": [],
        "warnings": [],
        "files": [],
    }

    for subdir in ["stock", "futures", "energy"]:
        d = seeds_dir / subdir
        if not d.exists():
            continue
        for yaml_file in list_yaml_files(d):
            report["total_files"] += 1
            try:
                data = load_yaml_file(yaml_file)
                factors = data.get("factors", [])
                report["total_factors"] += len(factors)

                file_info = {"file": yaml_file.name, "factor_count": len(factors)}
                errors_in_file = []
                for i, defn in enumerate(factors):
                    if "name" not in defn:
                        errors_in_file.append(f"Factor[{i}]: missing 'name'")
                    try:
                        _detect_factor_type(defn)
                    except ValueError as e:
                        errors_in_file.append(f"Factor[{i}] ({defn.get('name', '?')}): {e}")

                if errors_in_file:
                    report["errors"].extend([f"{yaml_file.name}: {e}" for e in errors_in_file])
                    file_info["errors"] = errors_in_file

                report["files"].append(file_info)

            except Exception as e:
                report["errors"].append(f"{yaml_file.name}: parse error — {e}")

    report["valid"] = len(report["errors"]) == 0
    return report


__all__ = [
    "get_seeds_dir",
    "set_seeds_dir",
    "load_factors_from_yaml",
    "load_factors_from_dir",
    "load_all_yaml_seeds",
    "verify_yaml_integrity",
]
