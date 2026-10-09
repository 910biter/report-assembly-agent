from contextlib import contextmanager
from unittest.mock import patch

from app.quality import (
    _QA_SYSTEM,
    _build_qa_context,
    _normalized_numeric_tokens,
    attach_quality_issue_locations,
    run_quality_check,
)


def test_qa_context_contains_only_sentence_linked_facts_inferences_and_sources():
    rows = [{
        "id": 31,
        "section": "第二章",
        "paragraph": 2,
        "content": "该机制仍存在覆盖不足。",
        "source_level": "FACT",
        "source_refs": '{"fact_ids":[7],"inference_ids":[3]}',
    }]

    context = _build_qa_context(
        rows,
        fact_by_id={7: "测试覆盖集中于低空场景"},
        fact_semantics_by_id={7: {
            "semantic_profile": {"facets": [{"axis": "实现状态", "label": "测试设计目标"}]},
        }},
        inference_by_id={3: {
            "content": "高空域验证仍不充分",
            "source_level": "MATERIAL_INFERENCE",
            "based_fact_ids": [7],
            "confidence_level": "medium",
            "semantic_profile": {"facets": [{"axis": "证据边界", "label": "覆盖不足"}]},
            "source_semantics": [{"fact_id": 7, "profile": {
                "facets": [{"axis": "实现状态", "label": "测试设计目标"}],
            }}],
        }},
        evidence_by_fact={7: [{
            "source_file": "测试报告.pdf", "page": 4, "paragraph": 2, "quote": "验证高度集中于低空场景。",
        }]},
    )

    assert "[SENTENCE:31]" in context["sentence_lines"][0]
    assert "FACT_IDS:7" in context["sentence_lines"][0]
    assert "INFERENCE_IDS:3" in context["sentence_lines"][0]
    assert "测试覆盖集中于低空场景" in context["fact_lines"][0]
    assert '"label":"测试设计目标"' in context["fact_lines"][0]
    assert "高空域验证仍不充分" in context["inference_lines"][0]
    assert '"label":"覆盖不足"' in context["inference_lines"][0]
    assert "BASED_FACT_IDS:7" in context["inference_lines"][0]
    assert '"label":"测试设计目标"' not in context["inference_lines"][0]
    assert "测试报告.pdf · 第4页 · 第2段" in context["evidence_lines"][0]
    assert "验证高度集中于低空场景" in context["evidence_lines"][0]


def test_qa_context_follows_inference_basis_to_its_source_evidence():
    context = _build_qa_context(
        [{
            "id": 32, "section": "第三章", "paragraph": 1,
            "content": "因此，高空域的验证仍不充分。", "source_level": "INFERENCE",
            "source_refs": '{"fact_ids":[],"inference_ids":[4]}',
        }],
        fact_by_id={9: "现有测试集中在低空域"},
        inference_by_id={4: {
            "content": "高空域证据不足", "source_level": "MATERIAL_INFERENCE",
            "based_fact_ids": [9], "confidence_level": "medium",
        }},
        evidence_by_fact={9: [{
            "source_file": "飞行测试.pdf", "page": 8, "paragraph": 1, "quote": "测试高度集中在低空域。",
        }]},
    )

    assert "[FACT:9]" in context["fact_lines"][0]
    assert "SENTENCES:32" in context["fact_lines"][0]
    assert "EVIDENCE_FOR_FACT:9" in context["evidence_lines"][0]
    assert "SENTENCES:32" in context["evidence_lines"][0]


def test_quality_check_passes_linked_source_evidence_to_its_existing_llm_call():
    report_rows = [{
        "id": 31,
        "section": "第二章",
        "paragraph": 2,
        "content": "该机制仍存在覆盖不足。",
        "source_level": "FACT",
        "source_refs": '{"fact_ids":[],"inference_ids":[3]}',
    }]
    query_results = [
        report_rows,
        [{"id": 3, "content": "高空域验证仍不充分", "source_level": "MATERIAL_INFERENCE",
          "based_fact_ids": "[7]", "confidence_level": "medium",
          "semantic_profile_json": '{"facets":[{"axis":"结论范围","label":"证据不足"}]}',
          "source_semantics_json": '[{"fact_id":7,"profile":{"facets":[{"axis":"实现状态","label":"设计目标"}]}}]'}],
        [{"id": 7, "content": "测试覆盖集中于低空场景",
          "semantic_profile_json": '{"facets":[{"axis":"实现状态","label":"设计目标"}]}',
          "assertion_mode": "prescriptive", "assertion_mode_raw": "设计要求"}],
        [{"fact_id": 7, "source_file": "测试报告.pdf", "page": 4,
          "paragraph": 2, "quote": "验证高度集中于低空场景。"}],
        [{"source_refs": '{"fact_ids":[],"inference_ids":[3]}'}],
    ]
    call_index = 0
    prompts = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def execute(self, _statement):
            nonlocal call_index
            result = Result(query_results[call_index])
            call_index += 1
            return result

    @contextmanager
    def fake_session_scope():
        yield Session()

    def capture_prompt(_agent, prompt):
        prompts.append(prompt)
        return {"issues": [{
            "type": "CITATION_MISMATCH", "target_type": "sentence", "sentence_ids": [31],
            "quote": "覆盖不足", "support_status": "INSUFFICIENT_EVIDENCE",
            "note": "该分句超出了来源明确支持的范围。",
        }]}

    with (
        patch("app.quality.session_scope", fake_session_scope),
        patch("app.quality._QualityAgent.generate_json", capture_prompt),
        patch("app.quality.attach_quality_issue_locations", side_effect=lambda _id, issues, **_kw: issues),
    ):
        result = run_quality_check(7, ["第二章"])

    assert len(result) == 1
    assert result[0]["support_status"] == "INSUFFICIENT_EVIDENCE"
    assert result[0]["quote"] == "覆盖不足"
    assert call_index == 5
    assert len(prompts) == 1
    assert "[FACT:7]" in prompts[0]
    assert "[INFERENCE:3]" in prompts[0]
    assert "[EVIDENCE_FOR_FACT:7]" in prompts[0]
    assert "测试报告.pdf" in prompts[0]
    assert "验证高度集中于低空场景" in prompts[0]
    assert '"label":"设计目标"' in prompts[0]
    assert "BASED_FACT_IDS:7" in prompts[0]
    assert "INHERITED_SOURCE_SEMANTICS" not in prompts[0]


def test_quality_check_does_not_treat_low_lexical_overlap_as_unsupported_claim():
    report_row = {
        "id": 41,
        "section": "Section",
        "paragraph": 1,
        "content": "The processor preserves confidentiality while executing user code.",
        "source_level": "FACT",
        "source_refs": '{"fact_ids":[7],"inference_ids":[]}',
    }
    query_results = [
        [report_row],
        [{"id": 7, "content": "程序运行期间，外界无法读取受保护工作区的明文状态。"}],
        [{"fact_id": 7, "source_file": "材料.pdf", "page": 1,
          "paragraph": 1, "quote": "处理器执行用户代码时，受保护数据对外部不可见。"}],
        [{"source_refs": '{"fact_ids":[7],"inference_ids":[]}'}],
    ]
    call_index = 0

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def execute(self, _statement):
            nonlocal call_index
            result = Result(query_results[call_index])
            call_index += 1
            return result

    @contextmanager
    def fake_session_scope():
        yield Session()

    with (
        patch("app.quality.session_scope", fake_session_scope),
        patch("app.quality._QualityAgent.generate_json", return_value={"issues": []}),
        patch("app.quality.attach_quality_issue_locations", side_effect=lambda _id, issues, **_kw: issues),
    ):
        result = run_quality_check(9, ["Section"])

    assert call_index == 4
    assert result == []
    assert "不能仅因措辞不同或词面相似度低" in _QA_SYSTEM
    assert "无引用的实质性句子" in _QA_SYSTEM
    assert "复合句中的每个可独立核验主张" in _QA_SYSTEM
    assert "全部 FACT 与 INFERENCE 联合判断" in _QA_SYSTEM
    assert "材料关于自身或某系统现状的描述" in _QA_SYSTEM
    assert "support_status 与 note 必须一致" in _QA_SYSTEM


def test_qa_failure_is_reported_as_incomplete_instead_of_silent_zero(monkeypatch):
    report_row = {
        "id": 51, "section": "正文", "paragraph": 1,
        "content": "材料未说明该机制是否已经部署。", "source_level": "TRANSITION",
        "source_refs": '{"fact_ids":[],"inference_ids":[]}',
    }
    query_results = [[report_row], [{"source_refs": report_row["source_refs"]}]]
    call_index = 0

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def execute(self, _statement):
            nonlocal call_index
            result = Result(query_results[call_index])
            call_index += 1
            return result

    @contextmanager
    def fake_session_scope():
        yield Session()

    monkeypatch.setattr("app.quality.session_scope", fake_session_scope)
    monkeypatch.setattr(
        "app.quality._QualityAgent.generate_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )
    monkeypatch.setattr("app.quality.attach_quality_issue_locations", lambda _id, issues, **_kw: issues)

    result = run_quality_check(12, [])

    assert call_index == 2
    assert len(result) == 1
    assert result[0]["type"] == "QA_CHECK_INCOMPLETE"
    assert "不能据此认为正文没有问题" in result[0]["note"]


def test_numeric_normalization_equates_k_suffix_with_full_quantity():
    assert _normalized_numeric_tokens("安全监控器实现少于 5k 行 C++ 代码") == {"5000"}
    assert _normalized_numeric_tokens("安全监控器实现少于 5000 行 C++ 代码") == {"5000"}


def test_quality_issue_without_paragraph_number_anchors_by_unique_quote():
    issue = {
        "type": "ATTRIBUTION_WEAK",
        "section": "第二章",
        "quote": "由事实支撑的具体正文句子。",
    }
    rows = [{
        "id": 31,
        "section": "第二章",
        "paragraph": 2,
        "content": "由事实支撑的具体正文句子。",
        "user_edit": None,
        "selected": 1,
    }]

    result = attach_quality_issue_locations(7, [issue], rows=rows, report_version_id=49)

    assert result[0]["sentence_ids"] == [31]
    assert result[0]["sentence_id"] == 31
    assert result[0]["target_type"] == "sentence"
    assert result[0]["location_source"] == "quote_exact"
