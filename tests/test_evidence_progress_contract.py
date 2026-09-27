from app.workflow.controller import _evidence_checkpoint_complete
from app.workflow.progress import progress_for_display


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


def test_invalid_historical_progress_is_not_presented_as_a_valid_ratio():
    progress = progress_for_display({"done": 22, "total": 3, "status": "running"})

    assert progress == {"status": "stale"}


def test_valid_progress_is_preserved_for_display():
    progress = progress_for_display({"done": 2, "total": 3, "status": "running", "unit": "phase"})

    assert progress == {"done": 2, "total": 3, "status": "running", "unit": "phase"}
