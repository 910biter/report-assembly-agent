import unittest
from unittest.mock import patch

from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from app.control_agent import AgentToolCall, ArtifactFocus, TaskAgentContext
from app.interaction import _focus_locator
from app.task_collaboration_agent import (
    TaskAgentDeps,
    _clean_titles,
    _build_agent,
    _missing_read_groups,
    _required_read_groups,
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

    def test_agent_can_start_from_task_map(self):
        deps = TaskAgentDeps(context=self.context(), user_message="这些材料主要讲什么？")
        agent = _build_agent()
        with (
            patch("app.task_collaboration_agent.execute_read_tool", return_value={
                "materials": [{"filename": "政策文件.pdf", "topic": "低空经济政策目标"}],
                "counts": {"facts": 0, "inferences": 0},
            }),
            agent.override(model=TestModel(
                call_tools=["read_task_map"],
                custom_output_text="材料围绕低空经济政策目标展开。",
            )),
        ):
            result = agent.run_sync("请读取任务全景", deps=deps)
        self.assertEqual(result.output, "材料围绕低空经济政策目标展开。")
        self.assertEqual(deps.tool_trace, ["get_task_map"])

    def test_template_question_requires_authoritative_template_read(self):
        required = _required_read_groups("这个模板的文风和版式是什么？", self.context())
        self.assertEqual(_missing_read_groups(required, []), ["模板画像"])
        self.assertEqual(_missing_read_groups(required, ["get_task_template"]), [])

    def test_focused_sentence_requires_a_task_scoped_read(self):
        context = self.context().model_copy(update={
            "current_focus": ArtifactFocus(artifact_type="sentence", object_id="101", title="正文句子"),
        })
        required = _required_read_groups("这句话有依据吗？", context)
        self.assertIn("当前引用对象", required)
        self.assertEqual(_missing_read_groups(required, ["get_focused_artifact"]), [])

    def test_focus_locator_discards_browser_content_and_reference_text(self):
        locator = _focus_locator(ArtifactFocus.model_validate({
            "artifact_type": "sentence",
            "object_id": "101",
            "current": {"content": "浏览器正文", "quote": "浏览器摘录"},
            "references": [{
                "artifact_type": "fact", "object_id": "17",
                "current": {"content": "不应进入会话存储"},
            }],
        }))
        self.assertEqual(locator["object_id"], "101")
        self.assertEqual(locator["current"], {})
        self.assertEqual(locator["references"], [{
            "artifact_type": "fact", "object_id": "17",
            "artifact_version": "", "title": "当前任务",
        }])



if __name__ == "__main__":
    unittest.main()
