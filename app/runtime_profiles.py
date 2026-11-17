"""Workload-specific LLM capacity contracts derived from one physical window.

These names describe model workload profiles, not persisted workflow stages.
The workflow state protocol lives in ``app.workflow.stages``.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.model_connection import capacity


@dataclass(frozen=True)
class StageRuntimeProfile:
    name: str
    workload: str
    batch_policy: str
    latency_class: str
    input_cap_setting: str = ""

    @property
    def output_tokens(self) -> int:
        window, service_output_limit = capacity()
        usable = window - int(settings.prompt_overhead_tokens) - int(settings.safety_margin_tokens)
        if usable < 2:
            raise ValueError(f"Stage {self.name} has no usable model capacity")
        return min(service_output_limit, max(1, usable * 55 // 100))

    @property
    def physical_input_tokens(self) -> int:
        window = capacity()[0]
        usable = window - int(settings.prompt_overhead_tokens) - int(settings.safety_margin_tokens)
        return min(usable * 45 // 100, usable - self.output_tokens)

    @property
    def input_tokens(self) -> int:
        available = self.physical_input_tokens
        if available < 1024:
            raise ValueError(
                f"Stage {self.name} leaves only {available} input tokens; "
                "increase the physical window or reduce output/overhead reserves"
            )
        if self.input_cap_setting:
            available = min(available, int(getattr(settings, self.input_cap_setting)))
        return available

_PROFILES = {
    "structured": StageRuntimeProfile("structured", "balanced_structured", "single", "normal"),
    "material_analyzer": StageRuntimeProfile("material_analyzer", "representative_document_input", "document_batches", "background", "material_analysis_input_tokens"),
    "planner": StageRuntimeProfile("planner", "medium_input_structured_output", "single", "normal"),
    "final_planner": StageRuntimeProfile("final_planner", "medium_input_structured_output", "single", "normal"),
    "evidence": StageRuntimeProfile("evidence", "long_input_structured_output", "coverage_batches", "throughput"),
    "conflict": StageRuntimeProfile("conflict", "candidate_input_short_output", "candidate_groups", "quality"),
    "analysis": StageRuntimeProfile("analysis", "medium_input_reasoning_output", "dimension_map_reduce", "quality"),
    "narrative": StageRuntimeProfile("narrative", "medium_input_structured_output", "chapter", "quality"),
    "narrative_qa": StageRuntimeProfile("narrative_qa", "medium_input_short_output", "chapter", "quality"),
    "writer": StageRuntimeProfile("writer", "medium_input_long_output", "subsection", "quality"),
    "qa": StageRuntimeProfile("qa", "medium_input_short_output", "section", "quality"),
    "graph": StageRuntimeProfile("graph", "fact_input_structured_output", "adaptive_fact_batches", "background"),
    "interaction": StageRuntimeProfile("interaction", "short_input_short_output", "single", "interactive", "interactive_input_tokens"),
    "comparison": StageRuntimeProfile("comparison", "candidate_input_structured_output", "candidate_batches", "quality"),
    "style_probe": StageRuntimeProfile("style_probe", "sample_input_short_output", "sample", "background"),
    "style_profile": StageRuntimeProfile("style_profile", "sample_input_structured_output", "sample_bank", "background"),
}

_ALIASES = {"base": "structured", "graph_extract": "graph", "template": "style_profile"}


def stage_profile(name: str) -> StageRuntimeProfile:
    raw = str(name or "").strip()
    return _PROFILES.get(_ALIASES.get(raw, raw), _PROFILES["structured"])


def stage_input_budget_tokens(name: str) -> int:
    return stage_profile(name).input_tokens


def runtime_profile_manifest() -> list[dict]:
    return [{
        "stage": profile.name,
        "workload": profile.workload,
        "input_tokens": profile.input_tokens,
        "physical_input_tokens": profile.physical_input_tokens,
        "reserved_unused_tokens": profile.physical_input_tokens - profile.input_tokens,
        "output_tokens": profile.output_tokens,
        "batch_policy": profile.batch_policy,
        "latency_class": profile.latency_class,
    } for profile in _PROFILES.values()]
