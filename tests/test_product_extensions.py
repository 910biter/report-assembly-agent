import unittest
import inspect
from unittest.mock import patch

from app.evidence.extractor import EvidenceAgent
from app.infrastructure.orm import Base
from app.interaction import _artifact_summary, _proposal_instruction
from app.graph.service import (
    GraphExtractionAgent,
    _extract_adaptive,
    _fact_batches,
    _visualization_nodes,
)
from app.context import ContextManager
from app.material_comparison import (
    CHANGE_TYPES,
    _candidate_sets,
    _comparison_document,
    _comparison_metrics,
    _fallback_decision,
    _lineage_impacts,
)
from app.memory.style import _editorial_confidence, _normalize_profile_label, _validate_profile_payload
from app.memory.style_profile import (
    aggregate_style_metrics,
    assess_style_alignment,
    build_report_exemplars,
    select_exemplars,
)
from app.memory.style_jobs import create_job as create_style_job
from app.memory.style_jobs import get_job as get_style_job
from app.memory.style_jobs import run_job as run_style_job
from app.models.memory import StyleVariant
from app.rendering.headings import HeadingNumbering, format_heading, strip_heading_prefix
from app.writing.writer import _clean_generated_subheading, _style_sample_type
from app.workflow.controller import _graph_artifact_status


class ProductExtensionTests(unittest.TestCase):
    def test_graph_truncation_splits_instead_of_repeating_the_same_batch(self):
        calls = []

        def extract(batch):
            calls.append([item["id"] for item in batch])
            if len(batch) > 2:
                raise RuntimeError("MODEL_OUTPUT_TRUNCATED")
            return {"entities": [], "assertions": []}

        outcome = _extract_adaptive([{"id": value} for value in range(1, 6)], extract)
        self.assertEqual(len(outcome.payloads), 3)
        self.assertEqual(outcome.failures, [])
        self.assertEqual(outcome.split_count, 2)
        self.assertEqual(calls[0], [1, 2, 3, 4, 5])
        self.assertNotEqual(calls[1], calls[0])

    def test_graph_terminal_failure_reports_affected_fact_ids(self):
        outcome = _extract_adaptive(
            [{"id": 7}], lambda _batch: (_ for _ in ()).throw(RuntimeError("MODEL_OUTPUT_TRUNCATED")),
        )
        self.assertEqual(outcome.payloads, [])
        self.assertEqual(outcome.failures[0]["fact_ids"], [7])
        self.assertFalse(GraphExtractionAgent().should_retry(RuntimeError("MODEL_OUTPUT_TRUNCATED")))

    def test_graph_batch_limit_is_a_resource_setting_not_semantic_selection(self):
        facts = [{"id": value, "content": f"事实 {value}"} for value in range(25)]
        with patch("app.graph.service.settings.graph_facts_per_batch", 8):
            batches = _fact_batches(facts)
        self.assertEqual(sum(len(batch) for batch in batches), len(facts))
        self.assertLessEqual(max(len(batch) for batch in batches), 8)

    def test_graph_artifact_status_never_marks_degraded_or_queued_as_done(self):
        self.assertEqual(_graph_artifact_status({"status": "pending"}), "pending")
        self.assertEqual(_graph_artifact_status({"status": "queued"}), "pending")
        self.assertEqual(_graph_artifact_status({"status": "degraded"}), "failed")
        self.assertEqual(_graph_artifact_status({"status": "partial_ready"}), "partial")

    def test_context_budget_tracks_serving_window_and_stage_reserves(self):
        with (
            patch("app.context.settings.model_context_window_tokens", 16000),
            patch("app.context.settings.structured_output_tokens", 3000),
            patch("app.context.settings.prompt_overhead_tokens", 2000),
            patch("app.context.settings.safety_margin_tokens", 1000),
        ):
            self.assertEqual(ContextManager({}).budget_tokens(), 10000)

    def test_graph_visualization_materializes_literal_value_targets(self):
        records = {
            "entities": [{"key": "entity-a", "name": "机构甲", "entity_type": "机构"}],
            "assertions": [{
                "subject_key": "entity-a",
                "target_key": "value-2027",
                "target_name": "2027年",
                "object_value": "2027年",
                "workspace_id": "default",
                "status": "validated",
            }],
        }
        nodes = _visualization_nodes(records)
        self.assertEqual({item["key"] for item in nodes}, {"entity-a", "value-2027"})
        self.assertEqual(nodes[-1]["entity_type"], "value")

    def test_heading_renderer_replaces_existing_prefix_with_template_numbering(self):
        strategy = HeadingNumbering({1: "cjk_comma", 2: "cjk_parenthesized"})
        self.assertEqual(
            format_heading(2, [1, 1], "1. 从概念提出到战略确立", strategy),
            "（一）从概念提出到战略确立",
        )
        self.assertEqual(
            format_heading(2, [2, 2], "2.3 关键技术支撑", strategy),
            "（二）关键技术支撑",
        )

    def test_all_heading_boundaries_strip_simple_arabic_prefixes(self):
        self.assertEqual(strip_heading_prefix("1. 法规体系构建"), "法规体系构建")
        self.assertEqual(_clean_generated_subheading("2. 法规体系构建"), "法规体系构建")
        self.assertEqual(strip_heading_prefix("2024年度工作安排"), "2024年度工作安排")

    def test_style_profile_name_rejects_prompt_schema_text(self):
        prompt_text = "报告类型,用 2-4 字简称,如:政策研究/情报快报/专题分析/风险研判/其他"
        self.assertEqual(_normalize_profile_label(prompt_text), "综合报告风格")
        self.assertEqual(_normalize_profile_label("工作总结"), "工作总结")

    def test_editorial_confidence_requires_actual_model_profile(self):
        self.assertEqual(
            _editorial_confidence({}, {"observed_metrics": {"sample_count": 12}}, 2, 12),
            "low",
        )
        self.assertEqual(
            _editorial_confidence({"tone": "正式"}, {"information_progression": "先事实后判断"}, 2, 12),
            "high",
        )

    def test_style_profile_requires_fact_and_judgment_realization(self):
        with self.assertRaisesRegex(ValueError, "CONTENT_REALIZATION_INCOMPLETE"):
            _validate_profile_payload({
                "writing_style": {"tone": "正式"},
                "writing_patterns": {"information_progression": "先事实后判断"},
            })
        _validate_profile_payload({
            "writing_style": {"tone": "正式"},
            "writing_patterns": {
                "fact_expression": "保留具体信息",
                "judgment_expression": "审慎判断",
                "fact_judgment_transition": "先事实后解释",
            },
        })

    def test_editorial_style_never_changes_evidence_extraction_contract(self):
        parameters = inspect.signature(EvidenceAgent.extract_facts).parameters
        self.assertNotIn("usage_guidance", parameters)

    def test_writer_receives_fact_and_judgment_realization_style(self):
        variant = StyleVariant(
            library_id=1,
            name="示例画像",
            writing_style={"tone": "审慎"},
            writing_patterns={
                "fact_expression": "保留具体名称和数字",
                "judgment_expression": "使用有依据的审慎判断",
            },
        )
        block = variant.writer_prompt_block()
        self.assertIn("fact_expression", block)
        self.assertIn("judgment_expression", block)
        self.assertNotIn("配对案例", block)

    def test_new_capabilities_have_independent_audit_tables(self):
        for table in (
            "material_comparison_runs", "material_comparison_items",
            "interaction_threads", "interaction_messages", "change_proposals",
            "interaction_notifications",
        ):
            self.assertIn(table, Base.metadata.tables)
        self.assertIn("evidence_usage_profile_json", Base.metadata.tables["style_variants"].c)
        proposals = Base.metadata.tables["change_proposals"]
        self.assertIn("execution_status", proposals.c)
        self.assertIn("candidate_version_id", proposals.c)

    def test_semantic_proposal_preserves_scope_in_recompute_instruction(self):
        instruction = _proposal_instruction({
            "artifact_type": "fact", "object_id": "18",
            "rationale": "需要重新核对限定条件",
            "after_json": '{"content":"重新核对适用范围"}',
        })
        self.assertIn("fact/18", instruction)
        self.assertIn("重新核对限定条件", instruction)
        self.assertIn("适用范围", instruction)

    def test_narrative_summary_reports_semantic_units(self):
        summary = _artifact_summary("narrative_plan", {
            "subsections": [{"title": "机制"}, {"title": "挑战"}],
            "core_message": "围绕机制与挑战形成主线",
        })
        self.assertIn("2 个小节", summary)

    def test_comparison_candidates_never_force_an_unrelated_baseline(self):
        candidates = _candidate_sets(
            [{"id": 10, "content": "新增材料说明设备采用可信执行环境", "dimension": "技术"}],
            [{"id": 2, "content": "本周会议于周一召开", "dimension": "会议"}],
        )
        self.assertEqual(candidates[10], [])
        decision = _fallback_decision({"content": "新增材料说明设备采用可信执行环境"}, [])
        self.assertEqual(decision["change_type"], "addition")
        self.assertIsNone(decision["baseline_fact_id"])

    def test_lineage_impact_uses_fact_binding_not_only_text_similarity(self):
        impacts = _lineage_impacts({"sentence_snapshot": [{
            "id": 8, "section": "风险分析", "paragraph": 2,
            "content": "旧正文", "source_refs": {"fact_ids": [3]},
        }]})
        self.assertEqual(impacts[3][0]["section"], "风险分析")
        self.assertEqual(impacts[3][0]["reason"], "lineage")

    def test_comparison_document_maps_changes_to_stable_report_sentences(self):
        document = _comparison_document(
            {"sentence_snapshot": [{
                "id": 8, "section": "风险分析", "paragraph": 2,
                "position": 1, "content": "原报告事实。", "source_refs": {"fact_ids": [3]},
            }]},
            [{
                "id": 21, "change_type": "conflict", "status": "pending_review",
                "confidence": "high", "impact": {"report_locations": [{"sentence_id": 8}]},
            }],
        )
        self.assertEqual(document["sentences"][0]["changes"][0]["item_id"], 21)
        self.assertEqual(document["sentences"][0]["changes"][0]["change_type"], "conflict")
        self.assertEqual(document["unmapped_item_ids"], [])

    def test_comparison_can_retain_related_material_without_calling_it_irrelevant(self):
        self.assertIn("related", CHANGE_TYPES)

    def test_comparison_metrics_separate_sentence_impacts_from_independent_findings(self):
        metrics = _comparison_metrics([
            {"change_type": "conflict", "impact_json": '{"report_locations":[{"sentence_id":8}]}'},
            {"change_type": "corroboration", "impact_json": '{"report_locations":[{"sentence_id":8}]}'},
            {"change_type": "addition", "impact_json": '{"report_locations":[]}'},
            {"change_type": "irrelevant", "impact_json": '{"report_locations":[]}'},
        ])
        self.assertEqual(metrics["reviewable_change_count"], 3)
        self.assertEqual(metrics["mapped_change_count"], 2)
        self.assertEqual(metrics["affected_sentence_count"], 1)
        self.assertEqual(metrics["independent_finding_count"], 1)

    def test_structure_policy_separates_layout_from_content_planning(self):
        variant = StyleVariant(library_id=1, structure={"sections": [{"title": "模板示例目录"}]})
        variant.structure_policy = {"mode": "FORMAT_ONLY"}
        self.assertIn("不得继承", variant.planner_prompt_block())
        variant.structure_policy = {"mode": "SOFT_STRUCTURE"}
        self.assertIn("弱参考", variant.planner_prompt_block())
        variant.structure_policy = {"mode": "HARD_STRUCTURE"}
        self.assertIn("必须遵守", variant.planner_prompt_block())

    def test_style_learning_samples_sections_instead_of_whole_document_slice(self):
        members = [{
            "filename": "approved.docx",
            "text": "一、背景\n这是第一节的完整自然段，用于说明背景和基本关系，不是占位说明。\n"
                    "二、分析\n这是第二节的分析自然段，先引用事实，再解释事实之间的联系和影响。\n"
                    "这是最后的收束自然段，用于形成谨慎结论并说明边界。",
            "headings": [{"text": "一、背景", "level": 1}, {"text": "二、分析", "level": 1}],
        }]
        examples = build_report_exemplars(members)
        self.assertEqual({item["section"] for item in examples}, {"一、背景", "二、分析"})
        self.assertTrue(all(item["provenance"] == "historical_report_upload" for item in examples))
        metrics = aggregate_style_metrics(examples)
        self.assertEqual(metrics["source_report_count"], 1)
        self.assertGreater(metrics["paragraph_chars"]["p50"], 0)

    def test_runtime_style_retrieval_uses_narrative_purpose_and_diversity(self):
        bank = [
            {"content": "先交代事实，再解释其影响。", "purpose": "解释影响", "sample_type": "analysis", "source_report": "A", "section": "研判", "approved": True},
            {"content": "概述研究范围和问题边界。", "purpose": "介绍背景", "sample_type": "opening", "source_report": "B", "section": "背景", "approved": True},
            {"content": "从证据出发形成审慎判断。", "purpose": "解释影响", "sample_type": "analysis", "source_report": "C", "section": "结论", "approved": True},
        ]
        selected = select_exemplars(bank, {"purpose": "解释影响", "sample_type": "analysis"}, limit=2)
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(item["sample_type"] == "analysis" for item in selected))
        self.assertEqual(len({item["source_report"] for item in selected}), 2)

    def test_style_diagnostic_is_advisory_and_never_rewrites(self):
        result = assess_style_alignment(
            "这是一个明显很短的句子。",
            {"observed_metrics": {"sentence_chars": {"p50": 80}, "sentences_per_paragraph": {"p50": 4}}},
            {"forbidden": ["明显"]},
        )
        self.assertFalse(result["auto_rewrite"])
        self.assertLess(result["score"], 100)
        self.assertIn("FORBIDDEN_TERMINOLOGY", {item["code"] for item in result["issues"]})

    def test_writer_style_intent_comes_from_narrative_roles(self):
        self.assertEqual(_style_sample_type({"discourse_flow": [{"role": "analysis"}]}, 2, 4), "analysis")
        self.assertEqual(_style_sample_type({"discourse_flow": []}, 1, 4), "opening")
        self.assertEqual(_style_sample_type({"discourse_flow": []}, 4, 4), "conclusion")

    def test_style_learning_job_exposes_progress_without_internal_paths(self):
        job = create_style_job([], job_id="test-empty-style-job")
        self.assertEqual(job["status"], "queued")
        self.assertNotIn("files", job)
        run_style_job(job["id"])
        finished = get_style_job(job["id"])
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "NO_UPLOADED_FILES")


if __name__ == "__main__":
    unittest.main()
