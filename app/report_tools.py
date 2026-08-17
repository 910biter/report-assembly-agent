"""Runtime task understanding and cross-domain business QA.

The core system keeps fixed data structures, not fixed business semantics.
Material roles, boundaries and missing information come from runtime material
understanding instead of hard-coded domain knowledge.
"""
from __future__ import annotations

import json
import re

ACTUAL_SUPPORT = {"actual"}
NON_ACTUAL_SUPPORT = {"normative", "template", "reference", "unknown"}


def build_task_profile(theme: str, insights: list[dict]) -> dict:
    """Build RuntimeTaskProfile from current material understanding."""
    enriched = []
    role_counts: dict[str, int] = {}
    support_counts: dict[str, int] = {}
    missing: list[str] = []
    boundaries: list[str] = []
    for insight in insights or []:
        item = dict(insight)
        role = _clean_role(item.get("material_role") or item.get("doc_type") or "未分类材料")
        support = _normalize_support(item.get("claim_support"))
        item["material_role"] = role
        item["material_role_label"] = role
        item["claim_support"] = support
        item["allowed_usage"] = [str(v) for v in item.get("allowed_usage") or []]
        item["forbidden_usage"] = [str(v) for v in item.get("forbidden_usage") or []]
        item["missing_information"] = [str(v) for v in item.get("missing_information") or []]
        role_counts[role] = role_counts.get(role, 0) + 1
        support_counts[support] = support_counts.get(support, 0) + 1
        missing.extend(item["missing_information"])
        boundaries.extend(item["forbidden_usage"])
        enriched.append(item)

    has_actual = bool(support_counts.get("actual"))
    report_mode = "evidence_report" if has_actual else "requirement_summary"
    return {
        "domain": "runtime",
        "task_intent": theme or "",
        "report_mode": report_mode,
        "has_actual_evidence": has_actual,
        "material_roles": role_counts,
        "support_counts": support_counts,
        "actual_roles": sorted({i["material_role"] for i in enriched if i["claim_support"] == "actual"}),
        "normative_roles": sorted({i["material_role"] for i in enriched if i["claim_support"] in NON_ACTUAL_SUPPORT}),
        "missing_inputs": _dedup_text(missing),
        "evidence_boundaries": _dedup_text(boundaries),
        "business_guardrails": _runtime_guardrails(has_actual, boundaries),
        "enriched_insights": enriched,
        "role_matrix": build_role_matrix(enriched),
    }


def build_role_matrix(insights: list[dict]) -> list[dict]:
    matrix = []
    for insight in insights or []:
        role = _clean_role(insight.get("material_role") or insight.get("doc_type") or "未分类材料")
        support = _normalize_support(insight.get("claim_support"))
        matrix.append({
            "material_id": insight.get("material_id"),
            "filename": insight.get("filename", ""),
            "role": role,
            "role_label": role,
            "claim_support": support,
            "allowed_usage": [str(v) for v in insight.get("allowed_usage") or []],
            "forbidden_usage": [str(v) for v in insight.get("forbidden_usage") or []],
        })
    return matrix


def business_block(profile: dict) -> str:
    if not profile:
        return ""
    lines = [
        "运行时任务理解:",
        f"任务意图: {profile.get('task_intent', '') or '未明确'}",
        f"报告模式: {profile.get('report_mode', 'generic')}",
        f"材料支撑结构: {json.dumps(profile.get('support_counts') or {}, ensure_ascii=False)}",
    ]
    if profile.get("material_roles"):
        lines.append("材料角色(由当前材料理解得到):")
        lines.extend(f"- {role}: {count} 份" for role, count in sorted(profile["material_roles"].items()))
    if profile.get("missing_inputs"):
        lines.append("当前材料暴露的缺失信息:")
        lines.extend(f"- {item}" for item in profile["missing_inputs"][:8])
    if profile.get("evidence_boundaries"):
        lines.append("证据边界:")
        lines.extend(f"- {item}" for item in profile["evidence_boundaries"][:8])
    if profile.get("business_guardrails"):
        lines.append("业务写作底线:")
        lines.extend(f"- {item}" for item in profile["business_guardrails"])
    return "\n".join(lines)


def build_chapter_evidence_matrix(plan: dict, profile: dict, facts: list[dict],
                                  inferences: list[dict] | None = None) -> dict:
    if not isinstance(plan, dict):
        plan = getattr(plan, "__dict__", {}) or {}
    chapter_plans = plan.get("chapter_plans") or [{"title": title} for title in plan.get("structure", [])]
    facts = facts or []
    inferences = inferences or []
    inference_pool = {str(i.get("content", "")) for i in inferences}
    chapters = []
    total_required = 0
    total_covered = 0
    for chapter in chapter_plans:
        title = str(chapter.get("title", ""))
        required = [str(item) for item in chapter.get("required_facts", []) if str(item).strip()]
        allowed_roles = [str(item) for item in chapter.get("allowed_roles", []) if str(item).strip()]
        covered, missing = [], []
        support_fact_ids: set[int] = set()
        for requirement in required:
            matched = _match_requirement(requirement, facts, allowed_roles)
            if matched:
                covered.append(requirement)
                support_fact_ids.update(int(f["id"]) for f in matched if f.get("id") is not None)
            elif requirement in inference_pool:
                covered.append(requirement)
            else:
                missing.append(requirement)
        if not support_fact_ids:
            support_fact_ids.update(_chapter_related_fact_ids(chapter, facts, allowed_roles))
        total_required += len(required)
        total_covered += len(covered)
        chapters.append({
            "title": title,
            "required_facts": required,
            "allowed_roles": allowed_roles,
            "covered": covered,
            "missing": missing,
            "support_fact_ids": sorted(support_fact_ids),
            "coverage": round(len(covered) / max(len(required), 1), 2),
            "can_generate_assertive_claims": _can_generate_assertive_claims(profile, support_fact_ids, facts),
            "guardrails": _chapter_guardrails(profile, missing),
        })
    missing_inputs = list(profile.get("missing_inputs") or [])
    coverage = round(total_covered / max(total_required, 1), 2)
    return {
        "domain": profile.get("domain", "runtime"),
        "report_mode": profile.get("report_mode", "generic"),
        "coverage": coverage,
        "status": "ready" if coverage >= 0.8 and not missing_inputs else "needs_materials" if missing_inputs else "partial",
        "chapters": chapters,
        "missing_inputs": missing_inputs,
        "hard_rules": list(profile.get("business_guardrails") or []),
        "role_matrix": profile.get("role_matrix") or build_role_matrix(profile.get("enriched_insights") or []),
    }


def looks_like_template_meta(text: str) -> bool:
    return any(key in (text or "") for key in ("模板要求", "报告模板", "报告编制规范", "工作要求对", "需围绕"))


def _match_requirement(requirement: str, facts: list[dict], allowed_roles: list[str]) -> list[dict]:
    terms = _terms(requirement)
    allowed = set(allowed_roles or [])
    matched = []
    for fact in facts:
        roles = set(fact.get("source_roles") or [])
        if allowed and roles and not (roles & allowed):
            continue
        text = _fact_match_text(fact)
        score = 3 if requirement in text else sum(1 for term in terms if term in text)
        score += int(_ngram_similarity(requirement, text) * 10)
        if score:
            matched.append((score, fact))
    matched.sort(key=lambda item: item[0], reverse=True)
    return [fact for _score, fact in matched[:8]]


def _chapter_related_fact_ids(chapter: dict, facts: list[dict], allowed_roles: list[str]) -> list[int]:
    seed = " ".join(
        str(item)
        for item in [
            chapter.get("title", ""),
            chapter.get("judgment", ""),
            *(chapter.get("questions") or []),
            *(chapter.get("required_facts") or []),
        ]
    )
    terms = _terms(seed)
    allowed = set(allowed_roles or [])
    scored: list[tuple[int, int]] = []
    for fact in facts or []:
        if fact.get("id") is None:
            continue
        roles = set(fact.get("source_roles") or [])
        if allowed and roles and not (roles & allowed):
            continue
        text = _fact_match_text(fact)
        score = sum(1 for term in terms if term and term in text)
        score += int(_ngram_similarity(seed, text) * 10)
        if score:
            scored.append((score, int(fact["id"])))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [fid for _score, fid in scored[:8]]


def _fact_match_text(fact: dict) -> str:
    return " ".join(
        str(part)
        for part in [
            fact.get("dimension", ""),
            fact.get("fact_type", ""),
            fact.get("content", ""),
            " ".join(fact.get("source_roles") or []),
            " ".join(fact.get("source_files") or []),
        ]
    )


def _can_generate_assertive_claims(profile: dict, support_fact_ids: set[int], facts: list[dict]) -> bool:
    if profile.get("has_actual_evidence"):
        by_id = {int(f.get("id")): f for f in facts if f.get("id") is not None}
        return any(_fact_support(by_id.get(fid, {})) == "actual" for fid in support_fact_ids)
    return False


def _chapter_guardrails(profile: dict, missing: list[str]) -> list[str]:
    rules = list(profile.get("business_guardrails") or [])
    if missing:
        rules.append("缺失证据项必须显式转写为边界、风险或待补事项: " + "、".join(missing))
    return rules


def _runtime_guardrails(has_actual: bool, boundaries: list[str]) -> list[str]:
    rules = [
        "不得把模板、表单、说明性或规范性材料写成已经发生的事实或成果。",
        "材料不能证明的信息必须写成边界、风险或待补事项,不得补写为确定结论。",
    ]
    if not has_actual:
        rules.append("当前未识别到实际过程/结果类证据时,不得生成已完成成果、成效提升或量化业绩。")
    rules.extend(str(item) for item in boundaries[:5] if item)
    return _dedup_text(rules)


def _normalize_support(value) -> str:
    text = str(value or "").strip().lower()
    if text in {"actual", "normative", "template", "reference", "unknown"}:
        return text
    return "unknown"


def _fact_support(fact: dict) -> str:
    supports = set(fact.get("claim_supports") or [])
    if "actual" in supports:
        return "actual"
    if supports & NON_ACTUAL_SUPPORT:
        return sorted(supports & NON_ACTUAL_SUPPORT)[0]
    return "unknown"


def _clean_role(role) -> str:
    return re.sub(r"\s+", " ", str(role or "未分类材料")).strip()[:40] or "未分类材料"


def _terms(text: str) -> list[str]:
    return [term for term in re.split(r"[、/，,；;()\s]+", text or "") if len(term) >= 2]


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


def _looks_like_result_claim(text: str) -> bool:
    return bool(re.search(r"(取得|形成|完成|实现|提升|增长|降低|达成|产出|获得).{0,12}(成果|成效|结果|指标|数据|影响|收益)", text or ""))


def _dedup_text(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dedup_issues(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = (item.get("type"), item.get("section"), item.get("quote"), item.get("note"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
