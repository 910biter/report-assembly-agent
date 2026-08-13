"""Embedding 计算:统一经 Gateway 走远端模型。"""
from app.gateway import model_gateway


def embed_texts(texts: list[str]) -> list[list[float]]:
    return model_gateway.embed(texts)
