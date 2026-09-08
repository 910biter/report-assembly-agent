import unittest
from unittest.mock import patch

from app.workflow.checkpoints import CheckpointError, confirm_directory, confirm_requirements
from app.workflow.controller import Stage, WorkflowController


class WorkflowCheckpointTests(unittest.TestCase):
    def test_confirm_requirements_persists_brief_then_enqueues(self):
        with patch("app.workflow.checkpoints.short_term.load_task", return_value={
            "requirement_review_pending": True,
        }), patch("app.workflow.checkpoints.short_term.update_task") as update_task, patch(
            "app.workflow.checkpoints.enqueue_task", return_value={"status": "queued"},
        ) as enqueue:
            result = confirm_requirements("task-a", theme="专题报告", requirements="形成可信分析报告")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["replan_required"])
        update_task.assert_called_once_with(
            "task-a", requirement_review_pending=False, requirement_review_completed=True,
            theme="专题报告", user_requirements="形成可信分析报告",
            requirement_review_feedback="", stage="planning", resume_from_stage="planning",
        )
        enqueue.assert_called_once_with("task-a")

    def test_confirm_directory_preserves_current_plan_and_enqueues(self):
        with (
            patch("app.workflow.checkpoints.short_term.load_task", return_value={
                "directory_review_pending": True,
            }),
            patch("app.workflow.checkpoints.short_term.update_task") as update_task,
            patch("app.workflow.checkpoints.enqueue_task", return_value={"status": "queued"}),
        ):
            result = confirm_directory("task-a")
        self.assertFalse(result["replan_required"])
        update_task.assert_called_once_with(
            "task-a", directory_review_pending=False, directory_review_completed=True,
            directory_review_feedback="", resume_from_stage="writing",
        )

    def test_checkpoint_rejects_wrong_state(self):
        with patch("app.workflow.checkpoints.short_term.load_task", return_value={}):
            with self.assertRaisesRegex(CheckpointError, "REQUIREMENT_REVIEW_NOT_PENDING"):
                confirm_requirements("task-a", theme="主题", requirements="要求")

    def test_directory_resume_skips_all_upstream_stages_but_not_writing(self):
        controller = WorkflowController.__new__(WorkflowController)
        controller.task = {"resume_from_stage": "writing"}
        for stage in (
            Stage.PARSING,
            Stage.MATERIAL_ANALYSIS,
            Stage.PLANNING,
            Stage.EVIDENCE,
            Stage.CONFLICT,
            Stage.ANALYSIS,
        ):
            self.assertTrue(controller._resume_skips(stage))
        self.assertFalse(controller._resume_skips(Stage.WRITING))


if __name__ == "__main__":
    unittest.main()
