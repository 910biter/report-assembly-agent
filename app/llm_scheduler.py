"""Unified concurrency boundary for generation and CPU embedding calls."""
import threading
import time

from app.config import settings

_generation_slots = threading.BoundedSemaphore(
    max(1, int(settings.llm_concurrency or 1))
)
_interactive_slots = threading.BoundedSemaphore(max(1, int(settings.interactive_concurrency or 1)))
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
    # 交互请求需要先到达 vLLM，服务端优先级才有机会生效。它不能与长工作流
    # 共用本地 generation semaphore，否则所有工作流槽位繁忙时会被提前阻塞。
    if kind == "review_copilot":
        return _invoke_lane("interactive", _interactive_slots, fn, *args, **kwargs)
    return _invoke_lane("workflow", _generation_slots, fn, *args, **kwargs)


def _invoke_lane(lane: str, slots, fn, *args, **kwargs):
    submitted_at = time.perf_counter()
    with _lane_stats_lock:
        _lane_stats[lane]["submitted"] += 1
    with slots:
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


def model_lane_stats() -> dict:
    with _lane_stats_lock:
        result = {lane: dict(values) for lane, values in _lane_stats.items()}
    result["workflow"]["configured_concurrency"] = max(1, int(settings.llm_concurrency or 1))
    result["interactive"]["configured_concurrency"] = max(
        1, int(settings.interactive_concurrency or 1),
    )
    for values in result.values():
        completed = int(values.get("completed") or 0)
        values["avg_slot_wait_seconds"] = round(
            float(values.get("slot_wait_seconds") or 0.0) / completed, 4,
        ) if completed else 0.0
        values["slot_wait_seconds"] = round(float(values.get("slot_wait_seconds") or 0.0), 4)
    return result
