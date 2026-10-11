"""Fast local estimates and bounded vLLM request verification, without GPU work."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import json
import logging
import math
import re
import threading
import time

import httpx


@dataclass
class Observation:
    window: int | None = None
    retry_at: float = 0.0
    checked_at: float = 0.0
    ratios: deque = field(default_factory=lambda: deque(maxlen=32))
    error: str = ""
    count_lock: threading.Lock = field(default_factory=threading.Lock)


_lock = threading.RLock()
_profiles: OrderedDict[str, Observation] = OrderedDict()
_counts: OrderedDict[str, tuple[float, int]] = OrderedDict()
_logger = logging.getLogger(__name__)


def raw_estimate(text: str) -> int:
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return max(1, cjk + math.ceil((len(text) - cjk) / 3))


def _profile(connection):
    with _lock:
        key = connection.identity
        if key not in _profiles:
            _profiles[key] = Observation()
        _profiles.move_to_end(key)
        while len(_profiles) > 16:
            _profiles.popitem(last=False)
        return _profiles[key]


def estimate(text: str, connection) -> int:
    profile = _profile(connection)
    with _lock:
        factor = max([1.0, *profile.ratios])
    return math.ceil(raw_estimate(text) * factor)


def _count(connection, payload: dict) -> int:
    # Collapse concurrent probes/counts for one service, not inference requests.
    with _profile(connection).count_lock:
        return _count_locked(connection, payload)


def _count_locked(connection, payload: dict) -> int:
    body = {key: payload[key] for key in (
        "model", "messages", "tools", "chat_template", "chat_template_kwargs",
        "continue_final_message", "media_io_kwargs", "mm_processor_kwargs",
    ) if key in payload}
    body["add_generation_prompt"] = not body.get("continue_final_message", False)
    body["return_token_strs"] = False
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False)
    key = hashlib.sha256((connection.identity + encoded).encode()).hexdigest()
    profile = _profile(connection)
    now = time.monotonic()
    with _lock:
        cached = _counts.get(key)
        if cached and now - cached[0] < 120:
            _counts.move_to_end(key)
            return cached[1]
        if now < profile.retry_at:
            raise RuntimeError("VLLM_TOKEN_COUNT_COOLDOWN")
    try:
        response = httpx.post(connection.base_url.rstrip("/").removesuffix("/v1") + "/tokenize",
                              headers=connection.headers(), json=body, timeout=3, follow_redirects=False)
        response.raise_for_status()
        result = response.json()
        count = result["count"]
        window = result.get("max_model_len")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Invalid vLLM token count")
        if window is not None and (isinstance(window, bool) or not isinstance(window, int) or window <= 0):
            raise ValueError("Invalid vLLM model window")
    except Exception as exc:
        with _lock:
            profile.retry_at = now + 30
            profile.error = type(exc).__name__
        raise
    with _lock:
        profile.window = window or profile.window
        profile.checked_at = now
        profile.retry_at = 0
        profile.error = ""
        _counts[key] = (now, count)
        while len(_counts) > 128:
            _counts.popitem(last=False)
    return count


def window(connection) -> int:
    profile = _profile(connection)
    if connection.service_kind == "vllm" and time.monotonic() - profile.checked_at > 60:
        try:
            _count(connection, {"model": connection.model_name, "messages": [{"role": "user", "content": ""}],
                                **connection.extra_body(False)})
        except Exception:
            pass  # Configured fallback remains explicit in status and request audit.
    configured = connection.context_tokens
    if profile.window is not None:
        return min(configured, profile.window) if configured else profile.window
    if configured:
        return configured
    raise ValueError("MODEL_CAPACITY_UNAVAILABLE")


def verify_request(connection, payload: dict, safety_tokens: int = 0) -> dict:
    profile = _profile(connection)
    raw = raw_estimate(json.dumps(payload.get("messages", []), ensure_ascii=False))
    tools = payload.get("tools") or []
    raw += raw_estimate(json.dumps(tools, ensure_ascii=False)) if tools else 0
    limit = window(connection)
    output = int(payload.get(connection.output_parameter) or 0)
    if output <= 0 or output > connection.output_tokens:
        raise ValueError("MODEL_INPUT_OVER_BUDGET")
    estimated = estimate(json.dumps(payload.get("messages", []), ensure_ascii=False), connection)
    estimated += estimate(json.dumps(tools, ensure_ascii=False), connection) if tools else 0
    method, error = "estimated", profile.error
    count = estimated
    # Only final requests are remotely counted. No network I/O during packing.
    if connection.service_kind == "vllm" and (len(profile.ratios) < 8 or tools or estimated + output >= limit * 0.75):
        try:
            count = _count(connection, payload)
            method = "vllm_tokenize"
            error = ""
            with _lock:
                profile.ratios.append(count / max(1, raw))
        except Exception as exc:
            error = type(exc).__name__
    # A final count may reveal a changed deployment window after the probe.
    if profile.window is not None:
        limit = min(connection.context_tokens, profile.window) if connection.context_tokens else profile.window
    if count + output + safety_tokens > limit:
        _logger.warning("Model capacity exceeded: counter=%s input=%s output=%s safety=%s window=%s",
                        method, count, output, safety_tokens, limit)
        raise ValueError("MODEL_INPUT_OVER_BUDGET")
    if error:
        _logger.warning("vLLM token counting degraded to estimate: reason=%s window=%s input=%s",
                        error, limit, count)
    return {"request_count_method": method, "request_input_tokens": count,
            "effective_context_tokens": limit, "requested_output_tokens": output,
            "count_degraded": bool(error), "count_error": error}


def observe_usage(connection, payload: dict, usage: dict) -> None:
    incoming = usage.get("prompt_tokens")
    if isinstance(incoming, bool) or not isinstance(incoming, int) or incoming <= 0:
        return
    raw = raw_estimate(json.dumps(payload.get("messages", []), ensure_ascii=False))
    if payload.get("tools"):
        raw += raw_estimate(json.dumps(payload["tools"], ensure_ascii=False))
    with _lock:
        _profile(connection).ratios.append(incoming / max(1, raw))


def status(connection) -> dict:
    profile = _profile(connection)
    try:
        effective = window(connection)
    except ValueError:
        effective = None
    return {"effective_context_tokens": effective, "server_context_tokens": profile.window,
            "token_count_available": profile.window is not None and not profile.error,
            "token_count_error": profile.error, "calibration_samples": len(profile.ratios)}


def reset():
    with _lock:
        _profiles.clear()
        _counts.clear()
