"""Source-scoped proposition identity, independent of semantic classifications."""
import json
import re

from app.agents.base import BaseAgent
from app.context_budget import ContextSection, build_prompt_from_sections, count_tokens
from app.runtime_profiles import stage_input_budget_tokens
from app.token_monitor import update_call_funnel

SYSTEM = """判断同一来源的已有陈述与新陈述是否为同一命题。严格返回 JSON：
{"matches": [{"incoming": "c0", "target": "f12", "certain": true}]}
逐对核对正文及原文引用中的主体、对象、数值、时间、条件、归属、情态、否定和范围。仅在两者完整等价、可以确定为同义重复时返回匹配；新增独立信息、部分重合、矛盾或不确定时保留各自陈述。分类画像只描述语义状态，不决定命题身份。target 从该 incoming 的 candidates 中选择。没有确定匹配时返回 {"matches": []}。"""


def same_source(left: dict, right: dict) -> bool:
    return bool(set(left.get("unit_ids", [])) & set(right.get("unit_ids", [])))


def source_coverage_preserved(incoming: dict, candidate: dict) -> bool:
    """A semantic merge may not hide an independently quoted source fragment."""
    normalize = lambda value: "".join(str(value).split())
    old_quotes = [normalize(value) for value in candidate.get("quotes", []) if normalize(value)]
    new_quotes = [normalize(value) for value in incoming.get("quotes", []) if normalize(value)]
    if bool(new_quotes) and all(any(value in old for old in old_quotes) for value in new_quotes):
        return True
    # Equivalent propositions can quote different spans of the same source.
    # Only open that case for model review when explicit numeric anchors agree.
    numbers = lambda row: set(re.findall(r"\d+(?:\.\d+)?", str(row.get("content") or "")))
    incoming_numbers, candidate_numbers = numbers(incoming), numbers(candidate)
    return bool(incoming_numbers) and incoming_numbers == candidate_numbers


def validate_matches(payload: dict, bundles: list[dict]) -> dict[str, str]:
    allowed = {row["incoming"]["key"]: {item["key"] for item in row["candidates"]
                                          if same_source(row["incoming"], item)
                                          and source_coverage_preserved(row["incoming"], item)} for row in bundles}
    matches = {}
    rejected = set()
    for item in payload.get("matches", []) if isinstance(payload.get("matches"), list) else []:
        if not isinstance(item, dict) or item.get("certain") is not True:
            continue
        key, target = item.get("incoming"), item.get("target")
        if not isinstance(key, str) or not isinstance(target, str) or target not in allowed.get(key, set()):
            continue
        if key in matches and matches[key] != target:
            rejected.add(key)
        matches[key] = target
    return {key: target for key, target in matches.items() if key not in rejected}


def match_propositions(incoming: list[dict], existing: list[dict]) -> tuple[dict[str, str], dict]:
    matches, bundles = {}, []
    previous = list(existing)
    for row in incoming:
        candidates = [other for other in previous if same_source(row, other)
                      and source_coverage_preserved(row, other)]
        exact = next((other for other in candidates if row["content"] == other["content"]
                      and sorted(row["quotes"]) == sorted(other["quotes"])), None)
        if exact:
            matches[row["key"]] = exact["key"]
        elif candidates:
            bundles.append({"incoming": row, "candidates": candidates})
        previous.append(row)
    audit = {"ambiguous": len(bundles), "classified": 0, "unclassified": 0, "failures": []}
    agent = BaseAgent()
    agent.name = "evidence"
    agent.role = SYSTEM
    agent.max_retries = 0
    while bundles:
        serialized = [json.dumps(row, ensure_ascii=False) for row in bundles]
        prompt, packing = build_prompt_from_sections("evidence", [
            ContextSection("proposition_pairs", serialized, required_items=1),
        ], max(0, stage_input_budget_tokens("evidence") - count_tokens(SYSTEM)))
        selected = packing.get("sections", {}).get("proposition_pairs", {}).get("selected_items", 0)
        # Never make identity decisions from a truncated candidate bundle.
        fitted = [row for row, raw in zip(bundles, serialized) if raw in prompt]
        if not selected or not fitted:
            audit["unclassified"] += len(bundles)
            audit["failures"].append("identity_input_capacity")
            break
        try:
            result = agent.generate_json(prompt, max_tokens=min(2048, 256 + len(fitted) * 48))
            matches.update(validate_matches(result, fitted))
            audit["classified"] += len(fitted)
            update_call_funnel(agent.last_call_id, operation="proposition_identity", comparison_count=len(fitted))
        except Exception as exc:
            if str(exc) == "TASK_PAUSED":
                raise
            audit["unclassified"] += len(fitted)
            audit["failures"].append(str(exc)[:200])
        fitted_keys = {row["incoming"]["key"] for row in fitted}
        bundles = [row for row in bundles if row["incoming"]["key"] not in fitted_keys]
    return matches, audit
