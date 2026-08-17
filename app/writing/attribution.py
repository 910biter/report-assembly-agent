"""Evidence Attribution Plan:写之前先确定"准备说什么"和"凭什么说"。

Narrative Topic → 观点/论断 → 证据绑定(facts/inferences)。
Writer 只按 Plan 自然成文,不再自己找证据。
"""
from __future__ import annotations

import json

from app.agents.base import BaseAgent

_ATTRIBUTION_SYSTEM = """你是证据归因规划师。为一个报告 Topic 规划观点与证据绑定。
对 Topic 的每个核心观点,指定支撑的事实/推断(用 ID 引用),并说明论证关系。
严格输出 JSON,不要任何解释:
{"points": [
  {"statement": "观点陈述", "supporting_fact_ids": [1, 2], "supporting_inference_ids": [3],
   "relation": "direct/corroborating/derived", "rationale": "为什么这些证据支撑该观点"}
]}
要求:
1. 证据必须真实存在(只能引用提供的 ID 列表)
2. 没有证据支撑的观点不得输出(宁可少)
3. relation 表示证据与观点的关系:direct=直接陈述, corroborating=多源印证, derived=由证据推导
4. rationale 一句话说明论证链"""


class AttributionPlanner(BaseAgent):
    name = "attribution"
    role = _ATTRIBUTION_SYSTEM

    def plan_topic(self, topic: dict, facts: list[dict], inferences: list[dict]) -> dict:
        """为单个 Topic 生成 Attribution Plan。

        topic: {topic, core_message, supporting_fact_ids, supporting_inference_ids, ...}
        facts: [{id, content, sources}]
        inferences: [{id, content, based_fact_ids}]
        """
        fact_index = {f["id"]: f for f in facts}
        inference_index = {i["id"]: i for i in inferences}
        available_facts = [{"id": f["id"], "content": f["content"],
                            "sources": f.get("sources", [])} for f in facts]
        available_inferences = [{"id": i["id"], "content": i["content"]} for i in inferences]
        payload = self.generate_json(
            f"Topic:{topic.get('topic', '')}\n"
            f"核心信息:{topic.get('core_message', '')}\n"
            f"可用事实:\n{json.dumps(available_facts, ensure_ascii=False)[:6000]}\n"
            f"可用推断:\n{json.dumps(available_inferences, ensure_ascii=False)[:3000]}\n"
            f"请输出该 Topic 的 Attribution Plan JSON。"
        )
        points = payload.get("points") if isinstance(payload.get("points"), list) else []
        # 程序校验:只保留引用真实存在的证据
        valid = []
        for point in points:
            raw_fid = point.get("supporting_fact_ids") or []
            raw_iid = point.get("supporting_inference_ids") or []
            try:
                fid = [int(x) for x in raw_fid if int(x) in fact_index]
                iid = [int(x) for x in raw_iid if int(x) in inference_index]
            except (TypeError, ValueError):
                fid, iid = [], []
            if not fid and not iid:
                continue  # 无证据的观点丢弃(不编造)
            valid.append({
                "statement": str(point.get("statement", "")),
                "supporting_fact_ids": fid,
                "supporting_inference_ids": iid,
                "relation": str(point.get("relation", "direct")),
                "rationale": str(point.get("rationale", "")),
            })
        return {"topic": topic.get("topic", ""), "points": valid}
