"""简单向量存储，替代 ChromaDB。基于 SQLite3 + NumPy，无需任何原生扩展。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np


class Collection:
    def __init__(self, db_path: str, name: str) -> None:
        self._db_path = db_path
        self._name = name
        conn = self._conn()
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{self._name}" (
                id       TEXT PRIMARY KEY,
                document TEXT,
                metadata TEXT,
                embedding BLOB
            )
        """)
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, check_same_thread=False)

    def count(self) -> int:
        conn = self._conn()
        cur = conn.execute(f'SELECT COUNT(*) FROM "{self._name}"')
        n = int(cur.fetchone()[0])
        conn.close()
        return n

    def add(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict],
    ) -> None:
        rows = []
        for id_, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            blob = np.array(emb, dtype=np.float32).tobytes()
            rows.append((id_, doc, json.dumps(meta, ensure_ascii=False), blob))
        conn = self._conn()
        conn.executemany(
            f'INSERT OR REPLACE INTO "{self._name}" (id, document, metadata, embedding) VALUES (?,?,?,?)',
            rows,
        )
        conn.commit()
        conn.close()

    def query(
        self,
        query_embeddings: List[List[float]],
        n_results: int = 3,
        include: Optional[List[str]] = None,
    ) -> Dict:
        q = np.array(query_embeddings[0], dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm

        conn = self._conn()
        cur = conn.execute(
            f'SELECT document, metadata, embedding FROM "{self._name}"'
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

        docs, metas, embs = [], [], []
        for doc, meta, blob in rows:
            docs.append(doc)
            metas.append(json.loads(meta))
            embs.append(np.frombuffer(blob, dtype=np.float32))

        matrix = np.stack(embs)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix = matrix / norms

        scores = matrix @ q
        distances = (1.0 - scores).tolist()

        k = min(n_results, len(rows))
        order = sorted(range(len(distances)), key=lambda i: distances[i])[:k]

        return {
            "documents": [[docs[i] for i in order]],
            "metadatas": [[metas[i] for i in order]],
            "distances": [[distances[i] for i in order]],
        }

    def delete_all(self) -> None:
        conn = self._conn()
        conn.execute(f'DELETE FROM "{self._name}"')
        conn.commit()
        conn.close()


class _CollectionInfo:
    def __init__(self, name: str) -> None:
        self.name = name


class VectorStoreClient:
    def __init__(self, path: str) -> None:
        self._dir = Path(path)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db = str(self._dir / "store.sqlite3")
        conn = sqlite3.connect(self._db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _collections (name TEXT PRIMARY KEY)"
        )
        conn.commit()
        conn.close()

    def get_or_create_collection(
        self, name: str, metadata: Optional[Dict] = None
    ) -> Collection:
        conn = sqlite3.connect(self._db)
        conn.execute(
            "INSERT OR IGNORE INTO _collections (name) VALUES (?)", (name,)
        )
        conn.commit()
        conn.close()
        return Collection(self._db, name)

    def get_collection(self, name: str) -> Collection:
        conn = sqlite3.connect(self._db)
        cur = conn.execute(
            "SELECT name FROM _collections WHERE name=?", (name,)
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            raise ValueError(f"Collection {name!r} not found")
        return Collection(self._db, name)

    def list_collections(self) -> List[_CollectionInfo]:
        conn = sqlite3.connect(self._db)
        cur = conn.execute("SELECT name FROM _collections")
        names = [row[0] for row in cur.fetchall()]
        conn.close()
        return [_CollectionInfo(n) for n in names]

    def delete_collection(self, name: str) -> None:
        col = Collection(self._db, name)
        col.delete_all()
        conn = sqlite3.connect(self._db)
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
        conn.execute("DELETE FROM _collections WHERE name=?", (name,))
        conn.commit()
        conn.close()
