"""统一 LLM 资源边界:所有推理/embedding 调用必须经过此入口。

背景:任务队列只管任务内调用,UI/模板分析/QA/后台可能绕过队列直接调用
推理模型,导致资源状态混乱。本模块统一互斥与资源策略:

- invoke(kind, fn):Ollama 串行；vLLM 使用有界并发，让服务端执行连续批处理；
- heavy_stage():重资源阶段(解析/OCR)声明式上下文,按 GPU 策略
  (settings.gpu_memory_tight)决定是否临时卸载推理模型——unload 是
  资源调度策略,不是业务工作流的一部分;显存够时保持常驻零开销。
"""
import contextlib
import threading
import time
import uuid
from contextlib import contextmanager

from app.config import settings

_generation_slots = threading.BoundedSemaphore(
    max(1, int(settings.llm_concurrency or 1))
    if str(settings.generation_backend).lower() == "vllm" else 1
)
_interactive_slots = threading.BoundedSemaphore(max(1, int(settings.interactive_concurrency or 1)))
_embedding_lock = threading.RLock()
# 推理模型(qwen-agent)活跃状态:统一模型放置决策依据。
# 解析阶段(heavy_stage, tight)卸载 agent → False → embedding 可独占 GPU;
# LLM 阶段 agent 常驻 → True → embedding 让位走 CPU(显存 24.8G > 24.5G 不能共存)。
_agent_active = True
_agent_state_lock = threading.Lock()
_heavy_active = False
_lane_stats_lock = threading.Lock()
_lane_stats = {
    "workflow": {"submitted": 0, "completed": 0, "failed": 0, "active": 0, "max_active": 0,
                 "slot_wait_seconds": 0.0},
    "interactive": {"submitted": 0, "completed": 0, "failed": 0, "active": 0, "max_active": 0,
                    "slot_wait_seconds": 0.0},
}


def set_agent_active(active: bool) -> None:
    global _agent_active
    with _agent_state_lock:
        _agent_active = active


def embedding_num_gpu() -> int:
    """embedding 动态放置:解析重阶段让位给 Docling GPU。"""
    with _agent_state_lock:
        if _agent_active:
            return 0
    return settings.gpu_layers


def invoke(kind: str, fn, *args, **kwargs):
    """统一调用入口。kind 标识调用方(agent/template/qa/embed/health/ui)。"""
    if kind == "health":
        return fn(*args, **kwargs)
    if kind == "embed":
        with _embedding_lock:
            return fn(*args, **kwargs)
    # 交互请求需要先到达 vLLM，服务端优先级才有机会生效。它不能与长工作流
    # 共用本地 generation semaphore，否则所有工作流槽位繁忙时会被提前阻塞。
    capture_scope = contextlib.nullcontext()
    if settings.benchmark_capture_enabled:
        from app.benchmark_capture import benchmark_call_context, current_benchmark_call_context
        if not current_benchmark_call_context().get("call_id"):
            call_id = uuid.uuid4().hex[:16]
            capture_scope = benchmark_call_context(
                call_id=call_id, logical_call_id=call_id, agent=str(kind), attempt=0,
            )
    with capture_scope:
        if kind == "review_copilot":
            return _invoke_lane("interactive", _interactive_slots, fn, *args, capture_kind=kind, **kwargs)
        return _invoke_lane("workflow", _generation_slots, fn, *args, capture_kind=kind, **kwargs)


def _invoke_lane(lane: str, slots, fn, *args, capture_kind: str = "", **kwargs):
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
        executed_at = time.perf_counter()
        success = False
        error = ""
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            error = str(exc)[:500]
            with _lane_stats_lock:
                _lane_stats[lane]["failed"] += 1
            raise
        else:
            with _lane_stats_lock:
                _lane_stats[lane]["completed"] += 1
            success = True
            return result
        finally:
            with _lane_stats_lock:
                _lane_stats[lane]["active"] -= 1
            if settings.benchmark_capture_enabled:
                try:
                    from app.benchmark_capture import capture_enrichment, current_benchmark_call_context
                    call_id = str(current_benchmark_call_context().get("call_id") or "")
                    capture_enrichment(call_id, "scheduler", {
                        "kind": str(capture_kind), "lane": lane,
                        "queue_wait_seconds": round(wait, 6),
                        "execution_seconds": round(time.perf_counter() - executed_at, 6),
                        "success": success, "error": error,
                    })
                except Exception:
                    pass


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


@contextmanager
def heavy_stage(model: str = ""):
    """重资源阶段(解析/OCR 等):按 GPU 策略决定卸载/预热推理模型。

    tight 模式下进入即卸载 agent(agent 状态置 False → embedding 独占 GPU),
    退出时预热恢复(agent 状态置 True → embedding 自动让位走 CPU)。
    """
    from app.gateway import model_gateway
    tight = settings.gpu_memory_tight
    if tight:
        model_gateway.unload_model(model)
        global _heavy_active, _agent_active
        with _agent_state_lock:
            _heavy_active = True
            _agent_active = False
    try:
        yield
    finally:
        if tight:
            model_gateway.warmup_model(model)
            with _agent_state_lock:
                _heavy_active = False
                _agent_active = True
