import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from api.dependencies import override_container
from api.main import app
from application.chat_service import ChatService
from application.feedback_service import FeedbackService
from application.knowledge_service import KnowledgeService
from domain.models import DocumentRecord, EvaluationRun, IndexStatus
from infrastructure.database import ProductDatabase
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace
from ui.api_client import APIClientError, ProductAPIClient


CHUNK = Chunk("policy.md::1", "差旅费应在十个工作日内报销。", "policy.md", 1, "报销时限")


class FakeProvider:
    model_name = "fake-zhipu"
    provider_name = "fake"

    def __init__(self, available=True, fail=False):
        self.available, self.fail = available, fail

    def complete(self, messages):
        if self.fail:
            raise TimeoutError("timeout")
        return "差旅费应在十个工作日内报销 [citation-1]。", {"usage_source": "provider_reported", "input_tokens": 10, "output_tokens": 8, "total_tokens": 18}

    def stream(self, messages):
        if self.fail:
            raise TimeoutError("timeout")
        yield {"delta": "差旅费应在"}
        yield {"delta": "十个工作日内报销。"}
        yield {"usage": {"usage_source": "provider_reported", "input_tokens": 10, "output_tokens": 8, "total_tokens": 18}}


class FakePipeline:
    def __init__(self, empty=False, degraded=False):
        self.empty, self.degraded = empty, degraded

    def retrieve(self, query, strategy):
        candidate = RetrievalCandidate(CHUNK, dense_score=0.8, dense_rank=1, sparse_score=4.2, sparse_rank=1, rrf_score=0.03, fused_rank=1, final_rank=1)
        return RetrievalTrace(query=query, requested_strategy=strategy, actual_strategy="hybrid" if self.degraded else strategy,
            degraded=self.degraded, degradation_reason="reranker_error" if self.degraded else None,
            candidate_counts={"dense": 1, "sparse": 1, "fused": 1, "reranked": 0, "final": 0 if self.empty else 1},
            dense_results=[candidate], sparse_results=[candidate], fused_results=[candidate],
            final_selected_chunks=[] if self.empty else [candidate], latency_ms={"query_embedding_ms": 1.0, "dense_retrieval_ms": 0.1, "bm25_retrieval_ms": 0.1, "fusion_ms": 0.1, "total_retrieval_ms": 1.3})


class FakeKnowledge:
    def __init__(self):
        self.deleted = None

    def index_status(self):
        return IndexStatus(index_version="test", status="ready", embedding_model="fake", vector_dimension=3, chunk_count=1, created_at="now", pending_changes=False)

    def list_documents(self): return []
    def get_document(self, document_id): raise RuntimeError("not used")
    def upload(self, filename, content, category):
        return DocumentRecord(document_id="doc1", document_name=filename, knowledge_category=category, version="v1", chunk_count=0, index_status="pending", uploaded_at="2026-01-01T00:00:00Z", pending_reindex=True)
    def delete(self, document_id):
        self.deleted = document_id
        return DocumentRecord(document_id=document_id, document_name="x.md", knowledge_category="upload", version="v1", chunk_count=1, index_status="pending_deletion", uploaded_at="2026-01-01T00:00:00Z", pending_reindex=True)
    def rebuild(self): return self.index_status()


class FakeEvaluation:
    def __init__(self): self.runs = {}
    def create(self, payload):
        run = EvaluationRun(run_id="run1", status="queued", dataset_name=payload.dataset_name, dataset_version="1.0.0", retrieval_strategy=",".join(payload.retrieval_strategies), configuration=payload.model_dump())
        self.runs[run.run_id] = run; return run
    def execute(self, run_id): self.runs[run_id].status = "completed"
    def list(self): return list(self.runs.values())
    def get(self, run_id): return self.runs[run_id]


class Milestone3APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database = ProductDatabase(Path(self.temp.name) / "product.sqlite3")
        database.initialize()
        self.provider = FakeProvider()
        self.pipeline = FakePipeline()
        self.knowledge = FakeKnowledge()
        self.evaluation = FakeEvaluation()
        container = SimpleNamespace(database=database, provider=self.provider,
            chat=ChatService(database, lambda top_k: self.pipeline, self.provider),
            feedback=FeedbackService(database), knowledge=self.knowledge, evaluation=self.evaluation)
        override_container(container)
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close(); override_container(None); self.temp.cleanup()

    def test_health_public_config_and_validation(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code, 200)
        config = self.client.get("/api/v1/config/public").json()
        self.assertNotIn("api_key", json.dumps(config).lower())
        self.assertEqual(self.client.post("/api/v1/chat", json={"question": " "}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/chat", json={"question": "test", "retrieval_strategy": "invalid"}).status_code, 422)

    def test_normal_chat_and_citation_consistency(self):
        response = self.client.post("/api/v1/chat", json={"question": "差旅费多久报销？", "retrieval_strategy": "hybrid"})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["result_state"], "answered")
        self.assertEqual(result["citations"][0]["chunk_id"], result["retrieval_trace"]["final_chunks"][0]["chunk"]["chunk_id"])

    def test_out_of_scope_empty_index_provider_missing_timeout_and_degradation(self):
        self.assertEqual(self.client.post("/api/v1/chat", json={"question": "公司工资是多少"}).json()["result_state"], "out_of_scope")
        self.pipeline.empty = True
        self.assertEqual(self.client.post("/api/v1/chat", json={"question": "未知报销问题"}).json()["result_state"], "insufficient_evidence")
        self.pipeline.empty = False; self.provider.available = False
        self.assertEqual(self.client.post("/api/v1/chat", json={"question": "差旅费"}).json()["result_state"], "model_unavailable")
        self.provider.available = True; self.provider.fail = True
        self.assertEqual(self.client.post("/api/v1/chat", json={"question": "差旅费"}).json()["degradation_reason"], "provider_error")
        self.provider.fail = False; self.pipeline.degraded = True
        result = self.client.post("/api/v1/chat", json={"question": "差旅费", "retrieval_strategy": "hybrid_rerank"}).json()
        self.assertTrue(result["degraded"]); self.assertEqual(result["actual_strategy"], "hybrid")

    def test_sse_event_order_and_payload(self):
        response = self.client.post("/api/v1/chat/stream", json={"question": "差旅费多久报销？", "retrieval_strategy": "hybrid"})
        events = [line.split(":", 1)[1].strip() for line in response.text.splitlines() if line.startswith("event:")]
        required = ["request_started", "retrieval_started", "retrieval_completed", "generation_started", "answer_delta", "citations", "usage", "completed"]
        positions = [events.index(name) for name in required]
        self.assertEqual(positions, sorted(positions))
        data = [json.loads(line.split(":", 1)[1]) for line in response.text.splitlines() if line.startswith("data:")]
        self.assertTrue(all({"request_id", "event", "timestamp", "data"} <= item.keys() for item in data))

    def test_feedback_duplicate_bad_case_update_and_export(self):
        request_id = self.client.post("/api/v1/chat", json={"question": "差旅费多久报销？"}).json()["request_id"]
        payload = {"request_id": request_id, "rating": "not_helpful", "reason_codes": ["wrong_citation"]}
        self.assertEqual(self.client.post("/api/v1/feedback", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/v1/feedback", json=payload).status_code, 409)
        cases = self.client.get("/api/v1/bad-cases").json()
        self.assertEqual(cases[0]["error_category"], "unclassified")
        case_id = cases[0]["bad_case_id"]
        updated = self.client.patch(f"/api/v1/bad-cases/{case_id}", json={"status": "resolved", "error_category": "citation_error", "resolution": "fixed"}).json()
        self.assertEqual(updated["status"], "resolved")
        export = self.client.get("/api/v1/bad-cases/export").text
        self.assertIn("True", export)

    def test_invalid_upload_document_delete_rebuild_and_evaluation_lifecycle(self):
        invalid = self.client.post("/api/v1/knowledge/documents", files={"file": ("x.exe", b"x", "application/octet-stream")})
        self.assertEqual(invalid.status_code, 422)
        uploaded = self.client.post("/api/v1/knowledge/documents", files={"file": ("x.md", "# policy".encode(), "text/markdown")})
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(self.client.delete("/api/v1/knowledge/documents/doc1").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/knowledge/index/rebuild").status_code, 200)
        run = self.client.post("/api/v1/evaluations/runs", json={"retrieval_strategies": ["hybrid"]}).json()
        self.assertIn(self.client.get(f"/api/v1/evaluations/runs/{run['run_id']}").json()["status"], {"queued", "completed"})


class KnowledgeAndClientTests(unittest.TestCase):
    def test_failed_rebuild_preserves_previous_index_and_document_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); docs = root / "docs"; uploads = root / "uploads"; indexes = root / "indexes"
            docs.mkdir(); uploads.mkdir(); indexes.mkdir(); (docs / "official.md").write_text("# official", encoding="utf-8")
            (uploads / "custom.md").write_text("# custom", encoding="utf-8"); (indexes / "CURRENT").write_text("old", encoding="utf-8")
            service = KnowledgeService(docs, uploads, indexes)
            custom = next(item for item in service.list_documents() if item.document_name == "custom.md")
            service.delete(custom.document_id)
            self.assertFalse((uploads / "custom.md").exists())
            with patch("application.knowledge_service.build_versioned_index", side_effect=RuntimeError("build failed")):
                with self.assertRaises(RuntimeError): service.rebuild()
            self.assertEqual((indexes / "CURRENT").read_text(encoding="utf-8"), "old")

    def test_streamlit_api_client_backend_unavailable(self):
        client = ProductAPIClient("http://test")
        with patch("httpx.request", side_effect=httpx.ConnectError("offline")):
            with self.assertRaisesRegex(APIClientError, "Backend unavailable"):
                client.health()

    def test_api_client_request_timeout_can_be_overridden(self):
        client = ProductAPIClient("http://test", timeout=30.0)
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {"run_id": "run1"}
        with patch("httpx.request", return_value=response) as request:
            client.create_evaluation({"retrieval_strategies": ["hybrid"]})
            self.assertEqual(request.call_args.kwargs["timeout"], 300.0)
            client.health()
            self.assertEqual(request.call_args.kwargs["timeout"], 30.0)


if __name__ == "__main__":
    unittest.main()
