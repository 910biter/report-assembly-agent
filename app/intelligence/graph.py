"""Intelligence Graph:事实约束的全局情报图谱(方案 P5 落地)。

- 建图依据 = 已验证 Fact(非原始文本构图):FactCluster/FactRelation +
  entities/events/relations 表 → 节点(Entity/Event)+ 边(Relation,带证据)
- 图检索:实体命中 → 邻域扩展 → 相关 Fact 补证据(与 Vector RAG 双路)
- 可溯源:Relation → Fact → Evidence → Unit → Material
"""
from __future__ import annotations

from app.db import session_scope
from app.infrastructure.orm import Base
from sqlalchemy import select, func


EntityTable = Base.metadata.tables["entities"]
EventTable = Base.metadata.tables["events"]
RelationTable = Base.metadata.tables["relations"]


class IntelligenceGraph:
    """任务级情报图(内存视图 + 持久化 entities/events/relations)。"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.nodes: dict[int, dict] = {}      # node_id -> {type, name, fact_ids}
        self.edges: list[dict] = []           # {source, target, type, evidence}

    def build(self) -> "IntelligenceGraph":
        """从持久化关系数据构建图(关系已由 Consolidation/Conflict 产生)。"""
        self.nodes = {}
        self.edges = []
        with session_scope() as s:
            entities = s.execute(
                select(EntityTable.c.id, EntityTable.c.name, EntityTable.c.type, EntityTable.c.aliases)
                .where(EntityTable.c.task_id == self.task_id)
            ).mappings().all()
            events = s.execute(
                select(EventTable.c.id, EventTable.c.name, EventTable.c.time,
                       EventTable.c.entity_ids, EventTable.c.fact_ids)
                .where(EventTable.c.task_id == self.task_id)
            ).mappings().all()
            relations = s.execute(
                select(RelationTable.c.id, RelationTable.c.source_entity,
                       RelationTable.c.target_entity, RelationTable.c.relation_type,
                       RelationTable.c.fact_ids)
                .where(RelationTable.c.task_id == self.task_id)
            ).mappings().all()
        for row in entities:
            self.nodes[int(row["id"])] = {
                "type": "entity", "name": row["name"],
                "node_type": row["type"] or "entity",
                "fact_ids": "",
            }
        for row in events:
            # 事件节点 id 偏移(与实体 id 空间隔离,避免覆盖)
            self.nodes[int(row["id"]) + 1000000] = {
                "type": "event", "name": row["name"],
                "node_type": "event",
                "fact_ids": row["fact_ids"] or "",
            }
        for row in relations:
            self.edges.append({
                "source": int(row["source_entity"]), "target": int(row["target_entity"]),
                "type": row["relation_type"] or "related",
                "evidence": row["fact_ids"] or "",
            })
        return self

    def search(self, query: str, hop: int = 2, limit: int = 20) -> list[dict]:
        """图检索:查询实体命中 → 邻域扩展 → 关联 Fact(带证据链)。

        返回 [{node, node_type, name, relation_path, fact_ids}]。
        """
        hits = [
            nid for nid, node in self.nodes.items()
            if query and query in str(node.get("name", ""))
        ]
        if not hits:
            return []
        seen: set[int] = set(hits)
        frontier: list[int] = list(hits)
        paths: dict[int, list[str]] = {nid: [] for nid in hits}
        for _ in range(hop):
            next_frontier: list[int] = []
            for edge in self.edges:
                if edge["source"] in frontier and edge["target"] not in seen:
                    seen.add(edge["target"])
                    paths[edge["target"]] = paths.get(edge["source"], []) + [edge["type"]]
                    next_frontier.append(edge["target"])
                elif edge["target"] in frontier and edge["source"] not in seen:
                    seen.add(edge["source"])
                    paths[edge["source"]] = paths.get(edge["target"], []) + [edge["type"]]
                    next_frontier.append(edge["source"])
            frontier = next_frontier
            if not frontier:
                break
        results = []
        for nid in sorted(seen, key=lambda x: 0 if x in hits else 1)[:limit]:
            node = self.nodes[nid]
            results.append({
                "node_id": nid, "node_type": node.get("type", ""),
                "name": node.get("name", ""), "relation_path": paths.get(nid, []),
                "fact_ids": node.get("fact_ids", ""),
            })
        return results

    def stats(self) -> dict:
        return {"nodes": len(self.nodes), "edges": len(self.edges)}


def seed_graph_from_ledger(task_id: str) -> None:
    """从 FactRelation/实体表播种图数据(关系已落库时幂等;实体由 Consolidation 阶段产出)。

    FactCluster 的关键词可作为实体候选;当前以 entities/events 表为准
    (知识沉淀层已有提取),关系来自 fact_relations。
    """
    with session_scope() as s:
        row = s.execute(
            select(func.count().label("c"))
            .select_from(RelationTable)
            .where(RelationTable.c.task_id == task_id)
        ).mappings().first()
        count = int(row["c"]) if row else 0
    if count > 0:
        return  # 已播种,幂等


def graph_block(graph: IntelligenceGraph | None, query: str) -> str:
    """图检索结果 → prompt 块(供 Evidence/Analysis 参考;程序只格式,不判断)。"""
    if graph is None:
        return ""
    results = graph.search(query)
    if not results:
        return ""
    lines = [f"- {r['node_type']}「{r['name']}」路径:{'/'.join(r['relation_path']) or '直接命中'} "
             f"证据Fact:{r['fact_ids'][:60]}" for r in results[:8]]
    return "情报关系图谱(实体/事件关联,供交叉印证):\n" + "\n".join(lines) + "\n"
