"""检索层:Embedding 计算(远端)+ Qdrant 向量存储/去重/检索。"""
from app.retrieval.embedder import embed_texts
from app.retrieval.store import vector_store

__all__ = ["embed_texts", "vector_store"]
