"""Task-scoped control agent contracts and deterministic tool registry.

The control agent never generates workflow artifacts itself. It reads audited
artifacts and dispatches commands to the existing workflow services.
"""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db import session_scope
from app.infrastructure.orm import (
    ORMConflict, ORMEvidence, ORMFact, ORMInference, ORMInsight, ORMMaterial,
    ORMPlan, ORMSentence,
)
from app.memory import short_term
from app.workflow.queue import request_control, task_queue_status


class AgentIntent(str, Enum):
    ANSWER = "answer"
    READ = "read"
    NAVIGATE = "navigate"
    PROPOSE = "propose"
    EXECUTE = "execute"


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


class AgentDecision(BaseModel):
    intent: AgentIntent = AgentIntent.ANSWER
    reply: str
    tool_call: AgentToolCall | None = None
    proposal: dict[str, Any] | None = None


_STAGE_LABELS = {
    "created": "等待开始", "parsing": "材料解析", "dedup": "去重归并",
    "material_analysis": "材料理解", "planning": "分析规划", "evidence": "事实与证据",
    "conflict": "冲突核验", "analysis": "综合分析", "writing": "报告生成",
    "knowledge": "深度检查", "review": "等待审核", "done": "已完成",
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
    AgentToolName.REGENERATE_CHAPTER: (True, "调用 Narrative Plan 与 Writer 重写指定章节"),
    AgentToolName.RERUN_FINAL_PLAN: (True, "调用 Final Planner 重组目录及其下游"),
}


def tool_manifest() -> list[dict[str, Any]]:
    manifests = []
    for name, config in _TOOL_DEFINITIONS.items():
        item = {"name": name.value, "confirmation_required": config[0], "description": config[1]}
        if name == AgentToolName.RERUN_FINAL_PLAN:
            item["arguments"] = {
                "instruction": "用户对目录调整的完整要求",
                "new_structure": ["用户明确确认的章节标题，按顺序填写；未确认具体标题时留空"],
            }
        elif name == AgentToolName.REGENERATE_CHAPTER:
            item["arguments"] = {"chapter_title": "现有章节标题", "instruction": "本轮具体修改要求"}
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


def parse_agent_decision(payload: Any) -> AgentDecision:
    if isinstance(payload, dict) and payload.get("intent") == "change":
        payload = {**payload, "intent": "propose"}
    try:
        decision = AgentDecision.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"INVALID_CONTROL_AGENT_OUTPUT: {exc.errors(include_url=False)}") from exc
    if decision.tool_call:
        decision.tool_call.confirmation_required = _TOOL_DEFINITIONS[decision.tool_call.tool_name][0]
    return decision


def resolve_common_read(message: str, context: TaskAgentContext) -> tuple[str, dict[str, Any]] | None:
    """Handle high-frequency factual questions without an LLM round trip."""
    text = re.sub(r"\s+", "", str(message or ""))
    if re.search(r"(?:现在|当前|目前).{0,8}(?:进行到|运行到|处于)|(?:任务|报告).{0,6}(?:进度|状态)", text):
        result = execute_read_tool(AgentToolName.GET_TASK_OVERVIEW, {}, context)
        return format_tool_result(AgentToolName.GET_TASK_OVERVIEW, result), result
    if re.search(r"(?:本任务|当前|现在|已经).{0,8}(?:章节规划|章节结构|报告结构|最终目录|目录)", text):
        result = execute_read_tool(AgentToolName.GET_FINAL_PLAN, {}, context)
        return format_tool_result(AgentToolName.GET_FINAL_PLAN, result), result
    if re.search(r"(?:分析规划|分析维度|分析问题).{0,8}(?:是什么|有哪些|怎么样|查看)", text):
        result = execute_read_tool(AgentToolName.GET_ANALYSIS_PLAN, {}, context)
        return format_tool_result(AgentToolName.GET_ANALYSIS_PLAN, result), result
    if re.search(r"(?:质量问题|质检问题|QA).{0,8}(?:有哪些|在哪|是什么|查看)", text, re.IGNORECASE):
        result = execute_read_tool(AgentToolName.GET_QUALITY_ISSUES, {}, context)
        return format_tool_result(AgentToolName.GET_QUALITY_ISSUES, result), result
    return None


def resolve_control_command(message: str, context: TaskAgentContext) -> AgentToolCall | None:
    text = re.sub(r"\s+", "", str(message or ""))
    if re.search(r"^(?:请)?暂停(?:当前)?任务", text):
        return AgentToolCall(tool_name=AgentToolName.PAUSE_TASK, confirmation_required=True, reason="用户要求暂停任务")
    if re.search(r"^(?:请)?(?:继续|恢复)(?:运行|任务)", text):
        return AgentToolCall(tool_name=AgentToolName.RESUME_TASK, confirmation_required=True, reason="用户要求恢复任务")
    if re.search(r"^(?:请)?(?:重试|重新运行)(?:当前阶段|失败阶段|当前任务|任务)$", text):
        return AgentToolCall(tool_name=AgentToolName.RETRY_TASK, confirmation_required=True, reason="用户要求重试任务")
    chapter = re.search(r"(?:重新生成|重写|改写)(第[^，。；\s]{1,16}章|[^，。；\s]{2,30}章)", text)
    if chapter:
        chapter_title = _canonical_chapter_title(context.task_id, chapter.group(1))
        return AgentToolCall(
            tool_name=AgentToolName.REGENERATE_CHAPTER,
            arguments={"chapter_title": chapter_title, "instruction": str(message).strip()},
            confirmation_required=True,
            reason="用户要求调用现有成文链路重写指定章节",
        )
    if re.search(r"(?:重新规划|重做|重组).{0,6}(?:目录|章节结构|报告结构)", text):
        return AgentToolCall(
            tool_name=AgentToolName.RERUN_FINAL_PLAN,
            arguments={"instruction": str(message).strip()},
            confirmation_required=True,
            reason="用户要求调用 Final Planner 重组报告结构",
        )
    return None


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


def format_tool_result(name: AgentToolName, result: dict[str, Any]) -> str:
    if name == AgentToolName.GET_TASK_OVERVIEW:
        parts = [f"当前处于“{result.get('stage_label')}”阶段。"]
        progress = result.get("progress") or {}
        labels = {"parse": "材料", "material_analysis": "材料理解", "evidence": "证据批次", "writing": "章节"}
        done = []
        for key, label in labels.items():
            item = progress.get(key) or {}
            if item.get("total"):
                done.append(f"{label} {item.get('done', 0)}/{item['total']}")
        if done:
            parts.append("已完成：" + "、".join(done) + "。")
        if result.get("error"):
            parts.append("当前异常：" + str(result["error"])[:240])
        return "".join(parts)
    if name in {AgentToolName.GET_FINAL_PLAN, AgentToolName.GET_ANALYSIS_PLAN}:
        if not result.get("exists"):
            label = "最终报告结构" if name == AgentToolName.GET_FINAL_PLAN else "分析规划"
            return f"本任务尚未形成{label}，助手不会用建议内容冒充已落库产物。"
        titles = result.get("titles") or []
        label = "最终章节规划" if name == AgentToolName.GET_FINAL_PLAN else "分析规划"
        return f"本任务当前已落库的{label}如下：\n\n" + "\n".join(
            f"{index}. {title}" for index, title in enumerate(titles, start=1)
        )
    if name == AgentToolName.GET_QUALITY_ISSUES:
        issues = result.get("issues") or []
        if not issues:
            return "当前报告没有已记录的质量问题。"
        return f"当前共记录 {len(issues)} 个质量问题：\n\n" + "\n".join(
            f"{index}. [{item.get('type', '质量问题')}] {item.get('note') or item.get('quote') or ''}"
            for index, item in enumerate(issues[:12], start=1)
        )
    if name == AgentToolName.GET_REPORT_SECTION:
        sentences = result.get("sentences") or []
        if not sentences:
            return "没有找到对应章节正文。"
        return f"已读取“{result.get('chapter_title') or sentences[0].get('section')}”，共 {len(sentences)} 句。"
    if name == AgentToolName.GET_FACT_EVIDENCE:
        fact = result.get("fact") or {}
        evidence = result.get("evidence") or []
        if not fact:
            return "该事实不属于当前任务，或已经不存在。"
        lines = [f"事实：{fact.get('content')}", f"依据共 {len(evidence)} 处："]
        lines.extend(
            f"- {item.get('source_file')}{f' · 第 {item.get("page")} 页' if item.get('page') else ''}：{item.get('quote')}"
            for item in evidence[:8]
        )
        return "\n".join(lines)
    if name == AgentToolName.GET_MATERIALS:
        materials = result.get("materials") or []
        return f"当前任务使用 {len(materials)} 份材料：\n" + "\n".join(
            f"- {item.get('filename')}：{item.get('material_role') or '角色待确认'}"
            for item in materials[:20]
        )
    if name == AgentToolName.GET_INFERENCE:
        item = result.get("inference") or {}
        if not item:
            return "该推论不属于当前任务，或已经不存在。"
        return (
            f"推论：{item.get('content')}\n"
            f"置信度：{item.get('confidence_level') or '待复核'}\n"
            f"依据事实：{'、'.join(str(value) for value in item.get('based_fact_ids') or []) or '无'}\n"
            f"推理说明：{item.get('reasoning_chain') or item.get('confidence_reason') or '未记录'}"
        )
    if name == AgentToolName.GET_CONFLICTS:
        conflicts = result.get("conflicts") or []
        if not conflicts:
            return "当前任务没有已记录的材料冲突。"
        return f"当前共有 {len(conflicts)} 组冲突，已保留冲突双方与核验状态，可在“冲突与核验”中展开查看。"
    return json.dumps(result, ensure_ascii=False)


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
