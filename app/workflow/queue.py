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
_STATS = {
    "submitted": 0,
    "started": 0,
    "completed": 0,
    "failed": 0,
    "queue_wait_seconds": 0.0,
    "max_queue_wait_seconds": 0.0,
}


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
        _QUEUED_TASK_IDS.add(task_id)
        _STATS["submitted"] += 1
        position = _QUEUE.qsize() + 1
        short_term.update_task(task_id, queue_status={
            "status": "queued",
            "position": position,
            "queued_at": round(time.time(), 1),
        })
        _QUEUE.put(_QueuedTask(priority=priority, sequence=next(_SEQUENCE), task_id=task_id))
    return {"status": "queued", "queue_position": position}


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
            "pending": _QUEUE.qsize(),
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
        wait = time.time() - item.queued_at
        with _LOCK:
            _QUEUED_TASK_IDS.discard(item.task_id)
            _RUNNING_TASK_ID = item.task_id
            _STATS["started"] += 1
            _STATS["queue_wait_seconds"] = round(float(_STATS["queue_wait_seconds"]) + wait, 3)
            _STATS["max_queue_wait_seconds"] = max(float(_STATS["max_queue_wait_seconds"]), round(wait, 3))
        short_term.update_task(item.task_id, queue_status={
            "status": "running",
            "queue_wait_seconds": round(wait, 1),
            "started_at": round(time.time(), 1),
        })
        try:
            WorkflowController(item.task_id).run_to_review()
            with _LOCK:
                _STATS["completed"] += 1
            short_term.update_task(item.task_id, queue_status={
                "status": "completed",
                "queue_wait_seconds": round(wait, 1),
                "finished_at": round(time.time(), 1),
            })
        except Exception as exc:
            with _LOCK:
                _STATS["failed"] += 1
            short_term.update_task(item.task_id, stage="failed", error=str(exc), queue_status={
                "status": "failed",
                "queue_wait_seconds": round(wait, 1),
                "finished_at": round(time.time(), 1),
                "error": str(exc),
            })
        finally:
            with _LOCK:
                if _RUNNING_TASK_ID == item.task_id:
                    _RUNNING_TASK_ID = None
            _QUEUE.task_done()


def _queue_position(task_id: str) -> int | None:
    queued = list(_QUEUE.queue)
    for index, item in enumerate(sorted(queued), start=1):
        if item.task_id == task_id:
            return index
    return None
