"""Utility-aware Rerank:相关之外,再判断"有没有用"(方案第 7 节 / FER)。

粗召回(candidate)按 utility 重排:语义相关 + 对 Need 的直接支持度
+ 是否提供新信息 + 是否补覆盖缺口 + 是否独立来源。
轻量实现:不逐条调 LLM(embedding + 词面 + 信息增益),模型语义判断
仍由 Evidence 提取阶段承担。
"""
from __future__ import annotations

import re

from app.context import _ngram_similarity


def utility_score(unit_text: str, need_text: str, query_vector=None,
                  unit_vector=None, known_contents: set[str] | None = None,
                  source: str = "", source_freq: dict | None = None,
                  known_ngrams: set[str] | None = None) -> float:
    """Unit 对当前 Evidence Need 的 utility 分数(0~1)。

    组成(FER 修正:效用主导,相关是候选入口非主导):
    - 语义/词面相关(0~0.3):与 need 的相关度
    - 直接支持(0~0.3):need 关键词在原文中出现的密度(聚焦片段)
    - 信息增益(0~0.2):与已提取事实的重叠度(低重叠 = 新信息,加分)
    - 独立来源(0~0.2):来源稀有度(候选来自越少见的来源 = 跨源多样性,加分)
    """
    relevance = 0.0
    if query_vector is not None and unit_vector is not None and len(query_vector) == len(unit_vector):
        try:
            from app.retrieval.store import cosine
            relevance = max(0.0, cosine(query_vector, unit_vector)) * 0.3
        except Exception:
            relevance = 0.0
    lexical = _ngram_similarity(need_text, unit_text)
    relevance = max(relevance, lexical * 0.3)

    support = 0.0
    terms = [t for t in _need_terms(need_text) if t and t in unit_text]
    if terms:
        support = min(0.3, 0.1 * len(terms))

    gain = 0.0
    if known_contents:
        # Compare against one prebuilt corpus index rather than every Fact.
        # This keeps novelty scoring linear as the task knowledge base grows.
        unit_ngrams = _char_ngrams(unit_text)
        corpus_ngrams = known_ngrams if known_ngrams is not None else _known_corpus_ngrams(known_contents)
        overlap = (
            len(unit_ngrams & corpus_ngrams) / max(len(unit_ngrams), 1)
            if unit_ngrams else 0.0
        )
        gain = (1.0 - overlap) * 0.2
    else:
        gain = 0.2  # 无已知事实:默认视为新信息

    source_score = 0.0
    if source and source_freq:
        # 来源稀有度:候选总数 ÷ 该来源候选数(来源唯一 = 1.0,来源重复 = 低分)
        total = max(1, sum(source_freq.values()))
        count = source_freq.get(source, 0)
        source_score = (1.0 - (count / total)) * 0.2

    return min(1.0, relevance + support + gain + source_score)


def _need_terms(need_text: str) -> list[str]:
    """Need 的关键词(切分后按长度过滤;无固定阈值,仅去除过短噪音)。"""
    return [t for t in need_text.replace("、", " ").replace("，", " ").replace(",", " ").split()
            if len(t) >= 2]


def rerank(candidates: list[tuple], need_text: str, query_vector=None,
           unit_vectors: dict | None = None, known_contents: set[str] | None = None,
           limit: int | None = None) -> list[tuple]:
    """按 utility 降序重排候选(含独立来源维度)。

    candidates: [(score, unit)] 或 [(unit_id, score, unit)] 的粗召回结果。
    """
    # 来源频率统计(独立来源分):unit.material_id 为来源标识
    source_freq: dict = {}
    for item in candidates:
        unit = item[1] if len(item) == 2 else item[2]
        src = str(getattr(unit, "material_id", "") or "")
        if src:
            source_freq[src] = source_freq.get(src, 0) + 1
    known_ngrams = _known_corpus_ngrams(known_contents)
    scored = []
    for item in candidates:
        if len(item) == 3:
            unit_id, _score, unit = item
        else:
            _score, unit = item
            unit_id = getattr(unit, "id", 0)
        unit_vec = (unit_vectors or {}).get(unit_id)
        src = str(getattr(unit, "material_id", "") or "")
        u = utility_score(unit.content or "", need_text, query_vector, unit_vec,
                          known_contents, source=src, source_freq=source_freq,
                          known_ngrams=known_ngrams)
        scored.append((u, unit))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored if limit is None else scored[:limit]


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    normalized = re.sub(r"\s+", "", text or "")
    if len(normalized) < n:
        return {normalized} if normalized else set()
    return {normalized[index:index + n] for index in range(len(normalized) - n + 1)}


def _known_corpus_ngrams(known_contents: set[str] | None) -> set[str]:
    grams: set[str] = set()
    for content in known_contents or set():
        grams.update(_char_ngrams(content))
    return grams
