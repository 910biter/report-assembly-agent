"""Task-scoped control agent contracts and deterministic tool registry.

The control agent never generates workflow artifacts itself. It reads audited
artifacts and dispatches commands to the existing workflow services.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.db import session_scope
from app.infrastructure.orm import (
    ORMConflict, ORMEvidence, ORMFact, ORMInference, ORMInsight, ORMMaterial,
    ORMPlan, ORMSentence, ORMUnit,
)
from app.memory import short_term
from app.models import Unit
from app.retrieval.rag import hybrid_retrieve_units
from app.retrieval.embedder import embed_texts
from app.retrieval.store import vector_store
from app.workflow.queue import request_control, task_queue_status


class AgentToolName(str, Enum):
    GET_TASK_OVERVIEW = "get_task_overview"
    GET_TASK_MAP = "get_task_map"
    GET_MATERIALS = "get_materials"
    SEARCH_MATERIAL_UNITS = "search_material_units"
    LIST_FACTS = "list_facts"
    SEARCH_FACT_EVIDENCE = "search_fact_evidence"
    GET_FACT_EVIDENCE = "get_fact_evidence"
    LIST_INFERENCES = "list_inferences"
    GET_INFERENCE = "get_inference"
    GET_CONFLICTS = "get_conflicts"
    GET_ANALYSIS_PLAN = "get_analysis_plan"
    GET_FINAL_PLAN = "get_final_plan"
    GET_QUALITY_ISSUES = "get_quality_issues"
    GET_REPORT_OUTLINE = "get_report_outline"
    GET_REPORT_SECTION = "get_report_section"
    GET_REPORT_SENTENCE = "get_report_sentence"
    PAUSE_TASK = "pause_task"
    RESUME_TASK = "resume_task"
    RETRY_TASK = "retry_task"
    CONFIRM_REQUIREMENTS = "confirm_requirements"
    CONFIRM_DIRECTORY = "confirm_directory"
    REVISE_TASK_REQUIREMENTS = "revise_task_requirements"
    REVISE_ANALYSIS_PLAN = "revise_analysis_plan"
    RECHECK_FACT = "recheck_fact"
    RECHECK_INFERENCE = "recheck_inference"
    REWRITE_SENTENCE = "rewrite_sentence"
    REWRITE_PARAGRAPH = "rewrite_paragraph"
    UPDATE_REPORT_TITLE = "update_report_title"
    UPDATE_SECTION_TITLE = "update_section_title"
    UPDATE_SECTION_TITLES = "update_section_titles"
    REGENERATE_CHAPTER = "regenerate_chapter"
    RERUN_FINAL_PLAN = "rerun_final_plan"
    LOCATE_QUALITY_ISSUE = "locate_quality_issue"


class ArtifactFocus(BaseModel):
    artifact_type: str = "task_brief"
    object_id: str = ""
    title: str = "当前任务"
    artifact_version: str = ""
    current: dict[str, Any] = Field(default_factory=dict)
    references: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("object_id", "artifact_version", mode="before")
    @classmethod
    def normalize_identifier(cls, value: Any) -> str:
        """UI and historical rows may carry numeric ids; the protocol is textual."""
        return "" if value is None else str(value)


class TaskAgentContext(BaseModel):
    task_id: str
    report_id: int | None = None
    theme: str = ""
    user_requirements: str = ""
    stage: str = "created"
    stage_label: str = "等待开始"
    workflow_mode: str = "automatic"
    requirement_review_pending: bool = False
    directory_review_pending: bool = False
    queue_status: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    artifact_counts: dict[str, int] = Field(default_factory=dict)
    available_artifacts: list[str] = Field(default_factory=list)
    workflow_map: list[dict[str, str]] = Field(default_factory=list)
    current_focus: ArtifactFocus = Field(default_factory=ArtifactFocus)
    error: str = ""


class AgentToolCall(BaseModel):
    tool_name: AgentToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmation_required: bool = False
    reason: str = ""


_STAGE_LABELS = {
    "created": "等待开始", "parsing": "材料解析", "dedup": "去重归并",
    "material_analysis": "材料理解", "requirement_review": "需求讨论", "planning": "分析规划", "evidence": "事实与证据",
    "conflict": "冲突核验", "analysis": "综合分析", "directory_review": "目录讨论", "writing": "报告生成",
    "review": "等待审核", "done": "已完成",
    "paused": "已暂停", "failed": "运行异常",
}

_WORKFLOW_MAP = [
    {"stage": "parsing", "purpose": "将材料转换为带页码和来源位置的内容单元", "artifact": "Units"},
    {"stage": "material_analysis", "purpose": "判断材料角色、可证明范围和缺失信息", "artifact": "MaterialInsight"},
    {"stage": "requirement_review", "purpose": "基于材料理解与用户共同确定报告目标和要求", "artifact": "TaskRequirements"},
    {"stage": "planning", "purpose": "规划分析问题与证据需求，不提前冻结最终目录", "artifact": "AnalysisPlan"},
    {"stage": "evidence", "purpose": "提取事实并绑定原始证据", "artifact": "Fact/Evidence"},
    {"stage": "conflict", "purpose": "核验多来源对同一事项的矛盾", "artifact": "Conflict"},
    {"stage": "analysis", "purpose": "基于事实形成带置信度的推论", "artifact": "Inference"},
    {"stage": "final_plan", "purpose": "根据事实和推论形成最终内容结构", "artifact": "FinalReportPlan"},
    {"stage": "directory_review", "purpose": "让用户审阅并调整最终目录", "artifact": "FinalReportPlan"},
    {"stage": "narrative", "purpose": "组织章节主线、话题和逻辑顺序", "artifact": "NarrativePlan"},
    {"stage": "writing", "purpose": "调用 Writer 按章节自然成文并绑定来源", "artifact": "Report/ChapterDraft"},
    {"stage": "qa", "purpose": "发现事实、结构、语言和格式问题", "artifact": "QAResult"},
]

_TOOL_DEFINITIONS = {
    AgentToolName.GET_TASK_OVERVIEW: (False, "读取任务状态、进度、异常和产物数量"),
    AgentToolName.GET_TASK_MAP: (False, "读取任务全景、当前产物摘要和待处理事项"),
    AgentToolName.GET_MATERIALS: (False, "读取任务材料及材料理解结果"),
    AgentToolName.SEARCH_MATERIAL_UNITS: (False, "按问题检索当前任务材料的原始片段和来源位置"),
    AgentToolName.LIST_FACTS: (False, "浏览当前任务事实摘要，并按关键词或维度筛选"),
    AgentToolName.SEARCH_FACT_EVIDENCE: (False, "按问题混合检索任务事实及其证据"),
    AgentToolName.GET_FACT_EVIDENCE: (False, "读取任务内指定事实及原始证据"),
    AgentToolName.LIST_INFERENCES: (False, "浏览当前任务推论摘要，并按关键词、维度或置信度筛选"),
    AgentToolName.GET_INFERENCE: (False, "读取任务内指定推论、置信度和依据事实"),
    AgentToolName.GET_CONFLICTS: (False, "读取任务内冲突双方及核验状态"),
    AgentToolName.GET_ANALYSIS_PLAN: (False, "读取已落库的分析规划"),
    AgentToolName.GET_FINAL_PLAN: (False, "读取已落库的最终报告目录"),
    AgentToolName.GET_QUALITY_ISSUES: (False, "读取质量问题及报告定位"),
    AgentToolName.GET_REPORT_OUTLINE: (False, "读取报告章节和段落概览"),
    AgentToolName.GET_REPORT_SECTION: (False, "读取指定章节正文与引用概况"),
    AgentToolName.GET_REPORT_SENTENCE: (False, "读取指定报告句子及其事实、推论引用"),
    AgentToolName.LOCATE_QUALITY_ISSUE: (False, "定位质量问题对应句子或章节"),
    AgentToolName.PAUSE_TASK: (True, "在安全边界暂停任务"),
    AgentToolName.RESUME_TASK: (True, "从断点恢复任务"),
    AgentToolName.RETRY_TASK: (True, "从失败或暂停阶段重试任务"),
    AgentToolName.CONFIRM_REQUIREMENTS: (True, "确认已讨论的任务需求并继续分析规划"),
    AgentToolName.CONFIRM_DIRECTORY: (True, "确认当前目录并继续叙事组织和写作"),
    AgentToolName.REVISE_TASK_REQUIREMENTS: (True, "更新任务主题或报告要求，并从分析规划重新计算"),
    AgentToolName.REVISE_ANALYSIS_PLAN: (True, "调用 Planner 调整分析问题与证据需求"),
    AgentToolName.RECHECK_FACT: (True, "回到 Evidence 复核指定事实及其证据"),
    AgentToolName.RECHECK_INFERENCE: (True, "回到 Analysis 复核指定推论及其依据"),
    AgentToolName.REWRITE_SENTENCE: (True, "调用 Writer 在证据约束下改写指定句子"),
    AgentToolName.REWRITE_PARAGRAPH: (True, "调用 Writer 在证据约束下改写指定段落"),
    AgentToolName.UPDATE_REPORT_TITLE: (True, "修改报告主标题"),
    AgentToolName.UPDATE_SECTION_TITLE: (True, "修改现有章节标题并同步目录"),
    AgentToolName.UPDATE_SECTION_TITLES: (True, "批量修改现有章节标题并同步目录"),
    AgentToolName.REGENERATE_CHAPTER: (True, "调用 Narrative Plan 与 Writer 重写指定章节"),
    AgentToolName.RERUN_FINAL_PLAN: (True, "调用 Final Planner 重组目录及其下游"),
}

_MUTATING_ARGUMENT_KEYS = {
    AgentToolName.REVISE_TASK_REQUIREMENTS: {"updated_theme", "updated_requirements", "instruction"},
    AgentToolName.REVISE_ANALYSIS_PLAN: {"instruction", "required_dimensions", "required_dimension_count"},
    AgentToolName.RECHECK_FACT: {"fact_id", "instruction"},
    AgentToolName.RECHECK_INFERENCE: {"inference_id", "instruction"},
    AgentToolName.REWRITE_SENTENCE: {
        "sentence_id", "instruction", "reference_fact_ids", "reference_inference_ids",
    },
    AgentToolName.REWRITE_PARAGRAPH: {
        "chapter_title", "paragraph", "sentence_ids", "instruction",
        "reference_fact_ids", "reference_inference_ids",
    },
    AgentToolName.UPDATE_REPORT_TITLE: {"new_title", "instruction"},
    AgentToolName.UPDATE_SECTION_TITLE: {"old_title", "new_title", "instruction"},
    AgentToolName.UPDATE_SECTION_TITLES: {"changes", "instruction"},
    AgentToolName.REGENERATE_CHAPTER: {"chapter_title", "instruction"},
    AgentToolName.RERUN_FINAL_PLAN: {"instruction", "new_structure", "required_chapter_count"},
    AgentToolName.PAUSE_TASK: set(),
    AgentToolName.RESUME_TASK: set(),
    AgentToolName.RETRY_TASK: set(),
    AgentToolName.CONFIRM_REQUIREMENTS: set(),
    AgentToolName.CONFIRM_DIRECTORY: {"feedback"},
}


def tool_manifest() -> list[dict[str, Any]]:
    manifests = []
    for name, config in _TOOL_DEFINITIONS.items():
        item = {"name": name.value, "confirmation_required": config[0], "description": config[1]}
        if name == AgentToolName.RERUN_FINAL_PLAN:
            item["arguments"] = {
                "instruction": "用户对目录调整的完整要求",
                "new_structure": ["用户明确确认的章节标题，按顺序填写；未确认具体标题时留空"],
                "required_chapter_count": "用户明确要求的章节数量；未明确时为 0",
            }
        elif name == AgentToolName.REWRITE_SENTENCE:
            item["arguments"] = {
                "sentence_id": "当前报告中的句子 ID",
                "instruction": "只针对该句的具体修改要求",
                "reference_fact_ids": ["用户额外引用的事实 ID"],
                "reference_inference_ids": ["用户额外引用的推论 ID"],
            }
        elif name == AgentToolName.REWRITE_PARAGRAPH:
            item["arguments"] = {
                "chapter_title": "段落所属章节",
                "paragraph": "段落序号",
                "sentence_ids": ["段落内句子 ID"],
                "instruction": "只针对该段的具体修改要求",
                "reference_fact_ids": ["用户额外引用的事实 ID"],
                "reference_inference_ids": ["用户额外引用的推论 ID"],
            }
        elif name == AgentToolName.REGENERATE_CHAPTER:
            item["arguments"] = {"chapter_title": "现有章节标题", "instruction": "本轮具体修改要求"}
        elif name == AgentToolName.UPDATE_REPORT_TITLE:
            item["arguments"] = {"new_title": "新的完整报告标题", "instruction": "标题调整要求"}
        elif name == AgentToolName.UPDATE_SECTION_TITLE:
            item["arguments"] = {
                "old_title": "最终目录中现有的章节标题",
                "new_title": "新的章节标题",
                "instruction": "标题调整要求",
            }
        elif name == AgentToolName.UPDATE_SECTION_TITLES:
            item["arguments"] = {
                "changes": [{
                    "old_title": "最终目录中现有的章节标题",
                    "new_title": "对应的新的章节标题",
                }],
                "instruction": "批量标题调整要求",
            }
        elif name == AgentToolName.REVISE_TASK_REQUIREMENTS:
            item["arguments"] = {
                "updated_theme": "调整后的完整报告主题；主题不变时原样保留",
                "updated_requirements": "合并历史约束和本轮决定后的完整报告要求",
                "instruction": "本轮需求调整的简要说明",
            }
        elif name == AgentToolName.REVISE_ANALYSIS_PLAN:
            item["arguments"] = {
                "instruction": "分析范围和证据需求的完整调整要求",
                "required_dimensions": ["用户明确确认的分析维度；未确认具体名称时留空"],
                "required_dimension_count": "用户明确要求的维度数量；未明确时为 0",
            }
        elif name == AgentToolName.RECHECK_FACT:
            item["arguments"] = {"fact_id": "当前任务中的事实 ID", "instruction": "需要复核的问题"}
        elif name == AgentToolName.RECHECK_INFERENCE:
            item["arguments"] = {"inference_id": "当前任务中的推论 ID", "instruction": "需要复核的问题"}
        elif name == AgentToolName.SEARCH_MATERIAL_UNITS:
            item["arguments"] = {
                "query": "用户希望核实的材料问题或关键词",
                "material_id": "可选：限定当前任务中的一份材料",
                "limit": "返回片段数量，最大 8",
            }
        elif name == AgentToolName.SEARCH_FACT_EVIDENCE:
            item["arguments"] = {
                "query": "用户希望核实的事实、结论或问题",
                "limit": "返回事实数量，最大 8",
            }
        elif name == AgentToolName.CONFIRM_DIRECTORY:
            item["arguments"] = {"feedback": "可选：确认时一并交给后续写作的补充说明"}
        manifests.append(item)
    return manifests


def build_task_agent_context(task_id: str, focus: dict | None = None) -> TaskAgentContext:
    task = short_term.load_task(task_id) or {}
    report_id = int(task.get("report_id") or 0) or None
    artifact_counts = {
        "materials": len(task.get("material_ids") or []),
        "facts": len(task.get("fact_ids") or []),
        "inferences": len(task.get("inference_ids") or []) + len(task.get("external_ids") or []),
        "conflicts": len(task.get("conflict_ids") or []),
        "quality_issues": len(task.get("qa_notes") or []),
    }
    available = ["task_brief", "material_role"]
    if task.get("plan_id"):
        available.append("analysis_plan")
    if artifact_counts["facts"]:
        available.append("fact")
    if artifact_counts["inferences"]:
        available.append("inference")
    if task.get("final_plan_frozen"):
        available.append("final_plan")
    if task.get("requirement_review_pending", task.get("planning_review_pending")):
        available.append("requirements_review")
    if report_id:
        available.extend(["report", "qa_issue", "report_version"])
    return TaskAgentContext(
        task_id=task_id,
        report_id=report_id,
        theme=str(task.get("theme") or ""),
        user_requirements=str(task.get("user_requirements") or ""),
        stage=str(task.get("stage") or "created"),
        stage_label=_STAGE_LABELS.get(str(task.get("stage") or "created"), str(task.get("stage") or "处理中")),
        workflow_mode=str(task.get("workflow_mode") or "automatic"),
        requirement_review_pending=bool(task.get("requirement_review_pending", task.get("planning_review_pending"))),
        directory_review_pending=bool(task.get("directory_review_pending")),
        queue_status=dict(task.get("queue_status") or {}),
        progress={
            "parse": task.get("parse_progress") or {},
            "material_analysis": task.get("material_analysis_progress") or {},
            "evidence": task.get("evidence_progress") or {},
            "writing": task.get("write_progress") or {},
        },
        artifact_counts=artifact_counts,
        available_artifacts=available,
        workflow_map=_WORKFLOW_MAP,
        current_focus=ArtifactFocus.model_validate(focus or {}),
        error=str(task.get("error") or task.get("failure_reason") or ""),
    )


def validate_agent_tool_call(call: AgentToolCall, context: TaskAgentContext,
                             message: str = "") -> AgentToolCall:
    """Normalize model-selected tools against task scope and explicit user constraints."""
    arguments = dict(call.arguments or {})
    focus = context.current_focus
    if call.tool_name == AgentToolName.REVISE_TASK_REQUIREMENTS:
        requirements = str(arguments.get("updated_requirements") or "").strip()
        if not requirements:
            raise ValueError("需求调整缺少合并后的完整报告要求")
        arguments["updated_requirements"] = requirements
        arguments["updated_theme"] = str(arguments.get("updated_theme") or context.theme).strip()
    elif call.tool_name == AgentToolName.REVISE_ANALYSIS_PLAN:
        dimensions = _clean_text_list(arguments.get("required_dimensions"))
        expected = _positive_int(arguments.get("required_dimension_count")) or next((
            value for value in (_declared_count(message, unit) for unit in ("维度", "方面", "问题")) if value
        ), 0)
        if dimensions and expected and len(dimensions) != expected:
            raise ValueError(f"分析维度数量不一致：用户要求 {expected} 个，提案识别到 {len(dimensions)} 个")
        arguments.update(required_dimensions=dimensions, required_dimension_count=expected)
    elif call.tool_name == AgentToolName.RERUN_FINAL_PLAN:
        structure = _clean_text_list(arguments.get("new_structure"))
        structure = _preserve_confirmed_structure_scope(
            context.task_id,
            structure,
            f"{message}\n{arguments.get('instruction') or ''}",
        )
        if context.directory_review_pending and not structure:
            raise ValueError("目录讨论阶段需要助手先形成完整的新目录提案")
        expected = _positive_int(arguments.get("required_chapter_count")) or next((
            value for value in (_declared_count(message, unit) for unit in ("章", "部分")) if value
        ), 0) or len(structure)
        if structure and expected and len(structure) != expected:
            raise ValueError(f"章节数量不一致：用户要求 {expected} 章，提案识别到 {len(structure)} 章")
        arguments.update(new_structure=structure, required_chapter_count=expected)
    elif call.tool_name == AgentToolName.CONFIRM_REQUIREMENTS:
        if not context.requirement_review_pending:
            raise ValueError("当前不在需求确认阶段")
        if not str(context.theme or "").strip() or not str(context.user_requirements or "").strip():
            raise ValueError("确认需求前需要已保存完整主题和报告要求")
        arguments = {}
    elif call.tool_name == AgentToolName.CONFIRM_DIRECTORY:
        if not context.directory_review_pending:
            raise ValueError("当前不在目录确认阶段")
        arguments = {"feedback": str(arguments.get("feedback") or "").strip()}
    elif call.tool_name == AgentToolName.RECHECK_FACT:
        value = arguments.get("fact_id") or (focus.object_id if focus.artifact_type == "fact" else "")
        if not str(value).isdigit():
            raise ValueError("复核事实前需要选中具体事实")
        arguments["fact_id"] = int(value)
    elif call.tool_name == AgentToolName.RECHECK_INFERENCE:
        value = arguments.get("inference_id") or (focus.object_id if focus.artifact_type == "inference" else "")
        if not str(value).isdigit():
            raise ValueError("复核推论前需要选中具体推论")
        arguments["inference_id"] = int(value)
    elif call.tool_name == AgentToolName.REWRITE_SENTENCE:
        value = arguments.get("sentence_id") or (focus.object_id if focus.artifact_type == "sentence" else "")
        if focus.artifact_type != "sentence" or not str(value).isdigit() or str(value) != focus.object_id:
            raise ValueError("改写句子前需要在报告中选中该句")
        arguments["sentence_id"] = str(value)
    elif call.tool_name == AgentToolName.REWRITE_PARAGRAPH:
        if focus.artifact_type != "paragraph":
            raise ValueError("改写段落前需要在报告中选中该段")
        current = focus.current or {}
        arguments["chapter_title"] = str(arguments.get("chapter_title") or current.get("section") or "")
        arguments["paragraph"] = int(arguments.get("paragraph") or current.get("paragraph") or 0)
        arguments["sentence_ids"] = list(arguments.get("sentence_ids") or current.get("sentence_ids") or [])
        if not arguments["chapter_title"] or arguments["paragraph"] <= 0:
            raise ValueError("所选段落缺少稳定定位信息")
    elif call.tool_name == AgentToolName.UPDATE_REPORT_TITLE:
        arguments["new_title"] = str(arguments.get("new_title") or "").strip()
        if not arguments["new_title"] or context.report_id is None:
            raise ValueError("修改报告标题需要已生成报告和明确的新标题")
    elif call.tool_name == AgentToolName.UPDATE_SECTION_TITLE:
        old_title = _canonical_chapter_title(
            context.task_id,
            str(arguments.get("old_title") or context.current_focus.current.get("section") or ""),
        )
        new_title = _preserve_existing_heading_prefix(old_title, str(arguments.get("new_title") or ""))
        if not old_title or not new_title:
            raise ValueError("修改章节标题需要唯一的现有章节和明确的新标题")
        arguments.update(old_title=old_title, new_title=new_title)
    elif call.tool_name == AgentToolName.UPDATE_SECTION_TITLES:
        from app.rendering.headings import strip_heading_prefix

        titles = _load_plan(context.task_id, "final").get("titles") or []
        requested = arguments.get("changes")
        if not isinstance(requested, list) or len(requested) < 2:
            raise ValueError("批量修改章节标题至少需要两项明确变更")
        changes: list[dict[str, str]] = []
        seen_old: set[str] = set()
        seen_new: set[str] = set()
        for item in requested:
            if not isinstance(item, dict):
                raise ValueError("批量标题变更格式不正确")
            old_title = _canonical_chapter_title(context.task_id, str(item.get("old_title") or ""))
            new_title = _preserve_existing_heading_prefix(old_title, str(item.get("new_title") or ""))
            if old_title not in titles or not new_title:
                raise ValueError("批量标题变更必须指向当前目录中的章节并提供新标题")
            if old_title in seen_old or new_title in seen_new:
                raise ValueError("批量标题变更中不能重复章节或新标题")
            seen_old.add(old_title)
            seen_new.add(new_title)
            if old_title != new_title:
                changes.append({"old_title": old_title, "new_title": new_title})
        if len(changes) < 2:
            raise ValueError("批量标题变更至少应包含两项实际修改")
        unchanged_titles = set(titles) - seen_old
        if seen_new & unchanged_titles:
            raise ValueError("新的章节标题不能与未修改章节重复")
        order = {str(title): index for index, title in enumerate(titles)}
        changes.sort(key=lambda item: order[item["old_title"]])
        arguments["changes"] = changes
    elif call.tool_name == AgentToolName.REGENERATE_CHAPTER:
        chapter = _canonical_chapter_title(
            context.task_id,
            str(arguments.get("chapter_title") or focus.current.get("section") or focus.object_id),
        )
        titles = _load_plan(context.task_id, "final").get("titles") or []
        if not chapter or chapter not in titles:
            raise ValueError("需要指定当前最终目录中唯一存在的章节")
        arguments["chapter_title"] = chapter
    allowed = _MUTATING_ARGUMENT_KEYS.get(call.tool_name)
    if allowed is not None:
        arguments = {key: value for key, value in arguments.items() if key in allowed}
    call.arguments = arguments
    return call


def _clean_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _preserve_existing_heading_prefix(old_title: str, new_title: str) -> str:
    """Keep an existing chapter number when only title wording is changed."""
    from app.rendering.headings import strip_heading_prefix

    old_title = str(old_title or "").strip()
    new_title = str(new_title or "").strip()
    clean_old = strip_heading_prefix(old_title).strip()
    clean_new = strip_heading_prefix(new_title).strip()
    if not clean_new:
        return ""
    if clean_old == old_title or clean_new != new_title:
        return new_title
    prefix_end = old_title.find(clean_old)
    return f"{old_title[:prefix_end]}{clean_new}" if prefix_end >= 0 else clean_new


def _preserve_confirmed_structure_scope(task_id: str, proposed: list[str], instruction: str) -> list[str]:
    """Apply structural edit semantics against persisted titles, not model paraphrases."""
    if not proposed:
        return []
    from app.rendering.headings import strip_heading_prefix

    current = [
        strip_heading_prefix(str(item)) for item in (_load_plan(task_id, "final").get("titles") or [])
        if strip_heading_prefix(str(item))
    ]
    proposed = [strip_heading_prefix(str(item)) for item in proposed if strip_heading_prefix(str(item))]
    if not current:
        return proposed

    text = re.sub(r"\s+", "", str(instruction or ""))
    split_match = re.search(r"第([一二三四五六七八九十\d]+)章.{0,24}拆分|拆分.{0,12}第([一二三四五六七八九十\d]+)章", text)
    split_index = _chinese_or_arabic_int(next((value for value in split_match.groups() if value), "")) if split_match else 0
    preserve_match = re.search(r"(?:保持|保留)(?:原有|原)?前([一二三四五六七八九十\d]+)章(?:不变)?", text)
    preserve_count = _chinese_or_arabic_int(preserve_match.group(1)) if preserve_match else 0
    if split_index:
        preserve_count = max(preserve_count, split_index - 1)

    if preserve_count <= 0:
        return proposed
    preserve_count = min(preserve_count, len(current))
    tail_count = max(0, len(current) - (split_index or preserve_count))
    replacement_end = len(proposed) - tail_count if tail_count else len(proposed)
    replacements = proposed[preserve_count:replacement_end]
    if not replacements:
        raise ValueError("目录调整缺少目标章节的替代结构")
    suffix = current[len(current) - tail_count:] if tail_count else []
    return [*current[:preserve_count], *replacements, *suffix]


def _chinese_or_arabic_int(value: str) -> int:
    raw = str(value or "")
    if raw.isdigit():
        return int(raw)
    numerals = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                "七": 7, "八": 8, "九": 9, "十": 10}
    if raw in numerals:
        return numerals[raw]
    if raw.startswith("十"):
        return 10 + numerals.get(raw[1:], 0)
    if "十" in raw:
        left, right = raw.split("十", 1)
        return numerals.get(left, 0) * 10 + numerals.get(right, 0)
    return 0


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _declared_count(message: str, unit: str) -> int:
    match = re.search(rf"(?:划分|分成|调整为|改为|保留|形成|共)?\s*([一二三四五六七八九十\d]+)\s*个?{unit}", str(message or ""))
    if not match:
        return 0
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    numerals = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                "七": 7, "八": 8, "九": 9, "十": 10}
    if raw in numerals:
        return numerals[raw]
    if raw.startswith("十"):
        return 10 + numerals.get(raw[1:], 0)
    if "十" in raw:
        left, right = raw.split("十", 1)
        return numerals.get(left, 0) * 10 + numerals.get(right, 0)
    return 0


def execute_read_tool(name: AgentToolName, arguments: dict[str, Any], context: TaskAgentContext) -> dict[str, Any]:
    if name == AgentToolName.GET_TASK_OVERVIEW:
        queue = task_queue_status()
        return {
            "task_id": context.task_id, "theme": context.theme, "stage": context.stage,
            "stage_label": context.stage_label, "queue_status": context.queue_status,
            "progress": context.progress, "artifact_counts": context.artifact_counts,
            "error": context.error, "queue_position": (
                (context.queue_status or {}).get("position") if queue.get("running_task_id") != context.task_id else 0
            ),
        }
    if name == AgentToolName.GET_TASK_MAP:
        return _load_task_map(context.task_id, context.report_id)
    if name in {AgentToolName.GET_ANALYSIS_PLAN, AgentToolName.GET_FINAL_PLAN}:
        return _load_plan(
            context.task_id,
            "analysis" if name == AgentToolName.GET_ANALYSIS_PLAN else "final",
            include_details=bool(arguments.get("include_details")),
        )
    if name == AgentToolName.GET_MATERIALS:
        return _load_materials(context.task_id)
    if name == AgentToolName.SEARCH_MATERIAL_UNITS:
        return _search_material_units(
            context.task_id,
            query=str(arguments.get("query") or ""),
            material_id=int(arguments.get("material_id") or 0) or None,
            limit=int(arguments.get("limit") or 6),
        )
    if name == AgentToolName.LIST_FACTS:
        return _load_fact_catalog(
            context.task_id,
            query=str(arguments.get("query") or ""),
            dimension=str(arguments.get("dimension") or ""),
            offset=int(arguments.get("offset") or 0),
            limit=int(arguments.get("limit") or 12),
        )
    if name == AgentToolName.SEARCH_FACT_EVIDENCE:
        return _search_fact_evidence(
            context.task_id, query=str(arguments.get("query") or ""),
            limit=int(arguments.get("limit") or 6),
        )
    if name == AgentToolName.GET_FACT_EVIDENCE:
        return _load_fact_evidence(context.task_id, int(arguments.get("fact_id") or 0))
    if name == AgentToolName.LIST_INFERENCES:
        return _load_inference_catalog(
            context.task_id,
            query=str(arguments.get("query") or ""),
            dimension=str(arguments.get("dimension") or ""),
            confidence=str(arguments.get("confidence") or ""),
            offset=int(arguments.get("offset") or 0),
            limit=int(arguments.get("limit") or 12),
        )
    if name == AgentToolName.GET_INFERENCE:
        return _load_inference(context.task_id, int(arguments.get("inference_id") or 0))
    if name == AgentToolName.GET_CONFLICTS:
        return _load_conflicts(context.task_id)
    if name == AgentToolName.GET_QUALITY_ISSUES:
        task = short_term.load_task(context.task_id) or {}
        issues = list(task.get("qa_notes") or [])
        if context.report_id:
            from app.quality import attach_quality_issue_locations
            issues = attach_quality_issue_locations(context.report_id, issues)
        return _load_quality_issues(
            context.report_id, issues,
            offset=int(arguments.get("offset") or 0),
            limit=int(arguments.get("limit") or 12),
        )
    if name == AgentToolName.GET_REPORT_OUTLINE:
        return _load_report_outline(context.report_id)
    if name == AgentToolName.GET_REPORT_SECTION:
        return _load_report_section(
            context.report_id, str(arguments.get("chapter_title") or ""),
            offset=int(arguments.get("offset") or 0),
            limit=int(arguments.get("limit") or 60),
        )
    if name == AgentToolName.GET_REPORT_SENTENCE:
        return _load_report_sentence(context.report_id, int(arguments.get("sentence_id") or 0))
    if name == AgentToolName.LOCATE_QUALITY_ISSUE:
        index = max(0, int(arguments.get("issue_index") or 0))
        # Fetch the requested page rather than assuming the first 12 issues are
        # the complete QA result for a long report.
        data = execute_read_tool(
            AgentToolName.GET_QUALITY_ISSUES,
            {"offset": index, "limit": 1},
            context,
        )
        issue = (data.get("issues") or [{}])[0]
        return {"report_id": context.report_id, "issue": issue, "action_url": _issue_url(context.report_id, issue)}
    raise ValueError(f"READ_TOOL_NOT_SUPPORTED: {name.value}")


def execute_control_tool(name: AgentToolName, arguments: dict[str, Any], context: TaskAgentContext) -> dict[str, Any]:
    if name == AgentToolName.PAUSE_TASK:
        result = request_control(context.task_id, "pause")
        return _require_control_success(result)
    if name == AgentToolName.RESUME_TASK:
        result = request_control(context.task_id, "resume")
        return _require_control_success(result)
    if name == AgentToolName.RETRY_TASK:
        result = request_control(context.task_id, "restart")
        return _require_control_success(result)
    if name == AgentToolName.CONFIRM_REQUIREMENTS:
        from app.workflow.checkpoints import confirm_requirements
        return confirm_requirements(
            context.task_id, theme=context.theme, requirements=context.user_requirements,
        )
    if name == AgentToolName.CONFIRM_DIRECTORY:
        from app.workflow.checkpoints import confirm_directory
        return confirm_directory(context.task_id, feedback=str(arguments.get("feedback") or ""))
    raise ValueError(f"CONTROL_TOOL_REQUIRES_WORKFLOW_PROPOSAL: {name.value}")


def _require_control_success(result: dict[str, Any]) -> dict[str, Any]:
    if str(result.get("status") or "") in {
        "not_found", "not_running", "not_paused", "unsupported", "already_running", "already_queued",
    }:
        raise ValueError(f"TASK_CONTROL_REJECTED: {result.get('status')}")
    return result


def _load_plan(task_id: str, stage: Literal["analysis", "final"],
               include_details: bool = True) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    plan_id = task.get("plan_id")
    if not plan_id:
        return {"exists": False, "titles": []}
    with session_scope() as s:
        row = s.execute(select(ORMPlan).where(ORMPlan.c.id == int(plan_id))).mappings().first()
    if row is None:
        return {"exists": False, "titles": []}
    raw = row.get("analysis_plan_json") if stage == "analysis" else row.get("final_plan_json")
    payload = _json(raw, {})
    if not payload:
        payload = {
            "structure": _json(row.get("structure"), []),
            "chapter_plans": _json(row.get("chapter_plans"), []),
            "dimensions": _json(row.get("dimensions"), []),
        }
    entries = (
        payload.get("dimensions") if stage == "analysis"
        else payload.get("chapter_plans") or payload.get("structure") or []
    )
    titles = []
    for item in entries or []:
        title = item.get("title") or item.get("name") or item.get("dimension") if isinstance(item, dict) else item
        if str(title or "").strip():
            titles.append(str(title).strip())
    result = {
        "exists": bool(payload),
        "titles": titles,
        "summary": _plan_summary(payload, stage),
    }
    if include_details:
        result["plan"] = payload
    return result


def _plan_summary(payload: dict[str, Any], stage: Literal["analysis", "final"]) -> dict[str, Any]:
    """Expose planning intent without forcing the assistant to parse raw plan JSON."""
    common = {
        "title": _bounded_text(payload.get("title") or "", 180),
        "objective": _bounded_text(payload.get("objective") or "", 500),
        "core_question": _bounded_text(payload.get("core_question") or "", 500),
        "core_judgment": _bounded_text(payload.get("core_judgment") or "", 500),
        "narrative_logic": _bounded_text(payload.get("narrative_logic") or "", 700),
    }
    if stage == "analysis":
        dimensions = payload.get("dimensions") or []
        common["dimensions"] = [
            {
                "title": _item_title(item),
                "purpose": _bounded_text(
                    _item_value(item, "purpose", "objective", "question", "description"), 360,
                ),
            }
            for item in dimensions[:16]
        ]
        return common

    chapters = payload.get("chapter_plans") or payload.get("structure") or []
    common["chapters"] = [
        {
            "title": _item_title(item),
            "purpose": _bounded_text(
                _item_value(item, "purpose", "objective", "chapter_goal", "description"), 360,
            ),
            "target_words": _item_value(item, "target_words", "word_budget", "budget"),
            "required_fact_count": len(_item_list(item, "required_fact_ids", "fact_ids", "required_facts")),
            "required_inference_count": len(_item_list(item, "required_inference_ids", "inference_ids")),
        }
        for item in chapters[:20]
    ]
    return common


def _bounded_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _item_title(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("title") or item.get("name") or item.get("dimension") or "").strip()
    return str(item or "").strip()


def _item_value(item: Any, *keys: str) -> Any:
    if not isinstance(item, dict):
        return ""
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return ""


def _item_list(item: Any, *keys: str) -> list[Any]:
    value = _item_value(item, *keys)
    return value if isinstance(value, list) else []


def _clamp_page(offset: int, limit: int, maximum: int = 30) -> tuple[int, int]:
    return max(0, int(offset or 0)), max(1, min(int(limit or 12), maximum))


def _matches_catalog_query(item: dict[str, Any], query: str, dimension: str = "") -> bool:
    if dimension and str(item.get("dimension") or "") != dimension:
        return False
    query = str(query or "").strip().lower()
    if not query:
        return True
    haystack = " ".join(str(value or "") for value in item.values()).lower()
    return query in haystack


def _canonical_chapter_title(task_id: str, reference: str) -> str:
    try:
        titles = _load_plan(task_id, "final").get("titles") or []
    except Exception:
        titles = []
    reference = str(reference or "").strip()
    for title in titles:
        if reference == title or reference in title or title in reference:
            return str(title)
    match = re.fullmatch(r"第([一二三四五六七八九十\d]+)章", reference)
    if match:
        raw = match.group(1)
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                   "七": 7, "八": 8, "九": 9, "十": 10}
        index = int(raw) if raw.isdigit() else mapping.get(raw, 0)
        if 1 <= index <= len(titles):
            return str(titles[index - 1])
    return reference


def _load_report_outline(report_id: int | None) -> dict[str, Any]:
    if not report_id:
        return {"exists": False, "sections": []}
    with session_scope() as s:
        rows = s.execute(select(ORMSentence).where(
            ORMSentence.c.report_id == int(report_id), ORMSentence.c.selected == 1,
        ).order_by(ORMSentence.c.position)).mappings().all()
    sections: dict[str, dict[str, Any]] = {}
    for row in rows:
        title = str(row.get("section") or "未分章")
        item = sections.setdefault(title, {
            "title": title, "sentence_count": 0, "paragraphs": set(),
            "fact_ids": set(), "inference_ids": set(),
        })
        refs = _sentence_refs(row.get("source_refs"))
        item["sentence_count"] += 1
        item["paragraphs"].add(int(row.get("paragraph") or 0))
        item["fact_ids"].update(refs["fact_ids"])
        item["inference_ids"].update(refs["inference_ids"])
    return {
        "exists": bool(rows),
        "sections": [{
            "title": item["title"], "sentence_count": item["sentence_count"],
            "paragraph_count": len(item["paragraphs"]),
            "fact_count": len(item["fact_ids"]), "inference_count": len(item["inference_ids"]),
        } for item in sections.values()],
    }


def _load_report_section(report_id: int | None, chapter_title: str,
                         offset: int = 0, limit: int = 60) -> dict[str, Any]:
    if not report_id:
        return {"exists": False, "sentences": []}
    with session_scope() as s:
        query = select(ORMSentence).where(ORMSentence.c.report_id == int(report_id), ORMSentence.c.selected == 1)
        if chapter_title:
            query = query.where(ORMSentence.c.section == chapter_title)
        rows = s.execute(query.order_by(ORMSentence.c.position)).mappings().all()
    page_offset, page_limit = _clamp_page(offset, limit, maximum=100)
    page = rows[page_offset:page_offset + page_limit]
    sentences = [_report_sentence_view(row) for row in page]
    paragraphs: list[dict[str, Any]] = []
    for sentence in sentences:
        key = (sentence["section"], sentence["paragraph"])
        if not paragraphs or (paragraphs[-1]["section"], paragraphs[-1]["paragraph"]) != key:
            paragraphs.append({
                "section": sentence["section"], "paragraph": sentence["paragraph"],
                "sentence_ids": [], "text": "", "fact_ids": [], "inference_ids": [],
            })
        item = paragraphs[-1]
        item["sentence_ids"].append(sentence["id"])
        item["text"] = f"{item['text']}{sentence['content']}"
        item["fact_ids"] = sorted(set(item["fact_ids"]) | set(sentence["fact_ids"]))
        item["inference_ids"] = sorted(set(item["inference_ids"]) | set(sentence["inference_ids"]))
    next_offset = page_offset + page_limit if page_offset + page_limit < len(rows) else None
    return {
        "exists": bool(rows), "chapter_title": chapter_title, "total_sentences": len(rows),
        "offset": page_offset, "limit": page_limit, "next_offset": next_offset,
        "paragraphs": paragraphs, "sentences": sentences,
    }


def _load_report_sentence(report_id: int | None, sentence_id: int) -> dict[str, Any]:
    if not report_id or sentence_id <= 0:
        return {"exists": False}
    with session_scope() as s:
        row = s.execute(select(ORMSentence).where(
            ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == int(report_id),
        )).mappings().first()
    return {"exists": row is not None, "sentence": _report_sentence_view(row) if row else {}}


def _sentence_refs(value: Any) -> dict[str, list[int]]:
    refs = _json(value, {})
    if not isinstance(refs, dict):
        refs = {}
    return {
        "fact_ids": [int(item) for item in refs.get("fact_ids") or [] if str(item).isdigit()],
        "inference_ids": [int(item) for item in refs.get("inference_ids") or [] if str(item).isdigit()],
    }


def _report_sentence_view(row: Any) -> dict[str, Any]:
    refs = _sentence_refs(row.get("source_refs"))
    return {
        "id": int(row["id"]), "content": _bounded_text(row.get("user_edit") or row.get("content"), 900),
        "section": str(row.get("section") or ""), "paragraph": int(row.get("paragraph") or 0),
        "fact_ids": refs["fact_ids"], "inference_ids": refs["inference_ids"],
    }


def _load_materials(task_id: str) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    ids = [int(item) for item in task.get("material_ids") or [] if str(item).isdigit()]
    with session_scope() as s:
        materials = s.execute(select(ORMMaterial).where(ORMMaterial.c.id.in_(ids))).mappings().all() if ids else []
        insights = s.execute(select(ORMInsight).where(
            ORMInsight.c.task_id == task_id, ORMInsight.c.material_id.in_(ids),
        )).mappings().all() if ids else []
    roles = {int(row["material_id"]): row for row in insights}
    def bounded(value: Any, limit: int) -> Any:
        """Keep collaboration context useful without copying full artifacts."""
        parsed = _json(value, value)
        if isinstance(parsed, list):
            return parsed[:limit]
        if isinstance(parsed, str):
            return parsed[:2000]
        return parsed

    material_views = []
    for row in materials:
        insight = roles.get(int(row["id"])) or {}
        material_views.append({
            "id": int(row["id"]),
            "filename": row["filename"],
            "file_type": row["file_type"],
            "material_role": insight.get("material_role", ""),
            "claim_support": insight.get("claim_support", ""),
            "topic": insight.get("topic", ""),
            "key_sections": bounded(insight.get("key_sections"), 8),
            "key_points": bounded(insight.get("key_points"), 12),
            "missing_information": bounded(insight.get("missing_information"), 8),
            "allowed_usage": bounded(insight.get("allowed_usage"), 8),
            "forbidden_usage": bounded(insight.get("forbidden_usage"), 8),
        })
    return {"materials": material_views}


def _search_material_units(task_id: str, *, query: str, material_id: int | None = None,
                           limit: int = 6) -> dict[str, Any]:
    """Retrieve source units only from materials attached to this task.

    The assistant receives concise excerpts rather than a whole document.  This
    keeps the interaction context bounded while allowing it to answer material
    questions from verifiable text instead of from a MaterialInsight summary.
    """
    query = str(query or "").strip()
    task = short_term.load_task(task_id) or {}
    material_ids = [int(value) for value in task.get("material_ids") or [] if str(value).isdigit()]
    if material_id is not None:
        if int(material_id) not in material_ids:
            return {"query": query, "items": [], "error": "MATERIAL_NOT_IN_TASK"}
        material_ids = [int(material_id)]
    if not query or not material_ids:
        return {"query": query, "items": [], "total": 0, "strategy": "not_run"}
    with session_scope() as s:
        unit_rows = s.execute(select(ORMUnit).where(
            ORMUnit.c.material_id.in_(material_ids),
        )).mappings().all()
        material_rows = s.execute(select(ORMMaterial.c.id, ORMMaterial.c.filename).where(
            ORMMaterial.c.id.in_(material_ids),
        )).mappings().all()
    units_by_material: dict[int, list[Unit]] = {}
    by_unit: dict[int, dict[str, Any]] = {}
    for row in unit_rows:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        unit = Unit(
            id=int(row["id"]), material_id=int(row["material_id"]), kind=str(row.get("kind") or "text"),
            content=content, page=row.get("page"), paragraph=row.get("paragraph"),
            image_desc=row.get("image_desc"), metadata_json=str(row.get("metadata_json") or "{}"),
        )
        units_by_material.setdefault(unit.material_id, []).append(unit)
        by_unit[unit.id] = dict(row)
    hits, metadata = hybrid_retrieve_units(
        query, units_by_material, top_k=max(1, min(int(limit or 6), 8)),
        filters={"material_id": material_ids},
    )
    filenames = {int(row["id"]): str(row["filename"]) for row in material_rows}
    items = []
    for hit in hits:
        row = by_unit.get(int(hit.unit_id))
        if row is None:
            continue
        items.append({
            "unit_id": int(hit.unit_id), "material_id": int(row["material_id"]),
            "filename": filenames.get(int(row["material_id"]), ""),
            "kind": str(row.get("kind") or "text"), "page": row.get("page"),
            "paragraph": row.get("paragraph"), "excerpt": _bounded_text(row.get("content"), 1000),
            "retrieval": {"source": hit.source, "score": round(float(hit.score), 4)},
        })
    return {"query": query, "items": items, "total": len(items), "strategy": metadata}


def _load_task_map(task_id: str, report_id: int | None) -> dict[str, Any]:
    """Small, task-scoped map used as the assistant's first retrieval step."""
    task = short_term.load_task(task_id) or {}
    analysis = _load_plan(task_id, "analysis", include_details=False)
    final = _load_plan(task_id, "final", include_details=False)
    materials = _load_materials(task_id).get("materials") or []
    facts = _load_fact_catalog(task_id, limit=6).get("items") or []
    inferences = _load_inference_catalog(task_id, limit=6).get("items") or []
    return {
        "task": {
            "id": task_id,
            "theme": str(task.get("theme") or ""),
            "requirements": _bounded_text(task.get("user_requirements"), 900),
            "stage": str(task.get("stage") or "created"),
            "workflow_mode": str(task.get("workflow_mode") or "automatic"),
            "requirement_review_pending": bool(task.get("requirement_review_pending")),
            "directory_review_pending": bool(task.get("directory_review_pending")),
            "error": _bounded_text(task.get("error") or task.get("failure_reason"), 500),
        },
        "materials": [
            {
                "id": item["id"], "filename": item["filename"], "topic": item.get("topic", ""),
                "material_role": item.get("material_role", ""),
            }
            for item in materials[:12]
        ],
        "analysis_plan": analysis.get("summary") if analysis.get("exists") else {},
        "final_plan": final.get("summary") if final.get("exists") else {},
        "fact_highlights": facts,
        "inference_highlights": inferences,
        "report_outline": _load_report_outline(report_id).get("sections", []) if report_id else [],
        "counts": {
            "materials": len(task.get("material_ids") or []),
            "facts": len(task.get("fact_ids") or []),
            "inferences": len(task.get("inference_ids") or []) + len(task.get("external_ids") or []),
            "conflicts": len(task.get("conflict_ids") or []),
            "quality_issues": len(task.get("qa_notes") or []),
        },
    }


def _load_fact_catalog(task_id: str, query: str = "", dimension: str = "",
                       offset: int = 0, limit: int = 12) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    ids = [int(value) for value in task.get("fact_ids") or [] if str(value).isdigit()]
    if not ids:
        return {"items": [], "total": 0, "offset": 0, "limit": 0, "next_offset": None}
    with session_scope() as s:
        rows = s.execute(select(ORMFact).where(ORMFact.c.id.in_(ids))).mappings().all()
        evidence_rows = s.execute(select(ORMEvidence).where(ORMEvidence.c.fact_id.in_(ids))).mappings().all()
    by_id = {int(row["id"]): row for row in rows}
    evidence_by_fact: dict[int, list[dict[str, Any]]] = {}
    for evidence in evidence_rows:
        evidence_by_fact.setdefault(int(evidence["fact_id"]), []).append(dict(evidence))
    items = []
    for fact_id in ids:
        row = by_id.get(fact_id)
        if row is None or str(row.get("lifecycle_status") or "active") == "superseded":
            continue
        sources = sorted({str(item.get("source_file") or "") for item in evidence_by_fact.get(fact_id, []) if item.get("source_file")})
        item = {
            "id": fact_id,
            "content": _bounded_text(row.get("content"), 520),
            "dimension": str(row.get("dimension") or ""),
            "source_count": len(evidence_by_fact.get(fact_id, [])),
            "source_files": sources[:6],
            "lifecycle_status": str(row.get("lifecycle_status") or "active"),
        }
        if _matches_catalog_query(item, query, dimension):
            items.append(item)
    page_offset, page_limit = _clamp_page(offset, limit)
    page = items[page_offset:page_offset + page_limit]
    next_offset = page_offset + page_limit if page_offset + page_limit < len(items) else None
    return {"items": page, "total": len(items), "offset": page_offset,
            "limit": page_limit, "next_offset": next_offset}


def _search_fact_evidence(task_id: str, *, query: str, limit: int = 6) -> dict[str, Any]:
    """Hybrid task-local fact retrieval with evidence included in the result."""
    query = str(query or "").strip()
    task = short_term.load_task(task_id) or {}
    allowed = [int(value) for value in task.get("fact_ids") or [] if str(value).isdigit()]
    if not query or not allowed:
        return {"query": query, "items": [], "total": 0, "strategy": "not_run"}
    with session_scope() as s:
        facts = s.execute(select(ORMFact).where(ORMFact.c.id.in_(allowed))).mappings().all()
        evidence_rows = s.execute(select(ORMEvidence).where(ORMEvidence.c.fact_id.in_(allowed))).mappings().all()
    active = {
        int(row["id"]): dict(row) for row in facts
        if str(row.get("lifecycle_status") or "active") != "superseded"
    }
    semantic: dict[int, float] = {}
    try:
        vector = embed_texts([query], query=True)[0]
        semantic = {
            int(fact_id): float(score)
            for fact_id, score in vector_store.search_facts(
                vector, top_k=max(12, min(len(active), max(1, int(limit)) * 4)),
                filters={"task_id": task_id},
            ) if int(fact_id) in active
        }
    except Exception:
        semantic = {}
    evidence_by_fact: dict[int, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        evidence_by_fact.setdefault(int(row["fact_id"]), []).append(dict(row))
    scored = []
    for fact_id, row in active.items():
        lexical = _text_relevance(query, f"{row.get('dimension') or ''} {row.get('content') or ''}")
        semantic_score = semantic.get(fact_id, 0.0)
        if not lexical and not semantic_score:
            continue
        score = semantic_score * 0.7 + lexical * 0.3
        scored.append((score, lexical, semantic_score, fact_id, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    items = []
    for score, lexical, semantic_score, fact_id, row in scored[:max(1, min(int(limit or 6), 8))]:
        evidence = evidence_by_fact.get(fact_id, [])
        items.append({
            "id": fact_id, "content": _bounded_text(row.get("content"), 700),
            "dimension": str(row.get("dimension") or ""),
            "evidence": [{
                "source_file": str(item.get("source_file") or ""), "page": item.get("page"),
                "paragraph": item.get("paragraph"), "unit_id": item.get("unit_id"),
                "quote": _bounded_text(item.get("quote"), 500),
            } for item in evidence[:4]],
            "retrieval": {
                "score": round(score, 4), "semantic_score": round(semantic_score, 4),
                "lexical_score": round(lexical, 4),
            },
        })
    return {
        "query": query, "items": items, "total": len(items),
        "strategy": "semantic_keyword_rerank" if semantic else "keyword_rerank",
    }


def _text_relevance(query: str, text: str) -> float:
    """Cheap lexical fallback suitable for Chinese and mixed-language questions."""
    compact_query = re.sub(r"\s+", "", str(query or "").lower())
    compact_text = re.sub(r"\s+", "", str(text or "").lower())
    if not compact_query or not compact_text:
        return 0.0
    if compact_query in compact_text:
        return 1.0
    grams = {compact_query[index:index + 2] for index in range(max(0, len(compact_query) - 1))}
    if not grams:
        return 1.0 if compact_query in compact_text else 0.0
    hits = sum(1 for gram in grams if gram in compact_text)
    return hits / len(grams)


def _load_inference_catalog(task_id: str, query: str = "", dimension: str = "",
                            confidence: str = "", offset: int = 0, limit: int = 12) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    ids = [
        int(value) for value in [*(task.get("inference_ids") or []), *(task.get("external_ids") or [])]
        if str(value).isdigit()
    ]
    if not ids:
        return {"items": [], "total": 0, "offset": 0, "limit": 0, "next_offset": None}
    with session_scope() as s:
        rows = s.execute(select(ORMInference).where(ORMInference.c.id.in_(ids))).mappings().all()
        fact_rows = s.execute(select(ORMFact.c.id, ORMFact.c.content).where(
            ORMFact.c.id.in_({
                int(value) for row in rows for value in _json(row.get("based_fact_ids"), []) if str(value).isdigit()
            })
        )).mappings().all()
    by_id = {int(row["id"]): row for row in rows}
    fact_text = {int(row["id"]): _bounded_text(row.get("content"), 180) for row in fact_rows}
    items = []
    for inference_id in ids:
        row = by_id.get(inference_id)
        if row is None or str(row.get("lifecycle_status") or "active") == "superseded":
            continue
        based_fact_ids = [int(value) for value in _json(row.get("based_fact_ids"), []) if str(value).isdigit()]
        item = {
            "id": inference_id,
            "content": _bounded_text(row.get("content"), 520),
            "dimension": str(row.get("dimension") or ""),
            "confidence_level": str(row.get("confidence_level") or "medium"),
            "confidence_reason": _bounded_text(row.get("confidence_reason"), 280),
            "based_facts": [{"id": fact_id, "content": fact_text.get(fact_id, "")} for fact_id in based_fact_ids[:6]],
        }
        if confidence and item["confidence_level"] != confidence:
            continue
        if _matches_catalog_query(item, query, dimension):
            items.append(item)
    page_offset, page_limit = _clamp_page(offset, limit)
    page = items[page_offset:page_offset + page_limit]
    next_offset = page_offset + page_limit if page_offset + page_limit < len(items) else None
    return {"items": page, "total": len(items), "offset": page_offset,
            "limit": page_limit, "next_offset": next_offset}


def _load_fact_evidence(task_id: str, fact_id: int) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    allowed = {int(item) for item in task.get("fact_ids") or [] if str(item).isdigit()}
    if fact_id not in allowed:
        return {"exists": False, "error": "FACT_NOT_IN_TASK"}
    with session_scope() as s:
        # The task's fact-id registry is the isolation boundary. Incremental
        # tasks may legitimately inherit a fact created by an earlier task.
        fact = s.execute(select(ORMFact).where(ORMFact.c.id == fact_id)).mappings().first()
        evidence = s.execute(select(ORMEvidence).where(ORMEvidence.c.fact_id == fact_id)).mappings().all()
    return {
        "exists": fact is not None,
        "fact": {
            "id": fact_id, "content": fact["content"], "dimension": fact["dimension"],
            "lifecycle_status": fact.get("lifecycle_status") or "active",
        } if fact else {},
        "evidence": [{"source_file": row["source_file"], "page": row["page"],
                      "paragraph": row["paragraph"], "unit_id": row["unit_id"], "quote": row["quote"]}
                     for row in evidence],
    }


def _load_inference(task_id: str, inference_id: int) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    allowed = {int(item) for item in [*(task.get("inference_ids") or []), *(task.get("external_ids") or [])]
               if str(item).isdigit()}
    if inference_id not in allowed:
        return {"exists": False, "error": "INFERENCE_NOT_IN_TASK"}
    with session_scope() as s:
        row = s.execute(select(ORMInference).where(ORMInference.c.id == inference_id)).mappings().first()
    return {"exists": row is not None, "inference": {
        "id": inference_id, "content": row["content"],
        "based_fact_ids": _json(row["based_fact_ids"], []),
        "confidence_level": row["confidence_level"], "confidence_reason": row["confidence_reason"],
        "reasoning_chain": row["reasoning_chain"],
    } if row else {}}


def _load_conflicts(task_id: str) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    ids = [int(item) for item in task.get("conflict_ids") or [] if str(item).isdigit()]
    if not ids:
        return {"conflicts": [], "count": 0}
    from app.evidence.extractor import load_conflict_records
    records = load_conflict_records(ids)
    conflicts = []
    for item in records:
        entries = []
        for entry in item.get("entries") or []:
            entries.append({
                "claim_id": entry.get("claim_id"),
                "fact_id": entry.get("fact_id"),
                "content": _bounded_text(entry.get("statement") or entry.get("content"), 420),
                "source_file": str(entry.get("file") or entry.get("source_file") or entry.get("source") or ""),
                "page": entry.get("page"),
                "paragraph": entry.get("paragraph"),
                "quote": _bounded_text(entry.get("quote"), 360),
            })
        conflicts.append({
            "id": item.get("id"), "subject": _bounded_text(item.get("fact_key"), 220),
            "conflict_type": item.get("conflict_type"), "reason": _bounded_text(item.get("reason"), 500),
            "confidence": item.get("confidence"), "status": item.get("status"), "entries": entries,
        })
    return {"conflicts": conflicts, "count": len(conflicts)}


def _load_quality_issues(report_id: int | None, issues: list[Any],
                         offset: int = 0, limit: int = 12) -> dict[str, Any]:
    normalized = [item if isinstance(item, dict) else {"note": str(item)} for item in issues]
    sentence_ids = {
        int(value)
        for item in normalized
        for value in [item.get("sentence_id"), *(item.get("sentence_ids") or [])]
        if str(value).isdigit()
    }
    sentence_by_id: dict[int, Any] = {}
    if report_id and sentence_ids:
        with session_scope() as s:
            rows = s.execute(select(ORMSentence).where(
                ORMSentence.c.report_id == int(report_id), ORMSentence.c.id.in_(sentence_ids),
            )).mappings().all()
        sentence_by_id = {int(row["id"]): row for row in rows}
    entries = []
    for index, item in enumerate(normalized):
        ids = [int(value) for value in [item.get("sentence_id"), *(item.get("sentence_ids") or [])]
               if str(value).isdigit()]
        sentence = sentence_by_id.get(ids[0]) if ids else None
        entries.append({
            "index": index,
            "type": str(item.get("type") or item.get("target_type") or "质量问题"),
            "note": _bounded_text(item.get("note") or item.get("message") or "", 520),
            "section": str(item.get("section") or (sentence.get("section") if sentence else "") or ""),
            "paragraph": int(item.get("paragraph") or (sentence.get("paragraph") if sentence else 0) or 0),
            "sentence_ids": ids,
            "quote": _bounded_text(item.get("quote") or "", 360),
            "sentence_excerpt": _bounded_text(
                (sentence.get("user_edit") or sentence.get("content")) if sentence else "", 520,
            ),
            "location_confidence": str(item.get("location_confidence") or ""),
        })
    page_offset, page_limit = _clamp_page(offset, limit)
    page = entries[page_offset:page_offset + page_limit]
    next_offset = page_offset + page_limit if page_offset + page_limit < len(entries) else None
    return {"report_id": report_id, "issues": page, "count": len(entries),
            "offset": page_offset, "limit": page_limit, "next_offset": next_offset}


def _issue_url(report_id: int | None, issue: dict) -> str:
    if not report_id:
        return ""
    if issue.get("sentence_id"):
        return f"/reports/{report_id}?panel=qa&qa_sentence={int(issue['sentence_id'])}"
    if issue.get("section"):
        from urllib.parse import quote
        return f"/reports/{report_id}?panel=qa&qa_section={quote(str(issue['section']))}"
    return f"/reports/{report_id}?panel=qa"


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default
