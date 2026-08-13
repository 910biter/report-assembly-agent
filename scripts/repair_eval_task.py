"""Recompute post-generation QA for an existing evaluation task."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.memory import short_term
from app.workflow import WorkflowController


def main() -> None:
    task_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not task_id:
        raise SystemExit("Usage: repair_eval_task.py TASK_ID")
    controller = WorkflowController(task_id)
    task = short_term.load_task(task_id) or {}
    report_id = int(task.get("report_id") or 0)
    if not report_id:
        raise SystemExit("NO_REPORT_ID")
    plan = controller._plan()
    facts = controller._facts()
    inferences = controller._inferences()
    variant = controller._selected_variant()
    controller._update_business_coverage(facts, inferences)
    controller._auto_revision(report_id, plan, facts, inferences, variant)
    controller._apply_budget_control(report_id, plan, facts, inferences)
    controller._run_quality_check(variant, plan, report_id)
    controller._update_business_qa(report_id, plan, facts, inferences)
    task = short_term.load_task(task_id) or {}
    print("TASK_ID=" + task_id)
    print("AUTO_REVISION=" + json.dumps(task.get("auto_revision", {}), ensure_ascii=False))
    print("REPORT_STATS=" + json.dumps(task.get("report_stats", {}), ensure_ascii=False))
    print("BUSINESS_QA=" + json.dumps(task.get("business_qa", {}), ensure_ascii=False))
    print("QA_NOTES=" + json.dumps(task.get("qa_notes", []), ensure_ascii=False))


if __name__ == "__main__":
    main()
