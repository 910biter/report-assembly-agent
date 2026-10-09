"""Evidence Agent:材料 → Claim(陈述)→ Fact(确认信息)提取、来源绑定、冲突发现。

数据链路:Material → Document Unit → Evidence → Claim → Fact。
Claim 是材料中被提取的陈述(可能是原文观点,不一定是事实);quote 校验通过的
Claim 提升(promote)为 Fact 并绑定 Evidence;未通过的保留为 pending Claim。
冲突检测在 Claim 层进行:只标注矛盾,不替用户裁定。
硬约束:无原文支撑的陈述不提升为 Fact(宁可少输出)。
"""
import json
import re
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor


from app.agents.base import BaseAgent
from app.cache import stable_hash
from app.config import settings
from app.context_budget import ContextSection, build_prompt_from_sections, count_tokens
from app.db import session_scope
from app.assertion_semantics import (
    normalize_assertion_mode,
    normalize_assertion_semantics,
    normalize_semantic_profile,
    semantic_profile_json,
    semantic_profile_line,
)
from app.infrastructure.orm import ORMFact, ORMEvidence, ORMClaim, ORMConflict
from sqlalchemy import select, update
from app.models import Claim, Conflict, Evidence, Fact, Unit
from app.retrieval import vector_store
from app.retrieval.embedder import embed_texts
from app.token_monitor import current_context, log_pipeline_event, update_call_funnel, update_call_metrics, update_call_products

_FACT_PERSIST_LOCK = threading.RLock()


def _assert_not_paused(task_id: str) -> None:
    """批/调用之间检查暂停零件:读到 control_request=='pause' 抛 TASK_PAUSED。

    在单批 LLM 调用之前检查(即上一批刚结束的边界),绝不打断正在运行的单次调用。
    queue worker 以 str(exc)=='TASK_PAUSED' 统一置 stage=paused,故此处不须改状态,
    只需冒泡该异常,自然走暂停而非失败。
    """
    if not task_id:
        return
    from app.memory import short_term
    task = short_term.load_task(task_id)
    if task and task.get("control_request") == "pause":
        raise RuntimeError("TASK_PAUSED")

_SYSTEM = """你是情报事实提取员。从材料中提取可溯源的陈述(Claim),严格区分事实与推断。
严格输出 JSON,不要任何解释:
{"claims": [{"content": "保留原文时态、条件和语气的陈述", "unit_id": 材料单元编号, "short_quote": "30字以内原文短摘", "fact_type": "开放标签", "assertion_mode": "reported|prescriptive|prospective|conditional|opinion|unknown", "assertion_mode_raw": "粗粒度协议的原文描述", "semantic_profile": {"facets": [{"axis": "命题类型", "label": "事件/观察/定义/比较/建议/预测等开放类别", "detail": "原文依据"}, {"axis": "情态或言语行为", "label": "断言/义务/许可/计划/目标/可能/假设等开放类别", "detail": ""}, {"axis": "时间范围", "label": "过去/当前/进行中/未来/周期/无时间性/混合等开放类别", "detail": "明确时间锚点"}, {"axis": "实现状态", "label": "已发生/进行中/拟议/计划/目标/要求/假设/未证实等开放类别", "detail": ""}, {"axis": "知识依据", "label": "测量/观察/记录/转述/估算/推导/观点/争议等开放类别", "detail": ""}, {"axis": "主体/范围/量词", "label": "开放类别", "detail": "适用对象、范围边界或量化口径"}, {"axis": "条件/因果/关系", "label": "开放类别", "detail": "仅在相关时填写"}, {"axis": "极性/否定/例外", "label": "开放类别", "detail": "否定、限制或例外依据"}], "attribution": "来源/发言主体", "conditions": [], "scope_qualifiers": [], "additional": {}}}, ...]}

要求:
1. unit_id 必须来自材料文本标注的 U编号;short_quote 必须逐字摘自该单元,不得改写
2. content 是对 quote 的忠实表述(允许同义改述)
3. fact_type 是便于检索和展示的开放标签；可使用材料中的专业类别，无法判断时写 unknown，不得为了符合枚举改写陈述
4. 只输出有原文支撑的陈述;没有支撑就少输出
5. 禁止输出推断、评价、总结(那些属于"分析人士认为"类观点时,content 须注明是来源观点)
6. 分析维度和优先事实类别只是检索/提取重点,不是排除边界;材料中与用户目标明显相关的高价值事实即使不在维度内也应提取
7. 面向综述或多材料任务时,同一维度应覆盖主要机制、标准、平台、时间线、威胁模型和比较要素;不要只抽取少数摘要性陈述
8. semantic_profile 是主要语义分类，采用开放、多标签、多维画像，不存在封闭类别清单。逐条检查所有会影响陈述真值、适用性或可比性的面向；按内容填写相关维度，包括但不限于命题类型、情态/言语行为、时间、实现状态、认识依据/确定性、来源归属、主体/对象、范围/量词、条件/前提、因果/关系、极性/否定、例外/限制及其他领域细类。示例不是必填清单；不相关不填，不适用或未说明不得臆造。新维度直接使用贴合原文的开放轴名和类别，不得因示例未列出而遗漏或归并
9. facets 中每项用 axis/label/detail 保存一个维度及其开放类别；同一陈述可以有多个轴或同轴多标签。additional 用于保留未放入标准字段的其他分类，不得因 schema 示例未列出而丢弃类别
10. attribution、temporal_reference、conditions、scope_qualifiers 等保留原文明确的主体、时间、条件和范围；缺失时不得臆造。分类描述只刻画来源如何陈述，不代表系统独立核实
11. assertion_mode 仅是旧接口的粗粒度投影，不作为完整分类；assertion_mode_raw 保留原文语义描述。二者无法涵盖的类别必须完整保存在 semantic_profile，不能归并丢弃
12. content 必须保留陈述的时态、条件、情态和来源归属;材料提出创建/采用/实现某机制,应表述为“材料/方案提出或要求……”,不得改写成机制已经存在或已落地
13. 必须依据该陈述本身及必要的前后文判断;不得用领域关键词、文件名、章节标题或固定词表规则推断陈述状态,也不得用材料整体角色替代逐条判断
14. 示例类别仅用于解释分类维度，不是封闭词表；可输出任何跨领域或领域特有细类，禁止因不在示例中而降级为 unknown 或删除
15. 不要输出 file/page/完整原文,系统会根据 unit_id 回查来源"""

_CONFLICT_SYSTEM = """你是证据关系核验员。只比较给定 Claim 编号,判断同一事项的说法属于哪种关系。
严格输出 JSON,不要任何解释:
{"conflicts": [{"fact_key": "核验主题", "claim_ids": [说法A编号, 说法B编号], "conflict_type": "direct_contradiction/temporal_difference/scope_difference/metric_difference/qualification/needs_verification", "reason": "明确说明A与B在哪个事实点上相同或不同", "confidence": "high/medium/low"}]}
判定规则:
1. direct_contradiction 仅限主体、事项、时间、范围和指标口径可比,且结论不能同时成立。
2. 不同时间的变化是 temporal_difference;不同范围/对象是 scope_difference;不同统计定义是 metric_difference。
3. 总体可用与局部限制、原则与例外、结论与适用条件并存时是 qualification,不是直接矛盾。
4. 陈述性质不同的事实与设计目标、要求、建议或预测不是同一语义状态;除非材料明确表明其针对同一时点/状态作出互斥断言,不得仅因内容不同标成 direct_contradiction。
5. 条件不足以确认可比性时标 needs_verification;没有任何需要核验的关系时输出空数组。
6. Claim 的 semantic_profile 是开放多维分类，类别清单不封闭。须检查其中所有时间、条件、范围、归属、实现状态和细类；不得因粗粒度 assertion_mode 相同就假设语义等价。
7. 每个结果必须且只能包含两个 Claim 编号。一个候选组存在多组关系时,拆成多个两两比较结果,禁止把3条以上说法放进同一结果。
8. 只能返回输入中真实存在的 Claim 编号;不要生成来源、页码、引文或改写陈述,这些由系统按编号回查。"""

_CONFLICT_TYPES = {
    "direct_contradiction", "temporal_difference", "scope_difference",
    "metric_difference", "qualification", "needs_verification",
}


def _normalize_conflict_type(value: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in _CONFLICT_TYPES else "needs_verification"


def _normalize_confidence(value: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in {"high", "medium", "low"} else "medium"

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


def _positive_unit_id(value) -> int | None:
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed > 0 else None


def _unit_ids_in_text(text: str) -> list[int]:
    """Extract source-unit IDs from the stable markers embedded in Evidence input."""
    return sorted({int(value) for value in re.findall(r"\[U(\d+)\s*\|", str(text or ""))})


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
                        fact_id=None, material_id=material_id, unit_id=unit.id or 0,
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
                fact_id=None, material_id=material_id, unit_id=unit.id or 0,
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
                fact_id=None, material_id=material_id, unit_id=unit.id or 0,
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
    for index, entry in enumerate(cleaned, start=1):
        entry["need_id"] = index
    return cleaned


def _claim_field_chars(items: list[dict]) -> dict[str, int]:
    fields = {
        "content": 0, "short_quote": 0, "unit_id": 0,
        "fact_type": 0, "assertion_mode": 0, "assertion_mode_raw": 0, "other": 0,
    }
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
            elif key == "assertion_mode":
                fields["assertion_mode"] += len(text)
            elif key == "assertion_mode_raw":
                fields["assertion_mode_raw"] += len(text)
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


def _conflict_entries(claim_ids: list[int], claims_by_id: dict[int, dict]) -> list[dict]:
    """Rebuild source data from persisted Claim -> Fact -> Evidence lineage."""
    fact_ids = {
        int(claims_by_id[claim_id].get("fact_id") or 0)
        for claim_id in claim_ids if claim_id in claims_by_id
    }
    fact_ids.discard(0)
    evidence_by_fact: dict[int, list[dict]] = {}
    if fact_ids:
        with session_scope() as session:
            rows = session.execute(
                select(ORMEvidence).where(ORMEvidence.c.fact_id.in_(fact_ids))
                .order_by(ORMEvidence.c.id)
            ).mappings().all()
        for row in rows:
            evidence_by_fact.setdefault(int(row["fact_id"]), []).append(dict(row))
    entries: list[dict] = []
    for claim_id in claim_ids:
        claim = claims_by_id.get(claim_id)
        if not claim:
            continue
        fact_id = int(claim.get("fact_id") or 0)
        evidence_rows = evidence_by_fact.get(fact_id) or []
        preferred_unit = int(claim.get("unit_id") or 0)
        evidence = next(
            (row for row in evidence_rows if int(row.get("unit_id") or 0) == preferred_unit),
            evidence_rows[0] if evidence_rows else None,
        )
        if evidence is None:
            continue
        entries.append({
            "claim_id": claim_id,
            "fact_id": fact_id,
            "material_id": int(evidence.get("material_id") or claim.get("material_id") or 0),
            "unit_id": int(evidence.get("unit_id") or preferred_unit or 0),
            "file": str(evidence.get("source_file") or claim.get("source") or ""),
            "page": evidence.get("page"),
            "paragraph": evidence.get("paragraph"),
            "quote": str(evidence.get("quote") or claim.get("quote") or ""),
            "statement": str(claim.get("content") or ""),
            "semantic_profile": normalize_semantic_profile(
                claim.get("semantic_profile") or claim.get("semantic_profile_json"),
            ),
            "assertion_mode": normalize_assertion_semantics(
                claim.get("assertion_mode"), claim.get("assertion_mode_raw"),
            )[0],
            "assertion_mode_raw": normalize_assertion_semantics(
                claim.get("assertion_mode"), claim.get("assertion_mode_raw"),
            )[1],
        })
    return entries


def _split_unit_blocks(material_text: str, max_tokens: int) -> list[str]:
    """Pack complete labelled Units into token-bounded chunks.

    Evidence quotes are bound back to Unit ids, so a Unit is the smallest safe
    split boundary. A single oversized Unit is deliberately kept intact.
    """
    original = str(material_text or "")
    text = original.strip()
    if not text or count_tokens(text) <= max_tokens:
        return [original]
    prefix = ""
    marker = "相关材料片段:\n"
    if text.startswith(marker):
        prefix, text = marker, text[len(marker):]
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    if len(blocks) < 2:
        return [original]
    separator_tokens = count_tokens("\n\n")
    prefix_tokens = count_tokens(prefix)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = prefix_tokens
    for block in blocks:
        block_tokens = count_tokens(block)
        extra_tokens = separator_tokens if current else 0
        if current and current_tokens + extra_tokens + block_tokens > max_tokens:
            chunks.append(prefix + "\n\n".join(current))
            current = []
            current_tokens = prefix_tokens
            extra_tokens = 0
        current.append(block)
        current_tokens += extra_tokens + block_tokens
    if current:
        chunks.append(prefix + "\n\n".join(current))
    return chunks or [original]


def _split_batch_meta(batch_meta: dict, part: str, **split_fields) -> dict:
    """Keep retrieval metrics truthful after an adaptive source split."""
    meta = dict(batch_meta)
    unit_count = len(re.findall(r"(?m)^\[U(?:\d+|\?)\s*\|", part))
    material_ids = list(dict.fromkeys(meta.get("batch_material_ids") or []))
    meta.update({
        "retrieved_unit_count": unit_count,
        "retrieved_chars": len(part),
        "context_chars": len(part),
        **split_fields,
    })
    if len(material_ids) == 1:
        meta["batch_material_units"] = {material_ids[0]: unit_count}
    else:
        # A split can cut between materials; do not retain the parent's counts.
        meta["batch_material_units"] = {}
    return meta


class EvidenceAgent(BaseAgent):
    output_token_limit = settings.evidence_output_tokens
    name = "evidence"
    role = _SYSTEM

    def should_retry(self, exc: Exception) -> bool:
        """Let extraction split a truncated batch instead of retrying it unchanged."""
        if (
            str(exc) == "MODEL_OUTPUT_TRUNCATED"
            and getattr(self, "_split_truncated_batch", False)
        ):
            return False
        return super().should_retry(exc)

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
    ) -> list[Fact]:
        """主线(论文思想整合):First-pass 合并读取 → Coverage Audit → Iterative Retrieval。

        1. First-pass:全部 Evidence Needs 一次打分选候选(材料全覆盖),
           每批材料只读取一次,模型按 need 标注输出(多维联合提取);
        2. Coverage Audit:每个 Need 检查是否被事实真正支持(LLM 判断,非硬编码阈值);
        3. Iterative Retrieval:缺口 Needs 针对性补检(缺什么补什么,足够即停)。
        """
        needs = _normalize_needs(needs)
        self.batch_failures = []
        self.coverage_audit_status = {"status": "unknown", "error": "not_run"}
        self.last_extraction_status = {"status": "running"}
        facts: list[Fact] = []
        total_phases = 3

        def report_phase(phase: int) -> None:
            if progress_callback is not None:
                progress_callback(phase, total_phases)

        report_phase(1)
        first_pass_facts = 0
        if cm is not None and hasattr(cm, "for_first_pass"):
            batches = cm.for_first_pass(needs, insights)
            for produced in self._process_batches(
                batches, needs, units_by_material, filenames, task_id,
            ):
                first_pass_facts += len(produced)
                facts.extend(produced)
        else:  # 无 Context Manager:全量文本直接处理
            material_text = self._build_material_text(units_by_material, filenames)
            produced = self._process_batch(
                needs, material_text, {"pass": "full_material"}, units_by_material,
                filenames, task_id, 1, 1,
            )
            first_pass_facts += len(produced)
            facts.extend(produced)
        if first_pass_facts == 0 and _has_text_units(units_by_material):
            # Low-yield recovery is a quality guardrail: when a successful stage
            # produces no facts, retry with smaller material-sweep batches rather
            # than letting later stages write from an empty evidence base.
            recovery_batches = self._material_sweep_batches(units_by_material, filenames)
            log_pipeline_event(
                "evidence",
                recovery_reason="first_pass_zero_facts",
                recovery_batch_count=len(recovery_batches),
                total_materials=len(units_by_material),
            )
            for produced in self._process_batches(
                recovery_batches, needs, units_by_material, filenames, task_id, round_tag=-1,
            ):
                facts.extend(produced)
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
            for produced in self._process_batches(
                gap_batches, gaps, units_by_material, filenames, task_id, round_tag=round_index,
            ):
                facts.extend(produced)
            if len(facts) == before_count and _has_text_units(units_by_material):
                recovery_batches = self._gap_sweep_batches(gaps, units_by_material, filenames, known_contents)
                log_pipeline_event(
                    "evidence",
                    recovery_reason=f"gap_round_{round_index}_zero_new_facts",
                    recovery_batch_count=len(recovery_batches),
                    uncovered_need_count=len(gaps),
                )
                for produced in self._process_batches(
                    recovery_batches, gaps, units_by_material, filenames, task_id,
                    round_tag=round_index + 10,
                ):
                    facts.extend(produced)
            if len(facts) == before_count:
                break  # 无新证据:接受 information_gap,不再空转
            gaps = self._coverage_audit(facts, needs, task_id)
        report_phase(3)
        self.last_extraction_status = {
            "status": "partial" if self.batch_failures or gaps is None else "complete",
            "failed_batches": list(self.batch_failures),
            "coverage": dict(self.coverage_audit_status),
            "uncovered_needs": list(gaps or []),
            "needs": needs,
        }
        return facts

    def _process_batches(
        self,
        batches: list[tuple[str, dict]],
        needs: list[dict],
        units_by_material: dict[int, list[Unit]],
        filenames: dict[int, str],
        task_id: str,
        round_tag: int = 0,
    ) -> list[list[Fact]]:
        """Run independent extraction calls concurrently and merge by batch order."""
        if not batches:
            return []
        batches = self._fit_material_batches(batches, needs)
        concurrency = max(1, int(settings.evidence_batch_concurrency or 1))

        def run_one(batch_index: int, material_text: str, batch_meta: dict):
            worker = EvidenceAgent()
            worker.batch_failures = []
            produced = worker._process_batch(
                needs, material_text, batch_meta, units_by_material, filenames,
                task_id, batch_index, len(batches), round_tag=round_tag,
            )
            return produced, worker.batch_failures

        if concurrency == 1 or len(batches) == 1:
            outcomes = [run_one(index, text, meta) for index, (text, meta) in enumerate(batches, start=1)]
        else:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(batches)), thread_name_prefix="evidence-batch") as pool:
                futures = []
                for index, (text, meta) in enumerate(batches, start=1):
                    context = contextvars.copy_context()
                    futures.append(pool.submit(context.run, run_one, index, text, meta))
                outcomes = [future.result() for future in futures]
        self.batch_failures = list(getattr(self, "batch_failures", []))
        for _facts, failures in outcomes:
            self.batch_failures.extend(failures)
        return [produced for produced, _failures in outcomes]

    def _fit_material_batches(
        self, batches: list[tuple[str, dict]], needs: list[dict],
    ) -> list[tuple[str, dict]]:
        """Split only source batches that cannot fit without dropping whole Units."""
        from app.runtime_profiles import stage_input_budget_tokens

        need_tokens = count_tokens(self._build_need_block(needs))
        # Instructions and section labels are small but variable. This allowance
        # is a packing boundary, not a content quota.
        material_budget = max(
            2048,
            stage_input_budget_tokens("evidence") - need_tokens - 768,
        )
        fitted: list[tuple[str, dict]] = []
        for material_text, batch_meta in batches:
            parts = _split_unit_blocks(material_text, material_budget)
            if len(parts) == 1:
                fitted.append((material_text, batch_meta))
                continue
            for part_index, part in enumerate(parts, start=1):
                meta = _split_batch_meta(
                    batch_meta, part,
                    adaptive_split="input_capacity",
                    split_part=part_index,
                    split_total=len(parts),
                )
                fitted.append((part, meta))
        return fitted

    def _process_batch(self, needs: list[dict], material_text: str, batch_meta: dict,
                       units_by_material: dict[int, list[Unit]], filenames: dict[int, str],
                       task_id: str, batch_index: int, batch_count: int,
                       round_tag: int = 0) -> list[Fact]:
        """单批处理:多维联合提取(全部 Needs)→ claims 解析 → quote 校验 → Fact 落库。

        输出格式:claims 每条带 need_id/need(模型标注归属),无标注则按相关性回退到首个 need。
        """
        needs_list = needs if isinstance(needs, list) else [needs]
        _assert_not_paused(task_id)
        need_block = self._build_need_block(needs_list)
        insight_block = self._build_insight_block([])
        instruction = (
            "请提取与上述任一 Evidence Need 相关的陈述;每条必须标注属于哪个 need_id。"
            "若发现与用户目标明显相关但不属于任何 need 的高价值事实,也提取并标注 need_id=0。"
            "必须识别每条陈述的开放语义状态并保留其时态、条件与情态;设计目标、要求、建议或预测不能改写成已实现事实。"
            "如果材料覆盖多个平台/标准/攻击/机制,请分别抽取,不要合并成过度概括的一条。输出 JSON。"
        )
        from app.runtime_profiles import stage_input_budget_tokens
        prompt, _audit = build_prompt_from_sections("evidence", [
            ContextSection("instruction", [instruction], weight=5, required_items=1),
            ContextSection("evidence_needs", need_block.splitlines(), weight=5, required_items=1),
            ContextSection("material_insight", insight_block.splitlines(), weight=2, required_items=0),
            ContextSection("material_units", material_text.split("\n\n"), weight=4, required_items=1),
        ], stage_input_budget_tokens("evidence"))
        candidate_unit_ids = _unit_ids_in_text(material_text)
        input_unit_ids = _unit_ids_in_text(prompt)
        unit_to_material = {
            int(unit.id): int(material_id)
            for material_id, material_units in units_by_material.items()
            for unit in material_units
            if getattr(unit, "id", None) is not None
        }
        input_material_ids = sorted({
            unit_to_material[unit_id] for unit_id in input_unit_ids if unit_id in unit_to_material
        })
        batch_input_audit = {
            "input_unit_ids": input_unit_ids,
            "candidate_unit_ids": candidate_unit_ids,
            "omitted_unit_ids": sorted(set(candidate_unit_ids) - set(input_unit_ids)),
            "input_material_ids": input_material_ids,
            "input_material_count": len(input_material_ids),
            "context_audit": _audit,
        }
        prompt_parts = {
            "needs": sum(len(n.get("need", "")) for n in needs_list),
            "material_context": len(material_text or ""),
            "system_prompt": len(self.role or _SYSTEM),
        }
        context_meta = dict(batch_meta)
        try:
            self._split_truncated_batch = True
            payload = self.generate_json(prompt)
        except Exception as exc:
            if str(exc) in {"TASK_PAUSED", "EXECUTION_LOCK_LOST"}:
                raise
            call_id = self.last_call_id
            update_call_funnel(
                call_id,
                dimension="first_pass" if not round_tag else f"iterative_r{round_tag}",
                context_meta=context_meta,
                prompt_parts=prompt_parts,
                prompt_chars=len(prompt),
                material_context_chars=len(material_text),
                **batch_input_audit,
                model_claims=0,
                valid_field_claims=0,
                quote_bound_claims=0,
                promoted_facts=0,
                extraction_error=str(exc)[:300],
            )
            if str(exc) == "MODEL_OUTPUT_TRUNCATED":
                split_parts = _split_unit_blocks(
                    material_text, max(1, count_tokens(material_text) // 2),
                )
                if len(split_parts) > 1:
                    log_pipeline_event(
                        "evidence",
                        recovery_reason="output_truncated_split",
                        original_call_id=call_id,
                        split_count=len(split_parts),
                        batch_index=batch_index,
                    )
                    recovered: list[Fact] = []
                    for part_index, part in enumerate(split_parts, start=1):
                        meta = _split_batch_meta(
                            batch_meta, part,
                            adaptive_split="output_truncated",
                            parent_call_id=call_id,
                            split_part=part_index,
                            split_total=len(split_parts),
                        )
                        recovered.extend(self._process_batch(
                            needs, part, meta, units_by_material, filenames,
                            task_id, part_index, len(split_parts), round_tag=round_tag,
                        ))
                    return recovered
            self.batch_failures = list(getattr(self, "batch_failures", []))
            self.batch_failures.append({
                "call_id": call_id, "round": round_tag, "batch": batch_index,
                "unit_ids": candidate_unit_ids, "error": str(exc)[:300],
            })
            return []
        finally:
            self._split_truncated_batch = False
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
            unit_id = _positive_unit_id(item.get("unit_id"))
            if not content or not quote:
                continue
            valid_field_claims += 1
            # Keep the model's semantic label. Protocol consumers may map it
            # later, but extraction must not discard an unfamiliar category.
            fact_type = str(item.get("fact_type") or "unknown").strip()[:64]
            assertion_mode, assertion_mode_raw = normalize_assertion_semantics(
                item.get("assertion_mode"), item.get("assertion_mode_raw"),
            )
            semantic_profile = normalize_semantic_profile(item.get("semantic_profile"))
            # 维度归属:模型标注 need_id(0=开放发现),无标注回退首个 need 的维度
            try:
                need_idx = int(item.get("need_id", 0))
            except (TypeError, ValueError):
                need_idx = 0
            need_by_id = {
                int(need.get("need_id") or index): need
                for index, need in enumerate(needs_list, start=1)
            }
            if need_idx not in need_by_id:
                need_idx = 0
            dimension = need_by_id[need_idx].get("dimension", "开放发现") if need_idx else "开放发现"
            evidence_list = bind_sources(quote, units_by_material, filenames, unit_id=unit_id)
            claim = Claim(
                material_id=evidence_list[0].material_id if evidence_list else 0,
                unit_id=unit_id,
                content=content, quote=quote,
                source=filenames.get(evidence_list[0].material_id, "") if evidence_list else "",
                fact_type=fact_type, assertion_mode=assertion_mode,
                assertion_mode_raw=assertion_mode_raw,
                semantic_profile=semantic_profile,
                dimension=dimension, need_id=need_idx,
            )
            if not evidence_list:
                with _FACT_PERSIST_LOCK:
                    if fact_exists(content, task_id, assertion_mode, semantic_profile):
                        duplicate_claims += 1
                        continue
                pending_claims += 1
                save_claim(claim, status="pending", origin_call_id=call_id, task_id=task_id)
                continue
            quote_bound_claims += 1
            contributed_materials.update(int(ev.material_id) for ev in evidence_list)
            contributed_units.update(int(ev.unit_id) for ev in evidence_list)
            fact = Fact(
                content=content, dimension=dimension, fact_type=fact_type,
                assertion_mode=assertion_mode, assertion_mode_raw=assertion_mode_raw,
                semantic_profile=semantic_profile,
                need_id=need_idx,
            )
            # Check-and-insert is serialized within the process so concurrent
            # extraction batches cannot create duplicate task facts.
            with _FACT_PERSIST_LOCK:
                if fact_exists(content, task_id, assertion_mode, semantic_profile):
                    duplicate_claims += 1
                    continue
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
            **batch_input_audit,
            contributed_material_count=len(contributed_materials),
            contributed_unit_count=len(contributed_units),
            contributed_material_ids=sorted(contributed_materials),
            contributed_unit_ids=sorted(contributed_units),
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

    def _material_sweep_batches(self, units_by_material: dict[int, list[Unit]],
                                filenames: dict[int, str]) -> list[tuple[str, dict]]:
        """Generic recovery batches that scan source units without domain rules."""
        return _build_unit_batches(units_by_material, filenames, pass_name="recovery_material_sweep")

    def _gap_sweep_batches(self, gaps: list[dict], units_by_material: dict[int, list[Unit]],
                           filenames: dict[int, str], known_contents: set[str]) -> list[tuple[str, dict]]:
        batches = _build_unit_batches(
            units_by_material,
            filenames,
            pass_name="recovery_gap_sweep",
            known_contents=known_contents,
        )
        for _text, meta in batches:
            meta["needs"] = gaps
        return batches

    def _build_need_block(self, needs: list[dict]) -> str:
        """Evidence Needs 清单(合并读取的多维提取引导)。"""
        lines = ["Evidence Needs(待证实信息需求):"]
        for index, need in enumerate(needs, start=1):
            lines.append(f"{int(need.get('need_id') or index)}. [{need.get('priority', 'medium')}] {need.get('need', '')}")
        return "\n".join(lines)

    def _coverage_audit(self, facts: list[Fact], needs: list[dict], task_id: str) -> list[dict] | None:
        """Coverage Audit:每个 Need 是否被事实真正支持(LLM 语义判断,无硬编码阈值)。

        返回未被充分证实的 Needs; None 表示未核验,不能视为无缺口。
        """
        if not needs:
            self.coverage_audit_status = {"status": "complete", "uncovered_need_ids": []}
            return []
        need_by_id = {int(need.get("need_id") or index): need for index, need in enumerate(needs, start=1)}
        need_facts: dict[int, list[str]] = {}
        for need_index, need in need_by_id.items():
            # 真实关系匹配:fact.need_id 由模型标注(非 dimension 猜测)
            matched = [
                f"[F{getattr(f, 'id', index)}] {f.content}"
                for index, f in enumerate(facts, start=1)
                if int(getattr(f, "need_id", 0) or 0) == need_index
            ]
            # 无标注的开放发现(need_id=0)不自动归属任何 need(避免误判支持)
            need_facts[need_index] = matched
        from app.runtime_profiles import stage_input_budget_tokens, stage_profile

        prefix = (
            "以下每个 Evidence Need 后列出了当前已提取的相关事实。"
            "逐项判断所列事实能否直接、具体地回答需求；仅侧面提及、过于概括或无法回答，视为未证实。"
            "同一需求可能分在多个请求中核验；当前请求中的事实是该需求的一部分，判断这一组事实本身是否足以回答。\n\n"
        )
        suffix = "\n\n严格输出 JSON:\n{\"uncovered\": [未充分证实的 need 编号数组]}\n若全部充分,输出 {\"uncovered\": []}"

        def render_prompt(blocks: list[str]) -> str:
            return prefix + "\n\n".join(blocks) + suffix

        budget = max(0, int(stage_input_budget_tokens("qa") or 0))

        def fits(blocks: list[str]) -> bool:
            return count_tokens(_AUDIT_SYSTEM + "\n" + render_prompt(blocks)) <= budget

        # Split only on complete fact records. A single indivisible record that
        # cannot fit makes the audit unknown rather than silently truncating it.
        need_chunks: list[tuple[int, str]] = []
        for need_id, need in need_by_id.items():
            rows = need_facts[need_id]
            current_rows: list[str] = []
            row_groups: list[list[str]] = []
            if not rows:
                row_groups.append([])
            else:
                for row in rows:
                    candidate_rows = [*current_rows, row]
                    block = (
                        f"{need_id}. {need.get('need', '')}\n"
                        "已提取事实（本组）:\n" + "\n".join(f"- {value}" for value in candidate_rows)
                    )
                    if fits([block]):
                        current_rows = candidate_rows
                        continue
                    if current_rows:
                        row_groups.append(current_rows)
                        current_rows = []
                    single_block = (
                        f"{need_id}. {need.get('need', '')}\n已提取事实（本组）:\n- {row}"
                    )
                    if not fits([single_block]):
                        self.coverage_audit_status = {
                            "status": "unknown",
                            "error": "COVERAGE_RECORD_EXCEEDS_INPUT_BUDGET",
                            "unknown_need_ids": [need_id],
                        }
                        return None
                    current_rows = [row]
                if current_rows:
                    row_groups.append(current_rows)
            for rows_group in row_groups:
                facts_text = "\n".join(f"- {value}" for value in rows_group) or "(无)"
                need_chunks.append((need_id, f"{need_id}. {need.get('need', '')}\n已提取事实（本组）:\n{facts_text}"))

        # Greedily combine distinct needs while the complete system+user prompt
        # fits the stage budget. Repeated chunks for one need stay in separate calls.
        batches: list[list[tuple[int, str]]] = []
        current: list[tuple[int, str]] = []
        current_ids: set[int] = set()
        for need_id, block in need_chunks:
            candidate = [*current, (need_id, block)]
            if need_id in current_ids or not fits([item[1] for item in candidate]):
                if current:
                    batches.append(current)
                    current, current_ids = [], set()
                candidate = [(need_id, block)]
            if not fits([item[1] for item in candidate]):
                self.coverage_audit_status = {
                    "status": "unknown",
                    "error": "COVERAGE_RECORD_EXCEEDS_INPUT_BUDGET",
                    "unknown_need_ids": [need_id],
                }
                return None
            current = candidate
            current_ids.add(need_id)
        if current:
            batches.append(current)

        uncovered_by_need: dict[int, list[bool]] = {need_id: [] for need_id in need_by_id}
        batch_count = 0
        try:
            for batch in batches:
                prompt = render_prompt([block for _, block in batch])
                payload = self.generate_json(
                    prompt, system=_AUDIT_SYSTEM,
                    max_tokens=stage_profile("qa").output_tokens,
                )
                uncovered = payload.get("uncovered") if isinstance(payload, dict) else None
                batch_ids = {need_id for need_id, _ in batch}
                if not isinstance(uncovered, list) or any(
                    not str(value).isdigit() or int(value) not in batch_ids for value in uncovered
                ):
                    raise ValueError("INVALID_COVERAGE_RESULT")
                uncovered_ids = {int(value) for value in uncovered}
                for need_id in batch_ids:
                    uncovered_by_need[need_id].append(need_id in uncovered_ids)
                batch_count += 1
            # A need is uncovered only when every complete fact group was audited
            # as insufficient. Any independently sufficient group establishes coverage.
            uncovered_ids = sorted(
                need_id for need_id, results in uncovered_by_need.items()
                if results and all(results)
            )
            split_ids = sorted(need_id for need_id, results in uncovered_by_need.items() if len(results) > 1)
            self.coverage_audit_status = {
                "status": "complete",
                "uncovered_need_ids": uncovered_ids,
                "audited_need_ids": sorted(need_by_id),
                "split_need_ids": split_ids,
                "batch_count": batch_count,
            }
            return [need_by_id[need_id] for need_id in uncovered_ids]
        except Exception as exc:
            if str(exc) in {"TASK_PAUSED", "EXECUTION_LOCK_LOST"}:
                raise
            self.coverage_audit_status = {
                "status": "unknown", "error": str(exc)[:300],
                "audited_batch_count": batch_count,
                "unknown_need_ids": sorted(need_by_id),
            }
            return None

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
        # Only promoted Claims may enter quality decisions. Pending Claims have
        # not passed quote binding and therefore cannot be shown as evidence.
        verified_claims = [
            claim for claim in claims
            if claim.get("status") == "promoted" and int(claim.get("fact_id") or 0) > 0
        ]
        candidate_claims = _conflict_candidate_claims(verified_claims)
        if len(candidate_claims) < 2:
            log_pipeline_event(
                "evidence",
                candidate_total=len(verified_claims),
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
                candidate_total=len(verified_claims),
                candidate_count=len(candidate_claims),
                candidate_groups=0,
                llm_skipped_calls=1,
                skip_reason="no_conflict_signal_groups",
            )
            return []
        candidate_ids = {cid for group in candidate_groups for cid in group.get("claim_ids", [])}
        candidate_claims = [c for c in candidate_claims if int(c.get("id") or 0) in candidate_ids]
        claim_lines = [
            f"{c['id']}. [{c.get('source', '')}|{c.get('fact_type', 'unknown')}|"
            f"陈述性质={normalize_assertion_mode(c.get('assertion_mode'))}"
            f"（{normalize_assertion_semantics(c.get('assertion_mode'), c.get('assertion_mode_raw'))[1]}）"
            f"语义画像={semantic_profile_line(c.get('semantic_profile'), c.get('assertion_mode'), c.get('assertion_mode_raw'))}] {c['content']}"
            for c in candidate_claims
        ]
        group_lines = [
            f"- {g['kind']}({g['signal']}): " + ",".join(str(i) for i in g.get("claim_ids", []))
            for g in candidate_groups
        ]
        prompt = (
            "以下是已通过原文绑定的陈述清单(编号 + 来源 + 类型):\n"
            + "\n".join(claim_lines)
            + "\n\n规则预筛得到的疑似冲突组:\n"
            + "\n".join(group_lines)
            + "\n\n请按可比口径分类这些关系,输出 JSON。"
        )
        try:
            from app.runtime_profiles import stage_profile
            payload = self.generate_json(
                prompt, system=_CONFLICT_SYSTEM,
                max_tokens=stage_profile("conflict").output_tokens,
            )
        except Exception:
            return []
        call_id = self.last_call_id
        update_call_metrics(call_id, candidate_total=len(verified_claims), candidate_count=len(candidate_claims))
        update_call_funnel(
            call_id,
            candidate_total=len(verified_claims),
            candidate_count=len(candidate_claims),
            candidate_groups=len(candidate_groups),
            quality_guardrail_calls=1,
        )
        conflicts: list[Conflict] = []
        claims_by_id = {int(claim["id"]): claim for claim in candidate_claims}
        for item in payload.get("conflicts", []):
            claim_ids = list(dict.fromkeys(
                int(value) for value in item.get("claim_ids", [])
                if str(value).isdigit() and int(value) in claims_by_id
            ))
            # A review item is a pairwise proposition. Multi-claim bags do not
            # state who differs from whom and therefore cannot be persisted as
            # an explainable conflict.
            if len(claim_ids) != 2:
                continue
            entries = _conflict_entries(claim_ids, claims_by_id)
            if len(entries) < 2:
                continue
            conflict_type = _normalize_conflict_type(item.get("conflict_type"))
            conflict = Conflict(
                fact_key=str(item.get("fact_key", "")),
                entries=entries,
                claim_ids=claim_ids,
                conflict_type=conflict_type,
                reason=str(item.get("reason", "")).strip(),
                confidence=_normalize_confidence(item.get("confidence")),
                status="unresolved" if conflict_type == "direct_contradiction" else "needs_review",
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
        from app.runtime_profiles import stage_input_budget_tokens

        blocks: list[str] = []
        total = 0
        budget = stage_input_budget_tokens("evidence")
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
                block_tokens = count_tokens(block)
                if total + block_tokens > budget:
                    omitted += 1
                    continue
                blocks.append(block)
                total += block_tokens
        if omitted:
            blocks.append(
                f"[上下文预算提示] 另有 {omitted} 个材料单元未进入本次 LLM prompt;"
                "原始 Unit 已完整入库,后续可通过检索再次召回。"
            )
        return "\n\n".join(blocks)




def _has_text_units(units_by_material: dict[int, list[Unit]]) -> bool:
    return any((unit.content or "").strip() for units in units_by_material.values() for unit in units)


def _build_unit_batches(
    units_by_material: dict[int, list[Unit]],
    filenames: dict[int, str],
    pass_name: str,
    known_contents: set[str] | None = None,
) -> list[tuple[str, dict]]:
    from app.runtime_profiles import stage_input_budget_tokens

    budget = stage_input_budget_tokens("evidence")
    batches: list[tuple[str, dict]] = []
    current: list[str] = []
    current_tokens = 0
    current_materials: list[int] = []
    current_units: dict[int, int] = {}
    known = sorted(known_contents or set())[:20]
    prefix = ""
    if known:
        prefix = "已知事实摘要(用于避免重复,不是排除边界):\n" + "\n".join(f"- {item}" for item in known) + "\n\n"
    for material_id, units in units_by_material.items():
        for unit in units:
            text = (unit.content or "").strip()
            if not text:
                continue
            label = f"[U{unit.id} | {filenames.get(material_id, '?')}"
            if unit.page is not None:
                label += f" | 第{unit.page}页"
            label += f"]\n{text}"
            label_tokens = count_tokens(label)
            if current and current_tokens + label_tokens > budget:
                batches.append(_finish_unit_batch(prefix, current, current_materials, current_units, pass_name))
                current, current_tokens, current_materials, current_units = [], 0, [], {}
            current.append(label)
            current_tokens += label_tokens
            if material_id not in current_materials:
                current_materials.append(material_id)
            current_units[material_id] = current_units.get(material_id, 0) + 1
    if current:
        batches.append(_finish_unit_batch(prefix, current, current_materials, current_units, pass_name))
    return batches



def _finish_unit_batch(prefix: str, blocks: list[str], material_ids: list[int],
                       unit_counts: dict[int, int], pass_name: str) -> tuple[str, dict]:
    text = prefix + "相关材料片段:\n" + "\n\n".join(blocks)
    meta = {
        "pass": pass_name,
        "batch_material_ids": list(material_ids),
        "batch_material_units": dict(unit_counts),
        "batch_material_count": len(material_ids),
        "retrieved_unit_count": sum(unit_counts.values()),
        "retrieved_chars": sum(len(block) for block in blocks),
        "context_chars": len(text),
        "truncated": False,
        "recovery": True,
    }
    return text, meta

def fact_exists(content: str, task_id: str = "", assertion_mode: str = "unknown",
                semantic_profile: dict | None = None) -> bool:
    with session_scope() as s:
        query = select(ORMFact.c.id).where(
            ORMFact.c.content == content,
            ORMFact.c.assertion_mode == normalize_assertion_mode(assertion_mode),
            ORMFact.c.semantic_profile_json == semantic_profile_json(semantic_profile),
        )
        if task_id:
            query = query.where(ORMFact.c.task_id == task_id)
        return s.execute(query).first() is not None


def save_fact_with_evidence(fact: Fact, evidence_list: list[Evidence], task_id: str = "",
                            origin_call_id: str = "") -> int:
    source_locations = sorted({
        f"{ev.source_file}|{ev.page or 0}|{ev.paragraph or 0}" for ev in evidence_list
    })
    stable_key = stable_hash({"dimension": fact.dimension, "sources": source_locations})
    run_id = str(current_context().get("run_id") or "")
    with session_scope() as s:
        result = s.execute(
            ORMFact.insert().values(
                content=fact.content, dimension=fact.dimension, source_level=fact.source_level,
                fact_type=fact.fact_type,
                assertion_mode=normalize_assertion_mode(fact.assertion_mode),
                assertion_mode_raw=normalize_assertion_semantics(
                    fact.assertion_mode, fact.assertion_mode_raw,
                )[1],
                semantic_profile_json=semantic_profile_json(fact.semantic_profile),
                need_id=fact.need_id,
                evidence_ids="[]", conflict_ids="[]", task_id=task_id, origin_call_id=origin_call_id,
                stable_key=stable_key, lifecycle_status="active", introduced_run_id=run_id,
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
    fact_id = int(claim.fact_id) if claim.fact_id is not None else None
    if fact_id is not None and fact_id <= 0:
        raise ValueError("Claim fact_id must reference a persisted fact")
    if status == "promoted" and fact_id is None:
        raise ValueError("A promoted claim must reference a persisted fact")
    with session_scope() as s:
        result = s.execute(
            ORMClaim.insert().values(
                fact_id=fact_id, material_id=claim.material_id,
                unit_id=claim.unit_id,
                content=claim.content, quote=claim.quote, source=claim.source,
                fact_type=claim.fact_type,
                assertion_mode=normalize_assertion_mode(claim.assertion_mode),
                assertion_mode_raw=normalize_assertion_semantics(
                    claim.assertion_mode, claim.assertion_mode_raw,
                )[1],
                semantic_profile_json=semantic_profile_json(claim.semantic_profile),
                dimension=claim.dimension, need_id=claim.need_id,
                status=status, task_id=task_id, origin_call_id=origin_call_id,
            )
        )
        return int(result.inserted_primary_key[0])


def load_claims(task_id: str = "") -> list[dict]:
    """当前任务的全部陈述(含未提升的 pending)。"""
    return load_claims_for_tasks([task_id] if task_id else [])


def load_claims_for_tasks(task_ids: list[str]) -> list[dict]:
    """Load claims from explicit task scope only; never fall back to whole DB."""
    cleaned = [str(tid) for tid in task_ids if str(tid or "").strip()]
    with session_scope() as s:
        query = select(ORMClaim)
        if cleaned:
            query = query.where(ORMClaim.c.task_id.in_(cleaned))
        else:
            query = query.where(ORMClaim.c.task_id == "__no_task__")
        rows = s.execute(query.order_by(ORMClaim.c.id)).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["semantic_profile"] = normalize_semantic_profile(item.pop("semantic_profile_json", None))
        result.append(item)
    return result


def save_conflict(conflict: Conflict, origin_call_id: str = "", task_id: str = "") -> int:
    with session_scope() as s:
        result = s.execute(
            ORMConflict.insert().values(
                fact_key=conflict.fact_key,
                entries=json.dumps(conflict.entries, ensure_ascii=False),
                claim_ids=json.dumps(conflict.claim_ids),
                conflict_type=conflict.conflict_type,
                reason=conflict.reason,
                confidence=conflict.confidence,
                status=conflict.status, task_id=task_id, origin_call_id=origin_call_id,
            )
        )
        return int(result.inserted_primary_key[0])


def load_conflict_records(conflict_ids: list[int]) -> list[dict]:
    """Load conflicts with deterministic, current provenance for API display."""
    ids = [int(value) for value in conflict_ids if str(value).isdigit()]
    if not ids:
        return []
    with session_scope() as session:
        rows = session.execute(
            select(ORMConflict).where(ORMConflict.c.id.in_(ids)).order_by(ORMConflict.c.id)
        ).mappings().all()
        all_claim_ids: set[int] = set()
        parsed_ids: dict[int, list[int]] = {}
        for row in rows:
            try:
                values = [int(value) for value in json.loads(row.get("claim_ids") or "[]")]
            except (TypeError, ValueError, json.JSONDecodeError):
                values = []
            parsed_ids[int(row["id"])] = values
            all_claim_ids.update(values)
        claim_rows = session.execute(
            select(ORMClaim).where(ORMClaim.c.id.in_(all_claim_ids))
        ).mappings().all() if all_claim_ids else []
    claims_by_id = {int(row["id"]): dict(row) for row in claim_rows}
    records: list[dict] = []
    for row in rows:
        claim_ids = parsed_ids.get(int(row["id"]), [])
        entries = _conflict_entries(claim_ids, claims_by_id) if claim_ids else []
        if not entries:
            try:
                entries = json.loads(row.get("entries") or "[]")
            except (TypeError, json.JSONDecodeError):
                entries = []
        records.append({
            "id": int(row["id"]),
            "fact_key": str(row.get("fact_key") or ""),
            "claim_ids": claim_ids,
            "entries": entries,
            "conflict_type": _normalize_conflict_type(row.get("conflict_type")),
            "reason": str(row.get("reason") or ""),
            "confidence": _normalize_confidence(row.get("confidence")),
            "status": str(row.get("status") or "unresolved"),
        })
    return records


def load_evidence_quotes(fact_id: int) -> str:
    with session_scope() as s:
        rows = s.execute(
            select(ORMEvidence.c.source_file).where(ORMEvidence.c.fact_id == fact_id)
        ).all()
    return ", ".join(str(row[0]) for row in rows)

__all__ = ['bind_sources', 'EvidenceAgent', 'fact_exists', 'save_fact_with_evidence', 'save_claim', 'load_claims', 'load_claims_for_tasks', 'save_conflict', 'load_conflict_records', 'load_evidence_quotes']
