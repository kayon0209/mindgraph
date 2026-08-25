"""MindGraph 索引构建服务（M1-D3 增量同步核心）。

策略（对齐《最终方案 v1.0》"嵌入复用 + 增量重建 + 原子切换"）：

- 数据源：``notes`` 表（MindGraph 笔记），不再依赖原 RAG 文档目录；
- 增量优化：复用 ``embedding_cache`` 表（按 chunk 正文 checksum 缓存向量），
  仅变更 chunk 重新计算 embedding，未变更 chunk 直接复用；
- 原子切换：新索引版本构建成功后，才通过 ``CURRENT.tmp`` + ``replace`` 激活，
  失败时恢复笔记原索引状态，不影响线上可用索引
  （符合索引状态机 ``pending → processing → ready/failed``）。
- 删除的笔记在扫描阶段已物理剪枝，构建时自然排除，无需单独的「索引移除」逻辑。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
import uuid

import faiss
import numpy as np

from application.governance_reconciliation_service import GovernanceReconciliationService
from document_loader import _chunk_text, _split_by_markdown_headers
from domain.errors import GovernanceUnavailableError
from domain.governance import GovernanceDisposition, GovernanceEvaluation, GovernanceMode
from infrastructure.database import ProductDatabase, dumps, loads
from infrastructure.markdown_frontmatter import parse_frontmatter
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.types import Chunk

logger = logging.getLogger("mindgraph.index")


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_loads(value: str | None, default: Any) -> Any:
    try:
        return loads(value, default)
    except Exception:
        return default


class MindGraphIndexService:
    def __init__(
        self,
        db: ProductDatabase,
        vault_root: Path,
        index_root: Path,
        provider: Any | None = None,
        on_activated: Callable[[], None] | None = None,
        governance_reconciler: GovernanceReconciliationService | None = None,
    ) -> None:
        self.db = db
        self.vault_root = Path(vault_root)
        self.index_root = Path(index_root)
        self.index_root.mkdir(parents=True, exist_ok=True)
        self.provider = provider or BGEEmbeddingProvider()  # 尊重 BGE_LOCAL_FILES_ONLY 环境变量（默认 true=离线安全；设 false 即首次自动下载）
        self.on_activated = on_activated
        # ``None`` remains only for isolated legacy callers. Production composition
        # always supplies the governance authority.
        self.governance_reconciler = governance_reconciler

    # ------------------------------------------------------------------ #
    # 查询待索引笔记
    # ------------------------------------------------------------------ #
    def pending_notes(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM notes "
            "WHERE index_status IN ('pending','failed') AND ai_access_level <> 'excluded'"
        )

    def has_pending(self) -> bool:
        row = self.db.fetch_one(
            "SELECT 1 FROM notes "
            "WHERE index_status IN ('pending','failed') AND ai_access_level <> 'excluded' LIMIT 1"
        )
        return row is not None

    # ------------------------------------------------------------------ #
    # 分块（带 mindgraph_id，正文剥离 Frontmatter）
    # ------------------------------------------------------------------ #
    def _resolve_note_path(self, note: dict[str, Any]) -> Path | None:
        """Resolve built-in and connector-backed note paths safely.

        Connector notes use ``connector_id/relative/path.md`` as their stable,
        globally unique database path. The note's source ownership selects the
        root; the path prefix must agree with that owner.
        """
        stored_path = Path(note["vault_path"])
        source_id = note.get("source_id") or "builtin"
        if source_id == "builtin":
            source_root = self.vault_root
            relative_path = stored_path
        else:
            parts = stored_path.parts
            if len(parts) < 2 or parts[0] != source_id:
                return None
            connector = self.db.fetch_one(
                "SELECT source_path FROM connector_syncs "
                "WHERE connector_id=? AND status='completed' "
                "ORDER BY finished_at DESC LIMIT 1",
                (source_id,),
            )
            if not connector:
                return None
            source_root = Path(connector["source_path"])
            relative_path = Path(*parts[1:])
        try:
            canonical_root = source_root.resolve(strict=True)
            candidate = (canonical_root / relative_path).resolve(strict=True)
        except OSError:
            return None
        if canonical_root in candidate.parents and candidate.is_file():
            return candidate
        return None

    def _load_note_chunks(
        self,
        note: dict[str, Any],
        evaluation: GovernanceEvaluation | None = None,
        equivalent_note_ids: list[str] | None = None,
        governance_as_of: date | None = None,
    ) -> list[Chunk]:
        path = self._resolve_note_path(note)
        category = Path(note["vault_path"]).parent.name or "根目录"
        if path is None:
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []
        _, body, _ = parse_frontmatter(raw)
        if not body.strip():
            return []
        chunks: list[Chunk] = []
        idx = 0
        for section_path, sec_body in _split_by_markdown_headers(body):
            for sub in _chunk_text(sec_body, 500, 50):
                chunks.append(Chunk(
                    chunk_id=f"{note['note_id']}::{idx}",
                    text=sub,
                    document_id=note["note_id"],
                    chunk_index=idx,
                    section_path=section_path,
                    metadata={
                        "mindgraph_id": note["note_id"],
                        "source_id": note.get("source_id", "builtin"),
                        "vault_path": note["vault_path"],
                        "title": note["title"],
                        "doc_name": Path(note["vault_path"]).name,
                        "section_path": section_path,
                        "chunk_index": idx,
                        "ai_access_level": note.get("ai_access_level", "local_only"),
                        "workspace": note.get("workspace"),
                        "department": note.get("department"),
                        "acl_json": note.get("acl_json") or "{}",
                        "acl_public": bool(note.get("acl_public")),
                        "owner": note.get("owner"),
                        "policy_key": note.get("policy_key"),
                        "document_version": note.get("document_version"),
                        "effective_from": note.get("effective_from"),
                        "effective_to": note.get("effective_to"),
                        "policy_status": note.get("policy_status", "unspecified"),
                        "effective_date": note.get("effective_from"),
                        "expiration_date": note.get("effective_to"),
                        "document_status": note.get("policy_status", "unspecified"),
                        "knowledge_category": category,
                        "origin": "mindgraph",
                        # Defensive metadata for artifact inspection. Retrieval
                        # eligibility is decided from the fresh database evaluation
                        # immediately before index construction, never from this copy.
                        "governance_disposition": (
                            evaluation.disposition.value if evaluation is not None else None
                        ),
                        "governance_lifecycle_state": (
                            evaluation.lifecycle_state.value if evaluation is not None else None
                        ),
                        "governance_reason_codes": (
                            list(evaluation.reason_codes) if evaluation is not None else []
                        ),
                        "governance_as_of": (
                            governance_as_of.isoformat() if governance_as_of is not None else None
                        ),
                        "equivalent_note_ids": equivalent_note_ids or [note["note_id"]],
                    },
                ))
                idx += 1
        return chunks

    def _all_chunks(
        self,
        notes: list[dict[str, Any]] | None = None,
        evaluations: dict[str, GovernanceEvaluation] | None = None,
        equivalent_ids: dict[str, list[str]] | None = None,
        governance_as_of: date | None = None,
    ) -> list[Chunk]:
        notes = notes if notes is not None else self.db.fetch_all(
            "SELECT * FROM notes WHERE ai_access_level <> 'excluded'"
        )
        chunks: list[Chunk] = []
        for note in notes:
            chunks.extend(
                self._load_note_chunks(
                    note,
                    evaluations.get(note["note_id"]) if evaluations is not None else None,
                    equivalent_ids.get(note["note_id"]) if equivalent_ids is not None else None,
                    governance_as_of,
                )
            )
        return chunks

    def _governed_notes(
        self, build_date: date
    ) -> tuple[list[dict[str, Any]], dict[str, GovernanceEvaluation], dict[str, list[str]], dict[str, int]]:
        notes = self.db.fetch_all("SELECT * FROM notes WHERE ai_access_level <> 'excluded'")
        if self.governance_reconciler is None:
            return notes, {}, {}, {}
        try:
            evaluations = self.governance_reconciler.evaluate_notes(
                notes, as_of=build_date, mode=GovernanceMode.CURRENT
            )
        except Exception as exc:
            raise GovernanceUnavailableError("knowledge governance evaluation is unavailable") from exc

        equivalent_ids: dict[str, list[str]] = {}
        excluded_counts: dict[str, int] = {}
        eligible: list[dict[str, Any]] = []
        for note in notes:
            evaluation = evaluations[note["note_id"]]
            if evaluation.disposition is GovernanceDisposition.ELIGIBLE:
                eligible.append(note)
                equivalent_ids.setdefault(note["note_id"], [note["note_id"]])
                continue
            if (
                evaluation.disposition is GovernanceDisposition.DUPLICATE_ALIAS
                and evaluation.canonical_note_id is not None
            ):
                equivalent_ids.setdefault(evaluation.canonical_note_id, [evaluation.canonical_note_id]).append(
                    note["note_id"]
                )
            for reason in evaluation.reason_codes:
                excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
        for note_id, note_ids in equivalent_ids.items():
            equivalent_ids[note_id] = sorted(set(note_ids))
        return eligible, evaluations, equivalent_ids, dict(sorted(excluded_counts.items()))

    # ------------------------------------------------------------------ #
    # embedding 缓存（按 chunk 正文 checksum）
    # ------------------------------------------------------------------ #
    def _cached_embedding(self, checksum: str) -> list[float] | None:
        row = self.db.fetch_one(
            "SELECT embedding_json,dimension FROM embedding_cache "
            "WHERE model_name=? AND model_revision=? AND chunk_checksum=?",
            (self.provider.model_name, self.provider.model_revision, checksum),
        )
        if row and row["dimension"] == self.provider.dimension:
            return json.loads(row["embedding_json"])
        return None

    def _cache_embedding(self, checksum: str, vector: list[float]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?,?,?)",
            (self.provider.model_name, self.provider.model_revision, checksum,
             self.provider.dimension, dumps(vector), _utc_iso()),
        )

    # ------------------------------------------------------------------ #
    # 构建（增量 + 原子切换）
    # ------------------------------------------------------------------ #
    def build(
        self,
        operator: str = "local",
        force: bool = False,
        governance_reconciled: bool = False,
    ) -> dict[str, Any]:
        """构建索引。

        - 默认仅在有待索引笔记（pending/failed）时构建；
        - ``force=True`` 用于删除场景：笔记已从 ``notes`` 表剪枝，无 pending
          可触发，但旧 FAISS 索引仍含其 chunk，需强制全量重建以排除。
          embedding 仍按 checksum 命中缓存，重建成本仅为 FAISS.add（毫秒级）。
        """
        build_date = date.today()
        if self.governance_reconciler is not None and not governance_reconciled:
            try:
                self.governance_reconciler.reconcile(as_of=build_date)
            except Exception as exc:
                raise GovernanceUnavailableError("knowledge governance reconciliation is unavailable") from exc

        pending = self.pending_notes()
        if not pending and not force:
            return {"status": "noop", "reason": "no pending notes"}
        pending_ids = [n["note_id"] for n in pending]
        note_states = self.db.fetch_all(
            "SELECT note_id,index_status,index_version,last_indexed_at FROM notes"
        )

        self.db.execute_many(
            "UPDATE notes SET index_status='processing' WHERE note_id=?",
            [(i,) for i in pending_ids],
        )

        version = datetime.now(UTC).strftime("mg-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        directory = self.index_root / version
        directory.mkdir()
        previous = self._current()
        reused = 0
        new_count = 0
        try:
            eligible_notes, evaluations, equivalent_ids, excluded_reason_counts = self._governed_notes(
                build_date
            )
            chunks = self._all_chunks(
                eligible_notes,
                evaluations,
                equivalent_ids,
                build_date,
            )

            vectors: list[list[float] | None] = [None] * len(chunks)
            to_embed: list[tuple[int, str]] = []
            for i, chunk in enumerate(chunks):
                checksum = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                cached = self._cached_embedding(checksum)
                if cached is not None:
                    vectors[i] = cached
                    reused += 1
                else:
                    to_embed.append((i, checksum))

            if to_embed:
                texts = [chunks[i].text for i, _ in to_embed]
                computed = self.provider.embed_documents(texts)
                for (i, checksum), vec in zip(to_embed, computed, strict=True):
                    vectors[i] = vec
                    self._cache_embedding(checksum, vec)
                    new_count += 1

            if vectors:
                matrix = np.asarray(vectors, dtype="float32")
                faiss.normalize_L2(matrix)
            else:
                matrix = np.empty((0, self.provider.dimension), dtype="float32")
            index = faiss.IndexFlatIP(self.provider.dimension)
            if len(matrix):
                index.add(matrix)
            faiss.write_index(index, str(directory / "dense.faiss"))
            (directory / "chunks.json").write_text(
                json.dumps([c.__dict__ for c in chunks], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            all_ids = [row["note_id"] for row in self.db.fetch_all("SELECT note_id FROM notes")]
            manifest = {
                "index_version": version,
                "embedding_model_name": self.provider.model_name,
                "embedding_model_revision": self.provider.model_revision,
                "vector_dimension": self.provider.dimension,
                "chunk_count": len(chunks),
                "note_count": len(all_ids),
                "governance_as_of": build_date.isoformat() if self.governance_reconciler else None,
                "governance_policy_version": (
                    "v1" if self.governance_reconciler is not None else None
                ),
                "eligible_note_count": len(eligible_notes),
                "excluded_reason_counts": excluded_reason_counts,
                "reused_embeddings": reused,
                "new_embeddings": new_count,
                "created_at": _utc_iso(),
                "build_status": "validated",
                "previous_index_version": previous,
                "strategy": "mindgraph_incremental",
            }
            (directory / "metadata.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (directory / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            now = _utc_iso()
            self.db.execute_many(
                "UPDATE notes SET index_status='ready', index_version=?, last_indexed_at=? WHERE note_id=?",
                [(version, now, nid) for nid in all_ids],
            )
            self.db.execute(
                "INSERT INTO index_builds VALUES (?,?,?,?,?,?,?)",
                (version, "validated", dumps(manifest), previous, manifest["created_at"], now, None),
            )
            self._activate(version)
            if self.on_activated is not None:
                try:
                    self.on_activated()
                except Exception:
                    logger.exception("mindgraph_pipeline_invalidation_failed", extra={"index_version": version})
            return manifest
        except Exception as exc:
            failure = {
                "index_version": version,
                "build_status": "failed",
                "failure_reason": f"{type(exc).__name__}: {exc}",
            }
            try:
                (directory / "manifest.json").write_text(
                    json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self.db.execute(
                    "INSERT OR REPLACE INTO index_builds VALUES (?,?,?,?,?,?,?)",
                    (version, "failed", dumps(failure), previous, _utc_iso(), None, failure["failure_reason"]),
                )
            except Exception:
                logger.exception("mindgraph_index_failure_recording_failed", extra={"index_version": version})
            try:
                self.db.execute_many(
                    "UPDATE notes SET index_status=?,index_version=?,last_indexed_at=? WHERE note_id=?",
                    [
                        (
                            row["index_status"],
                            row["index_version"],
                            row["last_indexed_at"],
                            row["note_id"],
                        )
                        for row in note_states
                    ],
                )
            except Exception:
                logger.exception("mindgraph_note_state_restore_failed", extra={"index_version": version})
            raise

    # ------------------------------------------------------------------ #
    # 当前索引版本 + 原子激活
    # ------------------------------------------------------------------ #
    def _current(self) -> str | None:
        try:
            return (self.index_root / "CURRENT").read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def _activate(self, version: str) -> None:
        temp = self.index_root / "CURRENT.tmp"
        temp.write_text(version, encoding="utf-8")
        temp.replace(self.index_root / "CURRENT")

    def current_version(self) -> str | None:
        return self._current()
