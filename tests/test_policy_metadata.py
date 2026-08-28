import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.dependencies import override_container
from api.main import app
from application.chat_service import ChatService
from application.mindgraph_index_service import MindGraphIndexService
from application.vault_sync_service import VaultSyncService
from infrastructure.database import ProductDatabase
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


def test_initialize_migrates_existing_notes_without_losing_rows(tmp_path: Path) -> None:
    """Catches a migration that adds governance columns by recreating or dropping notes."""
    database_path = tmp_path / "product.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            INSERT INTO schema_meta(version) VALUES (3);
            CREATE TABLE notes (
                note_id TEXT PRIMARY KEY,
                vault_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                frontmatter_json TEXT NOT NULL DEFAULT '{}',
                ai_access_level TEXT NOT NULL DEFAULT 'local_only',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                index_status TEXT NOT NULL DEFAULT 'pending',
                index_version TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_indexed_at TEXT
            );
            INSERT INTO notes (
                note_id, vault_path, title, content_hash, created_at, updated_at
            ) VALUES ('note-1', 'policy.md', '旧制度', 'hash', '2026-01-01', '2026-01-01');
            """
        )

    database = ProductDatabase(database_path)
    database.initialize()

    row = database.fetch_one(
        """SELECT note_id, policy_key, owner, document_version, effective_from, effective_to,
                  policy_status, metadata_issues_json
           FROM notes WHERE note_id = 'note-1'"""
    )
    assert row == {
        "note_id": "note-1",
        "policy_key": None,
        "owner": None,
        "document_version": None,
        "effective_from": None,
        "effective_to": None,
        "policy_status": "unspecified",
        "metadata_issues_json": "[]",
    }
    assert database.fetch_one("SELECT version FROM schema_meta") == {"version": 9}
    with database.connect() as connection:
        indexes = {item[1] for item in connection.execute("PRAGMA index_list(notes)")}
    assert "idx_notes_policy_lifecycle" in indexes

    graph_store = SimpleNamespace(
        related_note_ids=lambda *_args, **_kwargs: [],
        note_titles=lambda _ids: {},
    )
    override_container(SimpleNamespace(database=database, mindgraph_graph_store=graph_store))
    client = TestClient(app, raise_server_exceptions=False)
    try:
        governance = client.get("/api/v1/mindgraph/notes/note-1").json()["governance"]
        assert governance["metadata_complete"] is False
        assert governance["issues"] == [
            "missing_owner",
            "missing_policy_key",
            "missing_version",
            "missing_effective_from",
            "missing_policy_status",
        ]
    finally:
        client.close()
        override_container(None)


def test_vault_sync_normalizes_policy_metadata_and_records_quality_issues(tmp_path: Path) -> None:
    """Catches dates left non-serializable or incomplete policy records marked complete."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "complete.md").write_text(
        """---
mindgraph_id: complete-note
owner: 财务运营部
policy_key: expense.general
version: "2.0"
status: active
effective_from: 2026-07-01
effective_to: 2027-06-30
---
# 费用制度 V2
正文。
""",
        encoding="utf-8",
    )
    (vault / "incomplete.md").write_text(
        """---
mindgraph_id: incomplete-note
status: unknown-state
effective_from: 2027-12-31
effective_to: 2027-01-01
---
# 未治理制度
正文。
""",
        encoding="utf-8",
    )
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()

    result = VaultSyncService(database, vault, write_ids=False).scan_vault()

    assert result.errors == []
    complete = database.fetch_one("SELECT * FROM notes WHERE note_id='complete-note'")
    assert complete is not None
    assert complete["owner"] == "财务运营部"
    assert complete["policy_key"] == "expense.general"
    assert complete["document_version"] == "2.0"
    assert complete["effective_from"] == "2026-07-01"
    assert complete["effective_to"] == "2027-06-30"
    assert complete["policy_status"] == "active"
    assert complete["metadata_issues_json"] == "[]"

    index_service = MindGraphIndexService(
        database,
        vault,
        tmp_path / "indexes",
        provider=SimpleNamespace(model_name="fake", model_revision="test", dimension=2),
    )
    complete_chunk = next(
        chunk for chunk in index_service._all_chunks() if chunk.document_id == "complete-note"
    )
    assert {
        key: complete_chunk.metadata[key]
        for key in (
            "owner",
            "policy_key",
            "document_version",
            "effective_from",
            "effective_to",
            "policy_status",
            "effective_date",
            "expiration_date",
            "document_status",
            "knowledge_category",
        )
    } == {
        "owner": "财务运营部",
        "policy_key": "expense.general",
        "document_version": "2.0",
        "effective_from": "2026-07-01",
        "effective_to": "2027-06-30",
        "policy_status": "active",
        "effective_date": "2026-07-01",
        "expiration_date": "2027-06-30",
        "document_status": "active",
        "knowledge_category": "根目录",
    }

    database.execute("UPDATE notes SET index_status='indexed' WHERE note_id='complete-note'")
    complete_path = vault / "complete.md"
    complete_path.write_text(
        complete_path.read_text(encoding="utf-8").replace('version: "2.0"', 'version: "2.1"'),
        encoding="utf-8",
    )
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    changed = database.fetch_one("SELECT index_status FROM notes WHERE note_id='complete-note'")
    assert changed == {"index_status": "pending"}

    incomplete = database.fetch_one("SELECT * FROM notes WHERE note_id='incomplete-note'")
    assert incomplete is not None
    assert incomplete["policy_status"] == "unspecified"
    assert set(json.loads(incomplete["metadata_issues_json"])) == {
        "missing_owner",
        "missing_policy_key",
        "missing_version",
        "invalid_policy_status",
        "invalid_effective_range",
    }


def test_notes_api_exposes_governance_metadata_and_filters_policy_status(tmp_path: Path) -> None:
    """Catches the ledger hiding lifecycle metadata or returning archived rows as active."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "active.md").write_text(
        """---
mindgraph_id: active-note
owner: 财务部
policy_key: expense.general
version: "3.0"
status: active
effective_from: 2026-08-01
---
# 在行制度
""",
        encoding="utf-8",
    )
    (vault / "archived.md").write_text(
        """---
mindgraph_id: archived-note
owner: 财务部
policy_key: expense.general
version: "2.0"
status: archived
effective_from: 2025-01-01
effective_to: 2026-07-31
---
# 历史制度
""",
        encoding="utf-8",
    )
    (vault / "incomplete.md").write_text(
        """---
mindgraph_id: incomplete-note
status: draft
effective_from: 2026-09-01
---
# 待治理制度
""",
        encoding="utf-8",
    )
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    graph_store = SimpleNamespace(
        related_note_ids=lambda *_args, **_kwargs: [],
        note_titles=lambda _ids: {},
    )
    override_container(SimpleNamespace(database=database, mindgraph_graph_store=graph_store))
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/api/v1/mindgraph/notes", params={"policy_status": "active"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["id"] == "active-note"
        assert payload["items"][0]["governance"] == {
            "owner": "财务部",
            "policy_key": "expense.general",
            "version": "3.0",
            "effective_from": "2026-08-01",
            "effective_to": None,
            "policy_status": "active",
            "metadata_complete": True,
            "issues": [],
        }
        detail = client.get("/api/v1/mindgraph/notes/active-note")
        assert detail.status_code == 200
        assert detail.json()["governance"] == payload["items"][0]["governance"]

        incomplete = client.get("/api/v1/mindgraph/notes", params={"governance": "incomplete"})
        assert incomplete.status_code == 200
        assert [item["id"] for item in incomplete.json()["items"]] == ["incomplete-note"]
    finally:
        client.close()
        override_container(None)


def test_citation_carries_policy_lifecycle_metadata() -> None:
    """Catches evidence cards that lose the version and validity context after retrieval."""
    chunk = Chunk(
        chunk_id="policy::0",
        text="员工应在三十日内提交报销。",
        document_id="policy",
        chunk_index=0,
        section_path="报销时限",
        metadata={
            "title": "费用制度",
            "vault_path": "policies/expense-v3.md",
            "owner": "财务部",
            "document_version": "3.0",
            "effective_from": "2026-08-01",
            "effective_to": None,
            "policy_status": "active",
        },
    )
    trace = RetrievalTrace(
        query="多久提交？",
        requested_strategy="hybrid",
        actual_strategy="hybrid",
        final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1)],
    )

    citation = ChatService._citations(trace)[0]

    assert citation.model_dump()["vault_path"] == "policies/expense-v3.md"
    assert citation.model_dump()["owner"] == "财务部"
    assert citation.model_dump()["effective_from"] == "2026-08-01"
    assert citation.model_dump()["effective_to"] is None
    assert citation.model_dump()["policy_status"] == "active"


def test_graph_expansion_does_not_reintroduce_archived_policy() -> None:
    """Catches graph traversal bypassing the lifecycle filter applied to hybrid results."""
    active = Chunk(
        "active::0",
        "当前制度",
        "active",
        0,
        None,
        {"mindgraph_id": "active", "document_status": "active"},
    )
    archived = Chunk(
        "archived::0",
        "历史制度",
        "archived",
        0,
        None,
        {
            "mindgraph_id": "archived",
            "document_status": "archived",
            "effective_date": "2025-01-01",
            "expiration_date": "2026-07-31",
        },
    )

    class BasePipeline:
        dense = SimpleNamespace(chunks=[active, archived])

        def retrieve(self, _query, strategy, query_date, categories, include_historical, access_scope=None):
            return RetrievalTrace(
                query="当前规则",
                requested_strategy=strategy,
                actual_strategy=strategy,
                final_selected_chunks=[RetrievalCandidate(active, final_rank=1)],
                applied_filters={
                    "query_date": query_date,
                    "knowledge_categories": categories,
                    "include_historical": include_historical,
                    "access_scope": access_scope,
                },
            )

    graph_store = SimpleNamespace(
        related_note_ids=lambda note_ids, status="confirmed", *, hops=1, access_scope=None: [
            {
                "source_note_id": "active",
                "target_note_id": "archived",
                "relation_type": "supersedes",
                "confidence": 0.9,
            }
        ],
        note_titles=lambda note_ids: {"active": "当前制度", "archived": "历史制度"},
    )
    pipeline = MindGraphRetrievalPipeline(BasePipeline(), graph_store)

    current = pipeline.retrieve(
        "当前规则", "hybrid", query_date="2026-08-17", categories=[], include_historical=False, graph_enabled=True
    )
    assert [item.chunk.document_id for item in current.final_selected_chunks] == ["active"]
    assert current.candidate_counts["graph_expanded"] == 0
    assert current.graph_links == []

    historical = pipeline.retrieve(
        "历史规则", "hybrid", query_date="2026-08-17", categories=[], include_historical=True, graph_enabled=True
    )
    assert [item.chunk.document_id for item in historical.final_selected_chunks] == [
        "active",
        "archived",
    ]
    assert [link["target_note_id"] for link in historical.graph_links] == ["archived"]
