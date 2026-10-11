import unittest

from app.workflow.controller import _analysis_groups


def _fact(fact_id: int, *, dimension: str = "D", need_id: int = 1,
          fact_type: str = "STATEMENT", content: str = "fact") -> dict:
    return {
        "id": fact_id,
        "dimension": dimension,
        "need_id": need_id,
        "fact_type": fact_type,
        "content": content,
        "sources": ["source"],
    }


class AnalysisGroupingTests(unittest.TestCase):
    def test_merges_multiple_ids_when_the_budget_allows(self):
        facts = [
            _fact(1, need_id=1),
            _fact(2, need_id=2),
            _fact(3, need_id=3),
        ]

        groups = _analysis_groups(facts, token_budget=10_000)

        self.assertEqual(len(groups), 1)
        self.assertEqual([item["id"] for item in next(iter(groups.values()))], [1, 2, 3])

    def test_moves_the_last_complete_id_group_to_the_next_batch(self):
        facts = [
            _fact(1, need_id=1, content="a" * 80),
            _fact(2, need_id=2, content="b" * 80),
            _fact(3, need_id=3, content="c" * 80),
        ]

        groups = _analysis_groups(facts, token_budget=35)
        batches = list(groups.values())

        self.assertGreaterEqual(len(batches), 2)
        self.assertEqual(
            [item["id"] for batch in batches for item in batch],
            [1, 2, 3],
        )
        self.assertTrue(all(batch for batch in batches))

    def test_splits_a_semantic_group_only_when_the_group_is_too_large(self):
        facts = [
            _fact(1, need_id=1, content="a" * 400),
            _fact(2, need_id=1, content="b" * 400),
            _fact(3, need_id=2, content="c"),
        ]

        groups = _analysis_groups(facts, token_budget=35)
        batches = list(groups.values())

        self.assertGreaterEqual(len(batches), 3)
        self.assertEqual(
            [item["id"] for batch in batches for item in batch],
            [1, 2, 3],
        )

    def test_keeps_dimensions_as_separate_analysis_scopes(self):
        facts = [
            _fact(1, dimension="A", need_id=1),
            _fact(2, dimension="B", need_id=2),
        ]

        groups = _analysis_groups(facts, token_budget=10_000)

        self.assertEqual(len(groups), 2)
        self.assertEqual(
            [[item["id"] for item in batch] for batch in groups.values()],
            [[1], [2]],
        )


if __name__ == "__main__":
    unittest.main()
