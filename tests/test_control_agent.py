import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.control_agent import (
    AgentToolName,
    ArtifactFocus,
    TaskAgentContext,
    _load_material_role,
    _load_materials,
    _load_report_paragraph,
    execute_read_tool,
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
            "arguments": {"new_structure": ["政策环境", "技术路径", "实施建议"], "required_chapter_count": 5},
        })
        with patch("app.control_agent._load_plan", return_value={"titles": []}):
            with self.assertRaisesRegex(ValueError, "用户要求 5 章"):
                validate_agent_tool_call(call, self.context(), "请调整为任意数量")

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

    def test_batch_section_titles_are_canonical_and_atomic(self):
        from app.control_agent import AgentToolCall
        titles = ["一、 政策规划：副标题", "二、 技术基础：副标题", "三、 风险边界：副标题"]
        call = AgentToolCall.model_validate({
            "tool_name": "update_section_titles",
            "arguments": {"changes": [
                {"old_title": "政策规划", "new_title": "政策规划"},
                {"old_title": "技术基础", "new_title": "技术基础"},
                {"old_title": "风险边界", "new_title": "风险边界"},
            ]},
        })
        with patch("app.control_agent._load_plan", return_value={"titles": titles}):
            normalized = validate_agent_tool_call(call, self.context(), "五个标题都去掉副标题")
        self.assertEqual(normalized.arguments["changes"], [
            {"old_title": titles[0], "new_title": "一、 政策规划"},
            {"old_title": titles[1], "new_title": "二、 技术基础"},
            {"old_title": titles[2], "new_title": "三、 风险边界"},
        ])

    def test_batch_section_titles_reject_collision_with_unchanged_title(self):
        from app.control_agent import AgentToolCall
        call = AgentToolCall.model_validate({
            "tool_name": "update_section_titles",
            "arguments": {"changes": [
                {"old_title": "政策环境", "new_title": "技术路径"},
                {"old_title": "风险边界", "new_title": "风险识别"},
            ]},
        })
        with patch("app.control_agent._load_plan", return_value={"titles": ["政策环境", "技术路径", "风险边界"]}):
            with self.assertRaisesRegex(ValueError, "未修改章节重复"):
                validate_agent_tool_call(call, self.context(), "批量改标题")

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

    def test_checkpoint_tools_are_only_valid_at_the_matching_checkpoint(self):
        from app.control_agent import AgentToolCall
        call = AgentToolCall.model_validate({"tool_name": "confirm_directory"})
        with self.assertRaisesRegex(ValueError, "目录确认阶段"):
            validate_agent_tool_call(call, self.context(), "确认并继续")
        context = self.context().model_copy(update={"directory_review_pending": True})
        normalized = validate_agent_tool_call(call, context, "确认并继续")
        self.assertEqual(normalized.arguments, {"feedback": ""})

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

    def test_locate_quality_issue_fetches_the_requested_page(self):
        expected = {"issues": [{"issue_id": "qa_17", "sentence_id": 88}]}
        with (
            patch("app.control_agent.execute_read_tool", return_value=expected) as read,
            patch("app.control_agent._issue_url", return_value="/reports/7?sentence=88"),
        ):
            result = execute_read_tool(
                AgentToolName.LOCATE_QUALITY_ISSUE,
                {"issue_index": 17}, self.context(),
            )
        read.assert_called_once_with(
            AgentToolName.GET_QUALITY_ISSUES, {"offset": 17, "limit": 1}, self.context(),
        )
        self.assertEqual(result["issue"]["issue_id"], "qa_17")

    def test_report_paragraph_uses_position_order(self):
        from sqlalchemy import create_engine
        from app.infrastructure.orm import ORMSentence

        engine = create_engine("sqlite:///:memory:")
        ORMSentence.create(engine)
        with engine.begin() as connection:
            connection.execute(ORMSentence.insert(), [
                {
                    "id": 2, "report_id": 7, "section": "第一章", "paragraph": 1,
                    "position": 2, "content": "第二句", "source_level": "FACT",
                },
                {
                    "id": 1, "report_id": 7, "section": "第一章", "paragraph": 1,
                    "position": 1, "content": "第一句", "source_level": "FACT",
                },
            ])

        from sqlalchemy.orm import Session

        @contextmanager
        def session_scope():
            with Session(engine) as session:
                yield session

        with patch("app.control_agent.session_scope", session_scope):
            result = _load_report_paragraph(7, "第一章", 1)

        self.assertEqual([item["id"] for item in result["sentences"]], [1, 2])

    def test_missing_material_insight_is_not_reused_from_another_task(self):
        old_insight = {
            "id": 9, "material_id": 11, "task_id": "other-task",
            "doc_type": "旧任务判断", "material_role": "不应泄漏",
        }

        class Result:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return self

            def first(self):
                return self.rows[0] if self.rows else None

            def all(self):
                return list(self.rows)

        class Session:
            def __init__(self):
                self.calls = []

            def execute(self, statement):
                self.calls.append(str(statement))
                if len(self.calls) == 1:
                    return Result([])
                return Result([old_insight])

        session = Session()

        @contextmanager
        def session_scope():
            yield session

        with patch("app.control_agent.session_scope", session_scope):
            result = _load_material_role("current-task", 11)

        self.assertFalse(result["exists"])
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["material_role"], "")
        self.assertEqual(len(session.calls), 1)

    def test_material_list_marks_missing_insight_and_supports_paging(self):
        from app.infrastructure.orm import ORMMaterial

        material = {
            "id": 11, "filename": "材料.pdf", "file_type": "pdf",
        }

        class Result:
            def __init__(self, rows):
                self.rows = rows

            def mappings(self):
                return self

            def all(self):
                return list(self.rows)

            def scalar_one(self):
                return len(self.rows)

        class Session:
            def execute(self, statement):
                table_name = str(statement.get_final_froms()[0].name)
                if table_name == ORMMaterial.name:
                    return Result([material])
                return Result([])

        @contextmanager
        def session_scope():
            yield Session()

        with (
            patch("app.control_agent.short_term.load_task", return_value={"material_ids": [11]}),
            patch("app.control_agent.session_scope", session_scope),
        ):
            result = _load_materials("current-task", offset=0, limit=1)

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["returned_count"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["materials"][0]["insight_status"], "missing")
        self.assertTrue(result["materials"][0]["insight_missing"])


if __name__ == "__main__":
    unittest.main()
