"""Query Compiler:Evidence Need → 检索请求(方案第 5 节)。

把 EvidenceNeed + TaskProfile + 当前已知 Facts + 缺口 编译为检索查询;
同一查询向量任务级缓存(不再每材料重复 embed)。
"""
from __future__ import annotations

from app.retrieval.embedder import embed_texts


class RetrievalQuery:
    """编译后的检索请求。"""

    def __init__(self, text: str, need_id: str = "", filters: dict | None = None):
        self.text = text
        self.need_id = need_id
        self.filters = filters or {}


class QueryCompiler:
    """Needs → 检索查询;查询向量缓存(任务级)。"""

    def __init__(self, task_id: str = ""):
        self.task_id = task_id
        self._vector_cache: dict[str, list[float] | None] = {}

    def compile(self, need: dict, known_facts: list[str] | None = None,
                gaps: list[dict] | None = None) -> RetrievalQuery:
        """编译单个 Need 为查询。

        - 查询文本 = need 本身(加上缺口方面作为聚焦)
        - 已知事实与缺口用于聚焦,不改变查询语义(程序不猜测)
        """
        text = str(need.get("need", "")).strip()
        if not text:
            text = str(need.get("dimension", "")).strip()
        return RetrievalQuery(text=text, need_id=str(need.get("need_id", "")))

    def query_vector(self, query: RetrievalQuery) -> list[float] | None:
        """查询向量(缓存:同一查询只 embed 一次)。"""
        if query.text in self._vector_cache:
            return self._vector_cache[query.text]
        try:
            vector = embed_texts([query.text], query=True)[0]
        except Exception:
            vector = None
        self._vector_cache[query.text] = vector
        return vector

    def clear(self) -> None:
        self._vector_cache.clear()
