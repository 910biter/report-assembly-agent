"""In-process report task queue.

This keeps the initial single-user deployment simple while preventing multiple
long report jobs from competing for the same local model resources.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field

from app.memory import short_term

TASK_PRIORITY_NORMAL = 50


@dataclass(order=True)
class _QueuedTask:
    priority: int
    sequence: int
    task_id: str = field(compare=False)
    queued_at: float = field(default_factory=time.time, compare=False)


_QUEUE: queue.PriorityQueue[_QueuedTask] = queue.PriorityQueue()
_SEQUENCE = itertools.count()
_LOCK = threading.Lock()
_WORKER_STARTED = False
_RUNNING_TASK_ID: str | None = None
_QUEUED_TASK_IDS: set[str] = set()
_CANCELLED_TASK_IDS: set[str] = set()
_STATS = {
    "submitted": 0,
    "started": 0,
    "completed": 0,
    "waiting_for_user": 0,
    "failed": 0,
    "queue_wait_seconds": 0.0,
    "max_queue_wait_seconds": 0.0,
}

_ACTIVE_QUEUE_STATES = {"queued", "running", "pause_requested"}


def reconcile_interrupted_tasks() -> int:
    """Pause persisted in-process jobs that cannot survive a service restart."""
    reconciled = 0
    now = round(time.time(), 1)
    for task_id, task in short_term.list_tasks():
        queue_state = str((task.get("queue_status") or {}).get("status") or "")
        if queue_state not in _ACTIVE_QUEUE_STATES:
            continue
        stage = str(task.get("stage") or "")
        if stage in {"review", "done"}:
            short_term.update_task(task_id, control_request="", queue_status={
                "status": "completed", "finished_at": now,
                "note": "status reconciled after service restart",
            })
            continue
        if stage == "failed":
            short_term.update_task(task_id, control_request="", queue_status={
                "status": "failed", "finished_at": now,
                "note": "status reconciled after service restart",
            })
            continue
        short_term.update_task(
            task_id,
            stage="paused",
            control_request="",
            queue_status={
                "status": "paused",
                "finished_at": now,
                "note": "task paused because the service restarted",
            },
        )
        run_id = str(task.get("run_id") or "")
        if run_id:
            try:
                from app.task_runs import update_task_run
                update_task_run(
                    run_id,
                    status="paused",
                    metadata={"reason": "service_restart"},
                    finished=False,
                )
            except Exception:
                pass
        reconciled += 1
    return reconciled


def enqueue_task(task_id: str, priority: int = TASK_PRIORITY_NORMAL) -> dict:
    """Queue a task if it is not already queued/running."""
    if short_term.load_task(task_id) is None:
        return {"status": "not_found"}
    _ensure_worker()
    with _LOCK:
        if task_id == _RUNNING_TASK_ID:
            return {"status": "already_running"}
        if task_id in _QUEUED_TASK_IDS:
            return {"status": "already_queued", "queue_position": _queue_position(task_id)}
        _CANCELLED_TASK_IDS.discard(task_id)
        _QUEUED_TASK_IDS.add(task_id)
        _STATS["submitted"] += 1
        position = _QUEUE.qsize() + 1
        short_term.update_task(task_id, error="", queue_status={
            "status": "queued",
            "position": position,
            "queued_at": round(time.time(), 1),
        })
        _QUEUE.put(_QueuedTask(priority=priority, sequence=next(_SEQUENCE), task_id=task_id))
    return {"status": "queued", "queue_position": position}


def request_control(task_id: str, action: str) -> dict:
    task = short_term.load_task(task_id)
    if task is None:
        return {"status": "not_found"}
    state = task.get("queue_status") or {}
    if action == "pause":
        with _LOCK:
            worker_owns_task = task_id == _RUNNING_TASK_ID
            queued = task_id in _QUEUED_TASK_IDS
        if not worker_owns_task and not queued:
            if str(state.get("status") or "") not in _ACTIVE_QUEUE_STATES:
                return {"status": "not_running"}
            return short_term.update_task(
                task_id, stage="paused", control_request="",
                queue_status={"status": "paused", "finished_at": round(time.time(), 1)},
            ) | {"status": "paused"}
        if queued and not worker_owns_task:
            with _LOCK:
                _QUEUED_TASK_IDS.discard(task_id)
                _CANCELLED_TASK_IDS.add(task_id)
            short_term.update_task(
                task_id, stage="paused", control_request="",
                queue_status={"status": "paused", "finished_at": round(time.time(), 1)},
            )
            return {"status": "paused"}
        short_term.update_task(task_id, control_request="pause", control_requested_at=round(time.time(), 1))
        from app import task_control
        task_control.request_pause(task_id)
        short_term.update_task(task_id, queue_status={"status": "pause_requested"})
        return {"status": "pause_requested"}
    if action == "resume":
        if task.get("stage") != "paused":
            return {"status": "not_paused"}
        short_term.update_task(task_id, control_request="resume", control_requested_at=round(time.time(), 1))
        from app import task_control
        task_control.clear_pause(task_id)
        return enqueue_task(task_id)
    if action == "restart":
        if state.get("status") in {"running", "queued"}:
            return {"status": "already_running"}
        short_term.update_task(
            task_id, stage="created", control_request="restart",
            control_requested_at=round(time.time(), 1), error="",
        )
        return enqueue_task(task_id)
    return {"status": "unsupported"}


def task_queue_status() -> dict:
    with _LOCK:
        stats = dict(_STATS)
        completed = int(stats.get("completed") or 0)
        stats["avg_queue_wait_seconds"] = (
            round(float(stats.get("queue_wait_seconds") or 0.0) / completed, 3)
            if completed else 0.0
        )
        return {
            "running_task_id": _RUNNING_TASK_ID,
            "pending": len(_QUEUED_TASK_IDS),
            "queued_task_ids": sorted(_QUEUED_TASK_IDS),
            "stats": stats,
        }


def _ensure_worker() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _LOCK:
        if _WORKER_STARTED:
            return
        threading.Thread(target=_worker_loop, daemon=True, name="report-task-worker").start()
        _WORKER_STARTED = True


def _worker_loop() -> None:
    global _RUNNING_TASK_ID
    # Import inside the worker to avoid import cycles during app startup.
    from app.workflow.controller import WorkflowController

    while True:
        item = _QUEUE.get()
        with _LOCK:
            if item.task_id in _CANCELLED_TASK_IDS:
                _CANCELLED_TASK_IDS.discard(item.task_id)
                _QUEUE.task_done()
                continue
        wait = time.time() - item.queued_at
        with _LOCK:
            _QUEUED_TASK_IDS.discard(item.task_id)
            _RUNNING_TASK_ID = item.task_id
            _STATS["started"] += 1
            _STATS["queue_wait_seconds"] = round(float(_STATS["queue_wait_seconds"]) + wait, 3)
            _STATS["max_queue_wait_seconds"] = max(float(_STATS["max_queue_wait_seconds"]), round(wait, 3))
        short_term.update_task(item.task_id, error="", queue_status={
            "status": "running",
            "queue_wait_seconds": round(wait, 1),
            "started_at": round(time.time(), 1),
        })
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop, args=(item.task_id, heartbeat_stop),
            daemon=True, name=f"heartbeat-{item.task_id}",
        )
        heartbeat.start()
        outcome = None
        try:
            from app import task_control
            task_control.begin_task(item.task_id)
            starting_task = short_term.load_task(item.task_id) or {}
            if str(starting_task.get("run_mode") or "") == "interaction_revision":
                try:
                    from app.interaction import mark_recompute_running
                    mark_recompute_running(str(starting_task.get("run_id") or ""))
                except Exception:
                    pass
            outcome = WorkflowController(item.task_id).run_to_review()
            if outcome in {"awaiting_requirements", "awaiting_directory"}:
                with _LOCK:
                    _STATS["waiting_for_user"] += 1
                short_term.update_task(item.task_id, queue_status={
                    "status": "waiting_for_user",
                    "checkpoint": outcome,
                    "queue_wait_seconds": round(wait, 1),
                })
            else:
                with _LOCK:
                    _STATS["completed"] += 1
                short_term.update_task(item.task_id, queue_status={
                    "status": "completed",
                    "queue_wait_seconds": round(wait, 1),
                    "finished_at": round(time.time(), 1),
                })
        except Exception as exc:
            paused = str(exc) == "TASK_PAUSED"
            if not paused:
                with _LOCK:
                    _STATS["failed"] += 1
            task = short_term.load_task(item.task_id) or {}
            rollback_error = ""
            base_version_id = task.get("incremental_base_version_id")
            if not paused and base_version_id and (
                str(task.get("run_mode") or "") == "interaction_revision"
                or bool(task.get("incremental_update"))
            ):
                try:
                    from app.report_versions import restore_report_version
                    if restore_report_version(int(base_version_id)) is None:
                        rollback_error = "BASE_VERSION_NOT_FOUND"
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)[:400]
            from app.task_runs import update_task_run
            update_task_run(
                str(task.get("run_id") or ""),
                status="paused" if paused else "failed",
                metadata={"error": "" if paused else str(exc)[:500], "rollback_error": rollback_error},
                finished=not paused,
            )
            interaction_revision = not paused and str(task.get("run_mode") or "") == "interaction_revision"
            if interaction_revision:
                try:
                    from app.interaction import fail_recompute_for_run
                    fail_recompute_for_run(str(task.get("run_id") or ""), str(exc))
                except Exception:
                    pass
            # A revision runs against the current report and is versioned on success.
            # Its failure must restore the last reviewable report.
            next_stage = "paused" if paused else ("review" if interaction_revision else "failed")
            short_term.update_task(
                item.task_id,
                stage=next_stage,
                error="" if (paused or interaction_revision) else str(exc),
                revision_error=(
                    f"{str(exc)}; rollback_error={rollback_error}"
                    if interaction_revision and rollback_error else str(exc)
                    if interaction_revision else ""
                ),
                queue_status={
                "status": "paused" if paused else "failed",
                "queue_wait_seconds": round(wait, 1),
                "finished_at": round(time.time(), 1),
                "error": "" if paused else str(exc),
            })
        finally:
            from app import task_control
            task_control.end_task(item.task_id)
            heartbeat_stop.set()
            with _LOCK:
                if _RUNNING_TASK_ID == item.task_id:
                    _RUNNING_TASK_ID = None
            _QUEUE.task_done()
            try:
                # A checkpoint is deliberately idle: accepted report revisions
                # must not start competing work before the user resumes it.
                if outcome not in {
                    "awaiting_requirements", "awaiting_directory",
                }:
                    from app.interaction import dispatch_pending_revisions
                    dispatch_pending_revisions(item.task_id)
            except Exception:
                pass


def _heartbeat_loop(task_id: str, stop: threading.Event) -> None:
    while not stop.wait(10):
        task = short_term.load_task(task_id) or {}
        short_term.update_task(
            task_id,
            last_progress_at=round(time.time(), 1),
            heartbeat_stage=task.get("stage", ""),
            heartbeat_status="alive",
        )


def _queue_position(task_id: str) -> int | None:
    queued = [item for item in list(_QUEUE.queue) if item.task_id not in _CANCELLED_TASK_IDS]
    for index, item in enumerate(sorted(queued), start=1):
        if item.task_id == task_id:
            return index
    return None
