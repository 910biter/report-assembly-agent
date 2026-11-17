import unittest
from unittest.mock import patch
from app.model_connection import Connection
from app.agents.base import BaseAgent, _prepare_prompt
from app.config import settings
from app.vllm_capacity import estimate_request_input

from app.context_budget import (
    ContextSection,
    build_prompt_from_sections,
    consume_context_audit,
    count_tokens,
    truncate_tokens,
)
from app.token_monitor import _aggregate_context_capacity


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        mock = patch("app.model_connection.load_connection", return_value=Connection("http://test/v1", "test"))
        mock.start()
        self.addCleanup(mock.stop)

    def tearDown(self):
        consume_context_audit()

    def test_payment_required_is_not_retried(self):
        self.assertFalse(BaseAgent().should_retry(RuntimeError("MODEL_HTTP_402")))

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

    def test_final_request_overhead_repacks_complete_records(self):
        connection = Connection("http://test/v1", "test", context_tokens=8192, output_tokens=4096)
        system = "Review the supplied records. " * 75
        records = [f"FACT-{index}: " + "alpha\nbeta\n" * 18 for index in range(80)]
        with patch("app.model_connection.load_connection", return_value=connection):
            usable = 8192 - settings.prompt_overhead_tokens - settings.safety_margin_tokens
            budget, output = usable * 45 // 100, usable * 55 // 100
            prompt, initial = build_prompt_from_sections("analysis", [
                ContextSection("instruction", ["Use the records below."], required_items=1),
                ContextSection("records", records, required_items=1),
            ], budget)
            self.assertGreater(initial["sections"]["records"]["selected_items"], 1)
            request = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
            self.assertGreater(estimate_request_input(connection, request) + output + settings.safety_margin_tokens, 8192)

            fitted, audit = _prepare_prompt(prompt, system, output, "analysis", False)
            final_request = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": fitted}]}
            self.assertGreater(audit["request_repack_count"], 0)
            self.assertEqual(audit["effective_output_ceiling"], output)
            self.assertEqual(audit["required_missing_items"], 0)
            self.assertLessEqual(estimate_request_input(connection, final_request) + output + settings.safety_margin_tokens, 8192)
            for record in records:
                if record.split(":", 1)[0] + ":" in fitted:
                    self.assertIn(record.strip(), fitted)

    def test_repack_reports_when_required_context_cannot_fit(self):
        connection = Connection("http://test/v1", "test", context_tokens=4096, output_tokens=2048)
        with patch("app.model_connection.load_connection", return_value=connection):
            prompt, _ = build_prompt_from_sections("analysis", [
                ContextSection("required", ["critical " * 100], required_items=1),
            ], 550)
            with self.assertRaisesRegex(ValueError, "MODEL_REQUIRED_CONTEXT_NOT_FIT"):
                _prepare_prompt(prompt, "system " * 1000, 800, "analysis", False)

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
