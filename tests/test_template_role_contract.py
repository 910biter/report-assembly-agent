import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.memory import style
from app.memory.style_jobs import create_job, get_job, run_job
from app.models.memory import StyleVariant
from app.policy import _template_policy


class TemplateRoleContractTests(unittest.TestCase):
    def test_upload_roles_are_per_file_and_layout_is_unique_docx(self):
        self.assertEqual(
            style.validate_asset_role_assignments(
                ["a.pdf", "b.docx"],
                json.dumps([["editorial"], ["structure", "layout"]]),
            ),
            [["editorial"], ["structure", "layout"]],
        )
        with self.assertRaisesRegex(ValueError, "ONLY_ONE_LAYOUT_MASTER_ALLOWED"):
            style.validate_asset_role_assignments(
                ["a.docx", "b.docx"], json.dumps([["layout"], ["layout"]]),
            )
        with self.assertRaisesRegex(ValueError, "LAYOUT_MASTER_MUST_BE_DOCX"):
            style.validate_asset_role_assignments(["a.pdf"], json.dumps([["layout"]]))
        with self.assertRaisesRegex(ValueError, "ASSET_ROLE_COUNT_MISMATCH"):
            style.validate_asset_role_assignments(["a.pdf"], "[]")

    def test_legacy_auto_never_grants_structure_or_layout(self):
        member = {
            "filename": "reference.docx", "path": "/tmp/reference.docx",
            "asset_role": "auto", "text": "可用于学习文体的正文样例。",
        }
        self.assertEqual(style.normalize_asset_roles("auto"), ["editorial"])
        self.assertEqual(style._eligible_layout_members([member]), [])
        roles = style._aggregate_asset_roles([member], {}, {})
        self.assertTrue(roles["editorial_reference"])
        self.assertFalse(roles["structural_reference"])
        self.assertFalse(roles["layout_master_available"])

    def test_layout_extraction_uses_only_the_explicit_master(self):
        selected = {"filename": "selected.docx", "path": "/tmp/selected.docx"}
        with (
            patch("app.memory.style._persist_template_source", return_value=selected["path"]),
            patch("app.memory.style.extract_docx_format", return_value={
                "source_template_path": selected["path"],
                "template_schema": {"document": {"page": "A4"}},
            }) as extract,
        ):
            result = style._collect_format([selected])
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(result["selection_strategy"]["source"], "selected.docx")
        self.assertEqual(result["alternatives"], [])
        with self.assertRaisesRegex(ValueError, "ONLY_ONE_LAYOUT_MASTER_ALLOWED"):
            style._collect_format([selected, {"filename": "other.docx", "path": "/tmp/other.docx"}])

    def test_profile_prompt_keeps_editorial_and_structure_inputs_separate(self):
        members = [
            {
                "filename": "style.txt", "asset_roles": ["editorial"],
                "samples": {"opening": "EDITORIAL_SAMPLE_SENTINEL", "middle": "", "ending": ""},
                "headings": [{"text": "不应把此标题当目录", "level": 1}],
            },
            {
                "filename": "outline.pdf", "asset_roles": ["structure"],
                "samples": {"opening": "STRUCTURE_BODY_SENTINEL", "middle": "", "ending": ""},
                "headings": [{"text": "明确的结构标题", "level": 1}],
            },
        ]
        response = {
            "name": "综合参考",
            "structure": {"sections": [{"title": "明确的结构标题"}], "document_shape": {"kind": "article_sections"}},
            "writing_style": {"tone": "正式"},
            "writing_patterns": {
                "fact_expression": "先给事实",
                "judgment_expression": "审慎给出判断",
                "fact_judgment_transition": "事实后解释影响",
            },
            "sample_annotations": [],
        }
        captured = {}

        def generate(_agent, prompt, **_kwargs):
            captured["prompt"] = prompt
            return response

        with (
            patch("app.memory.style.build_report_exemplars", return_value=[{
                "exemplar_id": "e1", "source_report": "style.txt", "content": "EDITORIAL_EXEMPLAR_SENTINEL",
                "sample_type": "analysis", "structural_role": "body",
            }]),
            patch("app.memory.style._StyleProfileAgent.generate_json", new=generate),
            patch("app.memory.style.save_variant", side_effect=lambda variant, **_kwargs: variant),
        ):
            variant = style._build_variant(1, "综合参考", members)

        self.assertIn("EDITORIAL_SAMPLE_SENTINEL", captured["prompt"])
        self.assertIn("EDITORIAL_EXEMPLAR_SENTINEL", captured["prompt"])
        self.assertIn("明确的结构标题", captured["prompt"])
        self.assertNotIn("STRUCTURE_BODY_SENTINEL", captured["prompt"])
        self.assertEqual(variant.structure_policy["mode"], "SOFT_STRUCTURE")
        self.assertEqual(variant.profile_confidence, {})
        self.assertEqual(variant.structure["learning_evidence"]["profile_prompt"]["truncated"], False)

    def test_structure_policy_has_one_authoritative_resolution(self):
        variant = StyleVariant(
            library_id=1,
            structure={
                "sections": [{"title": "参考目录"}],
                "asset_roles": {"structural_reference": True},
            },
            structure_policy={"mode": "FORMAT_ONLY", "confirmed": False},
        )
        self.assertEqual(_template_policy(variant)["structure_type"], "SOFT_STRUCTURE")
        self.assertIn("SOFT_STRUCTURE", variant.planner_prompt_block())

        variant.structure_policy = {"mode": "HARD_STRUCTURE", "source": "model", "confirmed": True}
        self.assertEqual(_template_policy(variant)["structure_type"], "SOFT_STRUCTURE")
        variant.structure_policy = {"mode": "HARD_STRUCTURE", "source": "user", "confirmed": True}
        self.assertEqual(_template_policy(variant)["structure_type"], "HARD_STRUCTURE")
        self.assertIn("必须遵守", variant.planner_prompt_block())

    def test_model_learned_rules_are_not_promoted_to_business_constraints(self):
        variant = StyleVariant(library_id=1, institution_rules={
            "must_include": ["样例里反复出现的某业务事项"],
        })
        self.assertEqual(_template_policy(variant)["institution_rules"], {})
        variant.institution_rules["confirmed"] = True
        self.assertEqual(
            _template_policy(variant)["institution_rules"]["must_include"],
            ["样例里反复出现的某业务事项"],
        )

    def test_layout_only_job_skips_full_text_parser(self):
        job_id = uuid.uuid4().hex[:16]
        captured = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / job_id
            job_dir.mkdir()
            template_path = job_dir / "layout.docx"
            template_path.write_bytes(b"docx-placeholder")
            with (
                patch("app.config.settings.multiuser_enabled", False),
                patch("app.memory.style_jobs.parse_file", side_effect=AssertionError("must not parse text")),
                patch("app.memory.style.analyze_library", side_effect=lambda reports, **_kwargs: captured.update(reports=reports) or []),
            ):
                create_job([{
                    "filename": "layout.docx",
                    "path": str(template_path),
                    "asset_roles": ["layout"],
                }], job_id=job_id)
                run_job(job_id)

            self.assertEqual(get_job(job_id)["status"], "completed")
            self.assertEqual(captured["reports"][0]["text"], "")
            self.assertEqual(captured["reports"][0]["asset_roles"], ["layout"])


if __name__ == "__main__":
    unittest.main()
