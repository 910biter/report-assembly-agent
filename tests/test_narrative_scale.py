import unittest
from types import MethodType

from app.context import ContextManager
from app.context_budget import count_tokens
from app.planning.narrative import _sanitize_plan
from app.planning.scale import (
    normalize_chapter_budgets,
    normalize_execution_plan,
    parse_user_scale,
    reconcile_scale_budget,
)
from app.writing.writer import WriterAgent, _focused_unit_evidence, _subsection_generation_units
from app.writing.scale_execution import assess_chapter_output, measure_text_words


class NarrativeScaleTests(unittest.TestCase):
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
        self.assertIsNone(units[0]["plan"])

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
