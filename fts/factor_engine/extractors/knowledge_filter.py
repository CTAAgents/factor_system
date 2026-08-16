"""
fts/factor_engine/extractors/knowledge_filter.py — plans/44 P0 粗筛层（embedding + 关键词降级）

三层管线之「筛选层」：
    - TextEmbedder: 轻量本地 embedding（sentence-transformers 中文多语模型），
                    模型缺失/导入失败自动降级关键词规则（如实标记 degraded）。
    - KnowledgeRelevanceFilter: 与 query 模板余弦相似度 ≥ 阈值 → 深读子集。
    - dedup_semantic: 语义去重（与既有种子/已注入候选 name+code 高相似拦截）。

零 LLM token；全部本地计算。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_QUERY_DEFAULT = "商品期货 量化因子 CTA 能化产业链 期限结构 库存 基差"
_FALLBACK_KEYWORDS = (
    "quant",
    "factor",
    "CTA",
    "futures",
    "commodity",
    "crude",
    "term structure",
    "carry",
    "momentum",
    "volatility",
    "inventory",
    "basis",
    "seasonal",
    "量化",
    "期货",
    "因子",
    "CTA",
    "能化",
    "化工",
    "原油",
    "库存",
    "基差",
    "期限结构",
    "聚酯",
    "甲醇",
    "尿素",
)


class TextEmbedder:
    """本地文本 embedding（惰性加载，模型缺失降级关键词）。"""

    def __init__(self, model_name: str = _DEFAULT_MODEL, enabled: bool = True):
        self.model_name = model_name
        self.enabled = enabled
        self._model: Any = None
        self._load_error: Optional[str] = None

    def _ensure_model(self) -> Any:
        if self._model is not None or self._load_error is not None:
            return self._model
        if not self.enabled:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            # 仅加载本地缓存模型（local_files_only）——在线下载会阻塞 L1 运行且
            # 网络不可用时无限挂起；模型缺失立即降级关键词粗筛（plans/44 风险表）。
            self._model = SentenceTransformer(self.model_name, local_files_only=True)
            logger.info("[embedder] 模型加载完成(本地缓存): %s", self.model_name)
        except Exception as e:  # noqa: BLE001
            self._load_error = str(e)
            logger.warning("[embedder] 本地模型不可用, 降级关键词粗筛: %s", e)
            self._model = None
        return self._model

    def embed(self, texts: list[str]) -> Optional[np.ndarray]:
        """批量 embed → (n, dim)；模型不可用返回 None（调用方走关键词降级）。"""
        model = self._ensure_model()
        if model is None:
            return None
        try:
            return model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[embedder] 编码失败, 降级关键词: %s", e)
            return None

    @property
    def degraded(self) -> bool:
        """是否处于关键词降级态。"""
        return self._model is None


class KnowledgeRelevanceFilter:
    """相关性粗筛：embedding 余弦相似度 ≥ 阈值 → 深读子集（降级为关键词计数）。"""

    def __init__(
        self,
        threshold: float = 0.30,
        query: str = _QUERY_DEFAULT,
        embedder: Optional[TextEmbedder] = None,
    ):
        self.threshold = threshold
        self.query = query
        self.embedder = embedder or TextEmbedder()
        self._query_vec: Optional[np.ndarray] = None

    def _query_vector(self) -> Optional[np.ndarray]:
        if self._query_vec is None:
            emb = self.embedder.embed([self.query])
            if emb is not None:
                self._query_vec = emb[0]
        return self._query_vec

    def _keyword_score(self, text: str) -> float:
        """关键词命中计数降级评分（0~1）。"""
        lowered = text.lower()
        hits = sum(1 for kw in _FALLBACK_KEYWORDS if kw.lower() in lowered)
        if hits == 0:
            return 0.0
        # 归一化：~6 个命中即达 1.0（饱和平滑）
        return min(1.0, hits / 6.0)

    def _cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
        return float(np.dot(a, b) / denom)

    def _filter_by_keyword(self, records: list[dict[str, Any]], texts: list[str]) -> list[dict[str, Any]]:
        """关键词命中计数降级粗筛（模型/编码不可用时）。"""
        scored: list[tuple[dict[str, Any], float]] = []
        for rec, text in zip(records, texts):
            score = self._keyword_score(text)
            if score >= self.threshold:
                scored.append((rec, score))
        for rec, score in scored:
            rec["relevance"] = round(score, 4)
            rec["filter_mode"] = "keyword"
        logger.info(
            "[knowledge_filter] 关键词降级粗筛: total=%d hits=%d (degraded=keyword)",
            len(records),
            len(scored),
        )
        return [rec for rec, _ in scored]

    def filter(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """返回命中子集（附 relevance 分），并标记降级模式。"""
        if not records:
            return []
        texts = [f"{r.get('title', '')} {r.get('abstract', '')}" for r in records]
        query_vec = self._query_vector()
        if query_vec is None:
            return self._filter_by_keyword(records, texts)
        embs = self.embedder.embed(texts)
        if embs is None:
            return self._filter_by_keyword(records, texts)
        hits: list[tuple[dict[str, Any], float]] = []
        for rec, vec in zip(records, embs):
            score = self._cosine(query_vec, vec)
            if score >= self.threshold:
                hits.append((rec, score))
        for rec, score in hits:
            rec["relevance"] = round(score, 4)
            rec["filter_mode"] = "embedding"
        logger.info(
            "[knowledge_filter] embedding 粗筛: total=%d hits=%d threshold=%.2f",
            len(records),
            len(hits),
            self.threshold,
        )
        return [rec for rec, _ in hits]


def dedup_semantic(
    candidate_texts: list[str],
    existing_texts: list[str],
    threshold: float = 0.90,
    embedder: Optional[TextEmbedder] = None,
) -> list[bool]:
    """语义去重：每个候选与既有文本最大余弦 ≥ 阈值 → 判重。

    Returns:
        list[bool] — 与 candidates 等长，True=允许（不重复）/ False=判重拦截。
    """
    if not candidate_texts:
        return []
    if not existing_texts:
        return [True] * len(candidate_texts)
    embedder = embedder or TextEmbedder()
    cand_embs = embedder.embed(candidate_texts)
    if cand_embs is None:
        # 降级：名称精确匹配（既有行为）
        return [c.lower() not in {e.lower() for e in existing_texts} for c in candidate_texts]
    existing_embs = embedder.embed(existing_texts)
    if existing_embs is None:
        return [True] * len(candidate_texts)
    allow: list[bool] = []
    for cvec in cand_embs:
        best = 0.0
        for evec in existing_embs:
            denom = (np.linalg.norm(cvec) * np.linalg.norm(evec)) or 1e-9
            best = max(best, float(np.dot(cvec, evec) / denom))
        allow.append(best < threshold)
    return allow
