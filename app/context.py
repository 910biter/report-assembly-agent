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
        return _fit("\n".join(lines), self.budget_chars())

    def for_evidence(self, dimension: str, insights: list[dict],
                     required_facts: list[str] | None = None) -> str:
        text, _meta = self.for_evidence_with_meta(dimension, insights, required_facts)
        return text

    def for_evidence_with_meta(self, dimension: str, insights: list[dict],
                               required_facts: list[str] | None = None) -> tuple[str, dict]:
        keywords: list[str] = []
        dimension_text = f"{dimension} {' '.join(required_facts or [])}"
        for insight in insights or []:
            keywords.extend(insight.get("entities", [])[:4])
            keywords.extend(insight.get("times", [])[:3])
        blocks, unit_meta = self.retrieve_units_with_meta(dimension_text, top_k=8, keywords=keywords)
        retrieval_strategy = {}
        for item in unit_meta:
            retrieval_strategy = item.get("retrieval_strategy") or retrieval_strategy
        if not blocks:
            return "材料片段:无命中", {
                "dimension": dimension,
                "retrieved_unit_count": 0,
                "retrieved_chars": 0,
                "retrieved_material_count": 0,
                "retrieved_units": [],
                "context_chars": len("材料片段:无命中"),
                "truncated": False,
                "retrieval_strategy": retrieval_strategy,
            }
        raw = "相关材料片段:\n" + "\n\n".join(blocks)
        fitted = _fit(raw, self.budget_chars() // 2)
        return fitted, {
            "dimension": dimension,
            "retrieved_unit_count": len(blocks),
            "retrieved_chars": sum(len(block) for block in blocks),
            "retrieved_material_count": len({item["material_id"] for item in unit_meta}),
            "retrieved_units": unit_meta[:20],
            "context_chars": len(fitted),
            "truncated": len(raw) > len(fitted),
            "retrieval_strategy": retrieval_strategy,
        }

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
        return _fit("\n".join(lines), self.budget_chars())

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
            vec_by_id = {int(fact_id): vector for fact_id, vector in vector_store.fact_vectors()}
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
