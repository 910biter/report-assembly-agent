"""Build portable JSON benchmark cases from raw opt-in capture JSONL files."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from benchmark import BENCHMARK_SCHEMA_VERSION
from benchmark.workloads import io_pattern, workload_contract, workload_kind


@dataclass(frozen=True)
class CapturedCall:
    source: Path
    line_number: int
    payload: dict[str, Any]
    enrichments: dict[str, dict[str, Any]]

    @property
    def context(self) -> dict[str, Any]:
        return dict(self.payload.get("context") or {})

    @property
    def request(self) -> dict[str, Any]:
        return dict(self.payload.get("request") or {})

    @property
    def observed(self) -> dict[str, Any]:
        return dict(self.payload.get("observed") or {})

    @property
    def usage(self) -> dict[str, Any]:
        usage = dict(self.observed.get("usage") or {})
        call_log = self.enrichments.get("call_log") or {}
        if not usage.get("prompt_tokens") and call_log.get("input_tokens") is not None:
            usage["prompt_tokens"] = call_log.get("input_tokens")
        if not usage.get("completion_tokens") and call_log.get("output_tokens") is not None:
            usage["completion_tokens"] = call_log.get("output_tokens")
        return usage

    @property
    def input_tokens(self) -> int:
        return int(self.usage.get("prompt_tokens") or 0)

    @property
    def output_tokens(self) -> int:
        return int(self.usage.get("completion_tokens") or 0)

    @property
    def stage(self) -> str:
        correlation = dict(self.payload.get("correlation") or {})
        return str(self.context.get("stage") or correlation.get("agent") or "unknown")

    @property
    def workload(self) -> str:
        correlation = dict(self.payload.get("correlation") or {})
        return workload_kind(self.stage, str(correlation.get("agent") or ""))

    @property
    def error(self) -> str:
        call_log = self.enrichments.get("call_log") or {}
        error = str(self.observed.get("error") or call_log.get("error") or "")
        if error:
            return error
        response_format = self.request.get("response_format")
        if response_format and not _top_level_json_fields(str(self.observed.get("content") or "")):
            return "MODEL_JSON_INVALID"
        if str(self.observed.get("finish_reason") or "").lower() == "length":
            return "MODEL_OUTPUT_TRUNCATED"
        return ""

    @property
    def success(self) -> bool:
        return not self.error

    @property
    def baseline(self) -> dict[str, Any]:
        """Everything needed later to interpret this replay case without the app."""
        return {
            "correlation": dict(self.payload.get("correlation") or {}),
            "application_call": dict(self.enrichments.get("call_log") or {}),
            "token_funnel": dict(self.enrichments.get("funnel") or {}),
            "artifact_metrics": dict(self.enrichments.get("metrics") or {}),
            "artifact_products": dict(self.enrichments.get("products") or {}),
            "scheduler": dict(self.enrichments.get("scheduler") or {}),
            "call_boundary": dict(self.enrichments.get("boundary") or {}),
        }


def read_capture_bundle(paths: Iterable[Path]) -> tuple[list[CapturedCall], list[dict[str, Any]]]:
    call_rows: list[tuple[Path, int, dict[str, Any]]] = []
    enrichments: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    resource_samples: list[dict[str, Any]] = []
    for path in sorted(Path(item) for item in paths):
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL capture: {path}:{line_number}") from exc
                if not isinstance(payload, dict):
                    raise ValueError(f"capture record must be an object: {path}:{line_number}")
                event_type = str(payload.get("event_type") or "call")
                if event_type == "resource_sample":
                    resource_samples.append(payload)
                    continue
                if event_type == "call_boundary":
                    call_id = str(payload.get("call_id") or "")
                    phase = str(payload.get("phase") or "")
                    if call_id and phase:
                        boundary = enrichments[call_id].setdefault("boundary", {})
                        boundary[phase] = dict(payload.get("payload") or {})
                    continue
                if event_type == "enrichment":
                    call_id = str(payload.get("call_id") or "")
                    kind = str(payload.get("kind") or "")
                    if call_id and kind:
                        previous = enrichments[call_id].get(kind) or {}
                        previous.update(dict(payload.get("payload") or {}))
                        enrichments[call_id][kind] = previous
                    continue
                call_rows.append((path, line_number, payload))
    calls: list[CapturedCall] = []
    for path, line_number, payload in call_rows:
        correlation = dict(payload.get("correlation") or {})
        call_id = str(correlation.get("call_id") or "")
        calls.append(CapturedCall(path, line_number, payload, dict(enrichments.get(call_id) or {})))
    return calls, resource_samples


def read_captures(paths: Iterable[Path]) -> list[CapturedCall]:
    """Compatibility helper for callers that only need model call records."""
    calls, _resource_samples = read_capture_bundle(paths)
    return calls


def _quantile_index(count: int, percentile: int) -> int:
    if count <= 1:
        return 0
    return min(count - 1, max(0, int(math.ceil((percentile / 100) * count) - 1)))


def _top_level_json_fields(content: str) -> list[str]:
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return []
    return sorted(str(key) for key in value) if isinstance(value, dict) else []


def _message_shape(messages: list[dict[str, Any]]) -> dict[str, Any]:
    roles: dict[str, dict[str, int]] = defaultdict(lambda: {"messages": 0, "characters": 0})
    lengths: list[int] = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        length = len(str(message.get("content") or ""))
        roles[role]["messages"] += 1
        roles[role]["characters"] += length
        lengths.append(length)
    return {
        "message_count": len(messages),
        "characters": sum(lengths),
        "largest_message_characters": max(lengths, default=0),
        "roles": dict(sorted(roles.items())),
    }


def _output_shape(content: str) -> dict[str, Any]:
    parsed = _safe_json_value(content)
    fields = _top_level_json_fields(content)
    return {
        "characters": len(content),
        "lines": content.count("\n") + (1 if content else 0),
        "json_object": isinstance(parsed, dict),
        "top_level_field_count": len(fields),
        "top_level_fields": fields,
        "list_item_count": sum(len(value) for value in parsed.values() if isinstance(value, list)) if isinstance(parsed, dict) else 0,
    }


def _safe_json_value(content: str) -> Any:
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


def _case_id(call: CapturedCall, load_class: str, index: int) -> str:
    stage = "".join(ch if ch.isalnum() else "-" for ch in call.stage.lower()).strip("-") or "unknown"
    return f"{stage}-{load_class}-{index:03d}"


def case_from_capture(call: CapturedCall, load_class: str, index: int) -> dict[str, Any]:
    request = call.request
    generation = {
        key: request[key]
        for key in ("model", "max_tokens", "temperature", "top_p", "response_format", "chat_template_kwargs", "priority")
        if key in request
    }
    content = str(call.observed.get("content") or "")
    output_tokens = call.output_tokens
    output_low = max(0, int(math.floor(output_tokens * 0.7)))
    output_high = max(output_low, int(math.ceil(output_tokens * 1.3)))
    required_fields = _top_level_json_fields(content)
    error = call.error
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "case_id": _case_id(call, load_class, index),
        "workload": call.workload,
        "stage": call.stage,
        "load_class": load_class,
        "request": {
            "messages": list(request.get("messages") or []),
            "generation": generation,
        },
        "shape": {
            "reference_input_tokens": call.input_tokens,
            "reference_output_tokens": {
                "observed": output_tokens,
                "minimum": output_low,
                "maximum": output_high,
            },
            "reference_input_output_ratio": round(call.input_tokens / max(output_tokens, 1), 4),
            "io_pattern": io_pattern(call.input_tokens, output_tokens),
            "material_count": int(call.context.get("material_count") or 0),
            "input_structure": _message_shape(list(request.get("messages") or [])),
            "output_structure": _output_shape(content),
        },
        "workload_contract": workload_contract(call.workload),
        "validators": {
            "expect_success": not bool(error),
            "expect_json_object": bool(required_fields),
            "required_top_level_fields": required_fields,
            "finish_reason": str(call.observed.get("finish_reason") or ""),
        },
        "reference": {
            "captured_at": str(call.payload.get("captured_at") or ""),
            "observed_latency_seconds": float(call.observed.get("latency_seconds") or 0.0),
            "source_capture": f"{call.source.name}:{call.line_number}",
            "error_class": error.split(":", 1)[0] if error else "",
        },
        "baseline": call.baseline,
    }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return round(ordered[_quantile_index(len(ordered), percentile)], 6)


def _distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "max": round(max(values), 6) if values else None,
    }


def _baseline_call(call: CapturedCall) -> dict[str, Any]:
    latency = float(call.observed.get("latency_seconds") or 0.0)
    return {
        "call_id": str((call.payload.get("correlation") or {}).get("call_id") or ""),
        "logical_call_id": str((call.payload.get("correlation") or {}).get("logical_call_id") or ""),
        "attempt": int((call.payload.get("correlation") or {}).get("attempt") or 0),
        "stage": call.stage,
        "workload": call.workload,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "context_tokens": call.input_tokens + call.output_tokens,
        "input_output_ratio": round(call.input_tokens / max(call.output_tokens, 1), 6),
        "io_pattern": io_pattern(call.input_tokens, call.output_tokens),
        "latency_seconds": latency,
        "success": call.success,
        "error_class": call.error.split(":", 1)[0] if call.error else "",
        "finish_reason": str(call.observed.get("finish_reason") or ""),
        "captured_at": str(call.payload.get("captured_at") or ""),
        "input_structure": _message_shape(list(call.request.get("messages") or [])),
        "output_structure": _output_shape(str(call.observed.get("content") or "")),
        "baseline": call.baseline,
    }


def _numeric_funnel(items: list[CapturedCall]) -> dict[str, dict[str, float | None]]:
    values: dict[str, list[float]] = defaultdict(list)
    for item in items:
        for key, value in (item.enrichments.get("funnel") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[str(key)].append(float(value))
    return {
        key: {"total": round(sum(group), 6), **_distribution(group)}
        for key, group in sorted(values.items())
    }


def _coverage(count: int, total: int) -> dict[str, int | float]:
    return {
        "observed": count,
        "total": total,
        "rate": round(count / max(total, 1), 6),
    }


def build_resource_profile(samples: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    correlated = 0
    for sample in samples:
        active = list(sample.get("active_calls") or [])
        if active:
            correlated += 1
        for stage in {str(call.get("stage") or "unknown") for call in active}:
            groups[stage].append(dict(sample.get("resource") or {}))
    stage_rows = []
    for stage, resources in sorted(groups.items()):
        cpu = [float(item["cpu_percent"]) for item in resources if item.get("cpu_percent") is not None]
        ram = [float(item["ram_percent"]) for item in resources if item.get("ram_percent") is not None]
        gpu_util = [float(gpu["utilization_percent"]) for item in resources for gpu in (item.get("gpus") or []) if gpu.get("utilization_percent") is not None]
        gpu_memory = [float(gpu["memory_used_mb"]) for item in resources for gpu in (item.get("gpus") or []) if gpu.get("memory_used_mb") is not None]
        stage_rows.append({
            "stage": stage,
            "samples": len(resources),
            "cpu_percent": _distribution(cpu),
            "ram_percent": _distribution(ram),
            "gpu_utilization_percent": _distribution(gpu_util),
            "gpu_memory_used_mb": _distribution(gpu_memory),
        })
    return {
        "samples": len(samples),
        "correlated_samples": correlated,
        "correlation_coverage": round(correlated / max(len(samples), 1), 6),
        "stages": stage_rows,
    }


def build_observed_profile(calls: list[CapturedCall], resource_samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    total_input = sum(call.input_tokens for call in calls)
    total_output = sum(call.output_tokens for call in calls)
    total_latency = sum(float(call.observed.get("latency_seconds") or 0.0) for call in calls)
    groups: dict[tuple[str, str], list[CapturedCall]] = defaultdict(list)
    for call in calls:
        groups[(call.stage, call.workload)].append(call)
    stages: list[dict[str, Any]] = []
    for (stage, workload), items in sorted(groups.items()):
        input_tokens = [float(item.input_tokens) for item in items]
        output_tokens = [float(item.output_tokens) for item in items]
        ratios = [item.input_tokens / max(item.output_tokens, 1) for item in items]
        latencies = [float(item.observed.get("latency_seconds") or 0.0) for item in items]
        queue_wait = [float((item.enrichments.get("scheduler") or {}).get("queue_wait_seconds")) for item in items if (item.enrichments.get("scheduler") or {}).get("queue_wait_seconds") is not None]
        execution = [float((item.enrichments.get("scheduler") or {}).get("execution_seconds")) for item in items if (item.enrichments.get("scheduler") or {}).get("execution_seconds") is not None]
        prefill_speed = [float(((item.enrichments.get("call_log") or {}).get("model_timing") or {}).get("prompt_tokens_per_second")) for item in items if float(((item.enrichments.get("call_log") or {}).get("model_timing") or {}).get("prompt_tokens_per_second") or 0) > 0]
        decode_speed = [float(((item.enrichments.get("call_log") or {}).get("model_timing") or {}).get("output_tokens_per_second")) for item in items if float(((item.enrichments.get("call_log") or {}).get("model_timing") or {}).get("output_tokens_per_second") or 0) > 0]
        stage_input = sum(input_tokens)
        stage_output = sum(output_tokens)
        stage_latency = sum(latencies)
        coverage = {
            "input_tokens": _coverage(sum(1 for item in items if item.input_tokens > 0), len(items)),
            "output_tokens": _coverage(sum(1 for item in items if item.output_tokens > 0), len(items)),
            "latency": _coverage(sum(1 for value in latencies if value > 0), len(items)),
            "scheduler_queue_wait": _coverage(len(queue_wait), len(items)),
            "scheduler_execution": _coverage(len(execution), len(items)),
            "server_prefill_speed": _coverage(len(prefill_speed), len(items)),
            "server_decode_speed": _coverage(len(decode_speed), len(items)),
            "token_funnel": _coverage(sum(1 for item in items if item.enrichments.get("funnel")), len(items)),
            "call_boundary": _coverage(sum(1 for item in items if item.enrichments.get("boundary")), len(items)),
        }
        stages.append({
            "stage": stage,
            "workload": workload,
            "workload_contract": workload_contract(workload),
            "calls": len(items),
            "success_rate": round(sum(1 for item in items if item.success) / max(len(items), 1), 6),
            "input_tokens": _distribution(input_tokens),
            "output_tokens": _distribution(output_tokens),
            "input_output_ratio": _distribution(ratios),
            "latency_seconds": _distribution(latencies),
            "scheduler_queue_wait_seconds": _distribution(queue_wait),
            "scheduler_execution_seconds": _distribution(execution),
            "server_reported_prefill_tokens_per_second": _distribution(prefill_speed),
            "server_reported_decode_tokens_per_second": _distribution(decode_speed),
            "retry_attempts": sum(1 for item in items if int((item.payload.get("correlation") or {}).get("attempt") or 0) > 0),
            "token_funnel": _numeric_funnel(items),
            "metric_coverage": coverage,
            "shares": {
                "calls": round(len(items) / max(len(calls), 1), 6),
                "input_tokens": round(stage_input / max(total_input, 1), 6),
                "output_tokens": round(stage_output / max(total_output, 1), 6),
                "llm_latency": round(stage_latency / max(total_latency, 1e-9), 6),
            },
            "failure_classes": dict(sorted((
                (error, sum(1 for item in items if (item.error.split(":", 1)[0] if item.error else "") == error))
                for error in {item.error.split(":", 1)[0] for item in items if item.error}
            ))),
        })
    return {
        "totals": {
            "calls": len(calls),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "llm_latency_seconds": round(total_latency, 6),
        },
        "stages": stages,
        "resource_correlation": build_resource_profile(list(resource_samples or [])),
    }


def select_representative_cases(calls: Iterable[CapturedCall]) -> list[dict[str, Any]]:
    """Select P50/P95/Max and one failure from every observed stage/workload."""
    grouped: dict[tuple[str, str], list[CapturedCall]] = defaultdict(list)
    for call in calls:
        grouped[(call.stage, call.workload)].append(call)
    cases: list[dict[str, Any]] = []
    for (_stage, _workload), group in sorted(grouped.items()):
        successful = sorted(
            (item for item in group if item.success),
            key=lambda item: (item.input_tokens, item.output_tokens, item.source.name, item.line_number),
        )
        failed = sorted(
            (item for item in group if not item.success),
            key=lambda item: (item.input_tokens, item.source.name, item.line_number),
        )
        selected: list[tuple[str, CapturedCall]] = []
        if successful:
            selected.extend([
                ("p50", successful[_quantile_index(len(successful), 50)]),
                ("p95", successful[_quantile_index(len(successful), 95)]),
                ("max", successful[-1]),
            ])
        if failed:
            selected.append(("failure", failed[0]))
        seen: set[tuple[str, Path, int]] = set()
        for load_class, call in selected:
            key = (load_class, call.source, call.line_number)
            if key in seen:
                continue
            seen.add(key)
            cases.append(case_from_capture(call, load_class, len(cases) + 1))
    return cases


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dataset(capture_paths: Iterable[Path], output_dir: Path, dataset_version: str = "v1.0") -> dict[str, Any]:
    source_paths = [Path(path) for path in capture_paths]
    calls, resource_samples = read_capture_bundle(source_paths)
    cases = select_representative_cases(calls)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    capture_events_path = output_dir / "capture-events.jsonl"
    capture_event_count = 0
    with capture_events_path.open("w", encoding="utf-8") as target:
        for source in source_paths:
            with source.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        target.write(line if line.endswith("\n") else line + "\n")
                        capture_event_count += 1
    cases_path = output_dir / "stage-replay.jsonl"
    with cases_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    resource_path = output_dir / "baseline-resources.jsonl"
    if resource_samples:
        with resource_path.open("w", encoding="utf-8") as handle:
            for sample in resource_samples:
                handle.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
    calls_path = output_dir / "baseline-calls.jsonl"
    with calls_path.open("w", encoding="utf-8") as handle:
        for call in calls:
            handle.write(json.dumps(_baseline_call(call), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    profile_path = output_dir / "observed-profile.json"
    profile_path.write_text(
        json.dumps(build_observed_profile(calls, resource_samples), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runner_path = output_dir / "runner.py"
    shutil.copyfile(Path(__file__).with_name("runner.py"), runner_path)
    launcher_path = output_dir / "benchmark.sh"
    shutil.copyfile(Path(__file__).with_name("benchmark.sh"), launcher_path)
    launcher_path.chmod(0o755)
    benchmark_env_path = output_dir / "benchmark.env.example"
    shutil.copyfile(Path(__file__).with_name("benchmark.env.example"), benchmark_env_path)
    requirements_path = output_dir / "requirements.txt"
    requirements_path.write_text("httpx>=0.27,<1\npsutil>=5.9,<8\n", encoding="utf-8")
    reproduction_path = output_dir / "reproduction.json"
    reproduction_path.write_text(json.dumps({
        "one_click": "./benchmark.sh all /absolute/path/to/model-or-huggingface-id",
        "install_only": "./benchmark.sh setup",
        "start_only": "./benchmark.sh start /absolute/path/to/model-or-huggingface-id",
        "run_only": "./benchmark.sh run",
        "stop": "./benchmark.sh stop",
        "protocol": "OpenAI-compatible chat completions",
        "environment": {
            "OPENAI_BASE_URL": "http://HOST:PORT/v1",
            "OPENAI_API_KEY": "optional",
            "OPENAI_MODEL": "MODEL",
        },
        "run": "python runner.py --dataset stage-replay.jsonl --output result.json",
        "notes": [
            "Command-line --endpoint/--api-key/--model values override environment defaults.",
            "Use --metrics-url for server-internal aggregate counters.",
            "Keep model, precision, context limit, concurrency and repeat identical for cross-device comparison.",
            "Streaming is required for client-observed TTFT and TPOT metrics.",
            "The bundled launcher keeps vLLM resident after the run so repeated tests do not reload weights.",
            "Model weights are referenced by local path or model id and are intentionally not copied into the dataset.",
        ],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_workload: dict[str, int] = defaultdict(int)
    by_stage: dict[str, int] = defaultdict(int)
    for case in cases:
        by_workload[str(case["workload"])] += 1
        by_stage[str(case["stage"])] += 1
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "dataset_version": dataset_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_capture_records": len(calls),
        "source_capture_events": capture_event_count,
        "case_count": len(cases),
        "files": {
            "stage-replay.jsonl": _sha256(cases_path),
            "capture-events.jsonl": _sha256(capture_events_path),
            "baseline-calls.jsonl": _sha256(calls_path),
            "observed-profile.json": _sha256(profile_path),
            "runner.py": _sha256(runner_path),
            "benchmark.sh": _sha256(launcher_path),
            "benchmark.env.example": _sha256(benchmark_env_path),
            "requirements.txt": _sha256(requirements_path),
            "reproduction.json": _sha256(reproduction_path),
            **({"baseline-resources.jsonl": _sha256(resource_path)} if resource_samples else {}),
        },
        "by_workload": dict(sorted(by_workload.items())),
        "by_stage": dict(sorted(by_stage.items())),
        "resource_sample_count": len(resource_samples),
        "independence": {
            "requires_application_database": False,
            "requires_vector_database": False,
            "requires_original_materials": False,
            "requires_project_source": False,
            "endpoint_protocol": "openai-compatible-chat-completions",
            "bundled_vllm_launcher": True,
            "bundled_model_weights": False,
        },
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build standalone JSON benchmark dataset from raw captures")
    parser.add_argument("--captures", required=True, nargs="+", help="Capture JSONL file(s) or directory(ies)")
    parser.add_argument("--output", required=True, help="Output dataset directory")
    parser.add_argument("--dataset-version", default="v1.0")
    args = parser.parse_args(argv)
    paths: list[Path] = []
    for raw in args.captures:
        path = Path(raw)
        paths.extend(sorted(path.glob("*.jsonl")) if path.is_dir() else [path])
    paths = [path for path in paths if path.exists()]
    if not paths:
        parser.error("no capture JSONL files found")
    manifest = build_dataset(paths, Path(args.output), dataset_version=args.dataset_version)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
