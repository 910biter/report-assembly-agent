"""Context Manager: build minimal context by workflow stage with hybrid retrieval."""

from app.models import Unit
from app.retrieval import embed_texts, vector_store
from app.retrieval.rag import hybrid_retrieve_units

BUDGET_TOKENS = 6000
_CHARS_PER_TOKEN = 4  # Conservative estimate for Chinese text.


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
    """中文保守 token 估算(约 2 字符/token,偏保守→更安全)。"""
    return max(1, len(text or "") // 2)


def _evidence_batch_budget() -> int:
    """单批可用 tokens = 模型窗口 - 输出预留 - 固定 prompt 开销 - 安全余量。"""
    from app.config import settings

    return max(
        1024,
        settings.model_context_window_tokens
        - settings.generation_reserve_tokens
        - settings.prompt_overhead_tokens
        - settings.safety_margin_tokens,
    )


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


class ContextManager:
    def __init__(self, task: dict, units_by_material: dict[int, list[Unit]] | None = None,
                 filenames: dict[int, str] | None = None):
        self.task = task
        self.units_by_material = units_by_material or {}
        self.filenames = filenames or {}
        self._unit_index: dict[int, tuple[int, Unit]] = {}
        for material_id, units in self.units_by_material.items():
            for unit in units:
                if unit.id is not None:
                    self._unit_index[unit.id] = (material_id, unit)

    def budget_chars(self) -> int:
        return BUDGET_TOKENS * _CHARS_PER_TOKEN

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
        label = f"[{self.filenames.get(material_id, '?')}"
        if unit.page is not None:
            label += f" 第{unit.page}页"
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
        fitted, _meta = _fit_with_meta("\n".join(lines), self.budget_chars())
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
        fitted, fit_meta = _fit_with_meta(raw, self.budget_chars() // 2)
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
                    query_vector = embed_texts([query])[0]
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
            block = self._label(material_id, selected[0]) + "\n".join(
                (u.content or "") for u in selected[1:]
            )
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
        fitted, fit_meta = _fit_with_meta(raw, self.budget_chars())
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
        lines = [
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
            lines.append("材料理解摘要:")
            for item in insights[:10]:
                lines.append(
                    f"- {item.get('filename','')}: {item.get('doc_type','')} / {item.get('topic','')} / "
                    f"角色:{item.get('material_role','')} / 边界:{item.get('claim_support','unknown')}"
                )
        lines.append("已抽取事实:")
        for fact in facts[:80]:
            lines.append(f"- fact_id={fact.get('id')}: {fact.get('content')}")
        if len(facts) > 80:
            lines.append(f"[提示] 事实总数 {len(facts)} 条,这里只展示前 80 条;最终结构应围绕核心事实分组。")
        lines.append("已形成分析判断:")
        for inf in inferences[:40]:
            lines.append(
                f"- inference_id={inf.get('id')}: {inf.get('content')} "
                f"(based_fact_ids={inf.get('based_fact_ids')})"
            )
        if style_block:
            lines.append("模板/风格信息(格式优先,结构按策略处理):")
            lines.append(style_block)
        if policy_block:
            lines.append(policy_block)
        fitted, _meta = _fit_with_meta("\n".join(lines), self.budget_chars())
        return fitted

    def for_writer_section(self, chapter: str, facts: list[dict],
                           inferences: list[dict], style_block: str,
                           top_facts: int = 10,
                           required_fact_ids: set[int] | None = None,
                           used_fact_ids: set[int] | None = None) -> tuple[list[dict], list[dict], str]:
        required_fact_ids = required_fact_ids or set()
        used_fact_ids = used_fact_ids or set()
        top_facts = max(top_facts, len(required_fact_ids))
        related_facts = self._retrieve_facts(chapter, facts, top_facts,
                                             used_fact_ids=used_fact_ids - required_fact_ids)
        if required_fact_ids:
            by_id = {int(f.get("id")): f for f in facts if f.get("id") is not None}
            seen = {int(f.get("id")) for f in related_facts if f.get("id") is not None}
            required = [by_id[fid] for fid in required_fact_ids if fid in by_id and fid not in seen]
            related_facts = required + related_facts
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
            query_vector = embed_texts([query])[0]
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
