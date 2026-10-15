"""Embedding 计算:统一经 Gateway 走远端模型。

自适应分批:按 token 预算(模型物理上下文上限)动态分组,
单批超时按 workload(字符数)与历史吞吐反馈动态计算,
超时/失败时批量减半重试(不整份失败,不固定批数/秒数)。
"""
import hashlib
import time
import unicodedata
from collections import OrderedDict
from threading import RLock

from app.gateway import model_gateway
from app.llm_scheduler import invoke

# 模型物理上下文上限(token):embedding 输入长度物理上限(允许硬编码)
_EMBED_TOKEN_BUDGET = 8192
# 历史吞吐反馈(chars/s,最近 5 次):驱动动态超时与后续分批
_throughput: list[float] = []
_VECTOR_CACHE_LIMIT = 2048
_vector_cache: OrderedDict[str, list[float]] = OrderedDict()
_vector_cache_lock = RLock()
_embedding_dedupe_lock = RLock()
_vector_cache_stats = {"hits": 0, "misses": 0, "evictions": 0}


def _normalized_text(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "").replace("\r\n", "\n").replace("\r", "\n"))


def _cache_key(text: str, query: bool) -> tuple[str, str]:
    from app.config import settings

    normalized = _normalized_text(text)
    identity = "\0".join((
        str(settings.embedding_backend or ""),
        str(settings.embedding_model or ""),
        str(settings.embedding_model_path or ""),
        str(settings.embedding_max_length or ""),
        "query" if query else "document",
        str(settings.embedding_query_instruction or "") if query else "",
        normalized,
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest(), normalized


def _cache_get(key: str) -> list[float] | None:
    with _vector_cache_lock:
        value = _vector_cache.get(key)
        if value is None:
            _vector_cache_stats["misses"] += 1
            return None
        _vector_cache.move_to_end(key)
        _vector_cache_stats["hits"] += 1
        return list(value)


def _cache_put(key: str, vector: list[float]) -> None:
    with _vector_cache_lock:
        _vector_cache[key] = [float(value) for value in vector]
        _vector_cache.move_to_end(key)
        while len(_vector_cache) > _VECTOR_CACHE_LIMIT:
            _vector_cache.popitem(last=False)
            _vector_cache_stats["evictions"] += 1


def embedding_cache_stats(*, reset: bool = False) -> dict:
    """Return bounded in-process cache counters; no source text is retained in keys."""
    with _vector_cache_lock:
        result = {**_vector_cache_stats, "entries": len(_vector_cache), "capacity": _VECTOR_CACHE_LIMIT}
        if reset:
            for key in _vector_cache_stats:
                _vector_cache_stats[key] = 0
        return result


def embed_texts(texts: list[str], query: bool = False) -> list[list[float]]:
    if not texts:
        return []
    with _embedding_dedupe_lock:
        return _embed_texts_cached(texts, query)


def _embed_texts_cached(texts: list[str], query: bool) -> list[list[float]]:
    keys_and_texts = [_cache_key(text, query) for text in texts]
    vectors: dict[str, list[float]] = {}
    missing: dict[str, str] = {}
    for key, normalized in keys_and_texts:
        cached = _cache_get(key)
        if cached is not None:
            vectors[key] = cached
        else:
            missing.setdefault(key, normalized)
    if missing:
        values = invoke("embed", model_gateway.embed, list(missing.values()), query=query)
        if len(values) != len(missing):
            raise RuntimeError("EMBEDDING_RESULT_COUNT_MISMATCH")
        for key, vector in zip(missing, values):
            vectors[key] = list(vector)
            _cache_put(key, vector)
    return [list(vectors[key]) for key, _text in keys_and_texts]


def _batch_by_tokens(texts: list[str], budget: int) -> list[list[str]]:
    """按 token 预算动态分组(中文约 2 字符/token)。"""
    batches: list[list[str]] = []
    cur: list[str] = []
    cur_tokens = 0
    for t in texts:
        tk = max(1, len(t or "") // 2)
        if cur and cur_tokens + tk > budget:
            batches.append(cur)
            cur, cur_tokens = [], 0
        cur.append(t)
        cur_tokens += tk
    if cur:
        batches.append(cur)
    return batches


def _dynamic_timeout(batch: list[str]) -> int:
    """超时 = 工作量(字符)/ 吞吐估计 + 余量;吞吐来自历史反馈(自适应)。"""
    chars = sum(len(t or "") for t in batch)
    if _throughput:
        est = chars / max(sum(_throughput) / len(_throughput), 1)
    else:
        est = chars / 200  # 初始保守估计(远端 CPU 中文 ~200 chars/s)
    return max(20, min(300, int(est) + 20))


def embed_texts_adaptive(texts: list[str]) -> tuple[list[list[float] | None], list[str]]:
    """自适应 embedding:token 预算分批 + 动态超时 + 超时减半重试。

    返回 (向量列表[与输入对齐,失败项为 None], 错误列表)。
    最小粒度批仍失败 → 跳过该条(不整份失败,向量缺失由检索词法回退)。
    """
    if not texts:
        return [], []
    with _embedding_dedupe_lock:
        return _embed_texts_adaptive_cached(texts)


def _embed_texts_adaptive_cached(texts: list[str]) -> tuple[list[list[float] | None], list[str]]:
    keys_and_texts = [_cache_key(text, False) for text in texts]
    unique_texts: dict[str, str] = {}
    unique_vectors: dict[str, list[float] | None] = {}
    for key, normalized in keys_and_texts:
        cached = _cache_get(key)
        if cached is not None:
            unique_vectors[key] = cached
        else:
            unique_texts.setdefault(key, normalized)
    pending = list(unique_texts.items())
    pending_vectors: list[list[float] | None] = [None] * len(pending)
    errors: list[str] = []
    offset = 0
    batches = _batch_by_tokens([text for _key, text in pending], _EMBED_TOKEN_BUDGET)
    for batch in batches:
        _embed_chunk(list(batch), offset, pending_vectors, errors)
        offset += len(batch)
    for (key, _text), vector in zip(pending, pending_vectors):
        unique_vectors[key] = vector
        if vector is not None:
            _cache_put(key, vector)
    return [unique_vectors.get(key) for key, _text in keys_and_texts], errors


def _embed_chunk(chunk: list[str], base: int, vectors: list, errors: list[str]) -> None:
    """递归减半重试:整批失败 → 后半先试,前半继续减半(不丢数据)。"""
    while chunk:
        try:
            t0 = time.time()
            vecs = invoke(
                "embed", model_gateway.embed, chunk,
                timeout=_dynamic_timeout(chunk),
            )
            elapsed = time.time() - t0
            chars = sum(len(t or "") for t in chunk)
            _throughput.append(chars / max(elapsed, 0.1))
            del _throughput[:-5]
            for i, v in enumerate(vecs):
                vectors[base + i] = v
            return
        except Exception as exc:
            if len(chunk) <= 1:
                errors.append(str(exc))
                return
            half = len(chunk) // 2
            _embed_chunk(chunk[half:], base + half, vectors, errors)  # 后半(可能更大更慢)先降级
            chunk = chunk[:half]  # 前半继续尝试
