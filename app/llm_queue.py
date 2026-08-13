"""Single-model priority queue.

The deployment currently has one generation model. Running multiple generation
requests concurrently mostly moves the bottleneck into the model server, so we
serialize generation calls and prioritize user-visible work over background
jobs.
"""
from __future__ import annotations

import contextlib
import contextvars
import itertools
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

PRIORITY_INTERACTIVE = 10
PRIORITY_NORMAL = 50
PRIORITY_BACKGROUND = 90

_priority_context: contextvars.ContextVar[int] = contextvars.ContextVar(
    "llm_priority",
    default=PRIORITY_NORMAL,
)


@dataclass(order=True)
class _QueuedCall:
    priority: int
    sequence: int
    submitted_at: float = field(compare=False)
    fn: Callable[[], Any] = field(compare=False)
    done: threading.Event = field(default_factory=threading.Event, compare=False)
    result: Any = field(default=None, compare=False)
    error: BaseException | None = field(default=None, compare=False)
    started_at: float | None = field(default=None, compare=False)
    finished_at: float | None = field(default=None, compare=False)


_QUEUE: queue.PriorityQueue[_QueuedCall] = queue.PriorityQueue()
_SEQUENCE = itertools.count()
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()
_STATS_LOCK = threading.Lock()
_STATS = {
    "submitted": 0,
    "completed": 0,
    "failed": 0,
    "queue_wait_seconds": 0.0,
    "max_queue_wait_seconds": 0.0,
}


def _ensure_worker() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        threading.Thread(target=_worker_loop, daemon=True, name="llm-priority-worker").start()
        _WORKER_STARTED = True


def _worker_loop() -> None:
    while True:
        item = _QUEUE.get()
        item.started_at = time.time()
        wait = item.started_at - item.submitted_at
        with _STATS_LOCK:
            _STATS["queue_wait_seconds"] = round(float(_STATS["queue_wait_seconds"]) + wait, 3)
            _STATS["max_queue_wait_seconds"] = max(float(_STATS["max_queue_wait_seconds"]), round(wait, 3))
        try:
            item.result = item.fn()
            with _STATS_LOCK:
                _STATS["completed"] += 1
        except BaseException as exc:  # propagate the original failure to caller
            item.error = exc
            with _STATS_LOCK:
                _STATS["failed"] += 1
        finally:
            item.finished_at = time.time()
            item.done.set()
            _QUEUE.task_done()


def submit_llm_call(fn: Callable[[], Any], priority: int | None = None) -> Any:
    """Submit one model generation call and block until it completes."""
    _ensure_worker()
    item = _QueuedCall(
        priority=int(priority if priority is not None else _priority_context.get()),
        sequence=next(_SEQUENCE),
        submitted_at=time.time(),
        fn=fn,
    )
    with _STATS_LOCK:
        _STATS["submitted"] += 1
    _QUEUE.put(item)
    item.done.wait()
    if item.error is not None:
        raise item.error
    return item.result


@contextlib.contextmanager
def llm_priority(priority: int):
    token = _priority_context.set(priority)
    try:
        yield
    finally:
        _priority_context.reset(token)


def llm_queue_stats() -> dict:
    with _STATS_LOCK:
        stats = dict(_STATS)
    stats["pending"] = _QUEUE.qsize()
    completed = int(stats.get("completed") or 0)
    stats["avg_queue_wait_seconds"] = (
        round(float(stats.get("queue_wait_seconds") or 0.0) / completed, 3)
        if completed else 0.0
    )
    return stats
