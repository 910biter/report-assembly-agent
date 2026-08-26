import unittest
from unittest.mock import patch

from app.config import settings
from app.evidence.extractor import EvidenceAgent, _normalize_conflict_type
from app.runtime_profiles import runtime_profile_manifest, stage_profile


class RuntimeProfilesAndConflictTests(unittest.TestCase):
    def test_stage_profiles_share_physical_invariant_but_keep_distinct_contracts(self):
        profiles = {item["stage"]: item for item in runtime_profile_manifest()}
        for profile in profiles.values():
            total = (
                profile["input_tokens"]
                + profile["output_tokens"]
                + settings.prompt_overhead_tokens
                + settings.safety_margin_tokens
            )
            self.assertEqual(total, settings.model_context_window_tokens)
            self.assertLessEqual(profile["input_chars"], profile["input_tokens"])
        self.assertGreater(profiles["evidence"]["output_tokens"], profiles["conflict"]["output_tokens"])
        self.assertEqual(profiles["writer"]["batch_policy"], "subsection")
        self.assertNotEqual(profiles["analysis"]["workload"], profiles["writer"]["workload"])

    def test_conflict_type_is_closed_protocol_not_free_text(self):
        self.assertEqual(_normalize_conflict_type("direct_contradiction"), "direct_contradiction")
        self.assertEqual(_normalize_conflict_type("模型随意发明的类型"), "needs_verification")

    def test_conflict_detection_excludes_unbound_claims_and_rebuilds_entries(self):
        claims = [
            {"id": 1, "fact_id": 11, "status": "promoted", "content": "同一事项已经完成", "source": "a.pdf"},
            {"id": 2, "fact_id": 12, "status": "promoted", "content": "同一事项尚未完成", "source": "b.pdf"},
            {"id": 3, "fact_id": None, "status": "pending", "content": "未绑定陈述", "source": ""},
        ]
        seen = {}

        def candidate_claims(items):
            seen["candidate_input"] = list(items)
            return list(items)

        agent = EvidenceAgent()
        agent.generate_json = lambda *args, **kwargs: {"conflicts": [{
            "fact_key": "完成状态",
            "claim_ids": [1, 2, 999],
            "conflict_type": "direct_contradiction",
            "reason": "同一事项状态相反",
            "confidence": "high",
        }]}
        agent.last_call_id = "call-conflict"
        entries = [
            {"claim_id": 1, "fact_id": 11, "file": "a.pdf", "page": 1, "quote": "已经完成", "statement": "同一事项已经完成"},
            {"claim_id": 2, "fact_id": 12, "file": "b.pdf", "page": 2, "quote": "尚未完成", "statement": "同一事项尚未完成"},
        ]
        with (
            patch("app.evidence.extractor._conflict_candidate_claims", side_effect=candidate_claims),
            patch("app.evidence.extractor._conflict_candidate_groups", return_value=[{"kind": "status", "signal": "相反", "claim_ids": [1, 2]}]),
            patch("app.evidence.extractor._conflict_entries", return_value=entries),
            patch("app.evidence.extractor.save_conflict", return_value=7),
            patch("app.evidence.extractor.update_call_metrics"),
            patch("app.evidence.extractor.update_call_funnel"),
        ):
            result = agent.detect_conflicts(claims, task_id="task-1")

        self.assertEqual([item["id"] for item in seen["candidate_input"]], [1, 2])
        self.assertEqual(result[0].claim_ids, [1, 2])
        self.assertEqual(result[0].entries, entries)
        self.assertEqual(result[0].conflict_type, "direct_contradiction")
        self.assertEqual(result[0].confidence, "high")
