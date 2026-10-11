from types import SimpleNamespace

import pytest

from app.context import ContextManager
from app.context_budget import count_tokens
from app.evidence import extractor
from app.models import Fact, Unit
from app.planning import narrative
from app.writing.writer import _pack_writer_evidence, _subsection_generation_units


def test_foreign_vector_hits_do_not_suppress_current_material_retrieval(monkeypatch):
    from app.retrieval import vector_store

    unit = Unit(material_id=7, id=12, kind="text", content="Relevant source evidence")
    manager = ContextManager({}, {7: [unit]}, {7: "source.pdf"})
    calls = []

    def search(vector, top_k, filters=None):
        calls.append(filters)
        return [(999, 0.9)]

    monkeypatch.setattr(vector_store, "enabled", True)
    monkeypatch.setattr(vector_store, "unit_vectors", lambda ids: {})
    monkeypatch.setattr(vector_store, "search_units", search)
    monkeypatch.setattr(manager._query_compiler, "query_vector", lambda query: [1.0])
    monkeypatch.setattr("app.context._unit_relevance", lambda *args: 1.0)
    monkeypatch.setattr("app.retrieval.reranker.rerank", lambda items, *args, **kwargs: items)

    batches = manager.for_first_pass([{"need": "source evidence"}], [])

    assert calls == [{"material_id": 7}]
    assert any("[U12 |" in text for text, _ in batches)


@pytest.fixture
def isolated_extraction(monkeypatch):
    monkeypatch.setattr(extractor, "_assert_not_paused", lambda task_id: None)
    for name in ("update_call_funnel", "update_call_metrics", "update_call_products"):
        monkeypatch.setattr(extractor, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(extractor.EvidenceAgent, "_record_material_scan", lambda *args: None)
    monkeypatch.setattr(extractor, "_identity_candidates", lambda *args: [])
    monkeypatch.setattr(extractor, "match_propositions", lambda *args: ({}, {}))
    monkeypatch.setattr(extractor, "save_fact_with_evidence", lambda *args, **kwargs: 123)
    monkeypatch.setattr(extractor, "save_claim", lambda *args, **kwargs: None)
    monkeypatch.setattr(extractor, "embed_texts", lambda texts: [])


def test_iterative_extraction_preserves_original_need_id(monkeypatch, isolated_extraction):
    agent = extractor.EvidenceAgent()
    needs = [{"need_id": 2, "need": "Second question", "dimension": "Second"}]
    monkeypatch.setattr(agent, "generate_json", lambda *args, **kwargs: {"claims": [{
        "content": "Supported statement", "short_quote": "Original statement", "unit_id": 12,
        "need_id": 2, "assertion_mode": "prescriptive",
        "assertion_mode_raw": "design requirement",
    }]})
    unit = Unit(material_id=7, id=12, kind="text", content="Original statement")

    facts = agent._process_batch(needs, "[U12 | source.pdf]\nOriginal statement", {},
                                 {7: [unit]}, {7: "source.pdf"}, "", 1, 1)

    assert facts[0].need_id == 2
    assert facts[0].dimension == "Second"
    assert facts[0].assertion_mode == "prescriptive"
    assert facts[0].assertion_mode_raw == "design requirement"
    assert "2. [" in agent._build_need_block(needs)


@pytest.mark.parametrize("response", [{}, {"uncovered": "bad"}, {"uncovered": [999]}])
def test_malformed_coverage_is_unknown_not_fully_supported(monkeypatch, response):
    agent = extractor.EvidenceAgent()
    monkeypatch.setattr(agent, "generate_json", lambda *args, **kwargs: response)

    assert agent._coverage_audit([], [{"need_id": 1, "need": "Question"}], "") is None
    assert agent.coverage_audit_status["status"] == "unknown"


def test_coverage_failure_does_not_trigger_blind_retrieval(monkeypatch):
    agent = extractor.EvidenceAgent()

    def fail(*args, **kwargs):
        raise RuntimeError("MODEL_OUTPUT_TRUNCATED")

    monkeypatch.setattr(agent, "generate_json", fail)
    manager = SimpleNamespace(for_first_pass=lambda *args: [],
                              for_gap_retrieval=lambda *args, **kwargs: pytest.fail("unexpected retrieval"))

    assert agent.extract_facts([{"need": "Question"}], {}, {}, cm=manager) == []
    assert agent.last_extraction_status["status"] == "partial"
    assert agent.last_extraction_status["coverage"]["status"] == "unknown"


def test_failed_batches_are_collected_without_losing_successful_batches(monkeypatch, isolated_extraction):
    agent = extractor.EvidenceAgent()

    def generate(self, prompt, **kwargs):
        if "broken" in prompt:
            raise RuntimeError("model unavailable")
        return {"claims": []}

    monkeypatch.setattr(extractor.EvidenceAgent, "generate_json", generate)
    monkeypatch.setattr(extractor.settings, "evidence_batch_concurrency", 2)
    result = agent._process_batches([
        ("[U1 | source.pdf]\nbroken", {}), ("[U2 | source.pdf]\nvalid", {}),
    ], [{"need": "Question"}], {}, {}, "")

    assert result == [[], []]
    assert len(agent.batch_failures) == 1
    assert agent.batch_failures[0]["unit_ids"] == [1]


@pytest.mark.parametrize("operation", ["batch", "coverage"])
def test_pause_is_not_swallowed_as_an_empty_result(monkeypatch, isolated_extraction, operation):
    agent = extractor.EvidenceAgent()

    def pause(*args, **kwargs):
        raise RuntimeError("TASK_PAUSED")

    monkeypatch.setattr(agent, "generate_json", pause)
    with pytest.raises(RuntimeError, match="TASK_PAUSED"):
        if operation == "coverage":
            agent._coverage_audit([], [{"need": "Question"}], "")
        else:
            agent._process_batch([], "[U1 | source.pdf]\ntext", {}, {}, {}, "", 1, 1)


def test_chapter_narrative_respects_input_budget_and_records_fallback(monkeypatch):
    agent = narrative.NarrativeAgent()
    prompts = []

    def fail(prompt, **kwargs):
        prompts.append(prompt)
        raise RuntimeError("unavailable")

    monkeypatch.setattr(agent, "generate_json", fail)
    monkeypatch.setattr(narrative, "save_task_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(narrative, "update_call_funnel", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.runtime_profiles.stage_input_budget_tokens", lambda stage: 900)
    chapter = {"title": "Chapter", "target_words": 1200, "subsections": [
        {"title": "Topic", "fact_ids": [1, 20], "purpose": "Explain evidence"},
    ]}
    facts = [{"id": i, "content": "事实内容" * 80} for i in range(1, 21)]

    result = agent.plan_chapter("task", chapter, {}, facts, [])

    assert count_tokens(prompts[0]) <= 900
    assert result["generation_status"] == "fallback"
    assert result["generation_error"] == "unavailable"
    assert result["subsections"][0]["fact_ids"] == [1, 20]


def test_multiline_narrative_records_are_recognized_as_visible():
    fact = {"id": 5, "content": "first line\nsecond line"}
    assert narrative._prompt_visible_ids("Facts:\n5. first line\nsecond line\n", [fact]) == [5]


def test_narrative_missing_instructions_does_not_call_model_or_reuse_call_id(monkeypatch):
    import json

    agent = narrative.NarrativeAgent()
    agent.last_call_id = "previous-chapter"
    chapter = {"title": "Chapter", "fact_ids": [1]}
    calls = []
    monkeypatch.setattr(narrative, "planning_prompt", lambda *args, **kwargs: (
        f"当前章节规划:{json.dumps(chapter, ensure_ascii=False)}", {},
    ))
    monkeypatch.setattr(agent, "generate_json", lambda *args: pytest.fail("incomplete contract"))
    monkeypatch.setattr(narrative, "save_task_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr(narrative, "update_call_funnel", lambda call_id, **kwargs: calls.append(call_id))

    result = agent.plan_chapter("task", chapter, {}, [{"id": 1, "content": "Evidence"}], [])

    assert result["generation_status"] == "fallback"
    assert result["generation_error"] == "NARRATIVE_CONTRACT_EXCEEDS_INPUT_BUDGET"
    assert calls == [""]


def test_single_subsection_keeps_plan_but_hides_heading():
    subsection = {"title": "Only", "fact_ids": [1], "inference_ids": [3],
                  "paragraphs": [{"paragraph_id": "P1", "fact_ids": [1]}],
                  "evidence_status": "limited", "evidence_reason": "Missing comparison"}
    units = _subsection_generation_units({"subsections": [subsection]}, 900)
    assert len(units) == 1
    assert units[0]["title"] == ""
    assert units[0]["plan"] == subsection
    assert units[0]["evidence_status"] == "limited"


def test_writer_packing_skips_long_record_and_keeps_later_short_record():
    selected, _ = _pack_writer_evidence(["事实" * 100, "短事实"], [], 20)
    assert selected == ["短事实"]


@pytest.mark.parametrize("coverage,expected", [
    ({"status": "unknown"}, "unknown"),
    ({"status": "complete", "uncovered_need_ids": [2]}, "partial"),
    ({"status": "complete", "uncovered_need_ids": []}, "supported"),
])
def test_ledger_preserves_audit_status_and_stable_need_ids(monkeypatch, coverage, expected):
    from app.intelligence import ledger

    monkeypatch.setattr(ledger, "list_clusters", lambda task_id: [])
    monkeypatch.setattr(ledger, "list_relations", lambda task_id: [])
    result = ledger.build_ledger(
        "task", [{"need_id": 2, "need": "Second question"}],
        [{"id": 10, "content": "A fact", "need_id": 2}], coverage=coverage,
    )

    assert result["need_coverage"][0]["status"] == expected
    assert result["need_coverage"][0]["supported_fact_ids"] == [10]
    if expected == "unknown":
        assert result["unverified_needs"] == ["Second question"]
        assert result["information_gaps"] == []


def test_coverage_uses_original_need_ids_after_gap_retrieval(monkeypatch):
    agent = extractor.EvidenceAgent()
    monkeypatch.setattr(agent, "generate_json", lambda *args, **kwargs: {"uncovered": [2]})
    needs = [{"need_id": 2, "need": "Second question"}]

    assert agent._coverage_audit([], needs, "") == needs
    assert agent.coverage_audit_status["uncovered_need_ids"] == [2]


def test_coverage_audit_splits_large_need_without_truncating_and_aggregates(monkeypatch):
    agent = extractor.EvidenceAgent()
    calls = []
    monkeypatch.setattr(extractor, "count_tokens", len)
    monkeypatch.setattr("app.runtime_profiles.stage_input_budget_tokens", lambda stage: 900)

    def audit(prompt, **kwargs):
        calls.append(prompt)
        # A covered chunk is enough to establish a supported need.
        return {"uncovered": [] if "supports-answer" in prompt else [4]}

    monkeypatch.setattr(agent, "generate_json", audit)
    needs = [{"need_id": 4, "need": "Question"}]
    facts = [Fact(id=index, content=("supports-answer" if index == 1 else "other") + ("x" * 180), need_id=4)
             for index in range(1, 8)]

    result = agent._coverage_audit(facts, needs, "")

    assert result == []
    assert len(calls) > 1
    assert all(len(extractor._AUDIT_SYSTEM + prompt) <= 900 for prompt in calls)
    assert all("supports-answer" in "".join(calls) for _ in [0])
    assert agent.coverage_audit_status["status"] == "complete"
    assert agent.coverage_audit_status["split_need_ids"] == [4]


def test_coverage_audit_oversized_indivisible_fact_stays_unknown(monkeypatch):
    agent = extractor.EvidenceAgent()
    monkeypatch.setattr(extractor, "count_tokens", len)
    monkeypatch.setattr("app.runtime_profiles.stage_input_budget_tokens", lambda stage: 700)
    monkeypatch.setattr(agent, "generate_json", lambda *args, **kwargs: pytest.fail("must not send truncated fact"))

    result = agent._coverage_audit(
        [Fact(id=1, content="x" * 2000, need_id=1)], [{"need_id": 1, "need": "Question"}], "",
    )

    assert result is None
    assert agent.coverage_audit_status["status"] == "unknown"
    assert agent.coverage_audit_status["unknown_need_ids"] == [1]


def test_coverage_audit_greedily_packs_needs_that_fit_together(monkeypatch):
    agent = extractor.EvidenceAgent()
    prompts = []
    monkeypatch.setattr(extractor, "count_tokens", len)
    monkeypatch.setattr("app.runtime_profiles.stage_input_budget_tokens", lambda stage: 1800)

    def audit(prompt, **kwargs):
        prompts.append(prompt)
        return {"uncovered": []}

    monkeypatch.setattr(agent, "generate_json", audit)
    needs = [{"need_id": index, "need": f"Question {index}"} for index in range(1, 7)]
    facts = [Fact(id=index, content=f"Evidence for {index}", need_id=index) for index in range(1, 7)]

    assert agent._coverage_audit(facts, needs, "") == []
    assert len(prompts) == 1
    assert agent.coverage_audit_status["audited_need_ids"] == [1, 2, 3, 4, 5, 6]
