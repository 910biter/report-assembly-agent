"""Domain-neutral report scale contracts.

User language defines the requested scale. Evidence may explain why the
request cannot safely be fulfilled, but must not silently rewrite it.
"""
from __future__ import annotations

import re
from copy import deepcopy

from app.planning.structure import normalize_contract, normalize_text_list


_NUMBER = r"\d+(?:\.\d+)?"


def parse_user_scale(requirements: str) -> dict:
    text = str(requirements or "").replace(",", "").replace("，", "")
    range_match = re.search(
        rf"({_NUMBER})\s*(万)?\s*(?:-|—|~|～|至|到)\s*({_NUMBER})\s*(万)?\s*字",
        text,
    )
    if range_match:
        left_unit = range_match.group(2)
        right_unit = range_match.group(4)
        if right_unit and not left_unit:
            left_unit = right_unit
        low = _word_value(range_match.group(1), left_unit)
        high = _word_value(range_match.group(3), right_unit)
        low, high = sorted((low, high))
        return {
            "kind": "range",
            "min_words": low,
            "max_words": high,
            "target_words": round((low + high) / 2),
            "source_text": range_match.group(0),
        }

    exact_match = re.search(rf"(?:约|大约|左右|正文约|正文)?\s*({_NUMBER})\s*(万)?\s*字(?:左右)?", text)
    if exact_match:
        target = _word_value(exact_match.group(1), exact_match.group(2))
        return {
            "kind": "target",
            "min_words": 0,
            "max_words": 0,
            "target_words": target,
            "source_text": exact_match.group(0),
        }
    return {}


def reconcile_scale_budget(
    user_requirements: str,
    preliminary: dict | None,
    final: dict | None,
) -> dict:
    """Keep one authoritative target while preserving evidence diagnostics."""
    preliminary = dict(preliminary or {})
    result = {**preliminary, **dict(final or {})}
    explicit = parse_user_scale(user_requirements)
    preliminary_target = _positive_int(preliminary.get("target_words"))
    final_target = _positive_int(result.get("target_words"))

    if explicit:
        if explicit["kind"] == "range":
            candidates = [preliminary_target, final_target]
            target = next(
                (value for value in candidates if explicit["min_words"] <= value <= explicit["max_words"]),
                explicit["target_words"],
            )
        else:
            target = explicit["target_words"]
        result["user_scale"] = explicit
        result["target_source"] = "user_explicit"
    else:
        target = preliminary_target or final_target
        result["target_source"] = "preliminary_plan" if preliminary_target else "final_plan"

    result["target_words"] = target
    result["requested_target_words"] = target
    evidence_max = _positive_int((final or {}).get("max_words"))
    if evidence_max:
        result["evidence_supported_max_words"] = evidence_max
    status = str(result.get("evidence_status") or "unknown").lower()
    result["evidence_status"] = status if status in {"sufficient", "limited", "insufficient"} else "unknown"
    if result["evidence_status"] == "sufficient" and target:
        result["max_words"] = max(target, evidence_max)
    if explicit.get("kind") == "range":
        result["min_words"] = explicit["min_words"]
        result["user_max_words"] = explicit["max_words"]
    return result


def normalize_chapter_budgets(chapters: list[dict], target_words: int) -> list[dict]:
    """Make chapter/subsection budgets execute one frozen report target."""
    items = [dict(item) for item in chapters if isinstance(item, dict)]
    if not items or target_words <= 0:
        return items
    chapter_targets = _allocate(items, target_words)
    normalized = []
    for chapter, target in zip(items, chapter_targets):
        chapter["target_words"] = target
        subsections = [dict(item) for item in chapter.get("subsections") or [] if isinstance(item, dict)]
        if len(subsections) >= 2:
            subsection_targets = _allocate(subsections, target)
            for subsection, subsection_target in zip(subsections, subsection_targets):
                subsection["target_words"] = subsection_target
            chapter["subsections"] = subsections
        normalized.append(chapter)
    return normalized


def normalize_execution_plan(plan: dict) -> dict:
    """Recover and apply one scale contract for current and historical plans."""
    result = normalize_contract(deepcopy(plan or {}))
    for field in ("dimensions", "required_facts"):
        if field in result:
            result[field] = normalize_text_list(result[field])
    chapters = result.get("chapter_plans") or [
        {"title": title} for title in (result.get("structure") or [])
    ]
    declared_total = sum(_positive_int(item.get("target_words")) for item in chapters)
    stored_budget = dict(result.get("budget") or {})
    if not _positive_int(stored_budget.get("target_words")) and declared_total:
        stored_budget["target_words"] = declared_total
        stored_budget["recovered_from"] = "chapter_budgets"
    budget = reconcile_scale_budget(
        str(result.get("user_requirements") or ""),
        stored_budget,
        {},
    )
    result["budget"] = budget
    result["chapter_plans"] = normalize_chapter_budgets(
        chapters, _positive_int(budget.get("target_words")),
    )
    result["chapters"] = result["chapter_plans"]
    result["structure"] = [
        str(item.get("title") or "") for item in result["chapter_plans"] if item.get("title")
    ]
    return result


def _allocate(items: list[dict], total: int) -> list[int]:
    declared = [_positive_int(item.get("target_words")) for item in items]
    weights = declared if sum(declared) else [1] * len(items)
    weight_sum = sum(weights)
    result = []
    used = 0
    for index, weight in enumerate(weights):
        value = total - used if index == len(weights) - 1 else round(total * weight / weight_sum)
        value = max(0, value)
        result.append(value)
        used += value
    return result


def _word_value(number: str, unit: str | None) -> int:
    value = float(number)
    return round(value * 10000) if unit else round(value)


def _positive_int(value) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0
