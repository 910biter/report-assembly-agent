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
    ORMPlan, ORMSentence,
)
from app.memory import short_term
from app.workflow.queue import request_control, task_queue_status


class AgentToolName(str, Enum):
    GET_TASK_OVERVIEW = "get_task_overview"
    GET_MATERIALS = "get_materials"
    GET_FACT_EVIDENCE = "get_fact_evidence"
    GET_INFERENCE = "get_inference"
    GET_CONFLICTS = "get_conflicts"
    GET_ANALYSIS_PLAN = "get_analysis_plan"
    GET_FINAL_PLAN = "get_final_plan"
    GET_QUALITY_ISSUES = "get_quality_issues"
    GET_REPORT_SECTION = "get_report_section"
    PAUSE_TASK = "pause_task"
    RESUME_TASK = "resume_task"
    RETRY_TASK = "retry_task"
    REVISE_TASK_REQUIREMENTS = "revise_task_requirements"
    REVISE_ANALYSIS_PLAN = "revise_analysis_plan"
    RECHECK_FACT = "recheck_fact"
    RECHECK_INFERENCE = "recheck_inference"
    REWRITE_SENTENCE = "rewrite_sentence"
    REWRITE_PARAGRAPH = "rewrite_paragraph"
    UPDATE_REPORT_TITLE = "update_report_title"
    UPDATE_SECTION_TITLE = "update_section_title"
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
    "material_analysis": "材料理解", "planning": "分析规划", "evidence": "事实与证据",
    "conflict": "冲突核验", "analysis": "综合分析", "writing": "报告生成",
    "review": "等待审核", "done": "已完成",
    "paused": "已暂停", "failed": "运行异常",
}

_WORKFLOW_MAP = [
    {"stage": "parsing", "purpose": "将材料转换为带页码和来源位置的内容单元", "artifact": "Units"},
    {"stage": "material_analysis", "purpose": "判断材料角色、可证明范围和缺失信息", "artifact": "MaterialInsight"},
    {"stage": "planning", "purpose": "规划分析问题与证据需求，不提前冻结最终目录", "artifact": "AnalysisPlan"},
    {"stage": "evidence", "purpose": "提取事实并绑定原始证据", "artifact": "Fact/Evidence"},
    {"stage": "conflict", "purpose": "核验多来源对同一事项的矛盾", "artifact": "Conflict"},
    {"stage": "analysis", "purpose": "基于事实形成带置信度的推论", "artifact": "Inference"},
    {"stage": "final_plan", "purpose": "根据事实和推论形成最终内容结构", "artifact": "FinalReportPlan"},
    {"stage": "narrative", "purpose": "组织章节主线、话题和逻辑顺序", "artifact": "NarrativePlan"},
    {"stage": "writing", "purpose": "调用 Writer 按章节自然成文并绑定来源", "artifact": "Report/ChapterDraft"},
    {"stage": "qa", "purpose": "发现事实、结构、语言和格式问题", "artifact": "QAResult"},
]

_TOOL_DEFINITIONS = {
    AgentToolName.GET_TASK_OVERVIEW: (False, "读取任务状态、进度、异常和产物数量"),
    AgentToolName.GET_MATERIALS: (False, "读取任务材料及材料理解角色"),
    AgentToolName.GET_FACT_EVIDENCE: (False, "读取任务内指定事实及原始证据"),
    AgentToolName.GET_INFERENCE: (False, "读取任务内指定推论、置信度和依据事实"),
    AgentToolName.GET_CONFLICTS: (False, "读取任务内冲突双方及核验状态"),
    AgentToolName.GET_ANALYSIS_PLAN: (False, "读取已落库的分析规划"),
    AgentToolName.GET_FINAL_PLAN: (False, "读取已落库的最终报告目录"),
    AgentToolName.GET_QUALITY_ISSUES: (False, "读取质量问题及报告定位"),
    AgentToolName.GET_REPORT_SECTION: (False, "读取指定章节正文与引用概况"),
    AgentToolName.LOCATE_QUALITY_ISSUE: (False, "定位质量问题对应句子或章节"),
    AgentToolName.PAUSE_TASK: (True, "在安全边界暂停任务"),
    AgentToolName.RESUME_TASK: (True, "从断点恢复任务"),
    AgentToolName.RETRY_TASK: (True, "从失败或暂停阶段重试任务"),
    AgentToolName.REVISE_TASK_REQUIREMENTS: (True, "更新任务主题或报告要求，并从分析规划重新计算"),
    AgentToolName.REVISE_ANALYSIS_PLAN: (True, "调用 Planner 调整分析问题与证据需求"),
    AgentToolName.RECHECK_FACT: (True, "回到 Evidence 复核指定事实及其证据"),
    AgentToolName.RECHECK_INFERENCE: (True, "回到 Analysis 复核指定推论及其依据"),
    AgentToolName.REWRITE_SENTENCE: (True, "调用 Writer 在证据约束下改写指定句子"),
    AgentToolName.REWRITE_PARAGRAPH: (True, "调用 Writer 在证据约束下改写指定段落"),
    AgentToolName.UPDATE_REPORT_TITLE: (True, "修改报告主标题"),
    AgentToolName.UPDATE_SECTION_TITLE: (True, "修改现有章节标题并同步目录"),
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
    AgentToolName.REGENERATE_CHAPTER: {"chapter_title", "instruction"},
    AgentToolName.RERUN_FINAL_PLAN: {"instruction", "new_structure", "required_chapter_count"},
    AgentToolName.PAUSE_TASK: set(),
    AgentToolName.RESUME_TASK: set(),
    AgentToolName.RETRY_TASK: set(),
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
    if report_id:
        available.extend(["report", "qa_issue", "report_version"])
    return TaskAgentContext(
        task_id=task_id,
        report_id=report_id,
        theme=str(task.get("theme") or ""),
        user_requirements=str(task.get("user_requirements") or ""),
        stage=str(task.get("stage") or "created"),
        stage_label=_STAGE_LABELS.get(str(task.get("stage") or "created"), str(task.get("stage") or "处理中")),
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
        expected = _positive_int(arguments.get("required_chapter_count")) or next((
            value for value in (_declared_count(message, unit) for unit in ("章", "部分")) if value
        ), 0) or len(structure)
        if structure and expected and len(structure) != expected:
            raise ValueError(f"章节数量不一致：用户要求 {expected} 章，提案识别到 {len(structure)} 章")
        arguments.update(new_structure=structure, required_chapter_count=expected)
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
        new_title = str(arguments.get("new_title") or "").strip()
        if not old_title or not new_title:
            raise ValueError("修改章节标题需要唯一的现有章节和明确的新标题")
        arguments.update(old_title=old_title, new_title=new_title)
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
    if name in {AgentToolName.GET_ANALYSIS_PLAN, AgentToolName.GET_FINAL_PLAN}:
        return _load_plan(context.task_id, "analysis" if name == AgentToolName.GET_ANALYSIS_PLAN else "final")
    if name == AgentToolName.GET_MATERIALS:
        return _load_materials(context.task_id)
    if name == AgentToolName.GET_FACT_EVIDENCE:
        return _load_fact_evidence(context.task_id, int(arguments.get("fact_id") or 0))
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
        return {"report_id": context.report_id, "issues": issues, "count": len(issues)}
    if name == AgentToolName.GET_REPORT_SECTION:
        return _load_report_section(context.report_id, str(arguments.get("chapter_title") or ""))
    if name == AgentToolName.LOCATE_QUALITY_ISSUE:
        data = execute_read_tool(AgentToolName.GET_QUALITY_ISSUES, {}, context)
        index = max(0, int(arguments.get("issue_index") or 0))
        issue = data["issues"][index] if index < len(data["issues"]) else {}
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
    raise ValueError(f"CONTROL_TOOL_REQUIRES_WORKFLOW_PROPOSAL: {name.value}")


def _require_control_success(result: dict[str, Any]) -> dict[str, Any]:
    if str(result.get("status") or "") in {
        "not_found", "not_running", "not_paused", "unsupported", "already_running", "already_queued",
    }:
        raise ValueError(f"TASK_CONTROL_REJECTED: {result.get('status')}")
    return result


def _load_plan(task_id: str, stage: Literal["analysis", "final"]) -> dict[str, Any]:
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
    return {"exists": bool(payload), "titles": titles, "plan": payload}


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


def _load_report_section(report_id: int | None, chapter_title: str) -> dict[str, Any]:
    if not report_id:
        return {"exists": False, "sentences": []}
    with session_scope() as s:
        query = select(ORMSentence).where(ORMSentence.c.report_id == int(report_id), ORMSentence.c.selected == 1)
        if chapter_title:
            query = query.where(ORMSentence.c.section == chapter_title)
        rows = s.execute(query.order_by(ORMSentence.c.position)).mappings().all()
    return {
        "exists": bool(rows), "chapter_title": chapter_title,
        "sentences": [{"id": int(row["id"]), "content": row["user_edit"] or row["content"],
                       "section": row["section"], "paragraph": row["paragraph"]} for row in rows],
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
    return {"materials": [{
        "id": int(row["id"]), "filename": row["filename"], "file_type": row["file_type"],
        "material_role": (roles.get(int(row["id"])) or {}).get("material_role", ""),
        "claim_support": (roles.get(int(row["id"])) or {}).get("claim_support", ""),
    } for row in materials]}


def _load_fact_evidence(task_id: str, fact_id: int) -> dict[str, Any]:
    task = short_term.load_task(task_id) or {}
    allowed = {int(item) for item in task.get("fact_ids") or [] if str(item).isdigit()}
    if fact_id not in allowed:
        return {"exists": False, "error": "FACT_NOT_IN_TASK"}
    with session_scope() as s:
        fact = s.execute(select(ORMFact).where(
            ORMFact.c.id == fact_id, ORMFact.c.task_id == task_id,
        )).mappings().first()
        evidence = s.execute(select(ORMEvidence).where(ORMEvidence.c.fact_id == fact_id)).mappings().all()
    return {
        "exists": fact is not None,
        "fact": {"id": fact_id, "content": fact["content"], "dimension": fact["dimension"]} if fact else {},
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
    with session_scope() as s:
        rows = s.execute(select(ORMConflict).where(ORMConflict.c.id.in_(ids))).mappings().all() if ids else []
    return {"conflicts": [{key: row[key] for key in row.keys()} for row in rows], "count": len(rows)}


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
