from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import faiss
import numpy as np

from domain.errors import NotFoundError
from infrastructure.database import ProductDatabase, dumps, loads
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.types import Chunk


class IndexLifecycleService:
    def __init__(self, database: ProductDatabase, documents, index_root: Path, invalidate=lambda: None) -> None:
        self.database, self.documents, self.index_root, self.invalidate = database, documents, Path(index_root), invalidate
        self.index_root.mkdir(parents=True, exist_ok=True)

    def _current(self):
        try: return (self.index_root / "CURRENT").read_text(encoding="utf-8").strip()
        except OSError: return None

    def build(self, operator: str = "local"):
        provider = BGEEmbeddingProvider(); active = self.documents.active_chunks(include_historical=True)
        version = datetime.now(timezone.utc).strftime("m4-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]; directory = self.index_root / version; directory.mkdir()
        chunks, vectors, reused = [], [], 0
        try:
            for item in active:
                checksum = item["checksum"]
                row = self.database.fetch_one("SELECT embedding_json,dimension FROM embedding_cache WHERE model_name=? AND model_revision=? AND chunk_checksum=?", (provider.model_name, provider.model_revision, checksum))
                if row and row["dimension"] == provider.dimension:
                    vector = json.loads(row["embedding_json"]); reused += 1
                else:
                    vector = provider.embed_documents([item["text"]])[0]
                    self.database.execute("INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?,?,?)", (provider.model_name, provider.model_revision, checksum, provider.dimension, dumps(vector), datetime.now(timezone.utc).isoformat()))
                vectors.append(vector)
                chunks.append(Chunk(chunk_id=item["child_chunk_id"], text=item["text"], document_id=item["document_id"], chunk_index=len(chunks), section_path=" / ".join(item["heading_path"]), metadata=item))
            if not chunks: raise ValueError("No active chunks")
            matrix = np.asarray(vectors, dtype="float32"); faiss.normalize_L2(matrix); index = faiss.IndexFlatIP(matrix.shape[1]); index.add(matrix)
            faiss.write_index(index, str(directory / "dense.faiss")); (directory / "chunks.json").write_text(json.dumps([chunk.__dict__ for chunk in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
            previous = self._current(); manifest = {"index_version": version, "active_document_versions": sorted({f"{item['logical_document_id']}:{item['document_version']}" for item in active if item['document_status'] == 'active'}),
                "searchable_document_versions": sorted({f"{item['logical_document_id']}:{item['document_version']}" for item in active}),
                "active_chunk_ids": [item.chunk_id for item in chunks], "embedding_model_name": provider.model_name,
                "embedding_model_revision": provider.model_revision, "vector_dimension": provider.dimension,
                "chunker": {"child_size": 500, "parent_size": 1200, "overlap": 50}, "bm25": {"k1": 1.5, "b": 0.75},
                "rrf": {"constant": 60}, "reranker_model": os.getenv("RERANKER_MODEL_NAME", "BAAI/bge-reranker-base"),
                "created_at": datetime.now(timezone.utc).isoformat(), "corpus_checksum": __import__('hashlib').sha256(''.join(item['checksum'] for item in active).encode()).hexdigest(),
                "build_status": "validated", "previous_index_version": previous, "chunk_count": len(chunks), "metadata_count": len(chunks), "reused_embeddings": reused, "new_embeddings": len(chunks)-reused}
            (directory / "metadata.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"); (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            loaded = faiss.read_index(str(directory / "dense.faiss")); assert loaded.ntotal == len(chunks)
            self.database.execute("INSERT INTO index_builds VALUES (?,?,?,?,?,?,?)", (version, "validated", dumps(manifest), previous, manifest["created_at"], None, None))
            self.activate(version, operator, "validated build")
            self.database.execute("UPDATE document_versions SET indexed_at=?,status='active' WHERE status='active'", (datetime.now(timezone.utc).isoformat(),))
            return manifest
        except Exception as exc:
            failure = {"index_version": version, "build_status": "failed", "failure_reason": f"{type(exc).__name__}: {exc}"}
            (directory / "manifest.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
            self.database.execute("INSERT OR REPLACE INTO index_builds VALUES (?,?,?,?,?,?,?)", (version, "failed", dumps(failure), self._current(), datetime.now(timezone.utc).isoformat(), None, failure["failure_reason"]))
            raise

    def versions(self): return [self._public(row) for row in self.database.fetch_all("SELECT * FROM index_builds ORDER BY created_at DESC")]
    def get(self, version):
        row = self.database.fetch_one("SELECT * FROM index_builds WHERE index_version=?", (version,));
        if not row: raise NotFoundError("Index version not found")
        return self._public(row)

    def activate(self, version, operator="local", reason="manual activation"):
        row = self.get(version)
        if row["status"] != "validated": raise ValueError("Only validated indexes can be activated")
        previous = self._current(); temp = self.index_root / "CURRENT.tmp"; temp.write_text(version, encoding="utf-8"); temp.replace(self.index_root / "CURRENT")
        now = datetime.now(timezone.utc).isoformat(); self.database.execute("UPDATE index_builds SET activated_at=? WHERE index_version=?", (now, version))
        self.database.execute("INSERT INTO index_audit VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), "activate", previous, version, operator, reason, now)); self.invalidate(); return self.get(version)

    def rollback(self, operator="local", reason="manual rollback"):
        current = self._current()
        if current is None:
            raise ValueError("No current index to rollback from")
        row = self.get(current); previous = row["previous_index_version"]
        if not previous: raise ValueError("No previous index available")
        target = self.get(previous)
        if target["status"] != "validated": raise ValueError("Previous index is not validated")
        temp = self.index_root / "CURRENT.tmp"; temp.write_text(previous, encoding="utf-8"); temp.replace(self.index_root / "CURRENT")
        now = datetime.now(timezone.utc).isoformat(); self.database.execute("INSERT INTO index_audit VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), "rollback", current, previous, operator, reason, now)); self.invalidate(); return self.get(previous)

    @staticmethod
    def _public(row):
        return {"index_version": row["index_version"], "status": row["status"], "manifest": loads(row["manifest_json"], {}), "previous_index_version": row["previous_index_version"], "created_at": row["created_at"], "activated_at": row["activated_at"], "failure_reason": row["failure_reason"]}
