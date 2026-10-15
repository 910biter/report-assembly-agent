"""Unified concurrency boundary for generation and CPU embedding calls."""
import threading
import time
from contextlib import contextmanager

from app.config import settings

class _PriorityCapacity:
    """One process-wide model cap with a foreground-reserved slot."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = {"workflow": 0, "interactive": 0}
        self._waiting = {"workflow": 0, "interactive": 0}

    @staticmethod
    def _total() -> int:
        return max(1, int(settings.llm_concurrency or 1))

    @staticmethod
    def _interactive_limit(total: int) -> int:
        return min(total, max(1, int(settings.interactive_concurrency or 1)))

    @contextmanager
    def slot(self, lane: str):
        total = self._total()
        workflow_limit = max(1, total - 1) if total > 1 else 1
        lane_limit = workflow_limit if lane == "workflow" else self._interactive_limit(total)
        with self._condition:
            self._waiting[lane] += 1
            try:
                self._condition.wait_for(lambda: (
                    sum(self._active.values()) < total
                    and self._active[lane] < lane_limit
                    and (lane != "workflow" or self._waiting["interactive"] == 0)
                ))
                self._active[lane] += 1
            finally:
                self._waiting[lane] -= 1
        try:
            yield
        finally:
            with self._condition:
                self._active[lane] -= 1
                self._condition.notify_all()

    def snapshot(self) -> dict:
        with self._condition:
            return {
                "active": dict(self._active),
                "waiting": dict(self._waiting),
                "total_active": sum(self._active.values()),
                "total_concurrency": self._total(),
            }


_generation_capacity = _PriorityCapacity()
_embedding_lock = threading.RLock()
_lane_stats_lock = threading.Lock()
_lane_stats = {
    "workflow": {"submitted": 0, "completed": 0, "failed": 0, "active": 0, "max_active": 0,
                 "slot_wait_seconds": 0.0},
    "interactive": {"submitted": 0, "completed": 0, "failed": 0, "active": 0, "max_active": 0,
                    "slot_wait_seconds": 0.0},
}


def invoke(kind: str, fn, *args, **kwargs):
    """统一调用入口。kind 标识调用方(agent/template/qa/embed/health/ui)。"""
    if kind == "health":
        return fn(*args, **kwargs)
    if kind == "embed":
        with _embedding_lock:
            return fn(*args, **kwargs)
    # Keep interactive calls in a foreground lane while sharing the same total cap.
    if kind == "review_copilot":
        return _invoke_lane("interactive", fn, *args, **kwargs)
    return _invoke_lane("workflow", fn, *args, **kwargs)


def _invoke_lane(lane: str, fn, *args, **kwargs):
    submitted_at = time.perf_counter()
    with _lane_stats_lock:
        _lane_stats[lane]["submitted"] += 1
    with _generation_capacity.slot(lane), _shared_slot(lane):
        wait = time.perf_counter() - submitted_at
        with _lane_stats_lock:
            stats = _lane_stats[lane]
            stats["slot_wait_seconds"] += wait
            stats["active"] += 1
            stats["max_active"] = max(stats["max_active"], stats["active"])
        try:
            result = fn(*args, **kwargs)
        except Exception:
            with _lane_stats_lock:
                _lane_stats[lane]["failed"] += 1
            raise
        else:
            with _lane_stats_lock:
                _lane_stats[lane]["completed"] += 1
            return result
        finally:
            with _lane_stats_lock:
                _lane_stats[lane]["active"] -= 1


@contextmanager
def _shared_slot(lane: str):
    """Share total model slots across multiuser app processes using PostgreSQL locks."""
    if not settings.multiuser_enabled:
        yield
        return
    from sqlalchemy import func, select
    from app.db import _get_engine
    from app.workflow.execution import _key

    total = max(1, int(settings.llm_concurrency or 1))
    count = max(1, total - 1) if lane == "workflow" and total > 1 else total
    keys = [_key(f"report-assembly:model:total:{index}") for index in range(count)]
    with _get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = None
        while acquired is None:
            for key in keys:
                if connection.execute(select(func.pg_try_advisory_lock(key))).scalar_one():
                    acquired = key
                    break
            if acquired is None:
                time.sleep(0.05)
        try:
            yield
        finally:
            if not connection.invalidated and not connection.closed:
                connection.execute(select(func.pg_advisory_unlock(acquired)))


def model_lane_stats() -> dict:
    with _lane_stats_lock:
        result = {lane: dict(values) for lane, values in _lane_stats.items()}
    result["capacity"] = _generation_capacity.snapshot()
    total = result["capacity"]["total_concurrency"]
    result["workflow"]["configured_total_concurrency"] = total
    result["workflow"]["configured_concurrency"] = (
        max(1, total - 1) if total > 1 else 1
    )
    result["interactive"]["configured_concurrency"] = min(
        total, max(1, int(settings.interactive_concurrency or 1)),
    )
    for values in (result["workflow"], result["interactive"]):
        completed = int(values.get("completed") or 0)
        values["avg_slot_wait_seconds"] = round(
            float(values.get("slot_wait_seconds") or 0.0) / completed, 4,
        ) if completed else 0.0
        values["slot_wait_seconds"] = round(float(values.get("slot_wait_seconds") or 0.0), 4)
    return result
