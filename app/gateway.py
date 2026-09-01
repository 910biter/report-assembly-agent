"""OpenAI-compatible generation and local embedding gateway."""
import json
import re
import threading
import time
import contextvars
from typing import Protocol

import httpx

from app.config import settings

_GENERATION_STATS_LOCK = threading.Lock()
_GENERATION_STATS = {
    "chat_calls": 0,
    "prompt_tokens": 0,
    "output_tokens": 0,
    "prompt_eval_seconds": 0.0,
    "output_eval_seconds": 0.0,
    "total_seconds": 0.0,
}
_LAST_GENERATION_META = {
    "returned_chars": 0,
    "valid_json_chars": 0,
}
_LAST_GENERATION_META_CONTEXT = contextvars.ContextVar("last_generation_meta", default={})
_LAST_GENERATION_STATS = contextvars.ContextVar("last_generation_stats", default={})


class ModelGateway(Protocol):
    def generate(self, prompt: str, system: str | None = None,
                 max_tokens: int | None = None) -> str: ...
    def generate_json(self, prompt: str, system: str | None = None,
                      think: bool | None = None,
                      max_tokens: int | None = None) -> dict: ...
    def embed(self, texts: list[str], query: bool = False, **kwargs) -> list[list[float]]: ...
    def health(self) -> dict: ...


class OpenAICompatibleGateway:
    """vLLM generation client with an independent CPU embedding runtime."""

    def __init__(self, base_url: str = "") -> None:
        self.generation_url = (
            base_url or settings.generation_url or "http://127.0.0.1:8100/v1"
        ).rstrip("/")

    def generate(self, prompt: str, system: str | None = None,
                 max_tokens: int | None = None) -> str:
        return self._chat(prompt, system=system, json_mode=False, max_tokens=max_tokens)

    def generate_json(self, prompt: str, system: str | None = None,
                      think: bool | None = None,
                      max_tokens: int | None = None) -> dict:
        content = self._chat(
            prompt, system, json_mode=False, think=think, max_tokens=max_tokens,
        )
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        if fenced:
            content = fenced.group(1)
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end < start:
            raise ValueError("MODEL_JSON_NOT_FOUND")
        json_text = content[start : end + 1]
        try:
            value = json.loads(json_text)
        except json.JSONDecodeError:
            json_text = _repair_json_text(json_text)
            value = json.loads(json_text)
        if not isinstance(value, dict):
            raise TypeError("MODEL_JSON_OBJECT_REQUIRED")
        meta = last_generation_meta()
        _record_generation_meta(
            returned_chars=int(meta.get("returned_chars") or 0),
            valid_json_chars=len(json_text),
        )
        return value

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.generation_api_key:
            headers["Authorization"] = f"Bearer {settings.generation_api_key}"
        return headers

    def embed(self, texts: list[str], query: bool = False, **kwargs) -> list[list[float]]:
        from app.local_embedding import local_embedding_runtime

        return local_embedding_runtime.embed(texts, query=query)

    def _chat(self, prompt: str, system: str | None = None, json_mode: bool = False,
              think: bool | None = None, max_tokens: int | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": settings.generation_model,
            "messages": messages,
            "stream": False,
            "max_tokens": int(max_tokens or settings.generation_reserve_tokens),
        }
        # Lower values are more urgent in vLLM's priority scheduler.
        from app.llm_queue import current_llm_priority
        payload["priority"] = current_llm_priority()
        if think is not None:
            payload["chat_template_kwargs"] = {
                "enable_thinking": bool(think),
                "preserve_thinking": False,
            }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        from app import task_control
        task_id = task_control.active_task_id()
        client = httpx.Client(timeout=settings.gateway_timeout_seconds)
        if task_id:
            task_control.register_client(task_id, client)
        started = time.perf_counter()
        try:
            response = client.post(
                f"{self.generation_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            adjusted_max_tokens = _context_safe_max_tokens(
                response,
                requested_max_tokens=int(payload["max_tokens"]),
            )
            if adjusted_max_tokens is not None:
                payload["max_tokens"] = adjusted_max_tokens
                response = client.post(
                    f"{self.generation_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except Exception:
            if task_id and task_control.is_paused(task_id):
                raise RuntimeError("TASK_PAUSED")
            raise
        finally:
            if task_id:
                task_control.unregister_client(task_id, client)
            client.close()
        if task_id:
            task_control.raise_if_paused(task_id)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip().replace("\n", " ")[:500]
            raise RuntimeError(
                f"MODEL_HTTP_{response.status_code}:{detail or 'empty response'}"
            ) from exc
        result = response.json()
        _record_openai_generation_stats(result, time.perf_counter() - started)
        if _completion_was_truncated(result):
            raise RuntimeError("MODEL_OUTPUT_TRUNCATED")
        content = str(result["choices"][0]["message"].get("content") or "").strip()
        _record_generation_meta(returned_chars=len(content), valid_json_chars=0)
        return content

    def health(self) -> dict:
        response = httpx.get(
            f"{self.generation_url}/models",
            headers=self._headers(),
            timeout=5,
        )
        response.raise_for_status()
        available = {str(item.get("id")) for item in response.json().get("data", [])}
        from app.local_embedding import local_embedding_runtime

        embedding_health = local_embedding_runtime.health()
        return {
            "version": "openai-compatible",
            "backend": "vllm",
            "models": {
                settings.generation_model: settings.generation_model in available,
                settings.embedding_model: bool(embedding_health["available"]),
            },
            "embedding": embedding_health,
        }


def _context_safe_max_tokens(response, requested_max_tokens: int) -> int | None:
    """Recover once when chat-template tokens barely cross the model window.

    Prompt builders reserve context conservatively, but the serving tokenizer
    is the final authority. vLLM reports the exact prompt length in its 400
    response; reducing only the unused generation ceiling avoids three
    identical retries without dropping evidence from the request.
    """
    if int(getattr(response, "status_code", 0) or 0) != 400:
        return None
    detail = str(getattr(response, "text", "") or "")
    match = re.search(r"prompt contains at least\s+(\d+)\s+input tokens", detail, re.I)
    if not match:
        return None
    # The reported prompt count is only a lower bound: vLLM stops tokenizing as
    # soon as it can prove the request is too large. It therefore cannot be used
    # to calculate an exact remainder. A bounded decrement is deterministic and
    # leaves prompt selection to the stage-specific context builder.
    available = int(requested_max_tokens) - 512
    if available < 512:
        return None
    return available


def _completion_was_truncated(payload: dict) -> bool:
    choices = payload.get("choices") or []
    return bool(choices and str(choices[0].get("finish_reason") or "").lower() == "length")


model_gateway: ModelGateway = OpenAICompatibleGateway()


def _repair_json_text(text: str) -> str:
    """Best-effort repair for common non-streaming local-model JSON glitches."""
    value = (text or "").strip()
    value = re.sub(r",\s*([}\]])", r"\1", value)
    stack = []
    in_string = False
    escaped = False
    for ch in value:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    if in_string:
        value += '"'
    while stack:
        value += "}" if stack.pop() == "{" else "]"
    return value


def reset_generation_stats() -> None:
    with _GENERATION_STATS_LOCK:
        for key in _GENERATION_STATS:
            _GENERATION_STATS[key] = 0 if key.endswith("calls") or key.endswith("tokens") else 0.0


def generation_stats() -> dict:
    with _GENERATION_STATS_LOCK:
        stats = dict(_GENERATION_STATS)
    output_seconds = float(stats.get("output_eval_seconds") or 0.0)
    prompt_seconds = float(stats.get("prompt_eval_seconds") or 0.0)
    total_seconds = float(stats.get("total_seconds") or 0.0)
    output_tokens = int(stats.get("output_tokens") or 0)
    prompt_tokens = int(stats.get("prompt_tokens") or 0)
    stats["output_tokens_per_second"] = round(output_tokens / output_seconds, 2) if output_seconds else 0.0
    stats["prompt_tokens_per_second"] = round(prompt_tokens / prompt_seconds, 2) if prompt_seconds else 0.0
    stats["overall_tokens_per_second"] = (
        round((prompt_tokens + output_tokens) / total_seconds, 2) if total_seconds else 0.0
    )
    return stats


def last_generation_meta() -> dict:
    return dict(_LAST_GENERATION_META_CONTEXT.get() or {})


def last_generation_stats() -> dict:
    """Return usage for the current request, safe under concurrent workers."""
    return dict(_LAST_GENERATION_STATS.get() or {})


def reset_last_generation_call() -> None:
    _LAST_GENERATION_META_CONTEXT.set({})
    _LAST_GENERATION_STATS.set({})


def transfer_last_generation_call(source: contextvars.Context) -> None:
    """Copy per-request telemetry from a worker context to its caller.

    ContextVar mutations intentionally stay inside the copied queue context.
    The business result crosses that boundary through the Future-like queue
    item, so its small telemetry snapshot must cross explicitly as well.
    """
    _LAST_GENERATION_META_CONTEXT.set(
        dict(source.get(_LAST_GENERATION_META_CONTEXT, {}) or {})
    )
    _LAST_GENERATION_STATS.set(
        dict(source.get(_LAST_GENERATION_STATS, {}) or {})
    )


def _record_generation_meta(returned_chars: int, valid_json_chars: int = 0) -> None:
    meta = {
        "returned_chars": int(returned_chars or 0),
        "valid_json_chars": int(valid_json_chars or 0),
    }
    _LAST_GENERATION_META_CONTEXT.set(meta)
    with _GENERATION_STATS_LOCK:
        _LAST_GENERATION_META.update(meta)


def _record_openai_generation_stats(payload: dict, elapsed_seconds: float) -> None:
    """Collect portable usage counters from an OpenAI-compatible response."""
    usage = payload.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    _LAST_GENERATION_STATS.set({
        "chat_calls": 1,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "prompt_eval_seconds": 0.0,
        "output_eval_seconds": 0.0,
        "total_seconds": float(elapsed_seconds or 0.0),
    })
    with _GENERATION_STATS_LOCK:
        _GENERATION_STATS["chat_calls"] += 1
        _GENERATION_STATS["prompt_tokens"] += prompt_tokens
        _GENERATION_STATS["output_tokens"] += output_tokens
        _GENERATION_STATS["total_seconds"] = round(
            float(_GENERATION_STATS["total_seconds"]) + float(elapsed_seconds or 0.0), 3
        )
