"""Agent 基类:角色 Prompt + Gateway 的封装。

所有 Agent 共用同一模型服务,靠 role(system prompt)区分职责。
"""
import contextlib
import threading
import time

from app.config import settings
from app.gateway import (
    generation_stats,
    last_generation_meta,
    last_generation_stats,
    model_gateway,
    reset_generation_stats,
    reset_last_generation_call,
)
from app.llm_queue import submit_llm_call
from app.llm_scheduler import invoke
from app.token_monitor import log_llm_call, new_call_id


class BaseAgent:
    name = "base"
    role = ""
    max_retries = 2
    # Structured workflow agents must return bounded, auditable JSON. Qwen's
    # chat template enables hidden thinking when this value is omitted, which
    # can consume the output budget before the JSON result is produced.
    thinking: bool | None = False
    output_token_limit: int | None = settings.structured_output_tokens
    last_call_id = ""

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self._with_retry(
            lambda: submit_llm_call(lambda: invoke(
                "agent", model_gateway.generate, prompt,
                system=system or self.role, max_tokens=self.output_token_limit,
            )),
            len(prompt),
        )

    def generate_json(self, prompt: str, system: str | None = None,
                      max_tokens: int | None = None) -> dict:
        return self._with_retry(
            lambda: submit_llm_call(lambda: invoke(
                "agent", model_gateway.generate_json, prompt,
                system=system or self.role, think=self.thinking,
                max_tokens=max_tokens or self.output_token_limit,
            )),
            len(prompt),
        )

    def _with_retry(self, fn, chars: int):
        last_error = None
        # A logical call may have several transport attempts.  The identifier is
        # only observational and never enters the model prompt or business data.
        logical_call_id = new_call_id()
        for attempt in range(self.max_retries + 1):
            call_id = new_call_id()
            self.last_call_id = call_id
            count_llm_call(chars)
            reset_last_generation_call()
            started = time.time()
            capture_scope = contextlib.nullcontext()
            if settings.benchmark_capture_enabled:
                from app.benchmark_capture import benchmark_call_context
                capture_scope = benchmark_call_context(
                    call_id=call_id, logical_call_id=logical_call_id,
                    agent=self.name, attempt=attempt,
                )
            with capture_scope:
                try:
                    result = fn()
                    elapsed = time.time() - started
                    count_llm_duration(self.name, elapsed, chars)
                    log_llm_call(
                        call_id, self.name, chars,
                        last_generation_stats(),
                        elapsed, retry_count=attempt, success=True,
                        **last_generation_meta(),
                    )
                    return result
                except Exception as exc:
                    if str(exc) == "TASK_PAUSED":
                        raise
                    elapsed = time.time() - started
                    count_llm_duration(self.name, elapsed, chars)
                    log_llm_call(
                        call_id, self.name, chars,
                        last_generation_stats(),
                        elapsed, retry_count=attempt, success=False, error=str(exc),
                        **last_generation_meta(),
                    )
                    last_error = exc
                    count_llm_retry()
                    if attempt >= self.max_retries:
                        break
                    time.sleep(1.5 * (attempt + 1))
        raise last_error


# ---------- LLM 调用统计(性能观测:每任务调用次数与输入规模) ----------

_LLM_LOCK = threading.Lock()
_LLM_CALLS: dict = {
    "calls": 0,
    "chars": 0,
    "retries": 0,
    "total_seconds": 0.0,
    "max_seconds": 0.0,
    "by_agent": {},
}


def reset_llm_stats() -> None:
    with _LLM_LOCK:
        _LLM_CALLS["calls"] = 0
        _LLM_CALLS["chars"] = 0
        _LLM_CALLS["retries"] = 0
        _LLM_CALLS["total_seconds"] = 0.0
        _LLM_CALLS["max_seconds"] = 0.0
        _LLM_CALLS["by_agent"] = {}
    reset_generation_stats()


def count_llm_call(chars: int) -> None:
    with _LLM_LOCK:
        _LLM_CALLS["calls"] += 1
        _LLM_CALLS["chars"] += chars


def count_llm_retry() -> None:
    with _LLM_LOCK:
        _LLM_CALLS["retries"] += 1


def count_llm_duration(agent: str, seconds: float, chars: int = 0) -> None:
    with _LLM_LOCK:
        seconds = round(seconds, 3)
        _LLM_CALLS["total_seconds"] = round(float(_LLM_CALLS.get("total_seconds", 0.0)) + seconds, 3)
        _LLM_CALLS["max_seconds"] = max(float(_LLM_CALLS.get("max_seconds", 0.0)), seconds)
        by_agent = _LLM_CALLS.setdefault("by_agent", {})
        item = by_agent.setdefault(agent or "base", {"calls": 0, "seconds": 0.0, "chars": 0})
        item["calls"] += 1
        item["seconds"] = round(float(item.get("seconds", 0.0)) + seconds, 3)
        item["chars"] = int(item.get("chars", 0)) + int(chars or 0)


def llm_stats() -> dict:
    with _LLM_LOCK:
        stats = dict(_LLM_CALLS)
        calls = int(stats.get("calls") or 0)
        stats["avg_seconds"] = round(float(stats.get("total_seconds") or 0.0) / calls, 3) if calls else 0.0
        stats["by_agent"] = {k: dict(v) for k, v in dict(stats.get("by_agent") or {}).items()}
        stats["tokens"] = generation_stats()
        return stats
