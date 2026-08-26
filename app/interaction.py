"""Asynchronous, scoped user intervention and auditable change proposals."""
from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import insert, select, update

from app.db import session_scope
from app.gateway import (
    last_generation_meta,
    last_generation_stats,
    model_gateway,
    reset_last_generation_call,
)
from app.infrastructure.orm import (
    ORMChangeProposal,
    ORMEvidence,
    ORMFact,
    ORMInference,
    ORMInsight,
    ORMInteractionNotification,
    ORMInteractionMessage,
    ORMInteractionThread,
    ORMPlan,
    ORMReport,
    ORMSentence,
    ORMTaskArtifact,
)
from app.llm_scheduler import invoke
from app.llm_queue import PRIORITY_INTERACTIVE, llm_priority
from app.config import settings
from app.report_versions import ensure_report_version
from app.context_budget import (
    ContextSection,
    build_prompt_from_sections,
    consume_context_audit,
    count_tokens,
    truncate_tokens,
)
from app.token_monitor import log_llm_call, new_call_id, token_context

EDITABLE_ARTIFACTS = {
    "task_draft", "task_brief", "material_role", "analysis_plan", "fact", "inference",
    "final_plan", "narrative_plan", "report_title", "section_title", "paragraph", "sentence",
    "qa_issue", "comparison_item",
}

_COPILOT_SYSTEM = """你是报告整编工作台中的审阅助手。用户正在讨论一个明确作用域的产物。
你只能解释、提出修改建议，不得声称已经修改数据库。只有用户明确要求修改当前产物时，才能输出 ChangeProposal。
询问进度、依据、原因、影响、可行性或“应该怎么改”，均属于讨论，不得创建提案。
严格输出 JSON：
{
  "intent": "answer/change",
  "reply": "对用户的简洁答复",
  "proposal": null 或 {
    "operation": "replace/update/reorganize/recheck",
    "after": {"content": "建议的新内容；若当前对象有明确字段，也可保留字段结构"},
    "rationale": "修改理由",
    "risk_level": "low/medium/high"
  }
}

proposal.after 只能包含提示中“允许修改字段”列出的字段，并且只表达当前产物的修改结果；
不得把报告正文、其他阶段产物或解释文字塞入任务需求、标题等不相干字段。

真实性和溯源是硬边界。不得凭空添加事实；事实、推论、章节结构的语义修改至少为 medium 风险。
事实或推论存在疑问时，应提出 recheck/reorganize，而不是直接编造替代内容。
说明建议将影响哪些下游产物，但不要要求用户等待在页面上。
只依据本次提供的任务边界、当前对象和证据上下文作答，不得调用或猜测其他任务的信息。"""

_INTERACTION_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(2, int(settings.interactive_concurrency or 1)),
    thread_name_prefix="interaction",
)


def create_thread(*, task_id: str = "", report_id: int | None = None,
                  artifact_type: str, artifact_version: str = "", object_id: str = "",
                  scope: dict | None = None) -> dict[str, Any]:
    if artifact_type not in EDITABLE_ARTIFACTS:
        raise ValueError("INVALID_ARTIFACT_TYPE")
    scope = dict(scope or {})
    with session_scope() as s:
        query = select(ORMInteractionThread).where(
            ORMInteractionThread.c.task_id == task_id,
            ORMInteractionThread.c.artifact_type == artifact_type,
            ORMInteractionThread.c.artifact_version == artifact_version,
            ORMInteractionThread.c.object_id == str(object_id or ""),
            ORMInteractionThread.c.status == "open",
        )
        if report_id is None:
            query = query.where(ORMInteractionThread.c.report_id.is_(None))
        else:
            query = query.where(ORMInteractionThread.c.report_id == int(report_id))
        existing = s.execute(query.order_by(ORMInteractionThread.c.id.desc())).mappings().first()
    if existing is not None:
        if scope:
            with session_scope() as s:
                s.execute(update(ORMInteractionThread).where(
                    ORMInteractionThread.c.id == int(existing["id"])
                ).values(
                    scope_json=_dump(scope),
                    updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                ))
        return get_thread(int(existing["id"])) or _thread_detail(existing)
    thread_key = uuid.uuid4().hex
    with session_scope() as s:
        result = s.execute(insert(ORMInteractionThread).values(
            thread_key=thread_key,
            task_id=task_id,
            report_id=report_id,
            artifact_type=artifact_type,
            artifact_version=artifact_version,
            object_id=str(object_id or ""),
            scope_json=_dump(scope),
            status="open",
        ))
        thread_id = int(result.inserted_primary_key[0])
    return get_thread(thread_id) or {"id": thread_id}


def get_thread(thread_id: int) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.execute(select(ORMInteractionThread).where(
            ORMInteractionThread.c.id == int(thread_id)
        )).mappings().first()
        if row is None:
            return None
        messages = s.execute(select(ORMInteractionMessage).where(
            ORMInteractionMessage.c.thread_id == int(thread_id)
        ).order_by(ORMInteractionMessage.c.id)).mappings().all()
        proposals = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.thread_id == int(thread_id)
        ).order_by(ORMChangeProposal.c.id.desc())).mappings().all()
    result = _thread_detail(row)
    result["messages"] = [_message_detail(item) for item in messages]
    proposal_details = [_proposal_detail(item) for item in proposals]
    if result["artifact_type"] == "task_draft":
        current = dict((result.get("scope") or {}).get("current") or {})
        for item in reversed(proposal_details):
            before = dict(item.get("before") or {})
            if not str(before.get("requirements") or before.get("content") or "").strip() \
                    and str(current.get("requirements") or "").strip():
                item["before"] = dict(current)
            if item.get("status") in {"accepted", "applied"}:
                current = _merge_draft_change(current, item.get("after") or {})
        result.setdefault("scope", {})["current"] = current
    result["proposals"] = proposal_details
    result["pending"] = any(
        item["role"] == "user"
        and _load(item["metadata_json"], {}).get("status") in {"queued", "running"}
        for item in messages
    )
    return result


def list_threads(*, task_id: str = "", report_id: int | None = None,
                 draft_id: str = "") -> list[dict[str, Any]]:
    query = select(ORMInteractionThread)
    if report_id is not None:
        query = query.where(ORMInteractionThread.c.report_id == int(report_id))
    elif task_id:
        query = query.where(ORMInteractionThread.c.task_id == task_id)
    elif draft_id:
        query = query.where(
            ORMInteractionThread.c.task_id == "",
            ORMInteractionThread.c.artifact_type == "task_draft",
            ORMInteractionThread.c.object_id == str(draft_id),
        )
    else:
        return []
    with session_scope() as s:
        if draft_id:
            # Early UI versions could create a pre-task thread before the
            # session draft id was ready. Adopt those auditable conversations
            # once, so a refresh can recover them without requiring a new turn.
            s.execute(update(ORMInteractionThread).where(
                ORMInteractionThread.c.task_id == "",
                ORMInteractionThread.c.artifact_type == "task_draft",
                ORMInteractionThread.c.object_id == "",
            ).values(object_id=str(draft_id)))
        rows = s.execute(query.order_by(ORMInteractionThread.c.updated_at.desc())).mappings().all()
    summaries = []
    for row in rows:
        detail = get_thread(int(row["id"])) or _thread_detail(row)
        summaries.append(_thread_summary(detail))
    return summaries


def close_thread(thread_id: int) -> dict[str, Any]:
    """Close the visible conversation while retaining its audit trail."""
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError("INTERACTION_THREAD_NOT_FOUND")
    if thread.get("pending") or any(
        str(item.get("execution_status") or "") in {"waiting", "queued", "running"}
        for item in thread.get("proposals") or []
    ):
        raise ValueError("INTERACTION_THREAD_BUSY")
    with session_scope() as s:
        s.execute(update(ORMInteractionThread).where(
            ORMInteractionThread.c.id == int(thread_id)
        ).values(
            status="closed",
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        ))
    return {"ok": True, "thread_id": int(thread_id), "status": "closed"}


def attach_draft_thread(draft_id: str, task_id: str) -> int:
    """Promote pre-task discussion into the created task's auditable history."""
    draft_id = str(draft_id or "").strip()
    if not draft_id or not task_id:
        return 0
    with session_scope() as s:
        rows = s.execute(select(ORMInteractionThread.c.id).where(
            ORMInteractionThread.c.task_id == "",
            ORMInteractionThread.c.artifact_type == "task_draft",
            ORMInteractionThread.c.object_id == draft_id,
        )).all()
        thread_ids = [int(row[0]) for row in rows]
        if not thread_ids:
            return 0
        s.execute(update(ORMInteractionThread).where(
            ORMInteractionThread.c.id.in_(thread_ids)
        ).values(
            task_id=task_id, artifact_type="task_brief", object_id="",
            artifact_version="1", updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        ))
        s.execute(update(ORMChangeProposal).where(
            ORMChangeProposal.c.thread_id.in_(thread_ids)
        ).values(task_id=task_id, artifact_type="task_brief", object_id="", artifact_version="1"))
    return len(thread_ids)


def post_message(thread_id: int, content: str) -> dict[str, Any]:
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError("INTERACTION_THREAD_NOT_FOUND")
    if thread.get("status") != "open":
        raise ValueError("INTERACTION_THREAD_CLOSED")
    content = content.strip()
    if not content:
        raise ValueError("EMPTY_MESSAGE")
    message_id = _save_message(thread_id, "user", content, {"status": "running"})
    try:
        result = _generate_interaction_reply(thread_id, content)
        _update_message_metadata(message_id, {"status": "completed"})
    except Exception as exc:
        _update_message_metadata(message_id, {"status": "failed", "error": str(exc)[:300]})
        raise
    return {**result, "thread": get_thread(thread_id)}


def queue_message(thread_id: int, content: str, request_id: str = "") -> dict[str, Any]:
    """Persist a user turn and return immediately; inference continues in background."""
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError("INTERACTION_THREAD_NOT_FOUND")
    if thread.get("status") != "open":
        raise ValueError("INTERACTION_THREAD_CLOSED")
    content = content.strip()
    if not content:
        raise ValueError("EMPTY_MESSAGE")
    request_id = str(request_id or uuid.uuid4().hex)[:80]
    for item in thread.get("messages") or []:
        if str((item.get("metadata") or {}).get("request_id") or "") == request_id:
            return {"status": "duplicate", "request_id": request_id, "thread": thread}
    message_id = _save_message(
        thread_id, "user", content,
        {"status": "queued", "request_id": request_id},
    )
    _INTERACTION_EXECUTOR.submit(
        _run_queued_interaction, thread_id, message_id, request_id, content,
    )
    return {"status": "queued", "request_id": request_id, "thread": get_thread(thread_id)}


def _run_queued_interaction(thread_id: int, message_id: int, request_id: str, content: str) -> None:
    _update_message_metadata(message_id, {"status": "running", "request_id": request_id})
    try:
        _generate_interaction_reply(thread_id, content, request_id=request_id)
        _update_message_metadata(message_id, {"status": "completed", "request_id": request_id})
    except Exception as exc:
        _save_message(
            thread_id, "assistant", f"暂时无法生成建议：{str(exc)[:160]}",
            {"request_id": request_id, "status": "failed"},
        )
        _update_message_metadata(
            message_id,
            {"status": "failed", "request_id": request_id, "error": str(exc)[:300]},
        )


def _generate_interaction_reply(thread_id: int, content: str, request_id: str = "") -> dict[str, Any]:
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError("INTERACTION_THREAD_NOT_FOUND")
    current = _resolve_current(thread)
    if _is_progress_question(content):
        reply = _progress_reply(str(thread.get("task_id") or ""))
        metadata = {"status": "completed", "intent": "answer"}
        if request_id:
            metadata["request_id"] = request_id
        _save_message(thread_id, "assistant", reply, metadata)
        return {"reply": reply, "proposal": None}
    prompt = _build_interaction_prompt(thread, current, content)
    # Do not submit this request to the long-running workflow queue. Sending it
    # directly lets vLLM observe its priority and preempt/reorder queued work.
    call_id = new_call_id()
    context_audit = consume_context_audit()
    context_audit["final_user_prompt_tokens"] = count_tokens(prompt)
    context_audit["estimated_request_tokens"] = count_tokens(f"{_COPILOT_SYSTEM}\n{prompt}")
    reset_last_generation_call()
    started = time.time()
    task_id = str(thread.get("task_id") or "")
    try:
        with token_context(task_id=task_id, stage="interaction"):
            with llm_priority(PRIORITY_INTERACTIVE):
                result = invoke(
                    "review_copilot",
                    model_gateway.generate_json,
                    prompt,
                    system=_COPILOT_SYSTEM,
                    think=False,
                    max_tokens=int(settings.interactive_output_tokens),
                )
            log_llm_call(
                call_id, "interaction", len(prompt), last_generation_stats(),
                time.time() - started, success=True, context_audit=context_audit,
                **last_generation_meta(),
            )
    except Exception as exc:
        with token_context(task_id=task_id, stage="interaction"):
            log_llm_call(
                call_id, "interaction", len(prompt), last_generation_stats(),
                time.time() - started, success=False, error=str(exc), context_audit=context_audit,
                **last_generation_meta(),
            )
        raise
    reply = str(result.get("reply") or "已记录你的意见。")
    metadata = {"status": "completed", "intent": str(result.get("intent") or "answer")}
    if request_id:
        metadata["request_id"] = request_id
    assistant_message_id = _save_message(thread_id, "assistant", reply, metadata)
    proposal = result.get("proposal") if isinstance(result, dict) else None
    proposal_detail = None
    normalized = _normalize_proposal(thread, current, content, proposal)
    if normalized is not None:
        proposal_detail = _create_proposal(
            thread, current, normalized, source_message_id=assistant_message_id,
        )
    return {"reply": reply, "proposal": proposal_detail}


def decide_proposal(proposal_id: int, decision: str) -> dict[str, Any]:
    if decision not in {"accepted", "rejected"}:
        raise ValueError("INVALID_PROPOSAL_DECISION")
    with session_scope() as s:
        row = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.id == int(proposal_id)
        )).mappings().first()
    if row is None:
        raise ValueError("CHANGE_PROPOSAL_NOT_FOUND")
    if row["status"] != "proposed":
        return _proposal_detail(row)
    applied = False
    scheduled = False
    if decision == "accepted":
        applied = _apply_supported_proposal(dict(row))
        if not applied:
            scheduled = _schedule_semantic_proposal(dict(row))
    status = "applied" if applied else decision
    execution_status = "completed" if applied else ("waiting" if scheduled else "not_required")
    with session_scope() as s:
        s.execute(update(ORMChangeProposal).where(
            ORMChangeProposal.c.id == int(proposal_id)
        ).values(
            status=status,
            execution_status=execution_status,
            decided_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        ))
    if scheduled:
        dispatch_pending_revisions(str(row.get("task_id") or ""))
    with session_scope() as s:
        current = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.id == int(proposal_id)
        )).mappings().first()
    return _proposal_detail(current)


def _create_proposal(thread: dict, current: dict, proposal: dict,
                     source_message_id: int | None = None) -> dict:
    artifact_type = thread["artifact_type"]
    risk = str(proposal.get("risk_level") or _default_risk(artifact_type))
    if artifact_type in {"fact", "inference", "analysis_plan", "final_plan", "narrative_plan"} and risk == "low":
        risk = "medium"
    impact = _impact_for(artifact_type, thread)
    if source_message_id is not None:
        impact["source_message_id"] = int(source_message_id)
    with session_scope() as s:
        result = s.execute(insert(ORMChangeProposal).values(
            proposal_key=uuid.uuid4().hex,
            thread_id=int(thread["id"]),
            task_id=thread.get("task_id") or "",
            report_id=thread.get("report_id"),
            artifact_type=artifact_type,
            artifact_version=thread.get("artifact_version") or "",
            object_id=str(thread.get("object_id") or ""),
            operation=str(proposal.get("operation") or "update"),
            before_json=_dump(current),
            after_json=_dump(proposal.get("after") or {}),
            impact_json=_dump(impact),
            rationale=str(proposal.get("rationale") or ""),
            risk_level=risk,
            status="proposed",
        ))
        proposal_id = int(result.inserted_primary_key[0])
    with session_scope() as s:
        row = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.id == proposal_id
        )).mappings().first()
    return _proposal_detail(row)


def _apply_supported_proposal(row: dict) -> bool:
    """Apply only local report edits; upstream semantics remain approved artifacts.

    Upstream approval is intentionally not executed here.  A scheduler can use
    the stored impact graph to invalidate and regenerate downstream artifacts.
    """
    artifact_type = row["artifact_type"]
    after = _load(row["after_json"], {})
    content = str(after.get("content") or after.get("title") or "").strip()
    report_id = row.get("report_id")
    task_id = str(row.get("task_id") or "")
    if artifact_type == "task_draft":
        with session_scope() as s:
            thread = s.execute(select(ORMInteractionThread).where(
                ORMInteractionThread.c.id == int(row["thread_id"])
            )).mappings().first()
            if thread is None:
                return False
            scope = _load(thread["scope_json"], {})
            current = _merge_draft_change(dict(scope.get("current") or {}), after)
            scope["current"] = current
            s.execute(update(ORMInteractionThread).where(
                ORMInteractionThread.c.id == int(row["thread_id"])
            ).values(scope_json=_dump(scope)))
        return True
    if artifact_type == "task_brief":
        from app.memory import short_term
        task = short_term.load_task(task_id) or {}
        if str(task.get("stage") or "created") != "created":
            return False
        values = {}
        if str(after.get("theme") or "").strip():
            values["theme"] = str(after["theme"]).strip()
        requirements = after.get("requirements", after.get("content"))
        if str(requirements or "").strip():
            values["user_requirements"] = str(requirements).strip()
        if not values:
            return False
        short_term.update_task(task_id, **values)
        return True
    if not content or report_id is None:
        return False
    if artifact_type == "report_title":
        with session_scope() as s:
            s.execute(update(ORMReport).where(ORMReport.c.id == int(report_id)).values(title=content))
        ensure_report_version(int(report_id), task_id=row.get("task_id") or "", status="snapshot",
                              change_summary="审阅助手提案：修改报告标题", kind="minor")
        return True
    if artifact_type == "sentence":
        try:
            sentence_id = int(row["object_id"])
        except (TypeError, ValueError):
            return False
        with session_scope() as s:
            sentence = s.execute(select(ORMSentence).where(
                ORMSentence.c.id == sentence_id,
                ORMSentence.c.report_id == int(report_id),
            )).mappings().first()
            if sentence is None:
                return False
            history = _load(sentence["edit_history"], [])
            history.append({"content": sentence["user_edit"] or sentence["content"], "source": "change_proposal"})
            s.execute(update(ORMSentence).where(ORMSentence.c.id == sentence_id).values(
                user_edit=content, edit_history=_dump(history[-50:]),
            ))
        ensure_report_version(int(report_id), task_id=row.get("task_id") or "", status="snapshot",
                              change_summary="审阅助手提案：局部正文修改", kind="minor")
        return True
    if artifact_type == "section_title":
        old_title = str(_load(row["before_json"], {}).get("title") or row.get("object_id") or "").strip()
        if not old_title:
            return False
        with session_scope() as s:
            changed = s.execute(update(ORMSentence).where(
                ORMSentence.c.report_id == int(report_id),
                ORMSentence.c.section == old_title,
            ).values(section=content)).rowcount
        if not changed:
            return False
        _rename_section_in_plan(int(report_id), old_title, content)
        ensure_report_version(int(report_id), task_id=task_id, status="snapshot",
                              change_summary="审阅助手提案：修改章节标题", kind="minor")
        return True
    return False


def _merge_draft_change(current: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current or {})
    if str(after.get("theme") or "").strip():
        merged["theme"] = str(after["theme"]).strip()
    requirements = after.get("requirements", after.get("content"))
    if requirements is not None:
        merged["requirements"] = str(requirements).strip()
    return merged


def _rename_section_in_plan(report_id: int, old_title: str, new_title: str) -> None:
    with session_scope() as s:
        report = s.execute(select(ORMReport).where(ORMReport.c.id == report_id)).mappings().first()
        if report is None:
            return
        plan = s.execute(select(ORMPlan).where(ORMPlan.c.id == int(report["plan_id"]))).mappings().first()
        if plan is None:
            return
        structure = [new_title if str(item) == old_title else item for item in _load(plan["structure"], [])]
        chapters = []
        for item in _load(plan["chapter_plans"], []):
            chapters.append({**item, "title": new_title} if str(item.get("title") or "") == old_title else item)
        final_plan = _replace_exact_string(_load(plan.get("final_plan_json"), {}), old_title, new_title)
        s.execute(update(ORMPlan).where(ORMPlan.c.id == int(report["plan_id"])).values(
            structure=_dump(structure), chapter_plans=_dump(chapters), final_plan_json=_dump(final_plan),
        ))


def _replace_exact_string(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return new if value == old else value
    if isinstance(value, list):
        return [_replace_exact_string(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace_exact_string(item, old, new) for key, item in value.items()}
    return value


def _schedule_semantic_proposal(row: dict) -> bool:
    """Mark semantic changes for asynchronous, versioned recomputation."""
    artifact_type = str(row.get("artifact_type") or "")
    if artifact_type not in {
        "task_brief", "material_role", "analysis_plan", "fact", "inference",
        "final_plan", "narrative_plan", "paragraph", "qa_issue", "comparison_item",
    }:
        return False
    task_id = str(row.get("task_id") or "")
    if not task_id:
        return False
    _apply_approved_context_patch(row)
    _notify(
        task_id=task_id, report_id=row.get("report_id"), notification_type="proposal_accepted",
        title="修改建议已进入后台处理",
        message="系统将在当前轮次结束后生成候选版本，不会覆盖已有版本。",
        action_url=f"/tasks/{task_id}?tab=collaboration",
        metadata={"proposal_id": int(row["id"]), "artifact_type": artifact_type},
    )
    return True


def _apply_approved_context_patch(row: dict) -> None:
    """Persist user-owned task context; derived artifacts still require recompute."""
    from app.memory import short_term
    task_id = str(row.get("task_id") or "")
    artifact_type = str(row.get("artifact_type") or "")
    after = _load(row.get("after_json"), {})
    if artifact_type == "task_brief":
        values = {}
        if str(after.get("theme") or "").strip():
            values["theme"] = str(after["theme"]).strip()
        requirements = after.get("requirements", after.get("content"))
        if str(requirements or "").strip():
            values["user_requirements"] = str(requirements).strip()
        if values:
            short_term.update_task(task_id, **values)
        return
    if artifact_type != "material_role" or not str(row.get("object_id") or "").isdigit():
        return
    material_id = int(row["object_id"])
    allowed_fields = {key: after[key] for key in (
        "material_role", "claim_support", "allowed_usage", "forbidden_usage", "missing_information"
    ) if key in after}
    if not allowed_fields:
        return
    db_values = {}
    for key, value in allowed_fields.items():
        db_values[key] = _dump(value) if isinstance(value, (list, dict)) else str(value)
    with session_scope() as s:
        s.execute(update(ORMInsight).where(
            ORMInsight.c.task_id == task_id,
            ORMInsight.c.material_id == material_id,
        ).values(**db_values))
    task = short_term.load_task(task_id) or {}
    insights = []
    for item in task.get("material_insights") or []:
        insights.append({**item, **allowed_fields} if int(item.get("material_id") or 0) == material_id else item)
    short_term.update_task(task_id, material_insights=insights)


def _build_interaction_prompt(thread: dict, current: dict, message: str) -> str:
    """Build a small, task-isolated context instead of copying the full report."""
    task_context = _interaction_task_context(str(thread.get("task_id") or ""))
    grounding = _interaction_grounding(thread, current, message)
    accepted = _accepted_interaction_decisions(int(thread["id"]))
    messages = list(thread.get("messages") or [])
    if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == message:
        messages = messages[:-1]

    from app.runtime_profiles import stage_input_budget_tokens
    total_budget = stage_input_budget_tokens("interaction")
    history_budget = min(
        int(settings.interactive_history_tokens or 1536),
        max(300, total_budget // 3),
    )
    history = _bounded_history(messages, history_budget)
    prompt, _audit = build_prompt_from_sections(
        "interaction",
        [
            ContextSection("用户消息", [
                f"用户消息：{_truncate_tokens(message, max(256, total_budget // 3))}",
                f"允许修改字段：{_dump(_editable_fields(thread, current))}",
            ], weight=6, required_items=2),
            ContextSection("当前对象", [
                f"当前作用域：{_dump(thread.get('scope') or {})}",
                f"当前对象：{_dump(current)}",
            ], weight=5),
            ContextSection("直接依据", [f"直接依据：{_dump(grounding)}"], weight=5),
            ContextSection("任务边界", [f"任务边界：{_dump(task_context)}"], weight=3),
            ContextSection("已确认变更", [f"已确认变更：{_dump(accepted)}"], weight=2),
            ContextSection("最近对话", [
                f"{item.get('role', '')}：{item.get('content', '')}" for item in history
            ], weight=3),
        ],
        total_budget,
    )
    return prompt


def _editable_fields(thread: dict, current: dict) -> list[str]:
    artifact_type = str(thread.get("artifact_type") or "")
    fixed = {
        "task_draft": ["theme", "requirements"],
        "task_brief": ["theme", "requirements"],
        "material_role": ["material_role", "claim_support", "allowed_usage", "forbidden_usage", "missing_information"],
        "fact": ["content", "dimension", "fact_type"],
        "inference": ["content", "based_fact_ids", "confidence_level", "confidence_reason", "uncertainty", "reasoning_chain"],
        "report_title": ["title"],
        "section_title": ["title"],
        "paragraph": ["content"],
        "sentence": ["content"],
    }
    if artifact_type in fixed:
        return fixed[artifact_type]
    excluded = {"id", "task_id", "report_id", "stage", "status", "source_refs", "created_at", "updated_at"}
    return sorted(str(key) for key in current if str(key) not in excluded)


_DIRECT_CHANGE_PATTERNS = (
    r"(?:请|帮我|需要|希望|我要|务必).{0,24}(?:修改|调整|删除|删掉|去掉|增加|新增|补充|重写|改写|替换|改名|修正|重组|草拟|拟定)",
    r"(?:把|将).{1,100}(?:改成|改为|调整为|替换为|删除|删掉|去掉|补充|重写|改写)",
    r"^(?:修改|调整|删除|删掉|去掉|增加|新增|补充|重写|改写|替换|改名|修正|重组|草拟|拟定)",
    r"\b(?:change|edit|rewrite|replace|remove|delete|add|revise)\b",
)


def _is_explicit_change_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _DIRECT_CHANGE_PATTERNS)


def _normalize_proposal(thread: dict, current: dict, message: str,
                        proposal: Any) -> dict | None:
    if not _is_explicit_change_request(message) or not isinstance(proposal, dict):
        return None
    after = proposal.get("after")
    if not isinstance(after, dict):
        return None
    allowed = set(_editable_fields(thread, current))
    cleaned = {str(key): value for key, value in after.items() if str(key) in allowed}
    if not cleaned:
        return None
    if all(current.get(key) == value for key, value in cleaned.items()):
        return None
    return {**proposal, "after": cleaned}


def _is_progress_question(message: str) -> bool:
    if _is_explicit_change_request(message):
        return False
    text = re.sub(r"\s+", "", str(message or "").lower())
    return bool(re.search(
        r"(?:现在|当前|目前).{0,8}(?:进行到|运行到|做到|处于|在哪).{0,8}(?:哪|什么|哪里|阶段|步骤)|"
        r"(?:任务|报告).{0,6}(?:进度|进行到哪|运行到哪)|进度怎么样|完成了吗",
        text,
    ))


def _progress_reply(task_id: str) -> str:
    if not task_id:
        return "当前讨论尚未绑定任务，提交任务后才能读取实时进度。"
    from app.memory import short_term
    task = short_term.load_task(task_id) or {}
    stage = str(task.get("stage") or "created")
    labels = {
        "created": "等待开始", "parsing": "材料解析", "dedup": "材料去重",
        "material_analysis": "材料理解", "planning": "分析规划", "evidence": "事实与证据提取",
        "conflict": "冲突核验", "analysis": "综合分析", "writing": "报告生成",
        "review": "等待审核", "done": "已完成", "paused": "已暂停", "failed": "运行异常",
    }
    details = []
    for key, label in (
        ("parse_progress", "材料"), ("material_analysis_progress", "材料理解"),
        ("evidence_progress", "证据批次"), ("write_progress", "章节"),
    ):
        progress = task.get(key) or (task.get("progress") or {}).get(key) or {}
        done, total = progress.get("done"), progress.get("total")
        if done is not None and total:
            details.append(f"{label} {done}/{total}")
    suffix = f"；已完成：{'、'.join(details)}" if details else ""
    return f"当前处于“{labels.get(stage, stage)}”阶段{suffix}。"


def _interaction_task_context(task_id: str) -> dict[str, Any]:
    if not task_id:
        return {}
    from app.memory import short_term
    task = short_term.load_task(task_id) or {}
    return {
        "task_id": task_id,
        "theme": task.get("theme", ""),
        "user_requirements": task.get("user_requirements", ""),
        "stage": task.get("stage", ""),
        "material_count": len(task.get("material_ids") or []),
    }


def _interaction_grounding(thread: dict, current: dict, message: str) -> dict[str, Any]:
    task_id = str(thread.get("task_id") or "")
    if not task_id:
        return {}
    from app.memory import short_term
    task = short_term.load_task(task_id) or {}
    task_fact_ids = {int(item) for item in task.get("fact_ids") or [] if str(item).isdigit()}
    task_inference_ids = {
        int(item) for item in task.get("inference_ids") or [] if str(item).isdigit()
    }
    refs = current.get("source_refs") if isinstance(current, dict) else {}
    refs = refs if isinstance(refs, dict) else {}
    required_fact_ids = {
        int(item) for item in (refs.get("fact_ids") or current.get("based_fact_ids") or [])
        if str(item).isdigit() and int(item) in task_fact_ids
    }
    required_inference_ids = {
        int(item) for item in refs.get("inference_ids") or []
        if str(item).isdigit() and int(item) in task_inference_ids
    }
    object_id = str(thread.get("object_id") or "")
    if thread.get("artifact_type") == "fact" and object_id.isdigit() and int(object_id) in task_fact_ids:
        required_fact_ids.add(int(object_id))
    if thread.get("artifact_type") == "inference" and object_id.isdigit() and int(object_id) in task_inference_ids:
        required_inference_ids.add(int(object_id))

    fact_rows: list[dict] = []
    inference_rows: list[dict] = []
    with session_scope() as s:
        if task_fact_ids:
            rows = s.execute(select(ORMFact).where(
                ORMFact.c.task_id == task_id,
                ORMFact.c.id.in_(sorted(task_fact_ids)),
            )).mappings().all()
            fact_rows = [dict(row) for row in rows]
        if task_inference_ids:
            rows = s.execute(select(ORMInference).where(
                ORMInference.c.id.in_(sorted(task_inference_ids)),
            )).mappings().all()
            inference_rows = [dict(row) for row in rows]

    query_text = " ".join((message, _dump(current)))
    selected_inferences = _select_related_rows(
        inference_rows, query_text, required_inference_ids, limit=6,
    )
    for row in selected_inferences:
        required_fact_ids.update(
            int(item) for item in _load(row.get("based_fact_ids"), [])
            if str(item).isdigit() and int(item) in task_fact_ids
        )
    selected_facts = _select_related_rows(fact_rows, query_text, required_fact_ids, limit=10)
    selected_fact_ids = {int(row["id"]) for row in selected_facts}
    evidence_rows: list[dict] = []
    if selected_fact_ids:
        with session_scope() as s:
            rows = s.execute(select(ORMEvidence).where(
                ORMEvidence.c.fact_id.in_(sorted(selected_fact_ids)),
            ).order_by(ORMEvidence.c.fact_id, ORMEvidence.c.id).limit(16)).mappings().all()
            evidence_rows = [dict(row) for row in rows]
    return {
        "facts": [
            {"id": row["id"], "content": row["content"], "dimension": row.get("dimension", "")}
            for row in selected_facts
        ],
        "inferences": [
            {
                "id": row["id"], "content": row["content"],
                "based_fact_ids": _load(row.get("based_fact_ids"), []),
                "confidence_level": row.get("confidence_level", ""),
            }
            for row in selected_inferences
        ],
        "evidence": [
            {
                "fact_id": row["fact_id"], "source_file": row["source_file"],
                "page": row["page"], "unit_id": row["unit_id"], "quote": row["quote"],
            }
            for row in evidence_rows
        ],
    }


def _select_related_rows(rows: list[dict], query: str, required_ids: set[int], limit: int) -> list[dict]:
    terms = _semantic_terms(query)
    scored = []
    for row in rows:
        row_id = int(row.get("id") or 0)
        text = " ".join(str(row.get(key) or "") for key in ("content", "dimension", "reasoning_chain"))
        score = 1000 if row_id in required_ids else sum(1 for term in terms if term in text.lower())
        if score:
            scored.append((score, row_id, row))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def _semantic_terms(text: str) -> set[str]:
    text = str(text or "").lower()
    terms = set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text))
    for chunk in tuple(terms):
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", chunk):
            terms.update(chunk[index:index + 2] for index in range(min(len(chunk) - 1, 32)))
    return terms


def _accepted_interaction_decisions(thread_id: int) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.thread_id == int(thread_id),
            ORMChangeProposal.c.status.in_(("accepted", "applied")),
        ).order_by(ORMChangeProposal.c.id.desc()).limit(5)).mappings().all()
    return [
        {"operation": row["operation"], "after": _load(row["after_json"], {}), "rationale": row["rationale"]}
        for row in rows
    ]


def _bounded_history(messages: list[dict], token_budget: int) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    used = 0
    for item in reversed(messages):
        content = str(item.get("content") or "")
        cost = _estimate_tokens(content) + 8
        if selected and used + cost > token_budget:
            break
        selected.append({"role": str(item.get("role") or ""), "content": content})
        used += cost
        if used >= token_budget:
            break
    return list(reversed(selected))


def _estimate_tokens(text: str) -> int:
    return count_tokens(text)


def _truncate_tokens(text: str, token_budget: int) -> str:
    return truncate_tokens(text, token_budget)


def _resolve_current(thread: dict) -> dict:
    artifact_type = thread["artifact_type"]
    object_id = thread.get("object_id") or ""
    report_id = thread.get("report_id")
    if artifact_type == "report_title" and report_id is not None:
        with session_scope() as s:
            row = s.execute(select(ORMReport.c.title).where(ORMReport.c.id == int(report_id))).first()
        return {"title": row[0] if row else ""}
    if artifact_type == "sentence" and report_id is not None and str(object_id).isdigit():
        with session_scope() as s:
            row = s.execute(select(ORMSentence).where(
                ORMSentence.c.id == int(object_id), ORMSentence.c.report_id == int(report_id)
            )).mappings().first()
        return {"content": row["user_edit"] or row["content"], "source_refs": _load(row["source_refs"], {})} if row else {}
    if artifact_type == "task_brief":
        from app.memory import short_term
        task = short_term.load_task(str(thread.get("task_id") or "")) or {}
        scoped = dict((thread.get("scope") or {}).get("current") or {})
        return {**scoped,
            "theme": task.get("theme", ""),
            "requirements": task.get("user_requirements", ""),
            "stage": task.get("stage", ""),
        }
    if artifact_type == "material_role" and str(object_id).isdigit():
        with session_scope() as s:
            row = s.execute(select(ORMInsight).where(
                ORMInsight.c.task_id == str(thread.get("task_id") or ""),
                ORMInsight.c.material_id == int(object_id),
            ).order_by(ORMInsight.c.id.desc())).mappings().first()
        return {
            "material_role": row["material_role"],
            "claim_support": row["claim_support"],
            "allowed_usage": _load(row["allowed_usage"], []),
            "forbidden_usage": _load(row["forbidden_usage"], []),
        } if row else {}
    if artifact_type == "fact" and str(object_id).isdigit():
        with session_scope() as s:
            row = s.execute(select(ORMFact).where(
                ORMFact.c.id == int(object_id),
                ORMFact.c.task_id == str(thread.get("task_id") or ""),
            )).mappings().first()
        return {key: row[key] for key in ("id", "content", "dimension", "fact_type") if key in row} if row else {}
    if artifact_type == "inference" and str(object_id).isdigit():
        from app.memory import short_term
        task = short_term.load_task(str(thread.get("task_id") or "")) or {}
        if int(object_id) not in {
            int(item) for item in task.get("inference_ids") or [] if str(item).isdigit()
        }:
            return {}
        with session_scope() as s:
            row = s.execute(select(ORMInference).where(ORMInference.c.id == int(object_id))).mappings().first()
        return {
            "id": row["id"], "content": row["content"],
            "based_fact_ids": _load(row["based_fact_ids"], []),
            "confidence_level": row["confidence_level"],
            "reasoning_chain": row["reasoning_chain"],
        } if row else {}
    if artifact_type in {"analysis_plan", "final_plan"}:
        return _current_plan(str(thread.get("task_id") or ""), artifact_type)
    if artifact_type == "narrative_plan":
        return _current_narrative_plan(
            str(thread.get("task_id") or ""), str(thread.get("artifact_version") or ""), object_id,
        )
    return dict((thread.get("scope") or {}).get("current") or {})


def _impact_for(artifact_type: str, thread: dict) -> dict:
    mapping = {
        "task_brief": ["analysis_plan", "evidence", "analysis", "final_plan", "narrative_plan", "writing", "qa"],
        "material_role": ["evidence", "analysis", "final_plan", "narrative_plan", "writing", "qa"],
        "analysis_plan": ["evidence", "analysis", "final_plan", "narrative_plan", "writing", "qa"],
        "fact": ["analysis", "final_plan", "narrative_plan", "writing", "qa"],
        "inference": ["final_plan", "narrative_plan", "writing", "qa"],
        "final_plan": ["narrative_plan", "writing", "qa"],
        "narrative_plan": ["writing", "qa"],
        "report_title": ["qa", "render"],
        "section_title": ["qa", "render"],
        "paragraph": ["lineage_check", "qa", "render"],
        "sentence": ["lineage_check", "qa", "render"],
        "qa_issue": ["qa", "render"],
        "comparison_item": ["incremental_update_proposal"],
    }
    return {"invalidates": mapping.get(artifact_type, []), "scope": thread.get("scope") or {},
            "automatic_execution": artifact_type in {"report_title", "sentence"}}


def _default_risk(artifact_type: str) -> str:
    return "low" if artifact_type in {"report_title", "section_title", "paragraph", "sentence"} else "medium"


def _save_message(thread_id: int, role: str, content: str, metadata: dict | None = None) -> int:
    with session_scope() as s:
        result = s.execute(insert(ORMInteractionMessage).values(
            thread_id=int(thread_id), role=role, content=content,
            metadata_json=_dump(metadata or {}),
        ))
        s.execute(update(ORMInteractionThread).where(
            ORMInteractionThread.c.id == int(thread_id)
        ).values(updated_at=time.strftime("%Y-%m-%d %H:%M:%S")))
        return int(result.inserted_primary_key[0])


def _update_message_metadata(message_id: int, metadata: dict) -> None:
    with session_scope() as s:
        s.execute(update(ORMInteractionMessage).where(
            ORMInteractionMessage.c.id == int(message_id)
        ).values(metadata_json=_dump(metadata)))


def _thread_detail(row) -> dict:
    return {
        "id": int(row["id"]), "thread_key": row["thread_key"], "task_id": row["task_id"],
        "report_id": row["report_id"], "artifact_type": row["artifact_type"],
        "artifact_version": row["artifact_version"], "object_id": row["object_id"],
        "scope": _load(row["scope_json"], {}), "status": row["status"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _thread_summary(thread: dict[str, Any]) -> dict[str, Any]:
    """Return the compact, auditable metadata needed by the session picker."""
    result = {key: thread.get(key) for key in (
        "id", "thread_key", "task_id", "report_id", "artifact_type",
        "artifact_version", "object_id", "scope", "status", "created_at", "updated_at",
    )}
    messages = list(thread.get("messages") or [])
    proposals = list(thread.get("proposals") or [])
    last_message = messages[-1] if messages else None
    result.update({
        "message_count": len(messages),
        "proposal_count": len(proposals),
        "pending_proposal_count": sum(
            1 for item in proposals if item.get("status") == "proposed"
        ),
        "last_message": {
            "role": last_message.get("role"),
            "content": str(last_message.get("content") or "")[:160],
            "created_at": last_message.get("created_at"),
        } if last_message else None,
        "pending": bool(thread.get("pending")),
    })
    return result


def _message_detail(row) -> dict:
    return {"id": int(row["id"]), "role": row["role"], "content": row["content"],
            "metadata": _load(row["metadata_json"], {}), "created_at": row["created_at"]}


def _proposal_detail(row) -> dict:
    return {
        "id": int(row["id"]), "proposal_key": row["proposal_key"], "thread_id": int(row["thread_id"]),
        "task_id": row["task_id"], "report_id": row["report_id"], "artifact_type": row["artifact_type"],
        "artifact_version": row["artifact_version"], "object_id": row["object_id"],
        "operation": row["operation"], "before": _load(row["before_json"], {}),
        "after": _load(row["after_json"], {}), "impact": _load(row["impact_json"], {}),
        "rationale": row["rationale"], "risk_level": row["risk_level"], "status": row["status"],
        "execution_status": row.get("execution_status") or "not_required",
        "execution_run_id": row.get("execution_run_id") or "",
        "base_version_id": row.get("base_version_id"),
        "candidate_version_id": row.get("candidate_version_id"),
        "execution_error": row.get("execution_error") or "",
        "created_at": row["created_at"], "decided_at": row["decided_at"],
    }


_RECOMPUTE_ORDER = {
    "task_brief": 1,
    "material_role": 2,
    "analysis_plan": 2,
    "fact": 2,
    "inference": 3,
    "final_plan": 4,
    "narrative_plan": 5,
    "paragraph": 5,
    "qa_issue": 5,
    "comparison_item": 5,
}


def dispatch_pending_revisions(task_id: str) -> dict[str, Any]:
    """Batch accepted semantic proposals into one non-blocking revision run."""
    if not task_id:
        return {"status": "missing_task"}
    from app.memory import short_term
    from app.workflow.queue import enqueue_task, task_queue_status

    task = short_term.load_task(task_id) or {}
    queues = task_queue_status()
    if (
        queues.get("running_task_id") == task_id
        or task_id in set(queues.get("queued_task_ids") or [])
        or str(task.get("stage") or "") not in {"review", "done", "failed", "paused"}
        or not task.get("report_id")
    ):
        return {"status": "waiting"}
    with session_scope() as s:
        rows = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.task_id == task_id,
            ORMChangeProposal.c.status == "accepted",
            ORMChangeProposal.c.execution_status == "waiting",
        ).order_by(ORMChangeProposal.c.id)).mappings().all()
    if not rows:
        return {"status": "empty"}
    try:
        prepared = _prepare_revision_run(task_id, [dict(row) for row in rows])
    except Exception as exc:
        proposal_ids = [int(row["id"]) for row in rows]
        with session_scope() as s:
            s.execute(update(ORMChangeProposal).where(
                ORMChangeProposal.c.id.in_(proposal_ids)
            ).values(execution_status="failed", execution_error=str(exc)[:1000]))
        _notify(
            task_id=task_id, report_id=task.get("report_id"), notification_type="revision_failed",
            title="后台修改任务创建失败", message=str(exc)[:240],
            action_url=f"/tasks/{task_id}?tab=collaboration",
            metadata={"proposal_ids": proposal_ids},
        )
        return {"status": "failed", "error": str(exc)}
    queue = enqueue_task(task_id)
    return {**prepared, "queue": queue}


def _prepare_revision_run(task_id: str, proposals: list[dict]) -> dict[str, Any]:
    from app.memory import short_term
    from app.report_versions import create_incremental_delta, ensure_report_version, get_report_version
    from app.task_runs import create_task_run

    base_task = short_term.load_task(task_id) or {}
    report_id = int(base_task.get("report_id") or 0)
    if not report_id:
        raise ValueError("REPORT_NOT_READY")
    version = ensure_report_version(
        report_id, task_id=task_id, status="snapshot",
        change_summary="交互式修改前自动生成基线快照", kind="minor",
    )
    base_version = get_report_version(version.version_id)
    if base_version is None:
        raise ValueError("BASE_VERSION_NOT_FOUND")
    proposal_ids = [int(item["id"]) for item in proposals]
    earliest = min(_RECOMPUTE_ORDER.get(str(item.get("artifact_type") or ""), 5) for item in proposals)
    instructions = [_proposal_instruction(item) for item in proposals]
    update_reason = "根据用户已批准的交互提案生成候选版本：\n" + "\n".join(instructions)
    revision = max(1, int(base_task.get("run_revision") or 1)) + 1
    run_id = create_task_run(
        task_id, revision=revision, run_mode="interaction_revision",
        base_version_id=int(base_version["id"]), update_reason=update_reason,
    )
    delta = create_incremental_delta(report_id, [], update_reason=update_reason, run_id=run_id)
    plan_snapshot = base_version.get("report_plan_snapshot") or {}
    old_fact_ids = _snapshot_ids(base_version.get("fact_snapshot") or [])
    inference_rows = base_version.get("inference_snapshot") or []
    old_inference_ids = [int(item["id"]) for item in inference_rows
                         if item.get("id") is not None and item.get("source_level") != "EXTERNAL_INFORMATION"]
    old_external_ids = [int(item["id"]) for item in inference_rows
                        if item.get("id") is not None and item.get("source_level") == "EXTERNAL_INFORMATION"]
    old_conflict_ids = _snapshot_ids(base_version.get("conflict_snapshot") or [])
    force_evidence = earliest <= 2
    force_analysis = earliest <= 3
    force_final_plan = earliest <= 4
    rerun_initial_plan = force_evidence
    run_history = list(base_task.get("run_history") or [])
    run_history.append({
        "revision": int(base_task.get("run_revision") or 1),
        "mode": str(base_task.get("run_mode") or "initial"),
        "stage": str(base_task.get("stage") or ""),
        "report_version_id": int(base_version["id"]),
        "finished_at": (base_task.get("queue_status") or {}).get("finished_at"),
    })
    next_payload = dict(base_task)
    next_payload.update({
        "stage": "created", "run_revision": revision, "run_id": run_id,
        "run_mode": "interaction_revision", "run_history": run_history[-50:],
        "report_id": report_id,
        "plan_id": None if rerun_initial_plan else base_task.get("plan_id"),
        "plan_title": plan_snapshot.get("title") or base_task.get("plan_title", ""),
        "fact_ids": [] if force_evidence else old_fact_ids,
        "inference_ids": [] if force_analysis else old_inference_ids,
        "external_ids": [] if force_analysis else old_external_ids,
        "conflict_ids": [] if force_evidence else old_conflict_ids,
        "incremental_update": True,
        "incremental_base_task_id": task_id,
        "incremental_base_version_id": int(base_version["id"]),
        "incremental_delta_id": delta.get("id"),
        "incremental_added_material_ids": [],
        "incremental_update_reason": update_reason,
        "incremental_inherited_fact_ids": [] if force_evidence else old_fact_ids,
        "incremental_inherited_inference_ids": [] if force_analysis else old_inference_ids,
        "incremental_inherited_external_ids": [] if force_analysis else old_external_ids,
        "incremental_inherited_conflict_ids": [] if force_evidence else old_conflict_ids,
        "incremental_new_fact_ids": [],
        "incremental_generated_inference_ids": [],
        "incremental_generated_external_ids": [],
        "incremental_plan": {}, "incremental_delta": {},
        "incremental_structure_review_required": False,
        "analysis_done": not force_analysis,
        "final_plan_frozen": not force_final_plan and bool(plan_snapshot.get("structure")),
        "intervention_proposal_ids": proposal_ids,
        "intervention_recompute_from": (
            "evidence" if force_evidence else "analysis" if force_analysis
            else "final_plan" if force_final_plan else "writing"
        ),
        "intervention_force_evidence": force_evidence,
        "intervention_force_analysis": force_analysis,
        "intervention_force_final_plan": force_final_plan,
        "intervention_rewrite_all": earliest <= 4,
        "parse_progress": {}, "evidence_progress": {}, "write_progress": {},
        "qa_notes": [], "stage_timings": {}, "stage_durations": {},
        "llm_stats": {}, "token_efficiency": {}, "workload_profile": {},
        "resource_samples": [], "artifact_status": {},
        "queue_status": {"status": "created"}, "control_request": "", "error": "",
        "critical_path_done": False,
    })
    short_term.save_task(task_id, next_payload)
    with session_scope() as s:
        s.execute(update(ORMChangeProposal).where(
            ORMChangeProposal.c.id.in_(proposal_ids)
        ).values(
            execution_status="queued", execution_run_id=run_id,
            base_version_id=int(base_version["id"]), execution_error="",
        ))
    return {"status": "queued", "run_id": run_id, "proposal_ids": proposal_ids,
            "base_version_id": int(base_version["id"]), "recompute_from": next_payload["intervention_recompute_from"]}


def complete_recompute_for_run(run_id: str, candidate_version_id: int) -> None:
    if not run_id:
        return
    with session_scope() as s:
        rows = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.execution_run_id == run_id
        )).mappings().all()
        if not rows:
            return
        s.execute(update(ORMChangeProposal).where(
            ORMChangeProposal.c.execution_run_id == run_id
        ).values(
            status="accepted", execution_status="completed",
            candidate_version_id=int(candidate_version_id), execution_error="",
        ))
    first = rows[0]
    _notify(
        task_id=str(first["task_id"]), report_id=first["report_id"],
        notification_type="candidate_ready", title="交互修改候选版本已生成",
        message="后台重算已完成，请在版本审阅中比较并决定保留哪些变化。",
        action_url=f"/reports/{first['report_id']}?version={int(candidate_version_id)}",
        metadata={"run_id": run_id, "candidate_version_id": int(candidate_version_id),
                  "proposal_ids": [int(row["id"]) for row in rows]},
    )


def mark_recompute_running(run_id: str) -> None:
    if not run_id:
        return
    with session_scope() as s:
        s.execute(update(ORMChangeProposal).where(
            ORMChangeProposal.c.execution_run_id == run_id,
            ORMChangeProposal.c.execution_status == "queued",
        ).values(execution_status="running"))


def fail_recompute_for_run(run_id: str, error: str) -> None:
    if not run_id:
        return
    with session_scope() as s:
        rows = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.execution_run_id == run_id
        )).mappings().all()
        if not rows:
            return
        s.execute(update(ORMChangeProposal).where(
            ORMChangeProposal.c.execution_run_id == run_id
        ).values(execution_status="failed", execution_error=error[:1000]))
    first = rows[0]
    _notify(
        task_id=str(first["task_id"]), report_id=first["report_id"],
        notification_type="revision_failed", title="交互修改后台处理失败",
        message=error[:240], action_url=f"/tasks/{first['task_id']}?tab=collaboration",
        metadata={"run_id": run_id},
    )


def list_notifications(*, task_id: str = "", report_id: int | None = None) -> list[dict[str, Any]]:
    query = select(ORMInteractionNotification)
    if task_id:
        query = query.where(ORMInteractionNotification.c.task_id == task_id)
    elif report_id is not None:
        query = query.where(ORMInteractionNotification.c.report_id == int(report_id))
    else:
        return []
    with session_scope() as s:
        rows = s.execute(query.order_by(ORMInteractionNotification.c.id.desc()).limit(50)).mappings().all()
    return [{
        "id": int(row["id"]), "task_id": row["task_id"], "report_id": row["report_id"],
        "type": row["notification_type"], "title": row["title"], "message": row["message"],
        "status": row["status"], "action_url": row["action_url"],
        "metadata": _load(row["metadata_json"], {}), "created_at": row["created_at"],
    } for row in rows]


def mark_notification_read(notification_id: int) -> bool:
    with session_scope() as s:
        changed = s.execute(update(ORMInteractionNotification).where(
            ORMInteractionNotification.c.id == int(notification_id)
        ).values(status="read", read_at=time.strftime("%Y-%m-%d %H:%M:%S"))).rowcount
    return bool(changed)


def review_workspace(task_id: str, artifact_type: str = "task_brief", query: str = "",
                     offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Return a compact, paged view of reviewable workflow artifacts."""
    from app.memory import short_term
    task = short_term.load_task(task_id)
    if task is None:
        raise ValueError("TASK_NOT_FOUND")
    groups = _review_groups(task_id, task)
    allowed = {item["artifact_type"] for item in groups}
    selected = artifact_type if artifact_type in allowed else "task_brief"
    items = _review_items(task_id, task, selected)
    needle = query.strip().lower()
    if needle:
        items = [item for item in items if needle in _dump(item).lower()]
    total = len(items)
    page = items[max(0, offset):max(0, offset) + max(1, min(int(limit), 100))]
    return {"groups": groups, "artifact_type": selected, "items": page,
            "total": total, "offset": max(0, offset), "limit": min(int(limit), 100)}


def _review_groups(task_id: str, task: dict) -> list[dict]:
    with session_scope() as s:
        narrative_count = s.execute(select(ORMTaskArtifact.c.id).where(
            ORMTaskArtifact.c.task_id == task_id,
            ORMTaskArtifact.c.stage.like("narrative_plan:%"),
        )).all()
    counts = {
        "task_brief": 1,
        "material_role": len(task.get("material_insights") or []),
        "analysis_plan": 1 if task.get("plan_id") else 0,
        "fact": len(task.get("fact_ids") or []),
        "inference": len(task.get("inference_ids") or []) + len(task.get("external_ids") or []),
        "final_plan": 1 if task.get("final_plan_frozen") else 0,
        "narrative_plan": len(narrative_count),
        "qa_issue": len(task.get("qa_notes") or []) + len(task.get("qa_results") or []),
    }
    labels = {
        "task_brief": "任务目标", "material_role": "材料理解", "analysis_plan": "分析规划",
        "fact": "事实", "inference": "分析判断", "final_plan": "最终目录",
        "narrative_plan": "叙事计划",
        "qa_issue": "质量问题",
    }
    return [{"artifact_type": key, "label": labels[key], "count": value,
             "available": bool(value)} for key, value in counts.items()]


def _review_items(task_id: str, task: dict, artifact_type: str) -> list[dict]:
    version = str(task.get("run_revision") or 1)
    if artifact_type == "task_brief":
        current = {"theme": task.get("theme", ""), "requirements": task.get("user_requirements", ""),
                   "stage": task.get("stage", "")}
        return [_review_item(artifact_type, "", "任务目标与报告要求", current, version)]
    if artifact_type == "material_role":
        return [_review_item(
            artifact_type, str(item.get("material_id") or ""),
            str(item.get("filename") or item.get("topic") or "材料"),
            {key: item.get(key) for key in ("doc_type", "topic", "material_role", "claim_support",
                                             "allowed_usage", "forbidden_usage", "missing_information")}, version,
        ) for item in task.get("material_insights") or []]
    if artifact_type in {"analysis_plan", "final_plan"}:
        current = _current_plan(task_id, artifact_type)
        return [_review_item(artifact_type, str(task.get("plan_id") or ""),
                             "分析范围与问题" if artifact_type == "analysis_plan" else "最终报告结构",
                             current, version)] if current else []
    if artifact_type == "fact":
        ids = [int(value) for value in task.get("fact_ids") or [] if str(value).isdigit()]
        with session_scope() as s:
            rows = s.execute(select(ORMFact).where(ORMFact.c.id.in_(ids))).mappings().all() if ids else []
        by_id = {int(row["id"]): row for row in rows}
        return [_review_item(artifact_type, str(fid), str(by_id[fid]["content"])[:90], {
            "id": fid, "content": by_id[fid]["content"], "dimension": by_id[fid]["dimension"],
            "fact_type": by_id[fid]["fact_type"],
        }, version) for fid in ids if fid in by_id]
    if artifact_type == "inference":
        ids = [int(value) for value in [*(task.get("inference_ids") or []), *(task.get("external_ids") or [])]
               if str(value).isdigit()]
        with session_scope() as s:
            rows = s.execute(select(ORMInference).where(ORMInference.c.id.in_(ids))).mappings().all() if ids else []
        by_id = {int(row["id"]): row for row in rows}
        return [_review_item(artifact_type, str(iid), str(by_id[iid]["content"])[:90], {
            "id": iid, "content": by_id[iid]["content"],
            "based_fact_ids": _load(by_id[iid]["based_fact_ids"], []),
            "confidence_level": by_id[iid]["confidence_level"],
            "reasoning_chain": by_id[iid]["reasoning_chain"],
        }, version) for iid in ids if iid in by_id]
    if artifact_type == "narrative_plan":
        with session_scope() as s:
            rows = s.execute(select(ORMTaskArtifact).where(
                ORMTaskArtifact.c.task_id == task_id,
                ORMTaskArtifact.c.stage.like("narrative_plan:%"),
            ).order_by(ORMTaskArtifact.c.id.desc())).mappings().all()
        seen = set()
        result = []
        for row in rows:
            chapter = str(row["stage"]).split(":", 1)[-1]
            if chapter in seen:
                continue
            seen.add(chapter)
            result.append(_review_item(artifact_type, chapter, chapter, _load(row["payload"], {}),
                                       str(row["run_id"] or version)))
        return result
    if artifact_type == "qa_issue":
        issues = [*(task.get("qa_notes") or []), *(task.get("qa_results") or [])]
        return [_review_item(
            artifact_type, str(index), str(item.get("type") or item.get("section") or f"质量问题 {index + 1}"),
            item if isinstance(item, dict) else {"content": str(item)}, version,
        ) for index, item in enumerate(issues)]
    return []


def _current_plan(task_id: str, artifact_type: str) -> dict:
    from app.memory import short_term
    task = short_term.load_task(task_id) or {}
    plan_id = task.get("plan_id")
    if not plan_id:
        return {}
    with session_scope() as s:
        row = s.execute(select(ORMPlan).where(ORMPlan.c.id == int(plan_id))).mappings().first()
    if row is None:
        return {}
    key = "analysis_plan_json" if artifact_type == "analysis_plan" else "final_plan_json"
    explicit = _load(row.get(key), {})
    if explicit:
        return explicit
    return {
        "title": row["title"], "objective": row["objective"], "core_question": row["core_question"],
        "core_judgment": row["core_judgment"], "narrative_logic": row["narrative_logic"],
        "dimensions": _load(row["dimensions"], []), "structure": _load(row["structure"], []),
        "chapter_plans": _load(row["chapter_plans"], []), "budget": _load(row["budget"], {}),
    }


def _current_narrative_plan(task_id: str, run_id: str, object_id: str) -> dict:
    with session_scope() as s:
        query = select(ORMTaskArtifact).where(
            ORMTaskArtifact.c.task_id == task_id,
            ORMTaskArtifact.c.stage == f"narrative_plan:{object_id}",
        )
        if run_id:
            query = query.where(ORMTaskArtifact.c.run_id == run_id)
        row = s.execute(query.order_by(ORMTaskArtifact.c.id.desc())).mappings().first()
    return _load(row["payload"], {}) if row else {}


def _review_item(artifact_type: str, object_id: str, title: str, current: dict, version: str) -> dict:
    return {"artifact_type": artifact_type, "object_id": object_id, "title": title,
            "summary": _artifact_summary(artifact_type, current), "current": current,
            "artifact_version": version}


def _artifact_summary(artifact_type: str, current: dict) -> str:
    if artifact_type == "task_brief":
        return str(current.get("requirements") or current.get("theme") or "")[:180]
    if artifact_type == "material_role":
        return " · ".join(str(current.get(key) or "") for key in ("material_role", "topic") if current.get(key))[:180]
    if artifact_type in {"analysis_plan", "final_plan"}:
        chapters = current.get("structure") or current.get("chapter_plans") or []
        return f"{len(chapters)} 个结构单元 · {str(current.get('core_judgment') or current.get('objective') or '')[:120]}"
    if artifact_type == "narrative_plan":
        return f"{len(current.get('subsections') or [])} 个小节 · {str(current.get('core_message') or '')[:120]}"
    return str(current.get("content") or "")[:180]


def _proposal_instruction(row: dict) -> str:
    return (
        f"- [{row.get('artifact_type')}/{row.get('object_id') or '整体'}] "
        f"{row.get('rationale') or '按批准提案调整'}；建议变更：{_dump(_load(row.get('after_json'), {}))[:1800]}"
    )


def _snapshot_ids(items: list[dict]) -> list[int]:
    return [int(item["id"]) for item in items if item.get("id") is not None]


def _notify(*, task_id: str, report_id: int | None, notification_type: str,
            title: str, message: str, action_url: str, metadata: dict) -> None:
    with session_scope() as s:
        s.execute(insert(ORMInteractionNotification).values(
            task_id=task_id, report_id=report_id, notification_type=notification_type,
            title=title, message=message, status="unread", action_url=action_url,
            metadata_json=_dump(metadata),
        ))


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
