from app.observability.core_chain import (
    aggregate_generation_units,
    audit_id_flow,
    build_generation_unit_stats,
    collect_assigned_ids,
    extract_tagged_ids,
    extract_numbered_ids,
)


def test_audit_id_flow_deduplicates_ids_and_reports_handoffs():
    result = audit_id_flow({
        "evidence": [1, 2, 2, 3],
        "analysis": [2, 3, 4],
        "writer": [3, 4, 4],
    })

    assert result["stages"]["evidence"]["unique_count"] == 3
    assert result["handoffs"][0]["transferred_unique_count"] == 2
    assert result["handoffs"][0]["not_forwarded_ids"] == [1]
    assert result["handoffs"][0]["outside_source_ids"] == [4]
    assert result["handoffs"][1]["transfer_ratio"] == 0.6667


def test_extract_numbered_ids_reads_only_evidence_records():
    assert extract_numbered_ids(["12. Fact", "12. duplicate", "  19. Another", "Chapter 2. not a record"]) == [12, 19]


def test_tagged_input_and_recursive_assignment_ids_are_unique():
    assert extract_tagged_ids("fact_id=4 inference_id=9 fact_id=4", "fact_id") == [4]
    assert collect_assigned_ids({"primary_fact_ids": [4, 5], "children": [{"fact_ids": [5, 6]}]}, "fact") == [4, 5, 6]


def test_generation_unit_schema_is_shared_and_uses_unique_citations():
    result = build_generation_unit_stats(
        generation_mode="chaptered",
        unit_id="chapter-1/subsection-2",
        target_words=100,
        actual_text="甲" * 75,
        planned_fact_ids=[1, 2],
        supplied_fact_ids=[1, 2, 3],
        cited_fact_ids=[1, 1, 3],
        planned_inference_ids=[9],
        supplied_inference_ids=[9],
        cited_inference_ids=[9],
    )

    assert result["generation_mode"] == "chaptered"
    assert result["cited_fact_ids"] == [1, 3]
    assert result["cited_fact_count"] == 2
    assert result["planned_fact_coverage"] == 0.5
    assert result["supplied_fact_coverage"] == 0.6667
    assert result["underfill_reason"] == "generation_budget_not_fulfilled"


def test_generation_unit_audit_distinguishes_unavailable_and_budget_omitted_plan_ids():
    result = build_generation_unit_stats(
        generation_mode="chaptered",
        unit_id="chapter-1/subsection-1",
        target_words=100,
        actual_text="正文",
        planned_fact_ids=[1, 2, 3],
        candidate_fact_ids=[1, 2],
        supplied_fact_ids=[1],
        cited_fact_ids=[1],
        planned_inference_ids=[8, 9],
        candidate_inference_ids=[8],
        supplied_inference_ids=[],
        cited_inference_ids=[],
    )

    assert result["planned_fact_ids_missing_from_candidate_pool"] == [3]
    assert result["planned_fact_ids_omitted_by_budget"] == [2]
    assert result["budget_omitted_fact_ids"] == [2]
    assert result["planned_inference_ids_missing_from_candidate_pool"] == [9]
    assert result["planned_inference_ids_omitted_by_budget"] == [8]
    aggregate = aggregate_generation_units([result])
    assert aggregate["planned_fact_missing_from_candidate_pool_count"] == 1
    assert aggregate["planned_fact_omitted_by_budget_count"] == 1
    assert aggregate["candidate_fact_count"] == 2


def test_generation_unit_aggregate_deduplicates_cross_unit_fact_ids():
    common = {
        "planned_fact_ids": [1, 2],
        "supplied_fact_ids": [1, 2],
        "cited_fact_ids": [1],
        "planned_inference_ids": [],
        "supplied_inference_ids": [],
        "cited_inference_ids": [],
    }
    first = build_generation_unit_stats(
        generation_mode="chaptered", unit_id="c1", target_words=100, actual_text="甲" * 100,
        **common,
    )
    second = build_generation_unit_stats(
        generation_mode="article_beats", unit_id="beat-1", target_words=100, actual_text="乙" * 100,
        **{**common, "planned_fact_ids": [2, 3], "supplied_fact_ids": [2, 3], "cited_fact_ids": [2]},
    )

    result = aggregate_generation_units([first, second])
    assert result["planned_fact_ids"] == [1, 2, 3]
    assert result["cited_fact_ids"] == [1, 2]
    assert result["planned_fact_coverage"] == 0.6667
    assert result["generation_units"] == 2
    assert result["completion_rate"] == 1.0
