"""统一 LLM 调度器:所有推理/embedding 调用必须经过此入口。

背景:任务队列只管任务内调用,UI/模板分析/QA/后台可能绕过队列直接调用
推理模型,导致资源状态混乱。本模块统一互斥与资源策略:

- invoke(kind, fn):所有调用(任务/模板/QA/embed/health)经统一锁串行排队
  (Ollama 单模型本就串行,锁保证排队顺序与调用记录统一);
- heavy_stage():重资源阶段(解析/OCR)声明式上下文,按 GPU 策略
  (settings.gpu_memory_tight)决定是否临时卸载推理模型——unload 是
  资源调度策略,不是业务工作流的一部分;显存够时保持常驻零开销。
"""
import threading
from contextlib import contextmanager

from app.config import settings

_lock = threading.RLock()
# 推理模型(qwen-agent)活跃状态:统一模型放置决策依据。
# 解析阶段(heavy_stage, tight)卸载 agent → False → embedding 可独占 GPU;
# LLM 阶段 agent 常驻 → True → embedding 让位走 CPU(显存 24.8G > 24.5G 不能共存)。
_agent_active = True
_agent_state_lock = threading.Lock()
_heavy_active = False


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
    with _lock:
        return fn(*args, **kwargs)


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
