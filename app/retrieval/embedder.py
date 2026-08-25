"""Embedding 计算:统一经 Gateway 走远端模型。

自适应分批:按 token 预算(模型物理上下文上限)动态分组,
单批超时按 workload(字符数)与历史吞吐反馈动态计算,
超时/失败时批量减半重试(不整份失败,不固定批数/秒数)。
"""
import time

from app.gateway import model_gateway
from app.llm_scheduler import embedding_num_gpu, invoke

# 模型物理上下文上限(token):embedding 输入长度物理上限(允许硬编码)
_EMBED_TOKEN_BUDGET = 8192
# 历史吞吐反馈(chars/s,最近 5 次):驱动动态超时与后续分批
_throughput: list[float] = []


def embed_texts(texts: list[str], query: bool = False) -> list[list[float]]:
    return invoke("embed", model_gateway.embed, texts, query=query)


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
    batches = _batch_by_tokens(texts, _EMBED_TOKEN_BUDGET)
    vectors: list[list[float] | None] = [None] * len(texts)
    errors: list[str] = []
    offset = 0
    for batch in batches:
        _embed_chunk(list(batch), offset, vectors, errors)
        offset += len(batch)
    return vectors, errors


def _embed_chunk(chunk: list[str], base: int, vectors: list, errors: list[str]) -> None:
    """递归减半重试:整批失败 → 后半先试,前半继续减半(不丢数据)。"""
    while chunk:
        try:
            t0 = time.time()
            vecs = invoke(
                "embed", model_gateway.embed, chunk,
                timeout=_dynamic_timeout(chunk),
                num_gpu=embedding_num_gpu(),  # 动态放置:agent 活跃时 CPU,否则 GPU
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
