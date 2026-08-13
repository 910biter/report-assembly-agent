"""Agent 基类:角色 Prompt + Gateway 的封装。

所有 Agent 共用同一模型服务,靠 role(system prompt)区分职责。
"""
import threading
import time

from app.gateway import generation_stats, last_generation_meta, model_gateway, reset_generation_stats
from app.llm_queue import submit_llm_call
from app.token_monitor import generation_delta, log_llm_call, new_call_id


class BaseAgent:
    name = "base"
    role = ""
    max_retries = 2
    last_call_id = ""

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self._with_retry(
            lambda: submit_llm_call(lambda: model_gateway.generate(prompt, system=system or self.role)),
            len(prompt),
        )

    def generate_json(self, prompt: str, system: str | None = None) -> dict:
        return self._with_retry(
            lambda: submit_llm_call(lambda: model_gateway.generate_json(prompt, system=system or self.role)),
            len(prompt),
        )

    def _with_retry(self, fn, chars: int):
        last_error = None
        for attempt in range(self.max_retries + 1):
            call_id = new_call_id()
            self.last_call_id = call_id
            count_llm_call(chars)
            before_tokens = generation_stats()
            started = time.time()
            try:
                result = fn()
                elapsed = time.time() - started
                count_llm_duration(self.name, elapsed, chars)
                log_llm_call(
                    call_id, self.name, chars,
                    generation_delta(before_tokens, generation_stats()),
                    elapsed, retry_count=attempt, success=True,
                    **last_generation_meta(),
                )
                return result
            except Exception as exc:
                elapsed = time.time() - started
                count_llm_duration(self.name, elapsed, chars)
                log_llm_call(
                    call_id, self.name, chars,
                    generation_delta(before_tokens, generation_stats()),
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
