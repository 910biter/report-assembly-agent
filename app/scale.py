"""Report scale planning.

Scale is handled as constraints, not a weighted average:
- user expectation defines the requested target;
- evidence capacity defines the safe upper bound;
- report structure distributes the budget;
- quality guardrails cap unsupported expansion.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy

from app.db import connect

SCALE_POLICY = {
    "generic_recommended_words": 3000,
    "generic_preliminary_max_words": 5000,
    "boundary_mode_recommended_words": 3200,
    "boundary_mode_preliminary_max_words": 4500,
    "actual_evidence_base_words": 4200,
    "actual_evidence_role_bonus_words": 600,
    "actual_evidence_preliminary_base_words": 8000,
    "actual_evidence_preliminary_bonus_words": 900,
    "empty_plan_safe_words": 1800,
    "empty_plan_recommended_words": 1200,
    "minimum_report_words": 800,
    "boundary_mode_base_capacity_words": 1400,
    "boundary_mode_fact_capacity_words": 95,
    "boundary_mode_inference_capacity_words": 120,
    "boundary_mode_capacity_cap_words": 5200,
    "chapter_min_words": 160,
    "chapter_no_match_boundary_words": 220,
    "chapter_no_match_generic_words": 300,
    "chapter_high_capacity_cap_words": 1500,
    "chapter_medium_capacity_cap_words": 900,
    "soft_max_factor": 1.15,
    "hard_max_factor": 1.3,
    "severe_conflict_factor": 1.25,
    "under_support_factor": 0.6,
    "fact_match_broaden_limit": 10,
}


def parse_requested_words(text: str) -> tuple[int | None, bool]:
    """Extract a user word-count target and whether it is phrased as mandatory."""
    raw = text or ""
    mandatory = any(word in raw for word in ("必须", "一定", "不少于", "至少", "一万字", "10000字"))
    match = re.search(r"(\d+(?:\.\d+)?)\s*万\s*字", raw)
    if match:
        return int(float(match.group(1)) * 10000), mandatory
    chinese_wan = {
        "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    match = re.search(r"([一两二三四五六七八九十])\s*万\s*字", raw)
    if match:
        return chinese_wan[match.group(1)] * 10000, mandatory
    candidates = [int(item) for item in re.findall(r"(\d{3,5})\s*字", raw)]
    if candidates:
        return max(candidates), mandatory
    return None, mandatory


def build_preliminary_scale_plan(theme: str, user_requirements: str,
                                 profile: dict | None = None) -> dict:
    """Estimate scale before evidence extraction from user intent and material roles."""
    profile = profile or {}
    requested, mandatory = parse_requested_words(f"{theme}\n{user_requirements}")
    report_mode = profile.get("report_mode") or "generic"
    roles = profile.get("material_roles") or {}
    has_actual = bool(profile.get("has_actual_evidence"))
    actual_roles = set(profile.get("actual_roles") or [])

    if report_mode == "requirement_summary":
        recommended = SCALE_POLICY["boundary_mode_recommended_words"]
        preliminary_max = SCALE_POLICY["boundary_mode_preliminary_max_words"]
    elif has_actual:
        actual_count = sum(int(roles.get(role, 0)) for role in actual_roles)
        recommended = (
            SCALE_POLICY["actual_evidence_base_words"]
            + min(actual_count, 6) * SCALE_POLICY["actual_evidence_role_bonus_words"]
        )
        preliminary_max = (
            SCALE_POLICY["actual_evidence_preliminary_base_words"]
            + min(actual_count, 8) * SCALE_POLICY["actual_evidence_preliminary_bonus_words"]
        )
    else:
        recommended = SCALE_POLICY["generic_recommended_words"]
        preliminary_max = SCALE_POLICY["generic_preliminary_max_words"]

    target = requested or recommended
    conflict = requested is not None and requested > preliminary_max * SCALE_POLICY["severe_conflict_factor"]
    return {
        "stage": "preliminary",
        "user_requested_words": requested,
        "user_requirement_mandatory": mandatory,
        "recommended_words": recommended,
        "preliminary_safe_max_words": preliminary_max,
        "target_words": target,
        "scale_risk": "high" if conflict else "medium" if requested and requested > preliminary_max else "low",
        "needs_negotiation": bool(conflict),
        "reason": (
            "用户目标明显高于当前材料类型可预估承载能力，需在事实提取后重新核算。"
            if conflict else "初步规模可自动处理，最终规模以后续事实容量为准。"
        ),
    }


def build_report_scale_plan(plan: dict, facts: list[dict], inferences: list[dict],
                            profile: dict | None = None,
                            preliminary: dict | None = None) -> dict:
    """Compute final safe budget after evidence extraction."""
    profile = profile or {}
    preliminary = preliminary or {}
    chapter_plans = deepcopy(plan.get("chapter_plans") or [{"title": t} for t in plan.get("structure", [])])
    requested = preliminary.get("user_requested_words")
    mandatory = bool(preliminary.get("user_requirement_mandatory"))
    existing_budget = plan.get("budget") or {}
    default_target = int(
        existing_budget.get("target_words")
        or preliminary.get("recommended_words")
        or SCALE_POLICY["generic_recommended_words"]
    )

    chapter_budgets = []
    total_safe = 0
    total_recommended = 0
    for chapter in chapter_plans:
        budget = _chapter_capacity(chapter, facts, inferences, profile)
        chapter_budgets.append(budget)
        total_safe += int(budget["max_safe_words"])
        total_recommended += int(budget["recommended_words"])

    if not chapter_budgets:
        total_safe = SCALE_POLICY["empty_plan_safe_words"]
        total_recommended = SCALE_POLICY["empty_plan_recommended_words"]

    max_safe = max(total_safe, SCALE_POLICY["minimum_report_words"])
    if profile.get("report_mode") == "requirement_summary" and len(facts) >= 10:
        # Boundary-only material cannot support unproven real-world results, but
        # can still support adequate explanation of confirmed rules, structures,
        # constraints and missing information.
        requirement_capacity = (
            SCALE_POLICY["boundary_mode_base_capacity_words"]
            + len(facts) * SCALE_POLICY["boundary_mode_fact_capacity_words"]
            + len(inferences) * SCALE_POLICY["boundary_mode_inference_capacity_words"]
        )
        max_safe = max(max_safe, min(SCALE_POLICY["boundary_mode_capacity_cap_words"], requirement_capacity))
        if max_safe > total_safe:
            chapter_budgets = _expand_chapter_capacities(
                chapter_budgets,
                max_safe,
                profile.get("report_mode") or "generic",
            )
            total_safe = sum(int(item["max_safe_words"]) for item in chapter_budgets)
            total_recommended = sum(int(item["recommended_words"]) for item in chapter_budgets)
    recommended = min(max(total_recommended, SCALE_POLICY["minimum_report_words"]), max_safe)
    user_target = int(requested or default_target)
    if requested is None:
        effective_target = min(max(default_target, recommended), max_safe)
    else:
        effective_target = min(user_target, max_safe)

    severe_conflict = requested is not None and user_target > max_safe * SCALE_POLICY["severe_conflict_factor"]
    scale_risk = "high" if severe_conflict else "medium" if requested and user_target > max_safe else "low"
    final_chapters = _distribute_target(chapter_plans, chapter_budgets, effective_target)
    soft_max = max(effective_target, min(max_safe, int(effective_target * SCALE_POLICY["soft_max_factor"])))
    hard_max = max(soft_max, min(max_safe, int(effective_target * SCALE_POLICY["hard_max_factor"])))

    return {
        "stage": "final",
        "user_requested_words": requested,
        "user_requirement_mandatory": mandatory,
        "effective_target_words": int(effective_target),
        "recommended_words": int(recommended),
        "max_safe_words": int(max_safe),
        "soft_max_words": int(soft_max),
        "hard_max_words": int(hard_max),
        "fact_count": len(facts),
        "inference_count": len(inferences),
        "scale_risk": scale_risk,
        "needs_negotiation": bool(severe_conflict),
        "reason": _scale_reason(profile, requested, max_safe, severe_conflict),
        "chapter_budgets": final_chapters,
        "quality_guardrails": [
            "不得为达到字数目标虚构事实、成果、数据或案例。",
            "证据不足章节应转写为边界说明、待补事项或风险提示。",
            "扩写只能围绕已抽取 facts/inferences 及其证据来源展开。",
        ],
    }


def apply_scale_plan_to_report_plan(plan_id: int, scale_plan: dict) -> None:
    """Persist final scale budget into report_plans for Writer consumption."""
    with connect() as conn:
        row = conn.execute("SELECT budget, chapter_plans FROM report_plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            return
        try:
            budget = json.loads(row["budget"] or "{}")
        except (TypeError, ValueError):
            budget = {}
        try:
            chapters = json.loads(row["chapter_plans"] or "[]")
        except (TypeError, ValueError):
            chapters = []
        by_title = {item["title"]: item for item in scale_plan.get("chapter_budgets") or []}
        for chapter in chapters:
            item = by_title.get(chapter.get("title"))
            if not item:
                continue
            chapter["target_words"] = int(item.get("target_words") or chapter.get("target_words") or 0)
            chapter["soft_max_words"] = int(
                item.get("soft_max_words")
                or chapter["target_words"] * SCALE_POLICY["soft_max_factor"]
            )
            chapter["hard_max_words"] = int(
                item.get("hard_max_words")
                or chapter["target_words"] * SCALE_POLICY["hard_max_factor"]
            )
            chapter["scale_risk"] = item.get("scale_risk", "low")
            chapter["evidence_capacity"] = item.get("evidence_capacity", "medium")
        budget.update({
            "target_words": int(scale_plan.get("effective_target_words") or budget.get("target_words") or 0),
            "soft_max_words": int(scale_plan.get("soft_max_words") or budget.get("soft_max_words") or 0),
            "hard_max_words": int(scale_plan.get("hard_max_words") or budget.get("hard_max_words") or 0),
            "user_requested_words": scale_plan.get("user_requested_words"),
            "max_safe_words": scale_plan.get("max_safe_words"),
            "scale_risk": scale_plan.get("scale_risk"),
            "budget_authority": "report_scale_plan",
            "budget_freeze_stage": "analysis" if scale_plan.get("inference_count", 0) else "evidence",
        })
        conn.execute(
            "UPDATE report_plans SET budget=?, chapter_plans=? WHERE id=?",
            (json.dumps(budget, ensure_ascii=False), json.dumps(chapters, ensure_ascii=False), plan_id),
        )


def _chapter_capacity(chapter: dict, facts: list[dict], inferences: list[dict], profile: dict) -> dict:
    title = str(chapter.get("title", ""))
    allowed_roles = set(chapter.get("allowed_roles") or [])
    required_terms = [str(item) for item in chapter.get("required_facts") or []]
    matched = _matching_facts(facts, allowed_roles, required_terms)
    if profile.get("report_mode") == "requirement_summary" and len(matched) < 2:
        matched = _broaden_chapter_fact_match(chapter, facts)
    actual_roles = set(profile.get("actual_roles") or [])
    normative_roles = set(profile.get("normative_roles") or [])
    actual = [f for f in matched if set(f.get("source_roles") or []) & actual_roles]
    normative = [f for f in matched if set(f.get("source_roles") or []) & normative_roles]
    unknown = [f for f in matched if f not in actual and f not in normative]
    matched_ids = {int(f.get("id")) for f in matched if f.get("id") is not None}
    related_inferences = [
        inf for inf in inferences
        if matched_ids & set(int(x) for x in (inf.get("based_fact_ids") or []) if str(x).isdigit())
    ]

    base = 220 if profile.get("report_mode") != "requirement_summary" else 180
    max_safe = (
        base
        + len(actual) * 220
        + len(normative) * 115
        + len(unknown) * 80
        + len(related_inferences) * 140
    )
    if not matched:
        max_safe = (
            SCALE_POLICY["chapter_no_match_boundary_words"]
            if profile.get("report_mode") == "requirement_summary"
            else SCALE_POLICY["chapter_no_match_generic_words"]
        )
    duplicate_penalty = max(0, len(matched) - len({f.get("content", "")[:60] for f in matched})) * 60
    max_safe = max(SCALE_POLICY["chapter_min_words"], max_safe - duplicate_penalty)
    if profile.get("report_mode") == "requirement_summary" and not actual:
        # Normative packets cannot support invented outcomes, but can still
        # support interpretation. Use Planner/evidence signals, not domain words.
        high_capacity = chapter.get("evidence_density") == "high" or len(matched) >= 5
        max_safe = min(
            max_safe,
            SCALE_POLICY["chapter_high_capacity_cap_words"]
            if high_capacity
            else SCALE_POLICY["chapter_medium_capacity_cap_words"],
        )
    recommended = int(max_safe * (0.72 if profile.get("report_mode") == "requirement_summary" else 0.78))
    return {
        "title": title,
        "matched_fact_count": len(matched),
        "actual_fact_count": len(actual),
        "normative_fact_count": len(normative),
        "inference_count": len(related_inferences),
        "recommended_words": max(SCALE_POLICY["chapter_min_words"], recommended),
        "max_safe_words": int(max_safe),
        "evidence_capacity": "high" if max_safe >= 900 else "medium" if max_safe >= 450 else "low",
        "scale_risk": (
            "high"
            if max_safe < int(chapter.get("target_words") or 0) * SCALE_POLICY["under_support_factor"]
            else "low"
        ),
    }


def _matching_facts(facts: list[dict], allowed_roles: set[str], required_terms: list[str]) -> list[dict]:
    result = []
    terms = [term for item in required_terms for term in re.split(r"[、/，,；;()\s]+", item) if len(term) >= 2]
    seed = " ".join(required_terms)
    for fact in facts:
        roles = set(fact.get("source_roles") or [])
        if allowed_roles and roles and not (roles & allowed_roles):
            continue
        text = f"{fact.get('dimension', '')} {fact.get('content', '')} {' '.join(fact.get('source_files') or [])}"
        if not terms or any(term in text for term in terms) or _ngram_similarity(seed, text) >= 0.08:
            result.append(fact)
    return result


def _broaden_chapter_fact_match(chapter: dict, facts: list[dict]) -> list[dict]:
    """Broaden matching using only planner-provided chapter terms.

    This is intentionally domain-neutral. Domain words belong in task profiles or
    external policy/config, not in the generic scale algorithm.
    """
    title = str(chapter.get("title", ""))
    required = " ".join(str(item) for item in chapter.get("required_facts") or [])
    questions = " ".join(str(item) for item in chapter.get("questions") or [])
    seed = f"{title} {required} {questions}"
    topic_terms = set(_terms(seed))
    scored = []
    for fact in facts:
        text = f"{fact.get('dimension', '')} {fact.get('content', '')} {' '.join(fact.get('source_files') or [])}"
        score = sum(1 for term in topic_terms if term and term in text)
        score += int(_ngram_similarity(seed, text) * 10)
        if score:
            scored.append((score, fact))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [fact for _score, fact in scored[:SCALE_POLICY["fact_match_broaden_limit"]]]


def _terms(text: str) -> list[str]:
    return [
        term for term in re.split(r"[、/，,；;()\s]+", text or "")
        if len(term) >= 2
    ]


def _ngram_similarity(left: str, right: str, n: int = 2) -> float:
    a = re.sub(r"\s+", "", left or "")
    b = re.sub(r"\s+", "", right or "")
    if len(a) < n or len(b) < n:
        return 0.0
    grams_a = {a[i:i + n] for i in range(len(a) - n + 1)}
    grams_b = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / max(len(grams_a), 1)


def _distribute_target(chapter_plans: list[dict], capacities: list[dict], effective_target: int) -> list[dict]:
    if not capacities:
        return []
    total_recommended = sum(int(c["recommended_words"]) for c in capacities) or 1
    result = []
    assigned = 0
    for idx, (chapter, cap) in enumerate(zip(chapter_plans, capacities)):
        if idx == len(capacities) - 1:
            target = max(SCALE_POLICY["chapter_min_words"], effective_target - assigned)
        else:
            target = int(effective_target * int(cap["recommended_words"]) / total_recommended)
            target = max(SCALE_POLICY["chapter_min_words"], min(target, int(cap["max_safe_words"])))
            assigned += target
        target = min(target, int(cap["max_safe_words"]))
        result.append({
            **cap,
            "title": chapter.get("title", cap.get("title", "")),
            "target_words": int(target),
            "soft_max_words": int(
                max(target, min(int(cap["max_safe_words"]), target * SCALE_POLICY["soft_max_factor"]))
            ),
            "hard_max_words": int(
                max(target, min(int(cap["max_safe_words"]), target * SCALE_POLICY["hard_max_factor"]))
            ),
        })
    return result


def _expand_chapter_capacities(capacities: list[dict], target_total_safe: int, report_mode: str) -> list[dict]:
    """Spread global capacity uplift back to chapters before target distribution."""
    if not capacities:
        return []
    current_total = sum(int(item.get("max_safe_words") or 0) for item in capacities)
    extra = max(0, int(target_total_safe) - current_total)
    if not extra:
        return capacities
    weights = []
    for item in capacities:
        weight = 1.0
        if item.get("evidence_capacity") == "high":
            weight += 0.8
        elif item.get("evidence_capacity") == "medium":
            weight += 0.4
        weight += min(int(item.get("matched_fact_count") or 0), 8) * 0.12
        weight += min(int(item.get("inference_count") or 0), 5) * 0.15
        weights.append(weight)
    total_weight = sum(weights) or 1.0
    expanded = []
    assigned_extra = 0
    for idx, (item, weight) in enumerate(zip(capacities, weights)):
        if idx == len(capacities) - 1:
            add = extra - assigned_extra
        else:
            add = int(extra * weight / total_weight)
            assigned_extra += add
        next_item = dict(item)
        next_item["max_safe_words"] = int(next_item.get("max_safe_words") or 0) + max(0, add)
        ratio = 0.72 if report_mode == "requirement_summary" else 0.78
        next_item["recommended_words"] = max(
            SCALE_POLICY["chapter_min_words"],
            int(next_item["max_safe_words"] * ratio),
        )
        next_item["evidence_capacity"] = (
            "high" if next_item["max_safe_words"] >= 900
            else "medium" if next_item["max_safe_words"] >= 450
            else "low"
        )
        expanded.append(next_item)
    return expanded


def _scale_reason(profile: dict, requested: int | None, max_safe: int, severe_conflict: bool) -> str:
    if severe_conflict:
        if profile.get("report_mode") == "requirement_summary":
            return "用户目标明显超过当前边界型材料可支撑的安全规模；系统将保留目标记录，但按证据容量生成，禁止虚构未被证明的内容。"
        return "用户目标明显超过当前事实、推断和材料重复度可支撑的安全规模；需要补充更多可验证材料。"
    if requested and requested > max_safe:
        return "用户目标略高于证据安全上限，系统已自动压到安全规模。"
    return "用户目标、证据容量与章节结构可以自动协调。"
