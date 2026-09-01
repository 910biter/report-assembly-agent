"""Run the task collaboration agent without persisting or executing proposals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.control_agent import build_task_agent_context
from app.task_collaboration_agent import run_task_collaboration_agent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--expect-action", action="store_true")
    args = parser.parse_args()

    result = run_task_collaboration_agent(
        build_task_agent_context(args.task_id),
        args.message,
        [],
        expect_action=args.expect_action,
    )
    print(json.dumps({
        "reply": result.reply,
        "tool_trace": result.tool_trace,
        "usage": result.usage,
        "tool_call": result.tool_call.model_dump(mode="json") if result.tool_call else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
