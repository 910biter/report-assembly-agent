import unittest

from app.planning.planner import _fallback_local_restructure


class PlannerStructureRevisionTests(unittest.TestCase):
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
