import unittest

from app.analysis.analyzer import _confidence_from_facts


class AnalysisConfidenceTests(unittest.TestCase):
    def test_no_fact_is_low(self):
        self.assertEqual(_confidence_from_facts([])[0], "low")

    def test_one_fact_is_medium(self):
        self.assertEqual(_confidence_from_facts([7])[0], "medium")

    def test_two_or_more_facts_are_high(self):
        self.assertEqual(_confidence_from_facts([7, 8])[0], "high")
        self.assertEqual(_confidence_from_facts([7, 8, 9])[0], "high")

    def test_duplicate_fact_ids_do_not_inflate_confidence(self):
        self.assertEqual(_confidence_from_facts([7, 7])[0], "medium")


if __name__ == "__main__":
    unittest.main()
