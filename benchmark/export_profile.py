"""Export task/run workload statistics to JSON without changing workflow state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export existing LLM workload profile as JSON")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    # This import stays inside the command so the standalone runner never pulls
    # application configuration or database dependencies.
    from app.token_monitor import build_workload_profile, list_llm_calls

    profile = build_workload_profile(args.task_id, run_id=args.run_id)
    calls = list_llm_calls(args.task_id, run_id=args.run_id)
    payload = {
        "schema_version": "1.0",
        "profile": profile,
        "calls": [
            {
                key: call.get(key)
                for key in (
                    "call_id", "agent", "stage", "input_tokens", "output_tokens", "context_tokens",
                    "latency_ms", "retry_count", "success", "material_count", "created_at", "funnel_json",
                )
            }
            for call in calls
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

