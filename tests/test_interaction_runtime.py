import threading
import unittest
from unittest.mock import patch

from app import llm_scheduler
from app.interaction import (
    _bounded_history,
    _friendly_interaction_error,
    _friendly_revision_error,
    _merge_draft_change,
    _latest_failed_proposal,
    _task_decision_memory,
    _thread_summary,
    close_thread,
)


class InteractionRuntimeTests(unittest.TestCase):
    def test_retry_selects_latest_accepted_failed_proposal(self):
        selected = _latest_failed_proposal([
            {"id": 4, "status": "accepted", "execution_status": "failed"},
            {"id": 6, "status": "accepted", "execution_status": "completed"},
            {"id": 9, "status": "accepted", "execution_status": "failed"},
        ])
        self.assertEqual(selected["id"], 9)
        self.assertIsNone(_latest_failed_proposal([
            {"id": 9, "status": "accepted", "execution_status": "failed"},
            {"id": 12, "status": "accepted", "execution_status": "completed"},
        ]))
        self.assertIsNone(_latest_failed_proposal([
            {"id": 9, "status": "accepted", "execution_status": "failed"},
            {"id": 12, "status": "proposed", "execution_status": "not_required"},
        ]))

    def test_internal_model_errors_are_not_exposed_to_users(self):
        self.assertNotIn("MODEL_OUTPUT_TRUNCATED", _friendly_interaction_error(Exception("MODEL_OUTPUT_TRUNCATED")))
        self.assertIn("原报告保持不变", _friendly_revision_error("FINAL_PLAN_CHAPTER_COUNT_MISMATCH"))

    def test_draft_change_uses_canonical_requirements_field(self):
        current = {"theme": "原主题", "requirements": "原要求"}
        self.assertEqual(
            _merge_draft_change(current, {"requirements": "新要求"}),
            {"theme": "原主题", "requirements": "新要求"},
        )
        self.assertEqual(_merge_draft_change(current, {"content": "非协议字段"}), current)

    def test_thread_summary_exposes_history_and_proposal_state(self):
        summary = _thread_summary({
            "id": 7,
            "artifact_type": "final_plan",
            "status": "open",
            "messages": [
                {"role": "user", "content": "第一轮讨论"},
                {"role": "assistant", "content": "建议调整章节顺序", "created_at": "2026-08-25"},
            ],
            "proposals": [
                {"status": "proposed"},
                {"status": "accepted"},
            ],
            "pending": False,
        })
        self.assertEqual(summary["message_count"], 2)
        self.assertEqual(summary["proposal_count"], 2)
        self.assertEqual(summary["pending_proposal_count"], 1)
        self.assertEqual(summary["last_message"]["content"], "建议调整章节顺序")

    def test_task_decision_memory_survives_conversation_boundaries(self):
        session = unittest.mock.MagicMock()
        session.execute.return_value.mappings.return_value.all.return_value = [{
            "id": 9, "artifact_type": "final_plan", "object_id": "task-a",
            "operation": "reorganize", "rationale": "按确认目录重组",
            "status": "accepted", "execution_status": "completed",
            "impact_json": '{"scope":{"tool_call":{"tool_name":"rerun_final_plan","arguments":{"instruction":"调整为六章","required_chapter_count":6}}}}',
            "after_json": "{}", "decided_at": "2026-08-31", "candidate_version_id": 12,
            "execution_run_id": "run-1",
        }]
        context = unittest.mock.MagicMock()
        context.__enter__.return_value = session
        with (
            patch("app.interaction.session_scope", return_value=context),
            patch("app.memory.short_term.load_task", return_value={
                "theme": "测试报告", "user_requirements": "形成六章报告",
            }),
        ):
            memory = _task_decision_memory("task-a", {"artifact_type": "final_plan"})
        self.assertEqual(memory["task_goal"]["theme"], "测试报告")
        self.assertEqual(memory["confirmed_decisions"][0]["required_chapter_count"], 6)
        self.assertEqual(memory["latest_candidate"]["version_id"], 12)

    def test_background_proposal_does_not_block_closing_conversation(self):
        thread = {
            "id": 7,
            "pending": False,
            "proposals": [{"execution_status": "running"}],
        }
        session = unittest.mock.MagicMock()
        context = unittest.mock.MagicMock()
        context.__enter__.return_value = session
        with (
            patch("app.interaction.get_thread", return_value=thread),
            patch("app.interaction.session_scope", return_value=context),
        ):
            result = close_thread(7)
        self.assertEqual(result["status"], "closed")
        session.execute.assert_called_once()

    def test_pending_assistant_turn_still_blocks_closing_conversation(self):
        with patch("app.interaction.get_thread", return_value={"id": 8, "pending": True}):
            with self.assertRaisesRegex(ValueError, "INTERACTION_THREAD_BUSY"):
                close_thread(8)

    def test_agent_history_is_bounded_without_losing_latest_turn(self):
        messages = [
            {"role": "user", "content": "较早问题" * 4000},
            {"role": "assistant", "content": "最近答复"},
        ]
        history = _bounded_history(messages, 128)
        self.assertEqual(history[-1]["content"], "最近答复")

    def test_interaction_lane_is_not_blocked_by_workflow_lane(self):
        original_generation = llm_scheduler._generation_slots
        original_interactive = llm_scheduler._interactive_slots
        llm_scheduler._generation_slots = threading.BoundedSemaphore(1)
        llm_scheduler._interactive_slots = threading.BoundedSemaphore(1)
        workflow_started = threading.Event()
        release_workflow = threading.Event()
        interaction_done = threading.Event()

        def workflow_call():
            workflow_started.set()
            release_workflow.wait(2)

        try:
            worker = threading.Thread(
                target=lambda: llm_scheduler.invoke("agent", workflow_call), daemon=True,
            )
            worker.start()
            self.assertTrue(workflow_started.wait(1))
            llm_scheduler.invoke("review_copilot", interaction_done.set)
            self.assertTrue(interaction_done.is_set())
            release_workflow.set()
            worker.join(1)
        finally:
            release_workflow.set()
            llm_scheduler._generation_slots = original_generation
            llm_scheduler._interactive_slots = original_interactive


if __name__ == "__main__":
    unittest.main()
