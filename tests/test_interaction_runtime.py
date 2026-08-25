import threading
import unittest
from unittest.mock import patch

from app import llm_scheduler
from app.interaction import (
    _build_interaction_prompt,
    _estimate_tokens,
    _is_explicit_change_request,
    _is_progress_question,
    _merge_draft_change,
    _normalize_proposal,
    _thread_summary,
)


class InteractionRuntimeTests(unittest.TestCase):
    def test_status_question_cannot_become_change_request(self):
        self.assertTrue(_is_progress_question("你现在进行到哪一步了？"))
        self.assertFalse(_is_explicit_change_request("你现在进行到哪一步了？"))

    def test_explicit_edit_is_recognized(self):
        self.assertTrue(_is_explicit_change_request("请把报告要求调整得更精炼一些"))
        self.assertFalse(_is_explicit_change_request("你认为报告要求应该怎么调整？"))

    def test_proposal_is_limited_to_current_artifact_schema(self):
        thread = {"artifact_type": "task_brief"}
        current = {"theme": "原主题", "requirements": "原要求", "stage": "analysis"}
        proposal = _normalize_proposal(
            thread, current, "请调整报告要求",
            {"after": {"requirements": "新要求", "content": "不应混入的报告正文", "stage": "done"}},
        )
        self.assertEqual(proposal["after"], {"requirements": "新要求"})
        self.assertIsNone(_normalize_proposal(
            thread, current, "现在进行到哪一步了？",
            {"after": {"requirements": "无意义提案"}},
        ))

    def test_draft_change_normalizes_requirements_and_content(self):
        current = {"theme": "原主题", "requirements": "原要求"}
        self.assertEqual(
            _merge_draft_change(current, {"requirements": "新要求"}),
            {"theme": "原主题", "requirements": "新要求"},
        )
        self.assertEqual(
            _merge_draft_change(current, {"content": "兼容要求"})["requirements"],
            "兼容要求",
        )

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

    def test_interaction_prompt_keeps_current_turn_within_budget(self):
        thread = {
            "id": 1,
            "task_id": "task-a",
            "scope": {"current": {"content": "旧内容"}},
            "messages": [
                {"role": "user", "content": "较早问题" * 4000},
                {"role": "assistant", "content": "较早答复" * 4000},
            ],
        }
        with (
            patch("app.interaction._interaction_task_context", return_value={"theme": "测试任务"}),
            patch("app.interaction._interaction_grounding", return_value={"facts": [{"id": 2, "content": "依据"}]}),
            patch("app.interaction._accepted_interaction_decisions", return_value=[]),
        ):
            prompt = _build_interaction_prompt(thread, {"content": "当前句子"}, "请解释当前句子的依据")
        self.assertLessEqual(_estimate_tokens(prompt), 6144)
        self.assertIn("当前句子", prompt)
        self.assertIn("请解释当前句子的依据", prompt)

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
