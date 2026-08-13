"""Planner Agent:一次性输出完整报告逻辑(ReportPlan + ChapterPlan[])。

一次规划直接得到:核心问题/核心判断/总体叙事逻辑 + 每章(标题/回答的问题/
核心判断/与上章关系/所需事实推断/禁止内容/下章承接)。
Writer 只执行 ChapterPlan,不再重复规划(消除逻辑漂移与每章额外调用)。
"""

import json

from app.agents.base import BaseAgent
from app.db import connect
from app.models import ReportPlan

_SYSTEM = """你是情报报告规划师。根据用户主题、材料摘要与机构风格,一次性制定完整报告逻辑。
严格输出 JSON,不要任何解释:
{
  "title": "报告标题",
  "objective": "报告目的(解决什么问题、支撑什么决策)",
  "audience": "报告受众",
  "report_type": "报告类型(与机构风格变体对应)",
  "core_question": "报告要回答的核心问题(一句话)",
  "core_judgment": "报告的核心判断/主线结论(一句话)",
  "narrative_logic": "总体叙事逻辑:章节如何递进(如:是什么→如何发展→产生什么影响→未来风险在哪)",
  "report_budget": {
    "target_words": 全文目标字数,
    "soft_max_words": 建议最大字数,
    "hard_max_words": 绝对最大字数,
    "summary_budget": 摘要字数预算
  },
  "dimensions": ["分析维度1", "分析维度2", ...],
  "required_facts": ["证据提取应优先寻找的信息类别"],
  "chapters": [
    {
      "title": "章节标题",
      "questions": ["本章要回答的问题(2-4 个)"],
      "judgment": "本章的核心判断/写作目的",
      "relation_to_prev": "与上一章的关系(承接/转折/递进)",
      "required_facts": ["本章需要的事实类别"],
      "required_inferences": ["本章需要的推断类别"],
      "exclude": ["本章明确不写的内容(防止与其他章节重复)"],
      "next_bridge": "本章结尾如何承接下一章",
      "target_words": 本章目标字数,
      "importance": "high/medium/low",
      "evidence_density": "high/medium/low"
    }
  ]
}
要求:
1. 章节之间必须形成递进关系(不是并列堆叠),每章回答明确问题
2. 章节结构优先遵循机构风格变体;同时依据材料摘要,避免固定模板遗漏重要信息
3. chapters 的 title 列表即报告结构;常规报告通常 2-6 章,但应服从用户要求、材料信息量、模板结构和报告类型,不得固定成五段式或其他单一结构
4. 规模预算(report_budget 与每章 target_words)依据:用户明确字数要求(如有)、报告类型档位
   (简要约2000-4000字/标准约5000-10000字/深度约10000-20000字)、材料信息量、章节数与重要性。
   章节长度不得平均分配:高 importance+高 evidence_density 的章节分配更多字数,
   信息不足的章节给低预算(禁止扩写凑字)。软预算:达到合理完整度即可,不要求精确凑字。
5. 可核对、可执行、可比较的信息可以规划为集中章节、表格或自然段,具体形式由模板风格、用户需求和信息复杂度决定;除非模板明确采用列表符号,否则应保持正式中文报告规范。"""


class PlannerAgent(BaseAgent):
    name = "planner"
    role = _SYSTEM

    def plan(self, context_block: str) -> ReportPlan:
        """context_block 由 Context Manager 生成(主题/要求/材料摘要/风格)。"""
        payload = self.generate_json(f"{context_block}\n\n请输出完整报告规划 JSON。")
        chapters = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
        if not chapters:
            # 兼容旧格式/模型输出缺 chapters:按结构回退为最小章节规划
            chapters = [{"title": str(t)} for t in (payload.get("structure") or [])]
        plan = ReportPlan(
            title=str(payload.get("title", "")),
            objective=str(payload.get("objective", "")),
            audience=str(payload.get("audience", "")),
            report_type=str(payload.get("report_type", "")),
            core_question=str(payload.get("core_question", "")),
            core_judgment=str(payload.get("core_judgment", "")),
            narrative_logic=str(payload.get("narrative_logic", "")),
            structure=[str(c.get("title", "")) for c in chapters if c.get("title")],
            dimensions=[str(item) for item in payload.get("dimensions", [])],
            required_facts=[str(item) for item in payload.get("required_facts", [])],
            budget=payload.get("report_budget") if isinstance(payload.get("report_budget"), dict) else {},
            chapter_plans=chapters,
            user_requirements="",
        )
        return save_plan(plan)


def save_plan(plan: ReportPlan) -> ReportPlan:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO report_plans(title, objective, audience, report_type, core_question, "
            "core_judgment, narrative_logic, structure, dimensions, required_facts, chapter_plans, "
            "budget, user_requirements) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (plan.title, plan.objective, plan.audience, plan.report_type,
             plan.core_question, plan.core_judgment, plan.narrative_logic,
             json.dumps(plan.structure, ensure_ascii=False),
             json.dumps(plan.dimensions, ensure_ascii=False),
             json.dumps(plan.required_facts, ensure_ascii=False),
             json.dumps(plan.chapter_plans, ensure_ascii=False),
             json.dumps(plan.budget, ensure_ascii=False),
             plan.user_requirements),
        )
        plan.id = cur.lastrowid
    return plan
