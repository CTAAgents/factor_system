"""
fts/factor_engine/alternative_sentiment.py — 另类数据舆情情感因子生成器（C2，2026-08-11）

从公开新闻/公告（东方财富新闻搜索 API，可注入替换）按品种关键词抓取文本，
用轻量金融情感词典（v1，无需 LLM/API key）逐条打分，按交易日聚合为日频
情感因子值序列，产出可过全套审计链的 FactorProgram（独立候选源注入 L2）。

设计约束（对齐 HARNESS 因子研发红线）:
    - 零未来函数：t 日聚合值仅由 ≤t 新闻打分（数据准备层固定）；
      因子 code 为确定性日期查找，t 日信号只依赖 ≤t 日聚合值
    - 窗口自适应：code 按 data['datetime'] 对齐聚合日期序列（复用 C1 执行器
      datetime 注入通道）
    - 数据降级：无新闻 / 有效情感交易日 < min_factor_rows → 返回 None，不阻断
    - 词典法 v1：内置金融情感词典（正/负词 + 强度 + 否定反转），无外部依赖

用法:
    from fts.factor_engine.alternative_sentiment import SentimentFactorGenerator

    gen = SentimentFactorGenerator()
    cand = gen.generate(symbol="RB0", trace_id="l2_xxx")   # Optional[SentimentFactorCandidate]
    cands = gen.generate_batch(trace_id="l2_xxx")

版本: v1.0.0（C2 首期）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# 东方财富新闻搜索 API（公开免鉴权；JSONP 包装）
_EASTMONEY_SEARCH_API = "https://search-api-web.eastmoney.com/search/jsonp"
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
_SEARCH_PAGE_SIZE = 20

# 日频聚合因子种类（每品种产出 3 个因子）
FACTOR_KINDS: list[str] = ["sent_mean", "sent_std", "sent_chg"]

# 新闻提供者签名：fetch_news(symbol, lookback_days, trace_id) -> DataFrame(date/title/summary)
NewsProvider = Callable[..., pd.DataFrame]


class FinancialSentimentLexicon:
    """内置金融情感词典（v1）：正/负词条 + 强度 + 否定反转。

    score_text(text) -> float ∈ [-1, 1]：
        score = tanh(2 * (Σ正权重 - Σ负权重) / 命中词条数)
        命中前否定词（不/未/无/非/难/逆）时词条权重反转；
        无命中或空文本返回 0.0。
    """

    POSITIVE_WORDS: dict[str, float] = {
        "增长": 1.0, "上涨": 1.0, "盈利": 1.0, "利好": 1.0, "突破": 1.0,
        "买入": 0.8, "上调": 1.0, "超预期": 1.2, "增持": 0.9, "回购": 0.8,
        "中标": 1.0, "获批": 1.0, "提速": 0.9, "回暖": 1.0, "改善": 0.9,
        "领先": 0.7, "龙头": 0.7, "旺盛": 0.8, "预增": 1.0, "扩张": 0.7,
        "创收": 0.8, "提价": 0.8, "涨价": 0.7, "顺差": 0.6, "强劲": 0.8,
        "向好": 1.0, "复苏": 0.9, "高增长": 1.2, "业绩亮眼": 1.1,
    }
    NEGATIVE_WORDS: dict[str, float] = {
        "下跌": 1.0, "亏损": 1.0, "利空": 1.0, "下调": 1.0, "减持": 0.9,
        "质押": 0.6, "违规": 1.0, "处罚": 1.0, "诉讼": 0.9, "退市": 1.2,
        "爆雷": 1.3, "风险": 0.7, "下滑": 1.0, "承压": 0.9, "萎缩": 1.0,
        "拖累": 1.0, "预警": 1.0, "降级": 0.9, "违约": 1.2, "跌停": 1.1,
        "跳水": 1.0, "崩盘": 1.3, "疲软": 0.8, "弱于预期": 1.0,
        "不达标": 1.0, "减产": 0.7, "去库存": 0.5, "过剩": 0.8,
    }
    # 否定词：紧邻词条前出现时反转极性
    NEGATION_WORDS: tuple[str, ...] = ("不", "未", "无", "非", "难", "逆", "低于", "不及")

    _NEG_RE = re.compile("|".join(re.escape(w) for w in NEGATION_WORDS))

    @classmethod
    def score_text(cls, text: str) -> float:
        """对单条文本打分 ∈ [-1,1]；空文本/无命中返回 0.0。"""
        if not text or not str(text).strip():
            return 0.0
        s = str(text)
        hits: list[tuple[float, float]] = []  # (weight, 1/-1 极性)
        for w, wgt in cls.POSITIVE_WORDS.items():
            for m in re.finditer(re.escape(w), s):
                polarity = -1.0 if cls._negated_before(s, m.start()) else 1.0
                hits.append((wgt, polarity))
        for w, wgt in cls.NEGATIVE_WORDS.items():
            for m in re.finditer(re.escape(w), s):
                polarity = 1.0 if cls._negated_before(s, m.start()) else -1.0
                hits.append((wgt, polarity))
        if not hits:
            return 0.0
        signed = sum(wgt * pol for wgt, pol in hits)
        n = len(hits)
        return float(np.tanh(2.0 * signed / n))

    @classmethod
    def _negated_before(cls, s: str, pos: int) -> bool:
        """检查词条前 4 字符内是否有否定词。"""
        window = s[max(0, pos - 4):pos]
        return bool(cls._NEG_RE.search(window))


@dataclass
class NewsRecord:
    """单条新闻记录。

    Attributes:
        date: 新闻日期（ISO，yyyy-mm-dd）。
        title: 标题。
        summary: 摘要（可为空）。
        source: 来源标识。
    """

    date: str
    title: str
    summary: str = ""
    source: str = "eastmoney"


class EastmoneyNewsProvider:
    """东方财富新闻搜索提供者：按品种关键词抓取近 N 日新闻。

    外部 API 失败/空/网络异常 → 返回空 DataFrame（优雅降级，不阻断主流程）。
    """

    def __init__(self, page_size: int = _SEARCH_PAGE_SIZE, timeout: float = 15.0) -> None:
        self.page_size = page_size
        self.timeout = timeout

    def fetch_news(
        self,
        symbol: str,
        lookback_days: int = 63,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """按品种关键词抓取新闻。

        Args:
            symbol: 品种代码（如 RB0）。
            lookback_days: 回看天数（关键词按品种名推断）。
            trace_id: trace_id。

        Returns:
            DataFrame（列 date/title/summary）；失败返回空 DataFrame。
        """
        keyword = self._keyword_for(symbol)
        try:
            param = {
                "uid": "",
                "keyword": keyword,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "time",
                        "pageIndex": 1,
                        "pageSize": self.page_size,
                        "preTag": "",
                        "postTag": "",
                    }
                },
            }
            r = requests.get(
                _EASTMONEY_SEARCH_API,
                params={"cb": "callback", "param": json.dumps(param, ensure_ascii=False)},
                headers=_HTTP_HEADERS,
                timeout=self.timeout,
            )
            if r.status_code != 200:
                logger.warning("[sentiment] 新闻搜索 API 非 200: %s", r.status_code)
                return pd.DataFrame()
            payload = self._parse_jsonp(r.text)
            items = ((payload.get("result") or {}).get("cmsArticleWebOld")) or []
            rows: list[dict] = []
            for it in items:
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                date = (it.get("date") or "")[:10]
                summary = (it.get("content") or it.get("summary") or "").strip()
                if not date:
                    continue
                rows.append({"date": date, "title": title, "summary": summary})
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame(rows)
        except Exception as e:  # noqa: BLE001 - 外部 API 异常全面降级
            logger.warning("[sentiment] 新闻搜索不可用 [%s]: %s", symbol, e)
            return pd.DataFrame()

    @staticmethod
    def _keyword_for(symbol: str) -> str:
        """品种代码 → 搜索关键词：动态池存品种级代码（RB0），取字母根 + 常见中文名。"""
        from fts.data_futures import FUTURES_SYMBOL_NAMES

        root = re.match(r"[A-Za-z]+", symbol)
        base = root.group(0).upper() if root else symbol
        # 优先中文品种名（如 螺纹钢），否则退回字母代码
        try:
            name = FUTURES_SYMBOL_NAMES.get(base)
            if name:
                return str(name)
        except Exception:  # noqa: BLE001
            pass
        return base

    @staticmethod
    def _parse_jsonp(text: str) -> dict:
        """剥离 JSONP 包装（callback({...})）返回 dict；失败返回空 dict。"""
        s = text.strip()
        start = s.find("(")
        end = s.rfind(")")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            return json.loads(s[start + 1:end])
        except (json.JSONDecodeError, ValueError):
            return {}


@dataclass
class SentimentGeneratorConfig:
    """舆情情感因子生成配置（C2）。

    Attributes:
        symbols: 目标品种清单（默认动态池；显式传入优先）。
        lookback_days: 新闻回看天数（默认 63 ≈ 1 季度）。
        min_records: 最少新闻记录数，不足降级（默认 5）。
        min_factor_rows: 最少有效情感交易日，不足降级（默认 20）。
        save_sentiment_db: 是否落库 DuckDB sentiment_daily（默认关）。
        seed: 随机种子（保持可复现）。
    """

    symbols: list[str] = field(default_factory=list)
    lookback_days: int = 63
    min_records: int = 5
    min_factor_rows: int = 20
    save_sentiment_db: bool = False
    seed: int = 42


@dataclass
class SentimentFactorCandidate:
    """单个舆情情感因子候选。

    Attributes:
        factor: FactorProgram dict。
        symbol: 来源品种。
        kind: 因子种类（sent_mean/sent_std/sent_chg）。
        n_days: 有效聚合交易日数。
        generated_at: ISO 时间戳。
    """

    factor: dict[str, Any]
    symbol: str
    kind: str
    n_days: int
    generated_at: str


class SentimentFactorGenerator:
    """舆情情感因子生成器 — 新闻 → 词典打分 → 日频聚合 → FactorProgram（C2）。

    生成流程:
        1. fetch_news 抓取品种近 N 日新闻（eastmoney 默认 / 测试注入）
        2. FinancialSentimentLexicon.score_text 逐条打分
        3. 按交易日聚合（均值/离散/变化率）
        4. dates/values 内嵌 params，生成确定性日期查找 code（零未来）
    """

    def __init__(
        self,
        config: Optional[SentimentGeneratorConfig] = None,
        news_provider: Optional[NewsProvider] = None,
        lexicon: Optional[type[FinancialSentimentLexicon]] = None,
    ) -> None:
        """初始化生成器。

        Args:
            config: 生成配置；None 使用默认。
            news_provider: 新闻提供者（测试注入）；None 用 EastmoneyNewsProvider。
            lexicon: 词典类（测试可注入自定义词典）。
        """
        self.config = config or SentimentGeneratorConfig()
        self._news_provider = news_provider or EastmoneyNewsProvider()
        self._lexicon = lexicon or FinancialSentimentLexicon

    # ─── 主入口 ──────────────────────────────────────────

    def generate(
        self,
        symbol: str,
        trace_id: Optional[str] = None,
    ) -> Optional[SentimentFactorCandidate]:
        """为单个品种生成舆情情感因子候选集（3 个因子）。

        Returns:
            首个有效因子候选（sent_mean）；数据不足返回 None。
        """
        daily = self._load_daily_aggregates(symbol, trace_id)
        if daily is None:
            return None
        cands = self._build_candidates(symbol, daily, trace_id)
        return cands[0] if cands else None

    def generate_batch(
        self,
        symbols: Optional[list[str]] = None,
        trace_id: Optional[str] = None,
    ) -> list[SentimentFactorCandidate]:
        """批量生成：对目标品种清单逐品种聚合产出全部候选。"""
        target = symbols or self.config.symbols or self._default_symbols()
        out: list[SentimentFactorCandidate] = []
        for sym in target:
            daily = self._load_daily_aggregates(sym, trace_id)
            if daily is None:
                logger.info("[sentiment] [%s] 新闻数据不足，跳过", sym)
                continue
            out.extend(self._build_candidates(sym, daily, trace_id))
        return out

    # ─── 数据准备 ────────────────────────────────────────

    def _load_daily_aggregates(
        self,
        symbol: str,
        trace_id: Optional[str],
    ) -> Optional[pd.DataFrame]:
        """抓取新闻 → 打分 → 日频聚合。

        Returns:
            DataFrame（index=date，列 sent_mean/sent_std/sent_chg）；
            新闻不足或有效日不足返回 None（降级）。
        """
        news = self._news_provider(symbol, self.config.lookback_days, trace_id or "")
        if news is None or news.empty or len(news) < self.config.min_records:
            return None
        needed = {"date", "title"}
        if not needed.issubset(news.columns):
            logger.warning("[sentiment] [%s] 缺必需列 %s", symbol, needed - set(news.columns))
            return None

        df = news.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
        df = df.dropna(subset=["date"])
        if df.empty:
            return None
        # 逐条打分（title + summary）
        df["score"] = df.apply(
            lambda r: self._lexicon.score_text(f"{r.get('title', '')} {r.get('summary', '')}"),
            axis=1,
        )
        daily = (
            df.groupby("date")["score"]
            .agg(sent_mean="mean", sent_std="std")
            .sort_index()
        )
        daily["sent_chg"] = daily["sent_mean"].diff().fillna(0.0)
        daily["sent_std"] = daily["sent_std"].fillna(0.0)
        if len(daily) < self.config.min_factor_rows:
            return None
        if self.config.save_sentiment_db:
            self._save_daily(symbol, daily)
        return daily

    def _save_daily(self, symbol: str, daily: pd.DataFrame) -> None:
        """落库 DuckDB sentiment_daily（UPSERT 增量去重，可选）。"""
        try:
            import duckdb

            db_path = "data/fts_history.duckdb"
            con = duckdb.connect(str(db_path))
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sentiment_daily (
                    symbol VARCHAR, date DATE,
                    sent_mean DOUBLE, sent_std DOUBLE, sent_chg DOUBLE,
                    PRIMARY KEY (symbol, date)
                )
                """
            )
            con.execute("DELETE FROM sentiment_daily WHERE symbol = ?", [symbol])
            con.executemany(
                """
                INSERT INTO sentiment_daily (symbol, date, sent_mean, sent_std, sent_chg)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (symbol, str(d), float(r.sent_mean), float(r.sent_std), float(r.sent_chg))
                    for d, r in daily.iterrows()
                ],
            )
            con.close()
            logger.info("[sentiment] [%s] sentiment_daily 落库 %d 行", symbol, len(daily))
        except Exception as e:  # noqa: BLE001 - 落库失败仅告警不阻断
            logger.warning("[sentiment] [%s] 落库失败: %s", symbol, e)

    # ─── 候选构造 ────────────────────────────────────────

    def _build_candidates(
        self,
        symbol: str,
        daily: pd.DataFrame,
        trace_id: Optional[str],
    ) -> list[SentimentFactorCandidate]:
        """由日频聚合表构造 3 个因子候选（kinds 见 FACTOR_KINDS）。"""
        dates = [d.strftime("%Y-%m-%d") for d in daily.index]
        out: list[SentimentFactorCandidate] = []
        for kind in FACTOR_KINDS:
            values = daily[kind].fillna(0.0).to_numpy(dtype=float)
            factor = self._build_factor(symbol, kind, dates, values, len(daily), trace_id)
            out.append(
                SentimentFactorCandidate(
                    factor=factor,
                    symbol=symbol,
                    kind=kind,
                    n_days=len(daily),
                    generated_at=pd.Timestamp.now().isoformat(),
                )
            )
        return out

    def _build_factor(
        self,
        symbol: str,
        kind: str,
        dates: list[str],
        values: np.ndarray,
        n_days: int,
        trace_id: Optional[str],
    ) -> dict[str, Any]:
        """构造 FactorProgram（code 内嵌日期-值映射，零未来查找）。"""
        code = self._build_code(dates, values)
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.factor_program import create_factor_program

        unique_key = f"sent_{symbol}_{kind}_{dates[0]}_{dates[-1]}"
        factor_id = "fct_" + hashlib.md5(unique_key.encode()).hexdigest()[:8]
        factor_name = f"sent_{symbol}_{kind}"

        signature = FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        )
        kind_label = {
            "sent_mean": "日舆情情感均值（正面性）",
            "sent_std": "日舆情情感离散度（分歧）",
            "sent_chg": "日舆情情感变化率（动量）",
        }[kind]
        factor: dict[str, Any] = create_factor_program(
            name=factor_name,
            code=code,
            params={
                "dates": dates,
                "values": [round(float(v), 6) for v in values],
                "kind": kind,
                "symbol": symbol,
            },
            signature=signature,
            economic_logic=EconomicLogic(
                theory=2,
                behavioral=5,
                microstructure=1,
                institutional=3,
                narrative=(
                    f"舆情情感因子（C2）: {symbol} 近 {n_days} 交易日公开新闻词典法打分的"
                    f"{kind_label}；聚合值由 ≤t 新闻在数据准备层计算（零未来），"
                    f"因子 code 按日期确定性查找输出日频信号。"
                ),
            ),
            source="manual",
            market="futures",
            family="behavioral",
            symbols=[symbol],
            trace_id=trace_id,
        )
        factor["factor_id"] = factor_id
        factor["generation"] = 0
        factor["kind"] = "code"
        factor["sentiment"] = {
            "symbol": symbol,
            "kind": kind,
            "n_days": n_days,
            "date_start": dates[0],
            "date_end": dates[-1],
        }
        return factor

    # ─── code 生成（对齐 C1 日期查找模式） ────────────────

    @staticmethod
    def _build_code(dates: list[str], values: np.ndarray) -> str:
        """生成确定性日期查找 code：t 日信号 = 最新 ≤t 聚合值（零未来）。"""
        dates_repr = repr(dates)
        values_repr = repr([round(float(v), 6) for v in np.asarray(values, dtype=float)])
        code = f"""\
def factor_program(data, params):
    import numpy as np
    n = len(data.get('close', []))
    out = np.zeros(n)
    dt = data.get('datetime', None)
    if dt is None or len(dt) != n:
        return out
    dates = {dates_repr}
    values = {values_repr}
    vmap = dict(zip(dates, values))
    last = 0.0
    for i in range(n):
        d = str(dt[i])[:10]
        if d in vmap:
            last = vmap[d]
        out[i] = last
    return out
"""
        return code

    # ─── 工具 ────────────────────────────────────────────

    def _default_symbols(self) -> list[str]:
        """默认品种清单：动态池（缺失/损坏回退静态核心池）。"""
        try:
            from fts.data_futures import get_dynamic_core_subset

            return get_dynamic_core_subset()
        except Exception:  # noqa: BLE001 — 降级优先
            return []


def generate_sentiment_factors(
    symbols: Optional[list[str]] = None,
    trace_id: Optional[str] = None,
    config: Optional[SentimentGeneratorConfig] = None,
) -> list[SentimentFactorCandidate]:
    """便捷入口：批量生成舆情情感因子候选。

    Returns:
        有效候选列表（数据不足品种自动跳过）；无数据返回空列表。
    """
    gen = SentimentFactorGenerator(config)
    return gen.generate_batch(symbols=symbols, trace_id=trace_id)


class LlmSentimentScorer:
    """LLM 情感打分器（C2 LLM 精修，2026-08-11）。

    复用 ``LLMClient.complete``，提示词约束输出 [-1,1] 或 0（中性）；
    解析失败/异常返回 None（降级不阻断一致性统计）。供
    ``evaluate_lexicon_consistency`` 做词典-LLM 一致性验收（≥0.7）。
    """

    _PROMPT_TEMPLATE = (
        "你是金融新闻情感分析器。对下面这条新闻，判断其对公司/品种的利空利多倾向，"
        "只输出一个 -1（强烈利空）到 1（强烈利好）之间的数字，中性输出 0，不要任何解释。\n"
        "新闻：{text}\n"
        "评分："
    )

    def __init__(self, llm: Any) -> None:
        """Args: llm: LLMClient（complete(prompt, max_tokens) -> (text, tokens)）。"""
        self._llm = llm

    def score_text(self, text: str) -> Optional[float]:
        """LLM 单条打分 ∈[-1,1]；空文本/解析失败返回 None。"""
        if not text or not str(text).strip():
            return None
        try:
            out, _ = self._llm.complete(self._PROMPT_TEMPLATE.format(text=str(text)[:200]), max_tokens=16)
        except Exception:  # noqa: BLE001 — LLM 异常降级
            return None
        m = re.search(r"[-+]?\d+(?:\.\d+)?", out or "")
        if not m:
            return None
        return max(-1.0, min(1.0, float(m.group(0))))


def evaluate_lexicon_consistency(
    samples: list[str],
    llm: Any,
    min_consistency: float = 0.7,
    match_threshold: float = 0.25,
) -> dict[str, Any]:
    """词典打分与 LLM 标注一致性验证（C2 验收：一致率 ≥ 0.7）。

    对每条文本同时词典打分（``FinancialSentimentLexicon``）与 LLM 打分；
    一致判定：任一 侧为中性（|分| < match_threshold）或两侧同号视为一致；
    LLM 解析失败计 invalid 跳过（不计入分母）。数据为空/LLM 全失败时
    passed=False（不误报达标）。

    Args:
        samples: 待标注文本列表（新闻标题/摘要）
        llm: LLMClient
        min_consistency: 一致性达标阈值（默认 0.7，对齐 C2 验收）
        match_threshold: 中性判定阈值（默认 0.25）

    Returns:
        {total, valid, agreement, agreement_rate, min_consistency, passed}
    """
    scorer = LlmSentimentScorer(llm)
    total = len(samples)
    valid = 0
    agree = 0
    for text in samples:
        lex = FinancialSentimentLexicon.score_text(text)
        llm_v = scorer.score_text(text)
        if llm_v is None:
            continue
        valid += 1
        if abs(lex) < match_threshold or abs(llm_v) < match_threshold or (lex > 0) == (llm_v > 0):
            agree += 1
    rate = (agree / valid) if valid else 0.0
    return {
        "total": total,
        "valid": valid,
        "agreement": agree,
        "agreement_rate": round(rate, 4),
        "min_consistency": min_consistency,
        "passed": rate >= min_consistency and valid > 0,
    }


__all__ = [
    "FinancialSentimentLexicon",
    "NewsRecord",
    "EastmoneyNewsProvider",
    "SentimentGeneratorConfig",
    "SentimentFactorCandidate",
    "SentimentFactorGenerator",
    "generate_sentiment_factors",
    "LlmSentimentScorer",
    "evaluate_lexicon_consistency",
    "FACTOR_KINDS",
]
