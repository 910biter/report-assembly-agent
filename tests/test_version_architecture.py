import unittest

from app.infrastructure.orm import Base
from app.report_versions import (
    _align_paragraphs,
    _build_sentence_diff,
    _content_snapshot_hash,
    _inline_text_diff,
    _object_delta,
    _pair_sections,
    _paired_structure_changes,
    _review_scopes_overlap,
    _sequence_diff,
    _version_label,
    _restore_plan_values,
)


class VersionArchitectureTests(unittest.TestCase):
    def test_snapshot_hash_ignores_storage_and_runtime_identity(self):
        base = {
            "title": "报告",
            "sentence_snapshot": [{
                "id": 1,
                "lineage_id": "old-lineage",
                "origin_call_id": "call-1",
                "section": "第一章",
                "paragraph": 1,
                "position": 1,
                "content": "  同一份正文。 ",
                "source_level": "MATERIAL_FACT",
                "source_refs": {"fact_ids": [10], "inference_ids": []},
            }],
            "fact_snapshot": [{
                "id": 10,
                "stable_key": "fact-a",
                "content": "同一事实",
                "dimension": "背景",
                "origin_call_id": "call-1",
            }],
            "inference_snapshot": [],
            "conflict_snapshot": [],
            "report_plan_snapshot": {"id": 4, "title": "报告", "finalized_at": "now"},
            "narrative_plan_snapshot": {},
            "scale_plan_snapshot": {},
        }
        rebuilt = {
            **base,
            "sentence_snapshot": [{
                **base["sentence_snapshot"][0],
                "id": 99,
                "lineage_id": "new-lineage",
                "origin_call_id": "call-2",
                "source_refs": {"fact_ids": [1010], "inference_ids": []},
            }],
            "fact_snapshot": [{
                **base["fact_snapshot"][0],
                "id": 1010,
                "origin_call_id": "call-2",
            }],
            "report_plan_snapshot": {"id": 44, "title": "报告", "finalized_at": "later"},
        }

        self.assertEqual(_content_snapshot_hash(base), _content_snapshot_hash(rebuilt))

    def test_snapshot_hash_changes_when_report_content_changes(self):
        base = {
            "title": "报告",
            "sentence_snapshot": [{"section": "第一章", "paragraph": 1, "position": 1, "content": "原文"}],
            "fact_snapshot": [], "inference_snapshot": [], "conflict_snapshot": [],
            "report_plan_snapshot": {}, "narrative_plan_snapshot": {}, "scale_plan_snapshot": {},
        }
        changed = {**base, "sentence_snapshot": [{**base["sentence_snapshot"][0], "content": "修改后的正文"}]}

        self.assertNotEqual(_content_snapshot_hash(base), _content_snapshot_hash(changed))

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

    def test_restore_plan_values_include_final_plan_snapshot(self):
        values = _restore_plan_values(
            {
                "title": "旧计划",
                "structure": ["旧章节"],
                "chapter_plans": [{"title": "旧章节"}],
                "final_plan_json": {"composition_mode": "chaptered"},
                "budget": {"target_words": 3000},
            },
            {"target_words": 1000},
            "旧标题",
        )

        self.assertEqual(values["final_plan_json"], '{"composition_mode": "chaptered"}')

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

    def test_section_title_change_is_paired_as_rename(self):
        old = {
            "Old chapter": {1: [{"id": 1, "text": "The same evidence based discussion.", "lineage_id": "a"}]},
        }
        new = {
            "New chapter": {1: [{"id": 2, "text": "The same evidence based discussion.", "lineage_id": "a"}]},
        }

        pairs = _pair_sections(old, new)

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["old_title"], "Old chapter")
        self.assertEqual(pairs[0]["new_title"], "New chapter")
        self.assertEqual(pairs[0]["change_type"], "renamed")

    def test_paired_structure_changes_keep_rename_as_one_change(self):
        changes = _paired_structure_changes([
            {
                "old_title": "\u65e7\u7ae0\u8282",
                "new_title": "\u65b0\u7ae0\u8282",
                "change_type": "renamed",
                "old_index": 0,
                "new_index": 0,
            },
        ])

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["type"], "renamed_section")
        self.assertEqual(changes[0]["from"], "\u65e7\u7ae0\u8282")
        self.assertEqual(changes[0]["to"], "\u65b0\u7ae0\u8282")

    def test_same_sentence_with_new_lineage_is_provenance_change(self):
        diff = _sequence_diff(
            [{"id": 1, "text": "Stable text.", "lineage_id": "old", "source_refs": {"fact_ids": [1]}}],
            [{"id": 2, "text": "Stable text.", "lineage_id": "new", "source_refs": {"fact_ids": [2]}}],
        )

        self.assertEqual(diff[0]["change_type"], "provenance_changed")

    def test_unified_diff_keeps_renamed_section_and_paragraph_visible(self):
        base = {
            "report_id": 9,
            "title": "Report",
            "sentence_snapshot": [
                {"id": 1, "section": "Old chapter", "paragraph": 1, "position": 1,
                 "content": "The same evidence based discussion.", "lineage_id": "a"},
            ],
            "metadata": {"snapshot_hash": "old"},
        }
        target = {
            "report_id": 9,
            "title": "Report",
            "sentence_snapshot": [
                {"id": 2, "section": "New chapter", "paragraph": 1, "position": 1,
                 "content": "The same evidence based discussion.", "lineage_id": "a"},
            ],
            "metadata": {"snapshot_hash": "new"},
        }

        result = _build_sentence_diff(
            base, target, base_version_id=1, target_version_id=2,
            comparison_mode="version", can_apply=False,
        )

        self.assertEqual(result["summary"]["sections_changed"], 1)
        self.assertEqual(result["sections"][0]["change_type"], "renamed")
        self.assertEqual(result["sections"][0]["old_title"], "Old chapter")
        self.assertEqual(result["sections"][0]["new_title"], "New chapter")
        self.assertEqual(len(result["sections"][0]["paragraphs"]), 1)

if __name__ == "__main__":
    unittest.main()
