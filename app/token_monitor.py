"""Token efficiency observability and lineage helpers."""
from __future__ import annotations

import contextlib
import contextvars
import json
import time
import uuid
from statistics import median
from typing import Any

from app.db import session_scope
from app.infrastructure.orm import ORMLLMCall, ORMFact, ORMInference, ORMSentence
from sqlalchemy import select, update

_task_id: contextvars.ContextVar[str] = contextvars.ContextVar("token_task_id", default="")
_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("token_run_id", default="")
_stage: contextvars.ContextVar[str] = contextvars.ContextVar("token_stage", default="")
_report_mode: contextvars.ContextVar[str] = contextvars.ContextVar("token_report_mode", default="")
_material_count: contextvars.ContextVar[int] = contextvars.ContextVar("token_material_count", default=0)


@contextlib.contextmanager
def token_context(task_id: str = "", run_id: str = "", stage: str = "", report_mode: str = "",
                  material_count: int | None = None):
    tokens = []
    if task_id:
        tokens.append((_task_id, _task_id.set(task_id)))
    if run_id:
        tokens.append((_run_id, _run_id.set(run_id)))
    if stage:
        tokens.append((_stage, _stage.set(stage)))
    if report_mode:
        tokens.append((_report_mode, _report_mode.set(report_mode)))
    if material_count is not None:
        tokens.append((_material_count, _material_count.set(int(material_count))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current_context() -> dict:
    return {
        "task_id": _task_id.get(),
        "run_id": _run_id.get(),
        "stage": _stage.get(),
        "report_mode": _report_mode.get(),
        "material_count": _material_count.get(),
    }


def new_call_id() -> str:
    return uuid.uuid4().hex[:16]


def log_llm_call(call_id: str, agent: str, input_chars: int, stats_delta: dict,
                 latency_seconds: float, retry_count: int = 0,
                 success: bool = True, error: str = "",
                 returned_chars: int = 0, valid_json_chars: int = 0) -> None:
    ctx = current_context()
    prompt_tokens = int(stats_delta.get("prompt_tokens") or 0)
    output_tokens = int(stats_delta.get("output_tokens") or 0)
    context_tokens = prompt_tokens or max(1, int(input_chars / 4))
    returned_tokens = output_tokens
    parsed_tokens = _estimate_sub_tokens(valid_json_chars, returned_chars, returned_tokens)
    timing = {
        "prompt_eval_seconds": round(float(stats_delta.get("prompt_eval_seconds") or 0), 3),
        "output_eval_seconds": round(float(stats_delta.get("output_eval_seconds") or 0), 3),
        "model_total_seconds": round(float(stats_delta.get("total_seconds") or 0), 3),
        "wall_latency_seconds": round(float(latency_seconds or 0), 3),
    }
    timing["prompt_tokens_per_second"] = round(prompt_tokens / timing["prompt_eval_seconds"], 2) if timing["prompt_eval_seconds"] else 0
    timing["output_tokens_per_second"] = round(output_tokens / timing["output_eval_seconds"], 2) if timing["output_eval_seconds"] else 0
    with session_scope() as s:
        s.execute(
            ORMLLMCall.insert().values(
                call_id=call_id,
                task_id=ctx.get("task_id", ""),
                run_id=ctx.get("run_id", ""),
                agent=agent or "base",
                stage=ctx.get("stage", ""),
                report_mode=ctx.get("report_mode", ""),
                input_chars=int(input_chars),
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                context_tokens=context_tokens,
                latency_ms=int(latency_seconds * 1000),
                retry_count=int(retry_count),
                success=1 if success else 0,
                error=str(error or "")[:500],
                material_count=int(ctx.get("material_count") or 0),
                returned_chars=int(returned_chars or 0),
                valid_json_chars=int(valid_json_chars or 0),
                returned_tokens=returned_tokens,
                parsed_tokens=parsed_tokens,
                created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        s.execute(
            update(ORMLLMCall).where(ORMLLMCall.c.call_id == call_id)
            .values(funnel_json=json.dumps({"model_timing": timing}, ensure_ascii=False))
        )


def log_pipeline_event(agent: str, **funnel: Any) -> str:
    """Record a zero-token pipeline event for observability.

    Used for optimized-away work, e.g. Conflict prefilter deciding that an LLM
    guardrail call is not needed. This keeps successful skips visible without
    inflating model token usage.
    """
    call_id = "evt_" + new_call_id()
    ctx = current_context()
    with session_scope() as s:
        s.execute(
            ORMLLMCall.insert().values(
                call_id=call_id,
                task_id=ctx.get("task_id", ""),
                run_id=ctx.get("run_id", ""),
                agent=agent or "pipeline",
                stage=ctx.get("stage", ""),
                report_mode=ctx.get("report_mode", ""),
                success=1,
                material_count=int(ctx.get("material_count") or 0),
                funnel_json=json.dumps(_json_safe(funnel), ensure_ascii=False),
                created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
    return call_id


def _estimate_sub_tokens(part_chars: int, whole_chars: int, whole_tokens: int) -> int:
    if part_chars <= 0 or whole_tokens <= 0:
        return 0
    if whole_chars <= 0:
        return int(part_chars)
    return max(1, round(int(whole_tokens) * min(1.0, int(part_chars) / max(int(whole_chars), 1))))


def update_call_products(call_id: str, **products: list[int]) -> None:
    allowed = {
        "produced_fact_ids",
        "produced_inference_ids",
        "produced_chapter_ids",
        "final_used_fact_ids",
        "final_used_inference_ids",
    }
    values = {}
    for key, value in products.items():
        if key not in allowed:
            continue
        values[key] = json.dumps([int(v) for v in value or [] if str(v).isdigit()], ensure_ascii=False)
    if not values:
        return
    with session_scope() as s:
        s.execute(update(ORMLLMCall).where(ORMLLMCall.c.call_id == call_id).values(**values))


def update_call_metrics(call_id: str, **metrics: int) -> None:
    allowed = {
        "stored_chars", "final_chars", "returned_chars", "valid_json_chars",
        "returned_tokens", "parsed_tokens", "persisted_tokens", "final_tokens",
        "candidate_count", "candidate_total",
    }
    with session_scope() as s:
        row = s.execute(
            select(ORMLLMCall.c.returned_chars, ORMLLMCall.c.returned_tokens, ORMLLMCall.c.output_tokens)
            .where(ORMLLMCall.c.call_id == call_id)
        ).mappings().first()
    returned_chars = int(row["returned_chars"] or 0) if row else 0
    returned_tokens = int(row["returned_tokens"] or row["output_tokens"] or 0) if row else 0
    if "stored_chars" in metrics and "persisted_tokens" not in metrics:
        metrics["persisted_tokens"] = _estimate_sub_tokens(int(metrics.get("stored_chars") or 0), returned_chars, returned_tokens)
    if "final_chars" in metrics and "final_tokens" not in metrics:
        metrics["final_tokens"] = _estimate_sub_tokens(int(metrics.get("final_chars") or 0), returned_chars, returned_tokens)

    values = {}
    for key, value in metrics.items():
        if key not in allowed:
            continue
        values[key] = int(value or 0)
    if not values:
        return
    with session_scope() as s:
        s.execute(update(ORMLLMCall).where(ORMLLMCall.c.call_id == call_id).values(**values))


def update_call_funnel(call_id: str, **funnel: Any) -> None:
    """Attach detailed stage-specific funnel metrics to a call log row.

    The schema intentionally stays flexible: Evidence, Conflict, Writer, and QA
    produce different intermediate artifacts, and we want observability to evolve
    without running migrations for every new counter.
    """
    if not call_id or not funnel:
        return
    with session_scope() as s:
        row = s.execute(
            select(ORMLLMCall.c.funnel_json).where(ORMLLMCall.c.call_id == call_id)
        ).mappings().first()
        try:
            current = json.loads(row["funnel_json"] or "{}") if row else {}
        except (TypeError, ValueError):
            current = {}
        current.update(_json_safe(funnel))
        s.execute(
            update(ORMLLMCall).where(ORMLLMCall.c.call_id == call_id)
            .values(funnel_json=json.dumps(current, ensure_ascii=False))
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def generation_delta(before: dict, after: dict) -> dict:
    keys = ("prompt_tokens", "output_tokens", "prompt_eval_seconds", "output_eval_seconds", "total_seconds")
    return {key: round(float(after.get(key, 0)) - float(before.get(key, 0)), 3) for key in keys}


def build_token_efficiency(task_id: str, report_id: int | None = None, run_id: str = "") -> dict:
    """Compute post-run token utilization metrics from call logs and lineage tables."""
    with session_scope() as s:
        call_query = select(ORMLLMCall).where(ORMLLMCall.c.task_id == task_id)
        if run_id:
            call_query = call_query.where(ORMLLMCall.c.run_id == run_id)
        calls = s.execute(call_query).mappings().all()
        call_ids = [c["call_id"] for c in calls]
        fact_query = select(ORMFact.c.id, ORMFact.c.origin_call_id).where(ORMFact.c.task_id == task_id)
        if run_id and call_ids:
            fact_query = fact_query.where(ORMFact.c.origin_call_id.in_(call_ids))
        facts = s.execute(fact_query).mappings().all()
        inferences = []
        if call_ids:
            inferences = s.execute(
                select(ORMInference.c.id, ORMInference.c.origin_call_id)
                .where(ORMInference.c.origin_call_id.in_(call_ids))
            ).mappings().all()
        sentence_rows = []
        if report_id is not None:
            sentence_rows = s.execute(
                select(ORMSentence.c.id, ORMSentence.c.content, ORMSentence.c.source_refs,
                       ORMSentence.c.origin_call_id, ORMSentence.c.selected)
                .where(ORMSentence.c.report_id == int(report_id))
            ).mappings().all()

    total_output = sum(int(c["output_tokens"] or 0) for c in calls)
    total_input = sum(int(c["input_tokens"] or 0) for c in calls)
    total_returned_chars = sum(int(c["returned_chars"] or 0) for c in calls)
    total_valid_json_chars = sum(int(c["valid_json_chars"] or 0) for c in calls)
    total_stored_chars = sum(int(c["stored_chars"] or 0) for c in calls)
    total_final_chars_logged = sum(int(c["final_chars"] or 0) for c in calls)
    total_returned_tokens = sum(int(c["returned_tokens"] or c["output_tokens"] or 0) for c in calls)
    total_parsed_tokens = sum(int(c["parsed_tokens"] or 0) for c in calls)
    total_persisted_tokens = sum(int(c["persisted_tokens"] or 0) for c in calls)
    total_final_tokens_logged = sum(int(c["final_tokens"] or 0) for c in calls)
    candidate_total = sum(int(c["candidate_total"] or 0) for c in calls)
    candidate_count = sum(int(c["candidate_count"] or 0) for c in calls)
    max_material_count = max((int(c["material_count"] or 0) for c in calls), default=0)
    final_text = "".join(r["content"] for r in sentence_rows if r["selected"])
    final_tokens = max(0, len(final_text))
    user_visible_final_tokens = final_tokens
    used_fact_ids: set[int] = set()
    used_inference_ids: set[int] = set()
    writer_output_tokens = sum(int(c["output_tokens"] or 0) for c in calls if c["agent"] == "writer")
    retry_output_tokens = sum(int(c["output_tokens"] or 0) for c in calls if int(c["retry_count"] or 0) > 0 or not c["success"])
    for row in sentence_rows:
        try:
            refs = json.loads(row["source_refs"] or "{}")
        except (TypeError, ValueError):
            refs = {}
        used_fact_ids.update(int(v) for v in refs.get("fact_ids") or [] if str(v).isdigit())
        used_inference_ids.update(int(v) for v in refs.get("inference_ids") or [] if str(v).isdigit())
    fact_ids = {int(r["id"]) for r in facts}
    inference_ids = {int(r["id"]) for r in inferences}
    fact_origin = {int(r["id"]): r["origin_call_id"] for r in facts if r["origin_call_id"]}
    inference_origin = {int(r["id"]): r["origin_call_id"] for r in inferences if r["origin_call_id"]}
    used_facts_by_call: dict[str, list[int]] = {}
    used_inferences_by_call: dict[str, list[int]] = {}
    for fact_id in used_fact_ids & fact_ids:
        if fact_origin.get(fact_id):
            used_facts_by_call.setdefault(fact_origin[fact_id], []).append(fact_id)
    for inference_id in used_inference_ids & inference_ids:
        if inference_origin.get(inference_id):
            used_inferences_by_call.setdefault(inference_origin[inference_id], []).append(inference_id)
    for call_id in set(used_facts_by_call) | set(used_inferences_by_call):
        update_call_products(
            call_id,
            final_used_fact_ids=used_facts_by_call.get(call_id, []),
            final_used_inference_ids=used_inferences_by_call.get(call_id, []),
        )
    if used_facts_by_call or used_inferences_by_call:
        for call in calls:
            call_final_chars = 0
            for row in sentence_rows:
                if row["origin_call_id"] == call["call_id"] and row["selected"]:
                    call_final_chars += len(row["content"] or "")
            if call_final_chars:
                final_token_est = _estimate_sub_tokens(
                    call_final_chars,
                    int(call["returned_chars"] or 0),
                    int(call["returned_tokens"] or call["output_tokens"] or 0),
                )
                update_call_metrics(call["call_id"], final_chars=call_final_chars, final_tokens=final_token_est)
    by_agent: dict[str, dict] = {}
    by_stage: dict[str, dict] = {}
    for call in calls:
        for bucket, key in ((by_agent, call["agent"] or "unknown"), (by_stage, call["stage"] or "unknown")):
            item = bucket.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0})
            item["calls"] += 1
            item["input_tokens"] += int(call["input_tokens"] or 0)
            item["output_tokens"] += int(call["output_tokens"] or 0)
            item["latency_ms"] += int(call["latency_ms"] or 0)
    for bucket in (by_agent, by_stage):
        for item in bucket.values():
            item["output_share"] = round(item["output_tokens"] / max(total_output, 1), 4)

    detailed_funnel = _aggregate_funnel(calls, final_text)
    evidence_detail = _aggregate_evidence_detail(calls, used_fact_ids)

    knowledge_used_tokens_est = 0
    knowledge_unused_tokens_est = 0
    produced_fact_count = 0
    produced_inference_count = 0
    used_produced_fact_count = 0
    used_produced_inference_count = 0
    quality_guardrail_tokens = 0
    for call in calls:
        output_tokens = int(call["output_tokens"] or 0)
        try:
            produced_facts = json.loads(call["produced_fact_ids"] or "[]")
        except (TypeError, ValueError):
            produced_facts = []
        try:
            produced_inferences = json.loads(call["produced_inference_ids"] or "[]")
        except (TypeError, ValueError):
            produced_inferences = []
        produced_ids = [int(v) for v in produced_facts + produced_inferences if str(v).isdigit()]
        if call["stage"] == "conflict":
            quality_guardrail_tokens += output_tokens
            continue
        if not produced_ids:
            continue
        used_ids = []
        used_ids.extend(v for v in produced_ids if v in used_fact_ids)
        used_ids.extend(v for v in produced_ids if v in used_inference_ids)
        used_ratio = min(1.0, len(set(used_ids)) / max(len(set(produced_ids)), 1))
        knowledge_used_tokens_est += int(output_tokens * used_ratio)
        knowledge_unused_tokens_est += output_tokens - int(output_tokens * used_ratio)
        produced_fact_count += len([v for v in produced_facts if str(v).isdigit()])
        produced_inference_count += len([v for v in produced_inferences if str(v).isdigit()])
        used_produced_fact_count += len({int(v) for v in produced_facts if str(v).isdigit()} & used_fact_ids)
        used_produced_inference_count += len({int(v) for v in produced_inferences if str(v).isdigit()} & used_inference_ids)

    writing_output_tokens = by_stage.get("writing", {}).get("output_tokens", 0)
    writer_overhead_tokens = max(0, int(writing_output_tokens) - final_tokens)
    necessary_proxy_tokens = min(
        total_output,
        final_tokens + knowledge_used_tokens_est + quality_guardrail_tokens,
    )
    compressible_proxy_tokens = max(0, total_output - necessary_proxy_tokens - retry_output_tokens)
    token_buckets = {
        "final_report_tokens": final_tokens,
        "used_knowledge_tokens_est": knowledge_used_tokens_est,
        "unused_knowledge_tokens_est": knowledge_unused_tokens_est,
        "quality_guardrail_tokens": quality_guardrail_tokens,
        "writer_overhead_tokens_est": writer_overhead_tokens,
        "retry_waste_tokens": retry_output_tokens,
        "returned_chars": total_returned_chars,
        "valid_json_chars": total_valid_json_chars,
        "stored_chars": total_stored_chars,
        "final_chars_logged": total_final_chars_logged or final_tokens,
        "returned_tokens": total_returned_tokens,
        "parsed_tokens": total_parsed_tokens,
        "persisted_tokens": total_persisted_tokens,
        "final_tokens_logged": total_final_tokens_logged or final_tokens,
        "user_visible_final_tokens": user_visible_final_tokens,
        "returned_to_parsed_share": round(total_parsed_tokens / max(total_returned_tokens, 1), 4),
        "parsed_to_persisted_share": round(total_persisted_tokens / max(total_parsed_tokens, 1), 4),
        "persisted_to_final_share": round((total_final_tokens_logged or final_tokens) / max(total_persisted_tokens, 1), 4),
        "returned_to_json_share": round(total_valid_json_chars / max(total_returned_chars, 1), 4),
        "json_to_stored_share": round(total_stored_chars / max(total_valid_json_chars, 1), 4),
        "stored_to_final_share": round((total_final_chars_logged or final_tokens) / max(total_stored_chars, 1), 4),
        "candidate_total": candidate_total,
        "candidate_count": candidate_count,
        "candidate_compression_share": round(candidate_count / max(candidate_total, 1), 4) if candidate_total else 0,
        "necessary_proxy_tokens": necessary_proxy_tokens,
        "compressible_proxy_tokens": compressible_proxy_tokens,
        "necessary_proxy_share": round(necessary_proxy_tokens / max(total_output, 1), 4),
        "compressible_proxy_share": round(compressible_proxy_tokens / max(total_output, 1), 4),
    }
    scale_drivers = {
        "material_count": max_material_count,
        "facts_per_material": round(len(fact_ids) / max(max_material_count, 1), 2),
        "output_tokens_per_material": round(total_output / max(max_material_count, 1), 2),
        "evidence_output_tokens_per_fact": round(
            by_stage.get("evidence", {}).get("output_tokens", 0) / max(len(fact_ids), 1),
            2,
        ),
        "writer_output_tokens_per_final_token": round(
            writing_output_tokens / max(final_tokens, 1),
            2,
        ),
        "produced_fact_count": produced_fact_count,
        "used_produced_fact_count": used_produced_fact_count,
        "produced_inference_count": produced_inference_count,
        "used_produced_inference_count": used_produced_inference_count,
    }

    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "final_report_tokens_est": final_tokens,
        "final_token_efficiency": round(final_tokens / max(total_output, 1), 4),
        "fact_utilization_rate": round(len(used_fact_ids & fact_ids) / max(len(fact_ids), 1), 4),
        "inference_utilization_rate": round(len(used_inference_ids & inference_ids) / max(len(inference_ids), 1), 4),
        "writer_retention_rate": round(final_tokens / max(writer_output_tokens, 1), 4),
        "retry_waste_rate": round(retry_output_tokens / max(total_output, 1), 4),
        "fact_count": len(fact_ids),
        "used_fact_count": len(used_fact_ids & fact_ids),
        "inference_count": len(inference_ids),
        "used_inference_count": len(used_inference_ids & inference_ids),
        "by_agent": by_agent,
        "by_stage": by_stage,
        "token_buckets": token_buckets,
        "detailed_funnel": detailed_funnel,
        "evidence_detail": evidence_detail,
        "scale_drivers": scale_drivers,
        "call_count": len(calls),
    }


def _aggregate_evidence_detail(calls: list, used_fact_ids: set[int] | None = None) -> dict:
    used_fact_ids = used_fact_ids or set()
    ev_calls = [c for c in calls if (c["agent"] or "") == "evidence" and (c["stage"] or "") == "evidence"]
    summary = {
        "calls": len(ev_calls),
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_seconds": 0.0,
        "returned_chars": 0,
        "valid_json_chars": 0,
        "field_chars": {"content": 0, "short_quote": 0, "unit_id": 0, "fact_type": 0, "other": 0},
        "model_claims": 0,
        "valid_field_claims": 0,
        "quote_bound_claims": 0,
        "promoted_facts": 0,
        "duplicate_claims": 0,
        "pending_claims": 0,
        "retrieved_unit_count": 0,
        "retrieved_material_count": 0,
        "retrieved_chars": 0,
        "context_chars": 0,
        "contributed_material_count": 0,
        "contributed_unit_count": 0,
        "truncated_context_calls": 0,
        "prompt_parts": {
            "system_prompt": 0,
            "dimension": 0,
            "insight_block": 0,
            "material_context": 0,
            "instruction": 0,
        },
        "model_timing": {
            "prompt_eval_seconds": 0.0,
            "output_eval_seconds": 0.0,
            "model_total_seconds": 0.0,
            "wall_latency_seconds": 0.0,
        },
        "retrieved_materials": {},
        "contributed_materials": {},
        "by_dimension": [],
    }
    for call in ev_calls:
        try:
            funnel = json.loads(call["funnel_json"] or "{}")
        except (TypeError, ValueError):
            funnel = {}
        fields = funnel.get("field_chars") or {}
        ctx = funnel.get("context_meta") or {}
        prompt_parts = funnel.get("prompt_parts") or {}
        timing = funnel.get("model_timing") or {}
        produced_fact_ids = []
        try:
            produced_fact_ids = [int(v) for v in json.loads(call["produced_fact_ids"] or "[]") if str(v).isdigit()]
        except (TypeError, ValueError):
            produced_fact_ids = []
        item = {
            "call_id": call["call_id"],
            "dimension": str(funnel.get("dimension") or ctx.get("dimension") or ""),
            "input_tokens": int(call["input_tokens"] or 0),
            "output_tokens": int(call["output_tokens"] or 0),
            "latency_seconds": round(int(call["latency_ms"] or 0) / 1000, 1),
            "returned_chars": int(call["returned_chars"] or 0),
            "valid_json_chars": int(call["valid_json_chars"] or 0),
            "field_chars_total": sum(int(fields.get(k) or 0) for k in ("content", "short_quote", "unit_id", "fact_type", "other")),
            "returned_to_field_char_gap": max(
                0,
                int(call["returned_chars"] or 0)
                - sum(int(fields.get(k) or 0) for k in ("content", "short_quote", "unit_id", "fact_type", "other")),
            ),
            "model_claims": int(funnel.get("model_claims") or 0),
            "valid_field_claims": int(funnel.get("valid_field_claims") or 0),
            "quote_bound_claims": int(funnel.get("quote_bound_claims") or 0),
            "promoted_facts": int(funnel.get("promoted_facts") or 0),
            "duplicate_claims": int(funnel.get("duplicate_claims") or 0),
            "pending_claims": int(funnel.get("pending_claims") or 0),
            "retrieved_unit_count": int(ctx.get("retrieved_unit_count") or 0),
            "retrieved_material_count": int(ctx.get("retrieved_material_count") or 0),
            "retrieved_chars": int(ctx.get("retrieved_chars") or 0),
            "context_chars": int(ctx.get("context_chars") or 0),
            "truncated": bool(ctx.get("truncated")),
            "contributed_material_count": int(funnel.get("contributed_material_count") or 0),
            "contributed_unit_count": int(funnel.get("contributed_unit_count") or 0),
            "prompt_parts": {key: int(prompt_parts.get(key) or 0) for key in summary["prompt_parts"]},
            "model_timing": {
                "prompt_eval_seconds": round(float(timing.get("prompt_eval_seconds") or 0), 3),
                "output_eval_seconds": round(float(timing.get("output_eval_seconds") or 0), 3),
                "model_total_seconds": round(float(timing.get("model_total_seconds") or 0), 3),
                "wall_latency_seconds": round(float(timing.get("wall_latency_seconds") or 0), 3),
                "prompt_tokens_per_second": round(float(timing.get("prompt_tokens_per_second") or 0), 2),
                "output_tokens_per_second": round(float(timing.get("output_tokens_per_second") or 0), 2),
            },
            "produced_fact_count": len(produced_fact_ids),
            "used_fact_count": len(set(produced_fact_ids) & used_fact_ids),
        }
        summary["by_dimension"].append(item)
        summary["input_tokens"] += item["input_tokens"]
        summary["output_tokens"] += item["output_tokens"]
        summary["latency_seconds"] += item["latency_seconds"]
        summary["returned_chars"] += item["returned_chars"]
        summary["valid_json_chars"] += item["valid_json_chars"]
        summary["model_claims"] += item["model_claims"]
        summary["valid_field_claims"] += item["valid_field_claims"]
        summary["quote_bound_claims"] += item["quote_bound_claims"]
        summary["promoted_facts"] += item["promoted_facts"]
        summary["duplicate_claims"] += item["duplicate_claims"]
        summary["pending_claims"] += item["pending_claims"]
        summary["retrieved_unit_count"] += item["retrieved_unit_count"]
        summary["retrieved_material_count"] += item["retrieved_material_count"]
        summary["retrieved_chars"] += item["retrieved_chars"]
        summary["context_chars"] += item["context_chars"]
        summary["contributed_material_count"] += item["contributed_material_count"]
        summary["contributed_unit_count"] += item["contributed_unit_count"]
        summary["truncated_context_calls"] += 1 if item["truncated"] else 0
        for key in summary["prompt_parts"]:
            summary["prompt_parts"][key] += item["prompt_parts"].get(key, 0)
        for key in ("prompt_eval_seconds", "output_eval_seconds", "model_total_seconds", "wall_latency_seconds"):
            summary["model_timing"][key] += item["model_timing"].get(key, 0)
        for unit in (ctx.get("retrieved_units") or []):
            mid = str(unit.get("material_id") or "")
            if not mid:
                continue
            entry = summary["retrieved_materials"].setdefault(mid, {
                "material_id": int(unit.get("material_id") or 0),
                "filename": str(unit.get("filename") or ""),
                "retrieved_units": 0,
                "retrieved_chars": 0,
            })
            entry["retrieved_units"] += 1
            entry["retrieved_chars"] += int(unit.get("chars") or 0)
        for mid in funnel.get("contributed_material_ids") or []:
            key = str(mid)
            entry = summary["contributed_materials"].setdefault(key, {"material_id": int(mid), "calls": 0})
            entry["calls"] += 1
        for key in summary["field_chars"]:
            summary["field_chars"][key] += int(fields.get(key) or 0)
    field_total = sum(summary["field_chars"].values())
    summary["field_char_share"] = {
        key: round(value / max(field_total, 1), 4)
        for key, value in summary["field_chars"].items()
    }
    prompt_total = sum(summary["prompt_parts"].values())
    summary["prompt_part_share"] = {
        key: round(value / max(prompt_total, 1), 4)
        for key, value in summary["prompt_parts"].items()
    }
    summary["model_timing"] = {
        **{key: round(float(value), 3) for key, value in summary["model_timing"].items()},
        "prompt_tokens_per_second": round(summary["input_tokens"] / max(summary["model_timing"]["prompt_eval_seconds"], 0.001), 2),
        "output_tokens_per_second": round(summary["output_tokens"] / max(summary["model_timing"]["output_eval_seconds"], 0.001), 2),
    }
    summary["retrieved_materials"] = sorted(
        summary["retrieved_materials"].values(),
        key=lambda item: item["retrieved_units"],
        reverse=True,
    )
    summary["contributed_materials"] = sorted(
        summary["contributed_materials"].values(),
        key=lambda item: item["calls"],
        reverse=True,
    )
    summary["retrieved_to_contributed_unit_share"] = round(
        summary["contributed_unit_count"] / max(summary["retrieved_unit_count"], 1),
        4,
    )
    summary["output_tokens_per_fact"] = round(summary["output_tokens"] / max(summary["promoted_facts"], 1), 2)
    summary["seconds_per_fact"] = round(summary["latency_seconds"] / max(summary["promoted_facts"], 1), 2)
    summary["json_chars_per_output_token"] = round(summary["valid_json_chars"] / max(summary["output_tokens"], 1), 4)
    summary["field_chars_per_fact"] = round(field_total / max(summary["promoted_facts"], 1), 2)
    summary["claim_to_fact_share"] = round(summary["promoted_facts"] / max(summary["model_claims"], 1), 4)
    summary["used_fact_share"] = round(
        sum(int(item["used_fact_count"]) for item in summary["by_dimension"])
        / max(summary["promoted_facts"], 1),
        4,
    )
    summary["latency_seconds"] = round(summary["latency_seconds"], 1)
    return summary


def _aggregate_funnel(calls: list, final_text: str = "") -> dict:
    result = {
        "evidence": {
            "model_claims": 0,
            "valid_field_claims": 0,
            "quote_bound_claims": 0,
            "duplicate_claims": 0,
            "pending_claims": 0,
            "promoted_facts": 0,
            "field_chars": {"content": 0, "short_quote": 0, "unit_id": 0, "fact_type": 0, "other": 0},
        },
        "conflict": {
            "candidate_total": 0,
            "candidate_count": 0,
            "candidate_groups": 0,
            "llm_skipped_calls": 0,
            "quality_guardrail_calls": 0,
            "conflict_count": 0,
            "skip_reasons": {},
        },
        "writer": {
            "model_sentences": 0,
            "accepted_sentences": 0,
            "dropped_untraced_sentences": 0,
            "split_items": 0,
            "persisted_writer_tokens": 0,
            "user_visible_final_tokens": max(0, len(final_text or "")),
        },
    }
    for call in calls:
        try:
            funnel = json.loads(call["funnel_json"] or "{}")
        except (TypeError, ValueError):
            funnel = {}
        agent = call["agent"] or ""
        stage = call["stage"] or ""
        if agent == "evidence" and stage == "evidence":
            ev = result["evidence"]
            for key in ("model_claims", "valid_field_claims", "quote_bound_claims",
                        "duplicate_claims", "pending_claims", "promoted_facts"):
                ev[key] += int(funnel.get(key) or 0)
            fields = funnel.get("field_chars") or {}
            for key in ev["field_chars"]:
                ev["field_chars"][key] += int(fields.get(key) or 0)
        if agent == "evidence" and stage == "conflict":
            cf = result["conflict"]
            for key in ("candidate_total", "candidate_count", "candidate_groups",
                        "llm_skipped_calls", "quality_guardrail_calls", "conflict_count"):
                cf[key] += int(funnel.get(key) or 0)
            reason = str(funnel.get("skip_reason") or "")
            if reason:
                cf["skip_reasons"][reason] = int(cf["skip_reasons"].get(reason, 0)) + 1
        if agent == "writer" or stage == "writing":
            wr = result["writer"]
            for key in ("model_sentences", "accepted_sentences", "dropped_untraced_sentences", "split_items"):
                wr[key] += int(funnel.get(key) or 0)
            wr["persisted_writer_tokens"] += int(call["persisted_tokens"] or 0)
    ev = result["evidence"]
    field_total = sum(ev["field_chars"].values())
    ev["field_char_share"] = {
        key: round(value / max(field_total, 1), 4)
        for key, value in ev["field_chars"].items()
    }
    ev["model_to_valid_field_share"] = round(ev["valid_field_claims"] / max(ev["model_claims"], 1), 4)
    ev["valid_to_bound_share"] = round(ev["quote_bound_claims"] / max(ev["valid_field_claims"], 1), 4)
    ev["bound_to_fact_share"] = round(ev["promoted_facts"] / max(ev["quote_bound_claims"], 1), 4)
    cf = result["conflict"]
    cf["candidate_compression_share"] = round(cf["candidate_count"] / max(cf["candidate_total"], 1), 4)
    cf["group_compression_share"] = round(cf["candidate_groups"] / max(cf["candidate_count"], 1), 4)
    wr = result["writer"]
    wr["sentence_acceptance_share"] = round(wr["accepted_sentences"] / max(wr["model_sentences"], 1), 4)
    wr["persisted_to_visible_share"] = round(
        wr["user_visible_final_tokens"] / max(wr["persisted_writer_tokens"], 1), 4
    )
    return result



def build_workload_profile(task_id: str, run_id: str = "") -> dict:
    """Build stage/workload profiles for capacity planning.

    This is a read-only aggregation over LLM call logs. It intentionally stays
    outside the workflow prompts so observability cannot change report content.
    """
    calls = list_llm_calls(task_id, run_id=run_id)
    profiles: dict[str, dict] = {}
    for call in calls:
        stage = str(call.get("stage") or "unknown")
        workload = _workload_kind(stage, str(call.get("agent") or ""))
        key = f"{stage}:{workload}"
        item = profiles.setdefault(key, {
            "stage": stage,
            "workload": workload,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "context_tokens": 0,
            "latency_seconds": 0.0,
            "prompt_eval_seconds": 0.0,
            "output_eval_seconds": 0.0,
            "retry_count": 0,
            "errors": 0,
            "input_output_ratios": [],
            "context_lengths": [],
            "prefill_speeds": [],
            "decode_speeds": [],
            "call_latencies": [],
            "representative_agents": {},
        })
        input_tokens = int(call.get("input_tokens") or 0)
        output_tokens = int(call.get("output_tokens") or 0)
        context_tokens = int(call.get("context_tokens") or input_tokens or 0)
        latency = int(call.get("latency_ms") or 0) / 1000
        funnel = call.get("funnel_json") or {}
        timing = funnel.get("model_timing") if isinstance(funnel, dict) else {}
        prompt_seconds = float((timing or {}).get("prompt_eval_seconds") or 0)
        output_seconds = float((timing or {}).get("output_eval_seconds") or 0)
        item["calls"] += 1
        item["input_tokens"] += input_tokens
        item["output_tokens"] += output_tokens
        item["context_tokens"] += context_tokens
        item["latency_seconds"] += latency
        item["prompt_eval_seconds"] += prompt_seconds
        item["output_eval_seconds"] += output_seconds
        item["retry_count"] += int(call.get("retry_count") or 0)
        item["errors"] += 0 if int(call.get("success") or 0) else 1
        item["input_output_ratios"].append(round(input_tokens / max(output_tokens, 1), 4))
        item["context_lengths"].append(context_tokens)
        item["call_latencies"].append(latency)
        if prompt_seconds:
            item["prefill_speeds"].append(input_tokens / prompt_seconds)
        if output_seconds:
            item["decode_speeds"].append(output_tokens / output_seconds)
        agent = str(call.get("agent") or "unknown")
        item["representative_agents"][agent] = int(item["representative_agents"].get(agent, 0)) + 1
    finalized = []
    totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_seconds": 0.0}
    for item in profiles.values():
        tokens_total = item["input_tokens"] + item["output_tokens"]
        totals["calls"] += item["calls"]
        totals["input_tokens"] += item["input_tokens"]
        totals["output_tokens"] += item["output_tokens"]
        totals["latency_seconds"] += item["latency_seconds"]
        item["latency_seconds"] = round(item["latency_seconds"], 3)
        item["input_share"] = round(item["input_tokens"] / max(totals["input_tokens"], 1), 4)
        item["output_share"] = 0  # filled after totals are known
        item["token_share"] = 0
        ratios = item.pop("input_output_ratios")
        contexts = item.pop("context_lengths")
        prefill_speeds = item.pop("prefill_speeds")
        decode_speeds = item.pop("decode_speeds")
        latencies = item.pop("call_latencies")
        item["input_output_ratio_p50"] = _p(ratios, 50)
        item["input_output_ratio_p95"] = _p(ratios, 95)
        item["context_length_p50"] = _p(contexts, 50)
        item["context_length_p95"] = _p(contexts, 95)
        item["prefill_tokens_per_second_p50"] = _p(prefill_speeds, 50)
        item["prefill_tokens_per_second_p95"] = _p(prefill_speeds, 95)
        item["decode_tokens_per_second_p50"] = _p(decode_speeds, 50)
        item["decode_tokens_per_second_p95"] = _p(decode_speeds, 95)
        item["latency_p50_seconds"] = _p(latencies, 50)
        item["latency_p95_seconds"] = _p(latencies, 95)
        item["compute_profile"] = _compute_profile(item["workload"], item["input_tokens"], item["output_tokens"])
        item["sla_hint"] = _sla_hint(item["workload"])
        item["tokens_total"] = tokens_total
        finalized.append(item)
    total_tokens = totals["input_tokens"] + totals["output_tokens"]
    for item in finalized:
        item["input_share"] = round(item["input_tokens"] / max(totals["input_tokens"], 1), 4)
        item["output_share"] = round(item["output_tokens"] / max(totals["output_tokens"], 1), 4)
        item["token_share"] = round(item["tokens_total"] / max(total_tokens, 1), 4)
    finalized.sort(key=lambda x: (x["stage"], x["workload"]))
    summary_by_workload: dict[str, dict] = {}
    for item in finalized:
        bucket = summary_by_workload.setdefault(item["workload"], {
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_seconds": 0.0,
        })
        bucket["calls"] += item["calls"]
        bucket["input_tokens"] += item["input_tokens"]
        bucket["output_tokens"] += item["output_tokens"]
        bucket["latency_seconds"] += item["latency_seconds"]
    for bucket in summary_by_workload.values():
        bucket["latency_seconds"] = round(bucket["latency_seconds"], 3)
        bucket["input_share"] = round(bucket["input_tokens"] / max(totals["input_tokens"], 1), 4)
        bucket["output_share"] = round(bucket["output_tokens"] / max(totals["output_tokens"], 1), 4)
    return {
        "task_id": task_id,
        "totals": {
            "calls": totals["calls"],
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "total_tokens": total_tokens,
            "latency_seconds": round(totals["latency_seconds"], 3),
        },
        "by_stage_workload": finalized,
        "by_workload": summary_by_workload,
        "notes": [
            "CPU侧主要承载Docling解析、Unit入库、Embedding前处理、检索与JSON校验。",
            "NPU/GPU侧主要承载LLM prefill与decode；输入长的阶段更考验prefill/带宽，输出长的阶段更考验decode吞吐。",
        ],
    }


def _workload_kind(stage: str, agent: str) -> str:
    if stage in {"material_analysis", "parse"}:
        return "document_understanding"
    if stage == "evidence":
        return "evidence_extraction"
    if stage == "conflict":
        return "quality_guardrail"
    if stage in {"analysis", "final_planning"}:
        return "reasoning_planning"
    if stage == "writing":
        return "document_generation"
    if stage in {"knowledge", "qa"} or agent in {"qa", "knowledge"}:
        return "post_review_quality_or_memory"
    if stage in {"planning"}:
        return "reasoning_planning"
    return "other"


def _compute_profile(workload: str, input_tokens: int, output_tokens: int) -> dict:
    ratio = input_tokens / max(output_tokens, 1)
    if ratio >= 3:
        pattern = "long_input_short_output"
        accelerator_pressure = "prefill_bandwidth"
    elif ratio <= 0.75:
        pattern = "short_input_long_output"
        accelerator_pressure = "decode_throughput"
    else:
        pattern = "balanced_input_output"
        accelerator_pressure = "mixed"
    return {
        "io_pattern": pattern,
        "input_output_ratio": round(ratio, 4),
        "cpu_side": _cpu_side(workload),
        "npu_gpu_side": accelerator_pressure,
    }


def _cpu_side(workload: str) -> str:
    if workload == "document_understanding":
        return "high_for_docling_parse_and_table_layout"
    if workload == "evidence_extraction":
        return "medium_for_retrieval_binding_and_json_validation"
    if workload == "document_generation":
        return "low_medium_for_rendering_and_source_binding"
    return "low_medium_for_orchestration_and_validation"


def _sla_hint(workload: str) -> dict:
    return {
        "document_understanding": {"primary_metric": "parse_success_rate", "target": ">=95% usable_or_partial"},
        "evidence_extraction": {"primary_metric": "promoted_facts_per_material", "target": "no_zero_fact_run_when_text_units_exist"},
        "reasoning_planning": {"primary_metric": "stage_latency", "target": "bounded_by_fact_count_and_context"},
        "document_generation": {"primary_metric": "decode_tokens_per_second", "target": ">= target_model_baseline"},
        "quality_guardrail": {"primary_metric": "recall_before_cost", "target": "skip_llm_only_when_no_candidate_signal"},
        "post_review_quality_or_memory": {"primary_metric": "not_on_ttfr", "target": "background_or_non_blocking"},
        "other": {"primary_metric": "latency", "target": "observable"},
    }.get(workload, {"primary_metric": "latency", "target": "observable"})


def _p(values: list[float | int], pct: int) -> float:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return 0.0
    if pct == 50:
        return round(float(median(vals)), 3)
    idx = min(len(vals) - 1, max(0, int(round((pct / 100) * (len(vals) - 1)))))
    return round(vals[idx], 3)

def list_llm_calls(task_id: str, run_id: str = "") -> list[dict]:
    with session_scope() as s:
        query = select(ORMLLMCall).where(ORMLLMCall.c.task_id == task_id)
        if run_id:
            query = query.where(ORMLLMCall.c.run_id == run_id)
        rows = s.execute(query.order_by(ORMLLMCall.c.id)).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        for key in (
            "produced_fact_ids",
            "produced_inference_ids",
            "produced_chapter_ids",
            "final_used_fact_ids",
            "final_used_inference_ids",
        ):
            try:
                item[key] = json.loads(item.get(key) or "[]")
            except (TypeError, ValueError):
                item[key] = []
        try:
            item["funnel_json"] = json.loads(item.get("funnel_json") or "{}")
        except (TypeError, ValueError):
            item["funnel_json"] = {}
        result.append(item)
    return result
