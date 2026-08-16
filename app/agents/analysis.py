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


_GLOBAL_SYSTEM = """你是情报报告综合研判师。基于各维度局部推断与关键事实,形成跨维度综合判断。
严格输出 JSON,不要任何解释:
{
  "inferences": [
    {"content": "跨维度综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "全局综合", "analysis_type": "TREND/IMPACT/RISK/CAUSE/PREDICTION"}
  ],
  "external_notes": [{"content": "模型常识/外部知识补充,与材料无关"}],
  "critical_fact_ids": [1],
  "coverage_status": {"维度名": "SUFFICIENT/PARTIAL/WEAK/ABSENT/CONFLICTING/MATERIAL_INSUFFICIENT"},
  "unresolved_conflicts": ["未解决的矛盾口径"],
  "uncertainty": ["不确定性说明"]
}
要求:
1. 综合判断必须基于局部推断与给定事实,based_fact_ids 必须真实存在
2. 不得重复局部推断的原文,综合是跨维度的新判断
3. coverage_status 逐维度给出证据覆盖状态,证据不足的维度必须如实标注
4. critical_fact_ids 列出支撑核心结论的关键事实
5. 材料存在冲突时,unresolved_conflicts 必须列出,不得假装一致"""


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

    def analyze_local(self, dimension: str, facts: list[dict],
                      context_block: str = "") -> list[Inference]:
        """Map:单维局部分析(按维度分组的 facts),产出该维推断。"""
        if not facts:
            return []
        valid_ids = {f["id"] for f in facts}
        if not context_block:
            fact_lines = [f"{f['id']}. [{f['sources']}] {f['content']}" for f in facts]
            context_block = "事实清单(编号 + 来源):\n" + "\n".join(fact_lines)
        prompt = f"分析维度:{dimension}\n\n{context_block}\n\n请基于该维度事实做局部研判,输出 JSON。"
        payload = self.generate_json(prompt)
        call_id = self.last_call_id
        inferences, _external = self._parse_payload(payload, valid_ids, call_id)
        update_call_products(call_id, produced_inference_ids=[i.id for i in inferences if i.id])
        update_call_metrics(call_id, stored_chars=sum(len(i.content or "") + len(i.reasoning_chain or "") for i in inferences))
        return inferences

    def analyze_global(self, local_inferences: list[Inference], facts: list[dict],
                       conflicts: str = "") -> tuple[list[Inference], list[Inference], dict]:
        """Reduce:跨维度综合分析。

        输入各维局部推断 + 关键事实;输出全局推断 + 综合元数据
        (critical_fact_ids / coverage_status / unresolved_conflicts / uncertainty)。
        """
        if not local_inferences:
            return [], [], {}
        valid_ids = {f["id"] for f in facts}
        lines = ["各维度局部推断:"]
        lines.extend(
            f"- [{i.dimension or '未分类'}] {i.content} (based_fact_ids={i.based_fact_ids})"
            for i in local_inferences
        )
        if conflicts:
            lines.append(f"来源冲突:{conflicts}")
        prompt = "\n".join(lines) + "\n\n请基于局部推断与关键事实做跨维度综合研判,输出 JSON。"
        payload = self.generate_json(prompt, system=_GLOBAL_SYSTEM)
        call_id = self.last_call_id
        inferences, external = self._parse_payload(payload, valid_ids, call_id)
        meta = {
            "critical_fact_ids": [int(i) for i in (payload.get("critical_fact_ids") or []) if str(i).isdigit() and int(i) in valid_ids],
            "coverage_status": {
                str(k): str(v).upper() for k, v in (payload.get("coverage_status") or {}).items()
                if str(v).upper() in {"SUFFICIENT", "PARTIAL", "WEAK", "ABSENT", "CONFLICTING", "MATERIAL_INSUFFICIENT"}
            },
            "unresolved_conflicts": [str(c)[:200] for c in (payload.get("unresolved_conflicts") or []) if str(c).strip()],
            "uncertainty": [str(c)[:200] for c in (payload.get("uncertainty") or []) if str(c).strip()],
        }
        update_call_products(call_id, produced_inference_ids=[i.id for i in inferences + external if i.id])
        update_call_metrics(call_id, stored_chars=sum(len(i.content or "") + len(i.reasoning_chain or "") for i in inferences + external))
        return inferences, external, meta

    def _parse_payload(self, payload: dict, valid_ids: set[int], call_id: str) -> tuple[list[Inference], list[Inference]]:
        """解析推断与外部知识并保存(局部/全局共用)。"""
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
