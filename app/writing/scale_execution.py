"""Generic chapter-budget assessment helpers without domain vocabulary."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping


def measure_text_words(text: str) -> int:
    """Approximate Chinese report length using Chinese chars plus word tokens."""
    value = text or ""
    return len(re.findall(r"[\u4e00-\u9fff]", value)) + len(re.findall(r"[A-Za-z0-9]+", value))


def measure_report_body_words_by_section(rows: Iterable[Mapping[str, object]]) -> dict[str, int]:
    """Measure report prose by section, excluding rendered subsection headings."""
    parts: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if str(row.get("source_level") or "").upper() == "SUBHEADING":
            continue
        section = str(row.get("section") or "")
        text = str(row.get("user_edit") or row.get("content") or "")
        parts[section].append(text)
    return {section: measure_text_words("".join(texts)) for section, texts in parts.items()}


def report_minimum_words(target_words: int, budget: dict | None, default_ratio: float) -> int:
    """Use a declared lower bound when present; otherwise retain the QA floor."""
    budget = dict(budget or {})
    bounds = dict(budget.get("output_bounds") or {})
    target = max(0, int(target_words or 0))
    declared_minimum = max(
        0,
        int(budget.get("min_words") or 0),
        int(bounds.get("min_words") or 0),
    )
    fallback_minimum = round(target * max(0.0, float(default_ratio or 0.0)))
    return max(declared_minimum, fallback_minimum)


def writer_output_token_limit(
    target_words: int,
    configured_max_tokens: int,
    tokens_per_word: float = 1.45,
    minimum_tokens: int = 512,
    structured_output_overhead_tokens: int = 256,
) -> int:
    """Translate a unit's visible-word target into a calibrated output cap.

    The configured stage cap remains authoritative. Untargeted legacy calls keep
    that cap; targeted calls receive only the budget calibrated for their unit.
    """
    maximum = max(1, int(configured_max_tokens or 1))
    target = max(0, int(target_words or 0))
    if not target:
        return maximum
    ratio = max(0.1, float(tokens_per_word or 1.45))
    requested = max(
        int(minimum_tokens),
        math.ceil(target * ratio) + max(0, int(structured_output_overhead_tokens)),
    )
    return min(maximum, requested)


def writer_output_retry_limit(initial_tokens: int, configured_max_tokens: int) -> int:
    """Return one larger, stage-bounded cap after a genuinely truncated output."""
    maximum = max(1, int(configured_max_tokens or 1))
    initial = min(maximum, max(1, int(initial_tokens or 1)))
    if initial >= maximum:
        return initial
    return min(maximum, max(initial * 2, initial + 256))


def writer_scale_prompt_hint(
    report_budget: dict | None,
    *,
    unit_target_words: int = 0,
    unit_label: str = "当前交付单元",
    chapter_target_words: int = 0,
    unit_minimum_words: int = 0,
    evidence_status: str = "unknown",
) -> str:
    """Describe nested report, chapter, and generation-unit word allocations."""
    budget = dict(report_budget or {})
    target = max(0, int(budget.get("target_words") or 0))
    bounds = dict(budget.get("output_bounds") or {})
    minimum = max(0, int(bounds.get("min_words") or 0), int(budget.get("min_words") or 0))
    maximum = max(0, int(bounds.get("max_words") or 0))
    soft_maximum = max(0, int(budget.get("soft_max_words") or 0))
    unit_target = max(0, int(unit_target_words or 0))
    chapter_target = max(0, int(chapter_target_words or 0))
    unit_minimum = max(0, int(unit_minimum_words or 0))
    evidence = str(evidence_status or "unknown").strip().lower()
    if minimum and target and unit_target and not unit_minimum:
        unit_minimum = round(unit_target * min(1.0, minimum / target))

    constraints: list[str] = []
    if target:
        constraints.append(f"全文目标约 {target} 字")
    if minimum and maximum:
        constraints.append(f"全文允许范围 {minimum}–{maximum} 字")
    elif minimum:
        constraints.append(f"全文最低要求 {minimum} 字")
    elif maximum:
        constraints.append(f"全文上限 {maximum} 字")
    if soft_maximum:
        constraints.append(f"全文软控制线 {soft_maximum} 字")
    if chapter_target:
        constraints.append(f"本章分配约 {chapter_target} 字")
    if unit_target:
        constraints.append(f"{unit_label}目标约 {unit_target} 字")
    if minimum and unit_minimum:
        minimum_label = "最低完成要求" if evidence == "sufficient" else "最低参考"
        constraints.append(f"{unit_label}{minimum_label}约 {unit_minimum} 字")
    if not constraints:
        return ""

    nested = target and (chapter_target or unit_target)
    hint = "篇幅预算：" + "；".join(constraints) + "。"
    if nested:
        hint += "章节与交付单元是全文预算的内部拆分，不可相加；避免单个部分超额挤占其他部分。"
    if maximum:
        hint += "全文不得超过上限。"
    if soft_maximum:
        hint += "优先控制在软控制线以内。"
    if minimum:
        if evidence == "sufficient":
            hint += (
                "最终规模评估判定证据充足；必须完整执行当前计划，未达到本单元最低完成要求前不得收束，"
                "不得因核心观点已被简要提及而提前结束。"
            )
        elif evidence in {"limited", "insufficient"}:
            hint += "最终规模评估判定证据有限或不足；允许低于本单元最低参考，但须保留证据边界，不得重复扩写或编造。"
        else:
            hint += "不得仅因正文简洁而自行判断证据不足；确有具体证据缺口时才可欠填，并保留证据边界。"
    hint += "完成当前论证且接近对应目标后收束；不得重复、泛化扩写或为凑字数编造内容。"
    return hint


def assess_report_scale(target_words: int, actual_words: int, output_bounds: dict | None) -> dict:
    """Report measurable deviation from the user/system scale contract."""
    target = max(0, int(target_words or 0))
    actual = max(0, int(actual_words or 0))
    bounds = dict(output_bounds or {})
    minimum = max(0, int(bounds.get("min_words") or 0))
    maximum = max(0, int(bounds.get("max_words") or 0))
    overrun = max(0, actual - maximum) if maximum else 0
    return {
        "target_words": target,
        "actual_words": actual,
        "completion_rate": round(actual / target, 4) if target else None,
        "output_bounds": bounds,
        "over_budget": bool(maximum and overrun),
        "overrun_words": overrun,
        "below_minimum": bool(minimum and actual < minimum),
        "shortfall_words": max(0, minimum - actual) if minimum else 0,
    }


def assess_chapter_output(
    *,
    target_words: int,
    generated_text: str,
    previous_text: str = "",
    minimum_completion_ratio: float = 0.8,
    evidence_limited: bool = False,
) -> dict:
    """Measure generated chapter scale without deciding whether users should keep it."""
    target = max(0, int(target_words or 0))
    actual = measure_text_words(generated_text)
    previous = measure_text_words(previous_text)
    completion = round(actual / target, 4) if target else None
    underfilled = bool(target and actual < target * float(minimum_completion_ratio))

    underfill_reason = ""
    if underfilled:
        underfill_reason = "evidence_limited" if evidence_limited else "generation_budget_not_fulfilled"
    return {
        "target_words": target,
        "actual_words": actual,
        "previous_words": previous,
        "completion_rate": completion,
        "change_words": actual - previous,
        "underfilled": underfilled,
        "underfill_reason": underfill_reason,
    }
