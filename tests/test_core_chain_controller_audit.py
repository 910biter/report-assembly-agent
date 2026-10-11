from contextlib import contextmanager
from types import MethodType
from unittest.mock import patch

import pytest

from app.workflow.controller import WorkflowController, _article_scale_completion


class _EmptyResult:
    def all(self):
        return []


class _EmptySession:
    def execute(self, _statement):
        return _EmptyResult()


@contextmanager
def _fake_session_scope():
    yield _EmptySession()


def test_core_chain_audit_is_run_scoped_and_separates_material_inferences():
    controller = object.__new__(WorkflowController)
    controller.task_id = "task-1"
    controller.task = {"run_id": "run-1", "analysis_group_audit": {"by_scope": {}}}
    controller._facts = MethodType(lambda self: [{"id": 1}, {"id": 2}], controller)
    controller._inferences = MethodType(lambda self: [
        {"id": 10, "source_level": "MATERIAL_INFERENCE"},
        {"id": 11, "source_level": "EXTERNAL_INFORMATION"},
    ], controller)
    calls = [
        {"call_id": "evidence-1", "agent": "evidence", "stage": "evidence", "success": 1,
         "produced_fact_ids": [1, 2], "funnel_json": {"input_unit_ids": [51, 52]}},
        {"call_id": "analysis-1", "agent": "analysis", "stage": "analysis", "success": 1,
         "produced_inference_ids": [10, 11], "funnel_json": {
             "analysis_scope": "local", "input_fact_ids": [1, 2], "referenced_fact_ids": [1],
             "material_inference_ids": [10], "external_information_ids": [11],
         }},
        {"call_id": "plan-1", "agent": "planner", "stage": "final_plan", "success": 1,
         "funnel_json": {"final_plan_input_fact_ids": [1, 2], "final_plan_assigned_fact_ids": [1],
                         "final_plan_input_inference_ids": [10], "final_plan_assigned_inference_ids": [10]}},
        {"call_id": "narrative-1", "agent": "narrative", "stage": "narrative", "success": 1,
         "funnel_json": {"narrative_scope": "chapter", "narrative_assigned_fact_ids": [1],
                         "narrative_assigned_inference_ids": [10]}},
        {"call_id": "writer-1", "agent": "writer", "stage": "writer", "success": 1,
         "funnel_json": {"generation_mode": "chaptered", "supplied_fact_ids": [1],
                         "cited_fact_ids": [1], "supplied_inference_ids": [10],
                         "cited_inference_ids": [10]}},
    ]
    saved = {}

    def capture_save(_task_id, stage, _inputs, payload, **kwargs):
        saved.update(stage=stage, payload=payload, kwargs=kwargs)

    with patch("app.workflow.controller.list_llm_calls", return_value=calls), \
         patch("app.workflow.controller.session_scope", _fake_session_scope), \
         patch("app.workflow.controller.save_task_artifact", side_effect=capture_save):
        summary = controller._save_core_chain_audit(
            {"chapter_plans": []}, 7, [], {"actual_words": 1},
        )

    assert summary["counts"]["facts_produced_this_run"] == 2
    assert summary["counts"]["material_inferences_produced_this_run"] == 1
    assert summary["counts"]["external_information_items"] == 1
    assert saved["stage"] == "core_chain_audit"
    assert saved["kwargs"]["run_id"] == "run-1"
    assert saved["payload"]["fact_id_flow"]["stages"]["writer_cited"]["ids"] == [1]
    assert saved["payload"]["evidence"]["input_unit_ids"] == [51, 52]


def test_article_completion_rate_uses_the_same_actual_words_as_display():
    actual_words, completion_rate = _article_scale_completion(
        execution_actual_words=800,
        report_actual_words=400,
        target_words=1000,
    )

    assert actual_words == 800
    assert completion_rate == 0.8


def test_core_chain_audit_refuses_unscoped_calls_when_run_id_is_missing():
    controller = object.__new__(WorkflowController)
    controller.task_id = "task-legacy"
    controller.task = {"run_id": ""}
    saved = []

    with patch("app.workflow.controller.list_llm_calls", return_value=[]) as list_calls, \
         patch("app.workflow.controller.save_task_artifact", side_effect=lambda *a, **k: saved.append(a)):
        result = controller._save_core_chain_audit({}, None, [], {})

    assert result["status"] == "unavailable"
    assert result["reason"] == "missing_run_id"
    list_calls.assert_not_called()
    assert saved == []


def test_workflow_failure_persists_a_run_scoped_audit_without_masking_error():
    controller = object.__new__(WorkflowController)
    controller.task_id = "task-failed"
    controller.task = {"run_id": "run-failed", "report_id": None}
    saved = []

    def fail(_self):
        raise RuntimeError("MODEL_OUTPUT_TRUNCATED")

    controller._run_to_review = MethodType(fail, controller)
    controller._save_failed_core_chain_audit = MethodType(
        lambda _self, exc: saved.append(str(exc)), controller,
    )

    with pytest.raises(RuntimeError, match="MODEL_OUTPUT_TRUNCATED"):
        controller.run_to_review()

    assert saved == ["MODEL_OUTPUT_TRUNCATED"]


def test_workflow_pause_does_not_record_a_failed_run_audit():
    controller = object.__new__(WorkflowController)
    controller.task_id = "task-paused"
    controller.task = {"run_id": "run-paused"}
    saved = []

    def pause(_self):
        raise RuntimeError("TASK_PAUSED")

    controller._run_to_review = MethodType(pause, controller)
    controller._save_failed_core_chain_audit = MethodType(
        lambda _self, exc: saved.append(str(exc)), controller,
    )

    with pytest.raises(RuntimeError, match="TASK_PAUSED"):
        controller.run_to_review()

    assert saved == []


def test_failed_core_chain_audit_keeps_failed_writer_unit_scale_and_evidence():
    controller = object.__new__(WorkflowController)
    controller.task_id = "task-failed"
    controller.task = {"run_id": "run-failed", "analysis_group_audit": {}}
    controller._facts = MethodType(lambda self: [{"id": 1}, {"id": 2}], controller)
    controller._inferences = MethodType(lambda self: [], controller)
    unit_stats = {
        "generation_mode": "article_beats",
        "unit_id": "beat-group-2",
        "target_words": 300,
        "actual_words": 0,
        "underfilled": True,
        "underfill_reason": "generation_failed_or_empty_output",
        "planned_fact_ids": [1, 2],
        "supplied_fact_ids": [1],
        "cited_fact_ids": [],
        "planned_inference_ids": [],
        "supplied_inference_ids": [],
        "cited_inference_ids": [],
    }
    calls = [{
        "call_id": "writer-failed",
        "agent": "writer",
        "stage": "writer",
        "success": 0,
        "error": "MODEL_OUTPUT_TRUNCATED",
        "funnel_json": {
            "generation_mode": "article_beats",
            "supplied_fact_ids": [1],
            "generation_unit_stats": [unit_stats],
        },
    }]
    saved = {}

    def capture_save(_task_id, stage, _inputs, payload, **kwargs):
        saved.update(stage=stage, payload=payload, kwargs=kwargs)

    with patch("app.workflow.controller.list_llm_calls", return_value=calls), \
         patch("app.workflow.controller.save_task_artifact", side_effect=capture_save):
        summary = controller._save_core_chain_audit(
            {}, None, [], {}, status="failed", error="MODEL_OUTPUT_TRUNCATED",
        )

    assert summary["status"] == "failed"
    assert summary["error"] == "MODEL_OUTPUT_TRUNCATED"
    assert saved["kwargs"]["status"] == "failed"
    assert saved["payload"]["report_scale"]["target_words"] == 300
    assert saved["payload"]["generation_unit_stats"][0]["planned_fact_ids"] == [1, 2]
    assert saved["payload"]["generation_unit_stats"][0]["supplied_fact_ids"] == [1]
