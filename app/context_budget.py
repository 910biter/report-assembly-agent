"""Token-aware context packing and low-overhead capacity observability."""
from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field

class TokenCounter:
    """Network-free packing estimates, calibrated by final vLLM request counts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.method = "estimated"

    def reset(self) -> None:
        with self._lock:
            self.method = "estimated"

    def count(self, text: str) -> int:
        value = str(text or "")
        if not value:
            return 0
        from app.vllm_capacity import raw_estimate, estimate
        try:
            from app.model_connection import load_connection
            count = estimate(value, load_connection())
            self.method = "estimated:calibrated" if count > raw_estimate(value) else "estimated"
            return count
        except Exception:
            self.method = "estimated"
            return raw_estimate(value)

    def truncate(self, text: str, budget_tokens: int, suffix: str = "…") -> str:
        value = str(text or "")
        budget = max(0, int(budget_tokens or 0))
        if self.count(value) <= budget:
            return value
        suffix_tokens = self.count(suffix)
        target = max(0, budget - suffix_tokens)
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if self.count(value[:middle]) <= target:
                low = middle
            else:
                high = middle - 1
        return value[:low].rstrip() + (suffix if low else "")


token_counter = TokenCounter()


@dataclass
class ContextSection:
    name: str
    items: list[str] = field(default_factory=list)
    weight: float = 1.0
    required_items: int = 1


_pending_audit: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "pending_context_audit", default=None,
)


def count_tokens(text: str) -> int:
    return token_counter.count(text)


def tokenizer_method() -> str:
    return token_counter.method


def truncate_tokens(text: str, budget_tokens: int, suffix: str = "…") -> str:
    return token_counter.truncate(text, budget_tokens, suffix=suffix)


def publish_context_audit(audit: dict) -> None:
    _pending_audit.set(dict(audit or {}))


def consume_context_audit() -> dict:
    audit = _pending_audit.get() or {}
    _pending_audit.set(None)
    return dict(audit)


def build_prompt_from_sections(stage: str, sections: list[ContextSection],
                               budget_tokens: int, separator: str = "\n") -> tuple[str, dict]:
    """Weighted fair packing with complete records and automatic slack reuse."""
    budget = max(0, int(budget_tokens or 0))
    normalized = [ContextSection(
        name=section.name,
        items=[str(item).strip() for item in section.items if str(item or "").strip()],
        weight=max(0.01, float(section.weight or 1.0)),
        required_items=max(0, int(section.required_items or 0)),
    ) for section in sections]
    costs = [[count_tokens(item + separator) for item in section.items] for section in normalized]
    selected: list[list[str]] = [[] for _ in normalized]
    selected_costs: list[list[int]] = [[] for _ in normalized]
    cursors = [0 for _ in normalized]
    # Suffix minimum avoids O(records^2) scanning on huge planning / QA contexts.
    cheapest_tail: list[list[int]] = []
    for part in costs:
        tail = [budget + 1] * (len(part) + 1)
        for index in range(len(part) - 1, -1, -1):
            tail[index] = min(part[index], tail[index + 1])
        cheapest_tail.append(tail)
    used = 0

    def take(section_index: int) -> bool:
        nonlocal used
        while cursors[section_index] < len(normalized[section_index].items):
            cursor = cursors[section_index]
            cost = costs[section_index][cursor]
            cursors[section_index] += 1
            if used + cost > budget:
                continue
            selected[section_index].append(normalized[section_index].items[cursor])
            selected_costs[section_index].append(cost)
            used += cost
            return True
        return False

    # Preserve each section's contract before distributing the remaining pool.
    for index, section in enumerate(normalized):
        for _ in range(min(section.required_items, len(section.items))):
            take(index)

    while True:
        available = [index for index, section in enumerate(normalized)
                     if used + cheapest_tail[index][cursors[index]] <= budget]
        if not available:
            break
        # Weight controls relative importance; selected count prevents monopolies.
        index = max(available, key=lambda value: normalized[value].weight / (len(selected[value]) + 1))
        take(index)

    output_lines: list[str] = []
    section_audit: dict[str, dict] = {}
    for index, section in enumerate(normalized):
        omitted = max(0, len(section.items) - len(selected[index]))
        output_lines.extend(selected[index])
        if omitted:
            marker = f"[上下文覆盖] {section.name}另有 {omitted} 条未展开，原始数据仍完整保留。"
            marker_cost = count_tokens(marker + separator)
            if used + marker_cost <= budget:
                output_lines.append(marker)
                used += marker_cost
        selected_tokens = sum(selected_costs[index])
        section_audit[section.name] = {
            "candidate_items": len(section.items),
            "selected_items": len(selected[index]),
            "omitted_items": omitted,
            "candidate_tokens": sum(costs[index]),
            "selected_tokens": selected_tokens,
            "required_items": min(section.required_items, len(section.items)),
            "required_missing_items": max(0, min(section.required_items, len(section.items)) - len(selected[index])),
            "selection_rate": round(len(selected[index]) / max(len(section.items), 1), 4),
        }
    prompt = separator.join(output_lines)
    actual_tokens = count_tokens(prompt)
    audit = {
        "stage": stage,
        "tokenizer_method": token_counter.method,
        "budget_tokens": budget,
        "actual_tokens": actual_tokens,
        "utilization": round(actual_tokens / max(budget, 1), 4),
        "truncated": any(item["omitted_items"] for item in section_audit.values()),
        "required_missing_items": sum(item["required_missing_items"] for item in section_audit.values()),
        "sections": section_audit,
    }
    publish_context_audit(audit)
    return prompt, audit


def audit_plain_prompt(stage: str, prompt: str, budget_tokens: int) -> dict:
    actual = count_tokens(prompt)
    audit = {
        "stage": stage,
        "tokenizer_method": token_counter.method,
        "budget_tokens": int(budget_tokens),
        "actual_tokens": actual,
        "utilization": round(actual / max(int(budget_tokens), 1), 4),
        "truncated": False,
        "sections": {"prompt": {
            "candidate_items": 1,
            "selected_items": 1,
            "omitted_items": 0,
            "candidate_tokens": actual,
            "selected_tokens": actual,
            "selection_rate": 1.0,
        }},
    }
    publish_context_audit(audit)
    return audit
