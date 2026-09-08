"""Deterministic workflow checkpoint transitions shared by API and copilot."""
from __future__ import annotations

from typing import Any

from app.memory import short_term
from app.workflow.queue import enqueue_task


class CheckpointError(ValueError):
    """A user-visible checkpoint cannot be advanced in the current task state."""


def confirm_requirements(task_id: str, *, theme: str, requirements: str,
                         feedback: str = "") -> dict[str, Any]:
    """Persist the approved brief and continue from AnalysisPlan."""
    task = short_term.load_task(task_id)
    if task is None:
        raise CheckpointError("TASK_NOT_FOUND")
    if not task.get("requirement_review_pending", task.get("planning_review_pending")):
        raise CheckpointError("REQUIREMENT_REVIEW_NOT_PENDING")
    theme, requirements = str(theme or "").strip(), str(requirements or "").strip()
    if not theme:
        raise CheckpointError("THEME_REQUIRED")
    if not requirements:
        raise CheckpointError("REQUIREMENTS_REQUIRED")
    short_term.update_task(
        task_id,
        requirement_review_pending=False,
        requirement_review_completed=True,
        theme=theme,
        user_requirements=requirements,
        requirement_review_feedback=str(feedback or "").strip(),
        # Material parsing and understanding completed before this checkpoint.
        # Resume from planning instead of re-entering the public task entry point.
        stage="planning",
        resume_from_stage="planning",
    )
    return _enqueue_or_raise(task_id, replan_required=True)


def confirm_directory(
    task_id: str, *, feedback: str = "", structure: list[str] | None = None,
) -> dict[str, Any]:
    """Approve the current final plan and continue into narrative/writing."""
    task = short_term.load_task(task_id)
    if task is None:
        raise CheckpointError("TASK_NOT_FOUND")
    if not task.get("directory_review_pending"):
        raise CheckpointError("DIRECTORY_REVIEW_NOT_PENDING")
    feedback = str(feedback or "").strip()
    structure = [str(title).strip() for title in (structure or []) if str(title).strip()]
    fields: dict[str, Any] = {
        "directory_review_pending": False,
        "directory_review_completed": True,
        "directory_review_feedback": feedback,
        "resume_from_stage": "writing",
    }
    if structure:
        from app.workflow.controller import planner as workflow_planner

        plan_id = int(task.get("plan_id") or 0)
        if not plan_id:
            raise CheckpointError("DIRECTORY_PLAN_NOT_FOUND")
        revised = workflow_planner.revise_final_plan_structure(
            plan_id, structure, instruction=feedback or "按用户确认目录调整",
        )
        fields.update(plan_title=revised.title, final_plan_frozen=True)
    if feedback and not structure:
        fields.update(
            intervention_force_final_plan=True,
            incremental_update_reason=feedback,
        )
    short_term.update_task(task_id, **fields)
    return _enqueue_or_raise(task_id, replan_required=bool(feedback))


def _enqueue_or_raise(task_id: str, *, replan_required: bool) -> dict[str, Any]:
    queue = enqueue_task(task_id)
    if queue.get("status") == "not_found":
        raise CheckpointError("TASK_NOT_FOUND")
    return {"task_id": task_id, "status": "confirmed", "replan_required": replan_required, "queue": queue}
