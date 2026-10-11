from unittest.mock import patch

import pytest

from app.evidence.extractor import SourceUnitIndex, EvidenceAgent, _split_unit_blocks
from app.evidence.outcomes import summarize
from app.models.material import Unit
from app.writing.evidence_references import repair_references, EvidenceReferenceBlocked
from app.context_budget import TokenCounter


def source(text):
    return SourceUnitIndex({1: [Unit(1, "text", text, id=7)]}, {1: "source.txt"})


def test_binding_never_reads_hidden_source_tail():
    index = source("visible text\n\nhidden evidence")
    view = index.visible("[U7]\nvisible text")
    assert view.bind("hidden evidence", 7) == []
    bound = view.bind("visible text", 7)[0]
    assert bound.visibility["spans"][0]["original_quote_start"] == 0
    assert bound.visibility["spans"][0]["original_quote_end"] == len("visible text")


def test_discontinuous_quote_requires_all_fragments():
    view = source("第一条。第二条。").visible("[U7]\n第一条。第二条。")
    assert view.bind("第一条。不存在。") == []


@pytest.mark.parametrize("separator", ["...", "…", "……"])
def test_omitted_quote_records_exact_visible_spans(separator):
    view = source("First evidence, intervening words, final evidence.").visible(
        "[U7]\nFirst evidence, intervening words, final evidence.")
    bound = view.bind(f"First evidence{separator}final evidence.", 7)
    assert [item.quote for item in bound] == ["First evidence", "final evidence."]
    assert all(item.visibility["quote_kind"] == "multiple_source_spans" for item in bound)
    assert [item.visibility["spans"][0]["original_quote_start"] for item in bound] == [0, 35]
    assert view.bind(f"First evidence{separator}invented evidence.", 7) == []


def test_discontinuous_quote_cannot_escape_declared_unit_or_visible_text():
    index = SourceUnitIndex({1: [Unit(1, "text", "visible first; hidden tail", id=7),
                               Unit(1, "text", "other evidence", id=8)]}, {1: "source.txt"})
    view = index.visible("[U7]\nvisible first;\n[U8]\nother evidence")
    assert view.bind("visible first...other evidence", 7) == []
    assert view.bind("visible first...hidden tail", 7) == []


def test_slices_preserve_unit_id_and_original_offsets():
    text = "重复内容。" * 60
    index = source(text)
    with patch("app.evidence.extractor.count_tokens", len):
        parts = _split_unit_blocks("[U7]\n" + text, 85)
    assert len(parts) > 1
    offsets = []
    for part in parts:
        assert len(part) <= 85
        view = index.visible(part)
        assert view.segments()
        offsets.append(view.segments()[0]["start"])
        quote = view.spans[7][0]["text"]
        assert view.bind(quote, 7)
    assert offsets[-1] > offsets[0]


def test_markers_do_not_contaminate_visible_source():
    view = source("证据正文").visible("[U7]\n证据正文\n\n[上下文预算提示]另有内容")
    assert view.bind("证据正文", 7)


def test_status_distinguishes_failure_from_no_evidence():
    agent = EvidenceAgent()
    segments = [{"unit_id": 7, "start": 0, "end": 10, "segment_id": "x"}]
    agent._record_outcome("", 1, 0, segments, [], reason="output_truncated")
    assert summarize(agent.batch_outcomes)["status"] == "failed"
    agent._record_outcome("", 1, 1, segments, segments, no_evidence=True)
    result = summarize(agent.batch_outcomes)
    assert result["status"] == "completed"
    assert not result["unprocessed_segments"]
    assert agent.batch_outcomes[-1]["status"] == "no_evidence"


def test_partial_range_recovery_does_not_erase_remaining_work():
    segment = {"unit_id": 7, "start": 0, "end": 10, "segment_id": "x"}
    result = summarize([
        {"status": "failed", "processed_segments": [], "unprocessed_segments": [segment], "retryable": True},
        {"status": "completed", "processed_segments": [{**segment, "end": 5}], "unprocessed_segments": [], "retryable": False},
    ])
    assert result["status"] == "partial"
    assert result["unprocessed_segments"]


def test_writer_restores_only_explicit_task_owned_reference():
    requests = []
    def load(table, task_id, identifier):
        requests.append((task_id, identifier))
        return {"id": identifier, "content": "recovered", "lifecycle_status": "active"}
    original = {"fact_ids": [7], "paragraphs": [{"fact_ids": [7]}]}
    plan, facts, _, audit = repair_references(original, [{"id": 9}], [], "task-a", load=load)
    assert requests == [("task-a", 7)]
    assert plan == original
    assert {row["id"] for row in facts} == {7, 9}
    assert audit == [{"kind": "fact", "from_id": 7, "to_id": 7}]


def test_writer_follows_explicit_replacement_and_rewrites_nested_plan():
    rows = {7: {"id": 7, "superseded_by_fact_id": 8}, 8: {"id": 8}}
    original = {"fact_ids": [7], "paragraphs": [{"fact_ids": [7]}]}
    plan, _, _, _ = repair_references(original, [], [], "task", load=lambda _, __, identifier: rows.get(identifier))
    assert plan["paragraphs"][0]["fact_ids"] == [8]
    assert original["fact_ids"] == [7]


@pytest.mark.parametrize("rows", [{}, {7: {"id": 7, "superseded_by_fact_id": 7}}])
def test_writer_blocks_missing_or_cyclic_references(rows):
    with pytest.raises(EvidenceReferenceBlocked):
        repair_references({"fact_ids": [7]}, [{"id": 9}], [], "task", load=lambda _, __, identifier: rows.get(identifier))


def test_packing_counter_does_not_load_tokenizer_or_call_network():
    counter = TokenCounter()
    from unittest.mock import patch
    with patch("httpx.post", side_effect=AssertionError("Packing must be network-free")):
        assert counter.count("hello") > 0
    assert counter.method.startswith("estimated")


def test_article_beats_references_are_not_missed():
    with pytest.raises(EvidenceReferenceBlocked):
        repair_references({"beats": [{"fact_ids": [7]}]}, [], [], "task", load=lambda *args: None)
