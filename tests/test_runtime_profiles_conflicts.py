import unittest
from unittest.mock import patch

from app.config import settings
from app.model_connection import Connection
from app.evidence.extractor import (
    EvidenceAgent, _conflict_candidate_claims, _conflict_candidate_groups,
    _conflict_prompt_batches, _normalize_conflict_type, _split_unit_blocks,
)
from app.runtime_profiles import runtime_profile_manifest, stage_profile


class RuntimeProfilesAndConflictTests(unittest.TestCase):
    def setUp(self):
        mock = patch("app.model_connection.load_connection", side_effect=lambda: Connection("http://test/v1", "test", context_tokens=settings.model_context_window_tokens, output_tokens=settings.generation_reserve_tokens))
        mock.start()
        self.addCleanup(mock.stop)

    def test_stage_profiles_share_physical_invariant_but_keep_distinct_contracts(self):
        profiles = {item["stage"]: item for item in runtime_profile_manifest()}
        for profile in profiles.values():
            total = (
                profile["input_tokens"]
                + profile["output_tokens"]
                + settings.prompt_overhead_tokens
                + settings.safety_margin_tokens
            )
            self.assertLessEqual(total, settings.model_context_window_tokens)
        self.assertGreater(profiles["evidence"]["output_tokens"], profiles["conflict"]["output_tokens"])
        self.assertEqual(profiles["writer"]["batch_policy"], "subsection")
        self.assertNotEqual(profiles["analysis"]["workload"], profiles["writer"]["workload"])
        self.assertEqual(profiles["interaction"]["input_tokens"], settings.interactive_input_tokens)

    def test_evidence_split_preserves_complete_units(self):
        source = (
            "相关材料片段:\n"
            "[U1 | a.pdf | 第1页]\n第一条材料内容\n\n"
            "[U2 | a.pdf | 第2页]\n第二条材料内容\n\n"
            "[U3 | a.pdf | 第3页]\n第三条材料内容"
        )
        parts = _split_unit_blocks(source, 20)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(part.startswith("相关材料片段:\n") for part in parts))
        self.assertEqual("\n".join(parts).count("[U"), 3)

    def test_evidence_truncation_is_split_instead_of_blind_retry(self):
        agent = EvidenceAgent()
        agent._split_truncated_batch = True
        self.assertFalse(agent.should_retry(RuntimeError("MODEL_OUTPUT_TRUNCATED")))
        self.assertTrue(agent.should_retry(RuntimeError("MODEL_HTTP_503")))

    def test_conflict_type_is_closed_protocol_not_free_text(self):
        self.assertEqual(_normalize_conflict_type("direct_contradiction"), "direct_contradiction")
        self.assertEqual(_normalize_conflict_type("模型随意发明的类型"), "needs_verification")

    def test_conflict_candidates_are_not_silently_capped(self):
        claims = [
            {"id": index, "fact_id": index, "content": f"同一事项在{index}年已完成"}
            for index in range(1, 48)
        ]
        candidates = _conflict_candidate_claims(claims)
        self.assertEqual(len(candidates), len(claims))
        groups = [
            {"kind": "date_mismatch", "signal": "test", "claim_ids": [index, index + 1]}
            for index in range(1, 25, 2)
        ]
        batches = _conflict_prompt_batches(candidates[:25], groups, input_budget=1200, output_tokens=500)
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(visible_ids) >= 2 for _, visible_ids, _ in batches))
        self.assertEqual(set(range(1, 25)), set().union(*(ids for _, ids, _ in batches)))

    def test_conflict_group_prefilter_keeps_groups_past_old_limit(self):
        claims = []
        for index in range(15):
            topic = chr(0x4e00 + index) * 6
            claims.extend([
                {"id": index * 2 + 1, "content": f"{topic}预算为100元"},
                {"id": index * 2 + 2, "content": f"{topic}预算为200元"},
            ])
        groups = _conflict_candidate_groups(claims)
        self.assertGreater(len(groups), 12)

    def test_conflict_model_failure_is_not_reported_as_no_conflict(self):
        claims = [
            {"id": 1, "fact_id": 11, "status": "promoted", "content": "同一事项已经完成"},
            {"id": 2, "fact_id": 12, "status": "promoted", "content": "同一事项尚未完成"},
        ]
        agent = EvidenceAgent()
        agent.generate_json = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("MODEL_HTTP_503"))
        with self.assertRaisesRegex(RuntimeError, "MODEL_HTTP_503"):
            agent.detect_conflicts(claims, task_id="task-1")

    def test_conflict_schema_is_retried_once_before_marking_incomplete(self):
        claims = [
            {"id": 1, "fact_id": 11, "status": "promoted", "content": "同一事项已经完成"},
            {"id": 2, "fact_id": 12, "status": "promoted", "content": "同一事项尚未完成"},
        ]
        agent = EvidenceAgent()
        replies = iter([{"message": "未发现冲突"}, {"conflicts": []}])
        agent.generate_json = lambda *args, **kwargs: next(replies)
        agent.last_call_id = "call-conflict"
        with (
            patch("app.evidence.extractor._conflict_candidate_groups", return_value=[
                {"kind": "status", "signal": "相反", "claim_ids": [1, 2]},
            ]),
            patch("app.evidence.extractor.update_call_metrics"),
            patch("app.evidence.extractor.update_call_funnel") as funnel,
        ):
            self.assertEqual(agent.detect_conflicts(claims, task_id="task-1"), [])
        self.assertTrue(any(call.kwargs.get("invalid_conflict_schema") == 1 for call in funnel.call_args_list))

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
