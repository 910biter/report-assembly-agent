"""Analysis Agent:基于事实的综合研判;外部知识单独标注。

硬约束:推断必须挂依据事实;无依据的判断不输出。
"""
import json

from app.agents.base import BaseAgent
from app.db import connect
from app.models import Inference
from app.token_monitor import update_call_metrics, update_call_products

_ANALYSIS_TYPES = ("TREND", "IMPACT", "RISK", "CAUSE", "PREDICTION")

_SYSTEM = """你是情报分析员。基于事实清单、来源冲突与历史知识做综合分析,禁止无依据结论。
严格输出 JSON,不要任何解释:
{
  "inferences": [
    {"content": "综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "所属维度", "analysis_type": "TREND/IMPACT/RISK/CAUSE/PREDICTION"}
  ],
  "external_notes": [
    {"content": "模型常识/外部知识补充,与材料无关"}
  ]
}
要求:
1. 每条推断必须基于给定事实,based_fact_ids 必须真实存在
2. 无事实依据的判断不得输出
3. analysis_type 按性质:趋势TREND/影响IMPACT/风险RISK/原因CAUSE/预测PREDICTION
4. 材料存在冲突时,推断须避开矛盾口径或明确标注基于哪一方
5. external_notes 用于材料无法得出、但有助于理解的常识补充,必须与材料事实区分
6. 不要输出长篇推理过程;short_rationale 只写必要依据链"""


def _int_ids(values) -> list[int]:
    result = []
    for value in values or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


class AnalysisAgent(BaseAgent):
    name = "analysis"
    role = _SYSTEM

    def analyze(self, facts: list[dict], context_block: str = "") -> tuple[list[Inference], list[Inference]]:
        """输入事实清单 + Context Manager 生成的上下文块(冲突/时间线/历史知识)。

        context_block 为空时回退为纯事实清单(兼容直接调用)。
        """
        if not facts:
            return [], []
        valid_ids = {f["id"] for f in facts}
        if not context_block:
            fact_lines = [f"{f['id']}. [{f['sources']}] {f['content']}" for f in facts]
            context_block = "事实清单(编号 + 来源):\n" + "\n".join(fact_lines)
        prompt = f"{context_block}\n\n请基于以上事实与冲突做综合研判,输出 JSON。"
        payload = self.generate_json(prompt)
        call_id = self.last_call_id
        inferences: list[Inference] = []
        for item in payload.get("inferences", []):
            # 兼容模型两种输出:对象 / 纯文本字符串(小模型提示跟随不稳)
            if isinstance(item, str):
                content = item.strip()
                based, analysis_type = [], ""
                reasoning = dimension = ""
            else:
                content = str(item.get("content", "")).strip()
                based = [i for i in _int_ids(item.get("based_fact_ids")) if i in valid_ids]
                analysis_type = str(item.get("analysis_type", "")).upper()
                reasoning = str(item.get("short_rationale") or item.get("reasoning") or "")[:80]
                dimension = str(item.get("dimension", ""))
            if not content or not based:
                continue  # 硬约束:推断必须挂依据事实
            if analysis_type not in _ANALYSIS_TYPES:
                analysis_type = ""
            inference = Inference(
                content=content,
                source_level="MATERIAL_INFERENCE",
                based_fact_ids=based,
                reasoning_chain=reasoning,
                dimension=dimension,
                analysis_type=analysis_type,
            )
            inference.id = save_inference(inference, origin_call_id=call_id)
            inferences.append(inference)
        external: list[Inference] = []
        for item in payload.get("external_notes", []):
            content = str(item.get("content", "")).strip() if isinstance(item, dict) else str(item).strip()
            if not content:
                continue
            note = Inference(content=content, source_level="EXTERNAL_INFORMATION")
            note.id = save_inference(note, origin_call_id=call_id)
            external.append(note)
        update_call_products(call_id, produced_inference_ids=[i.id for i in inferences + external if i.id])
        update_call_metrics(call_id, stored_chars=sum(len(i.content or "") + len(i.reasoning_chain or "") for i in inferences + external))
        return inferences, external


def save_inference(inference: Inference, origin_call_id: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO inferences(content, source_level, based_fact_ids, reasoning_chain, "
            "dimension, analysis_type, origin_call_id) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (inference.content, inference.source_level,
             json.dumps(inference.based_fact_ids), inference.reasoning_chain,
             inference.dimension, inference.analysis_type, origin_call_id),
        )
        # 数据血缘:推断 → 依据事实(关联表,支持"事实被哪些推断使用"查询)
        for fact_id in inference.based_fact_ids:
            try:
                conn.execute(
                    "INSERT INTO inference_fact(inference_id, fact_id) VALUES(?, ?) "
                    "ON CONFLICT(inference_id, fact_id) DO NOTHING",
                    (cur.lastrowid, int(fact_id)),
                )
            except Exception:
                continue  # 依据事实不存在(幻觉 id/旧数据)时跳过血缘,不影响推断本身
        return cur.lastrowid
