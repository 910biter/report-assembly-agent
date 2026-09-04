"""Single source of truth for collaboration-agent change propagation."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.control_agent import AgentToolName


@dataclass(frozen=True)
class PropagationPolicy:
    recompute_rank: int
    recompute_from: str
    invalidates: tuple[str, ...]
    scope_kind: str
    label: str
    force_evidence: bool = False
    force_analysis: bool = False
    force_final_plan: bool = False
    rerun_initial_plan: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


_POLICIES_BY_TOOL = {
    AgentToolName.REVISE_TASK_REQUIREMENTS.value: PropagationPolicy(
        1, "planning",
        ("analysis_plan", "evidence", "analysis", "final_plan", "narrative_plan", "writing", "qa"),
        "task", "从分析规划重新计算", True, True, True, True,
    ),
    AgentToolName.REVISE_ANALYSIS_PLAN.value: PropagationPolicy(
        2, "evidence",
        ("evidence", "analysis", "final_plan", "narrative_plan", "writing", "qa"),
        "task", "从事实与证据重新计算", True, True, True, True,
    ),
    AgentToolName.RECHECK_FACT.value: PropagationPolicy(
        2, "evidence",
        ("evidence", "conflict", "analysis", "final_plan", "narrative_plan", "writing", "qa"),
        "fact", "复核该事实及其下游", True, True, True,
    ),
    AgentToolName.RECHECK_INFERENCE.value: PropagationPolicy(
        3, "analysis",
        ("analysis", "final_plan", "narrative_plan", "writing", "qa"),
        "inference", "复核该分析判断及其下游", False, True, True,
    ),
    AgentToolName.RERUN_FINAL_PLAN.value: PropagationPolicy(
        4, "final_plan", ("final_plan", "narrative_plan", "writing", "qa"),
        "structure", "重组目录及相关正文", False, False, True,
    ),
    AgentToolName.REGENERATE_CHAPTER.value: PropagationPolicy(
        5, "writing", ("narrative_plan", "writing", "qa"),
        "chapter", "只重组并重写指定章节",
    ),
    AgentToolName.REWRITE_PARAGRAPH.value: PropagationPolicy(
        5, "writing", ("lineage_check", "qa", "render"),
        "paragraph", "只改写指定段落",
    ),
    AgentToolName.REWRITE_SENTENCE.value: PropagationPolicy(
        5, "writing", ("lineage_check", "qa", "render"),
        "sentence", "只改写指定句子",
    ),
    AgentToolName.PAUSE_TASK.value: PropagationPolicy(99, "none", (), "control", "暂停当前任务"),
    AgentToolName.RESUME_TASK.value: PropagationPolicy(99, "none", (), "control", "恢复当前任务"),
    AgentToolName.RETRY_TASK.value: PropagationPolicy(99, "none", (), "control", "重试当前任务"),
    AgentToolName.UPDATE_SECTION_TITLES.value: PropagationPolicy(
        99, "none", ("qa", "render"), "title", "批量更新章节标题",
    ),
}

_POLICIES_BY_ARTIFACT = {
    "task_brief": _POLICIES_BY_TOOL[AgentToolName.REVISE_TASK_REQUIREMENTS.value],
    "material_role": PropagationPolicy(
        2, "evidence", ("evidence", "analysis", "final_plan", "narrative_plan", "writing", "qa"),
        "material", "从事实与证据重新计算", True, True, True,
    ),
    "analysis_plan": _POLICIES_BY_TOOL[AgentToolName.REVISE_ANALYSIS_PLAN.value],
    "fact": _POLICIES_BY_TOOL[AgentToolName.RECHECK_FACT.value],
    "inference": _POLICIES_BY_TOOL[AgentToolName.RECHECK_INFERENCE.value],
    "final_plan": _POLICIES_BY_TOOL[AgentToolName.RERUN_FINAL_PLAN.value],
    "narrative_plan": _POLICIES_BY_TOOL[AgentToolName.REGENERATE_CHAPTER.value],
    "paragraph": _POLICIES_BY_TOOL[AgentToolName.REWRITE_PARAGRAPH.value],
    "sentence": _POLICIES_BY_TOOL[AgentToolName.REWRITE_SENTENCE.value],
    "qa_issue": PropagationPolicy(5, "writing", ("qa", "render"), "quality_issue", "定点修复质量问题"),
    "comparison_item": PropagationPolicy(
        5, "writing", ("incremental_update_proposal",), "comparison", "更新该材料变化项",
    ),
    "report_title": PropagationPolicy(99, "none", ("qa", "render"), "title", "更新报告标题"),
    "section_title": PropagationPolicy(99, "none", ("qa", "render"), "title", "更新章节标题"),
    "section_titles": PropagationPolicy(99, "none", ("qa", "render"), "title", "批量更新章节标题"),
    "task_control": PropagationPolicy(99, "none", (), "control", "执行任务操作"),
    "task_draft": PropagationPolicy(99, "none", (), "draft", "更新新建任务草稿"),
}


def propagation_policy(tool_name: str = "", artifact_type: str = "") -> PropagationPolicy:
    """Resolve one auditable policy without inferring domain semantics."""
    if tool_name and tool_name in _POLICIES_BY_TOOL:
        return _POLICIES_BY_TOOL[tool_name]
    return _POLICIES_BY_ARTIFACT.get(
        artifact_type,
        PropagationPolicy(5, "writing", ("qa", "render"), "artifact", "检查当前产物"),
    )
