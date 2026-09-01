import unittest

from app.infrastructure.orm import Base
from app.report_versions import (
    _align_paragraphs,
    _inline_text_diff,
    _object_delta,
    _review_scopes_overlap,
    _sequence_diff,
    _version_label,
)


class VersionArchitectureTests(unittest.TestCase):
    def test_paragraph_insert_does_not_shift_existing_paragraphs(self):
        old = {
            1: [{"id": 1, "text": "第一段原文", "lineage_id": "a"}],
            2: [{"id": 2, "text": "第二段原文", "lineage_id": "b"}],
        }
        new = {
            1: [{"id": 3, "text": "新增段落", "lineage_id": "c"}],
            2: [{"id": 4, "text": "第一段修改", "lineage_id": "a"}],
            3: [{"id": 5, "text": "第二段原文", "lineage_id": "b"}],
        }
        self.assertEqual(_align_paragraphs(old, new), [(None, 1), (1, 2), (2, 3)])

    def test_sentence_lineage_wins_over_text_position(self):
        diff = _sequence_diff(
            [{"id": 1, "text": "旧表达", "lineage_id": "stable"}],
            [{"id": 2, "text": "新的完整表达", "lineage_id": "stable"}],
        )
        self.assertEqual(diff[0]["change_type"], "modified")
        self.assertEqual(diff[0]["change_key"], "lineage:stable")

    def test_artifact_delta_uses_database_identity(self):
        delta = _object_delta(
            [{"id": 7, "content": "旧事实"}],
            [{"id": 7, "content": "修订后的事实"}],
        )
        self.assertEqual(len(delta["modified"]), 1)
        self.assertFalse(delta["added"])
        self.assertFalse(delta["deprecated"])

    def test_minor_version_label_does_not_use_float(self):
        self.assertEqual(_version_label(2, 10), "2.10")

    def test_critical_schema_constraints_are_present(self):
        versions = Base.metadata.tables["report_versions"]
        self.assertFalse(versions.c.report_id.nullable)
        self.assertTrue(versions.c.report_id.foreign_keys)
        self.assertIn("version_major", versions.c)
        self.assertIn("version_minor", versions.c)
        self.assertIn("task_runs", Base.metadata.tables)

    def test_inline_diff_marks_only_changed_chinese_span(self):
        result = _inline_text_diff("应于九月提交材料。", "应于九月十日前提交材料。")

        self.assertEqual("十日前", "".join(
            span["text"] for span in result["new"] if span["type"] == "added"
        ))
        self.assertTrue(any(span["type"] == "unchanged" for span in result["old"]))

    def test_review_scope_switch_clears_parent_or_children_only_in_same_paragraph(self):
        section = {"level": "section", "section": "第一章"}
        paragraph = {"level": "paragraph", "section": "第一章", "old_paragraph": 2, "new_paragraph": 3}
        same_sentence = {"level": "sentence", "section": "第一章", "paragraph": 3, "current_sentence_id": 9}
        other_sentence = {"level": "sentence", "section": "第一章", "paragraph": 4, "current_sentence_id": 10}

        self.assertTrue(_review_scopes_overlap(section, other_sentence))
        self.assertTrue(_review_scopes_overlap(paragraph, same_sentence))
        self.assertFalse(_review_scopes_overlap(paragraph, other_sentence))


if __name__ == "__main__":
    unittest.main()
