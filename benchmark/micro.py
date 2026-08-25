"""Generate business-free context/output-shape cases for hardware-only tests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmark import BENCHMARK_SCHEMA_VERSION


def _filler(chars: int) -> str:
    unit = "Benchmark context segment: stable synthetic content for accelerator measurement. "
    return (unit * ((chars // len(unit)) + 1))[:chars]


def generate_micro_cases(input_targets: list[int], output_targets: list[int], model: str) -> list[dict[str, Any]]:
    """Create non-semantic requests; server usage is the authoritative token count."""
    cases: list[dict[str, Any]] = []
    for input_target in input_targets:
        for output_target in output_targets:
            # Character estimate keeps this generator dependency-free. The runner
            # records tokenizer-authoritative usage returned by the target server.
            prompt = _filler(max(256, input_target * 4))
            case_id = f"micro-in{input_target}-out{output_target}"
            cases.append({
                "schema_version": BENCHMARK_SCHEMA_VERSION,
                "case_id": case_id,
                "workload": "micro_context_decode",
                "stage": "micro",
                "load_class": "synthetic",
                "request": {
                    "messages": [
                        {"role": "system", "content": "Return exactly the requested synthetic marker text."},
                        {"role": "user", "content": f"{prompt}\nReturn a concise synthetic response of approximately {output_target} tokens."},
                    ],
                    "generation": {"model": model, "max_tokens": output_target, "temperature": 0},
                },
                "shape": {
                    "target_input_tokens": input_target,
                    "reference_output_tokens": {
                        "observed": output_target,
                        "minimum": max(1, int(output_target * 0.5)),
                        "maximum": int(output_target * 1.1),
                    },
                    "token_count_source": "target_only; runner records server-reported tokenizer usage",
                },
                "validators": {"expect_success": True, "expect_json_object": False, "required_top_level_fields": []},
                "reference": {"synthetic": True},
            })
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate fully synthetic micro benchmark JSONL")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input-tokens", default="1024,4096,8192,16384,32768")
    parser.add_argument("--output-tokens", default="128,512,2048")
    args = parser.parse_args(argv)
    inputs = [int(item.strip()) for item in args.input_tokens.split(",") if item.strip()]
    outputs = [int(item.strip()) for item in args.output_tokens.split(",") if item.strip()]
    cases = generate_micro_cases(inputs, outputs, args.model)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    print(json.dumps({"output": str(output), "case_count": len(cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

