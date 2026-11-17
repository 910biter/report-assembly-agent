import unittest
import json
from contextlib import contextmanager
from unittest.mock import patch

from app.planning.planner import _fallback_local_restructure, PlannerAgent, update_plan
from app.models.report import ReportPlan


class PlannerStructureRevisionTests(unittest.TestCase):
    def test_concurrent_plan_change_prevents_locked_update(self):
        statements = []
        class Result:
            def first(self): return (4,)
        class Session:
            def execute(self, statement):
                statements.append(statement)
                return Result()
        @contextmanager
        def session_scope():
            yield Session()
        with patch("app.planning.planner.session_scope", session_scope):
            with self.assertRaisesRegex(ValueError, "PLAN_CHANGED_SINCE_PROPOSAL"):
                update_plan(ReportPlan(id=1, title="报告"), expected_plan_version=3)
        self.assertEqual(len(statements), 1)

    def test_ambiguous_numbered_existing_titles_are_not_silently_merged(self):
        row = {"chapter_plans": json.dumps([{"title": "一、政策"}, {"title": "二、政策"}], ensure_ascii=False),
               "final_plan_json": "{}"}
        class Result:
            def mappings(self): return self
            def first(self): return row
        class Session:
            def execute(self, statement): return Result()
        @contextmanager
        def session_scope():
            yield Session()
        with patch("app.planning.planner.session_scope", session_scope):
            with self.assertRaisesRegex(ValueError, "AMBIGUOUS_EXISTING_STRUCTURE"):
                PlannerAgent().revise_final_plan_structure(1, ["政策", "展望"], refine=False)

    def test_numbered_unchanged_chapter_retains_evidence_contract(self):
        row = {key: "" for key in ("title", "objective", "audience", "report_type", "core_question", "core_judgment", "narrative_logic", "user_requirements")}
        row.update(chapter_plans=json.dumps([
            {"title": "一、政策", "primary_fact_ids": [12]},
            {"title": "二、结论", "primary_fact_ids": [34]},
        ], ensure_ascii=False), final_plan_json="{}", budget="{}", dimensions="[]", evidence_needs="[]", required_facts="[]")
        class Result:
            def mappings(self): return self
            def first(self): return row
        class Session:
            def execute(self, statement): return Result()
        @contextmanager
        def session_scope():
            yield Session()
        with patch("app.planning.planner.session_scope", session_scope), patch("app.planning.planner.update_plan", side_effect=lambda plan, **kwargs: plan):
            result = PlannerAgent().revise_final_plan_structure(1, ["政策", "问题", "展望"], refine=False)
        self.assertEqual(result.chapter_plans[0]["primary_fact_ids"], [12])

    def test_split_reuses_subsection_evidence_in_order(self):
        source = [{
            "title": "问题与下一步工作",
            "target_words": 1200,
            "primary_fact_ids": [1, 2, 3, 4],
            "subsections": [
                {"title": "问题", "purpose": "识别风险", "primary_fact_ids": [1, 2]},
                {"title": "计划", "purpose": "提出行动", "primary_fact_ids": [3, 4]},
            ],
        }]
        result = _fallback_local_restructure(source, ["存在问题与风险研判", "下一步工作计划与建议"])
        self.assertEqual([item["title"] for item in result], ["存在问题与风险研判", "下一步工作计划与建议"])
        self.assertEqual(result[0]["primary_fact_ids"], [1, 2])
        self.assertEqual(result[1]["primary_fact_ids"], [3, 4])
        self.assertEqual(sum(item["target_words"] for item in result), 1200)

    def test_merge_preserves_all_evidence_ids(self):
        result = _fallback_local_restructure([
            {"title": "甲", "primary_fact_ids": [1, 2], "target_words": 500},
            {"title": "乙", "primary_fact_ids": [2, 3], "target_words": 700},
        ], ["综合研判"])
        self.assertEqual(result[0]["primary_fact_ids"], [1, 2, 3])
        self.assertEqual(result[0]["target_words"], 1200)


if __name__ == "__main__":
    unittest.main()
