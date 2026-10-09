"""Preserve source-claim semantics and provenance at the Analysis boundary."""
from __future__ import annotations

from app.assertion_semantics import normalize_assertion_semantics, semantic_profile_line


def analysis_fact_line(fact: dict) -> str:
    """Format per-claim semantics; never substitute material-level labels."""
    fact_id = fact.get("id", "?")
    mode, mode_raw = normalize_assertion_semantics(
        fact.get("assertion_mode"), fact.get("assertion_mode_raw"),
    )
    profile = semantic_profile_line(
        fact.get("semantic_profile"), fact.get("assertion_mode"), fact.get("assertion_mode_raw"),
    )
    return (
        f"{fact_id}. [陈述性质={mode}（{mode_raw}）] [开放语义画像={profile}] "
        f"{str(fact.get('content') or '').strip()}"
    )


def _excerpt_around_quote(text: str, quote: str, limit: int = 640) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    quote = str(quote or "").strip()
    position = text.find(quote) if quote else -1
    start = max(0, position - limit // 3) if position >= 0 else 0
    end = min(len(text), start + limit)
    if end == len(text):
        start = max(0, end - limit)
    excerpt = text[start:end]
    return ("…" if start else "") + excerpt + ("…" if end < len(text) else "")


def attach_source_contexts(facts: list[dict], evidence_rows: list[dict],
                           unit_rows: list[dict]) -> list[dict]:
    """Attach evidence-unit excerpts and preceding structural headings per fact."""
    unit_by_id = {int(row["id"]): dict(row) for row in unit_rows if row.get("id") is not None}
    headings_by_material: dict[int, list[dict]] = {}
    for row in unit_rows:
        if str(row.get("kind") or "").lower() == "heading":
            headings_by_material.setdefault(int(row.get("material_id") or 0), []).append(dict(row))
    for headings in headings_by_material.values():
        headings.sort(key=lambda row: int(row.get("id") or 0))

    evidence_by_fact: dict[int, list[dict]] = {}
    for evidence in evidence_rows:
        try:
            fact_id = int(evidence.get("fact_id"))
        except (TypeError, ValueError):
            continue
        evidence_by_fact.setdefault(fact_id, []).append(dict(evidence))

    enriched = []
    for fact in facts:
        item = dict(fact)
        contexts = []
        for evidence in evidence_by_fact.get(int(item.get("id") or 0), []):
            material_id = int(evidence.get("material_id") or 0)
            try:
                unit_id = int(evidence.get("unit_id") or 0)
            except (TypeError, ValueError):
                unit_id = 0
            unit = unit_by_id.get(unit_id, {})
            headings = [
                str(row.get("content") or "").strip()
                for row in headings_by_material.get(material_id, [])
                if int(row.get("id") or 0) < unit_id and str(row.get("content") or "").strip()
            ][-3:]
            if str(unit.get("kind") or "").lower() == "heading" and unit.get("content"):
                headings.append(str(unit["content"]).strip())
            quote = str(evidence.get("quote") or "").strip()
            contexts.append({
                "source_file": str(evidence.get("source_file") or ""),
                "page": evidence.get("page"),
                "paragraph": evidence.get("paragraph"),
                "section_headings": headings,
                "quote": quote,
                "excerpt": _excerpt_around_quote(str(unit.get("content") or ""), quote),
            })
        item["source_contexts"] = contexts
        enriched.append(item)
    return enriched


def analysis_source_lines(facts: list[dict]) -> list[str]:
    """Build lower-priority source context lines without replacing fact records."""
    lines = []
    for fact in facts or []:
        fact_id = fact.get("id", "?")
        contexts = fact.get("source_contexts") or []
        if contexts:
            for context in contexts:
                location = str(context.get("source_file") or "未知来源")
                if context.get("page") is not None:
                    location += f" · 第{context['page']}页"
                if context.get("paragraph") is not None:
                    location += f" · 第{context['paragraph']}段"
                headings = " > ".join(str(v) for v in context.get("section_headings") or [] if str(v).strip())
                details = [f"Fact {fact_id} 原文上下文", location]
                if headings:
                    details.append(f"章节={headings}")
                if context.get("quote"):
                    details.append(f"原文引文={context['quote']}")
                if context.get("excerpt"):
                    details.append(f"来源单元={context['excerpt']}")
                lines.append("- " + " | ".join(details))
            continue
        quotes = fact.get("sources") or []
        if isinstance(quotes, str):
            quotes = [quotes]
        for quote in quotes:
            if str(quote or "").strip():
                lines.append(f"- Fact {fact_id} 原文引文={str(quote).strip()}")
    return lines
