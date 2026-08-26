"""写作 QA + Repair Router:一次调用四类质检,程序按问题路由修复。

- Evidence QA:裸结论/引用错误
- Coverage QA:Topic 完成条件是否满足
- Narrative QA:逻辑/层级/衔接
- Style QA:语言/重复/格式

QA 只发现问题;修复动作由 Repair Router 决定(程序路由,不编造)。
"""
from __future__ import annotations

from app.agents.base import BaseAgent
from app.context_budget import ContextSection, build_prompt_from_sections
from app.runtime_profiles import stage_input_budget_tokens

_QA_SYSTEM = """你是报告质检员。对给定报告段落进行四类质检,一次输出全部结果。
严格输出 JSON,不要任何解释:
{
  "evidence": {"ok": true, "issues": ["裸结论无引用", "引用与内容不符"]},
  "coverage": {"ok": true, "issues": ["Topic 完成条件未满足:缺少..."], "missing_fact_ids": []},
  "narrative": {"ok": true, "issues": ["逻辑跳跃", "层级混乱"]},
  "style": {"ok": true, "issues": ["重复表达", "口语化"]}
}
要求:
1. 只报告真实问题;无问题 ok=true
2. evidence 检查:段落中每个论断是否有引用支撑(fact_ids/inference_ids)
3. coverage 检查:对照 Topic 的完成条件与分配的证据
4. 禁止臆造问题"""

_REPAIR_ROUTES = {
    "citation": "attribution_repair",      # 引用错误 → Attribution Plan 修复
    "evidence_missing": "evidence_gap",    # 缺证据 → 返回 Gap Retrieval
    "coverage": "narrative_rewrite",       # Topic 不完整 → 重写/补写
    "narrative": "writer_local_rewrite",   # 表达问题 → 局部重写
    "style": "writer_local_rewrite",       # 风格问题 → 局部重写
    "structure": "narrative_replan",       # 结构问题 → 重新规划
}


class ReportQA(BaseAgent):
    name = "qa"
    role = _QA_SYSTEM

    def check(self, section: dict, topic: dict | None = None,
              facts: list[dict] | None = None) -> dict:
        """质检报告片段。facts 供 evidence QA 核对引用真实性。"""
        cited_ids = {int(value) for value in section.get("fact_ids", []) if str(value).isdigit()}
        ordered_facts = sorted(
            facts or [], key=lambda item: 0 if int(item.get("id") or 0) in cited_ids else 1,
        )
        prompt, _audit = build_prompt_from_sections(
            "qa",
            [
                ContextSection("检查任务", [
                    "请输出四类质检 JSON。",
                    f"引用:facts={section.get('fact_ids', [])} inferences={section.get('inference_ids', [])}",
                ], weight=4, required_items=2),
                ContextSection("报告段落", ["报告段落:", section.get("content", "")], weight=6, required_items=2),
                ContextSection("Topic", [
                    f"Topic:{topic.get('topic', '') if topic else ''}",
                    f"完成条件:{topic.get('completion_criteria', '') if topic else ''}",
                ], weight=4),
                ContextSection("可用事实", ["可用事实:", *[
                    f"fact_id={fact.get('id')}: {fact.get('content', '')}" for fact in ordered_facts
                ]], weight=5),
            ],
            stage_input_budget_tokens("qa"),
        )
        payload = self.generate_json(prompt)
        return {
            "evidence": payload.get("evidence", {"ok": True, "issues": []}),
            "coverage": payload.get("coverage", {"ok": True, "issues": [], "missing_fact_ids": []}),
            "narrative": payload.get("narrative", {"ok": True, "issues": []}),
            "style": payload.get("style", {"ok": True, "issues": []}),
        }


def route_repairs(qa_result: dict) -> list[dict]:
    """Repair Router:按 QA 问题路由到修复动作(程序决定,不编造)。"""
    actions: list[dict] = []
    evidence = qa_result.get("evidence") or {}
    if not evidence.get("ok", True):
        actions.append({
            "action": _REPAIR_ROUTES["citation"],
            "target": "sentence",
            "reason": "; ".join(evidence.get("issues", []))[:200],
        })
    coverage = qa_result.get("coverage") or {}
    if not coverage.get("ok", True):
        actions.append({
            "action": _REPAIR_ROUTES["coverage"],
            "target": "topic",
            "reason": "; ".join(coverage.get("issues", []))[:200],
            "missing_fact_ids": coverage.get("missing_fact_ids", []),
        })
    narrative = qa_result.get("narrative") or {}
    if not narrative.get("ok", True):
        actions.append({
            "action": _REPAIR_ROUTES["narrative"],
            "target": "paragraph",
            "reason": "; ".join(narrative.get("issues", []))[:200],
        })
    style = qa_result.get("style") or {}
    if not style.get("ok", True):
        actions.append({
            "action": _REPAIR_ROUTES["style"],
            "target": "paragraph",
            "reason": "; ".join(style.get("issues", []))[:200],
        })
    return actions
