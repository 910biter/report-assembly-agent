import unittest
from unittest.mock import patch

from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from app.control_agent import AgentToolCall, ArtifactFocus, TaskAgentContext
from app.task_collaboration_agent import (
    TaskAgentDeps,
    _clean_titles,
    _build_agent,
    _resolve_chapter_index,
)


class TaskCollaborationAgentTests(unittest.TestCase):
    def context(self) -> TaskAgentContext:
        return TaskAgentContext(
            task_id="task-a",
            theme="测试报告",
            user_requirements="形成五章中文报告",
            current_focus=ArtifactFocus(),
        )

    def test_chapter_reference_supports_number_and_unique_title(self):
        titles = ["政策环境", "技术路径", "结论与展望"]
        self.assertEqual(_resolve_chapter_index(titles, "第三章"), 2)
        self.assertEqual(_resolve_chapter_index(titles, "技术"), 1)

    def test_ambiguous_chapter_reference_is_rejected(self):
        with self.assertRaises(ModelRetry):
            _resolve_chapter_index(["技术路径", "技术风险"], "技术")

    def test_heading_numbers_are_removed_without_changing_titles(self):
        self.assertEqual(
            _clean_titles(["一、政策环境", "（二）技术路径", "政策环境"]),
            ["政策环境", "技术路径"],
        )

    def test_only_one_mutating_action_can_be_prepared_per_turn(self):
        deps = TaskAgentDeps(context=self.context(), user_message="请修改")
        first = AgentToolCall.model_validate({
            "tool_name": "retry_task", "reason": "重试任务",
        })
        second = AgentToolCall.model_validate({
            "tool_name": "pause_task", "reason": "暂停任务",
        })
        with patch("app.task_collaboration_agent.validate_agent_tool_call", side_effect=lambda call, *_: call):
            deps.propose(first)
            with self.assertRaises(ModelRetry):
                deps.propose(second)

    def test_agent_can_read_authoritative_artifact_before_answering(self):
        deps = TaskAgentDeps(context=self.context(), user_message="当前目录是什么？")
        agent = _build_agent()
        with (
            patch("app.task_collaboration_agent.execute_read_tool", return_value={
                "exists": True, "titles": ["政策环境", "技术路径", "实施建议"],
            }),
            agent.override(model=TestModel(
                call_tools=["read_final_report_plan"],
                custom_output_text="当前目录共三章。",
            )),
        ):
            result = agent.run_sync("请读取并说明当前目录", deps=deps)
        self.assertEqual(result.output, "当前目录共三章。")
        self.assertEqual(deps.tool_trace, ["get_final_plan"])


if __name__ == "__main__":
    unittest.main()
