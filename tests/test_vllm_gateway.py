import contextvars
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.base import BaseAgent
from app.config import settings
from app.gateway import (
    OpenAICompatibleGateway,
    _completion_was_truncated,
    _context_safe_max_tokens,
    last_generation_stats,
)
from app.evidence.extractor import EvidenceAgent
from app.llm_queue import PRIORITY_INTERACTIVE, llm_priority, submit_llm_call
from app.context import _select_evidence_candidates
from app.retrieval.reranker import rerank
from app.graph.service import _fact_batches
from app.writing.writer import WriterAgent, _pack_writer_evidence


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        }


class _Client:
    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    def post(self, url, headers=None, json=None):
        self.__class__.last_payload = json
        return _Response()

    def close(self):
        pass


class _InvalidJsonResponse(_Response):
    def json(self):
        return {
            "choices": [{"message": {"content": "not-json"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 9},
        }


class _InvalidJsonClient(_Client):
    def post(self, url, headers=None, json=None):
        return _InvalidJsonResponse()


class _ContextOverflowResponse(_Response):
    status_code = 400
    text = (
        '{"error":{"message":"This model maximum context length is 15360 tokens. '
        'The prompt contains at least 12289 input tokens."}}'
    )


class _HealthResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class VllmGatewayTests(unittest.TestCase):
    def test_length_finish_reason_is_never_accepted_as_complete_json(self):
        self.assertTrue(_completion_was_truncated({
            "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
        }))
        self.assertFalse(_completion_was_truncated({
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        }))

    def test_writer_evidence_packing_keeps_complete_lines_and_both_kinds(self):
        facts = ["1. " + "f" * 30, "2. " + "f" * 30, "3. " + "f" * 30]
        inferences = ["10. " + "i" * 20, "11. " + "i" * 20]
        selected_facts, selected_inferences = _pack_writer_evidence(
            facts, inferences, available_chars=100,
        )
        self.assertTrue(selected_facts)
        self.assertTrue(selected_inferences)
        self.assertTrue(all(line in facts for line in selected_facts))
        self.assertTrue(all(line in inferences for line in selected_inferences))

    def test_context_overflow_reduces_only_generation_ceiling(self):
        response = _ContextOverflowResponse()
        with patch.object(settings, "model_context_window_tokens", 15360):
            adjusted = _context_safe_max_tokens(response, requested_max_tokens=3072)
        self.assertEqual(adjusted, 2560)

    def test_evidence_stage_budget_preserves_source_coverage_without_selecting_all(self):
        candidates = []
        unit_id = 1
        for material_id in range(1, 4):
            ranked = []
            for score in (0.9, 0.8, 0.7):
                ranked.append((score, SimpleNamespace(id=unit_id, content="x" * 100)))
                unit_id += 1
            candidates.append((material_id, ranked))
        selected = _select_evidence_candidates(candidates, token_budget=400)
        self.assertEqual([material_id for material_id, _units in selected], [1, 2, 3])
        self.assertEqual(sum(len(units) for _material_id, units in selected), 4)

    def test_rerank_prefers_information_not_already_in_fact_corpus(self):
        repeated = SimpleNamespace(id=1, content="可信执行环境通过远程证明建立信任")
        novel = SimpleNamespace(id=2, content="侧信道攻击会泄露机密状态并破坏隔离")
        ranked = rerank(
            [(0.5, repeated), (0.5, novel)],
            "安全机制分析",
            known_contents={"可信执行环境通过远程证明建立信任"},
        )
        self.assertEqual(ranked[0][1].id, 2)

    def test_graph_batches_preserve_all_facts_with_output_capacity_guard(self):
        facts = [{"id": index, "content": f"fact-{index}"} for index in range(1, 121)]
        with patch.object(settings, "graph_output_tokens", 4096):
            batches = _fact_batches(facts)
        self.assertGreater(len(batches), 1)
        self.assertEqual([item["id"] for batch in batches for item in batch], list(range(1, 121)))
        self.assertTrue(all(len(batch) <= 51 for batch in batches))

    def test_structured_agents_disable_unbounded_hidden_thinking_by_default(self):
        self.assertFalse(BaseAgent.thinking)
        self.assertEqual(BaseAgent.output_token_limit, settings.structured_output_tokens)
        self.assertEqual(WriterAgent.output_token_limit, settings.writer_output_tokens)

    def test_queue_propagates_caller_context(self):
        marker = contextvars.ContextVar("test_queue_marker", default="missing")
        token = marker.set("task-context")
        try:
            self.assertEqual(submit_llm_call(marker.get), "task-context")
        finally:
            marker.reset(token)

    def test_json_generation_propagates_priority_and_thinking_control(self):
        gateway = OpenAICompatibleGateway("http://vllm.test/v1")
        with patch("app.gateway.httpx.Client", _Client), patch.object(
            settings, "generation_model", "qwen3.6-27b"
        ):
            with llm_priority(PRIORITY_INTERACTIVE):
                result = gateway.generate_json("return json", think=False)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(_Client.last_payload["priority"], PRIORITY_INTERACTIVE)
        self.assertFalse(_Client.last_payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(last_generation_stats()["output_tokens"], 5)

    def test_queue_returns_worker_usage_to_calling_context(self):
        gateway = OpenAICompatibleGateway("http://vllm.test/v1")
        with patch("app.gateway.httpx.Client", _Client), patch.object(
            settings, "generation_model", "qwen3.6-27b"
        ):
            result = submit_llm_call(
                lambda: gateway.generate_json("return json", think=False)
            )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(last_generation_stats()["prompt_tokens"], 12)
        self.assertEqual(last_generation_stats()["output_tokens"], 5)

    def test_queue_returns_worker_usage_when_json_parsing_fails(self):
        gateway = OpenAICompatibleGateway("http://vllm.test/v1")
        with patch("app.gateway.httpx.Client", _InvalidJsonClient), patch.object(
            settings, "generation_model", "qwen3.6-27b"
        ):
            with self.assertRaisesRegex(ValueError, "MODEL_JSON_NOT_FOUND"):
                submit_llm_call(
                    lambda: gateway.generate_json("return json", think=False)
                )
        self.assertEqual(last_generation_stats()["prompt_tokens"], 20)
        self.assertEqual(last_generation_stats()["output_tokens"], 9)

    def test_health_checks_generation_and_embedding_backends(self):
        gateway = OpenAICompatibleGateway("http://vllm.test/v1")

        def fake_get(url, **kwargs):
            if url.endswith("/models"):
                return _HealthResponse({"data": [{"id": "qwen3.6-27b"}]})
            return _HealthResponse({"models": [{"name": "qwen-embed:latest"}]})

        with patch("app.gateway.httpx.get", side_effect=fake_get), patch.object(
            settings, "generation_model", "qwen3.6-27b"
        ), patch.object(settings, "embedding_model", "Qwen3-Embedding-0.6B"), patch(
            "app.local_embedding.local_embedding_runtime.health",
            return_value={"backend": "transformers-cpu", "model": "/models/qwen", "available": True, "loaded": False},
        ):
            health = gateway.health()
        self.assertTrue(health["models"]["qwen3.6-27b"])
        self.assertTrue(health["models"]["Qwen3-Embedding-0.6B"])
        self.assertEqual(health["embedding"]["backend"], "transformers-cpu")

    def test_evidence_batches_keep_input_order_under_concurrency(self):
        agent = EvidenceAgent()

        def fake_process(self, needs, text, meta, units, filenames, task_id, index, count, round_tag=0):
            return [text]

        with patch.object(settings, "generation_backend", "vllm"), patch.object(
            settings, "evidence_batch_concurrency", 2
        ), patch.object(EvidenceAgent, "_process_batch", fake_process):
            result = agent._process_batches(
                [("first", {}), ("second", {})], [], {}, {}, "task",
            )
        self.assertEqual(result, [["first"], ["second"]])


if __name__ == "__main__":
    unittest.main()
