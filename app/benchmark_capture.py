"""Best-effort, opt-in raw request capture for standalone benchmark building.

This module is intentionally a side channel: capture is disabled by default,
uses a bounded in-process queue, and silently drops records on any I/O error.
It must never delay, fail, or alter a production model request.
"""
from __future__ import annotations

import atexit
import contextlib
import contextvars
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.token_monitor import current_context

_call_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("benchmark_call_context", default={})
_captured_call_ids: set[str] = set()
_captured_call_ids_lock = threading.Lock()


@contextlib.contextmanager
def benchmark_call_context(*, call_id: str, logical_call_id: str, agent: str, attempt: int):
    """Associate one raw gateway request with its logical Agent call and retry."""
    token = _call_context.set({
        "call_id": str(call_id),
        "logical_call_id": str(logical_call_id),
        "agent": str(agent),
        "attempt": int(attempt),
    })
    try:
        yield
    finally:
        _call_context.reset(token)


def current_benchmark_call_context() -> dict[str, Any]:
    return dict(_call_context.get() or {})


class BenchmarkCaptureSink:
    """Append raw benchmark events to JSONL from a bounded background worker."""

    def __init__(self, output_dir: Path, max_queue_size: int = 256) -> None:
        self.output_dir = Path(output_dir)
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max(1, int(max_queue_size)))
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, event: dict[str, Any]) -> bool:
        if self._closed:
            return False
        try:
            self._ensure_worker()
            self.queue.put_nowait(event)
            return True
        except (OSError, RuntimeError, queue.Full):
            return False

    def close(self, timeout: float = 1.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker
        if worker and worker.is_alive():
            try:
                self.queue.put_nowait(None)
            except queue.Full:
                return
            worker.join(timeout=max(0.0, timeout))

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                name="benchmark-capture",
                daemon=True,
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            event = self.queue.get()
            try:
                if event is None:
                    return
                self.output_dir.mkdir(parents=True, exist_ok=True)
                target = self.output_dir / f"capture-{time.strftime('%Y%m%d')}.jsonl"
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                    handle.write("\n")
            except Exception:
                # This sink is intentionally non-fatal.  Benchmark capture is
                # never allowed to change report generation behaviour.
                pass
            finally:
                self.queue.task_done()


_sink_lock = threading.Lock()
_sink: BenchmarkCaptureSink | None = None
_resource_sampler: "CaptureResourceSampler | None" = None


class CaptureResourceSampler:
    """Sample test-session resources without participating in model calls."""

    def __init__(self, sink: BenchmarkCaptureSink, interval_seconds: float, metrics_url: str = "",
                 accelerator_probe_command: str = "", task_id: str = "") -> None:
        self.sink = sink
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.metrics_url = str(metrics_url or "")
        self.accelerator_probe_command = str(accelerator_probe_command or "")
        self.task_id = str(task_id or "")
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="benchmark-capture-resource", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self, timeout: float = 1.5) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(0.0, timeout))

    def _run(self) -> None:
        # Importing the portable probe here avoids adding CPU/GPU sampling work
        # to application startup or to disabled Capture paths.
        from benchmark.runner import _resource_sample, fetch_prometheus_snapshot

        while not self.stop_event.is_set():
            event = {
                "schema_version": "1.0",
                "event_type": "resource_sample",
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "context": {"task_id": self.task_id},
                "resource": _resource_sample(self.accelerator_probe_command),
                "server_metrics": _select_server_metrics(fetch_prometheus_snapshot(self.metrics_url)),
            }
            self.sink.submit(event)
            self.stop_event.wait(self.interval_seconds)


def _task_is_allowed(task_id: str) -> bool:
    if not bool(settings.benchmark_capture_enabled):
        return False
    if bool(settings.benchmark_capture_all):
        return True
    allowed = {
        item.strip() for item in str(settings.benchmark_capture_task_ids or "").split(",")
        if item.strip()
    }
    return bool(task_id and task_id in allowed)


def _capture_sink() -> BenchmarkCaptureSink:
    global _sink
    with _sink_lock:
        if _sink is None:
            _sink = BenchmarkCaptureSink(
                Path(settings.benchmark_capture_dir),
                max_queue_size=int(settings.benchmark_capture_queue_size),
            )
        return _sink


_SERVER_METRIC_PREFIXES = (
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
    "vllm:num_requests_",
    "vllm:gpu_cache_usage_perc",
    "vllm:kv_cache_usage_perc",
    "vllm:prefix_cache_",
    "vllm:time_to_first_token_seconds",
    "vllm:time_per_output_token_seconds",
    "vllm:e2e_request_latency_seconds",
    "vllm:request_prompt_tokens",
    "vllm:request_generation_tokens",
    "process_resident_memory_bytes",
    "process_cpu_seconds_total",
)


def _select_server_metrics(metrics: dict[str, float]) -> dict[str, float]:
    """Retain only counters needed by the portable benchmark."""
    return {
        key: value
        for key, value in metrics.items()
        if key.startswith(_SERVER_METRIC_PREFIXES)
    }


def start_task_capture(task_id: str) -> bool:
    """Start resource sampling for one allowed queued workflow task."""
    global _resource_sampler
    if not _task_is_allowed(task_id):
        return False
    sink = _capture_sink()
    with _sink_lock:
        if _resource_sampler is not None:
            return False
        _resource_sampler = CaptureResourceSampler(
            sink,
            interval_seconds=float(settings.benchmark_capture_sample_interval_seconds),
            metrics_url=str(settings.benchmark_capture_metrics_url or ""),
            accelerator_probe_command=str(settings.benchmark_capture_accelerator_probe_command or ""),
            task_id=task_id,
        )
        _resource_sampler.start()
    return True


def stop_task_capture(task_id: str) -> None:
    """Stop sampling at the workflow boundary; never record idle service time."""
    global _resource_sampler
    with _sink_lock:
        sampler = _resource_sampler
        if sampler is None or sampler.task_id != str(task_id or ""):
            return
        _resource_sampler = None
    sampler.close()


def capture_llm_call(*, backend: str, endpoint: str, request: dict[str, Any],
                     response: dict[str, Any] | None = None, content: str = "",
                     elapsed_seconds: float = 0.0, error: str = "") -> None:
    """Queue one raw call capture only when the explicit benchmark guard allows it."""
    context = current_context()
    task_id = str(context.get("task_id") or "")
    if not _task_is_allowed(task_id):
        return
    usage = (response or {}).get("usage") or {}
    choices = (response or {}).get("choices") or []
    finish_reason = str((choices[0] or {}).get("finish_reason") or "") if choices else ""
    correlation = dict(_call_context.get() or {})
    call_id = str(correlation.get("call_id") or "")
    if call_id:
        with _captured_call_ids_lock:
            _captured_call_ids.add(call_id)
    event = {
        "schema_version": "1.0",
        "event_type": "call",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "context": {
            "task_id": task_id,
            "run_id": str(context.get("run_id") or ""),
            "stage": str(context.get("stage") or ""),
            "report_mode": str(context.get("report_mode") or ""),
            "material_count": int(context.get("material_count") or 0),
        },
        "correlation": correlation,
        "transport": {"backend": str(backend), "endpoint": str(endpoint)},
        "request": request,
        "observed": {
            "usage": usage,
            "content": str(content or ""),
            "finish_reason": finish_reason,
            "latency_seconds": round(float(elapsed_seconds or 0.0), 6),
            "error": str(error or "")[:1000],
        },
    }
    try:
        _capture_sink().submit(event)
    except Exception:
        pass


def capture_enrichment(call_id: str, kind: str, payload: dict[str, Any]) -> None:
    """Append a later metrics/funnel update for an already captured call.

    The raw request returns before Fact persistence and Writer lineage updates.
    Appending immutable enrichment events lets the dataset builder join the full
    lifecycle without adding a database dependency to the future runner.
    """
    if not bool(settings.benchmark_capture_enabled) or not call_id:
        return
    with _captured_call_ids_lock:
        allowed = str(call_id) in _captured_call_ids
    if not allowed:
        return
    event = {
        "schema_version": "1.0",
        "event_type": "enrichment",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "call_id": str(call_id),
        "kind": str(kind),
        "payload": payload,
    }
    try:
        _capture_sink().submit(event)
    except Exception:
        pass


def close_capture_sink() -> None:
    global _sink, _resource_sampler
    with _sink_lock:
        sink, _sink = _sink, None
        sampler, _resource_sampler = _resource_sampler, None
    if sampler:
        sampler.close()
    if sink:
        sink.close()
    with _captured_call_ids_lock:
        _captured_call_ids.clear()


atexit.register(close_capture_sink)
