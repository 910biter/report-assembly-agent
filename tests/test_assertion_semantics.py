import json

from app.analysis.evidence_semantics import (
    analysis_fact_line,
    analysis_source_lines,
    attach_source_contexts,
)
from app.assertion_semantics import (
    normalize_assertion_mode,
    normalize_assertion_mode_raw,
    normalize_assertion_semantics,
    normalize_semantic_profile,
    semantic_profile_json,
)


def test_assertion_mode_is_protocol_and_raw_semantics_remain_open():
    assert normalize_assertion_mode("prescriptive") == "prescriptive"
    for unseen_label in ("设计目标", "已部署的队列方案", "C2 链路测试结果"):
        assert normalize_assertion_mode(unseen_label) == "unknown"
        assert normalize_assertion_semantics(unseen_label) == ("unknown", unseen_label)
    assert normalize_assertion_mode_raw("  设计目标：要求到 2028 年完成  ") == "设计目标：要求到 2028 年完成"
    assert normalize_assertion_mode("") == "unknown"
    assert normalize_assertion_semantics("设计目标") == ("unknown", "设计目标")


def test_analysis_fact_line_carries_assertion_and_material_support_state():
    line = analysis_fact_line({
        "id": 7757,
        "content": "创建 PostgreSQL 表 task_queue_entries。",
        "assertion_mode": "prescriptive",
        "assertion_mode_raw": "设计要求",
        "claim_supports": ["actual"],
        "source_roles": ["policy document"],
    })

    assert line.startswith("7757. [")
    assert "陈述性质=prescriptive（设计要求）" in line
    assert "claim_support" not in line
    assert "policy document" not in line
    assert "actual" not in line
    assert "创建 PostgreSQL 表 task_queue_entries" in line


def test_analysis_source_lines_preserve_section_and_verbatim_evidence_context():
    lines = analysis_source_lines([{
        "id": 7760,
        "source_contexts": [{
            "source_file": "multi-user-queue-design.md",
            "section_headings": ["Durable queue"],
            "quote": "One worker claims the next eligible row",
            "excerpt": "One worker claims the next eligible row with FOR UPDATE SKIP LOCKED.",
        }],
    }])

    assert len(lines) == 1
    assert "Fact 7760" in lines[0]
    assert "Durable queue" in lines[0]
    assert "One worker claims the next eligible row" in lines[0]


def test_source_context_is_joined_by_unit_and_retains_preceding_heading():
    facts = attach_source_contexts(
        [{"id": 7760, "content": "planned claim"}],
        [{
            "fact_id": 7760,
            "material_id": 4,
            "unit_id": 12,
            "source_file": "design.md",
            "page": None,
            "paragraph": 5,
            "quote": "SKIP LOCKED",
        }],
        [
            {"id": 9, "material_id": 4, "kind": "heading", "content": "Current state"},
            {"id": 11, "material_id": 4, "kind": "heading", "content": "Durable queue"},
            {"id": 12, "material_id": 4, "kind": "text",
             "content": "Create a queue using FOR UPDATE SKIP LOCKED."},
            {"id": 13, "material_id": 4, "kind": "heading", "content": "Later section"},
        ],
    )

    context = facts[0]["source_contexts"][0]
    assert context["section_headings"] == ["Current state", "Durable queue"]
    assert context["excerpt"] == "Create a queue using FOR UPDATE SKIP LOCKED."
    assert "Durable queue" in analysis_source_lines(facts)[0]


def test_semantic_profile_is_open_multiaxis_and_preserves_unseen_categories():
    source = {
        "facets": [
            {"axis": "modality", "label": "counterfactual scenario", "detail": "only under a stated condition"},
            {"axis": "temporal scope", "label": "valid from 2031 onward", "detail": "source date anchor"},
            {"axis": "domain-specific epistemic class", "label": "model-based estimate", "detail": "not directly measured"},
        ],
        "attribution": "the report's authors",
        "conditions": ["only when the service is deployed"],
        "new_future_axis": {"labels": ["not in today's schema"]},
        "additional": {"review_state": "contested"},
    }

    profile = normalize_semantic_profile(source)

    assert [item["label"] for item in profile["facets"]] == [
        "counterfactual scenario", "valid from 2031 onward", "model-based estimate",
        "only when the service is deployed", "not in today's schema",
    ]
    assert profile["additional"]["attribution"] == "the report's authors"
    assert {"axis": "new_future_axis", "label": "not in today's schema", "detail": ""} in profile["facets"]
    assert profile["additional"]["review_state"] == "contested"
    assert json.loads(semantic_profile_json(profile))["facets"][0]["axis"] == "modality"


def test_empty_semantics_keep_legacy_database_default_for_deduplication():
    assert semantic_profile_json(None) == "{}"
    assert semantic_profile_json({"facets": []}) == "{}"


def test_inference_keeps_each_cited_fact_semantics_without_collapsing(monkeypatch):
    from app.analysis import analyzer
    from app.analysis.analyzer import AnalysisAgent

    saved = []

    def fake_save(inference, origin_call_id=""):
        inference.id = 88
        saved.append(inference)
        return inference.id

    monkeypatch.setattr(analyzer, "save_inference", fake_save)
    agent = AnalysisAgent()
    facts = [
        {"id": 501, "content": "The specification proposes a queue.",
         "semantic_profile": {"facets": [{"axis": "realization", "label": "proposed"}]}},
        {"id": 502, "content": "A trial observed a queue.",
         "semantic_profile": {"facets": [{"axis": "epistemic basis", "label": "observed"}]}},
    ]
    payload = {"inferences": [{
        "content": "The proposal and trial provide different kinds of evidence.",
        "based_fact_ids": [501, 502],
        "semantic_profile": {"facets": [{"axis": "synthesis", "label": "qualified comparison"}]},
    }]}

    inferences, _, _ = agent._parse_payload(payload, {501, 502}, "call-1", facts)

    assert len(inferences) == 1
    assert inferences[0].source_semantics == [
        {"fact_id": 501, "profile": normalize_semantic_profile(facts[0]["semantic_profile"]),
         "legacy_assertion_mode": "unknown", "legacy_assertion_detail": "unknown"},
        {"fact_id": 502, "profile": normalize_semantic_profile(facts[1]["semantic_profile"]),
         "legacy_assertion_mode": "unknown", "legacy_assertion_detail": "unknown"},
    ]
    assert inferences[0].semantic_profile["facets"][0]["label"] == "qualified comparison"


def test_narrative_audit_recognizes_fact_lines_with_appended_semantics():
    from app.assertion_semantics import semantic_profile_line
    from app.planning.narrative import _prompt_visible_ids

    facts = [{"id": 22, "content": "The source describes a conditional target."}]
    prompt = (
        f"{facts[0]['id']}. {facts[0]['content']} "
        f"[semantic={semantic_profile_line({'facets': [{'axis': 'modality', 'label': 'conditional'}]})}]"
    )

    assert _prompt_visible_ids(prompt, facts) == [22]


def test_evidence_and_analysis_prompts_preserve_source_modality(monkeypatch):
    from app.analysis import analyzer
    from app.analysis.analyzer import AnalysisAgent
    from app.evidence.extractor import _SYSTEM as evidence_system

    assert '"assertion_mode"' in evidence_system
    assert '"assertion_mode_raw"' in evidence_system
    assert "保留原文时态、条件和语气" in evidence_system
    assert "材料标题、文件名和领域词仅供定位" in evidence_system
    for prompt in (evidence_system, analyzer._SYSTEM, analyzer._GLOBAL_SYSTEM):
        for boundary in ("时间", "条件", "归属", "实现状态"):
            assert boundary in prompt
        assert '"semantic_profile": {"facets": []' in prompt
    assert "相关字段缺失时省略" in evidence_system
    assert "按需补充" in analyzer._SYSTEM
    assert "现状/目标/计划/预测" in analyzer._SYSTEM
    assert "直接支撑判断的 based_fact_ids" in analyzer._SYSTEM

    agent = AnalysisAgent()
    captured = {}
    monkeypatch.setattr(agent, "generate_json", lambda prompt, **kwargs: captured.update(prompt=prompt) or {
        "inferences": [], "external_notes": [],
    })
    monkeypatch.setattr(analyzer, "update_call_funnel", lambda *args, **kwargs: None)
    monkeypatch.setattr(analyzer, "update_call_products", lambda *args, **kwargs: None)
    monkeypatch.setattr(analyzer, "update_call_metrics", lambda *args, **kwargs: None)

    agent.analyze_local("队列设计", [{
        "id": 7757,
        "content": "创建 PostgreSQL 表 task_queue_entries。",
        "sources": ["Create a PostgreSQL task_queue_entries table"],
        "assertion_mode": "prescriptive",
        "assertion_mode_raw": "设计要求",
        "claim_supports": ["normative"],
        "source_contexts": [{
            "source_file": "multi-user-queue-design.md",
            "section_headings": ["Durable queue"],
            "quote": "Create a PostgreSQL task_queue_entries table",
            "excerpt": "Create a PostgreSQL task_queue_entries table.",
        }],
    }])

    assert "陈述性质=prescriptive（设计要求）" in captured["prompt"]
    assert "材料支撑边界" not in captured["prompt"]
    assert "Durable queue" in captured["prompt"]


def test_global_analysis_receives_status_and_source_context(monkeypatch):
    from app.analysis import analyzer
    from app.analysis.analyzer import AnalysisAgent
    from app.models import Inference

    captured = {}
    agent = AnalysisAgent()
    monkeypatch.setattr(agent, "generate_json", lambda prompt, **kwargs: captured.update(prompt=prompt) or {
        "inferences": [], "external_notes": [],
    })
    monkeypatch.setattr(analyzer, "update_call_funnel", lambda *args, **kwargs: None)
    monkeypatch.setattr(analyzer, "update_call_products", lambda *args, **kwargs: None)
    monkeypatch.setattr(analyzer, "update_call_metrics", lambda *args, **kwargs: None)

    agent.analyze_global(
        [Inference(
            content="队列方案要求使用数据库持久化。",
            source_level="MATERIAL_INFERENCE",
            based_fact_ids=[7757],
        )],
        [{
            "id": 7757,
            "content": "创建 PostgreSQL 表 task_queue_entries。",
            "assertion_mode": "prescriptive",
            "assertion_mode_raw": "设计要求",
            "source_contexts": [{
                "source_file": "queue-design.md",
                "section_headings": ["Durable queue"],
                "quote": "Create a PostgreSQL task_queue_entries table",
                "excerpt": "Create a PostgreSQL task_queue_entries table.",
            }],
        }],
    )

    assert "陈述性质=prescriptive（设计要求）" in captured["prompt"]
    assert "Durable queue" in captured["prompt"]
