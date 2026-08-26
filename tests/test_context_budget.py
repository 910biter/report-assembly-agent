import unittest

from app.context_budget import (
    ContextSection,
    build_prompt_from_sections,
    consume_context_audit,
    count_tokens,
    truncate_tokens,
)
from app.token_monitor import _aggregate_context_capacity


class ContextBudgetTests(unittest.TestCase):
    def tearDown(self):
        consume_context_audit()

    def test_weighted_packer_uses_spare_capacity_without_cutting_records(self):
        facts = [f"FACT-{index}-" + "事实" * 20 for index in range(12)]
        prompt, audit = build_prompt_from_sections(
            "final_planner",
            [
                ContextSection("模板", ["模板:无"], weight=1),
                ContextSection("事实", facts, weight=5),
                ContextSection("推论", ["推论:无"], weight=3),
            ],
            budget_tokens=260,
        )

        self.assertLessEqual(count_tokens(prompt), 260)
        self.assertGreater(audit["sections"]["事实"]["selected_items"], 1)
        for line in prompt.splitlines():
            if line.startswith("FACT-"):
                self.assertIn(line, facts)

    def test_token_truncation_respects_budget(self):
        value = "中文上下文" * 100 + " mixed English 123"
        truncated = truncate_tokens(value, 80)

        self.assertLessEqual(count_tokens(truncated), 80)
        self.assertTrue(truncated.endswith("…"))

    def test_capacity_aggregation_reports_stage_distribution_and_sections(self):
        calls = [{
            "stage": "final_planning",
            "agent": "planner",
            "input_tokens": 850,
            "funnel_json": {
                "context_audit": {
                    "stage": "final_planner",
                    "tokenizer_method": "tokenizer:/models/qwen",
                    "budget_tokens": 1000,
                    "actual_tokens": 800,
                    "final_user_prompt_tokens": 810,
                    "estimated_request_tokens": 840,
                    "utilization": 0.8,
                    "truncated": True,
                    "sections": {
                        "事实": {
                            "candidate_items": 20,
                            "selected_items": 12,
                            "omitted_items": 8,
                            "candidate_tokens": 1200,
                            "selected_tokens": 600,
                        }
                    },
                }
            },
        }]

        result = _aggregate_context_capacity(calls)
        stage = result["by_stage"][0]
        self.assertEqual(1, result["audited_calls"])
        self.assertEqual(0.8, stage["utilization_p50"])
        self.assertEqual(1.0, stage["truncation_rate"])
        self.assertEqual(8, stage["sections"]["事实"]["omitted_items"])
        self.assertEqual(40.0, stage["prompt_overhead_tokens_p50"])
        self.assertEqual(-0.012, stage["token_estimate_error_rate_p50"])
        self.assertEqual(0.012, stage["token_estimate_absolute_error_rate_p95"])


if __name__ == "__main__":
    unittest.main()
