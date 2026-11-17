"""Compact evidence records and dependency-preserving planning contexts."""
from __future__ import annotations

import json

from app.assertion_semantics import normalize_semantic_profile, normalize_assertion_semantics
from app.context_budget import build_prompt_from_sections, count_tokens, publish_context_audit


def evidence_ids(values) -> list[int]:
    return list(dict.fromkeys(int(value) for value in values or [] if str(value).isdigit()))


def compact_profile(item: dict) -> str:
    profile = normalize_semantic_profile(item.get("semantic_profile"))
    parts = []
    for facet in profile["facets"]:
        value = f"{facet['axis']}={facet['label']}"
        if facet.get("detail") and facet["detail"] != facet["label"]:
            value += f"({facet['detail']})"
        parts.append(value)
    if profile["additional"]:
        parts.append(json.dumps(profile["additional"], ensure_ascii=False, separators=(",", ":")))
    if not parts and (item.get("assertion_mode") or item.get("assertion_mode_raw")):
        mode, raw = normalize_assertion_semantics(item.get("assertion_mode"), item.get("assertion_mode_raw"))
        parts.append(f"陈述性质={mode}({raw})")
    return "；".join(parts)


def fact_line(item: dict) -> str:
    profile = compact_profile(item)
    context = []
    if item.get("dimension"):
        context.append(f"维度={item['dimension']}")
    if item.get("source_files"):
        context.append(f"材料={item['source_files']}")
    if profile:
        context.append(f"来源语义:{profile}")
    return f"{item['id']}. {item.get('content', '')}" + (" [" + "; ".join(context) + "]" if context else "")


def inference_line(item: dict) -> str:
    # Inherited profiles live on the referenced fact records; rendering them
    # again for every inference consumes the window without adding information.
    profile = compact_profile(item)
    based = evidence_ids(item.get("based_fact_ids"))
    return (
        f"{item['id']}. {item.get('content', '')} "
        f"[来源层级={item.get('source_level') or 'MATERIAL_INFERENCE'}; based_fact_ids={based}"
        + (f"; 维度={item['dimension']}" if item.get("dimension") else "")
        + (f"; 置信度={item['confidence_level']}" if item.get("confidence_level") else "")
        + (f"; 推论语义:{profile}" if profile else "") + "]"
    )


def bind_plan_evidence(plan: dict, facts: list[dict], inferences: list[dict]) -> dict:
    """Keep valid inference bases in the same execution unit, without semantic guesses."""
    valid_facts = {int(item["id"]) for item in facts if item.get("id") is not None}
    by_id = {int(item["id"]): item for item in inferences if item.get("id") is not None}

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for value in list(node.values()):
                if isinstance(value, (dict, list)):
                    visit(value)
            for fact_key, inference_key in (
                ("fact_ids", "inference_ids"), ("primary_fact_ids", "primary_inference_ids"),
                ("facts", "inferences"),
            ):
                if inference_key not in node:
                    continue
                bases = [
                    fid for iid in evidence_ids(node[inference_key])
                    for fid in evidence_ids(by_id.get(iid, {}).get("based_fact_ids"))
                    if fid in valid_facts
                ]
                if bases:
                    node[fact_key] = list(dict.fromkeys(evidence_ids(node.get(fact_key)) + bases))
    visit(plan)
    return plan


def planning_prompt(stage: str, sections: list, facts: list[dict], inferences: list[dict],
                    budget: int, required_facts=(), required_inferences=(), tagged=False,
                    strict_required: bool = False) -> tuple[str, dict]:
    """Pack an inference together with its bases; each fact is rendered once."""
    headers = "\n可用事实(保留来源语义):\n\n可用推断(依据见事实 ID):\n"
    contract, audit = build_prompt_from_sections(stage, sections, max(0, budget - count_tokens(headers)))
    if strict_required and audit.get("required_missing_items"):
        raise ValueError(f"{stage.upper()}_REQUIRED_CONTRACT_EXCEEDS_CONTEXT")
    available = max(0, budget - count_tokens(contract + headers))
    by_fact = {int(item["id"]): item for item in facts if item.get("id") is not None}
    by_inference = {int(item["id"]): item for item in inferences if item.get("id") is not None}
    fact_records = {key: (f"fact_id={key}; " if tagged else "") + fact_line(item) for key, item in by_fact.items()}
    inference_records = {key: (f"inference_id={key}; " if tagged else "") + inference_line(item) for key, item in by_inference.items()}
    selected_facts, selected_inferences = {}, {}
    used = 0

    def take(fid=None, iid=None):
        nonlocal used
        bases = evidence_ids(by_inference.get(iid, {}).get("based_fact_ids")) if iid is not None else [fid]
        new_facts = {key: fact_records[key] for key in bases if key in fact_records and key not in selected_facts}
        new_inference = inference_records.get(iid) if iid not in selected_inferences else None
        cost = sum(count_tokens(line + "\n") for line in new_facts.values())
        if new_inference:
            cost += count_tokens(new_inference + "\n")
        if used + cost > available:
            return
        selected_facts.update(new_facts)
        if new_inference:
            selected_inferences[iid] = new_inference
        used += cost

    for fid in evidence_ids(required_facts):
        take(fid=fid)
    for iid in evidence_ids(required_inferences):
        take(iid=iid)
    if strict_required:
        missing_facts = sorted(set(evidence_ids(required_facts)) - set(selected_facts))
        missing_inferences = sorted(set(evidence_ids(required_inferences)) - set(selected_inferences))
        if missing_facts or missing_inferences:
            raise ValueError(
                f"{stage.upper()}_REQUIRED_EVIDENCE_EXCEEDS_CONTEXT: "
                f"facts={missing_facts}, inferences={missing_inferences}"
            )
    # Retrieval/anchor order remains authoritative. Share spare capacity between
    # facts and judgments, rather than letting either monopolize the window.
    fids, iids = list(by_fact), list(by_inference)
    for index in range(max(len(fids), len(iids), 0)):
        if index < len(fids):
            take(fid=fids[index])
        if index < len(iids):
            take(iid=iids[index])
    prompt = contract + "\n可用事实(保留来源语义):\n" + "\n".join(selected_facts.values())
    prompt += "\n可用推断(依据见事实 ID):\n" + "\n".join(selected_inferences.values())
    for name, records, selected in (
        ("事实", fact_records, selected_facts), ("推断", inference_records, selected_inferences),
    ):
        audit["sections"][name] = {
            "candidate_items": len(records), "selected_items": len(selected),
            "omitted_items": len(records) - len(selected),
            "candidate_tokens": sum(count_tokens(line) for line in records.values()),
            "selected_tokens": sum(count_tokens(line) for line in selected.values()),
            "selected_ids": list(selected), "omitted_ids": sorted(set(records) - set(selected)),
        }
    audit.update(actual_tokens=count_tokens(prompt), budget_tokens=budget)
    audit["utilization"] = round(audit["actual_tokens"] / max(1, budget), 4)
    audit["truncated"] = any(section["omitted_items"] for section in audit["sections"].values())
    publish_context_audit(audit)
    return prompt, audit
