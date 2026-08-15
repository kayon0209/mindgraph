import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from application.document_lifecycle_service import DocumentLifecycleService
from application.index_lifecycle_service import IndexLifecycleService
from domain.errors import ConflictError
from infrastructure.database import ProductDatabase
from retrieval.dense import FAISSDenseRetriever


class FakeEmbedding:
    model_name = "fake-bge"
    model_revision = "r1"
    dimension = 3
    calls = 0

    def embed_documents(self, texts):
        type(self).calls += len(texts)
        return [[1.0, 0.0, 0.0] if "交通" in text else [0.0, 1.0, 0.0] for text in texts]

    def embed_query(self, text): return [1.0, 0.0, 0.0]


class LifecycleIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True); root = Path(self.temp.name)
        self.db = ProductDatabase(root / "product.sqlite3"); self.db.initialize()
        self.documents = DocumentLifecycleService(self.db, root / "documents")
        self.indexes = IndexLifecycleService(self.db, self.documents, root / "indexes")
        FakeEmbedding.calls = 0

    def tearDown(self): self.temp.cleanup()

    def test_document_transitions_replacement_expiration_and_date_filter(self):
        v1 = self.documents.create_version("travel.md", "# 交通\n第一条 交通费可报销".encode(), "travel", "v1", "travel_expense", "official_policy", "2025-01-01", None, "active")
        v2 = self.documents.create_version("travel.md", "# 交通\n第二条 新标准".encode(), "travel", "v2", "travel_expense", "official_policy", "2026-01-01", None, "draft")
        with self.assertRaises(ConflictError): self.documents.transition(v2.document_id, "active")
        self.documents.transition(v2.document_id, "pending_index"); self.documents.transition(v2.document_id, "active")
        self.assertEqual(self.documents.get(v1.document_id).status, "replaced")
        self.assertTrue(all(item["document_version"] == "v2" for item in self.documents.active_chunks("2026-07-01")))
        self.documents.transition(v2.document_id, "expired")
        self.assertEqual(self.documents.active_chunks(), [])

    def test_embedding_reuse_manifest_activation_rollback_and_reload(self):
        self.documents.create_version("travel.md", "# 交通\n第一条 交通费可报销".encode(), "travel", "v1", "travel_expense", "official_policy", status="active")
        with patch("application.index_lifecycle_service.BGEEmbeddingProvider", return_value=FakeEmbedding()):
            first = self.indexes.build(); first_calls = FakeEmbedding.calls
            second = self.indexes.build()
        self.assertGreater(first_calls, 0); self.assertEqual(FakeEmbedding.calls, first_calls)
        self.assertEqual(second["reused_embeddings"], second["chunk_count"])
        self.assertEqual(second["previous_index_version"], first["index_version"])
        self.assertEqual(self.indexes.rollback(reason="test")["index_version"], first["index_version"])
        loaded = FAISSDenseRetriever(FakeEmbedding(), Path(self.temp.name) / "indexes" / first["index_version"]); loaded.load()
        self.assertTrue(loaded.search("交通", 1)[0])
        audits = self.db.fetch_all("SELECT * FROM index_audit WHERE action='rollback'")
        self.assertEqual(len(audits), 1)

    def test_failed_build_preserves_active_pointer(self):
        self.documents.create_version("travel.md", "# 交通\n规则".encode(), "travel", "v1", "travel_expense", "official_policy", status="active")
        with patch("application.index_lifecycle_service.BGEEmbeddingProvider", return_value=FakeEmbedding()): self.indexes.build()
        current = (Path(self.temp.name) / "indexes" / "CURRENT").read_text()
        with patch("application.index_lifecycle_service.BGEEmbeddingProvider", side_effect=RuntimeError("load failed")):
            with self.assertRaises(RuntimeError): self.indexes.build()
        self.assertEqual((Path(self.temp.name) / "indexes" / "CURRENT").read_text(), current)


if __name__ == "__main__": unittest.main()
