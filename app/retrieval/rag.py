"""RAG retrieval policy: semantic + keyword + rerank + coverage check."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import Unit
from app.retrieval.embedder import embed_texts
from app.retrieval.store import vector_store


@dataclass
class RetrievalHit:
    unit_id: int
    score: float
    source: str
    coverage_terms: list[str] = field(default_factory=list)


def hybrid_retrieve_units(
    query: str,
    units_by_material: dict[int, list[Unit]],
    top_k: int = 8,
    keywords: list[str] | None = None,
    required_terms: list[str] | None = None,
    filters: dict | None = None,
) -> tuple[list[RetrievalHit], dict]:
    """Retrieve with coverage-aware expansion.

    The strategy is deterministic and model-free:
    1. semantic vector retrieval;
    2. keyword recall from in-memory units;
    3. rerank by semantic score + term coverage + material diversity;
    4. expand candidate count once when required term coverage is weak.
    """
    required = _terms(" ".join(required_terms or []))
    keyword_terms = _terms(" ".join(keywords or []))
    query_terms = _terms(query)
    target_terms = list(dict.fromkeys(required + keyword_terms + query_terms))

    candidate_limit = max(top_k * 3, top_k + 8)
    semantic = _semantic_hits(query, candidate_limit, filters)
    keyword = _keyword_hits(units_by_material, target_terms, candidate_limit)
    hits = _merge_hits(semantic + keyword)
    ranked = _rerank(hits, units_by_material, query, target_terms)
    selected = ranked[:top_k]
    coverage = _coverage(selected, units_by_material, required)
    expanded = False
    if required and coverage["coverage"] < 0.6 and len(ranked) > top_k:
        expanded = True
        selected = ranked[:min(len(ranked), top_k + 4)]
        coverage = _coverage(selected, units_by_material, required)
    meta = {
        "strategy": "semantic_keyword_rerank_coverage",
        "backend": vector_store.backend,
        "candidate_count": len(hits),
        "selected_count": len(selected),
        "required_terms": required[:30],
        "covered_terms": coverage["covered_terms"],
        "missing_terms": coverage["missing_terms"],
        "coverage": coverage["coverage"],
        "expanded": expanded,
    }
    return selected, meta


def _semantic_hits(query: str, limit: int, filters: dict | None) -> list[RetrievalHit]:
    try:
        query_vector = embed_texts([query], query=True)[0]
        return [
            RetrievalHit(unit_id=int(unit_id), score=float(score), source="semantic")
            for unit_id, score in vector_store.search_units(query_vector, top_k=limit, filters=filters)
        ]
    except Exception:
        return []


def _keyword_hits(units_by_material: dict[int, list[Unit]], terms: list[str], limit: int) -> list[RetrievalHit]:
    if not terms:
        return []
    scored: list[tuple[int, Unit, list[str]]] = []
    for units in units_by_material.values():
        for unit in units:
            content = unit.content or ""
            covered = [term for term in terms if term and term in content]
            if covered and unit.id is not None:
                scored.append((len(covered), unit, covered))
    scored.sort(key=lambda item: (item[0], len(item[1].content or "")), reverse=True)
    return [
        RetrievalHit(unit_id=int(unit.id or 0), score=min(1.0, score / max(len(terms), 1)), source="keyword",
                     coverage_terms=covered)
        for score, unit, covered in scored[:limit]
    ]


def _merge_hits(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    by_id: dict[int, RetrievalHit] = {}
    for hit in hits:
        current = by_id.get(hit.unit_id)
        if current is None:
            by_id[hit.unit_id] = hit
            continue
        current.score = max(current.score, hit.score)
        current.source = "hybrid" if current.source != hit.source else current.source
        current.coverage_terms = list(dict.fromkeys(current.coverage_terms + hit.coverage_terms))
    return list(by_id.values())


def _rerank(hits: list[RetrievalHit], units_by_material: dict[int, list[Unit]],
            query: str, terms: list[str]) -> list[RetrievalHit]:
    index = _unit_index(units_by_material)
    material_seen: dict[int, int] = {}
    scored = []
    for hit in hits:
        pair = index.get(hit.unit_id)
        if not pair:
            continue
        material_id, unit = pair
        text = unit.content or ""
        term_hits = [term for term in terms if term and term in text]
        lexical = _ngram_similarity(query, text)
        diversity = 1.0 / (1 + material_seen.get(material_id, 0))
        final = hit.score * 0.55 + min(1.0, len(term_hits) / max(len(terms), 1)) * 0.25 + lexical * 0.15 + diversity * 0.05
        hit.coverage_terms = list(dict.fromkeys(hit.coverage_terms + term_hits))
        scored.append((final, hit, material_id))
    scored.sort(key=lambda item: item[0], reverse=True)
    result = []
    for _score, hit, material_id in scored:
        result.append(hit)
        material_seen[material_id] = material_seen.get(material_id, 0) + 1
    return result


def _coverage(hits: list[RetrievalHit], units_by_material: dict[int, list[Unit]], required: list[str]) -> dict:
    if not required:
        return {"coverage": 1.0, "covered_terms": [], "missing_terms": []}
    index = _unit_index(units_by_material)
    text = "\n".join((index.get(hit.unit_id, (0, None))[1].content or "") for hit in hits if index.get(hit.unit_id))
    covered = [term for term in required if term in text]
    missing = [term for term in required if term not in covered]
    return {
        "coverage": round(len(covered) / max(len(required), 1), 2),
        "covered_terms": covered[:30],
        "missing_terms": missing[:30],
    }


def _unit_index(units_by_material: dict[int, list[Unit]]) -> dict[int, tuple[int, Unit]]:
    result = {}
    for material_id, units in units_by_material.items():
        for unit in units:
            if unit.id is not None:
                result[int(unit.id)] = (int(material_id), unit)
    return result


def _terms(text: str) -> list[str]:
    return [term for term in re.split(r"[、/，,；;()\s]+", text or "") if len(term) >= 2]


def _ngram_similarity(left: str, right: str, n: int = 2) -> float:
    a = re.sub(r"\s+", "", left or "")
    b = re.sub(r"\s+", "", right or "")
    if len(a) < n or len(b) < n:
        return 0.0
    grams_a = {a[i:i + n] for i in range(len(a) - n + 1)}
    grams_b = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / max(len(grams_a), 1)
