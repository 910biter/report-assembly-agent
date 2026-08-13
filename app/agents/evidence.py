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
from app.db import connect
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
6. 不要输出 file/page/完整原文,系统会根据 unit_id 回查来源"""

_CONFLICT_SYSTEM = """你是情报冲突检测员。扫描陈述(Claim)清单,识别同一主题在不同来源中的矛盾
(如:同一数字口径不同、同一事件描述相反)。只标注冲突,不判断谁对谁错。
严格输出 JSON,不要任何解释:
{"conflicts": [{"fact_key": "矛盾主题", "claim_ids": [涉及陈述的编号], "entries": [{"file": "来源文件", "quote": "原文片段", "statement": "该来源的说法"}, ...]}, ...]}
没有冲突时输出 {"conflicts": []}"""

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
        dimensions: list[str],
        units_by_material: dict[int, list[Unit]],
        filenames: dict[int, str],
        cm=None,
        insights: list[dict] | None = None,
        required_facts: list[str] | None = None,
        task_id: str = "",
        progress_callback=None,
    ) -> list[Fact]:
        """按维度提取陈述(Claim) → quote 校验 → 提升为 Fact。

        Claim 全部落库:校验通过 status=promoted 并关联 fact_id,未通过 status=pending。
        有 Context Manager 时按维度检索相关材料片段(小上下文友好),否则回退全量文本。
        insights/required_facts 用于检索引导与提取聚焦。
        progress_callback(done, total) 每完成一个维度调用一次。
        """
        if not dimensions:
            dimensions = ["全部事实"]
        facts: list[Fact] = []
        total = len(dimensions)
        insight_block = self._build_insight_block(insights)
        facts_this_dim: list[Fact] = []
        for index, dimension in enumerate(dimensions, start=1):
            if progress_callback is not None:
                progress_callback(index, total)
            context_meta = {
                "dimension": dimension,
                "retrieved_unit_count": 0,
                "retrieved_chars": 0,
                "context_chars": 0,
                "truncated": False,
                "context_source": "full_material",
            }
            if cm is not None:
                if hasattr(cm, "for_evidence_with_meta"):
                    material_text, context_meta = cm.for_evidence_with_meta(dimension, insights, required_facts)
                    context_meta["context_source"] = "retrieval"
                else:
                    material_text = cm.for_evidence(dimension, insights, required_facts)
                    context_meta["context_chars"] = len(material_text)
            else:
                material_text = self._build_material_text(units_by_material, filenames)
                context_meta["context_chars"] = len(material_text)
            prompt = (
                f'分析维度:"{dimension}"\n\n'
                f"{insight_block}\n"
                f"材料文本(按来源标注):\n{material_text}\n\n"
                f"请提取该维度相关的陈述,输出 JSON。"
            )
            prompt_parts = {
                "dimension": len(f'分析维度:"{dimension}"\n\n'),
                "insight_block": len(insight_block or ""),
                "material_context": len(material_text or ""),
                "instruction": len("请提取该维度相关的陈述,输出 JSON。"),
                "system_prompt": len(self.role or _SYSTEM),
            }
            try:
                payload = self.generate_json(prompt)
            except Exception:
                continue
            call_id = self.last_call_id
            produced_fact_ids: list[int] = []
            stored_chars = 0
            # 兼容模型两种输出:新格式 claims / 旧格式 facts(小模型提示跟随不稳)
            items = payload.get("claims") or payload.get("facts") or []
            model_claims = len(items)
            valid_field_claims = 0
            quote_bound_claims = 0
            duplicate_claims = 0
            pending_claims = 0
            field_chars = _claim_field_chars(items)
            contributed_materials: set[int] = set()
            contributed_units: set[int] = set()
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
                if fact_exists(content):
                    duplicate_claims += 1
                    continue  # 跨维度去重,同一陈述只保留一条
                evidence_list = bind_sources(quote, units_by_material, filenames, unit_id=unit_id)
                claim = Claim(
                    material_id=evidence_list[0].material_id if evidence_list else 0,
                    content=content, quote=quote,
                    source=filenames.get(evidence_list[0].material_id, "") if evidence_list else "",
                    fact_type=fact_type, dimension=dimension,
                )
                if not evidence_list:
                    pending_claims += 1
                    save_claim(claim, status="pending", origin_call_id=call_id)  # 无来源支撑:保留为待核陈述
                    continue
                quote_bound_claims += 1
                contributed_materials.update(int(ev.material_id) for ev in evidence_list)
                contributed_units.update(int(ev.unit_id) for ev in evidence_list)
                # 校验通过:提升为 Fact 并绑定 Evidence
                fact = Fact(content=content, dimension=dimension, fact_type=fact_type)
                fact.id = save_fact_with_evidence(fact, evidence_list, task_id, origin_call_id=call_id)
                facts.append(fact)
                facts_this_dim.append(fact)
                produced_fact_ids.append(int(fact.id))
                stored_chars += len(fact.content or "") + sum(len(ev.quote or "") for ev in evidence_list)
                claim.fact_id = fact.id
                save_claim(claim, status="promoted", origin_call_id=call_id)
            update_call_products(call_id, produced_fact_ids=produced_fact_ids)
            update_call_metrics(call_id, stored_chars=stored_chars)
            update_call_funnel(
                call_id,
                dimension=dimension,
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
            )
            # 维度结束:该维度 Fact 批量计算 embedding 并持久化
            # (永久缓存:Writer 每章检索不再对全部事实重复计算,只算查询向量)
            if facts_this_dim:
                try:
                    vectors = embed_texts([f.content for f in facts_this_dim])
                    for fact, vector in zip(facts_this_dim, vectors):
                        vector_store.save_fact_vector(fact.id, vector)
                except Exception:
                    pass  # embedding 失败不影响事实本身,检索回退关键词
                facts_this_dim = []
        return facts

    def detect_conflicts(self, claims: list[dict]) -> list[Conflict]:
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
            conflict.id = save_conflict(conflict, origin_call_id=call_id)
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
        blocks: list[str] = []
        total = 0
        for material_id, units in units_by_material.items():
            for unit in units:
                if unit.kind == "image" and not unit.content:
                    continue
                label = f"[U{unit.id} {filenames.get(material_id, '?')}"
                if unit.page is not None:
                    label += f" 第{unit.page}页"
                label += "]"
                blocks.append(f"{label}\n{unit.content}")
                total += len(unit.content) + 20
                if total > 12000:
                    return "\n\n".join(blocks)
        return "\n\n".join(blocks)


def fact_exists(content: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM facts WHERE content=?", (content,)).fetchone()
    return row is not None


def save_fact_with_evidence(fact: Fact, evidence_list: list[Evidence], task_id: str = "",
                            origin_call_id: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO facts(content, dimension, source_level, fact_type, evidence_ids, conflict_ids, task_id, origin_call_id) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (fact.content, fact.dimension, fact.source_level, fact.fact_type, "[]", "[]", task_id, origin_call_id),
        )
        fact.id = cur.lastrowid
        evidence_ids = []
        for ev in evidence_list:
            ev.fact_id = fact.id
            cur2 = conn.execute(
                "INSERT INTO evidence(fact_id, material_id, unit_id, source_file, page, paragraph, quote) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (ev.fact_id, ev.material_id, ev.unit_id, ev.source_file, ev.page, ev.paragraph, ev.quote),
            )
            evidence_ids.append(cur2.lastrowid)
        conn.execute("UPDATE facts SET evidence_ids=? WHERE id=?", (json.dumps(evidence_ids), fact.id))
    return fact.id


def save_claim(claim: Claim, status: str, origin_call_id: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO claims(fact_id, material_id, content, quote, source, fact_type, dimension, status, origin_call_id) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (claim.fact_id, claim.material_id, claim.content, claim.quote,
             claim.source, claim.fact_type, claim.dimension, status, origin_call_id),
        )
        return cur.lastrowid


def load_claims() -> list[dict]:
    """当前任务的全部陈述(含未提升的 pending)。"""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM claims ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def save_conflict(conflict: Conflict, origin_call_id: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO conflicts(fact_key, entries, claim_ids, status, origin_call_id) VALUES(?, ?, ?, ?, ?)",
            (conflict.fact_key, json.dumps(conflict.entries, ensure_ascii=False),
             json.dumps(conflict.claim_ids), conflict.status, origin_call_id),
        )
        return cur.lastrowid


def load_evidence_quotes(fact_id: int) -> str:
    with connect() as conn:
        rows = conn.execute(
            "SELECT source_file FROM evidence WHERE fact_id=?", (fact_id,)
        ).fetchall()
    return ", ".join(row["source_file"] for row in rows)
