import unittest
from unittest.mock import patch

from app.workflow.checkpoints import CheckpointError, confirm_directory, confirm_requirements
from app.workflow.controller import Stage, WorkflowController


class WorkflowCheckpointTests(unittest.TestCase):
    def test_confirm_requirements_persists_brief_then_enqueues(self):
        payload = {"requirement_review_pending": True}

        def transition(_task_id, callback, *, _session):
            return callback(payload, _session)

        session = unittest.mock.MagicMock()
        context = unittest.mock.MagicMock()
        context.__enter__.return_value = session
        with (
            patch("app.workflow.checkpoints.short_term.load_task", return_value=payload),
            patch("app.workflow.checkpoints.short_term.transition_task", side_effect=transition) as transition_task,
            patch("app.workflow.checkpoints.session_scope", return_value=context),
            patch("app.workflow.checkpoints.enqueue_task", return_value={"status": "queued"}) as enqueue,
        ):
            result = confirm_requirements("task-a", theme="专题报告", requirements="形成可信分析报告")
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["replan_required"])
        transition_task.assert_called_once()
        self.assertEqual("专题报告", payload["theme"])
        self.assertEqual("形成可信分析报告", payload["user_requirements"])
        self.assertFalse(payload["requirement_review_pending"])
        self.assertEqual("planning", payload["resume_from_stage"])
        session.execute.assert_called_once()
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
