"""轻量知识关联层。"""
from app.knowledge.graph import KnowledgeAgent, load_entity_names, load_events, save_entity

__all__ = ["KnowledgeAgent", "save_entity", "load_entity_names", "load_events"]
