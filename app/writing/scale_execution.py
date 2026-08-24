"""Generic chapter-budget assessment helpers without domain vocabulary."""

from __future__ import annotations

import re


def measure_text_words(text: str) -> int:
    """Approximate Chinese report length using Chinese chars plus word tokens."""
    value = text or ""
    return len(re.findall(r"[\u4e00-\u9fff]", value)) + len(re.findall(r"[A-Za-z0-9]+", value))


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
