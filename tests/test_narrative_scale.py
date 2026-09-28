import unittest
from contextlib import contextmanager
from types import MethodType
from unittest.mock import patch

from app.context import ContextManager
from app.context_budget import count_tokens
from app.planning.narrative import NarrativeAgent, _prompt_visible_ids, _sanitize_document_plan, _sanitize_plan
from app.planning.scale import (
    normalize_chapter_budgets,
    normalize_execution_plan,
    parse_user_scale,
    reconcile_scale_budget,
)
from app.writing.writer import (
    WriterAgent,
    _article_evidence_ids,
    _coerce_paragraphs,
    _evidence_execution_hint,
    _focused_unit_evidence,
    _subsection_generation_units,
)
from app.writing.scale_execution import (
    assess_chapter_output,
    assess_report_scale,
    measure_text_words,
    writer_output_token_limit,
    writer_output_retry_limit,
    writer_scale_prompt_hint,
    measure_report_body_words_by_section,
    report_minimum_words,
)


class NarrativeScaleTests(unittest.TestCase):
    def test_paragraph_level_citations_are_not_broadcast_to_each_sentence(self):
        paragraphs = _coerce_paragraphs({
            "paragraphs": [{
                "fact_ids": [1, 2],
                "inference_ids": [10],
                "sentences": [
                    {"text": "第一句只描述事实一。", "fact_ids": [1]},
                    {"text": "第二句表达一个判断。", "inference_ids": [10]},
                    {"text": "第三句没有提供逐句依据。"},
                ],
            }],
        })

        sentences = paragraphs[0]["sentences"]
        self.assertEqual([1], sentences[0]["fact_ids"])
        self.assertEqual([], sentences[0]["inference_ids"])
        self.assertEqual([], sentences[1]["fact_ids"])
        self.assertEqual([10], sentences[1]["inference_ids"])
        self.assertEqual([], sentences[2]["fact_ids"])
        self.assertEqual([], sentences[2]["inference_ids"])

    def test_writer_prompt_requires_minimal_sentence_specific_citations(self):
        self.assertIn("最小且逐句绑定", WriterAgent.role)
        self.assertIn("不得把整段证据列表复制到每一句", WriterAgent.role)
        self.assertIn('source_level="TRANSITION"', WriterAgent.role)

    def test_local_rewrite_cannot_cite_inference_omitted_from_its_context(self):
        writer = WriterAgent.__new__(WriterAgent)
        writer.last_call_id = "local-rewrite-test"
        captured = {}

        class Result:
            def mappings(self):
                return self

            def all(self):
                return [{"id": 12, "content": "旧段落。", "position": 10001}]

        class Session:
            def execute(self, _statement):
                return Result()

        session_calls = 0

        @contextmanager
        def fake_session_scope():
            nonlocal session_calls
            session_calls += 1
            if session_calls > 1:
                raise AssertionError("unavailable inference must not be persisted as a citation")
            yield Session()

        def fake_build_prompt(_stage, sections, _budget):
            captured["prompt"] = "\n".join(str(item) for item in sections)
            return "rewrite prompt", {}

        writer.generate_json = lambda *_args, **_kwargs: {
            "paragraphs": [{"sentences": [{
                "text": "一个新的判断。", "fact_ids": [], "inference_ids": [99],
            }]}],
        }
        with (
            patch("app.writing.writer.session_scope", fake_session_scope),
            patch("app.writing.writer.build_prompt_from_sections", fake_build_prompt),
        ):
            result = writer._rewrite_paragraph(
                report_id=1,
                chapter_title="Chapter",
                chapter_index=1,
                qa_item={"target_paragraph": 1, "topic_id": "T1", "missing_aspects": []},
                narrative_plan={"topics": [{"topic_id": "T1", "fact_ids": [1], "inference_ids": []}]},
                chapter_facts=[{"id": 1, "content": "当前主题事实"}],
                chapter_inferences=[{"id": 99, "content": "不相关推论", "based_fact_ids": [2]}],
                valid_fact_ids={1}, valid_inf_ids={99}, inf_levels={}, plan={}, report_memory={},
            )

        self.assertIsNone(result)
        self.assertNotIn("不相关推论", captured["prompt"])

    def test_prompt_visibility_uses_complete_records_not_candidate_inventory(self):
        items = [
            {"id": 1, "content": "shown fact"},
            {"id": 2, "content": "omitted fact"},
        ]
        self.assertEqual([1], _prompt_visible_ids("1. shown fact", items))

    def test_document_narrative_cannot_assign_fact_omitted_from_final_prompt(self):
        agent = NarrativeAgent()
        agent.generate_json = lambda *_args, **_kwargs: {
            "beats": [{"beat_id": "B1", "purpose": "主题", "fact_ids": [1, 2]}],
        }
        facts = [
            {"id": 1, "content": "shown fact"},
            {"id": 2, "content": "omitted fact"},
        ]
        with patch(
            "app.planning.narrative.build_prompt_from_sections",
            return_value=("1. shown fact", {}),
        ), patch("app.planning.narrative.update_call_funnel"):
            plan = agent.plan_document(
                task_id="", report_plan={"budget": {"target_words": 300}},
                facts=facts, inferences=[], run_id="run-1",
            )
        self.assertEqual([1], plan["beats"][0]["fact_ids"])

    def test_document_narrative_beats_keep_only_supplied_evidence(self):
        plan = _sanitize_document_plan(
            {"beats": [{"beat_id": "B1", "purpose": "开篇", "fact_ids": [1, 999], "inference_ids": [7, 888]}]},
            {"budget": {"target_words": 1200}, "chapter_plans": []}, {1, 2}, {7},
        )
        self.assertEqual([1], plan["beats"][0]["fact_ids"])
        self.assertEqual([7], plan["beats"][0]["inference_ids"])

    def test_document_paragraph_plan_preserves_each_unmerged_beat(self):
        plan = _sanitize_document_plan(
            {"beats": [
                {"beat_id": "B1", "purpose": "起点", "fact_ids": [1]},
                {"beat_id": "B2", "purpose": "转折", "fact_ids": [2]},
            ], "paragraphs": [{
                "paragraph_id": "P1", "beat_ids": ["B1"], "purpose": "开篇", "fact_ids": [1],
            }]},
            {"budget": {"target_words": 800}, "chapter_plans": []}, {1, 2}, set(),
        )
        self.assertEqual([["B1"], ["B2"]], [item["beat_ids"] for item in plan["paragraphs"]])
        self.assertEqual(800, sum(item["target_words"] for item in plan["paragraphs"]))

    def test_article_evidence_is_collected_from_final_plan_not_titles(self):
        fact_ids, inference_ids = _article_evidence_ids([{
            "title": "任意标题",
            "primary_fact_ids": [1], "primary_inference_ids": [9],
            "subsections": [{"fact_ids": [2], "inference_ids": [10]}],
        }])
        self.assertEqual({1, 2}, fact_ids)
        self.assertEqual({9, 10}, inference_ids)
    def test_final_planner_context_is_coverage_driven_and_not_first_40_inferences(self):
        manager = ContextManager({"id": "task"})
        manager.budget_tokens = MethodType(lambda self, stage="structured": 5000, manager)
        facts = [
            {
                "id": index,
                "content": f"主题{index % 3}的独立事实{index}，包含可核验数据{index}",
                "dimension": f"维度{index % 3}",
                "source_files": [f"source-{index % 4}.pdf"],
            }
            for index in range(1, 31)
        ]

        def fake_retrieve(self, query, source, top_k, used_fact_ids=None):
            offset = sum(ord(char) for char in query) % 10
            return (source[offset:] + source[:offset])[:top_k]

        manager._retrieve_facts = MethodType(fake_retrieve, manager)
        inferences = [{
            "id": index,
            "content": f"普通推论{index}",
            "based_fact_ids": [1],
            "dimension": "普通维度",
            "confidence_level": "medium",
        } for index in range(1, 46)]
        inferences.append({
            "id": 99,
            "content": "关键事实支持的跨维度核心判断",
            "based_fact_ids": [30],
            "dimension": "关键维度",
            "confidence_level": "high",
        })
        context = manager.for_final_planner(
            "测试主题",
            "覆盖不同来源并形成合理目录",
            {
                "core_question": "如何形成结构",
                "analysis_global_meta": {"critical_fact_ids": [30], "coverage_status": {"维度A": "SUFFICIENT"}},
                "evidence_needs": [{"need": "识别主要差异"}],
            },
            facts,
            inferences,
            [],
            "模板风格必须保留",
            "报告策略必须保留",
        )

        self.assertIn("fact_id=30", context)
        self.assertIn("inference_id=99", context)
        self.assertIn("模板风格必须保留", context)
        self.assertIn("报告策略必须保留", context)
        self.assertLessEqual(count_tokens(context), 5000)

    def test_final_planner_fact_selection_preserves_sources_and_removes_semantic_duplicates(self):
        manager = ContextManager({"id": "task"})
        facts = [
            {"id": 1, "content": "甲地区产业规模达到一百亿元", "source_files": ["a.pdf"]},
            {"id": 2, "content": "甲地区产业规模达到一百亿元", "source_files": ["a.pdf"]},
            {"id": 3, "content": "乙地区建立了新的监管机制", "source_files": ["b.pdf"]},
            {"id": 4, "content": "丙地区形成了不同的应用场景", "source_files": ["c.pdf"]},
        ]
        manager._retrieve_facts = MethodType(
            lambda self, query, source, top_k, used_fact_ids=None: source[:top_k], manager
        )

        selected = manager._select_final_planner_facts(facts, ["产业", "监管"], {4}, 1200)
        ids = {item["id"] for item in selected}
        sources = {source for item in selected for source in item["source_files"]}

        self.assertIn(4, ids)
        self.assertFalse({1, 2} <= ids)
        self.assertEqual({"a.pdf", "b.pdf", "c.pdf"}, sources)

    def test_writer_retrieval_is_coverage_and_context_driven(self):
        manager = ContextManager({"id": "task"})
        manager.budget_tokens = MethodType(lambda self, stage="structured": 1200, manager)
        facts = [
            {"id": index, "content": f"fact-{index}-" + "x" * 76}
            for index in range(1, 31)
        ]

        def fake_retrieve(self, query, source, top_k, used_fact_ids=None):
            offsets = {"chapter": 0, "topic-a": 10, "topic-b": 20}
            start = offsets.get(query, 0)
            return source[start:start + top_k]

        manager._retrieve_facts = MethodType(fake_retrieve, manager)
        selected, _, _ = manager.for_writer_section(
            "chapter",
            facts,
            [],
            "",
            required_fact_ids={30},
            coverage_queries=["topic-a", "topic-b"],
        )

        selected_ids = {item["id"] for item in selected}
        self.assertIn(30, selected_ids)
        self.assertTrue(selected_ids & set(range(11, 21)))
        self.assertTrue(selected_ids & set(range(21, 30)))
        self.assertGreater(len(selected), 3)

    def test_explicit_user_range_remains_authoritative_after_final_planning(self):
        budget = reconcile_scale_budget(
            "正文约 15000-20000 字",
            {"target_words": 18000},
            {"target_words": 5000, "max_words": 7000, "evidence_status": "limited"},
        )

        self.assertEqual(18000, budget["target_words"])
        self.assertEqual("user_explicit", budget["target_source"])
        self.assertEqual(7000, budget["evidence_supported_max_words"])
        self.assertEqual("limited", budget["evidence_status"])
        self.assertEqual(18000, budget["target_words"])
        self.assertEqual(
            {"min_words": 15000, "max_words": 20000, "source": "user_range"},
            budget.get("output_bounds"),
        )

    def test_final_plan_reestimates_system_suggested_scale_from_evidence(self):
        budget = reconcile_scale_budget(
            "",
            {"target_words": 9000},
            {"target_words": 4200, "max_words": 5200, "evidence_status": "sufficient"},
        )

        self.assertEqual(4200, budget["target_words"])
        self.assertEqual(9000, budget["preliminary_target_words"])
        self.assertEqual("final_plan", budget["target_source"])
        self.assertEqual(
            {"min_words": 3780, "max_words": 4620, "source": "system_target_tolerance"},
            budget.get("output_bounds"),
        )

    def test_approximate_user_target_is_not_replaced_and_gets_auditable_bounds(self):
        budget = reconcile_scale_budget(
            "请写约3000字",
            {"target_words": 5000},
            {"target_words": 7000, "max_words": 9000, "evidence_status": "sufficient"},
        )

        self.assertEqual(3000, budget["target_words"])
        self.assertEqual("user_explicit", budget["target_source"])
        self.assertEqual(
            {"min_words": 2700, "max_words": 3300, "source": "user_target_tolerance"},
            budget.get("output_bounds"),
        )

    def test_final_planner_target_wins_when_it_is_inside_user_range(self):
        budget = reconcile_scale_budget(
            "15000-20000字",
            {"target_words": 18000},
            {"target_words": 19500, "evidence_status": "sufficient"},
        )

        self.assertEqual(19500, budget["target_words"])
        self.assertEqual(
            {"min_words": 15000, "max_words": 20000, "source": "user_range"},
            budget.get("output_bounds"),
        )

    def test_explicit_word_ceiling_and_floor_keep_their_one_sided_meaning(self):
        ceiling = reconcile_scale_budget("正文不超过3000字", {}, {"target_words": 2600})
        floor = reconcile_scale_budget("正文至少3000字", {}, {"target_words": 3800})

        self.assertEqual(3000, ceiling["target_words"])
        self.assertEqual({"min_words": 0, "max_words": 3000, "source": "user_maximum"}, ceiling["output_bounds"])
        self.assertEqual(3000, floor["target_words"])
        self.assertEqual({"min_words": 3000, "max_words": 0, "source": "user_minimum"}, floor["output_bounds"])

    def test_final_budget_source_survives_writer_plan_normalization(self):
        budget = reconcile_scale_budget(
            "", {"target_words": 9000}, {"target_words": 4200},
        )
        normalized = normalize_execution_plan({
            "user_requirements": "",
            "budget": budget,
            "chapter_plans": [{"title": "A", "target_words": 4200}],
        })

        self.assertEqual(4200, normalized["budget"]["target_words"])
        self.assertEqual("final_plan", normalized["budget"]["target_source"])
        self.assertEqual(9000, normalized["budget"]["preliminary_target_words"])

    def test_writer_output_tokens_scale_with_unit_target_and_respect_stage_cap(self):
        self.assertEqual(981, writer_output_token_limit(500, 3072, 1.45))
        self.assertEqual(512, writer_output_token_limit(174, 3072, 1.45))
        self.assertEqual(823, writer_output_token_limit(391, 3072, 1.45))
        self.assertEqual(3072, writer_output_token_limit(0, 3072, 1.45))
        self.assertEqual(3072, writer_output_token_limit(3000, 3072, 1.45))

    def test_writer_truncation_retry_escalates_once_without_repeating_same_cap(self):
        writer = WriterAgent.__new__(WriterAgent)
        caps = []

        def fake_generate_json(_prompt, max_tokens=None, **_kwargs):
            caps.append(max_tokens)
            if len(caps) == 1:
                raise RuntimeError("MODEL_OUTPUT_TRUNCATED")
            return {"paragraphs": []}

        writer.generate_json = fake_generate_json
        payload, used_cap = writer._generate_json_with_budget_retry("prompt", 512)

        self.assertEqual({"paragraphs": []}, payload)
        self.assertEqual([512, 1024], caps)
        self.assertEqual(1024, used_cap)
        self.assertEqual(3072, writer_output_retry_limit(1600, 3072))
        self.assertEqual(3072, writer_output_retry_limit(3072, 3072))

    def test_writer_does_not_retry_identical_truncated_request(self):
        writer = WriterAgent.__new__(WriterAgent)
        self.assertFalse(writer.should_retry(RuntimeError("MODEL_OUTPUT_TRUNCATED")))
        self.assertTrue(writer.should_retry(RuntimeError("temporary transport error")))

    def test_sufficient_evidence_guidance_enforces_declared_unit_minimum(self):
        hint = _evidence_execution_hint({
            "evidence_status": "sufficient", "target_words": 174,
            "minimum_words": 139,
        })

        self.assertIn("最低完成要求约 139 字", hint)
        self.assertIn("未达到最低完成要求前不得收束", hint)

    def test_sufficient_evidence_without_declared_minimum_does_not_invent_one(self):
        hint = _evidence_execution_hint({
            "evidence_status": "sufficient", "target_words": 174,
        })

        self.assertIn("目标不是最低字数", hint)
        self.assertNotIn("最低完成要求", hint)

    def test_limited_evidence_can_underfill_declared_unit_minimum(self):
        hint = _evidence_execution_hint({
            "evidence_status": "limited", "target_words": 174,
            "minimum_words": 139,
        })

        self.assertIn("允许低于最低完成要求 139 字", hint)
        self.assertIn("不得重复事实", hint)

    def test_writer_scale_hint_keeps_report_bounds_and_nested_allocations_distinct(self):
        hint = writer_scale_prompt_hint(
            {
                "target_words": 3000,
                "soft_max_words": 3200,
                "output_bounds": {"min_words": 2700, "max_words": 3300},
            },
            chapter_target_words=900,
            unit_target_words=500,
            unit_minimum_words=450,
            evidence_status="sufficient",
        )

        self.assertIn("全文目标约 3000 字", hint)
        self.assertIn("全文允许范围 2700–3300 字", hint)
        self.assertIn("全文软控制线 3200 字", hint)
        self.assertIn("本章分配约 900 字", hint)
        self.assertIn("当前交付单元目标约 500 字", hint)
        self.assertIn("当前交付单元最低完成要求约 450 字", hint)
        self.assertIn("最终规模评估判定证据充足", hint)
        self.assertIn("未达到本单元最低完成要求前不得收束", hint)
        self.assertIn("全文预算的内部拆分，不可相加", hint)
        self.assertIn("全文不得超过上限", hint)

    def test_writer_scale_hint_preserves_one_sided_user_minimum(self):
        hint = writer_scale_prompt_hint(
            {"target_words": 3000, "output_bounds": {"min_words": 3000}},
            unit_target_words=600,
        )

        self.assertIn("全文最低要求 3000 字", hint)
        self.assertNotIn("全文上限", hint)

    def test_insufficient_evidence_can_underfill_without_weakening_sufficient_case(self):
        hint = writer_scale_prompt_hint(
            {"target_words": 3000, "output_bounds": {"min_words": 2700}},
            unit_target_words=600,
            evidence_status="insufficient",
        )

        self.assertIn("证据有限或不足", hint)
        self.assertIn("允许低于", hint)
        self.assertNotIn("未达到本单元最低完成要求前不得收束", hint)

    def test_report_minimum_uses_explicit_output_bound_before_default_ratio(self):
        self.assertEqual(
            2700,
            report_minimum_words(
                3000,
                {"output_bounds": {"min_words": 2700, "max_words": 3300}},
                default_ratio=0.8,
            ),
        )
        self.assertEqual(2400, report_minimum_words(3000, {}, default_ratio=0.8))

    def test_report_scale_excludes_subheading_rows_from_body_count(self):
        rows = [
            {"section": "Chapter 1", "source_level": "SUBHEADING", "content": "小标题不计入正文"},
            {"section": "Chapter 1", "source_level": "MATERIAL_FACT", "content": "正文事实"},
            {"section": "Chapter 2", "source_level": "INFERENCE", "content": "第二章推论内容"},
        ]
        self.assertEqual(
            {"Chapter 1": measure_text_words("正文事实"), "Chapter 2": measure_text_words("第二章推论内容")},
            measure_report_body_words_by_section(rows),
        )

    def test_continuous_article_prompt_distinguishes_report_and_group_targets(self):
        writer = WriterAgent.__new__(WriterAgent)
        captured = {}
        writer.last_call_id = "article-scale-test-call"

        def fake_generate_json(prompt, max_tokens=None, **_kwargs):
            captured["prompt"] = prompt
            captured["max_tokens"] = max_tokens
            return {"paragraphs": []}

        writer.generate_json = fake_generate_json
        with patch("app.writing.writer.publish_context_audit"), patch("app.writing.writer.update_call_funnel"):
            result = writer._generate_article_pass(
                plan={"title": "Report", "budget": {
                    "target_words": 3000,
                    "output_bounds": {"min_words": 2700, "max_words": 3300},
                    "evidence_status": "sufficient",
                }},
                article_plan={"target_words": 650, "group_index": 1, "group_count": 2},
                facts=[], inferences=[], style_block="", allowed_fact_ids=set(),
                allowed_inference_ids=set(), institution_rules=None, task_profile=None,
                policy_block="", minimum_ratio=0.8,
            )

        self.assertEqual([], result)
        self.assertIn("全文目标约 3000 字", captured["prompt"])
        self.assertIn("当前文章片段目标约 650 字", captured["prompt"])
        self.assertIn("当前文章片段最低完成要求约 585 字", captured["prompt"])
        self.assertIn("最终规模评估判定证据充足", captured["prompt"])
        self.assertNotIn("证据不足时允许欠填", captured["prompt"])
        self.assertNotIn("全文目标:约 650 字", captured["prompt"])

    def test_writer_system_prompt_matches_subsection_generation_granularity(self):
        self.assertIn("当前交付单元", WriterAgent.role)
        self.assertNotIn("每次只撰写一个章节", WriterAgent.role)
        self.assertIn("不得为保留计划段数制造单句段", WriterAgent.role)
        self.assertIn("paragraph.target_words 是段落篇幅的分配参考", WriterAgent.role)
        self.assertIn("相邻论证连续且前段仅承担引出或过渡时", NarrativeAgent.role)
        self.assertIn("paragraph.target_words 只是篇幅分配参考", NarrativeAgent.role)

    def test_chapter_writer_receives_nested_report_chapter_and_unit_budgets(self):
        writer = WriterAgent.__new__(WriterAgent)
        captured = {}
        writer.last_call_id = "scale-test-call"

        def fake_generate_json(_prompt, max_tokens=None, **_kwargs):
            captured["max_tokens"] = max_tokens
            captured["prompt"] = _prompt
            return {"paragraphs": [{"sentences": [{
                "text": "材料明确记载了这一事实。", "fact_ids": [1], "inference_ids": [],
            }]}]}

        writer.generate_json = fake_generate_json
        with patch("app.writing.writer.publish_context_audit"), patch("app.writing.writer.update_call_funnel"):
            writer._generate_chapter_pass(
                "Chapter", 1, 1, ["Chapter"],
                [{"id": 1, "content": "材料明确记载了这一事实"}], [], "",
                {"title": "Report", "budget": {
                    "target_words": 3000, "soft_max_words": 3200,
                    "output_bounds": {"min_words": 2700, "max_words": 3300},
                }}, {}, {1}, set(),
                chapter_plan={"title": "Chapter", "target_words": 900},
                generation_unit={
                    "index": 1, "count": 1, "title": "", "target_words": 500,
                    "plan": {"fact_ids": [1]}, "evidence_status": "sufficient",
                },
            )

        self.assertEqual(981, captured["max_tokens"])
        self.assertIn("全文目标约 3000 字", captured["prompt"])
        self.assertIn("全文允许范围 2700–3300 字", captured["prompt"])
        self.assertIn("本章分配约 900 字", captured["prompt"])
        self.assertIn("当前交付单元目标约 500 字", captured["prompt"])
        self.assertIn("全文预算的内部拆分，不可相加", captured["prompt"])

    def test_chapter_writer_drops_short_uncited_claim_but_keeps_marked_transition(self):
        writer = WriterAgent.__new__(WriterAgent)
        writer.last_call_id = "citation-gate-test"
        writer.generate_json = lambda *_args, **_kwargs: {
            "paragraphs": [{"sentences": [
                {"text": "TrustZone 的设计几乎没有性能代价。", "fact_ids": [], "inference_ids": []},
                {"text": "接下来讨论其适用边界。", "fact_ids": [], "inference_ids": [], "source_level": "TRANSITION"},
            ]}],
        }

        with patch("app.writing.writer.publish_context_audit"), patch("app.writing.writer.update_call_funnel"):
            result = writer._generate_chapter_pass(
                "Chapter", 1, 1, ["Chapter"],
                [{"id": 1, "content": "TrustZone 的性能影响较小。"}], [], "",
                {"title": "Report", "budget": {"target_words": 100}}, {}, {1}, set(),
                chapter_plan={"title": "Chapter", "target_words": 100},
                generation_unit={"target_words": 100, "plan": {"fact_ids": [1]}},
            )

        self.assertEqual(["接下来讨论其适用边界。"], [item["text"] for item in result])
        self.assertEqual("TRANSITION", result[0]["source_level"])

    def test_article_writer_drops_short_uncited_substantive_sentence(self):
        writer = WriterAgent.__new__(WriterAgent)
        writer.last_call_id = "article-citation-gate-test"
        writer.generate_json = lambda *_args, **_kwargs: {
            "paragraphs": [{"sentences": [{
                "text": "TrustZone 的设计几乎没有性能代价。", "fact_ids": [], "inference_ids": [],
            }]}],
        }

        with patch("app.writing.writer.publish_context_audit"), patch("app.writing.writer.update_call_funnel"):
            result = writer._generate_article_pass(
                plan={"title": "Report", "budget": {"target_words": 100}},
                article_plan={"target_words": 100},
                facts=[{"id": 1, "content": "TrustZone 的性能影响较小。"}],
                inferences=[], style_block="", allowed_fact_ids={1}, allowed_inference_ids=set(),
                institution_rules=None, task_profile=None, policy_block="", minimum_ratio=0.8,
            )

        self.assertEqual([], result)

    def test_report_scale_audit_flags_overrun_and_keeps_in_range_output(self):
        bounds = {"min_words": 2700, "max_words": 3300, "source": "user_target_tolerance"}
        self.assertTrue(assess_report_scale(3000, 3400, bounds)["over_budget"])
        self.assertEqual(100, assess_report_scale(3000, 3400, bounds)["overrun_words"])
        self.assertFalse(assess_report_scale(3000, 3200, bounds)["over_budget"])
        self.assertTrue(assess_report_scale(3000, 2591, bounds)["below_minimum"])

    def test_exact_user_target_is_parsed_with_wan_unit(self):
        scale = parse_user_scale("请形成一篇2万字左右的研究综述")

        self.assertEqual(20000, scale["target_words"])

    def test_chapter_and_subsection_budgets_sum_to_frozen_target(self):
        chapters = [
            {"title": "A", "target_words": 1, "subsections": [
                {"title": "A1", "target_words": 1}, {"title": "A2", "target_words": 1},
            ]},
            {"title": "B", "target_words": 3, "subsections": [
                {"title": "B1", "target_words": 1}, {"title": "B2", "target_words": 2},
            ]},
        ]

        normalized = normalize_chapter_budgets(chapters, 20000)

        self.assertEqual(20000, sum(item["target_words"] for item in normalized))
        for chapter in normalized:
            self.assertEqual(
                chapter["target_words"],
                sum(item["target_words"] for item in chapter["subsections"]),
            )

    def test_single_subsection_budget_matches_its_chapter(self):
        normalized = normalize_chapter_budgets([{
            "title": "单小节章节", "target_words": 573,
            "subsections": [{"title": "唯一小节", "target_words": 900}],
        }], 3500)

        self.assertEqual(3500, normalized[0]["target_words"])
        self.assertEqual(3500, normalized[0]["subsections"][0]["target_words"])

    def test_system_scale_uses_one_effective_minimum_and_preserves_evidence_estimate(self):
        budget = reconcile_scale_budget(
            "",
            {"target_words": 3500, "min_words": 2800},
            {"target_words": 3500, "evidence_status": "sufficient"},
        )

        self.assertEqual(2800, budget["min_words"])
        self.assertEqual(3150, budget["effective_min_words"])
        self.assertEqual(3150, report_minimum_words(3500, budget, default_ratio=0.8))
        audit = assess_report_scale(
            3500, 3030, budget["output_bounds"],
            minimum_words=report_minimum_words(3500, budget, default_ratio=0.8),
        )
        self.assertTrue(audit["below_minimum"])
        self.assertEqual(3150, audit["shortfall_words"] + 3030)

    def test_scale_minimum_normalization_is_idempotent(self):
        initial = reconcile_scale_budget(
            "", {"target_words": 3500, "min_words": 2800},
            {"target_words": 3500, "evidence_status": "sufficient"},
        )
        normalized = reconcile_scale_budget("", initial, {})

        self.assertEqual(initial["min_words"], normalized["min_words"])
        self.assertEqual(initial["effective_min_words"], normalized["effective_min_words"])

    def test_historical_plan_recovers_budget_without_overriding_user_range(self):
        plan = normalize_execution_plan({
            "user_requirements": "正文约 15000-20000 字",
            "budget": {},
            "chapter_plans": [
                {"title": "A", "target_words": 3800},
                {"title": "B", "target_words": 4200},
                {"title": "C", "target_words": 4800},
                {"title": "D", "target_words": 4500},
            ],
        })

        self.assertEqual(17300, plan["budget"]["target_words"])
        self.assertEqual("chapter_budgets", plan["budget"]["recovered_from"])
        self.assertEqual(17300, sum(item["target_words"] for item in plan["chapter_plans"]))

    def test_fallback_preserves_subsections_and_chapter_budget(self):
        chapter = {
            "title": "Architecture Review",
            "target_words": 4500,
            "subsections": [
                {"title": "Roots", "fact_ids": [1, 2], "detail_level": "expand"},
                {"title": "Protocols", "fact_ids": [3], "inference_ids": [10]},
                {"title": "Limits", "fact_ids": [4], "inference_ids": [11]},
            ],
        }

        plan = _sanitize_plan({}, chapter, {1, 2, 3, 4}, {10, 11})

        self.assertEqual(3, len(plan["topics"]))
        self.assertEqual(3, len(plan["subsections"]))
        self.assertEqual(4500, sum(item["target_words"] for item in plan["subsections"]))
        self.assertNotIn("paragraph_plan", plan)
        self.assertTrue(all(item["paragraphs"] for item in plan["subsections"]))

    def test_declared_subsection_targets_are_normalized_to_chapter_budget(self):
        payload = {
            "topics": [
                {"topic_id": "T1", "name": "A", "fact_ids": [1]},
                {"topic_id": "T2", "name": "B", "fact_ids": [2]},
            ],
            "subsections": [
                {"title": "A", "topic_ids": ["T1"], "fact_ids": [1], "target_words": 1},
                {"title": "B", "topic_ids": ["T2"], "fact_ids": [2], "target_words": 3},
            ],
        }

        plan = _sanitize_plan(payload, {"title": "X", "target_words": 4000}, {1, 2}, set())

        self.assertEqual([1000, 3000], [item["target_words"] for item in plan["subsections"]])

    def test_subsections_are_the_single_narrative_source(self):
        payload = {
            "subsections": [{
                "title": "Trust roots",
                "purpose": "Explain the trust chain",
                "core_message": "Hardware measurement anchors verification",
                "fact_ids": [1, 2],
                "inference_ids": [10],
                "target_words": 2000,
                "discourse_flow": [
                    {"role": "background", "fact_ids": [1]},
                    {"role": "analysis", "fact_ids": [2], "inference_ids": [10]},
                ],
            }, {
                "title": "Protocols",
                "fact_ids": [3],
                "target_words": 2000,
            }],
        }

        plan = _sanitize_plan(payload, {"title": "X", "target_words": 4000}, {1, 2, 3}, {10})

        self.assertEqual(2, len(plan["topics"]))
        self.assertEqual(["T1"], plan["subsections"][0]["topic_ids"])
        self.assertEqual([1], plan["topics"][0]["discourse_flow"][0]["facts"])
        self.assertEqual([10], plan["topics"][0]["discourse_flow"][1]["inferences"])

    def test_no_evidence_is_explicitly_insufficient_without_shrinking_target(self):
        plan = _sanitize_plan({}, {"title": "X", "target_words": 3000}, set(), set())

        self.assertEqual("insufficient", plan["evidence_status"])
        self.assertTrue(plan["evidence_limited"])
        self.assertEqual(3000, plan["target_words"])

    def test_generation_units_follow_semantic_subsections_not_length_batches(self):
        narrative = {
            "subsections": [
                {"title": "A", "fact_ids": [1], "target_words": 1000},
                {"title": "B", "fact_ids": [2], "target_words": 1500},
                {"title": "C", "fact_ids": [3], "target_words": 2000},
            ]
        }

        units = _subsection_generation_units(narrative, 4500, 0.8)

        self.assertEqual(["A", "B", "C"], [unit["title"] for unit in units])
        self.assertEqual([1000, 1500, 2000], [unit["target_words"] for unit in units])
        self.assertEqual([800, 1200, 1600], [unit["minimum_words"] for unit in units])

    def test_generation_unit_uses_its_assigned_evidence(self):
        facts, inferences = _focused_unit_evidence(
            [{"id": 1}, {"id": 2}], [{"id": 9}, {"id": 10}],
            {"fact_ids": [2], "inference_ids": [10]}, {"primary_fact_ids": [1]},
        )
        self.assertEqual([2], [item["id"] for item in facts])
        self.assertEqual([10], [item["id"] for item in inferences])

    def test_incremental_output_is_measured_without_automatic_acceptance(self):
        assessment = assess_chapter_output(
            target_words=4000,
            generated_text="新" * 900,
            previous_text="旧" * 1800,
        )

        self.assertNotIn("candidate_action", assessment)
        self.assertEqual(-900, assessment["change_words"])
        self.assertEqual(900, measure_text_words("新" * 900))

    def test_evidence_shortage_is_recorded_without_rejecting_output(self):
        assessment = assess_chapter_output(
            target_words=4000,
            generated_text="新" * 900,
            previous_text="旧" * 1800,
            evidence_limited=True,
        )

        self.assertTrue(assessment["underfilled"])
        self.assertEqual("evidence_limited", assessment["underfill_reason"])

    def test_writer_calls_each_subsection_once_and_renders_planned_heading(self):
        writer = WriterAgent.__new__(WriterAgent)
        calls = []

        def fake_pass(self, *args, chapter_plan=None, generation_unit=None, **kwargs):
            calls.append(generation_unit)
            marker = chr(0x4E00 + len(calls))
            return [{
                "text": marker * 100,
                "fact_ids": [1],
                "inference_ids": [],
                "paragraph": 1,
                "source_level": "",
                "_origin_call_id": f"call-{len(calls)}",
            }]

        writer._generate_chapter_pass = MethodType(fake_pass, writer)
        narrative = {
            "subsections": [
                {"title": "Roots", "target_words": 1000, "fact_ids": [1]},
                {"title": "Protocols", "target_words": 1500, "fact_ids": [1]},
                {"title": "Limits", "target_words": 2000, "fact_ids": [1]},
            ]
        }
        result = writer._generate_chapter(
            "Chapter", 1, 1, ["Chapter"],
            [{"id": 1, "content": "evidence"}], [], "",
            {"title": "Report"}, {}, {1}, set(),
            chapter_plan={"title": "Chapter", "target_words": 4500},
            narrative_plan=narrative,
        )

        self.assertEqual(3, len(calls))
        headings = [item for item in result if item.get("source_level") == "SUBHEADING"]
        self.assertEqual(["Roots", "Protocols", "Limits"], [item["text"] for item in headings])
        for heading in headings:
            self.assertTrue(any(
                item.get("source_level") != "SUBHEADING"
                and item.get("paragraph") == heading.get("paragraph")
                for item in result
            ))
        self.assertEqual(3, writer._last_chapter_generation_stats["generation_calls"])
        self.assertTrue(all(
            item["planned_fact_coverage"] == 1.0
            for item in writer._last_chapter_generation_stats["generation_unit_stats"]
        ))

    def test_writer_does_not_retry_an_underfilled_subsection(self):
        writer = WriterAgent.__new__(WriterAgent)
        calls = []

        def fake_pass(self, *args, generation_unit=None, **kwargs):
            calls.append(generation_unit)
            return [{
                "text": "短" * 100,
                "fact_ids": [1],
                "inference_ids": [],
                "paragraph": 1,
                "source_level": "",
                "_origin_call_id": f"call-{len(calls)}",
            }]

        writer._generate_chapter_pass = MethodType(fake_pass, writer)
        writer._generate_chapter(
            "Chapter", 1, 1, ["Chapter"],
            [{"id": 1, "content": "evidence"}], [], "",
            {"title": "Report"}, {}, {1}, set(),
            chapter_plan={"title": "Chapter", "target_words": 4000},
            narrative_plan={"subsections": [
                {"title": "A", "target_words": 2000, "fact_ids": [1]},
                {"title": "B", "target_words": 2000, "fact_ids": [1]},
            ]},
        )

        self.assertEqual(2, len(calls))
        stats = writer._last_chapter_generation_stats["generation_unit_stats"]
        self.assertTrue(all(item["underfilled"] for item in stats))
        self.assertTrue(all(item["underfill_reason"] == "generation_budget_not_fulfilled" for item in stats))

    def test_evidence_limited_underfill_is_recorded_as_evidence_reason(self):
        writer = WriterAgent.__new__(WriterAgent)

        def fake_pass(self, *args, **kwargs):
            return [{
                "text": "短" * 100,
                "fact_ids": [1],
                "inference_ids": [],
                "paragraph": 1,
                "source_level": "",
                "_origin_call_id": "limited",
            }]

        writer._generate_chapter_pass = MethodType(fake_pass, writer)
        writer._generate_chapter(
            "Chapter", 1, 1, ["Chapter"],
            [{"id": 1, "content": "evidence"}], [], "",
            {"title": "Report"}, {}, {1}, set(),
            chapter_plan={"title": "Chapter", "target_words": 2000},
            narrative_plan={
                "evidence_status": "limited",
                "evidence_reason": "missing comparison data",
            },
        )

        stats = writer._last_chapter_generation_stats["generation_unit_stats"]
        self.assertEqual("evidence_limited", stats[0]["underfill_reason"])

    def test_single_subsection_is_flattened(self):
        units = _subsection_generation_units({
            "subsections": [{"title": "Only", "target_words": 3000, "fact_ids": [1]}]
        }, 3000)

        self.assertEqual(1, len(units))
        self.assertEqual("", units[0]["title"])
        self.assertEqual([1], units[0]["plan"]["fact_ids"])

    def test_no_evidence_still_uses_only_one_call(self):
        writer = WriterAgent.__new__(WriterAgent)
        calls = []

        def empty_pass(self, *args, **kwargs):
            calls.append(1)
            return []

        writer._generate_chapter_pass = MethodType(empty_pass, writer)
        result = writer._generate_chapter(
            "Chapter", 1, 1, ["Chapter"], [], [], "",
            {"title": "Report"}, {}, set(), set(),
            chapter_plan={"title": "Chapter", "target_words": 5000},
            narrative_plan={},
        )

        self.assertEqual([], result)
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
