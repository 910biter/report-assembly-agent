"""Domain-neutral workload classification shared by capture and replay tools."""
from __future__ import annotations

from typing import Any


_WORKLOAD_CONTRACTS: dict[str, dict[str, Any]] = {
    "document_understanding": {"service_class": "foreground_batch", "dominant_bound": "prefill_and_cpu_preprocess", "quality_gate": "structured_output_valid"},
    "evidence_extraction": {"service_class": "critical_path_batch", "dominant_bound": "prefill_bandwidth", "quality_gate": "traceable_fact_recall"},
    "quality_guardrail": {"service_class": "critical_path_batch", "dominant_bound": "prefill_bandwidth", "quality_gate": "conflict_recall"},
    "reasoning_planning": {"service_class": "critical_path_batch", "dominant_bound": "mixed_reasoning", "quality_gate": "plan_schema_and_coverage"},
    "document_generation": {"service_class": "critical_path_generation", "dominant_bound": "decode_throughput", "quality_gate": "traceability_and_readability"},
    "post_review_quality_or_memory": {"service_class": "background", "dominant_bound": "mixed", "quality_gate": "no_critical_issue_missed"},
    "interactive_assistant": {"service_class": "interactive", "dominant_bound": "queue_and_ttft", "quality_gate": "instruction_fidelity"},
    "template_style_learning": {"service_class": "offline_asset_build", "dominant_bound": "prefill_and_cpu_preprocess", "quality_gate": "style_schema_coverage"},
    "knowledge_graph_construction": {"service_class": "background", "dominant_bound": "prefill_and_cpu_postprocess", "quality_gate": "grounded_relation_precision"},
    "micro_context_decode": {"service_class": "synthetic", "dominant_bound": "parameterized", "quality_gate": "request_success"},
    "other": {"service_class": "unspecified", "dominant_bound": "unknown", "quality_gate": "request_success"},
}


def workload_contract(workload: str) -> dict[str, Any]:
    """Describe measurement semantics without embedding domain-specific SLA numbers."""
    return dict(_WORKLOAD_CONTRACTS.get(str(workload or ""), _WORKLOAD_CONTRACTS["other"]))


def workload_kind(stage: str, agent: str = "") -> str:
    """Map workflow mechanics, not business semantics, to a benchmark class."""
    normalized_stage = str(stage or "").strip().lower()
    normalized_agent = str(agent or "").strip().lower()
    if normalized_stage in {"material_analysis", "parse"}:
        return "document_understanding"
    if normalized_stage == "evidence":
        return "evidence_extraction"
    if normalized_stage == "conflict":
        return "quality_guardrail"
    if normalized_stage in {"planning", "analysis", "final_planning", "narrative_planning"}:
        return "reasoning_planning"
    if normalized_stage == "writing":
        return "document_generation"
    if normalized_stage in {"knowledge", "qa"} or normalized_agent in {"qa", "knowledge"}:
        return "post_review_quality_or_memory"
    if normalized_stage in {"interaction", "interactive", "collaboration"}:
        return "interactive_assistant"
    if normalized_stage in {"style_probe", "style_profile", "template"}:
        return "template_style_learning"
    if normalized_stage in {"graph", "graph_build", "graph_extract"}:
        return "knowledge_graph_construction"
    return "other"


def io_pattern(input_tokens: int, output_tokens: int) -> str:
    ratio = int(input_tokens or 0) / max(int(output_tokens or 0), 1)
    if ratio >= 3:
        return "long_input_short_output"
    if ratio <= 0.75:
        return "short_input_long_output"
    return "balanced_input_output"
