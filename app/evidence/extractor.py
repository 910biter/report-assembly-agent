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
import time
import threading
from contextlib import contextmanager
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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models import Claim, Conflict, Evidence, Fact, Unit
from app.retrieval import vector_store
from app.retrieval.embedder import embed_texts
from app.token_monitor import current_context, log_pipeline_event, update_call_funnel, update_call_metrics, update_call_products
from app.evidence.outcomes import EvidenceBatchStatus, failure_reason, summarize
from app.evidence.identity import match_propositions
from app.task_artifacts import save_task_artifact

_FACT_PERSIST_LOCK = threading.RLock()
# Per-worker transaction; never share a SQLAlchemy Session between threads.
_ACTIVE_FACT_BATCH: contextvars.ContextVar[tuple | None] = contextvars.ContextVar(
    "active_fact_batch", default=None,
)


@contextmanager
def _fact_batch_transaction(items: list[dict], task_id: str):
    """One transaction instead of 2-4 sessions per claim.

    Serialize persistence across Evidence workers. Preserve every source Claim.
    """
    if not task_id:  # Keep standalone/legacy extraction helpers unchanged.
        yield
        return
    with _FACT_PERSIST_LOCK:
        with session_scope() as session:
            token = _ACTIVE_FACT_BATCH.set((session, task_id))
            try:
                yield
            finally:
                _ACTIVE_FACT_BATCH.reset(token)


@contextmanager
def _write_session():
    active = _ACTIVE_FACT_BATCH.get()
    if active:
        yield active[0]
    else:
        with session_scope() as session:
            yield session


class SourceUnitIndex:
    """Read-only normalized source index shared between concurrent LLM batches.

    A quote must match the labeled Unit (if provided); unknown Unit ids may
    fall back to matching only Units present in this particular prompt.
    """

    def __init__(self, units_by_material: dict[int, list[Unit]], filenames: dict[int, str]):
        self.filenames = filenames
        self.by_id: dict[int, tuple[int, Unit, str]] = {}
        self.ordered: list[tuple[int, Unit, str]] = []
        self.raw_positions = {}
        for material_id, units in units_by_material.items():
            for unit in units:
                normalized = _normalize(unit.content or "")
                record = (int(material_id), unit, normalized)
                self.ordered.append(record)
                if unit.id is not None:
                    self.by_id[int(unit.id)] = record
                    self.raw_positions[int(unit.id)] = [index for index, char in enumerate(unit.content or "") if not char.isspace()]

    def restricted(self, allowed_ids: set[int]) -> "SourceUnitIndex":
        """Create one lightweight prompt-scoped view; reuse normalized strings."""
        view = object.__new__(SourceUnitIndex)
        view.filenames = self.filenames
        view.raw_positions = self.raw_positions
        view.by_id = {key: value for key, value in self.by_id.items() if key in allowed_ids}
        view.ordered = [record for record in self.ordered if record[1].id in allowed_ids]
        return view

    def visible(self, text: str) -> "SourceUnitIndex":
        view = self.restricted(set())
        view.spans = {}
        markers = list(re.finditer(r"(?m)^\[U(\d+)[^\n]*\]\n", text))
        for position, marker in enumerate(markers):
            uid = int(marker.group(1))
            record = self.by_id.get(uid)
            if record is None:
                continue
            end = markers[position + 1].start() if position + 1 < len(markers) else len(text)
            content = re.split(r"\[(?:上下文覆盖|上下文预算提示)\]", text[marker.end():end], maxsplit=1)[0].strip()
            normalized = _normalize(content)
            offset = re.search(r"\boffset=(\d+)\b", marker.group(0))
            start = int(offset.group(1)) if offset else record[2].find(normalized) if normalized else -1
            if not normalized or record[2][start:start + len(normalized)] != normalized:
                continue
            if start < 0:
                continue
            view.by_id[uid] = record
            view.spans.setdefault(uid, []).append({
                "start": start, "end": start + len(normalized),
                "text": content, "segment_id": stable_hash({"unit_id": uid, "start": start, "end": start + len(normalized)}),
            })
        view.ordered = list(view.by_id.values())
        return view

    def segments(self) -> list[dict]:
        return [{"unit_id": uid, **{key: value for key, value in span.items() if key != "text"}}
                for uid, spans in getattr(self, "spans", {}).items() for span in spans]

    def bind(self, quote: str, unit_id: int | None = None,
             allowed_ids: set[int] | None = None) -> list[Evidence]:
        allowed = allowed_ids
        if allowed is not None and unit_id is not None and unit_id not in allowed:
            return []

        def record_to_evidence(record, snippet):
            material_id, unit, _normalized = record
            spans = getattr(self, "spans", {}).get(unit.id, [])
            normalized_quote = _normalize(snippet)
            matches = []
            for span in spans:
                at = _normalize(span["text"]).find(normalized_quote)
                if at >= 0:
                    matches.append({
                        "segment_id": span["segment_id"], "visible_start": span["start"],
                        "visible_end": span["end"], "quote_start": span["start"] + at,
                        "quote_end": span["start"] + at + len(normalized_quote),
                        "original_quote_start": self.raw_positions[unit.id][span["start"] + at],
                        "original_quote_end": self.raw_positions[unit.id][span["start"] + at + len(normalized_quote) - 1] + 1,
                        "original_visible_start": self.raw_positions[unit.id][span["start"]],
                        "original_visible_end": self.raw_positions[unit.id][span["end"] - 1] + 1,
                    })
                    break
            if hasattr(self, "spans") and not matches:
                return None
            return Evidence(
                fact_id=None, material_id=material_id, unit_id=unit.id or 0,
                source_file=self.filenames.get(material_id, ""), page=unit.page,
                paragraph=unit.paragraph, quote=snippet,
                visibility={"offset_basis": "normalized_unit_text", "spans": matches,
                            "origin_call_id": "", "quote_kind": "continuous"},
            )

        lookup = self.ordered if allowed is None else [
            record for record in self.ordered if record[1].id in allowed
        ]
        if unit_id and unit_id in self.by_id:
            # A valid but unrelated ID is never allowed to promote a quote.
            if allowed is not None and unit_id not in allowed:
                return []
            record = self.by_id[unit_id]
            normalized_quote = _normalize(quote)
            evidence = record_to_evidence(record, quote) if normalized_quote and normalized_quote in record[2] else None
            if evidence is not None:
                return [evidence]
            # Discontinuous quotations must still stay inside the declared Unit.
            lookup = [record]

        def find(fragment):
            return next((record for record in lookup if fragment and (
                any(fragment in _normalize(span["text"]) for span in self.spans.get(record[1].id, []))
                if hasattr(self, "spans") else fragment in record[2]
            )), None)

        normalized = _normalize(quote)
        hit = find(normalized)
        if hit:
            return [record_to_evidence(hit, quote)]
        pieces = _split_quotes(quote)
        evidence = []
        for piece in pieces:
            hit = find(_normalize(piece))
            if hit is None:
                return []
            bound = record_to_evidence(hit, piece)
            if bound is None:
                return []
            bound.visibility["quote_kind"] = "multiple_source_spans"
            evidence.append(bound)
        return evidence



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

_SYSTEM = """你是材料陈述提取员。逐个可见材料单元提取可回到原文核验的 Claim。
只输出 JSON:
{"claims": [{"content": "保留原文时态、条件和语气的陈述", "unit_id": 材料单元编号, "short_quote": "足以核验陈述的原文片段", "fact_type": "开放标签", "assertion_mode": "reported|prescriptive|prospective|conditional|opinion|unknown", "assertion_mode_raw": "粗粒度协议的原文描述", "semantic_profile": {"facets": [], "attribution": "原文明确的归属（仅相关时）", "conditions": [], "scope_qualifiers": []}}, ...]}

提取方法:
1. 以单条来源陈述为单位写 content。逐条核对同一对象的主体、动作、时间、数值、条件和范围；同段出现多个对象或事件时分别陈述。原文为目标、计划、观点或假设时，写成带来源归属和相应语气的陈述；已经发生的行为才写成已发生。
2. 从本次可见文本选取对应的 U 编号作 unit_id，并逐字复制能核验整条 content 的 short_quote。引文尽量短，但要覆盖关键对象和限定；较长陈述可拆成多条。证据不足的内容留在未提取范围，交由后续环节处理。
3. 分析维度用于确定优先级，不构成排除条件。与用户目标相关的关键机制、数据、时间线、比较要素和边界应尽量覆盖；来源观点只记录为带归属的来源观点，分析推论留给 Analysis。
4. fact_type 使用材料适用的开放标签。assertion_mode 是粗粒度协议字段，assertion_mode_raw 与 semantic_profile 可保留更细的来源语义；未知时用 unknown，不把它解释成“其他”。
5. semantic_profile 只记录 content 尚未说明、但影响真实性的时间、条件、归属、实现状态和范围。facets 按需使用 {"axis":"开放轴名","label":"简短判断"}；相关字段缺失时省略，不机械填满。分类描述的是材料如何陈述，不等于系统独立核实。
6. 陈述性质依据该句及必要邻近上下文判断，材料标题、文件名和领域词仅供定位。只返回协议字段；页码和文件名由系统根据 unit_id 回查。"""

_CONFLICT_SYSTEM = """你是证据关系核验员。按同一事项逐对比较本次给定的 Claim。只输出 JSON:
{"conflicts": [{"fact_key": "核验主题", "claim_ids": [说法A编号, 说法B编号], "conflict_type": "direct_contradiction/temporal_difference/scope_difference/metric_difference/qualification/needs_verification", "reason": "明确说明A与B在哪个事实点上相同或不同", "confidence": "high/medium/low"}]}
比较方法:
1. 先核对主体、事项、时间、范围、统计口径以及 semantic_profile 中的条件、归属和实现状态。上述口径可比且结论互斥时标 direct_contradiction；不同时间、范围或统计定义分别用 temporal_difference、scope_difference、metric_difference。
2. 原则与例外、总体与局部限制、结论与适用条件可用 qualification 表达。事实、设计目标、要求、建议和预测按各自陈述性质比较；依据不足以稳定判定时用 needs_verification。
3. 每条结果只含两个本次输入的 Claim 编号，并在 reason 写明相同点和差异；同组多对关系逐对返回。没有需要核验的关系时返回空数组。来源位置由系统按编号回查。"""

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

_AUDIT_SYSTEM = """你是证据覆盖审计员。逐个判断本次列出的 Fact 是否能明确、具体地回答对应 Evidence Need。侧面提及、笼统概括或当前未覆盖的 Need 列入 uncovered。只输出 JSON:
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
    return sorted({int(value) for value in re.findall(r"\[U(\d+)(?:\s*\||\s+[^\]\n]+\])", str(text or ""))})


def _split_quotes(quote: str) -> list[str]:
    parts = re.split(r"\.{3,}|…+|(?<=[。！？!?；;])", quote)
    return [p.strip() for p in parts if p.strip()]


def bind_sources(quote: str, units_by_material: dict[int, list[Unit]], filenames: dict[int, str],
                 unit_id: int | None = None, *, index: SourceUnitIndex | None = None,
                 allowed_ids: set[int] | None = None) -> list[Evidence]:
    """Bind to original Unit, with optional indexed and prompt-scoped lookup."""
    return (index or SourceUnitIndex(units_by_material, filenames)).bind(
        quote, unit_id=unit_id, allowed_ids=allowed_ids,
    )


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


def _split_oversized_unit(block: str, max_tokens: int) -> list[str]:
    """Break one oversized labeled Unit without changing its immutable Unit id.

    Overlap helps short verbatim citations straddling a split boundary; the
    bound quote is always verified against the stored full original Unit.
    """
    first, sep, content = block.partition("\n")
    if not sep or not first.startswith("[U"):
        return [block]  # Cannot safely create a new source marker.
    remaining = content
    offset_match = re.search(r"\s*\| offset=(\d+)", first)
    base_offset = int(offset_match.group(1)) if offset_match else 0
    first = re.sub(r"\s*\| offset=\d+", "", first)
    raw_offset = 0
    def label():
        return first[:-1] + f" | offset={base_offset + len(_normalize(content[:raw_offset]))}]"
    result = []
    while remaining:
        current_label = label()
        if count_tokens(current_label + "\n" + remaining) <= max_tokens:
            result.append(current_label + "\n" + remaining)
            break
        # Count with real tokenizer when available; binary search for a
        # bounded prefix, not character-count assumptions on multilingual text.
        lo, hi = 1, len(remaining)
        while lo < hi:
            middle = (lo + hi + 1) // 2
            if count_tokens(current_label + "\n" + remaining[:middle]) <= max_tokens:
                lo = middle
            else:
                hi = middle - 1
        cut = lo
        if cut <= 1 or count_tokens(current_label + "\n" + remaining[:cut]) > max_tokens:
            return [block]  # Marker itself exceeds budget: explicit audit later.
        # Prefer a natural sentence/paragraph boundary within the trailing part.
        tail = remaining[max(0, cut - 100):cut]
        boundary = max((tail.rfind(ch) for ch in "。！？!?；;\n"), default=-1)
        if boundary >= 20:
            cut = max(1, cut - len(tail) + boundary + 1)
        result.append(current_label + "\n" + remaining[:cut])
        overlap = min(40, max(0, cut - 1))
        next_text = remaining[cut - overlap:]
        if len(next_text) >= len(remaining):
            next_text = remaining[cut:]
        raw_offset += len(remaining) - len(next_text)
        remaining = next_text
    return result


def _split_unit_blocks(material_text: str, max_tokens: int) -> list[str]:
    """Pack labeled original Units into bounded extraction batches.

    Large stored Units remain unchanged: only the LLM prompt view is split.
    """
    original = str(material_text or "")
    text = original.strip()
    limit = max(1, int(max_tokens))
    if not text or count_tokens(text) <= limit:
        return [original]
    marker = "相关材料片段:\n"
    prefix = marker if text.startswith(marker) else ""
    if prefix:
        text = text[len(marker):]
    blocks = [part.strip() for part in re.split(r"(?m)(?=^\[U\d+[^\n]*\]\n)", text) if part.strip()]
    # Expand any long individual Unit; never silently drop the whole source.
    expanded = []
    for block in blocks:
        if count_tokens(block) > limit:
            # Preserve a short, indivisible original Unit even when a repeated
            # prompt label pushes a tiny synthetic budget over its limit.
            expanded.extend(_split_oversized_unit(block, max(1, limit - count_tokens(prefix))))
        else:
            expanded.append(block)
    if not expanded:
        return [original]
    chunks: list[str] = []
    current: list[str] = []
    for block in expanded:
        candidate = prefix + "\n\n".join([*current, block])
        if current and count_tokens(candidate) > limit:
            chunks.append(prefix + "\n\n".join(current))
            current = []
        current.append(block)
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

    def _record_outcome(self, task_id: str, batch_index: int, round_tag: int,
                        candidates: list[dict], processed: list[dict], *,
                        reason: str = "", no_evidence: bool = False, fact_ids=()) -> None:
        processed_ids = {item["segment_id"] for item in processed}
        remaining = [item for item in candidates if item["segment_id"] not in processed_ids]
        status = (EvidenceBatchStatus.PARTIAL if (processed or fact_ids) and (remaining or reason) else
                  EvidenceBatchStatus.FAILED if remaining or reason else
                  EvidenceBatchStatus.NO_EVIDENCE if no_evidence else EvidenceBatchStatus.COMPLETED)
        result = {
            "status": status.value, "batch": batch_index, "round": round_tag,
            "processed_segments": processed, "unprocessed_segments": remaining,
            "failure_reason": reason, "retryable": bool(remaining or reason),
            "fact_ids": list(fact_ids), "call_id": self.last_call_id,
        }
        self.batch_outcomes = [*getattr(self, "batch_outcomes", []), result]
        if task_id:
            save_task_artifact(task_id, "evidence_batch", {
                "round": round_tag, "batch": batch_index, "segments": candidates,
            }, result, status=status.value, run_id=str(current_context().get("run_id") or ""))

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
        clock = time.perf_counter
        started = clock()
        needs = _normalize_needs(needs)
        # Pre-normalize source text once for all passes and gap retries.
        self._source_index = SourceUnitIndex(units_by_material, filenames)
        index_seconds = round(clock() - started, 4)
        self.batch_failures = []
        self.batch_outcomes = []
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
            for produced in self._process_batches(
                _build_unit_batches(units_by_material, filenames, pass_name="full_material"),
                needs, units_by_material, filenames, task_id,
            ):
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
        first_pass_seconds = round(clock() - started - index_seconds, 4)
        report_phase(2)

        # Coverage Audit + Iterative Retrieval:缺口补检,足够即停
        audit_started = clock()
        gaps = self._coverage_audit(facts, needs, task_id)
        initial_audit_seconds = round(clock() - audit_started, 4)
        # FIRE 式迭代:缺什么补什么,足够即停。
        # 停止条件:①无缺口 ②本轮未产出新事实(材料已无新证据)→ information_gap 接受
        # 安全 cap = 资源保护(工程限制,非业务规则),防止异常死循环
        round_index = 0
        iterative_started = clock()
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
            **summarize(self.batch_outcomes),
            "failed_batches": list(self.batch_failures),
            "coverage": dict(self.coverage_audit_status),
            "uncovered_needs": list(gaps or []),
            "needs": needs,
            "timing_seconds": {
                "source_index": index_seconds,
                "first_pass_including_recovery": first_pass_seconds,
                "initial_coverage_audit": initial_audit_seconds,
                "iterative_retrieval_and_audit": round(clock() - iterative_started, 4),
                "total": round(clock() - started, 4),
            },
        }
        if self.coverage_audit_status.get("status") == "unknown" and self.last_extraction_status["status"] == "completed":
            self.last_extraction_status["processing_status"] = "completed"
            self.last_extraction_status["status"] = "partial"
            self.last_extraction_status["failure_reason"] = "coverage_unverified"
        selected_ids = {segment["unit_id"] for batch in self.batch_outcomes
                        for segment in batch["processed_segments"] + batch["unprocessed_segments"]}
        all_ids = {int(unit.id) for units in units_by_material.values() for unit in units
                   if unit.id is not None and (unit.content or "").strip()}
        self.last_extraction_status["source_selection"] = {
            "candidate_unit_count": len(all_ids), "selected_unit_count": len(selected_ids),
            "unselected_unit_ids": sorted(all_ids - selected_ids),
            "reason": "retrieval_selection" if cm is not None else "full_material",
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
            worker._source_index = getattr(self, "_source_index", None)
            worker.batch_failures = []
            produced = worker._process_batch(
                needs, material_text, batch_meta, units_by_material, filenames,
                task_id, batch_index, len(batches), round_tag=round_tag,
            )
            return produced, worker.batch_failures, getattr(worker, "batch_outcomes", [])

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
        self.batch_outcomes = list(getattr(self, "batch_outcomes", []))
        for _facts, failures, batch_outcomes in outcomes:
            self.batch_failures.extend(failures)
            self.batch_outcomes.extend(batch_outcomes)
        return [produced for produced, _failures, _outcomes in outcomes]

    def _fit_material_batches(
        self, batches: list[tuple[str, dict]], needs: list[dict],
    ) -> list[tuple[str, dict]]:
        """Split only source batches that cannot fit without dropping whole Units."""
        from app.runtime_profiles import stage_input_budget_tokens

        need_tokens = count_tokens(self._build_need_block(needs))
        # Account for stage labels, instructions and system text not covered by
        # the configured fixed-overhead reserve on smaller model windows.
        from app.runtime_profiles import stage_profile
        extra_system = count_tokens(self.role)
        material_budget = max(
            1, stage_input_budget_tokens("evidence") - need_tokens - 512 - extra_system,
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
                       round_tag: int = 0, split_depth: int = 0,
                       split_budget: list[int] | None = None) -> list[Fact]:
        """单批处理:多维联合提取(全部 Needs)→ claims 解析 → quote 校验 → Fact 落库。

        输出格式:claims 每条带 need_id/need(模型标注归属),无标注则按相关性回退到首个 need。
        """
        needs_list = needs if isinstance(needs, list) else [needs]
        _assert_not_paused(task_id)
        source_index = getattr(self, "_source_index", None) or SourceUnitIndex(units_by_material, filenames)
        candidate_segments = source_index.visible(material_text).segments()
        self.last_call_id = ""  # Preflight failures must not inherit another batch's call ID.
        # All recursive children of one original batch consume the *same*
        # request budget. Depth alone cannot bound work: splitting a huge Unit
        # may yield >2 fragments and grow combinatorially on a weak model.
        if split_budget is None:
            split_budget = [max(1, int(settings.evidence_max_adaptive_split_calls))]
        if split_budget[0] <= 0:
            self.batch_failures = list(getattr(self, "batch_failures", []))
            self.batch_failures.append({
                "call_id": "", "round": round_tag, "batch": batch_index,
                "unit_ids": _unit_ids_in_text(material_text),
                "error": "EVIDENCE_ADAPTIVE_SPLIT_CALL_LIMIT",
            })
            self._record_outcome(task_id, batch_index, round_tag, candidate_segments, [], reason="recovery_budget_exhausted")
            return []
        split_budget[0] -= 1
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
            ContextSection("material_units", [part.strip() for part in re.split(
                r"(?m)(?=^\[U\d+[^\n]*\]\n)", material_text
            ) if part.strip()], weight=4, required_items=1),
        ], max(0, stage_input_budget_tokens("evidence") - count_tokens(self.role)))
        candidate_unit_ids = _unit_ids_in_text(material_text)
        input_unit_ids = _unit_ids_in_text(prompt)
        if _audit.get("sections", {}).get("evidence_needs", {}).get("omitted_items"):
            self._record_outcome(task_id, batch_index, round_tag, candidate_segments, [], reason="input_capacity")
            return []
        visible_segments = source_index.visible(prompt).segments()
        if {item["segment_id"] for item in candidate_segments} - {item["segment_id"] for item in visible_segments}:
            parts = _split_unit_blocks(material_text, max(1, count_tokens(material_text) // 2))
            if (split_depth < settings.evidence_max_adaptive_split_depth
                    and len(parts) > 1 and all(part != material_text for part in parts)):
                recovered = []
                for index, part in enumerate(parts, 1):
                    recovered.extend(self._process_batch(
                        needs, part, _split_batch_meta(batch_meta, part, adaptive_split="input_capacity"),
                        units_by_material, filenames, task_id, index, len(parts),
                        round_tag=round_tag, split_depth=split_depth + 1, split_budget=split_budget,
                    ))
                return recovered
            self._record_outcome(task_id, batch_index, round_tag, candidate_segments, [], reason="input_capacity")
            return []
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
        # If packing dropped every labeled source Unit, fail visibly instead of
        # producing zero Facts from an instruction-only request.
        if candidate_unit_ids and not input_unit_ids:
            self.batch_failures = list(getattr(self, "batch_failures", []))
            self.batch_failures.append({
                "call_id": "", "round": round_tag, "batch": batch_index,
                "unit_ids": candidate_unit_ids, "error": "EVIDENCE_INPUT_UNITS_NOT_FIT",
            })
            self._record_outcome(task_id, batch_index, round_tag, candidate_segments, [], reason="input_capacity")
            return []
        prompt_parts = {
            "needs": sum(len(n.get("need", "")) for n in needs_list),
            "material_context": len(material_text or ""),
            "system_prompt": len(self.role or _SYSTEM),
        }
        context_meta = dict(batch_meta)
        generation_started = time.perf_counter()
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
            if str(exc) in {"MODEL_OUTPUT_TRUNCATED", "MODEL_INPUT_OVER_BUDGET", "MODEL_CONTEXT_EXCEEDED"}:
                split_parts = _split_unit_blocks(
                    material_text, max(1, count_tokens(material_text) // 2),
                )
                if (split_depth < max(0, int(settings.evidence_max_adaptive_split_depth))
                        and len(split_parts) > 1
                        and all(part != material_text for part in split_parts)):
                    log_pipeline_event(
                        "evidence",
                        recovery_reason="output_truncated_split",
                        depth=split_depth + 1,
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
                            split_depth=split_depth + 1, split_budget=split_budget,
                        ))
                    return recovered
            self.batch_failures = list(getattr(self, "batch_failures", []))
            self.batch_failures.append({
                "call_id": call_id, "round": round_tag, "batch": batch_index,
                "unit_ids": candidate_unit_ids, "error": str(exc)[:300],
            })
            self._record_outcome(task_id, batch_index, round_tag, candidate_segments, [], reason=failure_reason(exc))
            return []
        finally:
            self._split_truncated_batch = False
        generation_seconds = time.perf_counter() - generation_started
        call_id = self.last_call_id
        produced_fact_ids: list[int] = []
        stored_chars = 0
        items = payload.get("claims") or payload.get("facts") or []
        model_claims = len(items) if isinstance(items, list) else 0
        items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        valid_field_claims = 0
        quote_bound_claims = 0
        duplicate_claims = 0
        pending_claims = 0
        field_chars = _claim_field_chars(items)
        contributed_materials: set[int] = set()
        contributed_units: set[int] = set()
        facts: list[Fact] = []
        material_scan: dict[int, dict] = {}
        allowed_unit_ids = set(input_unit_ids)
        batch_source_index = source_index.visible(prompt)
        bind_started = time.perf_counter()
        bound_items = {}
        identity_rows = []
        for index, item in enumerate(items):
            quote = str(item.get("short_quote") or item.get("quote") or "").strip()
            bound = bind_sources(quote, units_by_material, filenames,
                                 unit_id=_positive_unit_id(item.get("unit_id")),
                                 index=batch_source_index, allowed_ids=allowed_unit_ids)
            bound_items[index] = bound
            if bound and str(item.get("content") or "").strip():
                identity_rows.append({"key": f"c{index}", "content": str(item["content"]).strip(),
                                      "quotes": [ev.quote for ev in bound],
                                      "unit_ids": sorted({ev.unit_id for ev in bound})})
        source_bind_seconds = time.perf_counter() - bind_started
        existing = _identity_candidates(task_id, allowed_unit_ids) if identity_rows and task_id else []
        identities, identity_audit = match_propositions(identity_rows, existing)
        persisted_claim_ids = {}
        need_by_id = {
            int(need.get("need_id") or index): need
            for index, need in enumerate(needs_list, start=1)
        }
        # Atomic within each batch; preserve pending claims and provenance.
        persistence_started = time.perf_counter()
        with _fact_batch_transaction(items, task_id):
            for item_index, item in enumerate(items):
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
                if need_idx not in need_by_id:
                    need_idx = 0
                dimension = need_by_id[need_idx].get("dimension", "开放发现") if need_idx else "开放发现"
                evidence_list = bound_items[item_index]
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
                    target = identities.get(f"c{item_index}", "")
                    fact_id = int(target[1:]) if target.startswith("f") else persisted_claim_ids.get(target)
                    if fact_id:
                        merge_fact_evidence(fact_id, evidence_list, task_id, call_id)
                        claim.fact_id = fact_id
                        save_claim(claim, status="promoted", origin_call_id=call_id, task_id=task_id)
                        persisted_claim_ids[f"c{item_index}"] = fact_id
                        duplicate_claims += 1
                        continue
                    fact.id = save_fact_with_evidence(fact, evidence_list, task_id, origin_call_id=call_id)
                    persisted_claim_ids[f"c{item_index}"] = fact.id
                facts.append(fact)
                produced_fact_ids.append(int(fact.id))
                stored_chars += len(fact.content or "") + sum(len(ev.quote or "") for ev in evidence_list)
                claim.fact_id = fact.id
                save_claim(claim, status="promoted", origin_call_id=call_id, task_id=task_id)
                for ev in evidence_list:
                    scan = material_scan.setdefault(int(ev.material_id), {"scanned": True, "units_selected": 0, "fact_count": 0})
                    scan["fact_count"] += 1
        persistence_seconds = time.perf_counter() - persistence_started
        for mid, unit_count in (batch_meta.get("batch_material_units") or {}).items():
            scan = material_scan.setdefault(int(mid), {"scanned": True, "units_selected": 0, "fact_count": 0})
            scan["units_selected"] = int(unit_count)
        update_call_products(call_id, produced_fact_ids=produced_fact_ids)
        update_call_metrics(call_id, stored_chars=stored_chars)
        update_call_funnel(
            call_id,
            proposition_identity=identity_audit,
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
            generation_seconds=round(generation_seconds, 4),
            fact_persistence_seconds=round(persistence_seconds, 4),
            source_bind_seconds=round(source_bind_seconds, 4),
            field_chars=field_chars,
            batch=batch_index,
            batch_count=batch_count,
        )
        if material_scan:
            # One bulk telemetry upsert for all materials in this extraction
            # batch, not one PostgreSQL transaction per material.
            self._record_material_scan(task_id, f"need_pass_{round_tag or 0}", material_scan)
        _index_fact_vectors(facts, task_id)
        malformed = not isinstance(payload.get("claims", payload.get("facts")), list)
        rejected = quote_bound_claims != valid_field_claims or model_claims != valid_field_claims
        processed = [] if malformed or rejected else batch_source_index.segments()
        self._record_outcome(task_id, batch_index, round_tag, candidate_segments, processed,
                             reason="invalid_output" if malformed else "source_validation_failed" if rejected else "",
                             no_evidence=not model_claims and not malformed, fact_ids=produced_fact_ids)
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
        need_facts: dict[int, list[str]] = {need_id: [] for need_id in need_by_id}
        for index, fact in enumerate(facts, start=1):
            need_id = int(getattr(fact, "need_id", 0) or 0)
            if need_id in need_facts:
                # Open discovery (need_id=0) never auto-supports a need.
                need_facts[need_id].append(f"[F{getattr(fact, 'id', index)}] {fact.content}")
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
            rows = [{
                "task_id": task_id, "material_id": int(mid), "dimension": dimension,
                "scanned": int(info.get("scanned", 1)),
                "units_selected": int(info.get("units_selected", 0)),
                "fact_count": int(info.get("fact_count", 0)),
            } for mid, info in material_scan.items()]
            if rows:
                statement = pg_insert(ORMMaterialScan).values(rows)
                statement = statement.on_conflict_do_update(
                    index_elements=[ORMMaterialScan.c.task_id, ORMMaterialScan.c.material_id, ORMMaterialScan.c.dimension],
                    set_={
                        "scanned": statement.excluded.scanned,
                        "units_selected": statement.excluded.units_selected,
                        "fact_count": statement.excluded.fact_count,
                    },
                )
                with session_scope() as session:
                    session.execute(statement)
        except Exception:
            # Scan status is telemetry, not an authority over committed facts.
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
            if claims_by_id[claim_ids[0]]["fact_id"] == claims_by_id[claim_ids[1]]["fact_id"]:
                # Classification variants of one proposition are not two facts
                # contradicting each other; both remain in the Claim history.
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

def _index_fact_vectors(facts: list[Fact], task_id: str) -> None:
    """Optional fact indexing; skip CPU encoding entirely without Qdrant."""
    if not facts or not getattr(vector_store, "enabled", False):
        return
    try:
        vectors = embed_texts([fact.content for fact in facts])
        for fact, vector in zip(facts, vectors):
            vector_store.save_fact_vector(fact.id, vector, task_id=task_id)
    except Exception:
        # Canonical facts and provenance are already committed; lexical fallback
        # must remain available even when vector indexing degrades.
        pass




def _identity_candidates(task_id: str, unit_ids: set[int]) -> list[dict]:
    with session_scope() as session:
        rows = session.execute(select(
            ORMFact.c.id, ORMFact.c.content, ORMEvidence.c.unit_id, ORMEvidence.c.quote,
        ).join(ORMEvidence, ORMEvidence.c.fact_id == ORMFact.c.id).where(
            ORMFact.c.task_id == task_id, ORMFact.c.lifecycle_status == "active",
            ORMEvidence.c.unit_id.in_(unit_ids),
        ).order_by(ORMFact.c.id, ORMEvidence.c.id)).all()
    candidates = {}
    for row in rows:
        record = candidates.setdefault(row.id, {"key": f"f{row.id}", "content": row.content,
                                                "unit_ids": [], "quotes": []})
        record["unit_ids"].append(row.unit_id)
        record["quotes"].append(row.quote)
    return list(candidates.values())


def merge_fact_evidence(fact_id: int, evidence_list: list[Evidence], task_id: str, call_id: str) -> None:
    """Append provenance without overwriting the canonical semantic judgment."""
    with _write_session() as session:
        row = session.execute(select(ORMFact).where(
            ORMFact.c.id == fact_id, ORMFact.c.task_id == task_id,
            ORMFact.c.lifecycle_status == "active",
        ).with_for_update()).mappings().first()
        if row is None:
            raise ValueError("FACT_IDENTITY_TARGET_UNAVAILABLE")
        existing = session.execute(select(ORMEvidence.c.id, ORMEvidence.c.unit_id, ORMEvidence.c.quote)
                                   .where(ORMEvidence.c.fact_id == fact_id)).all()
        keys = {(ev.unit_id, ev.quote) for ev in existing}
        ids = [ev.id for ev in existing]
        for ev in evidence_list:
            if (ev.unit_id, ev.quote) in keys:
                continue
            result = session.execute(ORMEvidence.insert().values(
                fact_id=fact_id, material_id=ev.material_id, unit_id=ev.unit_id,
                source_file=ev.source_file, page=ev.page, paragraph=ev.paragraph, quote=ev.quote,
                visibility_json=json.dumps({**ev.visibility, "origin_call_id": call_id}, ensure_ascii=False),
            ))
            ids.append(int(result.inserted_primary_key[0]))
            keys.add((ev.unit_id, ev.quote))
        session.execute(update(ORMFact).where(ORMFact.c.id == fact_id).values(evidence_ids=json.dumps(ids)))


def save_fact_with_evidence(fact: Fact, evidence_list: list[Evidence], task_id: str = "",
                            origin_call_id: str = "") -> int:
    source_locations = sorted({
        f"{ev.source_file}|{ev.page or 0}|{ev.paragraph or 0}" for ev in evidence_list
    })
    stable_key = stable_hash({"dimension": fact.dimension, "sources": source_locations})
    run_id = str(current_context().get("run_id") or "")
    with _write_session() as s:
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
                    visibility_json=json.dumps({**ev.visibility, "origin_call_id": origin_call_id}, ensure_ascii=False),
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
    with _write_session() as s:
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

__all__ = ['bind_sources', 'EvidenceAgent', 'save_fact_with_evidence', 'save_claim', 'load_claims', 'load_claims_for_tasks', 'save_conflict', 'load_conflict_records', 'load_evidence_quotes']
