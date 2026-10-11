from app.graph.service import GraphService


class _LegacyOffGraphService(GraphService):
    @property
    def mode(self):
        return "off"


class _Projector:
    def __init__(self, configured=False, rows=None):
        self.configured = configured
        self.rows = rows or []

    def available(self):
        return self.configured

    def neighborhood_for_fact_ids(self, task_id, fact_ids, limit=12):
        return self.rows[:limit]


def test_graph_status_separates_postgres_rag_from_neo4j_projection():
    service = GraphService()
    service.projector = _Projector(configured=False)

    status = service.capability_status()

    assert status["graph_rag"] == {"status": "available", "backend": "postgresql"}
    assert status["neo4j_projection"] == {"status": "not_configured"}


def test_graph_context_does_not_depend_on_legacy_mode_label():
    service = _LegacyOffGraphService()
    service.projector = _Projector(rows=[{
        "subject": "A",
        "predicate": "关联",
        "object": "B",
        "fact_ids": [1],
        "confidence": "high",
    }])

    context = service.context_for_analysis("task-1", [{"id": 1}], limit=1)
    context = service.context_for_analysis("task-1", [{"id": 1}], limit=1)

    assert "关系图谱" in context
    assert "A" in context and "B" in context
