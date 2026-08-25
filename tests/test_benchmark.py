import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import benchmark_capture
from app.benchmark_capture import BenchmarkCaptureSink, benchmark_call_context, capture_enrichment, capture_llm_call
from app.config import settings
from app.token_monitor import token_context
from benchmark.dataset import build_dataset, read_captures, select_representative_cases
from benchmark.micro import generate_micro_cases
from benchmark.runner import execute_case, metric_delta, read_cases, run_dataset, validate_output


def _capture(stage: str, prompt_tokens: int, completion_tokens: int, content: str, error: str = "") -> dict:
    return {
        "schema_version": "1.0",
        "captured_at": "2026-08-25T00:00:00Z",
        "context": {"task_id": "task-benchmark", "run_id": "run-1", "stage": stage, "material_count": 2},
        "transport": {"backend": "openai-compatible", "endpoint": "http://test/v1/chat/completions"},
        "request": {
            "model": "test-model",
            "messages": [{"role": "user", "content": "benchmark input"}],
            "max_tokens": 128,
            "response_format": {"type": "json_object"},
        },
        "observed": {
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "content": content,
            "finish_reason": "stop",
            "latency_seconds": 1.2,
            "error": error,
        },
    }


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": '{"content":"ok","unit_id":"UNIT_1"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 8},
        }


class _Client:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, *args, **kwargs):
        return _Response()

    def stream(self, *args, **kwargs):
        return _StreamResponse()


class _StreamResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"{\\"content\\":\\"ok\\","},"finish_reason":null}]}'
        yield 'data: {"choices":[{"delta":{"content":"\\"unit_id\\":\\"UNIT_1\\"}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":8}}'
        yield "data: [DONE]"


class _OpenAIHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(content_length))
        if request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                {"choices": [{"delta": {"content": '{"content":"ok",'}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": '"unit_id":"UNIT_1"}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 8}},
            ]
            for event in events:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        body = {
            "choices": [{"message": {"content": '{"content":"ok","unit_id":"UNIT_1"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 8},
        }
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


class BenchmarkDatasetTests(unittest.TestCase):
    def test_capture_sink_writes_jsonl_without_workflow_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = BenchmarkCaptureSink(Path(tmp), max_queue_size=2)
            self.assertTrue(sink.submit({"case": "one"}))
            sink.queue.join()
            sink.close()
            files = list(Path(tmp).glob("*.jsonl"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text(encoding="utf-8")), {"case": "one"})

    def test_opt_in_capture_joins_gateway_call_and_later_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "benchmark_capture_enabled", True), patch.object(
            settings, "benchmark_capture_task_ids", "benchmark-task"
        ), patch.object(settings, "benchmark_capture_dir", Path(tmp)):
            benchmark_capture.close_capture_sink()
            with token_context(task_id="benchmark-task", run_id="run-1", stage="evidence"):
                with benchmark_call_context(call_id="call-9", logical_call_id="logical-9", agent="evidence", attempt=1):
                    capture_llm_call(
                        backend="openai-compatible", endpoint="http://test/v1/chat/completions",
                        request={"model": "m", "messages": [{"role": "user", "content": "raw input"}], "response_format": {"type": "json_object"}},
                        response={"usage": {"prompt_tokens": 30, "completion_tokens": 9}, "choices": [{"finish_reason": "stop"}]},
                        content='{"content":"fact","unit_id":"U1"}', elapsed_seconds=0.2,
                    )
                capture_enrichment("call-9", "funnel", {"model_claims": 4, "promoted_facts": 2})
            sink = benchmark_capture._sink
            self.assertIsNotNone(sink)
            sink.queue.join()
            benchmark_capture.close_capture_sink()
            calls = read_captures(list(Path(tmp).glob("*.jsonl")))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].baseline["token_funnel"]["promoted_facts"], 2)
            self.assertEqual(calls[0].baseline["correlation"]["attempt"], 1)

    def test_builder_selects_p50_p95_max_and_failure_per_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_path = Path(tmp) / "capture.jsonl"
            records = [
                _capture("evidence", 100, 10, '{"content":"a","unit_id":"U1"}'),
                _capture("evidence", 200, 20, '{"content":"b","unit_id":"U2"}'),
                _capture("evidence", 300, 30, '{"content":"c","unit_id":"U3"}'),
                _capture("evidence", 400, 0, "", error="MODEL_TIMEOUT"),
            ]
            capture_path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
            calls = read_captures([capture_path])
            cases = select_representative_cases(calls)
            self.assertEqual({case["load_class"] for case in cases}, {"p50", "p95", "max", "failure"})
            manifest = build_dataset([capture_path], Path(tmp) / "dataset")
            self.assertEqual(manifest["case_count"], 4)
            self.assertEqual(len(read_cases(Path(tmp) / "dataset" / "stage-replay.jsonl")), 4)

    def test_builder_exports_resource_timeline_as_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_path = Path(tmp) / "capture.jsonl"
            payloads = [
                _capture("analysis", 120, 20, '{"dimensions":[]}'),
                {"event_type": "resource_sample", "captured_at": "2026-08-25T00:00:01Z", "resource": {"cpu_percent": 42}, "server_metrics": {"vllm_prompt_tokens_total": 100}},
            ]
            capture_path.write_text("".join(json.dumps(item) + "\n" for item in payloads), encoding="utf-8")
            output = Path(tmp) / "dataset"
            manifest = build_dataset([capture_path], output)
            self.assertEqual(manifest["resource_sample_count"], 1)
            self.assertIn("baseline-resources.jsonl", manifest["files"])
            self.assertTrue((output / "baseline-resources.jsonl").exists())

    def test_builder_merges_post_call_funnel_into_standalone_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_path = Path(tmp) / "capture.jsonl"
            raw = _capture("writing", 200, 20, '{"paragraphs":[]}')
            raw["correlation"] = {"call_id": "call-1", "logical_call_id": "logical-1", "agent": "writer", "attempt": 1}
            enrichments = [
                {"event_type": "enrichment", "call_id": "call-1", "kind": "call_log", "payload": {"retry_count": 1, "model_timing": {"output_eval_seconds": 2.0}}},
                {"event_type": "enrichment", "call_id": "call-1", "kind": "funnel", "payload": {"model_sentences": 12, "accepted_sentences": 10}},
                {"event_type": "enrichment", "call_id": "call-1", "kind": "products", "payload": {"produced_chapter_ids": "[9]"}},
            ]
            capture_path.write_text(
                "".join(json.dumps(item) + "\n" for item in [raw, *enrichments]), encoding="utf-8",
            )
            case = select_representative_cases(read_captures([capture_path]))[0]
            self.assertEqual(case["baseline"]["correlation"]["logical_call_id"], "logical-1")
            self.assertEqual(case["baseline"]["token_funnel"]["accepted_sentences"], 10)
            self.assertEqual(case["baseline"]["artifact_products"]["produced_chapter_ids"], "[9]")

    def test_runner_validates_json_contract_with_server_usage(self):
        case = {
            "case_id": "evidence-p50-001",
            "stage": "evidence",
            "workload": "evidence_extraction",
            "request": {"messages": [{"role": "user", "content": "x"}], "generation": {"model": "m", "max_tokens": 32}},
            "shape": {"reference_output_tokens": {"minimum": 1, "maximum": 10}},
            "validators": {"expect_success": True, "expect_json_object": True, "required_top_level_fields": ["content", "unit_id"]},
        }
        with patch("benchmark.runner.httpx.Client", _Client):
            result = execute_case(case, "http://test/v1", stream=False)
        self.assertTrue(result["success"])
        self.assertTrue(result["validation"]["passed"])
        self.assertEqual(result["metrics"]["reported_prompt_tokens"], 100)

    def test_validation_keeps_missing_usage_as_unknown_not_failed(self):
        case = {"shape": {"reference_output_tokens": {"minimum": 1, "maximum": 2}}, "validators": {"expect_success": True}}
        validation = validate_output(case, {"success": True, "content": "ok", "usage": {}})
        self.assertTrue(validation["passed"])
        self.assertIsNone(validation["checks"]["output_token_range"])

    def test_stream_runner_records_ttft_and_decode_separately(self):
        case = {
            "case_id": "stream-001",
            "stage": "writing",
            "workload": "document_generation",
            "request": {"messages": [{"role": "user", "content": "x"}], "generation": {"model": "m", "max_tokens": 32}},
            "shape": {"reference_output_tokens": {"minimum": 1, "maximum": 10}},
            "validators": {"expect_success": True, "expect_json_object": True, "required_top_level_fields": ["content", "unit_id"]},
        }
        with patch("benchmark.runner.httpx.Client", _Client):
            result = execute_case(case, "http://test/v1", stream=True)
        self.assertIsNotNone(result["metrics"]["ttft_seconds"])
        self.assertIsNotNone(result["metrics"]["decode_tokens_per_second"])
        self.assertTrue(result["validation"]["passed"])

    def test_runner_replays_case_through_real_openai_compatible_http_server(self):
        case = {
            "case_id": "http-001", "stage": "evidence", "workload": "evidence_extraction",
            "request": {"messages": [{"role": "user", "content": "x"}], "generation": {"model": "m", "max_tokens": 32}},
            "shape": {"reference_output_tokens": {"minimum": 1, "maximum": 10}},
            "validators": {"expect_success": True, "expect_json_object": True, "required_top_level_fields": ["content", "unit_id"]},
            "baseline": {"application_call": {"latency_ms": 1000, "output_tokens": 8, "model_timing": {"output_eval_seconds": 1}}},
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = run_dataset(
                [case], f"http://127.0.0.1:{server.server_port}/v1", "", "", 1, 1,
                True, 10, 0.1, False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(result["results"][0]["validation"]["passed"])
        self.assertIsNotNone(result["results"][0]["baseline_comparison"]["current_to_baseline_decode_ratio"])

    def test_micro_cases_are_business_free_and_cover_matrix(self):
        cases = generate_micro_cases([1024, 4096], [128, 512], "test-model")
        self.assertEqual(len(cases), 4)
        self.assertTrue(all(case["reference"]["synthetic"] for case in cases))
        self.assertTrue(all(case["request"]["generation"]["model"] == "test-model" for case in cases))

    def test_server_metric_delta_keeps_only_monotonic_counters(self):
        self.assertEqual(
            metric_delta({"prompt_tokens": 100.0, "gauge": 9.0}, {"prompt_tokens": 160.0, "gauge": 4.0}),
            {"prompt_tokens": 60.0},
        )


if __name__ == "__main__":
    unittest.main()
