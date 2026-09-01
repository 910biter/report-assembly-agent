"""Independent OpenAI-compatible benchmark runner producing JSON only."""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import importlib.metadata
import os
import platform
import shlex
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx


def read_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid dataset JSONL: {path}:{line_number}") from exc
            if not isinstance(case, dict) or not case.get("case_id"):
                raise ValueError(f"invalid benchmark case: {path}:{line_number}")
            cases.append(case)
    return cases


def percentile(values: Iterable[float], pct: int) -> float | None:
    items = sorted(float(item) for item in values if item is not None)
    if not items:
        return None
    if len(items) == 1:
        return round(items[0], 6)
    index = min(len(items) - 1, max(0, round((pct / 100) * (len(items) - 1))))
    return round(items[index], 6)


def _safe_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def validate_output(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Validate structural contracts without judging content semantics."""
    validators = dict(case.get("validators") or {})
    expected_success = bool(validators.get("expect_success", True))
    actual_success = bool(result.get("success"))
    checks: dict[str, bool | None] = {
        "expected_success": actual_success if expected_success else not actual_success,
    }
    if expected_success and actual_success:
        parsed = _safe_json(str(result.get("content") or ""))
        if validators.get("expect_json_object"):
            checks["json_object"] = parsed is not None
            fields = set(parsed or {})
            checks["required_top_level_fields"] = all(
                field in fields for field in validators.get("required_top_level_fields") or []
            )
        output_tokens = result.get("usage", {}).get("completion_tokens")
        output_range = dict((case.get("shape") or {}).get("reference_output_tokens") or {})
        if output_tokens is None:
            checks["output_token_range"] = None
        else:
            checks["output_token_range"] = (
                int(output_range.get("minimum") or 0) <= int(output_tokens) <= int(output_range.get("maximum") or 0)
            )
    passed = all(value is not False for value in checks.values())
    return {"passed": passed, "checks": checks}


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        value = json.loads(data)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _stream_request(client: httpx.Client, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    first_token_at: float | None = None
    content_parts: list[str] = []
    response_payload: dict[str, Any] = {}
    usage: dict[str, Any] = {}
    finish_reason = ""
    chunk_arrivals: list[float] = []
    with client.stream("POST", url, headers=headers, json=payload) as response:
        headers_at = time.perf_counter()
        response.raise_for_status()
        for line in response.iter_lines():
            event = _parse_sse_line(line)
            if not event:
                continue
            response_payload = event
            choices = event.get("choices") or []
            if choices:
                choice = choices[0] or {}
                delta = choice.get("delta") or {}
                fragment = str(delta.get("content") or "")
                if fragment:
                    chunk_arrivals.append(time.perf_counter())
                    if first_token_at is None:
                        first_token_at = chunk_arrivals[-1]
                    content_parts.append(fragment)
                finish_reason = str(choice.get("finish_reason") or finish_reason)
            if event.get("usage"):
                usage = dict(event.get("usage") or {})
    finished = time.perf_counter()
    return {
        "success": True,
        "content": "".join(content_parts),
        "usage": usage,
        "finish_reason": finish_reason,
        "latency_seconds": round(finished - started, 6),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ttft_seconds": round((first_token_at or finished) - started, 6),
        "time_to_headers_seconds": round(headers_at - started, 6),
        "first_content_after_headers_seconds": round((first_token_at or finished) - headers_at, 6),
        "stream_chunk_count": len(chunk_arrivals),
        "stream_chunk_interarrival_seconds": [
            round(chunk_arrivals[index] - chunk_arrivals[index - 1], 6)
            for index in range(1, len(chunk_arrivals))
        ],
        "transport": "stream",
        "raw_response": response_payload,
    }


def _non_stream_request(client: httpx.Client, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    response = client.post(url, headers=headers, json=payload)
    finished = time.perf_counter()
    response.raise_for_status()
    body = response.json()
    choices = body.get("choices") or []
    choice = choices[0] if choices else {}
    return {
        "success": True,
        "content": str((choice.get("message") or {}).get("content") or ""),
        "usage": dict(body.get("usage") or {}),
        "finish_reason": str(choice.get("finish_reason") or ""),
        "latency_seconds": round(finished - started, 6),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "ttft_seconds": None,
        "transport": "non_stream",
        "raw_response": body,
    }


def _endpoint_url(endpoint: str) -> str:
    return endpoint.rstrip("/") + "/chat/completions"


def execute_case(case: dict[str, Any], endpoint: str, model_override: str = "", api_key: str = "",
                 stream: bool = True, timeout_seconds: float = 900.0,
                 preserve_priority: bool = False, client: httpx.Client | None = None,
                 submitted_at: float | None = None, metrics_url: str = "") -> dict[str, Any]:
    generation = dict((case.get("request") or {}).get("generation") or {})
    model = model_override or str(generation.pop("model", ""))
    if not model:
        raise ValueError(f"case {case.get('case_id')} has no model and no --model override")
    if not preserve_priority:
        generation.pop("priority", None)
    payload = {
        "model": model,
        "messages": list((case.get("request") or {}).get("messages") or []),
        **generation,
    }
    payload["stream"] = bool(stream)
    if stream:
        payload["stream_options"] = {"include_usage": True}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    server_metrics_before = fetch_prometheus_snapshot(metrics_url)
    try:
        request_started = time.perf_counter()
        client_context = contextlib.nullcontext(client) if client is not None else httpx.Client(timeout=timeout_seconds)
        with client_context as request_client:
            result = (
                _stream_request(request_client, _endpoint_url(endpoint), headers, payload)
                if stream else _non_stream_request(request_client, _endpoint_url(endpoint), headers, payload)
            )
    except Exception as exc:
        result = {
            "success": False,
            "content": "",
            "usage": {},
            "finish_reason": "",
            "latency_seconds": 0.0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ttft_seconds": None,
            "transport": "stream" if stream else "non_stream",
            "error": f"{type(exc).__name__}:{exc}",
        }
    server_metrics_after = fetch_prometheus_snapshot(metrics_url)
    server_delta = metric_delta(server_metrics_before, server_metrics_after)
    usage = result.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    latency = float(result.get("latency_seconds") or 0.0)
    ttft = result.get("ttft_seconds")
    decode_seconds = latency - float(ttft or 0.0) if ttft is not None else None
    chunk_intervals = list(result.get("stream_chunk_interarrival_seconds") or [])
    prompt_details = dict(usage.get("prompt_tokens_details") or {})
    result["metrics"] = {
        "reported_prompt_tokens": int(prompt_tokens) if prompt_tokens is not None else None,
        "reported_output_tokens": int(output_tokens) if output_tokens is not None else None,
        "ttft_seconds": ttft,
        "time_to_headers_seconds": result.get("time_to_headers_seconds"),
        "first_content_after_headers_seconds": result.get("first_content_after_headers_seconds"),
        "client_queue_wait_seconds": (
            round(max(0.0, request_started - submitted_at), 6)
            if submitted_at is not None else 0.0
        ),
        "decode_seconds_client": round(decode_seconds, 6) if decode_seconds is not None else None,
        "tpot_seconds": (
            round(decode_seconds / max(int(output_tokens or 0) - 1, 1), 8)
            if output_tokens is not None and decode_seconds is not None and decode_seconds > 0 else None
        ),
        "stream_chunk_count": int(result.get("stream_chunk_count") or 0),
        "stream_chunk_interarrival_seconds_p50": percentile(chunk_intervals, 50),
        "stream_chunk_interarrival_seconds_p95": percentile(chunk_intervals, 95),
        "stream_chunk_interarrival_seconds_max": round(max(chunk_intervals), 6) if chunk_intervals else None,
        "cached_prompt_tokens": int(prompt_details.get("cached_tokens") or 0),
        "input_output_ratio": (
            round(int(prompt_tokens) / max(int(output_tokens), 1), 6)
            if prompt_tokens is not None and output_tokens is not None else None
        ),
        # This is deliberately called a proxy: TTFT includes network and queue
        # time, so a client cannot claim it is model-internal prefill timing.
        "prefill_proxy_tokens_per_second": (
            round(int(prompt_tokens) / max(float(ttft), 1e-6), 4)
            if prompt_tokens is not None and ttft is not None else None
        ),
        "decode_tokens_per_second": (
            round(int(output_tokens) / max(decode_seconds, 1e-6), 4)
            if output_tokens is not None and decode_seconds is not None and decode_seconds > 0 else None
        ),
        "end_to_end_tokens_per_second": (
            round((int(prompt_tokens) + int(output_tokens)) / max(latency, 1e-6), 4)
            if prompt_tokens is not None and output_tokens is not None and latency > 0 else None
        ),
    }
    result["server_internal_metrics"] = _server_inference_metrics(server_delta)
    result["server_internal_metrics"]["attribution"] = "exclusive"
    result["validation"] = validate_output(case, result)
    result["reference_baseline"] = dict(case.get("baseline") or {})
    result["reference_shape"] = dict(case.get("shape") or {})
    result["baseline_comparison"] = _compare_baseline(case, result)
    result["case_id"] = str(case.get("case_id") or "")
    result["stage"] = str(case.get("stage") or "")
    result["workload"] = str(case.get("workload") or "")
    result["load_class"] = str(case.get("load_class") or "")
    return result


def _compare_baseline(case: dict[str, Any], result: dict[str, Any]) -> dict[str, float | None]:
    """Keep comparison arithmetic in the JSON result, not in a report layer."""
    baseline = dict(case.get("baseline") or {})
    application_call = dict(baseline.get("application_call") or {})
    timing = dict(application_call.get("model_timing") or {})
    baseline_latency = float(application_call.get("latency_ms") or 0) / 1000
    current_latency = float(result.get("latency_seconds") or 0)
    baseline_output = int(application_call.get("output_tokens") or 0)
    baseline_decode_seconds = float(timing.get("output_eval_seconds") or 0)
    baseline_decode = baseline_output / baseline_decode_seconds if baseline_decode_seconds > 0 else None
    current_decode = result.get("metrics", {}).get("decode_tokens_per_second")
    return {
        "baseline_latency_seconds": round(baseline_latency, 6) if baseline_latency else None,
        "current_to_baseline_latency_ratio": (
            round(current_latency / baseline_latency, 6) if baseline_latency > 0 and current_latency > 0 else None
        ),
        "baseline_decode_tokens_per_second": round(baseline_decode, 6) if baseline_decode is not None else None,
        "current_to_baseline_decode_ratio": (
            round(float(current_decode) / baseline_decode, 6)
            if baseline_decode and current_decode is not None else None
        ),
    }


class ResourceSampler:
    """Best-effort host/GPU samples; absence of a probe is recorded, not fatal."""

    def __init__(self, interval_seconds: float = 0.5, accelerator_probe_command: str = "") -> None:
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.accelerator_probe_command = str(accelerator_probe_command or "")
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="benchmark-resource-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> list[dict[str, Any]]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_seconds * 3)
        return list(self.samples)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(_resource_sample(self.accelerator_probe_command))
            self._stop.wait(self.interval_seconds)


def _resource_sample(accelerator_probe_command: str = "") -> dict[str, Any]:
    sample: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(), "cpu_percent": None, "ram_percent": None,
        "disk_read_bytes": None, "disk_write_bytes": None, "network_bytes_sent": None,
        "network_bytes_received": None, "gpus": [], "accelerator_probe": {},
    }
    try:
        import psutil  # optional in the portable runner
        sample["cpu_percent"] = psutil.cpu_percent(interval=None)
        sample["ram_percent"] = psutil.virtual_memory().percent
        disk = psutil.disk_io_counters()
        network = psutil.net_io_counters()
        if disk:
            sample["disk_read_bytes"] = int(disk.read_bytes)
            sample["disk_write_bytes"] = int(disk.write_bytes)
        if network:
            sample["network_bytes_sent"] = int(network.bytes_sent)
            sample["network_bytes_received"] = int(network.bytes_recv)
    except Exception:
        pass
    if accelerator_probe_command:
        try:
            completed = subprocess.run(
                shlex.split(accelerator_probe_command), capture_output=True, text=True, check=False, timeout=3,
            )
            if completed.returncode == 0:
                value = json.loads(completed.stdout)
                if isinstance(value, dict):
                    sample["accelerator_probe"] = value
        except Exception:
            pass
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu,clocks.sm,clocks.mem",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, check=False, timeout=3,
        )
        if completed.returncode == 0:
            for row in completed.stdout.splitlines():
                values = [value.strip() for value in row.split(",")]
                if len(values) == 9:
                    try:
                        gpu_index = int(values[0])
                        utilization = float(values[2])
                        memory_used = float(values[3])
                        memory_total = float(values[4])
                    except ValueError:
                        continue
                    sample["gpus"].append({
                        "index": gpu_index, "name": values[1], "utilization_percent": utilization,
                        "memory_used_mb": memory_used, "memory_total_mb": memory_total,
                        "power_watts": _optional_float(values[5]), "temperature_celsius": _optional_float(values[6]),
                        "sm_clock_mhz": _optional_float(values[7]), "memory_clock_mhz": _optional_float(values[8]),
                    })
    except Exception:
        pass
    return sample


def _optional_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resource_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    cpu = [sample["cpu_percent"] for sample in samples if sample.get("cpu_percent") is not None]
    ram = [sample["ram_percent"] for sample in samples if sample.get("ram_percent") is not None]
    disk_read = [sample["disk_read_bytes"] for sample in samples if sample.get("disk_read_bytes") is not None]
    disk_write = [sample["disk_write_bytes"] for sample in samples if sample.get("disk_write_bytes") is not None]
    network_sent = [sample["network_bytes_sent"] for sample in samples if sample.get("network_bytes_sent") is not None]
    network_received = [sample["network_bytes_received"] for sample in samples if sample.get("network_bytes_received") is not None]
    gpus: dict[int, dict[str, list[float] | str]] = {}
    for sample in samples:
        for gpu in sample.get("gpus") or []:
            item = gpus.setdefault(int(gpu["index"]), {"name": str(gpu["name"]), "util": [], "memory": [], "total": [], "power": [], "temperature": [], "sm_clock": [], "memory_clock": []})
            item["util"].append(float(gpu["utilization_percent"]))  # type: ignore[index]
            item["memory"].append(float(gpu["memory_used_mb"]))  # type: ignore[index]
            item["total"].append(float(gpu["memory_total_mb"]))  # type: ignore[index]
            for key, sample_key in (("power", "power_watts"), ("temperature", "temperature_celsius"), ("sm_clock", "sm_clock_mhz"), ("memory_clock", "memory_clock_mhz")):
                if gpu.get(sample_key) is not None:
                    item[key].append(float(gpu[sample_key]))  # type: ignore[index]
    return {
        "sample_count": len(samples),
        "cpu_percent_p50": percentile(cpu, 50),
        "cpu_percent_p95": percentile(cpu, 95),
        "ram_percent_p50": percentile(ram, 50),
        "ram_percent_p95": percentile(ram, 95),
        "disk_read_bytes_delta": (max(disk_read) - min(disk_read)) if len(disk_read) >= 2 else None,
        "disk_write_bytes_delta": (max(disk_write) - min(disk_write)) if len(disk_write) >= 2 else None,
        "network_bytes_sent_delta": (max(network_sent) - min(network_sent)) if len(network_sent) >= 2 else None,
        "network_bytes_received_delta": (max(network_received) - min(network_received)) if len(network_received) >= 2 else None,
        "gpus": [
            {
                "index": index,
                "name": values["name"],
                "utilization_percent_p50": percentile(values["util"], 50),  # type: ignore[arg-type]
                "utilization_percent_p95": percentile(values["util"], 95),  # type: ignore[arg-type]
                "memory_used_mb_peak": max(values["memory"], default=0),  # type: ignore[arg-type]
                "memory_total_mb": max(values["total"], default=0),  # type: ignore[arg-type]
                "power_watts_p50": percentile(values["power"], 50),  # type: ignore[arg-type]
                "power_watts_peak": max(values["power"], default=0),  # type: ignore[arg-type]
                "temperature_celsius_peak": max(values["temperature"], default=0),  # type: ignore[arg-type]
                "sm_clock_mhz_p50": percentile(values["sm_clock"], 50),  # type: ignore[arg-type]
                "memory_clock_mhz_p50": percentile(values["memory_clock"], 50),  # type: ignore[arg-type]
            }
            for index, values in sorted(gpus.items())
        ],
    }


def _iso_timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _attach_resource_windows(results: list[dict[str, Any]], samples: list[dict[str, Any]]) -> None:
    """Associate each request with resource samples observed during its wall-time window."""
    timed_samples = [(_iso_timestamp(sample.get("at", "")), sample) for sample in samples]
    for result in results:
        started = _iso_timestamp(result.get("started_at", ""))
        finished = _iso_timestamp(result.get("finished_at", ""))
        window = [sample for at, sample in timed_samples if started <= at <= finished]
        result["resource_window"] = {
            "sample_count": len(window),
            "shared_with_concurrent_requests": False,
            "summary": _resource_summary(window),
        }


def _attach_concurrency(results: list[dict[str, Any]]) -> None:
    intervals = [
        (_iso_timestamp(item.get("started_at", "")), _iso_timestamp(item.get("finished_at", "")))
        for item in results
    ]
    for index, result in enumerate(results):
        started, finished = intervals[index]
        overlap = sum(1 for other_started, other_finished in intervals
                      if other_started <= finished and other_finished >= started)
        result["overlapping_request_count"] = max(0, overlap - 1)
        result["resource_window"]["shared_with_concurrent_requests"] = overlap > 1
        if result.get("server_internal_metrics"):
            result["server_internal_metrics"]["attribution"] = "shared" if overlap > 1 else "exclusive"


def _environment() -> dict[str, Any]:
    packages = {}
    for package in ("vllm", "torch", "httpx", "psutil"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "hostname": platform.node(),
        "pid": os.getpid(),
        "packages": packages,
        "serving_envelope": {
            "model_path_or_id": os.getenv("MODEL_PATH_OR_ID", ""),
            "served_model_name": os.getenv("SERVED_MODEL_NAME", ""),
            "max_model_len": _env_number("MAX_MODEL_LEN"),
            "max_num_seqs": _env_number("MAX_NUM_SEQS"),
            "max_num_batched_tokens": _env_number("MAX_NUM_BATCHED_TOKENS"),
            "gpu_memory_utilization": _env_number("GPU_MEMORY_UTILIZATION", floating=True),
            "kv_cache_dtype": os.getenv("KV_CACHE_DTYPE", "") or "auto",
            "calculate_kv_scales": os.getenv("CALCULATE_KV_SCALES", "0") == "1",
            "cpu_offload_gb": _env_number("CPU_OFFLOAD_GB", floating=True),
            "prefix_caching": True,
            "chunked_prefill": True,
            "scheduling_policy": "priority",
        },
    }


def _env_number(name: str, floating: bool = False) -> int | float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw) if floating else int(raw)
    except ValueError:
        return None


def fetch_prometheus_snapshot(url: str, timeout_seconds: float = 3.0) -> dict[str, float]:
    """Read numeric Prometheus samples without coupling to a vLLM metric version.

    The raw metric names are retained in the result JSON. This lets later
    analysis use the server's native prompt/generation counters when available
    instead of mistaking client TTFT for internal prefill time.
    """
    if not url:
        return {}
    try:
        response = httpx.get(url, timeout=timeout_seconds)
        response.raise_for_status()
    except Exception:
        return {}
    values: dict[str, float] = {}
    for line in response.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name, raw_value = line.rsplit(None, 1)
            values[name] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return values


def metric_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {
        key: round(float(after[key]) - float(before[key]), 6)
        for key in after.keys() & before.keys()
        if float(after[key]) >= float(before[key])
    }


def _metric_total(metrics: dict[str, float], name: str) -> float:
    return sum(
        float(value)
        for key, value in metrics.items()
        if key == name or key.startswith(name + "{")
    )


def _server_inference_metrics(delta: dict[str, float]) -> dict[str, float | int | None]:
    """Derive model-internal timings from OpenAI server Prometheus counters."""
    prompt_tokens = _metric_total(delta, "vllm:prompt_tokens_total")
    output_tokens = _metric_total(delta, "vllm:generation_tokens_total")
    computed_prompt_tokens = _metric_total(delta, "vllm:request_prefill_kv_computed_tokens_sum")
    prefill_seconds = _metric_total(delta, "vllm:request_prefill_time_seconds_sum")
    decode_seconds = _metric_total(delta, "vllm:request_decode_time_seconds_sum")
    ttft_sum = _metric_total(delta, "vllm:time_to_first_token_seconds_sum")
    ttft_count = _metric_total(delta, "vllm:time_to_first_token_seconds_count")
    cache_queries = _metric_total(delta, "vllm:prefix_cache_queries_total")
    cache_hits = _metric_total(delta, "vllm:prefix_cache_hits_total")
    return {
        "reported_request_count": int(round(ttft_count)),
        "prompt_tokens": int(round(prompt_tokens)),
        "computed_prompt_tokens": int(round(computed_prompt_tokens)),
        "output_tokens": int(round(output_tokens)),
        "prefill_seconds": round(prefill_seconds, 6) if prefill_seconds > 0 else None,
        "decode_seconds": round(decode_seconds, 6) if decode_seconds > 0 else None,
        "ttft_seconds_average": round(ttft_sum / ttft_count, 6) if ttft_count > 0 else None,
        "prefill_tokens_per_second": (
            round(computed_prompt_tokens / prefill_seconds, 4)
            if computed_prompt_tokens > 0 and prefill_seconds > 0 else None
        ),
        "decode_tokens_per_second": (
            round(output_tokens / decode_seconds, 4)
            if output_tokens > 0 and decode_seconds > 0 else None
        ),
        "prefix_cache_hit_rate": round(cache_hits / cache_queries, 6) if cache_queries > 0 else None,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        groups[f"{result.get('stage')}:{result.get('workload')}"] .append(result)
    summary: dict[str, Any] = {}
    for key, items in sorted(groups.items()):
        latency = [float(item.get("latency_seconds") or 0.0) for item in items if item.get("success")]
        ttft = [float(item["metrics"]["ttft_seconds"]) for item in items if item.get("metrics", {}).get("ttft_seconds") is not None]
        decode = [float(item["metrics"]["decode_tokens_per_second"]) for item in items if item.get("metrics", {}).get("decode_tokens_per_second") is not None]
        server_prefill = [float(item["server_internal_metrics"]["prefill_tokens_per_second"]) for item in items if item.get("server_internal_metrics", {}).get("prefill_tokens_per_second") is not None]
        server_decode = [float(item["server_internal_metrics"]["decode_tokens_per_second"]) for item in items if item.get("server_internal_metrics", {}).get("decode_tokens_per_second") is not None]
        server_ttft = [float(item["server_internal_metrics"]["ttft_seconds_average"]) for item in items if item.get("server_internal_metrics", {}).get("ttft_seconds_average") is not None]
        prefill_proxy = [float(item["metrics"]["prefill_proxy_tokens_per_second"]) for item in items if item.get("metrics", {}).get("prefill_proxy_tokens_per_second") is not None]
        tpot = [float(item["metrics"]["tpot_seconds"]) for item in items if item.get("metrics", {}).get("tpot_seconds") is not None]
        queue_wait = [float(item["metrics"]["client_queue_wait_seconds"]) for item in items if item.get("metrics", {}).get("client_queue_wait_seconds") is not None]
        input_tokens = [float(item["metrics"]["reported_prompt_tokens"]) for item in items if item.get("metrics", {}).get("reported_prompt_tokens") is not None]
        output_tokens = [float(item["metrics"]["reported_output_tokens"]) for item in items if item.get("metrics", {}).get("reported_output_tokens") is not None]
        ratios = [float(item["metrics"]["input_output_ratio"]) for item in items if item.get("metrics", {}).get("input_output_ratio") is not None]
        summary[key] = {
            "calls": len(items),
            "success_rate": round(sum(1 for item in items if item.get("success")) / max(len(items), 1), 4),
            "validation_pass_rate": round(sum(1 for item in items if item.get("validation", {}).get("passed")) / max(len(items), 1), 4),
            "latency_seconds_p50": percentile(latency, 50),
            "latency_seconds_p95": percentile(latency, 95),
            "ttft_seconds_p50": percentile(ttft, 50),
            "ttft_seconds_p95": percentile(ttft, 95),
            "decode_tokens_per_second_p50": percentile(decode, 50),
            "decode_tokens_per_second_p95": percentile(decode, 95),
            "server_prefill_tokens_per_second_p50": percentile(server_prefill, 50),
            "server_prefill_tokens_per_second_p95": percentile(server_prefill, 95),
            "server_decode_tokens_per_second_p50": percentile(server_decode, 50),
            "server_decode_tokens_per_second_p95": percentile(server_decode, 95),
            "server_ttft_seconds_p50": percentile(server_ttft, 50),
            "server_ttft_seconds_p95": percentile(server_ttft, 95),
            "prefill_proxy_tokens_per_second_p50": percentile(prefill_proxy, 50),
            "prefill_proxy_tokens_per_second_p95": percentile(prefill_proxy, 95),
            "tpot_seconds_p50": percentile(tpot, 50),
            "tpot_seconds_p95": percentile(tpot, 95),
            "client_queue_wait_seconds_p50": percentile(queue_wait, 50),
            "client_queue_wait_seconds_p95": percentile(queue_wait, 95),
            "input_tokens_p50": percentile(input_tokens, 50),
            "input_tokens_p95": percentile(input_tokens, 95),
            "input_tokens_max": max(input_tokens, default=None),
            "output_tokens_p50": percentile(output_tokens, 50),
            "output_tokens_p95": percentile(output_tokens, 95),
            "output_tokens_max": max(output_tokens, default=None),
            "input_output_ratio_p50": percentile(ratios, 50),
            "input_output_ratio_p95": percentile(ratios, 95),
        }
    return summary


def run_dataset(cases: list[dict[str, Any]], endpoint: str, model_override: str, api_key: str,
                concurrency: int, repeat: int, stream: bool, timeout_seconds: float,
                sample_interval_seconds: float, preserve_priority: bool,
                metrics_url: str = "", accelerator_probe_command: str = "") -> dict[str, Any]:
    sampler = ResourceSampler(sample_interval_seconds, accelerator_probe_command)
    server_metrics_before = fetch_prometheus_snapshot(metrics_url)
    sampler.start()
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    jobs = [(index, case) for index, case in enumerate(
        [case for _ in range(max(1, repeat)) for case in cases], start=1
    )]
    results: list[dict[str, Any]] = []
    try:
        limits = httpx.Limits(max_connections=max(1, concurrency), max_keepalive_connections=max(1, concurrency))
        with httpx.Client(timeout=timeout_seconds, limits=limits) as shared_client, concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            submitted_at = time.perf_counter()
            futures = [
                executor.submit(execute_case, case, endpoint, model_override, api_key, stream,
                                timeout_seconds, preserve_priority, shared_client, submitted_at, metrics_url)
                for _index, case in jobs
            ]
            future_index = {future: jobs[index][0] for index, future in enumerate(futures)}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                result["job_index"] = future_index[future]
                results.append(result)
    finally:
        samples = sampler.stop()
    server_metrics_after = fetch_prometheus_snapshot(metrics_url)
    elapsed = time.perf_counter() - started
    _attach_resource_windows(results, samples)
    _attach_concurrency(results)
    results.sort(key=lambda item: (item.get("case_id", ""), item.get("latency_seconds", 0.0)))
    return {
        "schema_version": "1.2",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "environment": _environment(),
        "run_config": {
            "endpoint": endpoint,
            "model_override": model_override,
            "concurrency": concurrency,
            "repeat": repeat,
            "stream": stream,
            "timeout_seconds": timeout_seconds,
            "prefill_metric_note": "prefill_proxy_tokens_per_second uses input_tokens/TTFT and includes queue/network time; it is not model-internal prefill timing.",
            "metrics_url": metrics_url,
            "accelerator_probe_command_configured": bool(accelerator_probe_command),
        },
        "elapsed_seconds": round(elapsed, 6),
        "results": results,
        "summary": summarize(results),
        "resources": {"summary": _resource_summary(samples), "samples": samples},
        "server_metrics": {
            "before": server_metrics_before,
            "after": server_metrics_after,
            "delta": metric_delta(server_metrics_before, server_metrics_after),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay standalone JSON benchmark cases")
    parser.add_argument("--dataset", required=True, help="Path to stage-replay.jsonl")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1"),
        help="OpenAI-compatible /v1 endpoint (default: OPENAI_BASE_URL or localhost:8000/v1)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", ""),
        help="Override captured model id (default: OPENAI_MODEL)",
    )
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=0.5)
    parser.add_argument("--metrics-url", default="", help="Optional Prometheus endpoint; raw before/after counters are saved in JSON")
    parser.add_argument("--accelerator-probe-command", default="", help="Optional local command returning one JSON object for NPU/other accelerator telemetry")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming; TTFT and decode speed will be unavailable")
    parser.add_argument("--preserve-priority", action="store_true", help="Pass captured vLLM priority field through")
    parser.add_argument("--output", required=True, help="Result JSON path")
    args = parser.parse_args(argv)
    cases = read_cases(Path(args.dataset))
    # Warmup never contributes to results; it only removes model-load variance.
    for case in cases[:max(0, args.warmup)]:
        execute_case(case, args.endpoint, args.model, args.api_key, not args.no_stream, args.timeout_seconds, args.preserve_priority)
    payload = run_dataset(
        cases, args.endpoint, args.model, args.api_key, args.concurrency, args.repeat,
        not args.no_stream, args.timeout_seconds, args.sample_interval_seconds, args.preserve_priority,
        args.metrics_url, args.accelerator_probe_command,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": payload["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
