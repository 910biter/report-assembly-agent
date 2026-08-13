"""Run one real full workflow and print timing/quality summary."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect, init_db
from app.memory import short_term
from app.memory import style
from app.workflow import WorkflowController


def main() -> None:
    init_db()
    with connect() as conn:
        material_ids = [
            int(row["id"])
            for row in conn.execute("SELECT id FROM materials ORDER BY id").fetchall()
        ]
    if not material_ids:
        raise SystemExit("NO_MATERIAL_IDS")
    locked = style.get_locked_variant()

    task_id = "full_eval_" + datetime.now().strftime("%m%d_%H%M%S")
    short_term.save_task(task_id, {
        "theme": "大学生暑期社会实践活动总结报告（P0/P1全量评估）",
        "user_requirements": "按当前P0/P1修复后的业务闭环、模板学习和UI进度能力生成，并用于评估质量与速度。",
        "variant_id": locked.id if locked else None,
        "material_ids": material_ids,
        "stage": "created",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    print("TASK_ID=" + task_id, flush=True)
    print("MATERIAL_IDS=" + ",".join(map(str, material_ids)), flush=True)
    start = time.time()
    WorkflowController(task_id).run_to_review()
    elapsed = round(time.time() - start, 1)
    task = short_term.load_task(task_id) or {}

    print("ELAPSED=" + str(elapsed), flush=True)
    print("STAGE=" + str(task.get("stage")), flush=True)
    print("REPORT_ID=" + str(task.get("report_id")), flush=True)
    print("STAGE_DURATIONS=" + json.dumps(task.get("stage_durations", {}), ensure_ascii=False), flush=True)
    print("STAGE_TIMINGS=" + json.dumps(task.get("stage_timings", {}), ensure_ascii=False), flush=True)
    print("LLM_STATS=" + json.dumps(task.get("llm_stats", {}), ensure_ascii=False), flush=True)
    print("TOKEN_EFFICIENCY=" + json.dumps(task.get("token_efficiency", {}), ensure_ascii=False), flush=True)
    print("REPORT_STATS=" + json.dumps(task.get("report_stats", {}), ensure_ascii=False), flush=True)
    print("BUSINESS_QA=" + json.dumps(task.get("business_qa", {}), ensure_ascii=False), flush=True)
    print("QA_NOTES=" + json.dumps(task.get("qa_notes", []), ensure_ascii=False), flush=True)
    with connect() as conn:
        report_id = task.get("report_id")
        if report_id:
            rows = conn.execute(
                "SELECT section, COUNT(*) c, SUM(LENGTH(content)) chars "
                "FROM report_sentences WHERE report_id=? "
                "GROUP BY section ORDER BY MIN(position)",
                (report_id,),
            ).fetchall()
            print("SECTIONS=" + json.dumps([dict(r) for r in rows], ensure_ascii=False), flush=True)
            calls = conn.execute(
                "SELECT agent, stage, COUNT(*) calls, SUM(input_tokens) input_tokens, "
                "SUM(output_tokens) output_tokens, SUM(latency_ms) latency_ms "
                "FROM llm_call_logs WHERE task_id=? GROUP BY agent, stage ORDER BY SUM(output_tokens) DESC",
                (task_id,),
            ).fetchall()
            print("TOKEN_CALLS_BY_AGENT_STAGE=" + json.dumps([dict(r) for r in calls], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
