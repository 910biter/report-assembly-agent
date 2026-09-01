"""Asynchronous, scoped user intervention and auditable change proposals."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import insert, select, update

from app.db import session_scope
from app.infrastructure.orm import (
    ORMChangeProposal,
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
from app.context_budget import count_tokens
from app.token_monitor import log_llm_call, new_call_id, token_context
from app.task_collaboration_agent import run_task_collaboration_agent
from app.task_draft_agent import run_task_draft_agent
from app.interaction_policy import propagation_policy
from app.control_agent import (
    AgentToolName,
    ArtifactFocus,
    build_task_agent_context,
    execute_control_tool,
)

INTERACTION_THREAD_TYPES = {"task_draft", "task_control"}

_INTERACTION_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(2, int(settings.interactive_concurrency or 1)),
    thread_name_prefix="interaction",
)
_PROPOSAL_RETRY_LOCK = threading.RLock()


def _tool_confirmation_reply(tool_call) -> str:
    """Describe a pending action once; proposal cards own the detailed diff."""
    if tool_call.tool_name == AgentToolName.RERUN_FINAL_PLAN:
        structure = list(tool_call.arguments.get("new_structure") or [])
        count = int(tool_call.arguments.get("required_chapter_count") or len(structure) or 0)
        scope = f"{count}章目录" if count else "目录调整"
        return f"已整理为{scope}变更提案。请在下方核对调整后的目录；接受后再重组相关章节和正文。"
    return f"{tool_call.reason}。请在下方核对变更内容；接受后系统才会执行。"


def create_thread(*, task_id: str = "", report_id: int | None = None,
                  artifact_type: str, artifact_version: str = "", object_id: str = "",
                  scope: dict | None = None) -> dict[str, Any]:
    if artifact_type not in INTERACTION_THREAD_TYPES:
        raise ValueError("INVALID_ARTIFACT_TYPE")
    if not task_id and report_id is not None:
        with session_scope() as s:
            report = s.execute(select(ORMReport.c.task_id).where(
                ORMReport.c.id == int(report_id)
            )).first()
        task_id = str(report[0] or "") if report else ""
        if not task_id:
            raise ValueError("REPORT_TASK_NOT_FOUND")
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


def get_thread(thread_id: int, *, include_decision_memory: bool = True) -> dict[str, Any] | None:
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
    if include_decision_memory and result.get("task_id"):
        result["decision_memory"] = _task_decision_memory(
            str(result["task_id"]), dict((result.get("scope") or {}).get("focus") or {}),
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
        rows = s.execute(query.order_by(ORMInteractionThread.c.updated_at.desc())).mappings().all()
    summaries = []
    for row in rows:
        detail = get_thread(int(row["id"]), include_decision_memory=False) or _thread_detail(row)
        summaries.append(_thread_summary(detail))
    return summaries


def close_thread(thread_id: int) -> dict[str, Any]:
    """Close the visible conversation while retaining its audit trail."""
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError("INTERACTION_THREAD_NOT_FOUND")
    # Proposal execution is independent from the conversation lifecycle. It
    # remains auditable by proposal id and may finish after the user starts a
    # new discussion; only an unfinished assistant turn must keep this thread
    # open so its response has a valid destination.
    if thread.get("pending"):
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
            task_id=task_id, artifact_type="task_control", object_id="",
            artifact_version="", updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        ))
        s.execute(update(ORMChangeProposal).where(
            ORMChangeProposal.c.thread_id.in_(thread_ids)
        ).values(task_id=task_id, artifact_type="task_brief", object_id="", artifact_version="1"))
    return len(thread_ids)


def post_message(thread_id: int, content: str, context: dict | None = None) -> dict[str, Any]:
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError("INTERACTION_THREAD_NOT_FOUND")
    if thread.get("status") != "open":
        raise ValueError("INTERACTION_THREAD_CLOSED")
    content = content.strip()
    if not content:
        raise ValueError("EMPTY_MESSAGE")
    _update_thread_focus(thread_id, context)
    message_id = _save_message(thread_id, "user", content, {"status": "running"})
    try:
        result = _generate_interaction_reply(thread_id, content)
        _update_message_metadata(message_id, {"status": "completed"})
    except Exception as exc:
        _update_message_metadata(message_id, {"status": "failed", "error": str(exc)[:300]})
        raise
    return {**result, "thread": get_thread(thread_id)}


def queue_message(thread_id: int, content: str, request_id: str = "",
                  context: dict | None = None) -> dict[str, Any]:
    """Persist a user turn and return immediately; inference continues in background."""
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError("INTERACTION_THREAD_NOT_FOUND")
    if thread.get("status") != "open":
        raise ValueError("INTERACTION_THREAD_CLOSED")
    content = content.strip()
    if not content:
        raise ValueError("EMPTY_MESSAGE")
    _update_thread_focus(thread_id, context)
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


def _update_thread_focus(thread_id: int, context: dict | None) -> None:
    if not isinstance(context, dict) or not context:
        return
    with session_scope() as s:
        row = s.execute(select(ORMInteractionThread.c.scope_json).where(
            ORMInteractionThread.c.id == int(thread_id)
        )).first()
        if row is None:
            return
        scope = _load(row[0], {})
        scope["focus"] = ArtifactFocus.model_validate(context).model_dump(mode="json")
        s.execute(update(ORMInteractionThread).where(
            ORMInteractionThread.c.id == int(thread_id)
        ).values(scope_json=_dump(scope), updated_at=time.strftime("%Y-%m-%d %H:%M:%S")))


def _run_queued_interaction(thread_id: int, message_id: int, request_id: str, content: str) -> None:
    _update_message_metadata(message_id, {"status": "running", "request_id": request_id})
    try:
        _generate_interaction_reply(thread_id, content, request_id=request_id)
        _update_message_metadata(message_id, {"status": "completed", "request_id": request_id})
    except Exception as exc:
        _save_message(
            thread_id, "assistant", _friendly_interaction_error(exc),
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
    retried = _retry_failed_proposal(thread, content)
    if retried is not None:
        reply = "已按原修改方案重新执行。系统完整复用上次确认的范围和参数，不会重新解释或改动你的要求。"
        metadata = {
            "status": "completed", "intent": "execute",
            "proposal_id": int(retried["id"]), "retry_of_proposal_id": retried["retry_of_proposal_id"],
        }
        if request_id:
            metadata["request_id"] = request_id
        _save_message(thread_id, "assistant", reply, metadata)
        return {"reply": reply, "proposal": retried}
    proposal_decision = _pending_proposal_decision(thread, content)
    if proposal_decision is not None:
        proposal, decision = proposal_decision
        result = decide_proposal(int(proposal["id"]), decision)
        if decision == "accepted":
            if str(thread.get("artifact_type") or "") == "task_draft":
                reply = "已将这份需求写入新建任务草稿。你仍可继续调整，确认无误后再创建任务。"
            else:
                state = "已执行" if result.get("status") == "applied" else "已进入后台处理"
                reply = f"已确认该操作，{state}。现有报告不会被静默覆盖；语义修改完成后会生成候选版本供审阅。"
        else:
            reply = "已取消该操作，现有任务和报告不会发生变化。"
        metadata = {"status": "completed", "intent": "execute", "proposal_id": int(proposal["id"])}
        if request_id:
            metadata["request_id"] = request_id
        _save_message(thread_id, "assistant", reply, metadata)
        return {"reply": reply, "proposal": result}
    task_id = str(thread.get("task_id") or "")
    if task_id:
        focus = dict((thread.get("scope") or {}).get("focus") or {})
        return _run_task_agent_interaction(
            thread=thread,
            current=current,
            content=content,
            request_id=request_id,
            agent_context=build_task_agent_context(task_id, focus=focus),
        )
    return _run_draft_agent_interaction(
        thread=thread,
        current=current,
        content=content,
        request_id=request_id,
    )


def _run_task_agent_interaction(
    *,
    thread: dict,
    current: dict,
    content: str,
    request_id: str,
    agent_context,
) -> dict[str, Any]:
    """Run the task-scoped tool loop while preserving the proposal workflow."""
    messages = list(thread.get("messages") or [])
    if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == content:
        messages = messages[:-1]
    history = _bounded_history(messages, int(settings.interactive_history_tokens or 3072))
    decision_memory = _task_decision_memory(
        str(thread.get("task_id") or ""),
        dict((thread.get("scope") or {}).get("focus") or {}),
    )
    input_chars = (
        sum(len(str(item.get("content") or "")) for item in history)
        + len(content) + len(_dump(decision_memory))
    )
    call_id = new_call_id()
    started = time.time()
    context_audit = {
        "agent_framework": "pydantic_ai",
        "history_messages": len(history),
        "confirmed_decisions": len(decision_memory.get("confirmed_decisions") or []),
        "pending_decisions": len(decision_memory.get("pending_decisions") or []),
    }
    try:
        with token_context(task_id=str(thread.get("task_id") or ""), stage="interaction"):
            with llm_priority(PRIORITY_INTERACTIVE):
                result = invoke(
                    "task_collaboration_agent",
                    run_task_collaboration_agent,
                    agent_context,
                    content,
                    history,
                    decision_memory,
                )
            context_audit.update({
                "requests": int(result.usage.get("requests") or 0),
                "tool_calls": int(result.usage.get("tool_calls") or 0),
                "cache_read_tokens": int(result.usage.get("cache_read_tokens") or 0),
                "tool_trace": list(result.tool_trace),
            })
            log_llm_call(
                call_id,
                "task_collaboration_agent",
                input_chars,
                {
                    "prompt_tokens": int(result.usage.get("input_tokens") or 0),
                    "output_tokens": int(result.usage.get("output_tokens") or 0),
                    "total_seconds": time.time() - started,
                },
                time.time() - started,
                success=True,
                returned_chars=len(result.reply),
                context_audit=context_audit,
            )
    except Exception as exc:
        with token_context(task_id=str(thread.get("task_id") or ""), stage="interaction"):
            log_llm_call(
                call_id,
                "task_collaboration_agent",
                input_chars,
                {},
                time.time() - started,
                success=False,
                error=str(exc),
                context_audit=context_audit,
            )
        raise

    tool_call = result.tool_call
    reply = str(result.reply or "").strip() or "已完成本轮分析。"
    if tool_call is not None:
        reply = _tool_confirmation_reply(tool_call)
    metadata = {
        "status": "completed",
        "intent": "propose" if tool_call is not None else "answer",
        "tool_call": tool_call.model_dump(mode="json") if tool_call else None,
        "agent_trace": list(result.tool_trace),
    }
    if request_id:
        metadata["request_id"] = request_id
    assistant_message_id = _save_message(int(thread["id"]), "assistant", reply, metadata)
    proposal_detail = None
    if tool_call is not None:
        proposal_detail = _create_tool_action_proposal(
            thread,
            current,
            tool_call.model_dump(mode="json"),
            source_message_id=assistant_message_id,
        )
    return {"reply": reply, "proposal": proposal_detail}


def _run_draft_agent_interaction(
    *, thread: dict, current: dict, content: str, request_id: str,
) -> dict[str, Any]:
    """Run the pre-task assistant through the same typed tool architecture."""
    messages = list(thread.get("messages") or [])
    if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == content:
        messages = messages[:-1]
    history = _bounded_history(messages, int(settings.interactive_history_tokens or 3072))
    input_chars = sum(len(str(item.get("content") or "")) for item in history) + len(content) + len(_dump(current))
    call_id = new_call_id()
    started = time.time()
    context_audit = {
        "agent_framework": "pydantic_ai",
        "agent_name": "task_draft_agent",
        "history_messages": len(history),
    }
    try:
        with token_context(task_id="", stage="interaction"):
            with llm_priority(PRIORITY_INTERACTIVE):
                result = invoke(
                    "task_draft_agent", run_task_draft_agent,
                    current, content, history,
                )
            context_audit.update({
                "requests": int(result.usage.get("requests") or 0),
                "tool_calls": int(result.usage.get("tool_calls") or 0),
                "cache_read_tokens": int(result.usage.get("cache_read_tokens") or 0),
                "tool_trace": list(result.tool_trace),
            })
            log_llm_call(
                call_id, "task_draft_agent", input_chars,
                {
                    "prompt_tokens": int(result.usage.get("input_tokens") or 0),
                    "output_tokens": int(result.usage.get("output_tokens") or 0),
                    "total_seconds": time.time() - started,
                },
                time.time() - started, success=True,
                returned_chars=len(result.reply), context_audit=context_audit,
            )
    except Exception as exc:
        with token_context(task_id="", stage="interaction"):
            log_llm_call(
                call_id, "task_draft_agent", input_chars, {}, time.time() - started,
                success=False, error=str(exc), context_audit=context_audit,
            )
        raise

    proposal_after = result.proposal_after
    reply = str(result.reply or "").strip() or (
        "已形成一份完整需求草案，请在下方审阅。"
        if proposal_after else "已完成本轮需求分析。"
    )
    metadata = {
        "status": "completed",
        "intent": "propose" if proposal_after else "answer",
        "tool_call": {"tool_name": "propose_task_draft"} if proposal_after else None,
        "agent_trace": list(result.tool_trace),
    }
    if request_id:
        metadata["request_id"] = request_id
    assistant_message_id = _save_message(int(thread["id"]), "assistant", reply, metadata)
    proposal_detail = None
    if proposal_after:
        proposal_detail = _create_proposal(
            thread,
            current,
            {
                "operation": "update",
                "after": proposal_after,
                "rationale": "根据当前主题和本轮讨论形成可执行的任务需求草案",
                "risk_level": "low",
            },
            source_message_id=assistant_message_id,
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


_RETRY_FAILED_PATTERN = re.compile(r"^(?:请)?(?:再试一遍|重试|重新执行|再执行一次)[！!。\s]*$")


def _retry_failed_proposal(thread: dict, message: str) -> dict[str, Any] | None:
    """Retry an accepted failed proposal without asking the model to reinterpret it."""
    if not _RETRY_FAILED_PATTERN.fullmatch(str(message or "").strip()):
        return None
    failed = _latest_failed_proposal(thread.get("proposals") or [])
    if failed is None:
        return None
    thread_id = int(thread["id"])
    with _PROPOSAL_RETRY_LOCK:
        with session_scope() as s:
            active = s.execute(select(ORMChangeProposal).where(
                ORMChangeProposal.c.thread_id == thread_id,
                ORMChangeProposal.c.status == "accepted",
                ORMChangeProposal.c.execution_status.in_(["waiting", "queued", "running"]),
            ).order_by(ORMChangeProposal.c.id.desc())).mappings().first()
            if active is not None:
                detail = _proposal_detail(active)
                detail["retry_of_proposal_id"] = int(failed["id"])
                return detail
            source = s.execute(select(ORMChangeProposal).where(
                ORMChangeProposal.c.id == int(failed["id"]),
                ORMChangeProposal.c.thread_id == thread_id,
            )).mappings().first()
            if source is None or source["execution_status"] != "failed":
                return None
            result = s.execute(insert(ORMChangeProposal).values(
                proposal_key=uuid.uuid4().hex,
                thread_id=thread_id,
                task_id=source["task_id"], report_id=source["report_id"],
                artifact_type=source["artifact_type"], artifact_version=source["artifact_version"],
                object_id=source["object_id"], operation=source["operation"],
                before_json=source["before_json"], after_json=source["after_json"],
                impact_json=source["impact_json"],
                rationale=f"重试提案 #{int(source['id'])}：{source['rationale']}",
                risk_level=source["risk_level"], status="accepted",
                execution_status="waiting", execution_error="",
                decided_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            ))
            retry_id = int(result.inserted_primary_key[0])
    dispatch_pending_revisions(str(source["task_id"] or ""))
    with session_scope() as s:
        row = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.id == retry_id
        )).mappings().first()
    detail = _proposal_detail(row)
    detail["retry_of_proposal_id"] = int(source["id"])
    return detail


def _latest_failed_proposal(proposals: list[dict]) -> dict | None:
    if any(item.get("status") == "proposed" for item in proposals):
        return None
    executed = [
        item for item in proposals
        if item.get("status") == "accepted"
        and item.get("execution_status") in {"waiting", "queued", "running", "completed", "failed"}
    ]
    latest = max(executed, key=lambda item: int(item.get("id") or 0)) if executed else None
    return latest if latest and latest.get("execution_status") == "failed" else None


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


def _create_tool_action_proposal(thread: dict, current: dict, tool_call: dict,
                                 source_message_id: int | None = None) -> dict:
    """Represent a mutating tool call with the existing auditable proposal flow."""
    tool_name = str(tool_call.get("tool_name") or "")
    arguments = dict(tool_call.get("arguments") or {})
    target = dict(thread)
    if target.get("report_id") is None and str(thread.get("task_id") or ""):
        from app.memory import short_term
        task = short_term.load_task(str(thread.get("task_id") or "")) or {}
        target["report_id"] = int(task.get("report_id") or 0) or None
    before = dict(current)
    after: dict[str, Any]
    if tool_name == AgentToolName.REVISE_TASK_REQUIREMENTS.value:
        target["artifact_type"] = "task_brief"
        target["object_id"] = ""
        before = _resolve_current(target)
        after = {
            "theme": str(arguments.get("updated_theme") or before.get("theme") or "").strip(),
            "requirements": str(arguments.get("updated_requirements") or "").strip(),
            "instruction": str(arguments.get("instruction") or "调整任务目标与报告要求").strip(),
        }
        if not after["requirements"]:
            raise ValueError("UPDATED_REQUIREMENTS_REQUIRED")
    elif tool_name == AgentToolName.REVISE_ANALYSIS_PLAN.value:
        target["artifact_type"] = "analysis_plan"
        target["object_id"] = str(thread.get("task_id") or "")
        before = _current_plan(str(thread.get("task_id") or ""), "analysis_plan")
        after = {
            "instruction": str(arguments.get("instruction") or "调整分析问题与证据需求").strip(),
            "required_dimensions": list(arguments.get("required_dimensions") or []),
            "required_dimension_count": int(arguments.get("required_dimension_count") or 0),
        }
    elif tool_name == AgentToolName.RECHECK_FACT.value:
        target["artifact_type"] = "fact"
        target["object_id"] = str(arguments.get("fact_id") or "")
        before = _resolve_current(target)
        after = {"instruction": str(arguments.get("instruction") or "复核该事实及其证据").strip()}
    elif tool_name == AgentToolName.RECHECK_INFERENCE.value:
        target["artifact_type"] = "inference"
        target["object_id"] = str(arguments.get("inference_id") or "")
        before = _resolve_current(target)
        after = {"instruction": str(arguments.get("instruction") or "复核该推论及其依据").strip()}
    elif tool_name == AgentToolName.REGENERATE_CHAPTER.value:
        target["artifact_type"] = "narrative_plan"
        target["object_id"] = str(arguments.get("chapter_title") or "")
        before = _current_narrative_plan(
            str(thread.get("task_id") or ""), str(thread.get("artifact_version") or ""),
            target["object_id"],
        )
        after = {"instruction": str(arguments.get("instruction") or "重新组织并生成该章节")}
    elif tool_name == AgentToolName.REWRITE_SENTENCE.value:
        focus = dict((thread.get("scope") or {}).get("focus") or {})
        if not arguments.get("sentence_id"):
            arguments["sentence_id"] = focus.get("object_id")
        if not str(arguments.get("sentence_id") or "").isdigit():
            raise ValueError("SENTENCE_TARGET_REQUIRED")
        target["artifact_type"] = "sentence"
        target["object_id"] = str(arguments.get("sentence_id") or "")
        before = _resolve_current(target)
        after = {"instruction": str(arguments.get("instruction") or "在不改变事实含义的前提下改写该句")}
    elif tool_name == AgentToolName.REWRITE_PARAGRAPH.value:
        focus = dict((thread.get("scope") or {}).get("focus") or {})
        focus_current = dict(focus.get("current") or {})
        arguments["chapter_title"] = str(arguments.get("chapter_title") or focus_current.get("section") or "")
        arguments["paragraph"] = int(arguments.get("paragraph") or focus_current.get("paragraph") or 0)
        arguments["sentence_ids"] = list(arguments.get("sentence_ids") or focus_current.get("sentence_ids") or [])
        if not arguments["chapter_title"] or arguments["paragraph"] <= 0:
            raise ValueError("PARAGRAPH_TARGET_REQUIRED")
        target["artifact_type"] = "paragraph"
        target["object_id"] = f"{arguments['chapter_title']}:{arguments['paragraph']}"
        before = dict(focus_current or current)
        after = {"instruction": str(arguments.get("instruction") or "按要求重写该段")}
    elif tool_name == AgentToolName.UPDATE_REPORT_TITLE.value:
        target["artifact_type"] = "report_title"
        target["object_id"] = str(target.get("report_id") or "")
        before = _resolve_current(target)
        after = {"title": str(arguments.get("new_title") or "").strip()}
    elif tool_name == AgentToolName.UPDATE_SECTION_TITLE.value:
        target["artifact_type"] = "section_title"
        target["object_id"] = str(arguments.get("old_title") or "")
        before = {"title": target["object_id"]}
        after = {"title": str(arguments.get("new_title") or "").strip()}
    elif tool_name == AgentToolName.RERUN_FINAL_PLAN.value:
        target["artifact_type"] = "final_plan"
        target["object_id"] = str(thread.get("task_id") or "")
        before = _current_plan(str(thread.get("task_id") or ""), "final_plan")
        from app.rendering.headings import strip_heading_prefix
        structure = [
            strip_heading_prefix(str(title)) for title in arguments.get("new_structure") or []
            if strip_heading_prefix(str(title))
        ]
        after = {"instruction": str(arguments.get("instruction") or arguments.get("reason") or "重新规划最终报告结构")}
        if structure:
            after["required_structure"] = structure
        chapter_count = int(arguments.get("required_chapter_count") or len(structure) or 0)
        if chapter_count:
            after["required_chapter_count"] = chapter_count
    else:
        target["artifact_type"] = "task_control"
        target["object_id"] = tool_name
        after = {"tool_name": tool_name, "arguments": arguments}
    tool_call = {**tool_call, "arguments": arguments}
    target["scope"] = {
        **dict(thread.get("scope") or {}),
        "tool_call": tool_call,
        "focus": dict((thread.get("scope") or {}).get("focus") or {}),
    }
    return _create_proposal(
        target,
        before,
        {
            "operation": "execute" if target["artifact_type"] == "task_control" else "reorganize",
            "after": after,
            "rationale": str(tool_call.get("reason") or f"调用 {tool_name}"),
            "risk_level": "medium",
        },
        source_message_id=source_message_id,
    )


def _apply_supported_proposal(row: dict) -> bool:
    """Apply only local report edits; upstream semantics remain approved artifacts.

    Upstream approval is intentionally not executed here.  A scheduler can use
    the stored impact graph to invalidate and regenerate downstream artifacts.
    """
    artifact_type = row["artifact_type"]
    after = _load(row["after_json"], {})
    impact = _load(row.get("impact_json"), {})
    tool_call = dict((impact.get("scope") or {}).get("tool_call") or {})
    if str(tool_call.get("tool_name") or "") in {
        AgentToolName.REWRITE_SENTENCE.value,
        AgentToolName.REWRITE_PARAGRAPH.value,
        AgentToolName.REGENERATE_CHAPTER.value,
        AgentToolName.RERUN_FINAL_PLAN.value,
    }:
        return False
    content = str(after.get("content") or after.get("title") or "").strip()
    report_id = row.get("report_id")
    task_id = str(row.get("task_id") or "")
    if artifact_type == "task_control":
        tool_name = AgentToolName(str(after.get("tool_name") or ""))
        context = build_task_agent_context(task_id)
        execute_control_tool(tool_name, dict(after.get("arguments") or {}), context)
        return True
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
        requirements = after.get("requirements")
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
    requirements = after.get("requirements")
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
        "task_brief", "analysis_plan", "fact", "inference",
        "final_plan", "narrative_plan", "paragraph", "sentence",
    }:
        return False
    task_id = str(row.get("task_id") or "")
    if not task_id:
        return False
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
        requirements = after.get("requirements")
        if str(requirements or "").strip():
            values["user_requirements"] = str(requirements).strip()
        if values:
            short_term.update_task(task_id, **values)
        return


def _pending_proposal_decision(thread: dict, message: str) -> tuple[dict, str] | None:
    """Handle explicit approval as a control command, not a semantic shortcut."""
    text = re.sub(r"[！!。，,\s]", "", str(message or "").strip())
    accepted = bool(re.fullmatch(
        r"(?:是的)?(?:认可|确认|同意|可以|就这样|按此执行|好的?(?:来做吧|开始吧))",
        text,
    ))
    rejected = bool(re.fullmatch(r"(?:不认可|不同意|取消|不要执行|撤销)", text))
    if not accepted and not rejected:
        return None
    pending = [item for item in thread.get("proposals") or [] if item.get("status") == "proposed"]
    if not pending:
        return None
    latest = max(pending, key=lambda item: int(item.get("id") or 0))
    return latest, "accepted" if accepted else "rejected"


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
    scope = dict(thread.get("scope") or {})
    tool_call = dict(scope.get("tool_call") or {})
    policy = propagation_policy(str(tool_call.get("tool_name") or ""), artifact_type)
    return {
        "invalidates": list(policy.invalidates),
        "scope": scope,
        "policy": policy.as_dict(),
        "automatic_execution": artifact_type in {"report_title", "section_title"},
    }


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


def _task_decision_memory(task_id: str, focus: dict | None = None) -> dict[str, Any]:
    """Derive durable task memory from authoritative proposals and task state."""
    from app.memory import short_term

    task = short_term.load_task(task_id) or {}
    with session_scope() as s:
        rows = s.execute(select(ORMChangeProposal).where(
            ORMChangeProposal.c.task_id == task_id,
        ).order_by(ORMChangeProposal.c.id.desc()).limit(40)).mappings().all()

    buckets: dict[str, list[dict[str, Any]]] = {
        "confirmed_decisions": [], "pending_decisions": [], "rejected_decisions": [],
        "failed_decisions": [],
    }
    latest_candidate: dict[str, Any] = {}
    for row in rows:
        impact = _load(row.get("impact_json"), {})
        tool_call = dict((impact.get("scope") or {}).get("tool_call") or {})
        arguments = dict(tool_call.get("arguments") or {})
        policy = propagation_policy(
            str(tool_call.get("tool_name") or ""), str(row.get("artifact_type") or ""),
        )
        record = {
            "proposal_id": int(row["id"]),
            "action": str(tool_call.get("tool_name") or row.get("operation") or "update"),
            "artifact_type": str(row.get("artifact_type") or ""),
            "object_id": str(row.get("object_id") or ""),
            "instruction": str(arguments.get("instruction") or _load(row.get("after_json"), {}).get("instruction") or ""),
            "confirmed_structure": list(arguments.get("new_structure") or []),
            "required_chapter_count": int(arguments.get("required_chapter_count") or 0),
            "target_chapter": str(arguments.get("chapter_title") or ""),
            "summary": str(row.get("rationale") or policy.label),
            "propagation": policy.as_dict(),
            "execution_status": str(row.get("execution_status") or "not_required"),
            "decided_at": row.get("decided_at"),
        }
        status = str(row.get("status") or "")
        execution = str(row.get("execution_status") or "")
        if status == "proposed":
            buckets["pending_decisions"].append(record)
        elif status == "rejected":
            buckets["rejected_decisions"].append(record)
        elif status in {"accepted", "applied"} and execution == "failed":
            buckets["failed_decisions"].append(record)
        elif status in {"accepted", "applied"}:
            buckets["confirmed_decisions"].append(record)
        if not latest_candidate and row.get("candidate_version_id"):
            latest_candidate = {
                "version_id": int(row["candidate_version_id"]),
                "run_id": str(row.get("execution_run_id") or ""),
                "proposal_id": int(row["id"]),
            }

    for key in buckets:
        buckets[key] = list(reversed(buckets[key][:12]))
    focus = dict(focus or {})
    return {
        "task_goal": {
            "theme": str(task.get("theme") or ""),
            "requirements": str(task.get("user_requirements") or ""),
        },
        "current_focus": {
            "artifact_type": str(focus.get("artifact_type") or "task_brief"),
            "object_id": str(focus.get("object_id") or ""),
            "title": str(focus.get("title") or "当前任务"),
        },
        **buckets,
        "latest_candidate": latest_candidate,
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
    instructions = [_proposal_instruction(item) for item in proposals]
    tool_calls = [
        dict((_load(item.get("impact_json"), {}).get("scope") or {}).get("tool_call") or {})
        for item in proposals
    ]
    policies = [
        propagation_policy(
            str(call.get("tool_name") or ""), str(item.get("artifact_type") or ""),
        )
        for item, call in zip(proposals, tool_calls)
    ]
    earliest_policy = min(policies, key=lambda item: item.recompute_rank)
    structure_constraints = []
    chapter_count_constraints = []
    analysis_constraints = []
    evidence_rechecks = []
    inference_rechecks = []
    for item, tool_call in zip(proposals, tool_calls):
        after = _load(item.get("after_json"), {})
        arguments = tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {}
        tool_name = str(tool_call.get("tool_name") or "")
        if str(item.get("artifact_type") or "") == "final_plan":
            structure_constraints.append(list(
                after.get("required_structure") or arguments.get("new_structure") or []
            ))
            chapter_count_constraints.append(int(
                after.get("required_chapter_count") or arguments.get("required_chapter_count") or 0
            ))
        if tool_name == AgentToolName.REVISE_ANALYSIS_PLAN.value:
            analysis_constraints.append({
                "instruction": str(arguments.get("instruction") or after.get("instruction") or ""),
                "required_dimensions": list(arguments.get("required_dimensions") or after.get("required_dimensions") or []),
                "required_dimension_count": int(
                    arguments.get("required_dimension_count") or after.get("required_dimension_count") or 0
                ),
            })
        if tool_name == AgentToolName.RECHECK_FACT.value:
            before = _load(item.get("before_json"), {})
            evidence_rechecks.append({
                "fact_id": int(arguments.get("fact_id") or item.get("object_id") or 0),
                "fact_content": str(before.get("content") or ""),
                "instruction": str(arguments.get("instruction") or after.get("instruction") or "复核该事实及其证据"),
            })
        if tool_name == AgentToolName.RECHECK_INFERENCE.value:
            before = _load(item.get("before_json"), {})
            inference_rechecks.append({
                "inference_id": int(arguments.get("inference_id") or item.get("object_id") or 0),
                "inference_content": str(before.get("content") or ""),
                "based_fact_ids": list(before.get("based_fact_ids") or []),
                "instruction": str(arguments.get("instruction") or after.get("instruction") or "复核该推论及其依据"),
            })
    required_structure = next((item for item in reversed(structure_constraints) if item), [])
    required_chapter_count = next((item for item in reversed(chapter_count_constraints) if item > 0), 0)
    if required_structure and not required_chapter_count:
        required_chapter_count = len(required_structure)
    analysis_constraint = next(iter(reversed(analysis_constraints)), {})
    evidence_recheck = next(iter(reversed(evidence_rechecks)), {})
    inference_recheck = next(iter(reversed(inference_rechecks)), {})
    plan_snapshot = base_version.get("report_plan_snapshot") or {}
    old_structure = [str(item) for item in plan_snapshot.get("structure") or [] if str(item).strip()]
    structure_rewrite_sections = [title for title in required_structure if title not in set(old_structure)]
    obsolete_sections = [title for title in old_structure if title not in set(required_structure)] if required_structure else []
    explicit_chapters = [
        str((call.get("arguments") or {}).get("chapter_title") or "")
        for call in tool_calls
        if str(call.get("tool_name") or "") == AgentToolName.REGENERATE_CHAPTER.value
        and str((call.get("arguments") or {}).get("chapter_title") or "").strip()
    ]
    targeted_revision = next(({
        "tool_name": str(call.get("tool_name") or ""),
        "arguments": dict(call.get("arguments") or {}),
    } for call in reversed(tool_calls) if str(call.get("tool_name") or "") in {
        AgentToolName.REWRITE_SENTENCE.value, AgentToolName.REWRITE_PARAGRAPH.value,
    }), {})
    update_reason = "根据用户已批准的交互提案生成候选版本：\n" + "\n".join(instructions)
    revision = max(1, int(base_task.get("run_revision") or 1)) + 1
    run_id = create_task_run(
        task_id, revision=revision, run_mode="interaction_revision",
        base_version_id=int(base_version["id"]), update_reason=update_reason,
    )
    delta = create_incremental_delta(report_id, [], update_reason=update_reason, run_id=run_id)
    old_fact_ids = _snapshot_ids(base_version.get("fact_snapshot") or [])
    inference_rows = base_version.get("inference_snapshot") or []
    old_inference_ids = [int(item["id"]) for item in inference_rows
                         if item.get("id") is not None and item.get("source_level") != "EXTERNAL_INFORMATION"]
    old_external_ids = [int(item["id"]) for item in inference_rows
                        if item.get("id") is not None and item.get("source_level") == "EXTERNAL_INFORMATION"]
    old_conflict_ids = _snapshot_ids(base_version.get("conflict_snapshot") or [])
    force_evidence = any(item.force_evidence for item in policies)
    force_analysis = any(item.force_analysis for item in policies)
    force_final_plan = any(item.force_final_plan for item in policies)
    rerun_initial_plan = any(item.rerun_initial_plan for item in policies)
    run_history = list(base_task.get("run_history") or [])
    run_history.append({
        "revision": int(base_task.get("run_revision") or 1),
        "mode": str(base_task.get("run_mode") or "initial"),
        "stage": str(base_task.get("stage") or ""),
        "report_version_id": int(base_version["id"]),
        "finished_at": (base_task.get("queue_status") or {}).get("finished_at"),
    })
    # Apply approved user-owned context only after the revision snapshot and
    # run records exist. The active workflow therefore never observes a
    # half-applied requirement version.
    for proposal in proposals:
        _apply_approved_context_patch(proposal)
    base_task = short_term.load_task(task_id) or base_task
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
        "intervention_recompute_from": earliest_policy.recompute_from,
        "intervention_propagation_policy": earliest_policy.as_dict(),
        "intervention_force_evidence": force_evidence,
        "intervention_force_analysis": force_analysis,
        "intervention_force_final_plan": force_final_plan,
        "intervention_required_structure": required_structure,
        "intervention_required_chapter_count": required_chapter_count,
        "intervention_analysis_constraint": analysis_constraint,
        "intervention_evidence_recheck": evidence_recheck,
        "intervention_inference_recheck": inference_recheck,
        "intervention_tool_calls": [item for item in tool_calls if item],
        "intervention_target_scope": targeted_revision,
        "intervention_rewrite_sections": list(dict.fromkeys([*structure_rewrite_sections, *explicit_chapters])),
        "intervention_obsolete_sections": obsolete_sections,
        "intervention_scope_locked": bool(required_structure or explicit_chapters or targeted_revision),
        "intervention_rewrite_all": False,
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
        message=_friendly_revision_error(error), action_url=f"/tasks/{first['task_id']}?tab=collaboration",
        metadata={"run_id": run_id},
    )


def _friendly_interaction_error(error: Exception) -> str:
    raw = str(error)
    if "MODEL_OUTPUT_TRUNCATED" in raw:
        return "这次回复没有完整生成，原有任务和报告不受影响。请缩小本轮讨论范围后重试。"
    if "INVALID_CONTROL_AGENT_OUTPUT" in raw:
        return "这次没有形成可执行的建议，原有任务不受影响。请换一种更明确的说法重试。"
    return "这次讨论没有成功完成，原有任务和报告不受影响。请稍后重试。"


def _friendly_revision_error(error: str) -> str:
    raw = str(error or "")
    if "FINAL_PLAN_CHAPTER_COUNT_MISMATCH" in raw:
        return "候选目录没有满足你确认的章节数量，因此系统已拒绝该候选，原报告保持不变。"
    if "ANALYSIS_DIMENSION_COUNT_MISMATCH" in raw:
        return "候选分析规划没有满足你确认的维度数量，因此系统已拒绝该候选，原报告保持不变。"
    if "MODEL_OUTPUT_TRUNCATED" in raw:
        return "候选版本未完整生成，系统已保留原报告，可缩小修改范围后重试。"
    return "候选版本处理失败，系统已保留原报告和历史版本。"


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
        "qa_issue": len(task.get("qa_notes") or []),
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
        issues = list(task.get("qa_notes") or [])
        report_id = int(task.get("report_id") or 0)
        if report_id:
            from app.quality import attach_quality_issue_locations
            issues = attach_quality_issue_locations(report_id, issues)
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
    after = _load(row.get("after_json"), {})
    impact = _load(row.get("impact_json"), {})
    tool_call = dict((impact.get("scope") or {}).get("tool_call") or {})
    arguments = tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {}
    tool_name = str(tool_call.get("tool_name") or "")
    structure = [
        str(item) for item in (after.get("required_structure") or arguments.get("new_structure") or [])
        if str(item).strip()
    ]
    structure_text = f"；用户确认的目录（必须保持 {len(structure)} 章及顺序）：{' | '.join(structure)}" if structure else ""
    if tool_name == AgentToolName.REVISE_TASK_REQUIREMENTS.value:
        detail = str(arguments.get("instruction") or after.get("instruction") or "更新任务目标与报告要求")
    elif tool_name == AgentToolName.REVISE_ANALYSIS_PLAN.value:
        dimensions = list(arguments.get("required_dimensions") or after.get("required_dimensions") or [])
        detail = str(arguments.get("instruction") or after.get("instruction") or "调整分析规划")
        if dimensions:
            detail += "；确认的分析维度：" + " | ".join(str(item) for item in dimensions)
    elif tool_name in {
        AgentToolName.RECHECK_FACT.value, AgentToolName.RECHECK_INFERENCE.value,
        AgentToolName.REWRITE_SENTENCE.value, AgentToolName.REWRITE_PARAGRAPH.value,
        AgentToolName.REGENERATE_CHAPTER.value, AgentToolName.RERUN_FINAL_PLAN.value,
    }:
        detail = str(arguments.get("instruction") or after.get("instruction") or row.get("rationale") or "按批准提案调整")
    else:
        detail = str(row.get("rationale") or after.get("instruction") or "按批准提案调整")
    return (
        f"- [{row.get('artifact_type')}/{row.get('object_id') or '整体'}] "
        f"{detail}{structure_text}"
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
