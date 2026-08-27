import unittest

from app.control_agent import (
    AgentToolName,
    ArtifactFocus,
    TaskAgentContext,
    parse_agent_decision,
    resolve_common_read,
    resolve_control_command,
)
from app.interaction import _focused_proposal_target, _pending_proposal_decision
from app.quality import attach_quality_issue_locations


class ControlAgentTests(unittest.TestCase):
    def context(self) -> TaskAgentContext:
        return TaskAgentContext(
            task_id="task-a", report_id=7, theme="测试任务", stage="writing",
            stage_label="报告生成", progress={"writing": {"done": 2, "total": 4}},
            artifact_counts={"facts": 20, "inferences": 5},
            current_focus=ArtifactFocus(),
        )

    def test_common_progress_query_uses_deterministic_tool(self):
        reply, result = resolve_common_read("当前任务进度怎么样？", self.context())
        self.assertIn("报告生成", reply)
        self.assertEqual(result["stage"], "writing")

    def test_chapter_rewrite_routes_to_writer_command(self):
        command = resolve_control_command("请重新生成第三章，并强化证据说明", self.context())
        self.assertEqual(command.tool_name, AgentToolName.REGENERATE_CHAPTER)
        self.assertTrue(command.confirmation_required)
        self.assertIn("第三章", command.arguments["chapter_title"])
        self.assertIsNone(resolve_control_command("请重试", self.context()))

    def test_mutating_model_tool_is_forced_to_require_confirmation(self):
        decision = parse_agent_decision({
            "intent": "execute", "reply": "准备暂停",
            "tool_call": {"tool_name": "pause_task", "arguments": {}, "confirmation_required": False},
        })
        self.assertTrue(decision.tool_call.confirmation_required)

    def test_final_plan_tool_contract_exposes_optional_structure(self):
        from app.control_agent import tool_manifest
        item = next(value for value in tool_manifest() if value["name"] == "rerun_final_plan")
        self.assertIn("new_structure", item["arguments"])

    def test_task_conversation_routes_proposal_to_focused_artifact(self):
        thread = {
            "artifact_type": "task_control", "artifact_version": "", "object_id": "",
            "scope": {"focus": {
                "artifact_type": "final_plan", "artifact_version": "2", "object_id": "plan-1",
                "current": {"structure": ["第一章", "第二章"]},
            }},
        }
        target, current = _focused_proposal_target(thread, {"task_id": "task-a"})
        self.assertEqual(target["artifact_type"], "final_plan")
        self.assertEqual(current["structure"], ["第一章", "第二章"])

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

    def test_short_confirmation_binds_pending_proposal(self):
        pending = {"id": 8, "status": "proposed"}
        result = _pending_proposal_decision({"proposals": [pending]}, "认可")
        self.assertEqual(result, (pending, "accepted"))

    def test_quality_issue_gets_sentence_anchor(self):
        rows = [{
            "id": 31, "section": "第二章", "paragraph": 2,
            "content": "该机制目前仍存在覆盖不足的问题。", "user_edit": None,
        }]
        issues = attach_quality_issue_locations(1, [{
            "type": "LOGIC_GAP", "section": "第二章", "quote": "仍存在覆盖不足", "note": "论证不足",
        }], rows=rows)
        self.assertEqual(issues[0]["sentence_id"], 31)
        self.assertEqual(issues[0]["target_type"], "sentence")


if __name__ == "__main__":
    unittest.main()
