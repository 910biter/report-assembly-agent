import unittest

from app.workflow.stage_state import stage_input_signature, signature_matches


class WorkflowStageStateTests(unittest.TestCase):
    def test_analysis_signature_is_order_independent_for_fact_ids(self):
        task = {"theme": "主题", "material_ids": [2, 1], "fact_ids": [3, 1, 3]}
        first = stage_input_signature("analysis", task, plan={"id": 7})
        task["fact_ids"] = [1, 3]
        task["material_ids"] = [1, 2]
        self.assertEqual(first, stage_input_signature("analysis", task, plan={"id": 7}))

    def test_final_plan_signature_changes_when_analysis_changes(self):
        task = {"theme": "主题", "material_ids": [1], "fact_ids": [1], "inference_ids": [2]}
        first = stage_input_signature("final_plan", task, plan={"id": 7})
        task["inference_ids"] = [2, 3]
        self.assertNotEqual(first, stage_input_signature("final_plan", task, plan={"id": 7}))

    def test_writing_signature_changes_with_final_plan_contract(self):
        task = {"theme": "主题", "material_ids": [1], "fact_ids": [1]}
        first = stage_input_signature("writing", task, plan={
            "id": 7, "plan_version": 2,
            "final_plan_json": {"structure": ["第一章"]},
        })
        second = stage_input_signature("writing", task, plan={
            "id": 7, "plan_version": 3,
            "final_plan_json": {"structure": ["第一章", "第二章"]},
        })
        self.assertNotEqual(first, second)

    def test_boolean_completion_flag_does_not_replace_input_signature(self):
        task = {"analysis_done": True}
        self.assertFalse(signature_matches(task, "analysis", "expected"))


if __name__ == "__main__":
    unittest.main()
