"""Deterministic dependency contracts for resumable workflow stages.

Task payload fields describe the latest UI state. They are not sufficient to
prove that an expensive stage still belongs to the current task inputs. These
small signatures make reuse explicit without coupling the workflow to a
particular business domain.
"""
from __future__ import annotations

from typing import Any

from app.cache import stable_hash


STAGE_CONTRACT_VERSION = "workflow-stage-contract-1"


def stage_input_signature(stage: str, task: dict, *, plan: dict | None = None) -> str:
    """Hash only the effective inputs that can change a stage's result."""
    common = {
        "contract": STAGE_CONTRACT_VERSION,
        "stage": stage,
        "theme": str(task.get("theme") or ""),
        "requirements": str(task.get("user_requirements") or ""),
        "material_ids": _int_list(task.get("material_ids")),
        "run_mode": str(task.get("run_mode") or "initial"),
        "variant_id": _optional_int(task.get("variant_id")),
    }
    plan = plan or {}
    if stage == "evidence":
        common.update({
            "plan_id": _optional_int(plan.get("id") or task.get("plan_id")),
            "analysis_plan": plan.get("analysis_plan_json") or {
                "dimensions": plan.get("dimensions") or [],
                "evidence_needs": plan.get("evidence_needs") or [],
                "required_facts": plan.get("required_facts") or [],
            },
            "added_material_ids": _int_list(task.get("incremental_added_material_ids")),
            "evidence_recheck": task.get("intervention_evidence_recheck") or {},
        })
    elif stage == "conflict":
        common.update({
            "plan_id": _optional_int(plan.get("id") or task.get("plan_id")),
            "fact_ids": _int_list(task.get("fact_ids")),
            "claim_ids": _int_list(task.get("claim_ids")),
            "incremental_inherited_conflict_ids": _int_list(task.get("incremental_inherited_conflict_ids")),
        })
    elif stage == "analysis":
        common.update({
            "plan_id": _optional_int(plan.get("id") or task.get("plan_id")),
            "fact_ids": _int_list(task.get("fact_ids")),
            "conflict_ids": _int_list(task.get("conflict_ids")),
            "inference_recheck": task.get("intervention_inference_recheck") or {},
        })
    elif stage == "final_plan":
        common.update({
            "plan_id": _optional_int(plan.get("id") or task.get("plan_id")),
            "analysis_plan": plan.get("analysis_plan_json") or {},
            "fact_ids": _int_list(task.get("fact_ids")),
            "inference_ids": _int_list(task.get("inference_ids")),
            "external_ids": _int_list(task.get("external_ids")),
            "required_structure": task.get("intervention_required_structure") or [],
            "required_chapter_count": int(task.get("intervention_required_chapter_count") or 0),
        })
    elif stage == "writing":
        common.update({
            "plan_id": _optional_int(plan.get("id") or task.get("plan_id")),
            "plan_version": int(plan.get("plan_version") or 0),
            "final_plan": plan.get("final_plan_json") or {
                "structure": plan.get("structure") or [],
                "chapter_plans": plan.get("chapter_plans") or [],
                "budget": plan.get("budget") or {},
            },
            "fact_ids": _int_list(task.get("fact_ids")),
            "inference_ids": _int_list(task.get("inference_ids")),
            "external_ids": _int_list(task.get("external_ids")),
            "report_policy": task.get("report_policy") or {},
            "target_scope": task.get("intervention_target_scope") or {},
            "incremental_reason": str(task.get("incremental_update_reason") or ""),
        })
    else:
        raise ValueError(f"UNSUPPORTED_STAGE_SIGNATURE: {stage}")
    return stable_hash(common)


def signature_matches(task: dict, stage: str, expected: str) -> bool:
    return bool(expected) and str(task.get(f"{stage}_input_signature") or "") == expected


def _int_list(values: Any) -> list[int]:
    result: list[int] = []
    for value in values or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(result))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None
