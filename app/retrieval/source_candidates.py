"""Bounded SQL candidate retrieval for task-scoped source lookup."""
from __future__ import annotations

import re

from sqlalchemy import case, func, or_, select

from app.db import session_scope
from app.infrastructure.orm import ORMMaterial, ORMUnit


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_terms(query: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[、/，,；;()（）\s]+", query or "") if len(part.strip()) >= 2]
    terms: list[str] = []
    for part in parts:
        if len(part) > 12:
            compact = re.sub(r"\s+", "", part)
            terms.extend(compact[index:index + 3] for index in range(0, len(compact) - 2, 2))
        else:
            terms.append(part)
    return list(dict.fromkeys(terms))[:32]


def count_task_units(material_ids: list[int]) -> int:
    if not material_ids:
        return 0
    with session_scope() as session:
        return int(session.execute(
            select(func.count()).select_from(ORMUnit).where(
                ORMUnit.c.material_id.in_(material_ids), ORMUnit.c.content != ""
            )
        ).scalar_one())


def load_small_source_scope(material_ids: list[int], limit: int) -> list[dict] | None:
    """Use one read for a small scope; return None when bounded search is needed."""
    ids = list(dict.fromkeys(int(value) for value in material_ids if int(value) > 0))
    if not ids:
        return []
    with session_scope() as session:
        rows = session.execute(
            select(
                ORMUnit.c.id.label("unit_id"), ORMUnit.c.material_id, ORMUnit.c.kind,
                ORMUnit.c.page, ORMUnit.c.paragraph, ORMUnit.c.content,
                ORMMaterial.c.filename,
            )
            .join(ORMMaterial, ORMMaterial.c.id == ORMUnit.c.material_id)
            .where(ORMUnit.c.material_id.in_(ids), ORMUnit.c.content != "")
            .order_by(ORMUnit.c.id)
            .limit(max(1, int(limit)) + 1)
        ).mappings().all()
    return [dict(row) for row in rows] if len(rows) <= limit else None


def search_source_candidates(material_ids: list[int], query: str, limit: int = 96) -> tuple[list[dict], dict]:
    """Return a bounded, source-located lexical candidate set, never the full corpus."""
    ids = list(dict.fromkeys(int(value) for value in material_ids if int(value) > 0))
    terms = _search_terms(query)
    if not ids or not terms:
        return [], {"candidate_count": 0, "term_count": len(terms), "index": "unavailable"}

    patterns = [f"%{_escape_like(term)}%" for term in terms]
    predicates = [ORMUnit.c.content.ilike(pattern, escape="\\") for pattern in patterns]
    match_score = sum(
        (case((predicate, 1), else_=0) for predicate in predicates), start=0
    )
    candidate_limit = max(1, min(int(limit or 96), 256))
    hit_columns = [
        case((predicate, 1), else_=0).label(f"term_hit_{index}")
        for index, predicate in enumerate(predicates)
    ]
    statement = (
        select(
            ORMUnit.c.id.label("unit_id"),
            ORMUnit.c.material_id,
            ORMUnit.c.kind,
            ORMUnit.c.page,
            ORMUnit.c.paragraph,
            ORMUnit.c.content,
            ORMMaterial.c.filename,
            match_score.label("term_matches"),
            *hit_columns,
        )
        .join(ORMMaterial, ORMMaterial.c.id == ORMUnit.c.material_id)
        .where(ORMUnit.c.material_id.in_(ids), or_(*predicates))
        .order_by(match_score.desc(), func.length(ORMUnit.c.content).asc(), ORMUnit.c.id.asc())
        .limit(candidate_limit)
    )
    with session_scope() as session:
        rows = []
        for row in session.execute(statement).mappings().all():
            item = dict(row)
            item["matched_terms"] = [
                terms[index] for index in range(len(terms))
                if item.pop(f"term_hit_{index}", 0)
            ]
            rows.append(item)

    from app import db
    metadata = {
        "candidate_count": len(rows),
        "candidate_limit": candidate_limit,
        "term_count": len(terms),
        "index": "postgres_trigram" if db._UNIT_TRGM_INDEX_READY else "database_like_fallback",
        "bounded": True,
    }
    return rows, metadata


def load_source_candidates(material_ids: list[int], unit_ids: list[int]) -> list[dict]:
    """Hydrate semantic hits by ID, retaining material and document locators."""
    ids = list(dict.fromkeys(int(value) for value in unit_ids if int(value) > 0))
    materials = list(dict.fromkeys(int(value) for value in material_ids if int(value) > 0))
    if not ids or not materials:
        return []
    with session_scope() as session:
        rows = session.execute(
            select(
                ORMUnit.c.id.label("unit_id"), ORMUnit.c.material_id, ORMUnit.c.kind,
                ORMUnit.c.page, ORMUnit.c.paragraph, ORMUnit.c.content,
                ORMMaterial.c.filename,
            )
            .join(ORMMaterial, ORMMaterial.c.id == ORMUnit.c.material_id)
            .where(ORMUnit.c.id.in_(ids), ORMUnit.c.material_id.in_(materials))
        ).mappings().all()
    return [dict(row) for row in rows]
