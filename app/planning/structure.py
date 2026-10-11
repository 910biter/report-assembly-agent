"""Narrative structure contracts and evidence-backed writing objects."""
from __future__ import annotations

from typing import Any


class StructureError(ValueError):
    """Invalid chapter structure."""


def normalize_text_list(value: Any) -> list[str]:
    """Return prompt-safe text values without silently dropping legacy data."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_id_list(value: Any) -> list[int]:
    """Normalize ID collections while rejecting booleans and malformed values."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[int] = []
    for item in values:
        if isinstance(item, bool):
            continue
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed not in result:
            result.append(parsed)
    return result


_TEXT_LIST_FIELDS = (
    "questions",
    "required_facts",
    "required_inferences",
    "exclude",
    "evidence_requirements",
    "completion_criteria",
    "allowed_roles",
    "missing_information",
)
_ID_LIST_FIELDS = (
    "primary_fact_ids",
    "supporting_fact_ids",
    "fact_ids",
    "primary_inference_ids",
    "inference_ids",
)


def _normalize_subsections(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        for field in ("title", "purpose", "core_question", "core_message", "judgment"):
            if field in item and item[field] is not None:
                item[field] = str(item[field]).strip()
        for field in _TEXT_LIST_FIELDS:
            if field in item:
                item[field] = normalize_text_list(item[field])
        for field in _ID_LIST_FIELDS:
            if field in item:
                item[field] = normalize_id_list(item[field])
        result.append(item)
    return result


def order_chapters(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate chapter titles and keep the planner's narrative order.

    Chapter ordering is a narrative planning decision. The system no longer
    maintains a separate chapter graph because title-based cross references
    from LLM output are brittle and can block otherwise valid reports.
    """
    items = [c for c in chapters if isinstance(c, dict)]
    titles = [str(c.get("title", "")).strip() for c in items]
    if len(set(titles)) != len(titles) or any(not t for t in titles):
        raise StructureError("chapter titles must be non-empty and unique")
    return items


def normalize_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize final-plan chapters into one Narrative Contract schema."""
    result = dict(plan or {})
    for field in ("dimensions", "required_facts", "required_inferences"):
        if field in result:
            result[field] = normalize_text_list(result[field])
    chapters = result.get("chapter_plans") or result.get("chapters") or []
    normalized = []
    for raw in chapters:
        if not isinstance(raw, dict):
            continue
        c = dict(raw or {})
        for field in _TEXT_LIST_FIELDS:
            if field in c:
                c[field] = normalize_text_list(c[field])
        for field in _ID_LIST_FIELDS:
            if field in c:
                c[field] = normalize_id_list(c[field])
        c["title"] = str(c.get("title") or "").strip()
        c["core_question"] = str(c.get("core_question") or (c.get("questions") or [""])[0]).strip()
        c["core_message"] = str(c.get("core_message") or c.get("judgment") or "")
        c.pop("dependencies", None)
        c.pop("depends_on", None)
        c["evidence_requirements"] = normalize_text_list(
            c.get("evidence_requirements") or c.get("required_facts")
        )
        c["expected_content"] = str(c.get("expected_content") or c.get("judgment") or "")
        c["completion_criteria"] = normalize_text_list(c.get("completion_criteria"))
        c["discourse_plan"] = c.get("discourse_plan") or c.get("discourse_flow") or []
        c["subsections"] = _normalize_subsections(c.get("subsections"))
        normalized.append(c)
    result["chapter_plans"] = order_chapters(normalized)
    result["chapters"] = result["chapter_plans"]
    return result


def normalize_topic(topic: dict[str, Any], valid_facts: set[int], valid_inferences: set[int]) -> dict[str, Any]:
    """Keep the full Narrative Contract + Discourse Plan after model output."""
    item = dict(topic or {})
    item["core_question"] = str(item.get("core_question") or item.get("purpose") or "")
    item["core_message"] = str(item.get("core_message") or "")[:240]
    item["fact_ids"] = [int(x) for x in item.get("fact_ids", []) if str(x).isdigit() and int(x) in valid_facts]
    item["supporting_fact_ids"] = [int(x) for x in item.get("supporting_fact_ids", item["fact_ids"]) if str(x).isdigit() and int(x) in valid_facts]
    item["inference_ids"] = [int(x) for x in item.get("inference_ids", []) if str(x).isdigit() and int(x) in valid_inferences]
    flow = []
    for step in item.get("discourse_flow") or item.get("discourse_plan") or []:
        if not isinstance(step, dict):
            continue
        flow.append({
            "role": str(step.get("role") or "analysis"),
            "facts": [int(x) for x in step.get("facts", []) if str(x).isdigit() and int(x) in valid_facts],
            "inferences": [int(x) for x in step.get("inferences", []) if str(x).isdigit() and int(x) in valid_inferences],
        })
    item["discourse_flow"] = flow
    return item


def serializable_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """Bounded, JSON-safe memory for prompts and artifacts."""
    return {
        "core_judgment": memory.get("core_judgment", ""),
        "narrative_logic": memory.get("narrative_logic", ""),
        "unified_terms": list(memory.get("unified_terms", []))[-20:],
        "expressed_points": list(memory.get("expressed_points", []))[-12:],
        "formed_judgments": list(memory.get("formed_judgments", []))[-12:],
        "unresolved_issues": list(memory.get("unresolved_issues", []))[-12:],
        "fact_roles": dict(list((memory.get("fact_roles") or {}).items())[-30:]),
        "used_fact_ids": sorted(memory.get("used_fact_ids", set())),
        "used_inference_ids": sorted(memory.get("used_inference_ids", set())),
        "chapter_summaries": list(memory.get("chapter_summaries", []))[-8:],
    }


__all__ = [
    "StructureError",
    "order_chapters",
    "normalize_text_list",
    "normalize_id_list",
    "normalize_contract",
    "normalize_topic",
    "serializable_memory",
]
