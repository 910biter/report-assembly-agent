from app.context_budget import ContextSection, count_tokens
from app.planning.evidence_context import bind_plan_evidence, fact_line, inference_line, planning_prompt
from app.planning.narrative import _sanitize_document_plan
from app.writing.writer import _focused_unit_evidence, _pack_writer_evidence
from app.planning.narrative import NarrativeAgent
from app.writing.writer import _writer_json_overhead
from app.writing.writer import _writer_argument_contract
from app.writing.scale_execution import writer_scale_prompt_hint
from app.memory.style_profile import select_exemplars


def test_expression_examples_do_not_require_matching_business_subjects():
    bank = [{'content': 'Example prose about a different historical subject.',
             'purpose': 'Unrelated sample purpose', 'approved': True},
            {'content': 'Unapproved sample', 'approved': False}]
    context = {'title': 'A completely new topic', 'purpose': 'Another task'}
    assert select_exemplars(bank, context) == []
    assert select_exemplars(bank, {**context, 'match_dimension': 'expression'}) == bank[:1]


def test_expression_retrieval_keeps_source_links_out_of_prose_references():
    source = {'content': 'https://example.com/' + 'source-anchor-' * 30, 'approved': True}
    assert select_exemplars([source], {'match_dimension': 'expression'}) == []
    assert source['content'].startswith('https://')


def test_writer_contract_preserves_evidence_without_planner_prose_or_mutation():
    source = {
        'next_bridge': 'Draft bridge',
        'paragraphs': [{'paragraph_id': 'P1', 'purpose': 'Compare',
                        'argument': 'Evidence 1 limits the comparison in evidence 2.',
                        'transition': 'Draft transition', 'fact_ids': [1, 2], 'target_words': 200}],
        'must_not_claim': ['Unsupported result'],
    }
    projected = _writer_argument_contract(source)
    assert 'next_bridge' not in projected
    assert 'transition' not in projected['paragraphs'][0]
    assert projected['paragraphs'][0]['argument'] == source['paragraphs'][0]['argument']
    assert projected['paragraphs'][0]['fact_ids'] == [1, 2]
    assert projected['paragraphs'][0]['target_words'] == 200
    assert projected['must_not_claim'] == source['must_not_claim']
    assert source['paragraphs'][0]['transition'] == 'Draft transition'


def test_dependencies_are_closed_before_missing_arcs_are_added(monkeypatch):
    agent = NarrativeAgent()
    monkeypatch.setattr(agent, 'generate_json', lambda *a, **kw: {
        'beats': [{'beat_id': 'B1', 'fact_ids': [2], 'inference_ids': [9], 'target_words': 700}],
        'paragraphs': [{'beat_ids': ['B1'], 'fact_ids': [2], 'inference_ids': [9], 'target_words': 700}],
    })
    monkeypatch.setattr('app.planning.narrative.update_call_funnel', lambda *a, **kw: None)
    plan = agent.plan_document('', {
        'budget': {'target_words': 700},
        'chapter_plans': [{'primary_fact_ids': [1], 'primary_inference_ids': [9], 'target_words': 700}],
    }, [{'id': 1, 'content': 'Basis'}, {'id': 2, 'content': 'Context'}],
       [{'id': 9, 'content': 'Judgment', 'based_fact_ids': [1]}])
    assert len(plan['beats']) == len(plan['paragraphs']) == 1
    assert plan['paragraphs'][0]['target_words'] == 700
    assert set(plan['paragraphs'][0]['fact_ids']) == {1, 2}


def test_missing_fact_completes_its_existing_arc_without_new_paragraph():
    plan = _sanitize_document_plan({
        'beats': [{'beat_id': 'B1', 'fact_ids': [1], 'target_words': 700}],
        'paragraphs': [{'beat_ids': ['B1'], 'fact_ids': [1], 'target_words': 700}],
    }, {'budget': {'target_words': 700}, 'chapter_plans': [{'primary_fact_ids': [1, 2]}]}, {1, 2}, set())
    assert len(plan['beats']) == len(plan['paragraphs']) == 1
    assert plan['paragraphs'][0]['fact_ids'] == [1, 2]


def test_a_wholly_missing_arc_is_not_silently_discarded():
    plan = _sanitize_document_plan({'beats': [{'beat_id': 'B1', 'fact_ids': [1]}]},
        {'budget': {'target_words': 700}, 'chapter_plans': [{'primary_fact_ids': [1]}, {'primary_fact_ids': [2]}]},
        {1, 2}, set())
    assert len(plan['beats']) == 2
    assert {fid for beat in plan['beats'] for fid in beat['fact_ids']} == {1, 2}


def test_uncertain_judgment_does_not_relax_an_achievable_user_scale():
    hint = writer_scale_prompt_hint({
        'target_words': 700, 'output_bounds': {'min_words': 600},
        'evidence_supported_max_words': 800, 'underfill_reason': 'Long-term outcomes are unknown.',
    }, unit_target_words=700, evidence_status='limited')
    assert '不能仅因存在未知项而自行放宽用户下限' in hint
    assert '最低完成要求约 600 字' in hint
    assert '允许低于' not in hint


def test_json_budget_accounts_for_citation_density_without_changing_stage_cap():
    sparse = _writer_json_overhead([{'fact_ids': [1]}])
    dense = _writer_json_overhead([{'fact_ids': list(range(1, 40)), 'inference_ids': list(range(100, 110))}])
    assert dense > sparse


def test_document_narrative_receives_style_before_designing_paragraphs(monkeypatch):
    agent = NarrativeAgent()
    captured = {}

    def generate(prompt, **kwargs):
        captured['prompt'] = prompt
        return {'beats': [{'beat_id': 'B1', 'fact_ids': [1]}],
                'paragraphs': [{'beat_ids': ['B1'], 'fact_ids': [1],
                                'argument': 'Compare the observed scope with the proposed scope.'}]}

    monkeypatch.setattr(agent, 'generate_json', generate)
    monkeypatch.setattr('app.planning.narrative.update_call_funnel', lambda *a, **kw: None)
    result = agent.plan_document('', {'budget': {'target_words': 700}},
                        [{'id': 1, 'content': 'Observed result'}], [],
                        style_block='STYLE_REFERENCE: open with a concrete comparison')
    assert 'STYLE_REFERENCE: open with a concrete comparison' in captured['prompt']
    assert result['paragraphs'][0]['argument'] == 'Compare the observed scope with the proposed scope.'


def test_chapter_narrative_receives_style_before_designing_subsections(monkeypatch):
    agent = NarrativeAgent()
    captured = {}

    def generate(prompt, **kwargs):
        captured['prompt'] = prompt
        return {'subsections': [{'title': 'Comparison', 'fact_ids': [1]}]}

    monkeypatch.setattr(agent, 'generate_json', generate)
    monkeypatch.setattr('app.planning.narrative.update_call_funnel', lambda *a, **kw: None)
    agent.plan_chapter('', {'title': 'Results', 'target_words': 700}, {},
                       [{'id': 1, 'content': 'Observed result'}], [],
                       style_block='STYLE_REFERENCE: explain the comparison directly')
    assert 'STYLE_REFERENCE: explain the comparison directly' in captured['prompt']


def test_compact_records_keep_open_semantics_without_repeating_inherited_profiles():
    fact = {"id": 1, "content": "The target is planned for 2030.", "semantic_profile": {
        "facets": [{"axis": "custom state", "label": "proposed", "detail": "not yet implemented"}],
        "additional": {"condition": "approval pending"},
    }}
    inference = {"id": 8, "content": "Implementation remains uncertain.", "based_fact_ids": [1],
                 "source_semantics": [{"fact_id": 1, "profile": fact["semantic_profile"]}] * 10}
    line = fact_line(fact)
    assert "custom state=proposed(not yet implemented)" in line
    assert "approval pending" in line
    assert "based_fact_ids=[1]" in inference_line(inference)
    assert "source_semantics" not in inference_line(inference)


def test_planning_context_selects_inference_and_bases_as_one_group():
    facts = [{"id": 1, "content": "a" * 200}, {"id": 2, "content": "b" * 200}]
    inferences = [{"id": 9, "content": "Comparison", "based_fact_ids": [1, 2]}]
    prompt, audit = planning_prompt("test", [ContextSection("contract", ["Task"])], facts, inferences, 190,
                                    required_inferences=[9])
    assert count_tokens(prompt) <= 190
    if 9 in audit["sections"]["推断"]["selected_ids"]:
        assert {1, 2} <= set(audit["sections"]["事实"]["selected_ids"])


def test_strict_planning_context_reports_unfitted_required_evidence():
    import pytest

    facts = [{"id": 1, "content": "a" * 200}]
    with pytest.raises(ValueError, match="REQUIRED_EVIDENCE_EXCEEDS_CONTEXT"):
        planning_prompt("narrative", [ContextSection("contract", ["Task"])],
                        facts, [], 80, required_facts=[1], strict_required=True)


def test_small_material_retains_all_facts_despite_repeated_source_profiles():
    facts = [{"id": i, "content": f"Independent observation {i}", "semantic_profile": {
        "facets": [{"axis": "status", "label": "observed", "detail": "confirmed"}],
    }} for i in range(1, 38)]
    inferences = [{"id": 100 + i, "content": f"Judgment {i}", "based_fact_ids": [i, i + 1],
                   "source_semantics": [{"fact_id": f["id"], "profile": f["semantic_profile"]} for f in facts]}
                  for i in range(1, 17)]
    _, audit = planning_prompt("test", [ContextSection("contract", ["Task"])], facts, inferences, 5000)
    assert audit["sections"]["事实"]["selected_items"] == 37
    assert audit["sections"]["推断"]["selected_items"] == 16
    assert not audit["truncated"]


def test_inference_bases_reach_both_paragraph_and_writer_without_unrelated_facts():
    facts = [{"id": 1}, {"id": 2}, {"id": 3}]
    inferences = [{"id": 9, "based_fact_ids": [2, 999]}]
    plan = {"subsections": [{"fact_ids": [1], "inference_ids": [9],
                            "paragraphs": [{"fact_ids": [1], "inference_ids": [9]}]}]}
    bind_plan_evidence(plan, facts, inferences)
    unit = plan["subsections"][0]
    assert unit["fact_ids"] == [1, 2]
    assert unit["paragraphs"][0]["fact_ids"] == [1, 2]
    selected, _ = _focused_unit_evidence(facts, inferences, unit, {})
    assert [item["id"] for item in selected] == [1, 2]


def test_article_paragraphs_preserve_selected_beat_evidence():
    plan = _sanitize_document_plan({"beats": [{"beat_id": "B1", "fact_ids": [1, 2], "inference_ids": [9]}],
        "paragraphs": [{"beat_ids": ["B1"], "fact_ids": [1]}]},
        {"budget": {"target_words": 700}}, {1, 2}, {9})
    assert plan["paragraphs"][0]["fact_ids"] == [1, 2]
    assert plan["paragraphs"][0]["inference_ids"] == [9]


def test_writer_never_packs_an_inference_without_its_source_facts():
    facts = ["1. " + "x" * 300, "2. short"]
    judgments = ["9. comparison"]
    selected, inferred = _pack_writer_evidence(facts, judgments, 30, {9: [1, 2]})
    assert selected == ["2. short"]
    assert inferred == []
