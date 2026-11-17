from app.workflow.controller import _evidence_checkpoint_complete, _evidence_extraction_incomplete
from app.workflow.progress import progress_for_display
from app.workflow.stage_state import stage_input_signature


def test_writing_signature_tracks_effective_plan_even_with_snapshot():
    plan = {"id": 1, "final_plan_json": {"structure": ["old"]},
            "chapter_plans": [{"title": "old"}], "budget": {"target_words": 100}}
    changed = {**plan, "chapter_plans": [{"title": "new"}]}
    assert stage_input_signature("writing", {}, plan=plan) != stage_input_signature("writing", {}, plan=changed)


def test_legacy_progress_ratio_does_not_mark_evidence_complete():
    task = {
        "fact_ids": [11, 12],
        "evidence_active_signature": "input-v1",
        "evidence_progress": {"done": 22, "total": 3, "status": "running"},
    }

    assert not _evidence_checkpoint_complete(task, "input-v1")


def test_completed_evidence_signature_allows_reuse_independent_of_progress_counter():
    task = {
        "fact_ids": [11, 12],
        "evidence_input_signature": "input-v1",
        "evidence_progress": {"done": 3, "total": 3, "status": "completed"},
    }

    assert _evidence_checkpoint_complete(task, "input-v1")


def test_evidence_completion_signature_must_match_current_inputs():
    task = {
        "fact_ids": [11, 12],
        "evidence_input_signature": "old-input",
        "evidence_progress": {"done": 3, "total": 3, "status": "completed"},
    }

    assert not _evidence_checkpoint_complete(task, "new-input")


def test_incomplete_evidence_cannot_be_reused_even_with_matching_signature():
    task = {"fact_ids": [1], "evidence_input_signature": "input-v1",
            "evidence_status": {"status": "partial", "unprocessed_segments": [{"unit_id": 4}]}}
    assert not _evidence_checkpoint_complete(task, "input-v1")


def test_completed_empty_evidence_can_be_reused():
    task = {"fact_ids": [], "evidence_input_signature": "input-v1",
            "evidence_status": {"status": "completed", "unprocessed_segments": []}}
    assert _evidence_checkpoint_complete(task, "input-v1")


def test_partial_extraction_without_ranges_is_not_treated_as_complete():
    assert _evidence_extraction_incomplete({"status": "partial", "unprocessed_segments": []})


def test_unverified_coverage_does_not_imply_incomplete_extraction():
    assert not _evidence_extraction_incomplete({"status": "partial", "processing_status": "completed", "unprocessed_segments": []})


def test_invalid_historical_progress_is_not_presented_as_a_valid_ratio():
    progress = progress_for_display({"done": 22, "total": 3, "status": "running"})

    assert progress == {"status": "stale"}


def test_valid_progress_is_preserved_for_display():
    progress = progress_for_display({"done": 2, "total": 3, "status": "running", "unit": "phase"})

    assert progress == {"done": 2, "total": 3, "status": "running", "unit": "phase"}
