"""Analysis Agent:基于事实的综合研判;外部知识单独标注。

硬约束:推断必须挂依据事实;无依据的判断不输出。
"""
import json
import re

from app.agents.base import BaseAgent
from app.cache import stable_hash
from app.db import session_scope
from app.infrastructure.orm import ORMInference, Base
from sqlalchemy import select
from app.models import Inference
from app.token_monitor import current_context, update_call_metrics, update_call_products

_ANALYSIS_TYPES = (
    "OBSERVATION", "CAUSE", "IMPACT", "RISK", "TREND", "PREDICTION",
    "COMPARISON", "CONSTRAINT", "UNCERTAINTY", "SYNTHESIS",
)
_CONFIDENCE_LEVELS = {"high", "medium", "low"}

_CONFIDENCE_RUBRIC = """置信度判定口径:
- high: 至少 2 条事实或 2 个独立来源直接支撑,没有明显冲突、缺口或推测成分。
- medium: 有事实支撑,但依据较单一、间接,或仍需结合上下文判断。
- low: 证据薄弱、存在冲突/缺口/不确定性,或判断带有明显外推。
不要把 medium 当作默认值;必须根据每条推断自己的证据链单独判断。"""

_SYSTEM = """你是情报分析员。基于事实清单、来源冲突与历史知识做综合分析,禁止无依据结论。
严格输出 JSON,不要任何解释:
{
  "inferences": [
    {"content": "综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "所属维度", "analysis_type": "OBSERVATION/CAUSE/IMPACT/RISK/TREND/PREDICTION/COMPARISON/CONSTRAINT/UNCERTAINTY/SYNTHESIS",
     "confidence_level": "high/medium/low", "confidence_reason": "置信度依据", "uncertainty": "不确定性或证据边界"}
  ],
  "external_notes": [
    {"content": "模型常识/外部知识补充,与材料无关"}
  ]
}
要求:
1. 每条推断必须基于给定事实,based_fact_ids 必须真实存在
2. 无事实依据的判断不得输出
3. 推断层要覆盖该维度的关键观察、原因、影响、风险、趋势、约束或不确定性;不要只输出一条总括结论
4. 材料存在冲突时,推断须避开矛盾口径或明确标注基于哪一方
5. external_notes 用于材料无法得出、但有助于理解的常识补充,必须与材料事实区分
6. confidence_level 只能是 high/medium/low: 多个独立事实直接支撑为 high; 单一或间接支撑为 medium; 证据薄弱、存在缺口或冲突为 low
7. 不要输出长篇推理过程;short_rationale 只写必要依据链
""" + _CONFIDENCE_RUBRIC


_GLOBAL_SYSTEM = """你是情报报告综合研判师。基于各维度局部推断与关键事实,形成跨维度综合判断。
严格输出 JSON,不要任何解释:
{
  "inferences": [
    {"content": "跨维度综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "全局综合", "analysis_type": "SYNTHESIS/TREND/IMPACT/RISK/CONSTRAINT/UNCERTAINTY",
     "confidence_level": "high/medium/low", "confidence_reason": "置信度依据", "uncertainty": "不确定性或证据边界"}
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
5. 材料存在冲突时,unresolved_conflicts 必须列出,不得假装一致
6. 全局判断应形成主线、关键风险和证据边界,不要简单重复局部推断
""" + _CONFIDENCE_RUBRIC


def _int_ids(values) -> list[int]:
    result = []
    for value in values or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _target_local_inference_count(fact_count: int) -> int:
    if fact_count <= 0:
        return 0
    if fact_count < 8:
        return 1
    if fact_count < 25:
        return 2
    if fact_count < 60:
        return 3
    return 4


def _normalize_confidence(value) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "高": "high", "high": "high", "h": "high",
        "中": "medium", "中等": "medium", "medium": "medium", "mid": "medium", "m": "medium",
        "低": "low", "low": "low", "l": "low",
    }
    return mapping.get(text, "")


def _source_count(fact: dict) -> int:
    sources = fact.get("sources") or fact.get("source_files") or ""
    if isinstance(sources, (list, tuple, set)):
        return len({str(item).strip() for item in sources if str(item).strip()})
    parts = re.split(r"[,，;；\n]+", str(sources))
    return len({part.strip() for part in parts if part.strip()})


def _has_uncertainty_signal(*texts: str) -> bool:
    merged = " ".join(str(text or "") for text in texts)
    markers = (
        "可能", "或许", "倾向", "推测", "尚未", "未明确", "未提供", "不足",
        "缺口", "缺失", "待", "需进一步", "需补充", "无法确认", "不确定",
        "有限", "间接", "冲突", "矛盾", "风险", "边界",
    )
    return any(marker in merged for marker in markers)


def _has_conflict_signal(fact: dict) -> bool:
    conflict_ids = fact.get("conflict_ids") or []
    if isinstance(conflict_ids, str):
        try:
            conflict_ids = json.loads(conflict_ids)
        except Exception:
            conflict_ids = [conflict_ids] if conflict_ids.strip() and conflict_ids.strip() != "[]" else []
    return bool(conflict_ids)


def _evidence_strength(based_fact_ids: list[int], facts_by_id: dict[int, dict]) -> tuple[int, int, bool]:
    fact_ids = set(based_fact_ids)
    source_total = 0
    has_conflict = False
    for fact_id in fact_ids:
        fact = facts_by_id.get(int(fact_id), {})
        source_total += max(1, _source_count(fact))
        has_conflict = has_conflict or _has_conflict_signal(fact)
    return len(fact_ids), source_total, has_conflict


def _default_confidence(based_fact_ids: list[int], rationale: str, uncertainty: str,
                        facts_by_id: dict[int, dict] | None = None) -> str:
    facts_by_id = facts_by_id or {}
    fact_count, source_total, has_conflict = _evidence_strength(based_fact_ids, facts_by_id)
    if uncertainty or has_conflict or _has_uncertainty_signal(rationale, uncertainty):
        return "low"
    if (fact_count >= 3 or source_total >= 3) and rationale:
        return "high"
    return "medium"


def _calibrate_confidence(level: str, based_fact_ids: list[int], rationale: str,
                          confidence_reason: str, uncertainty: str,
                          facts_by_id: dict[int, dict]) -> str:
    """Use domain-neutral evidence strength to avoid meaningless all-medium output."""
    fact_count, source_total, has_conflict = _evidence_strength(based_fact_ids, facts_by_id)
    weak_signal = has_conflict or _has_uncertainty_signal(rationale, confidence_reason, uncertainty)
    strong_signal = fact_count >= 3 or source_total >= 3
    if not level:
        return _default_confidence(based_fact_ids, rationale, uncertainty, facts_by_id)
    if weak_signal and level != "low":
        return "low"
    if level == "medium" and strong_signal and not weak_signal and rationale:
        return "high"
    if level == "medium" and fact_count <= 1 and weak_signal:
        return "low"
    return level


def _confidence_reason(level: str, based_fact_ids: list[int],
                       facts_by_id: dict[int, dict] | None = None) -> str:
    facts_by_id = facts_by_id or {}
    count, source_total, has_conflict = _evidence_strength(based_fact_ids, facts_by_id)
    if level == "high":
        return f"由 {count} 条事实、约 {source_total} 个来源支撑,证据链较完整"
    if level == "low":
        suffix = "且存在冲突或不确定性" if has_conflict else "需关注证据边界"
        return f"由 {count} 条事实有限支撑,{suffix}"
    return f"由 {count} 条事实支撑,依据较单一或仍需结合上下文复核"


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
        target_count = _target_local_inference_count(len(facts))
        prompt = (
            f"分析维度:{dimension}\n"
            f"事实数量:{len(facts)}\n"
            f"建议推论数量:{target_count} 条左右;事实很少时可少于该数量,但不得为凑数重复。\n\n"
            f"{context_block}\n\n"
            "请先按主题/问题/实体/机制/时间线在心中组织事实,再生成该维度的结构化推论层。"
            "每条推论必须表达一个清晰判断,并挂真实 based_fact_ids、confidence_level、confidence_reason、uncertainty。"
            "输出 JSON。"
        )
        payload = self.generate_json(prompt)
        call_id = self.last_call_id
        inferences, _external = self._parse_payload(payload, valid_ids, call_id, facts)
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
            f"- [{i.dimension or '未分类'}] {i.content} "
            f"(type={i.analysis_type or 'SYNTHESIS'}, confidence={i.confidence_level}, "
            f"based_fact_ids={i.based_fact_ids}, uncertainty={i.uncertainty or '无'})"
            for i in local_inferences
        )
        if conflicts:
            lines.append(f"来源冲突:{conflicts}")
        prompt = (
            "\n".join(lines)
            + "\n\n请基于局部推断与关键事实做跨维度综合研判,输出 3-6 条全局推论。"
              "每条必须挂真实 based_fact_ids、confidence_level、confidence_reason、uncertainty,输出 JSON。"
        )
        payload = self.generate_json(prompt, system=_GLOBAL_SYSTEM)
        call_id = self.last_call_id
        inferences, external = self._parse_payload(payload, valid_ids, call_id, facts)
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

    def _parse_payload(self, payload: dict, valid_ids: set[int], call_id: str,
                       facts: list[dict] | None = None) -> tuple[list[Inference], list[Inference]]:
        """解析推断与外部知识并保存(局部/全局共用)。"""
        facts_by_id = {int(f.get("id")): f for f in (facts or []) if str(f.get("id") or "").isdigit()}
        inferences: list[Inference] = []
        for item in payload.get("inferences", []):
            # 兼容模型两种输出:对象 / 纯文本字符串(小模型提示跟随不稳)
            if isinstance(item, str):
                content = item.strip()
                based, analysis_type = [], ""
                reasoning = dimension = ""
                confidence = ""
                confidence_reason = uncertainty = ""
            else:
                content = str(item.get("content", "")).strip()
                based = [i for i in _int_ids(item.get("based_fact_ids")) if i in valid_ids]
                analysis_type = str(item.get("analysis_type", "")).upper()
                reasoning = str(item.get("short_rationale") or item.get("reasoning") or "")[:80]
                dimension = str(item.get("dimension", ""))
                confidence = _normalize_confidence(item.get("confidence_level") or item.get("confidence"))
                confidence_reason = str(item.get("confidence_reason") or "")[:120]
                uncertainty = str(item.get("uncertainty") or "")[:160]
            if not content or not based:
                continue  # 硬约束:推断必须挂依据事实
            if analysis_type not in _ANALYSIS_TYPES:
                analysis_type = ""
            confidence = _calibrate_confidence(
                confidence, based, reasoning, confidence_reason, uncertainty, facts_by_id
            )
            inference = Inference(
                content=content,
                source_level="MATERIAL_INFERENCE",
                based_fact_ids=based,
                reasoning_chain=reasoning,
                dimension=dimension,
                analysis_type=analysis_type,
                confidence_level=confidence,
                confidence_reason=confidence_reason or _confidence_reason(confidence, based, facts_by_id),
                uncertainty=uncertainty,
            )
            inference.id = save_inference(inference, origin_call_id=call_id)
            inferences.append(inference)
        external: list[Inference] = []
        for item in payload.get("external_notes", []):
            content = str(item.get("content", "")).strip() if isinstance(item, dict) else str(item).strip()
            if not content:
                continue
            note = Inference(
                content=content,
                source_level="EXTERNAL_INFORMATION",
                confidence_level="low",
                confidence_reason="外部补充不直接来自当前材料事实",
                uncertainty="需与材料证据分开使用",
            )
            note.id = save_inference(note, origin_call_id=call_id)
            external.append(note)
        return inferences, external


def save_inference(inference: Inference, origin_call_id: str = "") -> int:
    stable_key = stable_hash({
        "dimension": inference.dimension,
        "analysis_type": inference.analysis_type,
        "based_fact_ids": sorted(int(fid) for fid in inference.based_fact_ids),
    })
    run_id = str(current_context().get("run_id") or "")
    with session_scope() as s:
        result = s.execute(
            ORMInference.insert().values(
                content=inference.content, source_level=inference.source_level,
                based_fact_ids=json.dumps(inference.based_fact_ids),
                reasoning_chain=inference.reasoning_chain,
                dimension=inference.dimension, analysis_type=inference.analysis_type,
                confidence_level=inference.confidence_level,
                confidence_reason=inference.confidence_reason,
                uncertainty=inference.uncertainty,
                origin_call_id=origin_call_id,
                stable_key=stable_key, lifecycle_status="active", introduced_run_id=run_id,
            )
        )
        inference_id = int(result.inserted_primary_key[0])
        # 数据血缘:推断 → 依据事实(关联表,支持"事实被哪些推断使用"查询)
        inference_fact = Base.metadata.tables["inference_fact"]
        for fact_id in inference.based_fact_ids:
            try:
                exists = s.execute(
                    select(inference_fact.c.inference_id).where(
                        inference_fact.c.inference_id == inference_id,
                        inference_fact.c.fact_id == int(fact_id),
                    )
                ).first()
                if exists is None:
                    s.execute(inference_fact.insert().values(
                        inference_id=inference_id, fact_id=int(fact_id)))
            except Exception:
                continue  # 依据事实不存在(幻觉 id/旧数据)时跳过血缘,不影响推断本身
        return inference_id

__all__ = ['AnalysisAgent', 'save_inference']
