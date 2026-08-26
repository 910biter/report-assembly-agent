"""Context Manager: build minimal context by workflow stage with hybrid retrieval."""

from app.config import settings
from app.runtime_profiles import stage_input_budget_chars, stage_profile
from app.models import Unit
from app.retrieval.query_compiler import QueryCompiler, RetrievalQuery
from app.retrieval import embed_texts, vector_store
from app.retrieval.rag import hybrid_retrieve_units

def _fit(text: str, budget_chars: int) -> str:
    if len(text) <= budget_chars:
        return text
    return text[:budget_chars]


def _fit_with_meta(text: str, budget_chars: int) -> tuple[str, dict]:
    fitted = _fit(text, budget_chars)
    return fitted, {
        "raw_chars": len(text or ""),
        "context_chars": len(fitted or ""),
        "truncated": len(text or "") > len(fitted or ""),
        "budget_chars": int(budget_chars),
    }


def _text_similarity(left: str, right: str, n: int = 2) -> float:
    """Symmetric character n-gram overlap for deterministic near-duplicate control."""
    import re as _re

    a = _re.sub(r"\s+|[，。；：、,.!?！？:;]", "", left or "")
    b = _re.sub(r"\s+|[，。；：、,.!?！？:;]", "", right or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < n or len(b) < n:
        return 0.0
    grams_a = {a[index:index + n] for index in range(len(a) - n + 1)}
    grams_b = {b[index:index + n] for index in range(len(b) - n + 1)}
    union = grams_a | grams_b
    return len(grams_a & grams_b) / max(len(union), 1)


def _pack_complete_lines(lines: list[str], budget_chars: int, omitted_label: str) -> list[str]:
    """Pack complete semantic records; never cut the final prompt mid-record."""
    budget = max(0, int(budget_chars or 0))
    packed: list[str] = []
    used = 0
    omitted = 0
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line:
            continue
        cost = len(line) + 1
        if used + cost <= budget:
            packed.append(line)
            used += cost
        else:
            omitted += 1
    if omitted:
        marker = f"[覆盖说明] 另有 {omitted} {omitted_label}因当前物理上下文预算未展开，原始数据仍完整保留。"
        marker_cost = len(marker) + 1
        while packed and used + marker_cost > budget:
            removed = packed.pop()
            used -= len(removed) + 1
            omitted += 1
            marker = f"[覆盖说明] 另有 {omitted} {omitted_label}因当前物理上下文预算未展开，原始数据仍完整保留。"
            marker_cost = len(marker) + 1
        if marker_cost <= budget:
            packed.append(marker)
    return packed


def _unit_relevance(unit, query: str, keywords: list[str],
                    query_vector=None, unit_vectors: dict | None = None) -> float:
    """材料内 Unit 相关度:语义余弦(有向量时)+ 关键词 + 2-gram(确定性回退)。

    向量缺失时自动回退纯文本相关度(多语言/无向量场景)。
    """
    text = unit.content or ""
    if not text:
        return 0.0
    terms = [t for t in keywords if t and t in text]
    keyword_score = min(1.0, len(terms) / max(len(keywords), 1)) if keywords else 0.0
    lexical = keyword_score * 0.5 + _ngram_similarity(query, text) * 0.5
    if query_vector is not None and unit_vectors and unit.id in unit_vectors:
        vec = unit_vectors[unit.id]
        if len(vec) == len(query_vector):
            try:
                from app.retrieval.store import cosine
                return lexical * 0.5 + max(0.0, cosine(query_vector, vec)) * 0.5
            except Exception:
                pass
    return lexical


def _ngram_similarity(left: str, right: str, n: int = 2) -> float:
    import re as _re

    a = _re.sub(r"\s+", "", left or "")
    b = _re.sub(r"\s+", "", right or "")
    if len(a) < n or len(b) < n:
        return 0.0
    grams_a = {a[i:i + n] for i in range(len(a) - n + 1)}
    grams_b = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / max(len(grams_a), 1)


def _estimate_tokens(text: str) -> int:
    """token 估算:中文按 1 字符≈1 token(纠正此前 2 字符/token 的严重低估——
    那会导致装箱以为批次不大、实际却塞满窗口、把模型输出区挤没,提取出不了 JSON)。
    保守偏安全,让装箱后实际批次留在模型窗口内。"""
    return max(1, len(text or ""))


def _evidence_batch_budget() -> int:
    """单批可用 tokens = 模型窗口 - 输出预留 - 固定 prompt 开销 - 安全余量。
    额外再留 20% 余量(A 辅):防 token 估算偏差与输出抖动,确保模型有空间收尾输出完整 JSON。"""
    return max(1024, int(stage_profile("evidence").input_tokens * 0.8))


def _dynamic_cutoff(scores: list[float]) -> int:
    """动态 Top-K:相关度降序中找最大相邻缺口的自然边界。

    断点判据(无硬编码常数):最大缺口 > 平均缺口 → 断点在该缺口处;
    相关度平滑下降(无显著断点)→ 全部保留。
    """
    if len(scores) <= 1:
        return len(scores)
    gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
    mean_gap = sum(gaps) / len(gaps)
    max_gap = max(gaps)
    # 浮点精度保护(round 6 位消除 0.03 vs 0.030000000000000027 类噪声)
    if round(max_gap - mean_gap, 6) > 0:
        return gaps.index(max_gap) + 1
    return len(scores)


def _select_evidence_candidates(
    per_material: list[tuple[int, list[tuple[float, object]]]],
    token_budget: int,
) -> list[tuple[int, list[object]]]:
    """Select a source-diverse, utility-ranked set under a stage SLA budget.

    The first candidate from each represented material is protected so a large
    source cannot crowd out smaller sources. Remaining units compete globally
    by utility. Missing Evidence Needs are handled by the later gap pass rather
    than expanding a smooth score distribution to the entire corpus.
    """
    budget = max(1, int(token_budget or 0))
    selected: dict[int, list[object]] = {}
    selected_ids: set[int] = set()
    consumed = 0

    # Coverage seed: one best candidate per represented source.
    for material_id, ranked in per_material:
        if not ranked:
            continue
        _score, unit = ranked[0]
        unit_id = int(getattr(unit, "id", 0) or id(unit))
        if unit_id in selected_ids:
            continue
        selected.setdefault(material_id, []).append(unit)
        selected_ids.add(unit_id)
        consumed += _estimate_tokens(getattr(unit, "content", "") or "")

    remaining: list[tuple[float, int, object]] = []
    for material_id, ranked in per_material:
        for score, unit in ranked[1:]:
            unit_id = int(getattr(unit, "id", 0) or id(unit))
            if unit_id not in selected_ids:
                remaining.append((float(score), material_id, unit))
    remaining.sort(key=lambda item: item[0], reverse=True)

    for _score, material_id, unit in remaining:
        unit_tokens = _estimate_tokens(getattr(unit, "content", "") or "")
        if consumed + unit_tokens > budget:
            continue
        selected.setdefault(material_id, []).append(unit)
        selected_ids.add(int(getattr(unit, "id", 0) or id(unit)))
        consumed += unit_tokens

    return [
        (material_id, selected.get(material_id, []))
        for material_id, _ranked in per_material
        if selected.get(material_id)
    ]


class ContextManager:
    def __init__(self, task: dict, units_by_material: dict[int, list[Unit]] | None = None,
                 filenames: dict[int, str] | None = None):
        self.task = task
        self.units_by_material = units_by_material or {}
        self.filenames = filenames or {}
        self._query_compiler = QueryCompiler(task_id=str(task.get("id") or ""))
        self._unit_index: dict[int, tuple[int, Unit]] = {}
        for material_id, units in self.units_by_material.items():
            for unit in units:
                if unit.id is not None:
                    self._unit_index[unit.id] = (material_id, unit)

    def budget_chars(self, stage: str = "structured") -> int:
        """Return the stage contract's input budget within one physical window."""
        return stage_input_budget_chars(stage)

    def retrieve_units(self, query: str, top_k: int = 8,
                       keywords: list[str] | None = None) -> list[str]:
        blocks, _meta = self.retrieve_units_with_meta(query, top_k=top_k, keywords=keywords)
        return blocks

    def retrieve_units_with_meta(self, query: str, top_k: int = 8,
                                 keywords: list[str] | None = None) -> tuple[list[str], list[dict]]:
        blocks: list[str] = []
        meta: list[dict] = []
        hit_ids: set[int] = set()
        try:
            hits, strategy_meta = hybrid_retrieve_units(
                query,
                self.units_by_material,
                top_k=top_k,
                keywords=keywords,
                required_terms=keywords,
                filters={"material_id": list(self.units_by_material.keys())},
            )
            for hit in hits:
                unit_id, _score = hit.unit_id, hit.score
                if unit_id in self._unit_index:
                    material_id, unit = self._unit_index[unit_id]
                    hit_ids.add(unit_id)
                    blocks.append(self._label(material_id, unit))
                    item = self._unit_meta(material_id, unit, hit.source, _score)
                    item["coverage_terms"] = hit.coverage_terms
                    item["retrieval_strategy"] = strategy_meta
                    meta.append(item)
        except Exception:
            pass
        if keywords:
            filled = 0
            for material_id, units in self.units_by_material.items():
                for unit in units:
                    if unit.id in hit_ids or filled >= 3:
                        continue
                    content = unit.content or ""
                    if any(keyword and keyword in content for keyword in keywords):
                        blocks.append(self._label(material_id, unit))
                        meta.append(self._unit_meta(material_id, unit, "keyword", 0))
                        filled += 1
        return blocks, meta

    def _label(self, material_id: int, unit: Unit) -> str:
        uid = f"U{unit.id}" if unit.id is not None else "U?"
        label = f"[{uid} | {self.filenames.get(material_id, '?')}"
        if unit.page is not None:
            label += f" | 第{unit.page}页"
        label += f"]\n{unit.content}"
        return label

    def _unit_meta(self, material_id: int, unit: Unit, source: str, score: float) -> dict:
        return {
            "material_id": int(material_id),
            "unit_id": int(unit.id or 0),
            "filename": self.filenames.get(material_id, ""),
            "kind": unit.kind,
            "page": unit.page,
            "chars": len(unit.content or ""),
            "retrieval_source": source,
            "score": round(float(score or 0), 4),
        }

    def for_planner(self, theme: str, user_requirements: str,
                    insights: list[dict], style_block: str, business_block: str = "",
                    policy_block: str = "") -> str:
        lines = [f"用户主题:{theme}", f"用户要求:{user_requirements or '无'}"]
        if insights:
            lines.append("材料摘要(按价值排序,含候选事实要点):")
            for insight in insights[:10]:
                points = " / ".join(insight.get("key_points", [])[:3])
                lines.append(
                    f"- [{insight.get('value_rank', '?')}级] {insight.get('doc_type', '')} "
                    f"{insight.get('topic', '')} 候选事实:{points or '无'} "
                    f"关键实体:{'、'.join(insight.get('entities', [])[:5])} "
                    f"时间:{'、'.join(insight.get('times', [])[:3])}"
                )
        if business_block:
            lines.append(business_block)
        if policy_block:
            lines.append(policy_block)
        lines.append(style_block or "")
        # Initial planning has its own structured-output contract; final report
        # planning is budgeted separately after Evidence and Analysis.
        final_budget_chars = max(
            4000,
            min(
                self.budget_chars("planner"),
                stage_input_budget_chars("planner"),
            ),
        )
        fitted, _meta = _fit_with_meta("\n".join(lines), final_budget_chars)
        return fitted

    def for_evidence(self, dimension: str, insights: list[dict],
                     required_facts: list[str] | None = None) -> str:
        text, _meta = self.for_evidence_with_meta(dimension, insights, required_facts)
        return text

    def for_evidence_with_meta(self, dimension: str, insights: list[dict],
                               required_facts: list[str] | None = None) -> tuple[str, dict]:
        keywords: list[str] = []
        is_open_discovery = "开放发现" in (dimension or "")
        insight_terms: list[str] = []
        for insight in insights or []:
            keywords.extend(insight.get("entities", [])[:4])
            keywords.extend(insight.get("times", [])[:3])
            insight_terms.extend(insight.get("key_points", [])[:2])
            insight_terms.extend([insight.get("topic", ""), insight.get("material_role", "")])
        if is_open_discovery:
            dimension_text = " ".join(
                [self.task.get("theme", ""), self.task.get("user_requirements", "")]
                + insight_terms
                + list(required_facts or [])
            )
            top_k = 12
        else:
            dimension_text = f"{dimension} {' '.join(required_facts or [])}"
            top_k = 8
        blocks, unit_meta = self.retrieve_units_with_meta(dimension_text, top_k=top_k, keywords=keywords)
        retrieval_strategy = {}
        for item in unit_meta:
            retrieval_strategy = item.get("retrieval_strategy") or retrieval_strategy
        if not blocks:
            return "材料片段:无命中", {
                "dimension": dimension,
                "open_discovery": is_open_discovery,
                "retrieved_unit_count": 0,
                "retrieved_chars": 0,
                "retrieved_material_count": 0,
                "retrieved_units": [],
                "context_chars": len("材料片段:无命中"),
                "truncated": False,
                "retrieval_strategy": retrieval_strategy,
            }
        raw = "相关材料片段:\n" + "\n\n".join(blocks)
        fitted, fit_meta = _fit_with_meta(raw, self.budget_chars("evidence") // 2)
        return fitted, {
            "dimension": dimension,
            "open_discovery": is_open_discovery,
            "retrieved_unit_count": len(blocks),
            "retrieved_chars": sum(len(block) for block in blocks),
            "retrieved_material_count": len({item["material_id"] for item in unit_meta}),
            "retrieved_units": unit_meta[:20],
            **fit_meta,
            "retrieval_strategy": retrieval_strategy,
        }

    def for_first_pass(self, needs: list[dict], insights: list[dict]) -> list[tuple[str, dict]]:
        """First-pass 合并读取:全部 Evidence Needs 一次打分选候选,多维联合提取。

        与 for_evidence_batches 的区别:查询 = 全部 needs(而非单维度),
        候选并集 → 一次装箱 → 批次;每批材料只被读取一次,模型按 need 标注输出。
        """
        queries = [str(n.get("need", "")).strip() for n in (needs or []) if str(n.get("need", "")).strip()]
        if not queries:
            return []
        return self._build_batches(queries, insights, needs or [], pass_name="first_pass")

    def for_gap_retrieval(self, gaps: list[dict], insights: list[dict],
                          known_contents: set[str] | None = None) -> list[tuple[str, dict]]:
        """Iterative Retrieval:仅针对缺口 Needs 检索(缺什么补什么)。"""
        queries = [str(n.get("need", "")).strip() for n in (gaps or []) if str(n.get("need", "")).strip()]
        if not queries:
            return []
        return self._build_batches(queries, insights, gaps or [], pass_name="iterative",
                                   known_contents=known_contents)

    def _build_batches(self, queries: list[str], insights: list[dict],
                       needs: list[dict], pass_name: str,
                       known_contents: set[str] | None = None) -> list[tuple[str, dict]]:
        """按 token 预算装箱:queries 的候选并集 → utility 重排 → 批次。

        known_contents:已提取事实(迭代轮次 feedback)——信息增益重排依据。
        """
        from app.config import settings
        from app.retrieval.reranker import rerank

        per_batch_tokens = _evidence_batch_budget()
        keywords: list[str] = []
        for insight in insights or []:
            keywords.extend(insight.get("entities", [])[:4])
            keywords.extend(insight.get("times", [])[:3])

        # 向量混合检索:预加载单位向量 + 每查询 embed(失败自动回退纯文本)
        unit_vectors: dict = {}
        try:
            from app.retrieval import vector_store
            if getattr(vector_store, "enabled", False):
                unit_vectors = vector_store.unit_vectors({
                    int(u.id) for units in self.units_by_material.values()
                    for u in units if u.id is not None
                })
        except Exception:
            unit_vectors = {}

        need_text = " ".join(queries)
        # 检索候选:Qdrant 粗召回(查询相关)→ rerank;Qdrant 不可用时回退 Python 打分
        per_material: list[tuple[int, list[tuple[float, object]]]] = []
        for material_id, units in self.units_by_material.items():
            candidates: dict[int, object] = {}
            for query in queries:
                query_vector = self._query_compiler.query_vector(RetrievalQuery(query))
                hit_ids: set[int] = set()
                try:
                    from app.retrieval import vector_store
                    if getattr(vector_store, "enabled", False) and query_vector:
                        hits = vector_store.search_units(query_vector, top_k=_recall_budget(), query_text=query)
                        hit_ids = {int(h[0]) for h in hits}
                except Exception:
                    hit_ids = set()
                for unit in units:
                    if unit.id is not None and unit.id in hit_ids:
                        candidates.setdefault(id(unit), unit)
                if not hit_ids:
                    # 回退:全库 Python 打分(离线/无 Qdrant 场景)
                    scored = sorted(
                        [(_unit_relevance(unit, query, keywords, query_vector, unit_vectors), unit)
                         for unit in units if (unit.content or "").strip()],
                        key=lambda pair: pair[0], reverse=True,
                    )
                    cutoff = _dynamic_cutoff([score for score, _unit in scored])
                    for _score, unit in scored[:cutoff]:
                        candidates.setdefault(id(unit), unit)
            if candidates:
                # Utility 重排:相关 + 新信息(已提取事实 feedback 降权重复)
                ranked = rerank(
                    [(0.5, u) for u in candidates.values()],
                    need_text,
                    unit_vectors=unit_vectors,
                    known_contents=known_contents,
                )
                per_material.append((material_id, ranked))

        stage_budget = (
            int(settings.evidence_first_pass_input_tokens)
            if pass_name == "first_pass"
            else int(settings.evidence_gap_input_tokens)
        )
        selected_by_material = _select_evidence_candidates(per_material, stage_budget)

        blocks_by_material: list[tuple[int, list]] = []
        for material_id, selected in selected_by_material:
            block_units: list = []
            block_tokens = 0
            for unit in selected:
                unit_tokens = _estimate_tokens(unit.content or "")
                if block_tokens + unit_tokens > per_batch_tokens and block_units:
                    blocks_by_material.append((material_id, block_units))
                    block_units, block_tokens = [], 0
                block_units.append(unit)
                block_tokens += unit_tokens
            if block_units:
                blocks_by_material.append((material_id, block_units))

        batches: list[tuple[list[str], list[int], int, dict]] = []
        current_blocks: list[str] = []
        current_materials: list[int] = []
        current_tokens = 0
        current_units: dict[int, int] = {}
        for material_id, selected in blocks_by_material:
            block = "\n\n".join(self._label(material_id, u) for u in selected)
            block_tokens = _estimate_tokens(block)
            if current_tokens + block_tokens > per_batch_tokens and current_blocks:
                batches.append((current_blocks, current_materials, current_tokens, current_units))
                current_blocks, current_materials, current_tokens, current_units = [], [], 0, {}
            current_blocks.append(block)
            current_materials.append(material_id)
            current_tokens += block_tokens
            current_units[material_id] = current_units.get(material_id, 0) + len(selected)
        if current_blocks:
            batches.append((current_blocks, current_materials, current_tokens, current_units))

        result = []
        for blocks, material_ids, batch_tokens, batch_units in batches:
            text = "相关材料片段:\n" + "\n\n".join(blocks)
            result.append((text, {
                "pass": pass_name,
                "batch_material_ids": material_ids,
                "batch_material_units": batch_units,
                "batch_material_count": len(material_ids),
                "retrieved_unit_count": sum(batch_units.values()),
                "retrieved_chars": sum(len(b) for b in blocks),
                "context_chars": len(text),
                "needs": needs,
            }))
        return result

    def for_evidence_batches(self, dimension: str, insights: list[dict],
                             required_facts: list[str] | None = None) -> list[tuple[str, dict]]:
        """材料全覆盖 Evidence Pass:每份可用材料都获得检查机会。

        动态 Top-K:对每个查询(维度 + 每个 Required Fact)给材料内全部 Units
        打分并降序,按相关度分布识别自然断点,断点前 Units 全部保留;
        多个查询候选取并集去重 → 该材料的动态 K(无硬编码阈值)。

        上下文安全由 Token Budget 保证(非 Unit 数量):
        单批可用 tokens = 模型窗口 - 输出预留 - 固定 prompt 开销 - 安全余量;
        候选内容按实际 token 数装箱,超单批预算自动拆批;
        K 决定"选什么",Token Budget 决定"分几批",两者完全分离。
        """
        per_batch_tokens = _evidence_batch_budget()
        keywords: list[str] = []
        for insight in insights or []:
            keywords.extend(insight.get("entities", [])[:4])
            keywords.extend(insight.get("times", [])[:3])
        queries = [dimension] + [rf for rf in (required_facts or []) if rf]

        # 向量混合检索:预加载单位向量 + 每个查询 embed(失败自动回退纯文本)
        unit_vectors: dict = {}
        try:
            from app.retrieval import vector_store
            if getattr(vector_store, "enabled", False):
                unit_vectors = vector_store.unit_vectors({
                    int(u.id) for units in self.units_by_material.values()
                    for u in units if u.id is not None
                })
        except Exception:
            unit_vectors = {}

        per_material: list[tuple[int, list]] = []
        for material_id, units in self.units_by_material.items():
            candidates: dict[int, object] = {}
            for query in queries:
                query_vector = None
                try:
                    from app.retrieval.embedder import embed_texts
                    query_vector = embed_texts([query], query=True)[0]
                except Exception:
                    query_vector = None
                scored = sorted(
                    [(_unit_relevance(unit, query, keywords, query_vector, unit_vectors), unit)
                     for unit in units if (unit.content or "").strip()],
                    key=lambda pair: pair[0], reverse=True,
                )
                cutoff = _dynamic_cutoff([score for score, _unit in scored])
                for _score, unit in scored[:cutoff]:
                    candidates.setdefault(id(unit), unit)
            if candidates:
                per_material.append((material_id, list(candidates.values())))

        # Token Budget 装箱:候选 units 按实际 token 拆分,单批不超预算。
        # 材料块超预算时按 unit 再拆(候选不删减,只换批)。
        blocks_by_material: list[tuple[int, list]] = []
        for material_id, selected in per_material:
            block_units: list = []
            block_tokens = 0
            for unit in selected:
                unit_tokens = _estimate_tokens(unit.content or "")
                if block_tokens + unit_tokens > per_batch_tokens and block_units:
                    blocks_by_material.append((material_id, block_units))
                    block_units, block_tokens = [], 0
                block_units.append(unit)
                block_tokens += unit_tokens
            if block_units:
                blocks_by_material.append((material_id, block_units))

        batches: list[tuple[list[str], list[int], int, dict]] = []
        current_blocks: list[str] = []
        current_materials: list[int] = []
        current_tokens = 0
        current_units: dict[int, int] = {}
        for material_id, selected in blocks_by_material:
            # _label 已含首个 unit 的 content,后续 units 追加正文
            block = "\n\n".join(self._label(material_id, u) for u in selected)
            block_tokens = _estimate_tokens(block)
            if current_tokens + block_tokens > per_batch_tokens and current_blocks:
                batches.append((current_blocks, current_materials, current_tokens, current_units))
                current_blocks, current_materials, current_tokens, current_units = [], [], 0, {}
            current_blocks.append(block)
            current_materials.append(material_id)
            current_tokens += block_tokens
            current_units[material_id] = current_units.get(material_id, 0) + len(selected)
        if current_blocks:
            batches.append((current_blocks, current_materials, current_tokens, current_units))

        result = []
        for blocks, material_ids, batch_tokens, batch_units in batches:
            text = "相关材料片段:\n" + "\n\n".join(blocks)
            result.append((text, {
                "dimension": dimension,
                "batch_material_ids": material_ids,
                "batch_material_units": batch_units,
                "batch_material_count": len(material_ids),
                "retrieved_unit_count": sum(batch_units.values()),
                "retrieved_chars": sum(len(b) for b in blocks),
                "context_chars": len(text),
                "estimated_tokens": batch_tokens,
                "truncated": False,
                "context_source": "material_cover_pass",
                "scanned_material_count": len(per_material),
            }))
        return result

    def for_analysis(self, facts: list[dict], conflicts: list[dict],
                     timeline_block: str, memory_block: str) -> str:
        lines = ["事实清单(编号 + 来源):"]
        lines.extend(f"{fact['id']}. [{fact['sources']}] {fact['content']}" for fact in facts)
        lines.append("来源冲突:")
        lines.append(json_dumps(conflicts) if conflicts else "无")
        if timeline_block:
            lines.append(f"事件时间线:\n{timeline_block}")
        if memory_block:
            lines.append(f"历史知识:\n{memory_block}")
        raw = "\n".join(lines)
        fitted, fit_meta = _fit_with_meta(raw, self.budget_chars("analysis"))
        if fit_meta["truncated"]:
            fitted += (
                "\n\n[上下文预算提示] 事实过多,本次分析上下文已按当前顺序放入预算内内容;"
                "完整事实仍保留在数据库,后续写作与溯源不会删除原始数据。"
            )
        return fitted

    def for_final_planner(self, theme: str, user_requirements: str, plan: dict,
                          facts: list[dict], inferences: list[dict],
                          insights: list[dict], style_block: str,
                          policy_block: str = "") -> str:
        header_lines = [
            f"用户主题:{theme}",
            f"用户要求:{user_requirements or '无'}",
            "初步分析规划(仅为分析假设与检索重点,可被后续事实推翻,不是最终目录):",
            json_dumps({
                "title": plan.get("title"),
                "objective": plan.get("objective"),
                "core_question": plan.get("core_question"),
                "core_judgment": plan.get("core_judgment"),
                "initial_hypothesis": plan.get("narrative_logic"),
                "dimensions": plan.get("dimensions"),
                "required_facts": plan.get("required_facts"),
                "budget": plan.get("budget"),
            }),
        ]
        if insights:
            header_lines.append("材料理解摘要:")
            for item in insights[:10]:
                header_lines.append(
                    f"- {item.get('filename','')}: {item.get('doc_type','')} / {item.get('topic','')} / "
                    f"角色:{item.get('material_role','')} / 边界:{item.get('claim_support','unknown')}"
                )
        analysis_meta = plan.get("analysis_global_meta") or {}
        if analysis_meta:
            header_lines.append("综合分析摘要:")
            header_lines.append(json_dumps({
                "critical_fact_ids": analysis_meta.get("critical_fact_ids") or [],
                "coverage_status": analysis_meta.get("coverage_status") or {},
                "unresolved_conflicts": analysis_meta.get("unresolved_conflicts") or [],
                "uncertainty": analysis_meta.get("uncertainty") or [],
            }))

        queries = [theme, user_requirements, plan.get("core_question", ""), plan.get("core_judgment", "")]
        queries.extend(str(key) for key in (analysis_meta.get("coverage_status") or {}).keys())
        queries.extend(
            str(item.get("need") or "")
            for item in plan.get("evidence_needs") or []
            if isinstance(item, dict)
        )
        queries = list(dict.fromkeys(str(query).strip() for query in queries if str(query).strip()))
        total_budget = self.budget_chars("final_planner")
        budgets = {
            "header": int(total_budget * 0.22),
            "facts": int(total_budget * 0.43),
            "inferences": int(total_budget * 0.23),
            "style": int(total_budget * 0.06),
        }
        budgets["policy"] = total_budget - sum(budgets.values())
        critical_ids = {
            int(value) for value in (analysis_meta.get("critical_fact_ids") or [])
            if str(value).isdigit()
        }
        selected_facts = self._select_final_planner_facts(facts, queries, critical_ids, budgets["facts"])
        selected_fact_ids = {int(item.get("id") or 0) for item in selected_facts}
        selected_inferences = self._select_final_planner_inferences(
            inferences, selected_fact_ids, critical_ids, queries, budgets["inferences"]
        )

        fact_lines = ["已抽取事实(关键事实 + 多问题/来源覆盖 + 语义去冗余):"]
        fact_lines.extend(
            f"- fact_id={fact.get('id')}; dimension={fact.get('dimension') or '未分类'}; "
            f"sources={fact.get('source_files') or []}: {fact.get('content')}"
            for fact in selected_facts
        )
        if len(selected_facts) < len(facts):
            fact_lines.append(
                f"[覆盖说明] Facts 共 {len(facts)} 条，本次展开 {len(selected_facts)} 条；"
                "后续 Narrative Plan 与 Writer 仍会按章检索完整事实库。"
            )
        inference_lines = ["已形成分析判断(按关键事实关联、维度覆盖、置信度与去冗余选择):"]
        inference_lines.extend(
            f"- inference_id={inf.get('id')}; dimension={inf.get('dimension') or '未分类'}; "
            f"confidence={inf.get('confidence_level') or 'unknown'}; "
            f"based_fact_ids={inf.get('based_fact_ids')}: {inf.get('content')}"
            for inf in selected_inferences
        )
        if len(selected_inferences) < len(inferences):
            inference_lines.append(
                f"[覆盖说明] Inferences 共 {len(inferences)} 条，本次展开 {len(selected_inferences)} 条；"
                "未展开推论不会从任务中删除。"
            )

        sections = [
            _pack_complete_lines(header_lines, budgets["header"], "任务与分析摘要条目"),
            _pack_complete_lines(fact_lines, budgets["facts"], "事实条目"),
            _pack_complete_lines(inference_lines, budgets["inferences"], "推论条目"),
            _pack_complete_lines(
                ["模板/风格信息(格式优先,结构按策略处理):", *(style_block.splitlines() if style_block else ["无"])],
                budgets["style"], "模板风格条目",
            ),
            _pack_complete_lines(
                ["报告策略:", *(policy_block.splitlines() if policy_block else ["无"])],
                budgets["policy"], "报告策略条目",
            ),
        ]
        return "\n".join(line for section in sections for line in section)

    def _select_final_planner_facts(self, facts: list[dict], queries: list[str],
                                    critical_ids: set[int], budget_chars: int) -> list[dict]:
        """Select mandatory, source-diverse and topic-diverse facts under one budget."""
        if not facts:
            return []
        by_id = {int(item.get("id")): item for item in facts if item.get("id") is not None}
        average_size = max(1, sum(len(str(item.get("content") or "")) + 80 for item in facts) // len(facts))
        capacity = max(1, int(budget_chars) // average_size)
        per_query = max(2, (capacity + max(len(queries), 1) - 1) // max(len(queries), 1) + 1)
        ranked_groups = [self._retrieve_facts(query, facts, per_query) for query in queries]
        selected: list[dict] = []
        selected_ids: set[int] = set()
        selected_sources: set[str] = set()
        used_chars = 0

        def add(item: dict, mandatory: bool = False) -> bool:
            nonlocal used_chars
            item_id = int(item.get("id") or 0)
            content = str(item.get("content") or "")
            if not item_id or not content or item_id in selected_ids:
                return False
            size = len(content) + 80
            if not mandatory and used_chars + size > budget_chars:
                return False
            if not mandatory and any(_text_similarity(content, str(old.get("content") or "")) >= 0.82 for old in selected):
                return False
            selected.append(item)
            selected_ids.add(item_id)
            selected_sources.update(str(value) for value in item.get("source_files") or [] if str(value))
            used_chars += size
            return True

        for item_id in sorted(critical_ids):
            if item_id in by_id:
                add(by_id[item_id], mandatory=True)
        max_rank = max((len(group) for group in ranked_groups), default=0)
        for rank in range(max_rank):
            for group in ranked_groups:
                if rank >= len(group):
                    continue
                item = group[rank]
                sources = {str(value) for value in item.get("source_files") or [] if str(value)}
                if sources - selected_sources:
                    add(item)
        for rank in range(max_rank):
            for group in ranked_groups:
                if rank < len(group):
                    add(group[rank])
        if not selected:
            for item in facts:
                add(item)
        return selected

    def _select_final_planner_inferences(self, inferences: list[dict], selected_fact_ids: set[int],
                                         critical_fact_ids: set[int], queries: list[str],
                                         budget_chars: int) -> list[dict]:
        """Select judgments with visible evidence links and runtime dimension coverage."""
        if not inferences:
            return []
        query_chars = set("".join(queries))
        confidence_score = {"high": 3, "medium": 2, "low": 1}

        def based_ids(item: dict) -> set[int]:
            return {int(value) for value in item.get("based_fact_ids") or [] if str(value).isdigit()}

        def score(item: dict) -> tuple[float, int]:
            based = based_ids(item)
            overlap = based & selected_fact_ids
            critical = based & critical_fact_ids
            lexical = len(set(str(item.get("content") or "")) & query_chars)
            confidence = confidence_score.get(str(item.get("confidence_level") or "").lower(), 0)
            return (len(critical) * 20 + len(overlap) * 5 + confidence + lexical / 100, len(based))

        ranked = sorted(inferences, key=score, reverse=True)
        linked = [item for item in ranked if based_ids(item) & selected_fact_ids]
        candidates = linked or ranked
        selected: list[dict] = []
        selected_ids: set[int] = set()
        used_chars = 0

        def add(item: dict) -> bool:
            nonlocal used_chars
            item_id = int(item.get("id") or 0)
            content = str(item.get("content") or "")
            size = len(content) + len(str(item.get("based_fact_ids") or [])) + 100
            if not item_id or not content or item_id in selected_ids or used_chars + size > budget_chars:
                return False
            if any(_text_similarity(content, str(old.get("content") or "")) >= 0.82 for old in selected):
                return False
            selected.append(item)
            selected_ids.add(item_id)
            used_chars += size
            return True

        seen_dimensions: set[str] = set()
        for item in candidates:
            dimension = str(item.get("dimension") or "未分类")
            if dimension not in seen_dimensions and add(item):
                seen_dimensions.add(dimension)
        for item in candidates:
            add(item)
        return selected

    def for_writer_section(self, chapter: str, facts: list[dict],
                           inferences: list[dict], style_block: str,
                           top_facts: int | None = None,
                           required_fact_ids: set[int] | None = None,
                           used_fact_ids: set[int] | None = None,
                           coverage_queries: list[str] | None = None) -> tuple[list[dict], list[dict], str]:
        required_fact_ids = required_fact_ids or set()
        used_fact_ids = used_fact_ids or set()
        by_id = {int(f.get("id")): f for f in facts if f.get("id") is not None}
        queries = list(dict.fromkeys(
            str(value).strip()
            for value in [chapter, *(coverage_queries or [])]
            if str(value or "").strip()
        ))
        fact_budget = max(1000, int(self.budget_chars("writer") * 0.65))
        average_fact_size = max(
            1,
            sum(len(str(fact.get("content") or "")) + 24 for fact in facts) // max(len(facts), 1),
        )
        capacity = max(len(required_fact_ids), fact_budget // average_fact_size)
        if top_facts is not None:
            capacity = max(capacity, int(top_facts), len(required_fact_ids))
        per_query = max(1, (capacity + max(len(queries), 1) - 1) // max(len(queries), 1))

        # Required evidence is retained first. Remaining capacity is filled by
        # independent topic queries, so one dominant topic cannot consume the
        # whole context and long chapters are not constrained by a fixed Top-K.
        candidates = [by_id[fid] for fid in required_fact_ids if fid in by_id]
        for query in queries:
            candidates.extend(self._retrieve_facts(
                query, facts, per_query, used_fact_ids=used_fact_ids - required_fact_ids,
            ))
        related_facts = []
        seen: set[int] = set()
        used_chars = 0
        for fact in candidates:
            fact_id = int(fact.get("id") or 0)
            if not fact_id or fact_id in seen:
                continue
            size = len(str(fact.get("content") or "")) + 24
            if fact_id not in required_fact_ids and related_facts and used_chars + size > fact_budget:
                continue
            related_facts.append(fact)
            seen.add(fact_id)
            used_chars += size
        related_ids = {fact["id"] for fact in related_facts}
        related_inferences = [
            inference for inference in inferences
            if any(bid in related_ids for bid in (inference.get("based_fact_ids") or []))
        ] or inferences[:3]
        return related_facts, related_inferences, style_block

    def _retrieve_facts(self, query: str, facts: list[dict], top_k: int,
                        used_fact_ids: set[int] | None = None) -> list[dict]:
        if not facts:
            return []
        used_fact_ids = used_fact_ids or set()
        try:
            query_vector = embed_texts([query], query=True)[0]
            vec_by_id = {
                int(fact_id): vector
                for fact_id, vector in vector_store.fact_vectors(str(self.task.get("id") or self.task.get("task_id") or ""))
            }
            scored = []
            for fact in facts:
                vector = vec_by_id.get(fact["id"])
                if vector is not None:
                    scored.append((cosine(query_vector, vector.tolist()), fact))
            if not scored:
                raise ValueError("no cached vectors")
            # 检索只按相关性召回;事实分配由 Narrative Plan 决策(前文已用事实
            # 是否复用、承担什么新作用,都是模型语义判断,不在检索层做章节规划)
            scored.sort(key=lambda pair: pair[0], reverse=True)
            result = [fact for _score, fact in scored[:top_k]]
            rest = [fact for fact in facts if fact["id"] not in vec_by_id]
            if rest and len(result) < top_k:
                query_terms = set(query)
                result += sorted(rest, key=lambda fact: len(set(fact["content"]) & query_terms), reverse=True)[: top_k - len(result)]
            return result
        except Exception:
            query_terms = set(query)
            return sorted(facts, key=lambda fact: len(set(fact["content"]) & query_terms), reverse=True)[:top_k]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def json_dumps(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _recall_budget() -> int:
    """Qdrant 粗召回量:按 token 预算 ÷ 平均 unit 规模推导(动态)。

    预算 = 单批 token 上限的 3 倍(召回池需覆盖多批),除以平均单位规模
    得到召回条数;随材料规模自适应,无固定 Top-K。
    """
    budget = _evidence_batch_budget() * 3
    avg = 120  # 中文 unit 平均约 120 token(规模估算,非决策阈值)
    return max(200, int(budget / avg))
