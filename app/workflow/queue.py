"""In-process report task queue.

This keeps the initial single-user deployment simple while preventing multiple
long report jobs from competing for the same local model resources.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field

from app.config import settings
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
    if settings.multiuser_enabled:
        return 0
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
    from app.licensing.service import require_license
    require_license()
    if short_term.load_task(task_id) is None:
        return {"status": "not_found"}
    if settings.multiuser_enabled:
        from app.workflow import queue_store
        from app.infrastructure.orm import ORMShortMemory
        from sqlalchemy import select

        result = {}

        def transition(payload, session):
            owner = session.execute(select(ORMShortMemory.c.owner_user_id).where(
                ORMShortMemory.c.task_id == task_id,
            )).scalar_one()
            if owner is None:
                raise ValueError("TASK_OWNER_REQUIRED_FOR_QUEUE")
            run_id = str(payload.get("run_id") or "")
            if not run_id:
                raise ValueError("RUN_ID_REQUIRED_FOR_QUEUE")
            result.update(queue_store.enqueue(task_id, run_id, owner, priority, _session=session))
            if not result["duplicate"]:
                payload["error"] = ""
                payload["queue_status"] = {"status": "queued", "queued_at": round(time.time(), 1)}
            return payload

        try:
            short_term.transition_task(task_id, transition)
        except ValueError as exc:
            if str(exc) == "USER_QUEUE_LIMIT_REACHED":
                return {"status": "queue_limit"}
            raise
        _ensure_worker()
        position = queue_store.position(task_id)
        return {
            "status": "already_running" if result["duplicate"] and result["status"] == "running"
            else "already_queued" if result["duplicate"] else "queued",
            "queue_position": position,
        }
    _ensure_worker()
    with _LOCK:
        if task_id == _RUNNING_TASK_ID:
            return {"status": "already_running"}
        if task_id in _QUEUED_TASK_IDS:
            return {"status": "already_queued", "queue_position": _queue_position(task_id)}
        position = _QUEUE.qsize() + 1
        short_term.update_task(task_id, error="", queue_status={
            "status": "queued",
            "position": position,
            "queued_at": round(time.time(), 1),
        })
        # Persist first: a database error must not leave an in-memory ghost
        # reservation that reports this task as queued but never runs it.
        _CANCELLED_TASK_IDS.discard(task_id)
        _QUEUE.put(_QueuedTask(priority=priority, sequence=next(_SEQUENCE), task_id=task_id))
        _QUEUED_TASK_IDS.add(task_id)
        _STATS["submitted"] += 1
    return {"status": "queued", "queue_position": position}


def request_control(task_id: str, action: str) -> dict:
    task = short_term.load_task(task_id)
    if task is None:
        return {"status": "not_found"}
    if settings.multiuser_enabled:
        return _durable_request_control(task_id, task, action)
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
    if settings.multiuser_enabled:
        from app.workflow.queue_store import snapshot
        return snapshot()
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


def _durable_request_control(task_id: str, task: dict, action: str) -> dict:
    from app.workflow import queue_store

    entry = queue_store.active_state(task_id)
    if action == "pause":
        if entry is None:
            return {"status": "not_running"}
        if entry["status"] == "queued":
            def transition(payload, session):
                if not queue_store.cancel(task_id, _session=session):
                    raise ValueError("TASK_STARTED_DURING_PAUSE")
                payload.update(stage="paused", control_request="", queue_status={
                    "status": "paused", "finished_at": round(time.time(), 1),
                })
                return payload
            try:
                short_term.transition_task(task_id, transition)
            except ValueError:
                return {"status": "already_running"}
            return {"status": "paused"}
        short_term.update_task(task_id, control_request="pause",
                               control_requested_at=round(time.time(), 1),
                               queue_status={"status": "pause_requested"})
        from app import task_control
        task_control.request_pause(task_id)
        return {"status": "pause_requested"}
    if action == "resume":
        if task.get("stage") != "paused":
            return {"status": "not_paused"}
        short_term.update_task(task_id, control_request="resume",
                               control_requested_at=round(time.time(), 1))
        from app import task_control
        task_control.clear_pause(task_id)
        return enqueue_task(task_id)
    if action == "restart":
        if entry is not None:
            return {"status": "already_running"}
        short_term.update_task(task_id, stage="created", control_request="restart",
                               control_requested_at=round(time.time(), 1), error="")
        return enqueue_task(task_id)
    return {"status": "unsupported"}


def _worker_loop() -> None:
    while True:
        from app.licensing.service import get_service
        if not get_service().status()["active"]:
            time.sleep(1)
            continue
        if settings.multiuser_enabled:
            try:
                ran = _durable_claim_and_run()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Durable report worker failed")
                ran = False
            if not ran:
                time.sleep(1)
            continue
        item = _QUEUE.get()
        with _LOCK:
            if item.task_id in _CANCELLED_TASK_IDS:
                _CANCELLED_TASK_IDS.discard(item.task_id)
                _QUEUE.task_done()
                continue
        _process_item(item)


def _durable_claim_and_run() -> bool:
    from app.workflow import queue_store
    from app.workflow.execution import execution_slot

    with execution_slot(wait=True) as slot:
        from app.licensing.service import get_service
        if not get_service().status()["active"]:
            return False
        queue_store.reconcile_running()
        claimed = queue_store.claim_next()
        if claimed is None:
            return False
        item = _QueuedTask(
            priority=int(claimed["priority"]), sequence=int(claimed["id"]),
            task_id=str(claimed["task_id"]), queued_at=float(claimed["queued_at"]),
        )
        _process_item(item, held_slot=slot, entry_id=int(claimed["id"]))
    return True


def _process_item(item: _QueuedTask, *, held_slot=None, entry_id: int | None = None) -> None:
    global _RUNNING_TASK_ID
    from app.workflow.controller import WorkflowController
    # Both queue modes run the same report workflow and completion path.
    if item is not None:
        wait = time.time() - item.queued_at
        with _LOCK:
            _QUEUED_TASK_IDS.discard(item.task_id)
            _RUNNING_TASK_ID = item.task_id
            _STATS["started"] += 1
            _STATS["queue_wait_seconds"] = round(float(_STATS["queue_wait_seconds"]) + wait, 3)
            _STATS["max_queue_wait_seconds"] = max(float(_STATS["max_queue_wait_seconds"]), round(wait, 3))
        short_term.update_task(item.task_id, error="", license_blocked=False, queue_status={
            "status": "running",
            "queue_wait_seconds": round(wait, 1),
            "started_at": round(time.time(), 1),
        })
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop, args=(item.task_id, heartbeat_stop, entry_id),
            daemon=True, name=f"heartbeat-{item.task_id}",
        )
        heartbeat.start()
        outcome = None
        finish_status = "failed"
        finish_error = ""
        final_fields = None
        try:
            from app.workflow.execution import execution_slot
            with (nullcontext(held_slot) if held_slot is not None else execution_slot(wait=True)) as slot:
                with slot.task(item.task_id):
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
                finish_status = "paused"
                with _LOCK:
                    _STATS["waiting_for_user"] += 1
                final_fields = {"queue_status": {
                    "status": "waiting_for_user",
                    "checkpoint": outcome,
                    "queue_wait_seconds": round(wait, 1),
                }}
                if entry_id is None:
                    short_term.update_task(item.task_id, **final_fields)
            else:
                finish_status = "completed"
                with _LOCK:
                    _STATS["completed"] += 1
                final_fields = {"queue_status": {
                    "status": "completed",
                    "queue_wait_seconds": round(wait, 1),
                    "finished_at": round(time.time(), 1),
                }}
                if entry_id is None:
                    short_term.update_task(item.task_id, **final_fields)
        except Exception as exc:
            from app.licensing.service import LicenseBlocked
            license_paused = isinstance(exc, LicenseBlocked)
            paused = str(exc) == "TASK_PAUSED" or license_paused
            finish_status = "paused" if paused else "failed"
            finish_error = str(exc)
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
            final_fields = {
                "license_blocked": license_paused,
                "stage": next_stage,
                "error": "" if (paused or interaction_revision) else str(exc),
                "revision_error": (
                    f"{str(exc)}; rollback_error={rollback_error}"
                    if interaction_revision and rollback_error else str(exc)
                    if interaction_revision else ""
                ),
                "queue_status": {
                "status": "paused" if paused else "failed",
                "queue_wait_seconds": round(wait, 1),
                "finished_at": round(time.time(), 1),
                "error": "" if paused else str(exc),
                },
            }
            if entry_id is None:
                short_term.update_task(item.task_id, **final_fields)
        finally:
            from app import task_control
            task_control.end_task(item.task_id)
            heartbeat_stop.set()
            with _LOCK:
                if _RUNNING_TASK_ID == item.task_id:
                    _RUNNING_TASK_ID = None
            if entry_id is None:
                _QUEUE.task_done()
            else:
                from app.workflow.queue_store import finish
                finish(entry_id, finish_status, finish_error,
                       task_id=item.task_id, task_updates=final_fields)
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


def _heartbeat_loop(task_id: str, stop: threading.Event, entry_id: int | None = None) -> None:
    while not stop.wait(10):
        if entry_id is not None:
            from app.workflow.queue_store import heartbeat
            heartbeat(entry_id)
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
