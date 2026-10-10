"""Regression tests for the local-first performance and evidence fixes."""
import json
import sys
import pytest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents.base import BaseAgent
from app.config import settings
from app.context import ContextManager
from app.gateway import OpenAICompatibleGateway
from app.models import Unit
from app.retrieval.rag import RetrievalHit, _rerank, _semantic_hits
from app.retrieval.store import QdrantVectorStore
from app.workflow.controller import WorkflowController
from app.model_connection import Connection


@pytest.fixture(autouse=True)
def model_connection():
    with patch("app.model_connection.load_connection", return_value=Connection(
        base_url="http://test.invalid/v1", model_name="test",
    )):
        yield


def _unit(uid, material, page=1, content="原始内容事实证据"):
    return Unit(id=uid, material_id=material, kind="text", page=page,
                paragraph=uid, content=content, metadata_json=json.dumps({"page": page}))


def test_no_query_embedding_when_vector_backend_is_off():
    with patch("app.retrieval.rag.vector_store.enabled", False), patch(
        "app.retrieval.rag.embed_texts", side_effect=AssertionError("should not embed")
    ):
        assert _semantic_hits("资料检索", 5, None) == []

    cm = ContextManager({"id": "t"}, {1: [_unit(1, 1)], 2: [_unit(2, 2)]},
                        {1: "a.pdf", 2: "b.pdf"})
    with patch("app.context.vector_store.enabled", False), patch.object(
        cm._query_compiler, "query_vector", side_effect=AssertionError("should not embed")
    ):
        batches = cm.for_first_pass([{"need": "事实证据"}], [])
    assert batches and "原始内容事实证据" in batches[0][0]


def test_deterministic_http_failures_do_not_retry_with_either_error_format():
    from app.graph.service import GraphExtractionAgent
    for agent in (BaseAgent(), GraphExtractionAgent()):
        for status in (400, 401, 403, 404, 405, 413, 422):
            assert not agent.should_retry(RuntimeError(f"MODEL_HTTP_{status}"))
            assert not agent.should_retry(RuntimeError(f"MODEL_HTTP_{status}:details"))
        assert agent.should_retry(RuntimeError("MODEL_HTTP_503"))


def test_evidence_batches_embedding_once_per_query_not_per_material():
    cm = ContextManager({"id": "t"}, {
        1: [_unit(1, 1)], 2: [_unit(2, 2)], 3: [_unit(3, 3)],
    }, {1: "a.pdf", 2: "b.pdf", 3: "c.pdf"})
    with patch("app.context.vector_store.enabled", True), patch(
        "app.context.vector_store.unit_vectors", return_value={i: [0.7, 0.7] for i in range(1, 4)}
    ), patch("app.retrieval.embedder.embed_texts", return_value=[[0.7, 0.7]]) as embed:
        batches = cm.for_evidence_batches("维度", [], ["需求A", "需求B"])
    assert batches
    assert embed.call_count == 3  # 3 queries, not 3 queries * 3 materials


def test_writer_queries_reuse_fact_vectors_and_invalidate_on_fact_change():
    import numpy as np

    cm = ContextManager({"id": "task-1"})
    facts = [{"id": 1, "content": "安全加速"}, {"id": 2, "content": "模型推理"}]
    with patch("app.context.vector_store.enabled", True), patch(
        "app.context.vector_store.fact_vectors",
        return_value=[(1, np.asarray([1.0, 0.0])), (2, np.asarray([0.0, 1.0]))],
    ) as fetch, patch.object(
        cm._query_compiler, "query_vector", return_value=[1.0, 0.0]
    ):
        assert len(cm._retrieve_facts("安全", facts, top_k=2)) == 2
        assert len(cm._retrieve_facts("加速", facts, top_k=2)) == 2
        assert fetch.call_count == 1
        cm._retrieve_facts("模型", [*facts, {"id": 3, "content": "新增事实"}], top_k=2)
        assert fetch.call_count == 2


def test_fact_indexing_is_no_op_without_qdrant():
    from app.evidence.extractor import _index_fact_vectors

    fact = SimpleNamespace(id=7, content="加速事实")
    with patch("app.evidence.extractor.vector_store.enabled", False), patch(
        "app.evidence.extractor.embed_texts", side_effect=AssertionError("must not embed")
    ):
        _index_fact_vectors([fact], "task-a")
    store = Mock(enabled=True)
    with patch("app.evidence.extractor.vector_store", store), patch(
        "app.evidence.extractor.embed_texts", return_value=[[0.1, 0.2]]
    ) as embed:
        _index_fact_vectors([fact], "task-a")
    embed.assert_called_once_with(["加速事实"])
    store.save_fact_vector.assert_called_once_with(7, [0.1, 0.2], task_id="task-a")


def test_diverse_sources_actually_receive_a_boost():
    items = {1: [_unit(1, 1), _unit(2, 1)], 2: [_unit(3, 2)]}
    hits = [RetrievalHit(unit_id=i, score=0.8, source="keyword") for i in (1, 2, 3)]
    result = _rerank(hits, items, "事实", ["事实"])
    assert [hit.unit_id for hit in result] == [1, 3, 2]


class _DummyPoint:
    def __init__(self, id, vector, payload):
        self.id, self.vector, self.payload = id, vector, payload


class _FakeQdrant:
    def __init__(self, bad_id=None):
        self.batches = []
        self.bad_id = bad_id

    def upsert(self, collection_name, points, wait=False):
        ids = [point.id for point in points]
        if self.bad_id in ids:
            raise ValueError("bad payload")
        self.batches.append(ids)


def test_bulk_qdrant_upsert_is_chunked_and_tracks_acknowledgements():
    store = QdrantVectorStore()
    store.enabled = True
    store.client = _FakeQdrant()
    pairs = [(i, [0.5, 0.5]) for i in range(1, 101)]
    with patch.dict(sys.modules, {"qdrant_client.models": SimpleNamespace(PointStruct=_DummyPoint)}), patch.object(
        store, "_unit_payloads", return_value={i: {"unit_id": i} for i in range(1, 101)}
    ) as payloads, patch.object(store, "ensure_collection") as ensure:
        saved = store.save_unit_vectors(pairs, batch_size=32)
    assert saved == set(range(1, 101))
    assert [len(batch) for batch in store.client.batches] == [32, 32, 32, 4]
    payloads.assert_called_once()
    ensure.assert_called_once()



def test_controller_vector_bulk_write_does_not_mark_failed_ids_as_indexed():
    controller = WorkflowController.__new__(WorkflowController)
    store = Mock(enabled=True)
    store.save_unit_vectors.return_value = {1, 3}
    store.save_material_vector.return_value = True
    units = [_unit(i, 1) for i in (1, 2, 3)]
    with patch("app.workflow.controller.vector_store", store), patch(
        "app.retrieval.embedder.embed_texts_adaptive",
        return_value=([[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]], []),
    ):
        error = controller._embed_units(1, [1, 2, 3], units)
    assert error == "VECTOR_STORE_WRITE_FAILED:1"
    store.save_unit_vectors.assert_called_once()
    assert store.save_material_vector.call_count == 1


def test_bulk_service_failure_stops_without_recursive_requests():
    store = QdrantVectorStore()
    store.enabled = True
    store.client = Mock()
    store.client.upsert.side_effect = TimeoutError("unavailable")
    with patch.object(store, "_unit_payloads", return_value={i: {"unit_id": i} for i in range(100)}), patch.object(store, "ensure_collection"):
        assert store.save_unit_vectors([(i, [1.0, 0.0]) for i in range(100)]) == set()
    assert store.client.upsert.call_count == 1


def test_bulk_invalid_vectors_and_missing_provenance_are_not_indexed():
    store = QdrantVectorStore()
    store.enabled = True
    store.client = Mock()
    with patch.object(store, "_unit_payloads", return_value={1: {"unit_id": 1}, 2: {"unit_id": 2}}), patch.object(store, "ensure_collection"):
        assert store.save_unit_vectors([(1, [1.0, 0.0]), (2, [float("nan"), 0.0]), (3, [1.0, 0.0])]) == {1}


def test_fact_cache_refreshes_content_changes_and_expired_vectors():
    import numpy as np
    cm = ContextManager({"id": "t"})
    with patch("app.context.vector_store.enabled", True), patch("app.context.vector_store.fact_vectors", return_value=[(1, np.asarray([1.0, 0.0]))]) as fetch, patch.object(cm._query_compiler, "query_vector", return_value=[1.0, 0.0]), patch("app.context.time.monotonic", side_effect=[10, 10, 11, 16, 16]):
        cm._retrieve_facts("q", [{"id": 1, "content": "a"}], 1)
        cm._retrieve_facts("q", [{"id": 1, "content": "b"}], 1)
        cm._retrieve_facts("q", [{"id": 1, "content": "b"}], 1)
        cm._retrieve_facts("q", [{"id": 1, "content": "b"}], 1)
    assert fetch.call_count == 3
