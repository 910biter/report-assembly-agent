import unittest
from unittest.mock import patch

from app.document_shape import (
    normalize_document_shape,
    visible_sections,
    visible_subheadings,
    wants_numbering,
)
from app.rendering.headings import detect_numbering_strategy, format_heading
from app.memory.style import _aggregate_asset_roles, _combined_topic_type, _eligible_layout_members
from app.memory.style_profile import build_report_exemplars, editorial_contract
from app.models.memory import StyleVariant
from app.task_draft_agent import _selected_template_profile


class DocumentShapeTests(unittest.TestCase):
    def test_continuous_article_hides_internal_semantic_units(self):
        shape = normalize_document_shape({"kind": "continuous_article"})
        self.assertFalse(visible_sections(shape))
        self.assertFalse(visible_subheadings(shape))
        self.assertFalse(wants_numbering(shape, 1))

    def test_article_sections_default_to_plain_headings(self):
        shape = normalize_document_shape({"kind": "article_sections"})
        self.assertTrue(visible_sections(shape))
        self.assertFalse(wants_numbering(shape, 1))
        self.assertEqual(
            format_heading(1, [1], "研究进展", detect_numbering_strategy({})),
            "研究进展",
        )

    def test_structured_report_keeps_legacy_safe_numbering_only_when_requested(self):
        shape = normalize_document_shape({"kind": "structured_report"})
        strategy = detect_numbering_strategy({}, fallback_defaults=wants_numbering(shape, 1))
        self.assertEqual(format_heading(1, [1], "总体情况", strategy), "一、总体情况")

    def test_non_word_reference_never_becomes_word_layout_master(self):
        roles = _aggregate_asset_roles(
            [{"filename": "推送.pdf", "path": "/tmp/push.pdf", "text": "正文", "headings": []}],
            {}, {},
        )
        self.assertTrue(roles["editorial_reference"])
        self.assertFalse(roles["layout_master_available"])

    def test_docx_source_is_eligible_for_layout_reuse(self):
        roles = _aggregate_asset_roles(
            [{"filename": "格式.docx", "path": "/tmp/layout.docx", "text": "正文", "headings": []}],
            {}, {"dominant": {}},
        )
        self.assertTrue(roles["layout_master_available"])

    def test_explicit_editorial_docx_is_not_an_export_base(self):
        members = [{"path": "/tmp/style.docx", "asset_role": "editorial"}]
        self.assertEqual(_eligible_layout_members(members), [])

    def test_incidental_pdf_headings_do_not_grant_structure_authority(self):
        roles = _aggregate_asset_roles(
            [{"filename": "推送.pdf", "path": "/tmp/push.pdf", "text": "正文", "headings": ["导语"], "asset_role": "editorial"}],
            {}, {},
        )
        self.assertFalse(roles["structural_reference"])

    def test_editorial_contract_is_not_a_directory(self):
        contract = editorial_contract([{"content": "采购程序失控。随后，多项证据显示流程存在明显缺口。", "sample_type": "analysis"}])
        self.assertIn(contract["paragraph_rhythm"], {"compact", "extended"})
        self.assertNotIn("sections", contract)

    def test_uploaded_reference_set_stays_one_template_even_when_source_genres_differ(self):
        self.assertEqual(
            _combined_topic_type([
                {"topic_type": "工作总结"},
                {"topic_type": "工作总结"},
                {"topic_type": "新闻稿"},
            ]),
            "工作总结",
        )

    def test_draft_assistant_reads_selected_template_through_tool_profile(self):
        variant = StyleVariant(
            library_id=1,
            id=9,
            name="推送参考",
            structure={"document_shape": {"kind": "message_push"}},
            writing_style={"tone": "简洁"},
            writing_patterns={
                "paragraph_architecture": "短段落",
                "material_realization": {"fact_expression": "先给出具体事实"},
            },
        )
        with patch("app.memory.style.get_variant", return_value=variant):
            result = _selected_template_profile({"template": {"id": 9}})
        self.assertTrue(result["selected"])
        self.assertEqual(result["name"], "推送参考")
        self.assertEqual(
            result["document_shape"]["kind"],
            "message_push",
        )


if __name__ == "__main__":
    unittest.main()
