import unittest
from unittest.mock import MagicMock

from pydantic_ai.models.test import TestModel

from app.task_draft_agent import TaskDraftAgentDeps, _build_agent


class TaskDraftAgentTests(unittest.TestCase):
    def test_draft_tool_requires_complete_business_requirements(self):
        deps = TaskDraftAgentDeps(current={}, user_message="你来帮我想")
        with self.assertRaises(Exception):
            deps.prepare("低空经济发展总结", "简单写一下")
        proposal = deps.prepare(
            "低空经济发展总结",
            "面向内部决策人员形成行业总结，重点说明政策、产业、技术和风险，所有事实必须来自材料并保留来源，约三千字，采用专业客观的中文报告风格。",
        )
        self.assertTrue(proposal["prepared"])
        self.assertIn("事实必须来自材料", deps.proposed["requirements"])

    def test_agent_uses_typed_draft_tool_when_it_decides_to_prepare(self):
        deps = TaskDraftAgentDeps(
            current={"theme": "低空经济发展总结", "requirements": ""},
            user_message="你来帮我想，一般的报告要求怎么写",
        )
        deps.prepare = MagicMock(return_value={"prepared": True})
        agent = _build_agent()
        with agent.override(model=TestModel(
            call_tools=["propose_task_draft"],
            custom_output_text="已形成完整需求草案，请审阅。",
        )):
            result = agent.run_sync("请主动形成需求草案", deps=deps)
        self.assertIn("需求草案", result.output)
        deps.prepare.assert_called_once()


if __name__ == "__main__":
    unittest.main()
