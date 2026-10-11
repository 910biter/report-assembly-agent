import unittest

from app.document_shape import normalize_composition_mode, normalize_document_shape
from app.template_engine.compiler import (
    _is_template_instruction,
    _split_heading_instruction_chunks,
)
from app.control_agent import validate_agent_tool_call
from app.control_agent import AgentToolCall, AgentToolName, TaskAgentContext


class SemanticBoundaryTests(unittest.TestCase):
    def test_unknown_document_shape_is_not_silently_report(self):
        shape = normalize_document_shape({"kind": "field-notebook"})
        self.assertEqual(shape["kind"], "unknown")
        self.assertEqual(shape["raw_kind"], "field-notebook")
        self.assertEqual(shape["heading_policy"], "none")
        self.assertEqual(shape["section_policy"], "optional")
        self.assertEqual(normalize_composition_mode(None, shape=shape), "article_beats")

    def test_normal_prose_containing_shuo_ming_is_not_instruction(self):
        self.assertFalse(_is_template_instruction("本研究说明了该机制的适用边界。"))

    def test_body_after_heading_is_not_automatically_instruction(self):
        chunks = _split_heading_instruction_chunks("研究背景\n本研究说明了该机制的适用边界。")
        self.assertEqual(chunks[-1]["role"], "body")

    def test_interaction_count_comes_from_structured_argument(self):
        context = TaskAgentContext(
            task_id="task-1",
            theme="主题",
            user_requirements="要求",
            directory_review_pending=True,
        )
        call = AgentToolCall(
            tool_name=AgentToolName.RERUN_FINAL_PLAN,
            arguments={"new_structure": ["甲", "乙"], "required_chapter_count": 2},
        )
        from unittest.mock import patch
        with patch("app.control_agent._load_plan", return_value={"titles": []}):
            result = validate_agent_tool_call(call, context, message="请改成六章")
        self.assertEqual(result.arguments["required_chapter_count"], 2)


if __name__ == "__main__":
    unittest.main()
