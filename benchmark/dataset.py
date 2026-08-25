"""Build portable JSON benchmark cases from raw opt-in capture JSONL files."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from benchmark import BENCHMARK_SCHEMA_VERSION
from benchmark.workloads import io_pattern, workload_kind


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
        return str(self.context.get("stage") or "unknown")

    @property
    def workload(self) -> str:
        return workload_kind(self.stage)

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
        },
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
    calls, resource_samples = read_capture_bundle(capture_paths)
    cases = select_representative_cases(calls)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
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
        "case_count": len(cases),
        "files": {
            "stage-replay.jsonl": _sha256(cases_path),
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
