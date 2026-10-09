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

_SYSTEM = """你是情报分析员。基于事实清单、来源冲突与历史知识做综合分析,禁止无依据结论。
严格输出 JSON,不要任何解释:
{
  "inferences": [
    {"content": "综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "所属维度", "analysis_type": "开放类型标签",
     "semantic_profile": {"facets": [{"axis": "命题类型", "label": "开放类别", "detail": "判定依据"}, {"axis": "情态/言语行为", "label": "开放类别", "detail": ""}, {"axis": "时间范围", "label": "开放类别", "detail": "时间锚点"}, {"axis": "实现状态", "label": "开放类别", "detail": ""}, {"axis": "知识依据", "label": "开放类别", "detail": ""}, {"axis": "主体/范围/量词", "label": "开放类别", "detail": ""}, {"axis": "条件/因果/关系", "label": "开放类别", "detail": "仅相关时填写"}, {"axis": "极性/否定/例外", "label": "开放类别", "detail": "仅相关时填写"}], "attribution": "来源归属", "conditions": [], "scope_qualifiers": [], "additional": {}}}
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
6. 不要判断或输出置信度与不确定性字段;系统将根据有效 based_fact_ids 确定性计算
7. 不要输出长篇推理过程;short_rationale 只写必要依据链
8. 不得扩大事实适用范围;保留依据中的主体、对象、版本、时间和条件限定,综合结论范围不得超出依据共同支持的范围
9. assertion_mode/assertion_mode_raw 仅是粗粒度旧协议投影；完整分类和细类以每条事实的开放 semantic_profile 及其来源原文为准
10. 目标、要求、计划或假设等语义不能单独证明已经实施、发生或达到效果；不得把来源报告等同系统独立核实；观点、转述和估计须保留归属，状态/来源不同须分别表述
11. 来源陈述性质不同时,必须区分现状与目标,没有明确完成证据不得推断发生了状态转变
12. 必须结合单条事实、原文引文及章节上下文判断,不得按领域关键词、文件名、标题词或材料整体角色作硬编码推断;材料级摘要标签不能替代单条事实证据
13. 当推论依赖材料的陈述性质或状态边界时,short_rationale 必须简要说明该边界,content 使用与依据匹配的语气
14. semantic_profile 是开放的多维分类，不使用封闭标签表。逐条检查所有影响推论真值、适用性或可比性的面向，按内容标注命题类型、情态、时间、实现状态、认识依据、归属、主体/范围/量词、条件/前提、因果/关系、极性/否定、例外/限制及其他相关细类；示例不构成穷举或必填清单，不相关不填，不得因类别未列出而归并或遗漏。source_semantics 是程序从引用事实继承的逐条语义画像，不得省略、合并成更强的状态或把计划/目标转成已实现。
"""


_GLOBAL_SYSTEM = """你是情报报告综合研判师。基于各维度局部推断与关键事实,形成跨维度综合判断。
严格输出 JSON,不要任何解释:
{
  "inferences": [
    {"content": "跨维度综合判断", "based_fact_ids": [1, 2], "short_rationale": "60字以内依据说明",
     "dimension": "全局综合", "analysis_type": "开放类型标签",
     "semantic_profile": {"facets": [{"axis": "命题类型", "label": "开放类别", "detail": "判定依据"}, {"axis": "情态/言语行为", "label": "开放类别", "detail": ""}, {"axis": "时间范围", "label": "开放类别", "detail": "时间锚点"}, {"axis": "实现状态", "label": "开放类别", "detail": ""}, {"axis": "知识依据", "label": "开放类别", "detail": ""}, {"axis": "主体/范围/量词", "label": "开放类别", "detail": ""}, {"axis": "条件/因果/关系", "label": "开放类别", "detail": "仅相关时填写"}, {"axis": "极性/否定/例外", "label": "开放类别", "detail": "仅相关时填写"}], "attribution": "来源归属", "conditions": [], "scope_qualifiers": [], "additional": {}}}
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
7. 不要为单条推论输出置信度与不确定性字段;系统将按有效依据事实数量计算置信度
8. 不得扩大局部推断或事实的适用范围;保留依据中的主体、对象、版本、时间和条件限定
9. 继承局部推论及所引事实的完整开放语义画像；不得把不同来源、不同时间、条件或范围的状态合并为更强结论，材料报告也不等于系统独立核实
10. 只能依据逐条事实、对应原文和局部推论判断状态;不得依赖领域关键词、文件名、标题词或材料级汇总标签做硬编码分类
11. semantic_profile 使用开放、多标签分类，不限于示例；逐条保留所有影响结论真值和适用性的维度，包括时间、条件、归属、范围、量化口径、极性、例外、因果关系及领域特有细类，不相关时不强行填写。不得将不同来源的语义状态合并为更强结论。
"""


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
        dimension = str(get("dimension") or "未分类")
        content = str(get("content") or "")
        analysis_type = str(get("analysis_type") or "SYNTHESIS")
        based_fact_ids = get("based_fact_ids") or []
        record = f"- [{dimension}] {content} (type={analysis_type}, based_fact_ids={based_fact_ids})"
        if record in lines:
            visible_ids.add(int(inference_id))
            visible_fact_ids.update(_int_ids(based_fact_ids))
    return visible_ids, visible_fact_ids


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
    # This is an output-capacity estimate, not a domain rule. A semantic
    # batch may need more than the historical four-result suggestion, but the
    # target remains bounded by the configured Analysis output budget.
    output_capacity = max(4, stage_profile("analysis").output_tokens // max(1, int(settings.analysis_inference_token_budget)))
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
        inference_lines.extend(
            f"- [{i.dimension or '未分类'}] {i.content} "
            f"(type={i.analysis_type or 'SYNTHESIS'}, based_fact_ids={i.based_fact_ids}, "
            f"semantic_profile={semantic_profile_line(i.semantic_profile)}, "
            f"source_semantics={source_semantics_line(i.source_semantics)})"
            for i in local_inferences
        )
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
        visible_ids = visible_fact_ids & task_fact_ids
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
