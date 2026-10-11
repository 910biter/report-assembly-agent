"""Run-level, domain-neutral audit helpers for evidence and writing flows."""

from __future__ import annotations

import re


def extract_numbered_ids(lines: list[str]) -> list[int]:
    """Extract IDs from complete evidence records formatted as ``<id>. ...``."""
    return sorted({
        int(match.group(1))
        for line in lines
        if (match := re.match(r"^\s*(\d+)\.\s+", str(line or "")))
    })


def extract_tagged_ids(text: str, field: str) -> list[int]:
    """Extract stable ``field=<id>`` references from a serialized prompt."""
    name = re.escape(str(field or ""))
    return sorted({int(value) for value in re.findall(rf"\b{name}\s*=\s*(\d+)\b", str(text or ""))})


def collect_assigned_ids(value, kind: str) -> list[int]:
    """Collect plan assignments using the stable Fact/Inference ID fields."""
    keys = (
        {"fact_ids", "primary_fact_ids", "supporting_fact_ids"}
        if kind == "fact" else
        {"inference_ids", "primary_inference_ids", "supporting_inference_ids"}
    )
    found: set[int] = set()

    def visit(item):
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in keys and isinstance(nested, (list, tuple, set)):
                    for raw in nested:
                        try:
                            found.add(int(raw))
                        except (TypeError, ValueError):
                            continue
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return sorted(found)


def audit_id_flow(stage_ids: dict[str, list[int]]) -> dict:
    """Summarize unique identifier handoffs between ordered processing stages."""
    stages = {}
    ordered = list(stage_ids.items())
    for name, values in ordered:
        ids = sorted({int(value) for value in values if value is not None})
        stages[name] = {"unique_count": len(ids), "ids": ids}

    handoffs = []
    for (source_name, source_values), (target_name, target_values) in zip(ordered, ordered[1:]):
        source = {int(value) for value in source_values if value is not None}
        target = {int(value) for value in target_values if value is not None}
        handoffs.append({
            "from_stage": source_name,
            "to_stage": target_name,
            "source_unique_count": len(source),
            "target_unique_count": len(target),
            "transferred_unique_count": len(source & target),
            "not_forwarded_ids": sorted(source - target),
            "outside_source_ids": sorted(target - source),
            "transfer_ratio": round(len(source & target) / len(source), 4) if source else None,
        })
    return {"stages": stages, "handoffs": handoffs}


def build_generation_unit_stats(
    *, generation_mode: str, unit_id: str, target_words: int, actual_text: str,
    planned_fact_ids: list[int], supplied_fact_ids: list[int], cited_fact_ids: list[int],
    planned_inference_ids: list[int], supplied_inference_ids: list[int], cited_inference_ids: list[int],
    candidate_fact_ids: list[int] | None = None,
    candidate_inference_ids: list[int] | None = None,
    minimum_completion_ratio: float = 0.8,
) -> dict:
    """Use a single diagnostic schema for chapter and continuous-article calls."""
    from app.writing.scale_execution import measure_text_words

    def unique(values):
        return sorted({int(value) for value in values if value is not None})

    planned_facts = unique(planned_fact_ids)
    candidate_facts = unique(candidate_fact_ids if candidate_fact_ids is not None else supplied_fact_ids)
    supplied_facts = unique(supplied_fact_ids)
    cited_facts = unique(cited_fact_ids)
    planned_inferences = unique(planned_inference_ids)
    candidate_inferences = unique(
        candidate_inference_ids if candidate_inference_ids is not None else supplied_inference_ids
    )
    supplied_inferences = unique(supplied_inference_ids)
    cited_inferences = unique(cited_inference_ids)
    target = max(0, int(target_words or 0))
    actual = measure_text_words(actual_text or "")
    underfilled = bool(target and actual < target * float(minimum_completion_ratio))
    return {
        "generation_mode": str(generation_mode or "unknown"),
        "unit_id": str(unit_id or ""),
        "target_words": target,
        "actual_words": actual,
        "completion_rate": round(actual / target, 4) if target else None,
        "underfilled": underfilled,
        "underfill_reason": "generation_budget_not_fulfilled" if underfilled else "",
        "planned_fact_ids": planned_facts,
        "candidate_fact_ids": candidate_facts,
        "supplied_fact_ids": supplied_facts,
        "cited_fact_ids": cited_facts,
        "planned_fact_ids_missing_from_candidate_pool": sorted(set(planned_facts) - set(candidate_facts)),
        "budget_omitted_fact_ids": sorted(set(candidate_facts) - set(supplied_facts)),
        "planned_fact_ids_omitted_by_budget": sorted(
            (set(planned_facts) & set(candidate_facts)) - set(supplied_facts)
        ),
        "planned_fact_count": len(planned_facts),
        "supplied_fact_count": len(supplied_facts),
        "cited_fact_count": len(cited_facts),
        "planned_fact_coverage": round(len(set(planned_facts) & set(cited_facts)) / len(planned_facts), 4)
        if planned_facts else None,
        "supplied_fact_coverage": round(len(set(supplied_facts) & set(cited_facts)) / len(supplied_facts), 4)
        if supplied_facts else None,
        "planned_inference_ids": planned_inferences,
        "candidate_inference_ids": candidate_inferences,
        "supplied_inference_ids": supplied_inferences,
        "cited_inference_ids": cited_inferences,
        "planned_inference_ids_missing_from_candidate_pool": sorted(
            set(planned_inferences) - set(candidate_inferences)
        ),
        "budget_omitted_inference_ids": sorted(set(candidate_inferences) - set(supplied_inferences)),
        "planned_inference_ids_omitted_by_budget": sorted(
            (set(planned_inferences) & set(candidate_inferences)) - set(supplied_inferences)
        ),
        "planned_inference_count": len(planned_inferences),
        "supplied_inference_count": len(supplied_inferences),
        "cited_inference_count": len(cited_inferences),
        "planned_inference_coverage": round(len(set(planned_inferences) & set(cited_inferences)) / len(planned_inferences), 4)
        if planned_inferences else None,
        "supplied_inference_coverage": round(len(set(supplied_inferences) & set(cited_inferences)) / len(supplied_inferences), 4)
        if supplied_inferences else None,
    }


def aggregate_generation_units(units: list[dict]) -> dict:
    """Aggregate coverage over unique IDs; repeated cross-unit references do not inflate it."""
    def merged_ids(key):
        return sorted({int(value) for unit in units for value in unit.get(key, []) if value is not None})

    result = {}
    for prefix in ("fact", "inference"):
        planned = merged_ids(f"planned_{prefix}_ids")
        candidate = merged_ids(f"candidate_{prefix}_ids")
        supplied = merged_ids(f"supplied_{prefix}_ids")
        cited = merged_ids(f"cited_{prefix}_ids")
        missing_pool = merged_ids(f"planned_{prefix}_ids_missing_from_candidate_pool")
        budget_omitted = merged_ids(f"budget_omitted_{prefix}_ids")
        planned_budget_omitted = merged_ids(f"planned_{prefix}_ids_omitted_by_budget")
        result.update({
            f"planned_{prefix}_ids": planned,
            f"candidate_{prefix}_ids": candidate,
            f"supplied_{prefix}_ids": supplied,
            f"cited_{prefix}_ids": cited,
            f"planned_{prefix}_ids_missing_from_candidate_pool": missing_pool,
            f"budget_omitted_{prefix}_ids": budget_omitted,
            f"planned_{prefix}_ids_omitted_by_budget": planned_budget_omitted,
            f"planned_{prefix}_count": len(planned),
            f"candidate_{prefix}_count": len(candidate),
            f"supplied_{prefix}_count": len(supplied),
            f"cited_{prefix}_count": len(cited),
            f"planned_{prefix}_missing_from_candidate_pool_count": len(missing_pool),
            f"budget_omitted_{prefix}_count": len(budget_omitted),
            f"planned_{prefix}_omitted_by_budget_count": len(planned_budget_omitted),
            f"planned_{prefix}_coverage": round(len(set(planned) & set(cited)) / len(planned), 4) if planned else None,
            f"supplied_{prefix}_coverage": round(len(set(supplied) & set(cited)) / len(supplied), 4) if supplied else None,
        })
    targets = sum(int(unit.get("target_words") or 0) for unit in units)
    actuals = sum(int(unit.get("actual_words") or 0) for unit in units)
    result.update({
        "generation_units": len(units),
        "target_words": targets,
        "actual_words": actuals,
        "completion_rate": round(actuals / targets, 4) if targets else None,
        "underfilled_units": sum(bool(unit.get("underfilled")) for unit in units),
    })
    return result
