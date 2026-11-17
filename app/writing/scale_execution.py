"""Generic chapter-budget assessment helpers without domain vocabulary."""

from __future__ import annotations

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
    target = max(0, int(target_words or 0))
    declared_minimum = declared_report_minimum_words(budget)
    fallback_minimum = round(target * max(0.0, float(default_ratio or 0.0)))
    return max(declared_minimum, fallback_minimum)


def declared_report_minimum_words(budget: dict | None) -> int:
    """Resolve evidence and acceptance floors to the stored runtime contract."""
    budget = dict(budget or {})
    bounds = dict(budget.get("output_bounds") or {})
    resolved = max(
        0,
        int(budget.get("effective_min_words") or 0),
        int(bounds.get("min_words") or 0),
    )
    if "effective_min_words" in budget:
        return resolved
    # Read pre-contract historical plans without changing their semantics.
    return max(resolved, int(budget.get("min_words") or 0))


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
    minimum = declared_report_minimum_words(budget)
    maximum = max(0, int(bounds.get("max_words") or 0))
    soft_maximum = max(0, int(budget.get("soft_max_words") or 0))
    unit_target = max(0, int(unit_target_words or 0))
    chapter_target = max(0, int(chapter_target_words or 0))
    unit_minimum = max(0, int(unit_minimum_words or 0))
    evidence = str(evidence_status or "unknown").strip().lower()
    supported_maximum = max(0, int(budget.get("evidence_supported_max_words") or 0))
    capacity_shortfall = bool(supported_maximum and supported_maximum < minimum and budget.get("underfill_reason"))
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
        minimum_label = "最低参考" if capacity_shortfall else "最低完成要求"
        constraints.append(f"{unit_label}{minimum_label}约 {unit_minimum} 字")
    if not constraints:
        return ""

    nested = target and (chapter_target or unit_target)
    hint = "篇幅预算：" + "；".join(constraints) + "。"
    if nested:
        hint += "章节与交付单元按层级共同分配全文预算，各层目标用于安排所属内容。"
    if maximum:
        hint += f"全文控制在 {maximum} 字以内。"
    if soft_maximum:
        hint += "成文规模优先贴近全文软控制线。"
    if minimum:
        if evidence == "sufficient":
            hint += (
                "证据评估为充足：完整展开当前计划中的论证，并在达到本单元最低完成要求后收束。"
            )
        elif evidence in {"limited", "insufficient"}:
            if capacity_shortfall:
                hint += (
                    f"现有材料的篇幅承载估计约 {supported_maximum} 字，低于用户下限；"
                    "完整呈现材料能够支持的论证，并说明篇幅受限的具体依据。"
                )
            else:
                hint += (
                    "按用户篇幅下限写作；围绕已有事实、比较和适用范围充分展开，并说明具体的信息缺口。"
                )
        else:
            hint += "按用户篇幅下限组织完整论证；具体证据缺口及其对成文规模的影响以计划记录为准。"
    hint += "每段围绕证据、关系或适用范围提供有效信息，完成当前论证并接近对应目标后自然收束。"
    return hint


def assess_report_scale(
    target_words: int,
    actual_words: int,
    output_bounds: dict | None,
    *,
    minimum_words: int | None = None,
) -> dict:
    """Report measurable deviation from the user/system scale contract."""
    target = max(0, int(target_words or 0))
    actual = max(0, int(actual_words or 0))
    bounds = dict(output_bounds or {})
    minimum = max(
        0,
        int(bounds.get("min_words") or 0),
        int(minimum_words) if minimum_words is not None else 0,
    )
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
