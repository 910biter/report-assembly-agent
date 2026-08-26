"""Domain-neutral workload classification shared by capture and replay tools."""
from __future__ import annotations


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
