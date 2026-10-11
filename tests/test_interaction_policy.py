import unittest

from app.interaction_policy import propagation_policy


class InteractionPolicyTests(unittest.TestCase):
    def test_requirements_recompute_all_semantic_downstream(self):
        policy = propagation_policy("revise_task_requirements", "task_brief")
        self.assertEqual(policy.recompute_from, "planning")
        self.assertTrue(policy.force_evidence)
        self.assertTrue(policy.force_analysis)
        self.assertTrue(policy.force_final_plan)

    def test_structure_change_does_not_recompute_evidence_or_analysis(self):
        policy = propagation_policy("rerun_final_plan", "final_plan")
        self.assertEqual(policy.recompute_from, "final_plan")
        self.assertFalse(policy.force_evidence)
        self.assertFalse(policy.force_analysis)
        self.assertTrue(policy.force_final_plan)

    def test_local_rewrite_stays_local(self):
        paragraph = propagation_policy("rewrite_paragraph", "paragraph")
        sentence = propagation_policy("rewrite_sentence", "sentence")
        self.assertEqual(paragraph.scope_kind, "paragraph")
        self.assertEqual(sentence.scope_kind, "sentence")
        self.assertNotIn("final_plan", paragraph.invalidates)
        self.assertNotIn("analysis", sentence.invalidates)

    def test_chapter_rewrite_does_not_expand_to_whole_report(self):
        policy = propagation_policy("regenerate_chapter", "narrative_plan")
        self.assertEqual(policy.scope_kind, "chapter")
        self.assertEqual(policy.recompute_from, "writing")
        self.assertFalse(policy.force_final_plan)


if __name__ == "__main__":
    unittest.main()
