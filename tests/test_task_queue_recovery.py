import unittest
from unittest.mock import patch

from app.workflow import queue as task_queue


class TaskQueueRecoveryTests(unittest.TestCase):
    def test_restart_pauses_orphaned_running_task(self):
        tasks = [("task-1", {
            "stage": "evidence",
            "run_id": "run-1",
            "queue_status": {"status": "running"},
        })]
        updates = []

        with patch.object(task_queue.short_term, "list_tasks", return_value=tasks), patch.object(
            task_queue.short_term, "update_task", side_effect=lambda task_id, **fields: updates.append((task_id, fields)) or fields
        ), patch("app.task_runs.update_task_run") as update_run:
            count = task_queue.reconcile_interrupted_tasks()

        self.assertEqual(count, 1)
        self.assertEqual(updates[0][1]["stage"], "paused")
        self.assertEqual(updates[0][1]["queue_status"]["status"], "paused")
        update_run.assert_called_once()

    def test_pause_repairs_orphaned_pause_request(self):
        task = {
            "stage": "evidence",
            "queue_status": {"status": "pause_requested"},
        }
        with patch.object(task_queue.short_term, "load_task", return_value=task), patch.object(
            task_queue.short_term, "update_task",
            side_effect=lambda task_id, **fields: {**task, **fields},
        ):
            result = task_queue.request_control("task-1", "pause")

        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["stage"], "paused")


if __name__ == "__main__":
    unittest.main()
