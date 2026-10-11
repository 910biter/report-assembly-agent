import unittest
from unittest.mock import patch
from types import SimpleNamespace
from app.config import settings

from app.analysis.analyzer import (
    _GLOBAL_SYSTEM,
    _SYSTEM,
    _semantic_group_coverage,
    _semantic_group_overview,
    _target_local_inference_count,
)


def _fact(fact_id: int, need_id: int, fact_type: str = "STATEMENT") -> dict:
    return {
        "id": fact_id,
        "dimension": "D",
        "need_id": need_id,
        "fact_type": fact_type,
        "content": f"fact {fact_id}",
        "sources": ["source"],
    }


class AnalysisCoverageTests(unittest.TestCase):
    def setUp(self):
        profile = patch("app.analysis.analyzer.stage_profile", side_effect=lambda _: SimpleNamespace(output_tokens=settings.analysis_output_tokens))
        profile.start()
        self.addCleanup(profile.stop)

    def test_extra_output_room_does_not_increase_target_count(self):
        with patch.object(settings, "analysis_output_tokens", 3072):
            original = _target_local_inference_count(420, group_count=40)
        with patch.object(settings, "analysis_output_tokens", 5120):
            self.assertEqual(_target_local_inference_count(420, group_count=40), original)

    def test_local_and_global_inference_prompts_preserve_fact_scope(self):
        for prompt in (_SYSTEM, _GLOBAL_SYSTEM):
            with self.subTest(prompt=prompt[:24]):
                self.assertIn("范围", prompt)
                self.assertIn("时间", prompt)
                self.assertIn("条件", prompt)
                self.assertIn("来源归属", prompt)

    def test_target_count_grows_with_semantic_groups_within_output_capacity(self):
        self.assertEqual(_target_local_inference_count(100, group_count=1), 4)
        self.assertEqual(_target_local_inference_count(100, group_count=8), 8)
        self.assertEqual(_target_local_inference_count(420, group_count=40), 8)

    def test_overview_exposes_generic_fact_group_boundaries(self):
        facts = [_fact(1, 10), _fact(2, 10), _fact(3, 20)]

        overview, group_count = _semantic_group_overview(facts)

        self.assertEqual(group_count, 2)
        self.assertIn("事实组概览", overview)
        self.assertIn("事实数=2", overview)
        self.assertIn("事实ID=1,2", overview)

    def test_coverage_audit_counts_groups_not_only_facts(self):
        facts = [_fact(1, 10), _fact(2, 10), _fact(3, 20)]
        inferences = [{"based_fact_ids": [1, 2]}]

        audit = _semantic_group_coverage(facts, inferences)

        self.assertEqual(audit["total_groups"], 2)
        self.assertEqual(audit["covered_groups"], 1)
        self.assertEqual(audit["coverage_ratio"], 0.5)
        self.assertEqual(audit["referenced_fact_ids"], [1, 2])
        self.assertEqual(audit["unreferenced_fact_ids"], [3])
        self.assertIn("diagnostic, not a target", audit["coverage_semantics"])
        self.assertEqual(audit["uncovered_groups"][0]["fact_ids"], [3])


if __name__ == "__main__":
    unittest.main()
