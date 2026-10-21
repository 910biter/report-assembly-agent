"""Analysis Agent:基于事实的综合研判;外部知识单独标注。

硬约束:推断必须挂依据事实;无依据的判断不输出。
"""
import json
import re

from app.agents.base import BaseAgent
from app.cache import stable_hash
from app.config import settings

from app.context_budget import ContextSection, build_prompt_from_sections
from app.db import session_scope
from app.analysis.evidence_semantics import analysis_fact_line, analysis_source_lines
from app.assertion_semantics import (
    normalize_semantic_profile,
    semantic_profile_json,
    semantic_profile_line,
    source_semantics_line,
    source_semantics_for_facts,
)
from app.infrastructure.orm import ORMInference, Base
from sqlalchemy import select
from app.models import Inference
from app.token_monitor import current_context, update_call_funnel, update_call_metrics, update_call_products
from app.runtime_profiles import stage_input_budget_tokens, stage_profile

_SYSTEM = """你是情报分析员。根据本次给定的事实及其来源语义，形成可解释的局部判断。
只输出 JSON:
{
  "inferences": [
    {"content": "综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "所属维度", "analysis_type": "开放类型标签",
     "semantic_profile": {"facets": [], "attribution": "原文明确的归属（仅相关时）", "conditions": [], "scope_qualifiers": []}}
  ],
  "external_notes": [
    {"content": "模型常识/外部知识补充,与材料无关"}
  ]
}
工作方法:
1. 每条 inferences 使用本次给定、且直接支撑判断的 based_fact_ids；在 short_rationale 中简述事实之间的关系。具体数值、日期和对象来自所引事实；由多个数值计算的新结果应列出全部操作数的事实 ID 和计算方式。缺少依据的想法可暂不输出。
2. 围绕关键观察、原因、影响、风险、趋势及约束形成彼此有区别的判断，并写明事实之间的关系或材料可支持的解释。纯粹换说法重述单条事实的内容留在事实层；相近判断整合，互相矛盾的口径分别注明来源和范围。机构行动的动机、市场需求与实际效果，须有材料明示或可追溯的事实关系支撑；依据尚弱时将含义表述为待验证的解释。content 保留事实的主体、时间、条件、来源归属及现状/目标/计划/预测等陈述性质；来源观点仍写为来源观点。
3. 本次是局部事实批次。关于信息缺口，只能描述明确检查过的文件或批次范围；对未提供的全局信息保持开放。依据应逐条查看事实、原文片段及必要上下文，材料级摘要、标题和领域词只作定位线索。
4. semantic_profile 按需补充 content 未表达的时间、条件、归属、实现状态与范围，类别开放，字段简短；source_semantics 由程序继承。单条置信度由系统依据有效引用计算，无需在 JSON 中输出。外部常识只放 external_notes，与材料推断分开。"""


_GLOBAL_SYSTEM = """你是情报报告综合研判师。根据局部推论和关键事实，形成跨维度判断。
只输出 JSON:
{
  "inferences": [
    {"content": "跨维度综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "全局综合", "analysis_type": "开放类型标签",
     "semantic_profile": {"facets": [], "attribution": "原文明确的归属（仅相关时）", "conditions": [], "scope_qualifiers": []}}
  ],
  "external_notes": [{"content": "模型常识/外部知识补充,与材料无关"}],
  "critical_fact_ids": [1],
  "coverage_status": {"维度名": "SUFFICIENT/PARTIAL/WEAK/ABSENT/CONFLICTING/MATERIAL_INSUFFICIENT"},
  "unresolved_conflicts": ["未解决的矛盾口径"],
  "uncertainty": ["不确定性说明"]
}
工作方法:
1. 每条 inferences 提出跨维度的新判断，使用直接支撑它的 based_fact_ids，并在 short_rationale 中说明综合关系。具体数值、日期和对象由所引事实支持；计算结果应交代所用事实及算法。
2. 结合不同维度形成主线、风险和证据边界，解释事实间的关系、变化或影响。仅重述单条事实的内容留在事实层；相近判断整合。行动动机、外部需求与效果须能从来源或给定事实关系中定位，依据有限时保持为待验证解释。保留来源归属、时间、条件、版本及现状/计划/预测等性质；不同来源与不同范围分别陈述。局部推论中的“未提供”只适用于它实际检查过的范围，再对照本次给定事实判断全局覆盖。
3. coverage_status 逐维度标注证据充分程度，critical_fact_ids 列核心事实，unresolved_conflicts 列尚未消解的不同口径。材料级摘要、标题和领域词用来定位，不代替逐条事实和原文核验。
4. semantic_profile 仅按需补充影响真实性的时间、条件、归属、实现状态及范围，类别开放；source_semantics 由程序继承。单条置信度由系统按有效引用计算，外部常识另列 external_notes。"""


def _int_ids(values) -> list[int]:
    result = []
    for value in values or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _visible_fact_ids(prompt: str, candidates: list[dict] | None = None) -> set[int]:
    """Read fact IDs from the concrete numbered fact lines in the final prompt."""
    content_by_id = {
        int(item["id"]): str(item.get("content") or "")
        for item in candidates or [] if item.get("id") is not None
    }
    visible = set()
    for line in str(prompt or "").splitlines():
        match = re.match(r"^\s*(\d+)\.\s+\[", line)
        if not match:
            continue
        fact_id = int(match.group(1))
        if not content_by_id or (fact_id in content_by_id and content_by_id[fact_id] in line):
            visible.add(fact_id)
    return visible


def _visible_local_inference_refs(prompt: str, inferences: list) -> tuple[set[int], set[int]]:
    """Return IDs and Fact bases for complete local-inference records shown in prompt."""
    lines = set(str(prompt or "").splitlines())
    visible_ids: set[int] = set()
    visible_fact_ids: set[int] = set()
    for item in inferences:
        get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        inference_id = get("id")
        if inference_id is None:
            continue
        based_fact_ids = get("based_fact_ids") or []
        record = _global_inference_record(item)
        if record in lines:
            visible_ids.add(int(inference_id))
            visible_fact_ids.update(_int_ids(based_fact_ids))
    return visible_ids, visible_fact_ids


def _global_inference_record(item) -> str:
    get = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
    return (
        f"- [inference_id={get('id')}; {get('dimension') or '未分类'}] {get('content') or ''} "
        f"(type={get('analysis_type') or 'SYNTHESIS'}, based_fact_ids={get('based_fact_ids') or []}, "
        f"semantic_profile={semantic_profile_line(get('semantic_profile'))}, "
        f"source_semantics={source_semantics_line(get('source_semantics'))})"
    )


def _visible_local_inference_ids(prompt: str, inferences: list) -> set[int]:
    """Match complete local-inference records without adding prompt-only IDs."""
    visible_ids, _ = _visible_local_inference_refs(prompt, inferences)
    return visible_ids


def _filter_fact_references(values, allowed_ids: set[int]) -> tuple[list[int], list[int]]:
    """Return unique, allowed IDs and unique references outside the supplied context."""
    accepted = sorted({value for value in _int_ids(values) if value in allowed_ids})
    rejected = sorted({value for value in _int_ids(values) if value not in allowed_ids})
    return accepted, rejected


def _target_local_inference_count(fact_count: int, group_count: int = 0) -> int:
    if fact_count <= 0:
        return 0
    if fact_count < 8:
        base = 1
    elif fact_count < 25:
        base = 2
    elif fact_count < 60:
        base = 3
    else:
        base = 4
    if group_count <= 0:
        return base
    # Extra output room covers the same conclusions and their semantic metadata.
    # This suggestion does not limit the number of valid returned inferences.
    output_capacity = max(4, min(
        int(settings.analysis_target_inferences_per_batch),
        stage_profile("analysis").output_tokens // max(1, int(settings.analysis_inference_token_budget)),
    ))
    return max(base, min(int(group_count), int(fact_count), output_capacity))


def _semantic_fact_groups(facts: list[dict]) -> dict[tuple[str, int, str], list[dict]]:
    """Group facts using runtime planning keys, without domain assumptions."""
    groups: dict[tuple[str, int, str], list[dict]] = {}
    for fact in facts:
        key = (
            str(fact.get("dimension") or "未分类"),
            int(fact.get("need_id") or 0),
            str(fact.get("fact_type") or "unknown"),
        )
        groups.setdefault(key, []).append(fact)
    return groups


def _semantic_group_overview(facts: list[dict]) -> tuple[str, int]:
    """Build a compact, generic map of groups already present in the prompt."""
    groups = _semantic_fact_groups(facts)
    lines = ["本批次事实组概览（用于覆盖检查，不要求一组生成一条推断）："]
    for index, ((dimension, need_id, fact_type), items) in enumerate(groups.items(), start=1):
        sample_ids = ",".join(str(item.get("id")) for item in items[:6])
        if len(items) > 6:
            sample_ids = f"{sample_ids},..."
        lines.append(
            f"- 组{index}: dimension={dimension}, need_id={need_id or 'open'}, "
            f"fact_type={fact_type}, 事实数={len(items)}, 事实ID={sample_ids}"
        )
    return "\n".join(lines), len(groups)


def _semantic_group_coverage(facts: list[dict], inferences: list) -> dict:
    """Audit semantic-group coverage from deterministic fact bindings."""
    groups = _semantic_fact_groups(facts)
    referenced: set[int] = set()
    for inference in inferences:
        values = inference.get("based_fact_ids") if isinstance(inference, dict) else getattr(inference, "based_fact_ids", [])
        for value in values or []:
            try:
                referenced.add(int(value))
            except (TypeError, ValueError):
                continue
    covered_groups = 0
    uncovered_groups = []
    for (dimension, need_id, fact_type), items in groups.items():
        fact_ids = sorted(int(item["id"]) for item in items if item.get("id") is not None)
        group_covered = set(fact_ids) & referenced
        if group_covered:
            covered_groups += 1
        else:
            uncovered_groups.append({
                "dimension": dimension,
                "need_id": need_id or None,
                "fact_type": fact_type,
                "fact_count": len(fact_ids),
                "fact_ids": fact_ids,
            })
    total_groups = len(groups)
    all_fact_ids = sorted({
        int(item["id"]) for items in groups.values() for item in items
        if item.get("id") is not None
    })
    total_facts = len(all_fact_ids)
    referenced_fact_ids = sorted(set(all_fact_ids) & referenced)
    return {
        "total_groups": total_groups,
        "covered_groups": covered_groups,
        "coverage_ratio": round(covered_groups / total_groups, 4) if total_groups else 1.0,
        "total_facts": total_facts,
        "covered_facts": len(referenced_fact_ids),
        "fact_coverage_ratio": round(len(referenced_fact_ids) / total_facts, 4) if total_facts else 1.0,
        "referenced_fact_ids": referenced_fact_ids,
        "unreferenced_fact_ids": sorted(set(all_fact_ids) - set(referenced_fact_ids)),
        "coverage_semantics": "A group is counted as touched when at least one member Fact is referenced; this is diagnostic, not a target.",
        "uncovered_groups": uncovered_groups,
    }


def _confidence_from_facts(based_fact_ids: list[int]) -> tuple[str, str]:
    """Deterministic confidence contract based on valid, unique fact bindings."""
    count = len(set(int(value) for value in based_fact_ids))
    if count <= 0:
        return "low", "未绑定有效事实依据"
    if count == 1:
        return "medium", "由 1 条有效事实支撑"
    return "high", f"由 {count} 条有效事实共同支撑"


class AnalysisAgent(BaseAgent):
    name = "analysis"
    role = _SYSTEM

    def analyze_local(self, dimension: str, facts: list[dict],
                      context_block: str = "") -> list[Inference]:
        """Map:单维局部分析(按维度分组的 facts),产出该维推断。"""
        if not facts:
            return []
        candidate_ids = {int(f["id"]) for f in facts if f.get("id") is not None}
        if not context_block:
            fact_lines = [analysis_fact_line(f) for f in facts]
            source_lines = analysis_source_lines(facts)
            context_block = "事实清单(编号 + 来源陈述性质):\n" + "\n".join(fact_lines)
            if source_lines:
                context_block += "\n原文所在章节及上下文(辅助判读):\n" + "\n".join(source_lines)
        group_overview, group_count = _semantic_group_overview(facts)
        target_count = _target_local_inference_count(len(facts), group_count)
        instruction = (
            f"分析维度:{dimension}\n"
            f"事实数量:{len(facts)}\n"
            f"建议推论数量:{target_count} 条左右;这是覆盖提示而非硬上限,事实很少时可少于该数量,不得为凑数重复。\n\n"
            f"{group_overview}\n"
            f"事实组数量:{group_count}。\n"
            "请先按主题/问题/实体/机制/时间线在心中组织事实,再生成该维度的结构化推论层。"
            "优先覆盖主要事实组；相近事实组可以合并为一条推论，不要机械地一组生成一条。"
            "如果某个事实组无法形成可靠判断，可以不生成推论，但不得假装已经覆盖。"
            "每条推论必须表达一个清晰判断并挂真实 based_fact_ids；不要输出置信度相关字段。"
            "输出 JSON。"
        )
        prompt, _audit = build_prompt_from_sections("analysis", [
            ContextSection("instruction", [instruction], weight=5, required_items=1),
            ContextSection("facts", context_block.splitlines(), weight=4, required_items=1),
        ], stage_input_budget_tokens("analysis"))
        visible_ids = _visible_fact_ids(prompt, facts)
        try:
            payload = self.generate_json(prompt)
        except Exception as exc:
            call_id = self.last_call_id
            update_call_funnel(
                call_id,
                analysis_scope="local",
                candidate_fact_ids=sorted(candidate_ids),
                input_fact_ids=sorted(visible_ids),
                omitted_candidate_fact_ids=sorted(candidate_ids - visible_ids),
                analysis_generation_error=str(exc)[:300],
            )
            raise
        call_id = self.last_call_id
        inferences, external, parse_audit = self._parse_payload(payload, visible_ids, call_id, facts)
        referenced_ids = sorted({fact_id for item in inferences for fact_id in item.based_fact_ids})
        update_call_funnel(
            call_id,
            analysis_scope="local",
            candidate_fact_ids=sorted(candidate_ids),
            input_fact_ids=sorted(visible_ids),
            omitted_candidate_fact_ids=sorted(candidate_ids - visible_ids),
            referenced_fact_ids=referenced_ids,
            material_inference_ids=[int(i.id) for i in inferences if i.id],
            external_information_ids=[int(i.id) for i in external if i.id],
            **parse_audit,
        )
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
        task_fact_ids = {int(f["id"]) for f in facts if f.get("id") is not None}
        inference_lines = ["各维度局部推断:"]
        inference_lines.extend(_global_inference_record(i) for i in local_inferences)
        instruction = ("请基于局部推断与关键事实做跨维度综合研判,输出 3-6 条全局推论。"
                       "每条必须挂真实 based_fact_ids；不要输出单条置信度相关字段。"
                       "保留所引事实和局部推论的陈述性质；目标/要求/建议不能升级为已实现状态。输出 JSON。")
        referenced_fact_ids = {
            int(fact_id)
            for item in local_inferences
            for fact_id in (item.based_fact_ids or [])
        }
        referenced_facts = [
            fact for fact in facts
            if fact.get("id") is not None and int(fact["id"]) in referenced_fact_ids
        ]
        fact_lines = [analysis_fact_line(fact) for fact in referenced_facts]
        source_lines = analysis_source_lines(referenced_facts)
        prompt, _audit = build_prompt_from_sections("analysis", [
            ContextSection("instruction", [instruction], weight=5, required_items=1),
            ContextSection("local_inferences", inference_lines, weight=4, required_items=1),
            ContextSection("supporting_facts_and_assertion_modes", fact_lines, weight=5, required_items=1),
            ContextSection("source_sections_and_original_context", source_lines, weight=2),
            ContextSection("conflicts", [f"来源冲突:{conflicts}"] if conflicts else [], weight=4, required_items=1),
        ], stage_input_budget_tokens("analysis"))
        visible_inference_ids, visible_fact_ids = _visible_local_inference_refs(prompt, local_inferences)
        visible_ids = visible_fact_ids & _visible_fact_ids(prompt, referenced_facts) & task_fact_ids
        try:
            payload = self.generate_json(prompt, system=_GLOBAL_SYSTEM)
        except Exception as exc:
            call_id = self.last_call_id
            update_call_funnel(
                call_id,
                analysis_scope="global",
                input_inference_ids=sorted(visible_inference_ids),
                input_fact_ids=sorted(visible_ids),
                omitted_task_fact_count=len(task_fact_ids - visible_ids),
                analysis_generation_error=str(exc)[:300],
            )
            raise
        call_id = self.last_call_id
        inferences, external, parse_audit = self._parse_payload(payload, visible_ids, call_id, facts)
        referenced_ids = sorted({fact_id for item in inferences for fact_id in item.based_fact_ids})
        update_call_funnel(
            call_id,
            analysis_scope="global",
            input_inference_ids=sorted(visible_inference_ids),
            input_fact_ids=sorted(visible_ids),
            task_fact_ids=sorted(task_fact_ids),
            omitted_task_fact_count=len(task_fact_ids - visible_ids),
            referenced_fact_ids=referenced_ids,
            material_inference_ids=[int(i.id) for i in inferences if i.id],
            external_information_ids=[int(i.id) for i in external if i.id],
            **parse_audit,
        )
        meta = {
            "critical_fact_ids": [int(i) for i in (payload.get("critical_fact_ids") or []) if str(i).isdigit() and int(i) in visible_ids],
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
                       facts: list[dict] | None = None) -> tuple[list[Inference], list[Inference], dict]:
        """解析推断与外部知识并保存(局部/全局共用)。"""
        inferences: list[Inference] = []
        candidate_count = rejected_empty_content = rejected_without_visible_basis = 0
        invalid_fact_ids: set[int] = set()
        for item in payload.get("inferences", []):
            candidate_count += 1
            # 兼容模型两种输出:对象 / 纯文本字符串(小模型提示跟随不稳)
            if isinstance(item, str):
                content = item.strip()
                based, analysis_type = [], ""
                reasoning = dimension = ""
                semantic_profile = {}
            else:
                content = str(item.get("content", "")).strip()
                based, invalid = _filter_fact_references(item.get("based_fact_ids"), valid_ids)
                invalid_fact_ids.update(invalid)
                analysis_type = str(item.get("analysis_type") or "unknown").strip()[:64]
                reasoning = str(item.get("short_rationale") or item.get("reasoning") or "")[:80]
                dimension = str(item.get("dimension", ""))
                semantic_profile = normalize_semantic_profile(item.get("semantic_profile"))
            if not content:
                rejected_empty_content += 1
                continue
            if not based:
                rejected_without_visible_basis += 1
                continue  # 硬约束:推断必须挂依据事实
            confidence, confidence_reason = _confidence_from_facts(based)
            inference = Inference(
                content=content,
                source_level="MATERIAL_INFERENCE",
                based_fact_ids=based,
                reasoning_chain=reasoning,
                dimension=dimension,
                analysis_type=analysis_type,
                confidence_level=confidence,
                confidence_reason=confidence_reason,
                uncertainty="",
                semantic_profile=semantic_profile,
                source_semantics=source_semantics_for_facts(based, facts),
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
        audit = {
            "candidate_inference_count": candidate_count,
            "accepted_inference_count": len(inferences),
            "rejected_empty_content_count": rejected_empty_content,
            "rejected_without_visible_basis_count": rejected_without_visible_basis,
            "invalid_fact_reference_ids": sorted(invalid_fact_ids),
            "external_information_count": len(external),
        }
        return inferences, external, audit


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
                semantic_profile_json=semantic_profile_json(inference.semantic_profile),
                source_semantics_json=json.dumps(inference.source_semantics, ensure_ascii=False),
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
                continue  # Ignore model-produced fact ids outside the current task.
        return inference_id

__all__ = ['AnalysisAgent', 'save_inference']
