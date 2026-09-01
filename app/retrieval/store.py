"""向量存储:Qdrant 单后端。

SQLite 向量方案已移除(SQLiteVectorStore 与 unit_vectors/material_vectors/
fact_vectors 表不再维护)。单元/材料/事实向量统一存 Qdrant;Qdrant 不可用时
检索回退关键词,不落任何本地向量表。
"""
from __future__ import annotations

import numpy as np

from app.config import settings


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class QdrantVectorStore:
    def __init__(self) -> None:
        self.enabled = False
        self.client = None
        self.units_collection = settings.qdrant_collection_units
        self.materials_collection = settings.qdrant_collection_materials
        self.facts_collection = settings.qdrant_collection_facts
        backend = (settings.vector_backend or "auto").lower()
        if backend == "off":
            return
        if backend not in ("auto", "qdrant") or not settings.qdrant_url:
            return
        try:
            from qdrant_client import QdrantClient

            self.client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                timeout=5,
            )
            self.client.get_collections()
            self.enabled = True
        except Exception:
            self.client = None
            self.enabled = False

    @property
    def backend(self) -> str:
        return "qdrant" if self.enabled else "off"

    def ensure_collection(self, collection: str, vector_size: int) -> None:
        if not self.enabled or self.client is None:
            return
        try:
            from qdrant_client.models import Distance, VectorParams

            names = {c.name for c in self.client.get_collections().collections}
            if collection not in names:
                self.client.create_collection(
                    collection_name=collection,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
        except Exception:
            pass  # 集合创建失败不致命:检索回退关键词,下次写入再试

    def save_material_vector(self, material_id: int, vector: list[float]) -> bool:
        return self._upsert(
            self.materials_collection,
            int(material_id),
            vector,
            {"material_id": int(material_id), "kind": "material"},
        )

    def save_unit_vector(self, unit_id: int, vector: list[float]) -> bool:
        payload = self._unit_payload(unit_id)
        payload["kind"] = "unit"
        return self._upsert(self.units_collection, int(unit_id), vector, payload)

    def save_fact_vector(self, fact_id: int, vector: list[float], task_id: str = "") -> bool:
        return self._upsert(
            self.facts_collection,
            int(fact_id),
            vector,
            {"fact_id": int(fact_id), "task_id": task_id, "kind": "fact"},
        )

    def material_vectors(self) -> list[tuple[int, np.ndarray]]:
        """全部材料向量(供去重):[(material_id, np.ndarray)]。"""
        return self._scroll_vectors(self.materials_collection, "material_id")

    def fact_vectors(self, task_id: str = "") -> list[tuple[int, np.ndarray]]:
        """事实向量;传入 task_id 时只读取当前任务,避免全库滚动污染。"""
        filters = {"task_id": task_id} if task_id else None
        return self._scroll_vectors(self.facts_collection, "fact_id", filters=filters)

    def unit_vectors(self, unit_ids: set[int] | None = None) -> dict[int, np.ndarray]:
        """单位向量(供语义相关度混合检索):{unit_id: np.ndarray}。"""
        if not self.enabled or self.client is None:
            return {}
        try:
            filters = None
            if unit_ids:
                filters = {"unit_id": sorted(int(i) for i in unit_ids)}
            return {unit_id: vec for unit_id, vec in self._scroll_vectors(self.units_collection, "unit_id", filters=filters)}
        except Exception:
            return {}

    def _scroll_vectors(self, collection: str, id_field: str,
                        filters: dict | None = None) -> list[tuple[int, np.ndarray]]:
        if not self.enabled or self.client is None:
            return []
        try:
            result = []
            next_offset = None
            while True:
                page, next_offset = self.client.scroll(
                    collection_name=collection,
                    limit=512,
                    offset=next_offset,
                    scroll_filter=self._qdrant_filter(filters or {}),
                    with_payload=True,
                    with_vectors=True,
                )
                for point in page:
                    value = point.payload.get(id_field)
                    if value is None or point.vector is None:
                        continue
                    result.append((int(value), np.asarray(point.vector, dtype=np.float32)))
                if next_offset is None:
                    break
            return result
        except Exception:
            return []

    def search_units(self, query_vector: list[float] | np.ndarray, top_k: int = 10,
                     filters: dict | None = None) -> list[tuple[int, float]]:
        """Run task-filtered Qdrant unit vector retrieval."""
        if not self.enabled or self.client is None:
            return []
        try:
            query = np.asarray(query_vector, dtype=np.float32).tolist()
            qfilter = self._qdrant_filter(filters or {})
            hits = self.client.search(
                collection_name=self.units_collection,
                query_vector=query,
                query_filter=qfilter,
                limit=max(top_k, 1),
                with_payload=False,
            )
            return [(int(hit.id), float(hit.score or 0)) for hit in hits]
        except Exception:
            return []

    def _upsert(self, collection: str, point_id: int, vector: list[float], payload: dict) -> bool:
        if not self.enabled or self.client is None:
            return False
        try:
            from qdrant_client.models import PointStruct

            values = np.asarray(vector, dtype=np.float32).tolist()
            self.ensure_collection(collection, len(values))
            self.client.upsert(
                collection_name=collection,
                points=[PointStruct(id=point_id, vector=values, payload=payload)],
            )
            return True
        except Exception:
            return False  # Retrieval falls back to lexical search; caller records degradation.

    def _unit_payload(self, unit_id: int) -> dict:
        from app.db import session_scope
        from app.infrastructure.orm import ORMUnit, ORMMaterial
        from sqlalchemy import select

        with session_scope() as s:
            row = s.execute(
                select(ORMUnit.c.id, ORMUnit.c.material_id, ORMUnit.c.kind, ORMUnit.c.page,
                       ORMUnit.c.paragraph, ORMMaterial.c.filename, ORMMaterial.c.file_type)
                .join(ORMMaterial, ORMMaterial.c.id == ORMUnit.c.material_id)
                .where(ORMUnit.c.id == int(unit_id))
            ).mappings().first()
        if row is None:
            return {"unit_id": int(unit_id)}
        return {
            "unit_id": int(row["id"]),
            "material_id": int(row["material_id"]),
            "kind": row["kind"],
            "page": row["page"],
            "paragraph": row["paragraph"],
            "filename": row["filename"],
            "file_type": row["file_type"],
        }

    def _qdrant_filter(self, filters: dict):
        if not filters:
            return None
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

            must = []
            for key, value in filters.items():
                if value is None or value == []:
                    continue
                if isinstance(value, (list, tuple, set)):
                    must.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
                else:
                    must.append(FieldCondition(key=key, match=MatchValue(value=value)))
            return Filter(must=must) if must else None
        except Exception:
            return None


vector_store = QdrantVectorStore()
