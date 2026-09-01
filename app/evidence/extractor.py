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
from app.infrastructure.orm import ORMFact, ORMEvidence, ORMClaim, ORMConflict
from sqlalchemy import select, update
from app.models import Claim, Conflict, Evidence, Fact, Unit
from app.retrieval import vector_store
from app.retrieval.embedder import embed_texts
from app.token_monitor import current_context, log_pipeline_event, update_call_funnel, update_call_metrics, update_call_products

_FACT_TYPES = ("EVENT", "PERSON", "LOCATION", "TIME", "NUMBER", "STATEMENT")
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

_CONFLICT_SYSTEM = """你是证据关系核验员。只比较给定 Claim 编号,判断同一事项的说法属于哪种关系。
严格输出 JSON,不要任何解释:
{"conflicts": [{"fact_key": "核验主题", "claim_ids": [说法A编号, 说法B编号], "conflict_type": "direct_contradiction/temporal_difference/scope_difference/metric_difference/qualification/needs_verification", "reason": "明确说明A与B在哪个事实点上相同或不同", "confidence": "high/medium/low"}]}
判定规则:
1. direct_contradiction 仅限主体、事项、时间、范围和指标口径可比,且结论不能同时成立。
2. 不同时间的变化是 temporal_difference;不同范围/对象是 scope_difference;不同统计定义是 metric_difference。
3. 总体可用与局部限制、原则与例外、结论与适用条件并存时是 qualification,不是直接矛盾。
4. 条件不足以确认可比性时标 needs_verification;没有任何需要核验的关系时输出空数组。
5. 每个结果必须且只能包含两个 Claim 编号。一个候选组存在多组关系时,拆成多个两两比较结果,禁止把3条以上说法放进同一结果。
6. 只能返回输入中真实存在的 Claim 编号;不要生成来源、页码、引文或改写陈述,这些由系统按编号回查。"""

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

        def run_one(batch_index: int, material_text: str, batch_meta: dict) -> list[Fact]:
            worker = EvidenceAgent()
            return worker._process_batch(
                needs, material_text, batch_meta, units_by_material, filenames,
                task_id, batch_index, len(batches), round_tag=round_tag,
            )

        if concurrency == 1 or len(batches) == 1:
            return [run_one(index, text, meta) for index, (text, meta) in enumerate(batches, start=1)]
        results: list[list[Fact] | None] = [None] * len(batches)
        with ThreadPoolExecutor(max_workers=min(concurrency, len(batches)), thread_name_prefix="evidence-batch") as pool:
            futures = []
            for index, (text, meta) in enumerate(batches, start=1):
                context = contextvars.copy_context()
                futures.append((index, pool.submit(context.run, run_one, index, text, meta)))
            for index, future in futures:
                results[index - 1] = future.result()
        return [item or [] for item in results]

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
            "如果材料覆盖多个平台/标准/攻击/机制,请分别抽取,不要合并成过度概括的一条。输出 JSON。"
        )
        from app.runtime_profiles import stage_input_budget_tokens
        prompt, _audit = build_prompt_from_sections("evidence", [
            ContextSection("instruction", [instruction], weight=5, required_items=1),
            ContextSection("evidence_needs", need_block.splitlines(), weight=5, required_items=1),
            ContextSection("material_insight", insight_block.splitlines(), weight=2, required_items=0),
            ContextSection("material_units", material_text.split("\n\n"), weight=4, required_items=1),
        ], stage_input_budget_tokens("evidence"))
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
            call_id = self.last_call_id
            update_call_funnel(
                call_id,
                dimension="first_pass" if not round_tag else f"iterative_r{round_tag}",
                context_meta=context_meta,
                prompt_parts=prompt_parts,
                prompt_chars=len(prompt),
                material_context_chars=len(material_text),
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
            evidence_list = bind_sources(quote, units_by_material, filenames, unit_id=unit_id)
            claim = Claim(
                material_id=evidence_list[0].material_id if evidence_list else 0,
                unit_id=unit_id,
                content=content, quote=quote,
                source=filenames.get(evidence_list[0].material_id, "") if evidence_list else "",
                fact_type=fact_type, dimension=dimension, need_id=need_idx,
            )
            if not evidence_list:
                with _FACT_PERSIST_LOCK:
                    if fact_exists(content, task_id):
                        duplicate_claims += 1
                        continue
                pending_claims += 1
                save_claim(claim, status="pending", origin_call_id=call_id, task_id=task_id)
                continue
            quote_bound_claims += 1
            contributed_materials.update(int(ev.material_id) for ev in evidence_list)
            contributed_units.update(int(ev.unit_id) for ev in evidence_list)
            fact = Fact(content=content, dimension=dimension, fact_type=fact_type, need_id=need_idx)
            # Check-and-insert is serialized within the process so concurrent
            # extraction batches cannot create duplicate task facts.
            with _FACT_PERSIST_LOCK:
                if fact_exists(content, task_id):
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
            from app.runtime_profiles import stage_profile
            payload = self.generate_json(
                prompt, system=_AUDIT_SYSTEM,
                max_tokens=stage_profile("qa").output_tokens,
            )
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
            f"{c['id']}. [{c.get('source', '')}|{c.get('fact_type', 'STATEMENT')}] {c['content']}"
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

def fact_exists(content: str, task_id: str = "") -> bool:
    with session_scope() as s:
        query = select(ORMFact.c.id).where(ORMFact.c.content == content)
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
                fact_type=fact.fact_type, need_id=fact.need_id,
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
                fact_type=claim.fact_type, dimension=claim.dimension, need_id=claim.need_id,
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
    return [dict(row) for row in rows]


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
