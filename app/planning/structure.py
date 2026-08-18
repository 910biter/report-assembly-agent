"""Narrative structure contracts: chapter DAG and evidence-backed writing objects."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


class StructureError(ValueError):
    """Invalid chapter dependency graph."""


def order_chapters(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate dependencies and return a stable topological order.

    Dependencies are chapter titles. Unknown dependencies are rejected rather
    than silently ignored; this prevents a plausible but incorrectly ordered
    report. Original order breaks ties for deterministic output.
    """
    items = [c for c in chapters if isinstance(c, dict)]
    titles = [str(c.get("title", "")).strip() for c in items]
    if len(set(titles)) != len(titles) or any(not t for t in titles):
        raise StructureError("chapter titles must be non-empty and unique")
    index = {title: i for i, title in enumerate(titles)}
    edges: dict[str, set[str]] = defaultdict(set)
    indegree = {title: 0 for title in titles}
    for chapter, title in zip(items, titles):
        deps = chapter.get("dependencies") or chapter.get("depends_on") or []
        if isinstance(deps, str):
            deps = [deps]
        for dep in deps:
            dep = str(dep).strip()
            if not dep:
                continue
            if dep not in index:
                raise StructureError(f"unknown chapter dependency: {title} -> {dep}")
            if dep == title:
                raise StructureError(f"chapter cannot depend on itself: {title}")
            if title not in edges[dep]:
                edges[dep].add(title)
                indegree[title] += 1
    ready = [title for title in titles if indegree[title] == 0]
    ready.sort(key=lambda title: index[title])
    result: list[str] = []
    while ready:
        title = ready.pop(0)
        result.append(title)
        for child in sorted(edges[title], key=index.get):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=lambda title: index[title])
    if len(result) != len(titles):
        raise StructureError("chapter dependency graph contains a cycle")
    by_title = {str(c["title"]): c for c in items}
    return [by_title[title] for title in result]


def normalize_contract(plan: dict[str, Any]) -> dict[str, Any]:
    """Normalize final-plan chapters into one Narrative Contract schema."""
    result = dict(plan or {})
    chapters = result.get("chapter_plans") or result.get("chapters") or []
    normalized = []
    for raw in chapters:
        c = dict(raw or {})
        c["title"] = str(c.get("title") or "").strip()
        c["core_question"] = str(c.get("core_question") or (c.get("questions") or [""])[0])
        c["core_message"] = str(c.get("core_message") or c.get("judgment") or "")
        c["dependencies"] = list(c.get("dependencies") or c.get("depends_on") or [])
        c["evidence_requirements"] = list(c.get("evidence_requirements") or c.get("required_facts") or [])
        c["expected_content"] = str(c.get("expected_content") or c.get("judgment") or "")
        c["completion_criteria"] = list(c.get("completion_criteria") or [])
        c["discourse_plan"] = c.get("discourse_plan") or c.get("discourse_flow") or []
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


def evidence_gap_contract(gaps: list[str], chapter: str = "") -> dict[str, Any]:
    return {"status": "open", "chapter": chapter, "gaps": [str(g) for g in gaps if str(g).strip()]}


def update_gap_contract(contract: dict[str, Any], status: str, note: str = "") -> dict[str, Any]:
    result = dict(contract or {})
    result["status"] = status
    if note:
        result["note"] = note
    return result


def evaluation_contract() -> dict[str, Any]:
    """Survey-aligned quality dimensions used by the report QA layer."""
    return {
        "attribution": {"status": "programmatic", "metric": "source binding + attribution weak"},
        "citation": {"status": "programmatic", "metric": "citation coverage + source validity"},
        "correctness": {"status": "programmatic_llm", "metric": "numeric consistency + QA"},
        "linguistic_quality": {"status": "llm_qa", "metric": "fluency + coherence"},
        "preservation": {"status": "planned", "metric": "revision preservation"},
        "relevance": {"status": "programmatic", "metric": "contract coverage"},
        "retrieval": {"status": "programmatic", "metric": "need coverage + utility"},
    }


__all__ = ["StructureError", "order_chapters", "normalize_contract", "normalize_topic", "serializable_memory", "evidence_gap_contract", "update_gap_contract", "evaluation_contract"]
