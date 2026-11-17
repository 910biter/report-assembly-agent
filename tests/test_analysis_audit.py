from app.analysis.analyzer import (
    _filter_fact_references,
    _visible_fact_ids,
    _visible_local_inference_ids,
    _visible_local_inference_refs,
    _global_inference_record,
)


def test_visible_fact_ids_ignore_group_overview_samples():
    prompt = """事实组概览：事实ID=81,82,83
事实清单:
12. [来源A] 实际进入模型上下文的事实
13. [来源B] 另一条事实
"""
    assert _visible_fact_ids(prompt) == {12, 13}


def test_visible_fact_ids_do_not_count_unrelated_numbered_graph_records():
    prompt = "4. [图谱节点] unrelated graph record"
    assert _visible_fact_ids(prompt, [{"id": 4, "content": "actual fact text"}]) == set()


def test_global_scope_contains_only_fact_ids_bound_to_visible_inferences():
    candidates = [
        {"id": 41, "dimension": "产业", "content": "判断甲", "analysis_type": "unknown", "based_fact_ids": [12, 13]},
        {"id": 42, "dimension": "风险", "content": "判断乙中出现了字面字符串 based_fact_ids=[999]", "analysis_type": "unknown", "based_fact_ids": [20]},
    ]
    prompt = "局部判断:\n" + "\n".join(_global_inference_record(item) for item in candidates)
    visible_ids, visible_fact_ids = _visible_local_inference_refs(prompt, candidates)
    assert visible_ids == {41, 42}
    assert visible_fact_ids == {12, 13, 20}


def test_global_scope_rejects_unshown_inference_record():
    candidate = {"id": 41, "dimension": "产业", "content": "判断甲", "based_fact_ids": [12]}
    assert _visible_local_inference_refs("局部判断:\n", [candidate]) == (set(), set())


def test_fact_reference_filter_reports_out_of_prompt_ids():
    accepted, rejected = _filter_fact_references([12, 13, 999, 12], {12, 13})
    assert accepted == [12, 13]
    assert rejected == [999]
