"""Processing states are protocol facts, not judgments about material value."""
from enum import Enum


class EvidenceBatchStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    NO_EVIDENCE = "no_evidence"


def failure_reason(error: Exception | str) -> str:
    message = str(error)
    if message == "MODEL_OUTPUT_TRUNCATED":
        return "output_truncated"
    if message in {"MODEL_INPUT_OVER_BUDGET", "MODEL_CONTEXT_EXCEEDED", "EVIDENCE_INPUT_UNITS_NOT_FIT"}:
        return "input_capacity"
    if message == "EVIDENCE_ADAPTIVE_SPLIT_CALL_LIMIT":
        return "recovery_budget_exhausted"
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
        return "timeout"
    if "JSON" in message.upper() or isinstance(error, (ValueError, TypeError)):
        return "invalid_output"
    return "inference_failed"


def summarize(outcomes: list[dict]) -> dict:
    processed = [segment for item in outcomes for segment in item["processed_segments"]]
    def covered(segment):
        cursor = segment["start"]
        for span in sorted((value for value in processed if value["unit_id"] == segment["unit_id"]), key=lambda value: value["start"]):
            if span["start"] > cursor:
                break
            cursor = max(cursor, span["end"])
        return cursor >= segment["end"]
    remaining = [segment for item in outcomes for segment in item["unprocessed_segments"] if not covered(segment)]
    incomplete = [item for item in outcomes if item["status"] in {"partial", "failed"} and (
        not item["unprocessed_segments"] or any(not covered(segment) for segment in item["unprocessed_segments"]))]
    complete = bool(processed) or any(item.get("fact_ids") for item in outcomes)
    return {
        "status": "partial" if incomplete and complete else "failed" if incomplete else "completed",
        "processed_segments": processed,
        "unprocessed_segments": remaining,
        "batches": outcomes,
        "retryable": any(item["retryable"] for item in incomplete),
    }
