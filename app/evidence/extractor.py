"""Evidence Agent:材料 → Claim(陈述)→ Fact(确认信息)提取、来源绑定、冲突发现。

数据链路:Material → Document Unit → Evidence → Claim → Fact。
Claim 是材料中被提取的陈述(可能是原文观点,不一定是事实);quote 校验通过的
Claim 提升(promote)为 Fact 并绑定 Evidence;未通过的保留为 pending Claim。
冲突检测在 Claim 层进行:只标注矛盾,不替用户裁定。
硬约束:无原文支撑的陈述不提升为 Fact(宁可少输出)。
"""
import json
import re


from app.agents.base import BaseAgent
from app.db import session_scope
from app.infrastructure.orm import ORMFact, ORMEvidence, ORMClaim
from sqlalchemy import select, update
from app.models import Claim, Conflict, Evidence, Fact, Unit
from app.retrieval import vector_store
from app.retrieval.embedder import embed_texts
from app.token_monitor import log_pipeline_event, update_call_funnel, update_call_metrics, update_call_products

_FACT_TYPES = ("EVENT", "PERSON", "LOCATION", "TIME", "NUMBER", "STATEMENT")

_SYSTEM = """你是情报事实提取员。从材料中提取可溯源的陈述(Claim),严格区分事实与推断。
严格输出 JSON,不要任何解释:
{"claims": [{"content": "陈述内容", "unit_id": 材料单元编号, "short_quote": "30字以内原文短摘", "fact_type": "EVENT/PERSON/LOCATION/TIME/NUMBER/STATEMENT"}, ...]}

要求:
1. unit_id 必须来自材料文本标注的 U编号;short_quote 必须逐字摘自该单元,不得改写
2. content 是对 quote 的忠实表述(允许同义改述)
3. fact_type 按陈述性质:事件EVENT/人物PERSON/地点LOCATION/时间TIME/数字NUMBER/一般陈述STATEMENT
4. 只输出有原文支撑的陈述;没有支撑就少输出
5. 禁止输出推断、评价、总结(那些属于"分析人士认为"类观点时,content 须注明是来源观点)
6. 分析维度和优先事实类别只是检索/提取重点,不是排除边界;材料中与用户目标明显相关的高价值事实即使不在维度内也应提取
7. 面向综述或多材料任务时,同一维度应覆盖主要机制、标准、平台、时间线、威胁模型和比较要素;不要只抽取少数摘要性陈述
8. 不要输出 file/page/完整原文,系统会根据 unit_id 回查来源"""

_CONFLICT_SYSTEM = """你是情报冲突检测员。扫描陈述(Claim)清单,识别同一主题在不同来源中的矛盾
(如:同一数字口径不同、同一事件描述相反)。只标注冲突,不判断谁对谁错。
严格输出 JSON,不要任何解释:
{"conflicts": [{"fact_key": "矛盾主题", "claim_ids": [涉及陈述的编号], "entries": [{"file": "来源文件", "quote": "原文片段", "statement": "该来源的说法"}, ...]}, ...]}
没有冲突时输出 {"conflicts": []}"""

_AUDIT_SYSTEM = """你是证据覆盖审计员。判断每个 Evidence Need 是否已被列出的已提取事实充分证实。
判定标准:事实能直接回答该需求(明确、具体、有支撑);若事实缺失、仅侧面提及、过于概括或无法回答该需求,视为未证实。
严格输出 JSON,不要任何解释:
{"uncovered": [未充分证实的 need 编号数组]}
若全部充分,输出 {"uncovered": []}"""

_WHITESPACE = re.compile(r"\s+")
_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
    "0123456789abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)


def _normalize(text: str) -> str:
    """归一化:去空白 + 全角字母数字转半角,用于 quote 与原文的匹配。"""
    return _WHITESPACE.sub("", (text or "").translate(_FULLWIDTH))


def _split_quotes(quote: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])", quote)
    return [p.strip() for p in parts if p.strip()]


def bind_sources(quote: str, units_by_material: dict[int, list[Unit]], filenames: dict[int, str],
                 unit_id: int | None = None) -> list[Evidence]:
    """把 short_quote 绑定到来源单元。优先使用 unit_id,失败再回退全文匹配。"""
    if unit_id:
        for material_id, units in units_by_material.items():
            for unit in units:
                if unit.id == unit_id:
                    if _normalize(quote) not in _normalize(unit.content):
                        return []  # unit_id 不能替代 quote 校验,短摘必须真实存在
                    return [Evidence(
                        fact_id=0, material_id=material_id, unit_id=unit.id or 0,
                        source_file=filenames.get(material_id, ""), page=unit.page,
                        paragraph=unit.paragraph, quote=quote,
                    )]
    norm_quote = _normalize(quote)
    evidence_list: list[Evidence] = []

    def find_unit(norm_fragment: str) -> tuple[int, Unit] | None:
        for material_id, units in units_by_material.items():
            for unit in units:
                if norm_fragment and norm_fragment in _normalize(unit.content):
                    return material_id, unit
        return None

    if norm_quote:
        hit = find_unit(norm_quote)
        if hit:
            material_id, unit = hit
            evidence_list.append(Evidence(
                fact_id=0, material_id=material_id, unit_id=unit.id or 0,
                source_file=filenames.get(material_id, ""), page=unit.page,
                paragraph=unit.paragraph, quote=quote,
            ))
            return evidence_list
    # 完整 quote 无法匹配 → 按分句绑定(支持跨文档拼接)
    for fragment in _split_quotes(quote):
        hit = find_unit(_normalize(fragment))
        if hit:
            material_id, unit = hit
            evidence_list.append(Evidence(
                fact_id=0, material_id=material_id, unit_id=unit.id or 0,
                source_file=filenames.get(material_id, ""), page=unit.page,
                paragraph=unit.paragraph, quote=fragment,
            ))
    return evidence_list


def _conflict_candidate_claims(claims: list[dict], limit: int = 40) -> list[dict]:
    """规则预筛:只保留疑似同主题/同数字的候选 Claim,降低冲突检测上下文。"""
    digit_re = re.compile(r"\d+(?:\.\d+)?")
    candidate_ids: set[int] = set()
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a = claims[i].get("content", "")
            b = claims[j].get("content", "")
            if not a or not b:
                continue
            if set(digit_re.findall(a)) & set(digit_re.findall(b)):
                candidate_ids.update([claims[i].get("id"), claims[j].get("id")])
                continue
            grams_a = {a[k:k + 3] for k in range(len(a) - 2)}
            grams_b = {b[k:k + 3] for k in range(len(b) - 2)}
            if grams_a & grams_b:
                candidate_ids.update([claims[i].get("id"), claims[j].get("id")])
    result = [c for c in claims if c.get("id") in candidate_ids]
    return result[:limit]


_CONFLICT_WORD_GROUPS = (
    ("必须", "应当", "需", "不得", "禁止", "不允许", "可", "可以", "允许", "自愿", "选交"),
    ("已完成", "完成", "未完成", "尚未", "没有", "无"),
    ("有", "无", "包含", "不包含"),
)

_OPEN_DISCOVERY_DIMENSION = "开放发现:补充提取与用户目标相关、但未被前述维度覆盖的高价值事实"


def _normalize_dimensions(dimensions: list[str]) -> list[str]:
    """Keep planner dimensions as priorities while adding one open discovery pass."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in dimensions or []:
        text = str(item).strip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    if not cleaned:
        cleaned.append("核心事实发现")
    if not any("开放发现" in item for item in cleaned):
        cleaned.append(_OPEN_DISCOVERY_DIMENSION)
    return cleaned


def _normalize_needs(raw) -> list[dict]:
    """归一化 Evidence Needs:兼容 dict/str 输入,去重,确保 dimension/priority 字段。"""
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        if isinstance(item, str):
            entry = {"need": item.strip(), "dimension": "核心事实发现", "priority": "medium"}
        elif isinstance(item, dict):
            text = str(item.get("need") or item.get("question") or "").strip()
            if not text:
                continue
            entry = {
                "need": text,
                "dimension": str(item.get("dimension") or "开放发现").strip(),
                "priority": str(item.get("priority") or "medium").strip(),
            }
        else:
            continue
        if not entry["need"] or entry["need"] in seen:
            continue
        seen.add(entry["need"])
        cleaned.append(entry)
    if not cleaned:
        cleaned.append({"need": "核心事实发现", "dimension": "开放发现", "priority": "high"})
    return cleaned


def _claim_field_chars(items: list[dict]) -> dict[str, int]:
    fields = {"content": 0, "short_quote": 0, "unit_id": 0, "fact_type": 0, "other": 0}
    for item in items:
        for key, value in item.items():
            text = str(value if value is not None else "")
            if key in ("content",):
                fields["content"] += len(text)
            elif key in ("short_quote", "quote"):
                fields["short_quote"] += len(text)
            elif key == "unit_id":
                fields["unit_id"] += len(text)
            elif key == "fact_type":
                fields["fact_type"] += len(text)
            else:
                fields["other"] += len(str(key)) + len(text)
    return fields


def _conflict_candidate_groups(claims: list[dict], limit: int = 12) -> list[dict]:
    """Build high-recall conflict groups before LLM.

    We keep this deliberately permissive: numbers/dates/status oppositions and
    mandatory/optional language are strong signals, while shared text alone is
    only used as a topic anchor.
    """
    groups: dict[str, dict] = {}
    digit_re = re.compile(r"\d+(?:\.\d+)?")
    date_re = re.compile(r"(?:\d{1,2}月\d{1,2}日|\d{4}年|\d{1,2}日前)")

    def topic_key(text: str) -> str:
        tokens = re.findall(r"[\u4e00-\u9fa5]{2,}", text or "")
        stop = {"材料", "要求", "相关", "进行", "提交", "工作", "说明", "报告"}
        useful = [t[:6] for t in tokens if t not in stop]
        return " ".join(useful[:3]) or (text or "")[:12]

    def add_group(kind: str, a: dict, b: dict, signal: str) -> None:
        ids = {int(v) for v in (a.get("id"), b.get("id")) if str(v).isdigit()}
        if len(ids) < 2:
            return
        key = f"{kind}:{topic_key(a.get('content', ''))}:{topic_key(b.get('content', ''))}"[:120]
        group = groups.setdefault(key, {"kind": kind, "signal": signal, "claim_ids": set()})
        group["claim_ids"].update(ids)

    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a = claims[i]
            b = claims[j]
            ta = str(a.get("content", ""))
            tb = str(b.get("content", ""))
            if not ta or not tb:
                continue
            shared = set(ta[k:k + 3] for k in range(max(0, len(ta) - 2))) & set(
                tb[k:k + 3] for k in range(max(0, len(tb) - 2))
            )
            nums_a, nums_b = set(digit_re.findall(ta)), set(digit_re.findall(tb))
            dates_a, dates_b = set(date_re.findall(ta)), set(date_re.findall(tb))
            if shared and nums_a and nums_b and nums_a != nums_b:
                add_group("number_mismatch", a, b, "同主题数字/数量不一致")
                continue
            if shared and dates_a and dates_b and dates_a != dates_b:
                add_group("date_mismatch", a, b, "同主题时间节点不一致")
                continue
            for word_group in _CONFLICT_WORD_GROUPS:
                hits_a = {w for w in word_group if w in ta}
                hits_b = {w for w in word_group if w in tb}
                if shared and hits_a and hits_b and hits_a != hits_b:
                    add_group("status_or_modality", a, b, "同主题状态/义务表达可能相反")
                    break
    result = []
    for group in groups.values():
        claim_ids = sorted(group.pop("claim_ids"))
        result.append({**group, "claim_ids": claim_ids})
    return result[:limit]


class EvidenceAgent(BaseAgent):
    name = "evidence"
    role = _SYSTEM

    def extract_facts(
        self,
        needs: list[dict],
        units_by_material: dict[int, list[Unit]],
        filenames: dict[int, str],
        cm=None,
        insights: list[dict] | None = None,
        required_facts: list[str] | None = None,
        task_id: str = "",
        progress_callback=None,
        start_dimension: int = 0,
    ) -> list[Fact]:
        """主线(论文思想整合):First-pass 合并读取 → Coverage Audit → Iterative Retrieval。

        1. First-pass:全部 Evidence Needs 一次打分选候选(材料全覆盖),
           每批材料只读取一次,模型按 need 标注输出(多维联合提取);
        2. Coverage Audit:每个 Need 检查是否被事实真正支持(LLM 判断,非硬编码阈值);
        3. Iterative Retrieval:缺口 Needs 针对性补检(缺什么补什么,足够即停)。
        """
        needs = _normalize_needs(needs)
        facts: list[Fact] = []
        total_phases = 3

        def report_phase(phase: int) -> None:
            if progress_callback is not None:
                progress_callback(phase, total_phases)

        report_phase(1)
        if cm is not None and hasattr(cm, "for_first_pass"):
            batches = cm.for_first_pass(needs, insights)
            for batch_index, (material_text, batch_meta) in enumerate(batches, start=1):
                facts.extend(self._process_batch(
                    needs, material_text, batch_meta, units_by_material, filenames,
                    task_id, batch_index, len(batches),
                ))
        else:  # 无 Context Manager:全量文本直接处理
            material_text = self._build_material_text(units_by_material, filenames)
            facts.extend(self._process_batch(
                needs, material_text, {"pass": "full_material"}, units_by_material,
                filenames, task_id, 1, 1,
            ))
        report_phase(2)

        # Coverage Audit + Iterative Retrieval:缺口补检,足够即停
        gaps = self._coverage_audit(facts, needs, task_id)
        # FIRE 式迭代:缺什么补什么,足够即停。
        # 停止条件:①无缺口 ②本轮未产出新事实(材料已无新证据)→ information_gap 接受
        # 安全 cap = 资源保护(工程限制,非业务规则),防止异常死循环
        round_index = 0
        while gaps and round_index < 3:
            round_index += 1
            before_count = len(facts)
            known_contents = {f.content for f in facts if f.content}
            if cm is not None and hasattr(cm, "for_gap_retrieval"):
                gap_batches = cm.for_gap_retrieval(gaps, insights, known_contents=known_contents)
            else:
                gap_batches = []
            for batch_index, (material_text, batch_meta) in enumerate(gap_batches, start=1):
                facts.extend(self._process_batch(
                    gaps, material_text, batch_meta, units_by_material, filenames,
                    task_id, batch_index, len(gap_batches), round_tag=round_index,
                ))
            if len(facts) == before_count:
                break  # 无新证据:接受 information_gap,不再空转
            gaps = self._coverage_audit(facts, needs, task_id)
        report_phase(3)
        return facts

    def _process_batch(self, needs: list[dict], material_text: str, batch_meta: dict,
                       units_by_material: dict[int, list[Unit]], filenames: dict[int, str],
                       task_id: str, batch_index: int, batch_count: int,
                       round_tag: int = 0) -> list[Fact]:
        """单批处理:多维联合提取(全部 Needs)→ claims 解析 → quote 校验 → Fact 落库。

        输出格式:claims 每条带 need_id/need(模型标注归属),无标注则按相关性回退到首个 need。
        """
        needs_list = needs if isinstance(needs, list) else [needs]
        need_block = self._build_need_block(needs_list)
        insight_block = self._build_insight_block([])
        prompt = (
            f"{need_block}\n"
            f"{insight_block}\n"
            f"材料文本(按来源标注):\n{material_text}\n\n"
            f"请提取与上述任一 Evidence Need 相关的陈述;每条必须标注属于哪个 need_id。"
            f"若发现与用户目标明显相关但不属于任何 need 的高价值事实,也提取并标注 need_id=0。"
            f"如果材料覆盖多个平台/标准/攻击/机制,请分别抽取,不要合并成过度概括的一条。输出 JSON。"
        )
        prompt_parts = {
            "needs": sum(len(n.get("need", "")) for n in needs_list),
            "material_context": len(material_text or ""),
            "system_prompt": len(self.role or _SYSTEM),
        }
        context_meta = dict(batch_meta)
        try:
            payload = self.generate_json(prompt)
        except Exception:
            return []
        call_id = self.last_call_id
        produced_fact_ids: list[int] = []
        stored_chars = 0
        items = payload.get("claims") or payload.get("facts") or []
        model_claims = len(items)
        valid_field_claims = 0
        quote_bound_claims = 0
        duplicate_claims = 0
        pending_claims = 0
        field_chars = _claim_field_chars(items)
        contributed_materials: set[int] = set()
        contributed_units: set[int] = set()
        facts: list[Fact] = []
        material_scan: dict[int, dict] = {}
        for item in items:
            content = str(item.get("content", "")).strip()
            quote = str(item.get("short_quote") or item.get("quote") or "").strip()
            try:
                unit_id = int(item.get("unit_id")) if item.get("unit_id") is not None else None
            except (TypeError, ValueError):
                unit_id = None
            if not content or not quote:
                continue
            valid_field_claims += 1
            fact_type = str(item.get("fact_type", "STATEMENT")).upper()
            if fact_type not in _FACT_TYPES:
                fact_type = "STATEMENT"
            # 维度归属:模型标注 need_id(0=开放发现),无标注回退首个 need 的维度
            try:
                need_idx = int(item.get("need_id", 0))
            except (TypeError, ValueError):
                need_idx = 0
            if not (0 <= need_idx < len(needs_list) + 1):
                need_idx = 0
            dimension = needs_list[need_idx - 1].get("dimension", "开放发现") if need_idx > 0 else "开放发现"
            if fact_exists(content, task_id):
                duplicate_claims += 1
                continue
            evidence_list = bind_sources(quote, units_by_material, filenames, unit_id=unit_id)
            claim = Claim(
                material_id=evidence_list[0].material_id if evidence_list else 0,
                content=content, quote=quote,
                source=filenames.get(evidence_list[0].material_id, "") if evidence_list else "",
                fact_type=fact_type, dimension=dimension, need_id=need_idx,
            )
            if not evidence_list:
                pending_claims += 1
                save_claim(claim, status="pending", origin_call_id=call_id, task_id=task_id)
                continue
            quote_bound_claims += 1
            contributed_materials.update(int(ev.material_id) for ev in evidence_list)
            contributed_units.update(int(ev.unit_id) for ev in evidence_list)
            fact = Fact(content=content, dimension=dimension, fact_type=fact_type, need_id=need_idx)
            fact.id = save_fact_with_evidence(fact, evidence_list, task_id, origin_call_id=call_id)
            facts.append(fact)
            produced_fact_ids.append(int(fact.id))
            stored_chars += len(fact.content or "") + sum(len(ev.quote or "") for ev in evidence_list)
            claim.fact_id = fact.id
            save_claim(claim, status="promoted", origin_call_id=call_id, task_id=task_id)
            for ev in evidence_list:
                scan = material_scan.setdefault(int(ev.material_id), {"scanned": True, "units_selected": 0, "fact_count": 0})
                scan["fact_count"] += 1
        for mid, unit_count in (batch_meta.get("batch_material_units") or {}).items():
            scan = material_scan.setdefault(int(mid), {"scanned": True, "units_selected": 0, "fact_count": 0})
            scan["units_selected"] = int(unit_count)
        update_call_products(call_id, produced_fact_ids=produced_fact_ids)
        update_call_metrics(call_id, stored_chars=stored_chars)
        update_call_funnel(
            call_id,
            dimension="first_pass" if not round_tag else f"iterative_r{round_tag}",
            context_meta=context_meta,
            prompt_parts=prompt_parts,
            prompt_chars=len(prompt),
            material_context_chars=len(material_text),
            contributed_material_count=len(contributed_materials),
            contributed_unit_count=len(contributed_units),
            contributed_material_ids=sorted(contributed_materials),
            contributed_unit_ids=sorted(contributed_units)[:50],
            model_claims=model_claims,
            valid_field_claims=valid_field_claims,
            quote_bound_claims=quote_bound_claims,
            duplicate_claims=duplicate_claims,
            pending_claims=pending_claims,
            promoted_facts=len(produced_fact_ids),
            field_chars=field_chars,
            batch=batch_index,
            batch_count=batch_count,
        )
        for dimension_key, scan in material_scan.items():
            self._record_material_scan(task_id, f"need_pass_{round_tag or 0}", {int(dimension_key): scan})
        # 本批 Fact 向量持久化(失败回退关键词检索)
        if facts:
            try:
                vectors = embed_texts([f.content for f in facts])
                for fact, vector in zip(facts, vectors):
                    vector_store.save_fact_vector(fact.id, vector, task_id=task_id)
            except Exception:
                pass
        return facts

    def _build_need_block(self, needs: list[dict]) -> str:
        """Evidence Needs 清单(合并读取的多维提取引导)。"""
        lines = ["Evidence Needs(待证实信息需求):"]
        for index, need in enumerate(needs, start=1):
            lines.append(f"{index}. [{need.get('priority', 'medium')}] {need.get('need', '')}")
        return "\n".join(lines)

    def _coverage_audit(self, facts: list[Fact], needs: list[dict], task_id: str) -> list[dict]:
        """Coverage Audit:每个 Need 是否被事实真正支持(LLM 语义判断,无硬编码阈值)。

        返回未被充分证实的 Needs(缺口),供 Iterative Retrieval 补检。
        """
        if not needs:
            return []
        need_facts: dict[int, list[str]] = {}
        for need_index, need in enumerate(needs, start=1):
            # 真实关系匹配:fact.need_id 由模型标注(非 dimension 猜测)
            matched = [f.content for f in facts if int(getattr(f, "need_id", 0) or 0) == need_index]
            # 无标注的开放发现(need_id=0)不自动归属任何 need(避免误判支持)
            need_facts[need_index] = matched
        need_lines = []
        for index, need in enumerate(needs, start=1):
            facts_text = "\n".join(f"  - {c}" for c in need_facts[index]) or "  (无)"
            need_lines.append(f"{index}. {need.get('need', '')}\n已提取事实:\n{facts_text}")
        prompt = (
            "以下每个 Evidence Need 后列出了当前已提取的相关事实。"
            "请判断每个 Need 是否已被充分证实(事实能直接回答该需求;若事实缺失、仅侧面提及或无法回答,视为未证实)。\n\n"
            + "\n\n".join(need_lines) +
            "\n\n严格输出 JSON:\n{\"uncovered\": [未充分证实的 need 编号数组]}\n"
            "若全部充分,输出 {\"uncovered\": []}"
        )
        try:
            payload = self.generate_json(prompt, system=_AUDIT_SYSTEM)
            uncovered = payload.get("uncovered") or []
            return [needs[int(i) - 1] for i in uncovered if str(i).isdigit() and 0 < int(i) <= len(needs)]
        except Exception:
            return []

    def _record_material_scan(self, task_id: str, dimension: str, material_scan: dict) -> None:
        """材料扫描状态落独立表(可观测性,避免大 payload 反复写任务)。"""
        try:
            from app.infrastructure.orm import ORMMaterialScan
            from sqlalchemy import select as _select
            with session_scope() as s:
                for mid, info in material_scan.items():
                    values = dict(
                        task_id=task_id, material_id=int(mid), dimension=dimension,
                        scanned=int(info.get("scanned", 1)),
                        units_selected=int(info.get("units_selected", 0)),
                        fact_count=int(info.get("fact_count", 0)),
                    )
                    exists = s.execute(
                        _select(ORMMaterialScan.c.task_id).where(
                            ORMMaterialScan.c.task_id == task_id,
                            ORMMaterialScan.c.material_id == int(mid),
                            ORMMaterialScan.c.dimension == dimension,
                        )
                    ).first()
                    if exists:
                        s.execute(
                            ORMMaterialScan.update().where(
                                ORMMaterialScan.c.task_id == task_id,
                                ORMMaterialScan.c.material_id == int(mid),
                                ORMMaterialScan.c.dimension == dimension,
                            ).values(scanned=values["scanned"], units_selected=values["units_selected"],
                                     fact_count=values["fact_count"])
                        )
                    else:
                        s.execute(ORMMaterialScan.insert().values(**values))
        except Exception:
            pass

    def detect_conflicts(self, claims: list[dict], task_id: str = "") -> list[Conflict]:
        """在 Claim 层检测多源矛盾:只标注,不裁定。

        规则预筛:仅当存在候选冲突组(共享数字/共享文本片段)才调用 LLM,
        避免无条件消耗模型调用。
        """
        candidate_claims = _conflict_candidate_claims(claims)
        if len(candidate_claims) < 2:
            log_pipeline_event(
                "evidence",
                candidate_total=len(claims),
                candidate_count=len(candidate_claims),
                candidate_groups=0,
                llm_skipped_calls=1,
                skip_reason="not_enough_candidates",
            )
            return []
        candidate_groups = _conflict_candidate_groups(candidate_claims)
        if not candidate_groups:
            log_pipeline_event(
                "evidence",
                candidate_total=len(claims),
                candidate_count=len(candidate_claims),
                candidate_groups=0,
                llm_skipped_calls=1,
                skip_reason="no_conflict_signal_groups",
            )
            return []
        candidate_ids = {cid for group in candidate_groups for cid in group.get("claim_ids", [])}
        candidate_claims = [c for c in candidate_claims if int(c.get("id") or 0) in candidate_ids]
        claim_lines = [
            f"{c['id']}. [{c.get('source', '')}|{c.get('fact_type', 'STATEMENT')}] {c['content']}"
            for c in candidate_claims
        ]
        group_lines = [
            f"- {g['kind']}({g['signal']}): " + ",".join(str(i) for i in g.get("claim_ids", []))
            for g in candidate_groups
        ]
        prompt = (
            "以下是材料中提取的陈述清单(编号 + 来源 + 类型):\n"
            + "\n".join(claim_lines)
            + "\n\n规则预筛得到的疑似冲突组:\n"
            + "\n".join(group_lines)
            + "\n\n请检测同一主题在不同来源中的矛盾,输出 JSON。"
        )
        try:
            payload = self.generate_json(prompt, system=_CONFLICT_SYSTEM)
        except Exception:
            return []
        call_id = self.last_call_id
        update_call_metrics(call_id, candidate_total=len(claims), candidate_count=len(candidate_claims))
        update_call_funnel(
            call_id,
            candidate_total=len(claims),
            candidate_count=len(candidate_claims),
            candidate_groups=len(candidate_groups),
            quality_guardrail_calls=1,
        )
        conflicts: list[Conflict] = []
        for item in payload.get("conflicts", []):
            entries = []
            for entry in item.get("entries", []):
                entries.append({
                    "file": str(entry.get("file", "")),
                    "quote": str(entry.get("quote", "")),
                    "statement": str(entry.get("statement", "")),
                })
            conflict = Conflict(
                fact_key=str(item.get("fact_key", "")),
                entries=entries,
                claim_ids=[int(i) for i in item.get("claim_ids", []) if str(i).isdigit()],
            )
            conflict.id = save_conflict(conflict, origin_call_id=call_id, task_id=task_id)
            conflicts.append(conflict)
        update_call_metrics(
            call_id,
            stored_chars=sum(
                len(c.fact_key or "") + sum(len(e.get("statement", "")) + len(e.get("quote", "")) for e in c.entries)
                for c in conflicts
            ),
        )
        update_call_funnel(call_id, conflict_count=len(conflicts))
        return conflicts

    @staticmethod
    def _build_insight_block(insights: list[dict] | None) -> str:
        """材料理解摘要:类型/主题/价值排序,引导提取聚焦高价值材料。"""
        if not insights:
            return ""
        lines = ["材料理解(按价值排序):"]
        for insight in insights:
            lines.append(
                f"- [{insight.get('value_rank', '?')}级] {insight.get('doc_type', '')}: "
                f"{insight.get('topic', '')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_material_text(units_by_material: dict[int, list[Unit]], filenames: dict[int, str]) -> str:
        from app.config import settings

        blocks: list[str] = []
        total = 0
        budget = max(1000, int(settings.max_context_chars or 12000))
        omitted = 0
        for material_id, units in units_by_material.items():
            for unit in units:
                if unit.kind == "image" and not unit.content:
                    continue
                label = f"[U{unit.id} {filenames.get(material_id, '?')}"
                if unit.page is not None:
                    label += f" 第{unit.page}页"
                label += "]"
                block = f"{label}\n{unit.content}"
                if total + len(block) > budget:
                    omitted += 1
                    continue
                blocks.append(block)
                total += len(block)
        if omitted:
            blocks.append(
                f"[上下文预算提示] 另有 {omitted} 个材料单元未进入本次 LLM prompt;"
                "原始 Unit 已完整入库,后续可通过检索再次召回。"
            )
        return "\n\n".join(blocks)


def fact_exists(content: str, task_id: str = "") -> bool:
    with session_scope() as s:
        query = select(ORMFact.c.id).where(ORMFact.c.content == content)
        if task_id:
            query = query.where(ORMFact.c.task_id == task_id)
        return s.execute(query).first() is not None


def save_fact_with_evidence(fact: Fact, evidence_list: list[Evidence], task_id: str = "",
                            origin_call_id: str = "") -> int:
    with session_scope() as s:
        result = s.execute(
            ORMFact.insert().values(
                content=fact.content, dimension=fact.dimension, source_level=fact.source_level,
                fact_type=fact.fact_type, need_id=fact.need_id,
                evidence_ids="[]", conflict_ids="[]", task_id=task_id, origin_call_id=origin_call_id,
            )
        )
        fact.id = int(result.inserted_primary_key[0])
        evidence_ids = []
        for ev in evidence_list:
            ev.fact_id = fact.id
            r2 = s.execute(
                ORMEvidence.insert().values(
                    fact_id=ev.fact_id, material_id=ev.material_id, unit_id=ev.unit_id,
                    source_file=ev.source_file, page=ev.page, paragraph=ev.paragraph, quote=ev.quote,
                )
            )
            evidence_ids.append(int(r2.inserted_primary_key[0]))
        s.execute(update(ORMFact).where(ORMFact.c.id == fact.id).values(evidence_ids=json.dumps(evidence_ids)))
    return fact.id


def save_claim(claim: Claim, status: str, origin_call_id: str = "", task_id: str = "") -> int:
    with session_scope() as s:
        result = s.execute(
            ORMClaim.insert().values(
                fact_id=claim.fact_id or 0, material_id=claim.material_id,
                content=claim.content, quote=claim.quote, source=claim.source,
                fact_type=claim.fact_type, dimension=claim.dimension, need_id=claim.need_id,
                status=status, task_id=task_id, origin_call_id=origin_call_id,
            )
        )
        return int(result.inserted_primary_key[0])


def load_claims(task_id: str = "") -> list[dict]:
    """当前任务的全部陈述(含未提升的 pending)。"""
    with session_scope() as s:
        query = select(ORMClaim)
        if task_id:
            query = query.where(ORMClaim.c.task_id == task_id)
        rows = s.execute(query.order_by(ORMClaim.c.id)).mappings().all()
    return [dict(row) for row in rows]


def save_conflict(conflict: Conflict, origin_call_id: str = "", task_id: str = "") -> int:
    from app.infrastructure.orm import ORMConflict
    with session_scope() as s:
        result = s.execute(
            ORMConflict.insert().values(
                fact_key=conflict.fact_key,
                entries=json.dumps(conflict.entries, ensure_ascii=False),
                claim_ids=json.dumps(conflict.claim_ids),
                status=conflict.status, task_id=task_id, origin_call_id=origin_call_id,
            )
        )
        return int(result.inserted_primary_key[0])


def load_evidence_quotes(fact_id: int) -> str:
    with session_scope() as s:
        rows = s.execute(
            select(ORMEvidence.c.source_file).where(ORMEvidence.c.fact_id == fact_id)
        ).all()
    return ", ".join(str(row[0]) for row in rows)

__all__ = ['bind_sources', 'EvidenceAgent', 'fact_exists', 'save_fact_with_evidence', 'save_claim', 'load_claims', 'save_conflict', 'load_evidence_quotes']
