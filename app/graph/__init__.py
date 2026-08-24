"""Evidence-grounded knowledge graph services.

PostgreSQL owns the canonical graph records and provenance. Neo4j is an
optional query projection that can be rebuilt from those records at any time.
"""
from app.graph.service import GraphService, graph_service

__all__ = ["GraphService", "graph_service"]
