import unittest
from unittest.mock import patch

from app.control_agent import (
    AgentToolName,
    ArtifactFocus,
    TaskAgentContext,
    validate_agent_tool_call,
)
from app.interaction import _pending_proposal_decision
from app.quality import attach_quality_issue_locations


class ControlAgentTests(unittest.TestCase):
    def context(self) -> TaskAgentContext:
        return TaskAgentContext(
            task_id="task-a", report_id=7, theme="测试任务", stage="writing",
            stage_label="报告生成", progress={"writing": {"done": 2, "total": 4}},
            artifact_counts={"facts": 20, "inferences": 5},
            current_focus=ArtifactFocus(),
        )

    def test_final_plan_tool_contract_exposes_optional_structure(self):
        from app.control_agent import tool_manifest
        item = next(value for value in tool_manifest() if value["name"] == "rerun_final_plan")
        self.assertIn("new_structure", item["arguments"])

    def test_explicit_chapter_count_must_match_structure(self):
        from app.control_agent import AgentToolCall
        call = AgentToolCall.model_validate({
            "tool_name": "rerun_final_plan",
            "arguments": {"new_structure": ["政策环境", "技术路径", "实施建议"]},
        })
        with patch("app.control_agent._load_plan", return_value={"titles": []}):
            with self.assertRaisesRegex(ValueError, "用户要求 5 章"):
                validate_agent_tool_call(call, self.context(), "请调整为5章")

    def test_mutating_tool_discards_undeclared_arguments(self):
        from app.control_agent import AgentToolCall
        call = AgentToolCall.model_validate({
            "tool_name": "rerun_final_plan",
            "arguments": {
                "new_structure": ["第一章", "第二章"],
                "raw_json": {"must_not_reach_workflow": True},
            },
        })
        with patch("app.control_agent._load_plan", return_value={"titles": []}):
            normalized = validate_agent_tool_call(call, self.context(), "调整为两章")
        self.assertNotIn("raw_json", normalized.arguments)

    def test_requirement_revision_requires_complete_merged_requirements(self):
        from app.control_agent import AgentToolCall
        call = AgentToolCall.model_validate({
            "tool_name": "revise_task_requirements", "arguments": {"instruction": "去掉第一点"},
        })
        with self.assertRaisesRegex(ValueError, "完整报告要求"):
            validate_agent_tool_call(call, self.context(), "去掉第一点")

    def test_title_tools_are_scope_checked(self):
        from app.control_agent import AgentToolCall
        report_call = AgentToolCall.model_validate({
            "tool_name": "update_report_title", "arguments": {"new_title": "新的报告标题"},
        })
        normalized = validate_agent_tool_call(report_call, self.context(), "修改报告标题")
        self.assertEqual(normalized.arguments["new_title"], "新的报告标题")
        section_call = AgentToolCall.model_validate({
            "tool_name": "update_section_title",
            "arguments": {"old_title": "技术路径", "new_title": "关键技术路径"},
        })
        with patch("app.control_agent._load_plan", return_value={"titles": ["政策环境", "技术路径"]}):
            normalized = validate_agent_tool_call(section_call, self.context(), "修改第二章标题")
        self.assertEqual(normalized.arguments["old_title"], "技术路径")

    def test_split_keeps_unaffected_persisted_chapters_verbatim(self):
        from app.control_agent import AgentToolCall
        existing = ["政策与目标", "技术路径", "应用实践", "治理体系", "结论与展望"]
        call = AgentToolCall.model_validate({
            "tool_name": "rerun_final_plan",
            "arguments": {
                "instruction": "将第五章拆分为两个章节，前四章保持不变",
                "new_structure": ["政策部署", "产业技术", "典型场景", "基础设施", "核心制约", "突破路径"],
                "required_chapter_count": 6,
            },
        })
        with patch("app.control_agent._load_plan", return_value={"titles": existing}):
            normalized = validate_agent_tool_call(call, self.context(), "将第五章拆分为两个章节，前四章保持不变")
        self.assertEqual(normalized.arguments["new_structure"][:4], existing[:4])
        self.assertEqual(normalized.arguments["new_structure"][4:], ["核心制约", "突破路径"])

    def test_focus_keeps_multiple_user_references(self):
        focus = ArtifactFocus.model_validate({
            "artifact_type": "paragraph", "object_id": "第一章:2",
            "current": {"content": "待修改段落"},
            "references": [
                {"artifact_type": "sentence", "object_id": "31", "current": {
                    "quote": "用户明确选中的原句",
                    "source_refs": {"fact_ids": [7], "inference_ids": [3]},
                }},
            ],
        })
        self.assertEqual(focus.references[0]["current"]["source_refs"]["fact_ids"], [7])

    def test_focus_normalizes_numeric_sentence_id(self):
        focus = ArtifactFocus.model_validate({
            "artifact_type": "sentence", "object_id": 2004,
            "artifact_version": 3, "current": {"content": "待核验句子"},
        })
        self.assertEqual(focus.object_id, "2004")
        self.assertEqual(focus.artifact_version, "3")

    def test_short_confirmation_binds_pending_proposal(self):
        pending = {"id": 8, "status": "proposed"}
        result = _pending_proposal_decision({"proposals": [pending]}, "认可")
        self.assertEqual(result, (pending, "accepted"))

    def test_short_confirmation_binds_latest_pending_proposal(self):
        older = {"id": 8, "status": "proposed"}
        latest = {"id": 11, "status": "proposed"}
        result = _pending_proposal_decision({"proposals": [latest, older]}, "认可")
        self.assertEqual(result, (latest, "accepted"))

    def test_quality_issue_gets_sentence_anchor(self):
        rows = [{
            "id": 31, "section": "第二章", "paragraph": 2,
            "content": "该机制目前仍存在覆盖不足的问题。", "user_edit": None,
        }]
        issues = attach_quality_issue_locations(1, [{
            "type": "LOGIC_GAP", "section": "第二章", "sentence_id": 31,
            "quote": "仍存在覆盖不足", "note": "论证不足",
        }], rows=rows)
        self.assertEqual(issues[0]["sentence_id"], 31)
        self.assertEqual(issues[0]["target_type"], "sentence")
        self.assertEqual(issues[0]["location_confidence"], "high")
        self.assertTrue(issues[0]["issue_id"].startswith("qa_"))

    def test_quality_issue_prefers_stable_sentence_ids(self):
        rows = [{
            "id": 31, "section": "第二章", "paragraph": 2,
            "content": "正文已经调整，旧引文无法匹配。", "user_edit": None,
        }]
        issues = attach_quality_issue_locations(1, [{
            "type": "LOGIC_GAP", "sentence_ids": [31, 999],
            "target_type": "sentence", "quote": "旧引文", "note": "论证不足",
        }], rows=rows)
        self.assertEqual(issues[0]["sentence_ids"], [31])
        self.assertEqual(issues[0]["location_confidence"], "high")

    def test_quality_issue_becomes_stale_after_edit(self):
        original_rows = [{
            "id": 31, "section": "第二章", "paragraph": 2,
            "content": "原始正文。", "user_edit": None,
        }]
        issue = attach_quality_issue_locations(1, [{
            "type": "LOGIC_GAP", "sentence_ids": [31],
            "target_type": "sentence", "quote": "原始正文", "note": "论证不足",
        }], rows=original_rows)[0]
        edited_rows = [{**original_rows[0], "user_edit": "编辑后的正文。"}]
        refreshed = attach_quality_issue_locations(1, [issue], rows=edited_rows)[0]
        self.assertEqual(refreshed["status"], "stale")
        self.assertEqual(refreshed["issue_id"], issue["issue_id"])


if __name__ == "__main__":
    unittest.main()
