from __future__ import annotations

from datetime import date
import json
import sqlite3

import pytest

from application.chat_service import ChatService
from application.governance_policy import GovernancePolicy
from application.governance_reconciliation_service import GovernanceReconciliationService
from domain.governance import (
    ConfirmedGovernanceDecision,
    GovernanceAuthoritySnapshot,
    GovernanceDisposition,
)
from domain.models import ChatRequest
from infrastructure.database import ProductDatabase
from retrieval.fusion import ReciprocalRankFusion
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.pipeline import RetrievalPipeline
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


def _chunk(
    note_id: str,
    *,
    status: str = "active",
    effective_from: str = "2026-01-01",
    effective_to: str | None = None,
    public: bool = True,
) -> Chunk:
    return Chunk(
        chunk_id=f"{note_id}::0",
        text=f"policy text for {note_id}",
        document_id=note_id,
        chunk_index=0,
        section_path="Policy",
        metadata={
            "mindgraph_id": note_id,
            "source_id": "builtin",
            "owner": "Finance",
            "policy_key": f"policy.{note_id}",
            "document_version": "1.0",
            "effective_from": effective_from,
            "effective_to": effective_to,
            "policy_status": status,
            "document_status": status,
            "metadata_issues": [],
            "workspace": "corp",
            "department": "finance",
            "acl_json": "{}",
            "acl_public": public,
            "content_hash": f"hash-{note_id}",
        },
    )


class GuardedRetriever:
    def __init__(self, chunks: list[Chunk], forbidden: set[str], score: str) -> None:
        self.chunks = chunks
        self.forbidden = forbidden
        self.score = score

    def search(self, _query: str, top_k: int, allowed_chunk_ids=None):
        assert allowed_chunk_ids is not None
        assert not self.forbidden.intersection(allowed_chunk_ids)
        selected = [chunk for chunk in self.chunks if chunk.chunk_id in allowed_chunk_ids]
        candidates = []
        for rank, chunk in enumerate(selected[:top_k], 1):
            kwargs = {self.score: 1.0 / rank, self.score.replace("score", "rank"): rank}
            candidates.append(RetrievalCandidate(chunk=chunk, **kwargs))
        return candidates, {}


def _pipeline(
    chunks: list[Chunk],
    *,
    decisions: dict[str, tuple[ConfirmedGovernanceDecision, ...]] | None = None,
    forbidden: set[str] | None = None,
) -> RetrievalPipeline:
    blocked = forbidden or set()

    def load(note_ids, **_kwargs):
        notes = {
            note.note_id: note
            for note in (RetrievalPipeline._governance_note(chunk) for chunk in chunks)
            if note.note_id in note_ids
        }
        return GovernanceAuthoritySnapshot(notes, decisions or {}, {})

    return RetrievalPipeline(
        GuardedRetriever(chunks, blocked, "dense_score"),
        GuardedRetriever(chunks, blocked, "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
        governance_authority_loader=load,
    )


def test_governance_prefilter_excludes_ineligible_before_retrieval() -> None:
    current = _chunk("current")
    draft = _chunk("draft", status="draft")
    expired = _chunk("expired", effective_to="2026-08-24")
    conflict = _chunk("conflict")
    conflict_id = conflict.chunk_id
    decisions = {
        "conflict": (
            ConfirmedGovernanceDecision(
                "conflict",
                GovernanceDisposition.CONFLICT_BLOCKED,
                "overlapping_effective_intervals",
            ),
        )
    }
    pipeline = _pipeline(
        [current, draft, expired, conflict],
        decisions=decisions,
        forbidden={draft.chunk_id, expired.chunk_id, conflict_id},
    )

    trace = pipeline.retrieve(
        "policy",
        "hybrid",
        query_date="2026-08-25",
        access_scope={"allow": ["*"]},
    )

    assert [item.chunk.chunk_id for item in trace.final_selected_chunks] == [current.chunk_id]
    assert trace.applied_filters["governance_prefilter"] == {
        "corpus_count": 4,
        "eligible_count": 1,
        "excluded_reason_counts": {
            "declared_draft": 1,
            "effective_period_ended": 1,
            "overlapping_effective_intervals": 1,
        },
        "as_of": "2026-08-25",
        "mode": "current",
    }
    assert "conflict" not in str(trace.to_dict())


def test_historical_mode_requires_explicit_query_date_before_retrieval() -> None:
    chunk = _chunk("historical", status="archived", effective_to="2025-12-31")
    pipeline = _pipeline([chunk])

    with pytest.raises(ValueError, match="query_date"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            include_historical=True,
            access_scope={"allow": ["*"]},
        )


def test_governance_requires_complete_matching_dense_sparse_corpora() -> None:
    dense_chunk = _chunk("shared")
    sparse_chunk = _chunk("shared", status="draft")
    pipeline = RetrievalPipeline(
        GuardedRetriever([dense_chunk], set(), "dense_score"),
        GuardedRetriever([sparse_chunk], set(), "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
        governance_authority_loader=lambda _note_ids, **_kwargs: GovernanceAuthoritySnapshot(
            {"shared": RetrievalPipeline._governance_note(dense_chunk)},
            {},
            {},
        ),
    )

    with pytest.raises(ValueError, match=r"(?i)(governance|prefilter)"):
        pipeline.retrieve("policy", "hybrid", access_scope={"allow": ["*"]})


def test_missing_governance_authority_fails_closed() -> None:
    chunk = _chunk("current")
    pipeline = RetrievalPipeline(
        GuardedRetriever([chunk], set(), "dense_score"),
        GuardedRetriever([chunk], set(), "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
    )

    with pytest.raises(ValueError, match=r"(?i)governance"):
        pipeline.retrieve("policy", "hybrid", access_scope={"allow": ["*"]})


def test_governance_storage_failure_fails_closed() -> None:
    chunk = _chunk("current")

    def unavailable(_ids, **_kwargs):
        raise RuntimeError("storage unavailable")

    pipeline = RetrievalPipeline(
        GuardedRetriever([chunk], set(), "dense_score"),
        GuardedRetriever([chunk], set(), "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
        governance_authority_loader=unavailable,
    )

    with pytest.raises(ValueError, match="authority is unavailable"):
        pipeline.retrieve("policy", "hybrid", access_scope={"allow": ["*"]})


def test_confirmed_decisions_are_reloaded_for_every_retrieval() -> None:
    chunk = _chunk("current")
    decisions: dict[str, tuple[ConfirmedGovernanceDecision, ...]] = {}
    pipeline = _pipeline([chunk], decisions=decisions)

    first = pipeline.retrieve("policy", "hybrid", access_scope={"allow": ["*"]})
    decisions["current"] = (
        ConfirmedGovernanceDecision(
            "current",
            GovernanceDisposition.CONFLICT_BLOCKED,
            "overlapping_effective_intervals",
        ),
    )
    second = pipeline.retrieve("policy", "hybrid", access_scope={"allow": ["*"]})

    assert [item.chunk.chunk_id for item in first.final_selected_chunks] == [chunk.chunk_id]
    assert second.final_selected_chunks == []


def test_fusion_and_reranker_outputs_are_defensively_intersected() -> None:
    allowed = _chunk("allowed")
    excluded = _chunk("excluded", status="draft")

    class InjectingFusion:
        def fuse(self, rankings, _top_k):
            return [*rankings[0], RetrievalCandidate(excluded, rrf_score=100.0)]

    class InjectingReranker:
        model_name = "injecting"

        def rerank(self, _query, candidates, _top_k):
            return [RetrievalCandidate(excluded, reranker_score=100.0), *candidates]

    pipeline = RetrievalPipeline(
        GuardedRetriever([allowed, excluded], {excluded.chunk_id}, "dense_score"),
        GuardedRetriever([allowed, excluded], {excluded.chunk_id}, "sparse_score"),
        InjectingFusion(),
        InjectingReranker(),
        governance_policy=GovernancePolicy(),
        governance_authority_loader=lambda note_ids, **_kwargs: GovernanceAuthoritySnapshot(
            {
                note.note_id: note
                for note in (
                    RetrievalPipeline._governance_note(chunk)
                    for chunk in (allowed, excluded)
                )
                if note.note_id in note_ids
            },
            {},
            {},
        ),
    )

    trace = pipeline.retrieve(
        "policy", "hybrid_rerank", access_scope={"allow": ["*"]}
    )

    assert [item.chunk.chunk_id for item in trace.fused_results] == [allowed.chunk_id]
    assert [item.chunk.chunk_id for item in trace.reranked_results] == [allowed.chunk_id]
    assert [item.chunk.chunk_id for item in trace.final_selected_chunks] == [allowed.chunk_id]


def test_acl_denied_metadata_is_not_passed_to_governance_loader() -> None:
    visible = _chunk("visible")
    hidden = _chunk("hidden", public=False)
    loaded: list[tuple[str, ...]] = []

    def load(note_ids, **_kwargs):
        loaded.append(tuple(sorted(note_ids)))
        return GovernanceAuthoritySnapshot(
            {"visible": RetrievalPipeline._governance_note(visible)},
            {},
            {},
        )

    pipeline = RetrievalPipeline(
        GuardedRetriever([visible, hidden], {hidden.chunk_id}, "dense_score"),
        GuardedRetriever([visible, hidden], {hidden.chunk_id}, "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
        governance_authority_loader=load,
    )

    pipeline.retrieve("policy", "hybrid", access_scope={})

    assert loaded == [("visible",)]


def test_historical_selection_uses_requested_date() -> None:
    old = _chunk("old", status="archived", effective_from="2025-01-01", effective_to="2025-12-31")
    current = _chunk("current", effective_from="2026-01-01")
    pipeline = _pipeline([old, current], forbidden={current.chunk_id})

    trace = pipeline.retrieve(
        "policy",
        "hybrid",
        query_date="2025-06-01",
        include_historical=True,
        access_scope={"allow": ["*"]},
    )

    assert [item.chunk.chunk_id for item in trace.final_selected_chunks] == [old.chunk_id]
    assert trace.applied_filters["governance_prefilter"]["mode"] == "historical"


def test_graph_cannot_reintroduce_governance_excluded_target() -> None:
    source = _chunk("source")
    target = _chunk("target", status="draft")
    base = _pipeline([source, target], forbidden={target.chunk_id})
    graph_store = type(
        "GraphStore",
        (),
        {
            "related_note_ids": staticmethod(
                lambda _ids: [
                    {
                        "source_note_id": "source",
                        "target_note_id": "target",
                        "relation_type": "references",
                        "confidence": 1.0,
                        "evidence_chunk_id": "hidden-evidence",
                    }
                ]
            ),
            "note_titles": staticmethod(
                lambda _ids: {"source": "Source", "target": "Hidden target"}
            ),
        },
    )()

    trace = MindGraphRetrievalPipeline(base, graph_store).retrieve(
        "policy",
        "hybrid",
        query_date="2026-08-25",
        access_scope={"allow": ["*"]},
    )

    assert [item.chunk.chunk_id for item in trace.final_selected_chunks] == [source.chunk_id]
    assert trace.graph_links == []


class SentinelProvider:
    available = True
    model_name = "sentinel"
    provider_name = "sentinel"

    def complete(self, _messages):
        return "PROVIDER_WAS_CALLED", {}

    def stream(self, _messages):
        yield {"delta": "PROVIDER_WAS_CALLED"}


class FixedPipeline:
    def __init__(self, trace: RetrievalTrace) -> None:
        self.trace = trace

    def retrieve(self, *_args, **_kwargs):
        return self.trace


@pytest.mark.parametrize(
    ("reasons", "eligible_count", "expected_state"),
    [
        ({"overlapping_effective_intervals": 2}, 0, "conflicting_evidence"),
        ({"declared_draft": 1}, 0, "insufficient_evidence"),
    ],
)
def test_answer_and_sse_share_pre_provider_governance_refusal(
    tmp_path,
    reasons: dict[str, int],
    eligible_count: int,
    expected_state: str,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    trace = RetrievalTrace(
        query="policy",
        requested_strategy="hybrid",
        actual_strategy="hybrid",
        applied_filters={
            "governance_prefilter": {
                "corpus_count": sum(reasons.values()),
                "eligible_count": eligible_count,
                "excluded_reason_counts": reasons,
                "as_of": date(2026, 8, 25).isoformat(),
                "mode": "current",
            }
        },
    )
    service = ChatService(database, lambda _top_k: FixedPipeline(trace), SentinelProvider())
    request = ChatRequest(question="报销政策是什么？", graph_enabled=False)

    answer = service.answer(request)
    events = list(service.stream(request))
    completed = events[-1]["data"]

    assert answer.result_state.value == expected_state
    assert completed["result_state"] == expected_state
    assert "PROVIDER_WAS_CALLED" not in answer.answer
    assert all("PROVIDER_WAS_CALLED" not in str(event) for event in events)


def test_citation_exposes_only_canonical_safe_equivalent_id() -> None:
    canonical = _chunk("canonical")
    canonical.metadata["equivalent_note_ids"] = [
        "canonical",
        "hidden-alias",
    ]
    trace = RetrievalTrace(
        query="policy",
        requested_strategy="hybrid",
        actual_strategy="hybrid",
        final_selected_chunks=[RetrievalCandidate(chunk=canonical, final_rank=1)],
    )

    citation = ChatService._citations(trace)[0]

    assert citation.document_id == "canonical"
    assert citation.equivalent_document_ids == ["canonical"]
    assert "hidden-alias" not in citation.model_dump_json()


def _insert_authoritative_note(
    database: ProductDatabase,
    note_id: str,
    *,
    policy_key: str | None = None,
    version: str = "1.0",
    status: str = "active",
    effective_from: str = "2026-01-01",
    effective_to: str | None = None,
    acl_json: str = "{}",
    acl_public: bool = True,
    content_hash: str | None = None,
    source_id: str = "builtin",
) -> None:
    database.execute(
        """
        INSERT INTO notes (
            note_id, vault_path, source_id, title, content_hash, frontmatter_json,
            ai_access_level, index_status, workspace, department, acl_json, acl_public,
            policy_key, owner, document_version, effective_from, effective_to,
            policy_status, metadata_issues_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            f"policies/{note_id}.md",
            source_id,
            f"Policy {note_id}",
            content_hash or f"hash-{note_id}",
            "{}",
            "local_only",
            "indexed",
            "corp",
            "finance",
            acl_json,
            int(acl_public),
            policy_key or f"policy.{note_id}",
            "Finance",
            version,
            effective_from,
            effective_to,
            status,
            "[]",
            "2026-08-25T00:00:00Z",
            "2026-08-25T00:00:00Z",
        ),
    )


def _authoritative_pipeline(
    database: ProductDatabase,
    chunks: list[Chunk],
    *,
    forbidden: set[str] | None = None,
    final_top_k: int = 5,
) -> RetrievalPipeline:
    from application.governance_retrieval_authority import GovernanceRetrievalAuthority

    blocked = forbidden or set()
    return RetrievalPipeline(
        GuardedRetriever(chunks, blocked, "dense_score"),
        GuardedRetriever(chunks, blocked, "sparse_score"),
        ReciprocalRankFusion(),
        final_top_k=final_top_k,
        governance_policy=GovernancePolicy(),
        governance_authority_loader=GovernanceRetrievalAuthority(database).load,
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("policy_status", "draft"),
        ("effective_to", "2026-08-24"),
        ("acl_json", json.dumps({"allow": ["department:legal"]})),
    ],
)
def test_authoritative_database_drift_fails_before_retrieval(
    tmp_path,
    column: str,
    value: str,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    _insert_authoritative_note(database, "policy")
    GovernanceReconciliationService(database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )
    chunk = _chunk("policy")
    database.execute(f"UPDATE notes SET {column} = ? WHERE note_id = ?", (value, "policy"))
    pipeline = _authoritative_pipeline(database, [chunk])

    with pytest.raises(ValueError, match=r"(?i)(authority|governance|metadata)"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            query_date="2026-08-25",
            access_scope={"allow": ["*"]},
        )


def test_index_note_missing_from_database_fails_before_retrieval(tmp_path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    chunk = _chunk("orphan")
    pipeline = _authoritative_pipeline(database, [chunk])

    with pytest.raises(ValueError, match=r"(?i)(authority|governance|missing)"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            query_date="2026-08-25",
            access_scope={"allow": ["*"]},
        )


def test_proposed_overlap_blocks_top_one_retrieval_and_both_chat_paths(tmp_path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    _insert_authoritative_note(
        database,
        "old",
        policy_key="expense.shared",
        version="1.0",
        effective_to="2026-12-31",
    )
    _insert_authoritative_note(
        database,
        "new",
        policy_key="expense.shared",
        version="2.0",
        effective_from="2026-08-01",
    )
    GovernanceReconciliationService(database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )
    old = _chunk("old", effective_to="2026-12-31")
    new = _chunk("new", effective_from="2026-08-01")
    old.metadata.update({"policy_key": "expense.shared", "document_version": "1.0"})
    new.metadata.update({"policy_key": "expense.shared", "document_version": "2.0"})
    pipeline = _authoritative_pipeline(
        database,
        [old, new],
        forbidden={old.chunk_id, new.chunk_id},
        final_top_k=1,
    )

    trace = pipeline.retrieve(
        "policy",
        "hybrid",
        query_date="2026-08-25",
        access_scope={"allow": ["*"]},
    )
    service = ChatService(database, lambda _top_k: FixedPipeline(trace), SentinelProvider())
    request = ChatRequest(question="报销规则？", final_top_k=1, graph_enabled=False)
    answer = service.answer(request)
    events = list(service.stream(request))

    assert trace.final_selected_chunks == []
    assert trace.applied_filters["governance_prefilter"]["excluded_reason_counts"] == {
        "overlapping_effective_intervals": 2
    }
    assert "old" not in str(trace.to_dict())
    assert "new" not in str(trace.to_dict())
    assert answer.result_state.value == "conflicting_evidence"
    assert events[-1]["data"]["result_state"] == "conflicting_evidence"
    assert "PROVIDER_WAS_CALLED" not in answer.answer
    assert all("PROVIDER_WAS_CALLED" not in str(event) for event in events)


def test_proposed_exact_duplicate_blocks_retrieval_and_provider(tmp_path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    _insert_authoritative_note(
        database,
        "old-copy",
        policy_key="expense.copy",
        version="1.0",
        effective_from="2025-01-01",
        effective_to="2025-12-31",
        content_hash="same-checksum",
    )
    _insert_authoritative_note(
        database,
        "new-copy",
        policy_key="expense.copy",
        version="2.0",
        effective_from="2026-01-01",
        content_hash="same-checksum",
    )
    GovernanceReconciliationService(database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )
    old = _chunk(
        "old-copy",
        status="active",
        effective_from="2025-01-01",
        effective_to="2025-12-31",
    )
    new = _chunk("new-copy", effective_from="2026-01-01")
    for chunk, version in ((old, "1.0"), (new, "2.0")):
        chunk.metadata.update(
            {
                "policy_key": "expense.copy",
                "document_version": version,
                "content_hash": "same-checksum",
            }
        )
    pipeline = _authoritative_pipeline(
        database,
        [old, new],
        forbidden={old.chunk_id, new.chunk_id},
        final_top_k=1,
    )

    trace = pipeline.retrieve(
        "copy",
        "hybrid",
        query_date="2026-08-25",
        access_scope={"allow": ["*"]},
    )
    service = ChatService(database, lambda _top_k: FixedPipeline(trace), SentinelProvider())
    request = ChatRequest(question="哪份制度有效？", final_top_k=1, graph_enabled=False)
    answer = service.answer(request)
    events = list(service.stream(request))

    assert trace.final_selected_chunks == []
    assert trace.applied_filters["governance_prefilter"]["excluded_reason_counts"] == {
        "checksum_match_requires_review": 2
    }
    assert "old-copy" not in str(trace.to_dict())
    assert "new-copy" not in str(trace.to_dict())
    assert answer.result_state.value == "insufficient_evidence"
    assert events[-1]["data"]["result_state"] == "insufficient_evidence"
    assert "PROVIDER_WAS_CALLED" not in answer.answer
    assert all("PROVIDER_WAS_CALLED" not in str(event) for event in events)


def _confirmed_duplicate_database(tmp_path) -> tuple[ProductDatabase, Chunk]:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    for note_id in ("canonical", "z-alias"):
        _insert_authoritative_note(
            database,
            note_id,
            policy_key="expense.duplicate",
            version="1.0",
            content_hash="same-checksum",
            source_id="connector-main",
        )
    GovernanceReconciliationService(database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )
    canonical = _chunk("canonical")
    canonical.metadata.update(
        {
            "policy_key": "expense.duplicate",
            "content_hash": "same-checksum",
            "source_id": "connector-main",
        }
    )
    return database, canonical


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("content_hash", "changed-checksum"),
        ("acl_json", json.dumps({"allow": ["department:legal"]})),
        ("source_id", "connector-other"),
        ("document_version", "2.0"),
    ],
)
def test_confirmed_alias_case_fails_closed_after_linked_participant_drift(
    tmp_path,
    column: str,
    value: str,
) -> None:
    database, canonical = _confirmed_duplicate_database(tmp_path)
    database.execute(f"UPDATE notes SET {column} = ? WHERE note_id = ?", (value, "z-alias"))
    pipeline = _authoritative_pipeline(database, [canonical])

    with pytest.raises(ValueError, match="authority is unavailable"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            query_date="2026-08-25",
            access_scope={"allow": ["*"]},
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [("effective_from", "2027-01-01"), ("document_version", "3.0")],
)
def test_confirmed_conflict_fails_closed_after_semantic_drift(
    tmp_path,
    column: str,
    value: str,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    _insert_authoritative_note(
        database,
        "left",
        policy_key="expense.conflict",
        version="1.0",
        effective_to="2026-12-31",
    )
    _insert_authoritative_note(
        database,
        "right",
        policy_key="expense.conflict",
        version="2.0",
        effective_from="2026-08-01",
    )
    GovernanceReconciliationService(database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )
    database.execute(
        "UPDATE governance_cases SET status = 'confirmed' WHERE case_type = 'version_conflict'"
    )
    database.execute(f"UPDATE notes SET {column} = ? WHERE note_id = ?", (value, "right"))
    left = _chunk("left", effective_to="2026-12-31")
    left.metadata.update({"policy_key": "expense.conflict", "document_version": "1.0"})
    pipeline = _authoritative_pipeline(database, [left])

    with pytest.raises(ValueError, match="authority is unavailable"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            query_date="2026-08-25",
            access_scope={"allow": ["*"]},
        )


def test_confirmed_conflict_fails_closed_after_participant_policy_key_drift(
    tmp_path,
    monkeypatch,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    _insert_authoritative_note(
        database,
        "left",
        policy_key="expense.conflict",
        version="1.0",
        effective_to="2026-12-31",
    )
    _insert_authoritative_note(
        database,
        "right",
        policy_key="expense.conflict",
        version="2.0",
        effective_from="2026-08-01",
    )
    GovernanceReconciliationService(database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )
    database.execute(
        "UPDATE governance_cases SET status = 'confirmed' WHERE case_type = 'version_conflict'"
    )
    database.execute(
        "UPDATE notes SET policy_key = ? WHERE note_id = ?",
        ("expense.other", "right"),
    )
    left = _chunk("left", effective_to="2026-12-31")
    left.metadata.update({"policy_key": "expense.conflict", "document_version": "1.0"})
    connections: list[sqlite3.Connection] = []
    original_connect = database.connect

    def tracked_connect() -> sqlite3.Connection:
        connection = original_connect()
        connections.append(connection)
        return connection

    monkeypatch.setattr(database, "connect", tracked_connect)
    pipeline = _authoritative_pipeline(database, [left])

    with pytest.raises(ValueError, match="authority is unavailable"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            query_date="2026-08-25",
            access_scope={"allow": ["*"]},
        )
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_confirmed_conflict_fails_closed_when_participants_become_exact_duplicates(
    tmp_path,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    for note_id, acl_json in (
        ("left", "{}"),
        ("right", json.dumps({"allow": ["department:legal"]})),
    ):
        _insert_authoritative_note(
            database,
            note_id,
            policy_key="expense.conflict",
            version="1.0",
            effective_from="2026-01-01",
            content_hash="same-checksum",
            acl_json=acl_json,
        )
    GovernanceReconciliationService(database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )
    database.execute(
        "UPDATE governance_cases SET status = 'confirmed' WHERE case_type = 'version_conflict'"
    )
    database.execute(
        "UPDATE governance_cases SET status = 'rejected' WHERE case_type = 'exact_duplicate'"
    )
    database.execute("UPDATE notes SET acl_json = '{}' WHERE note_id = 'right'")
    left = _chunk("left")
    left.metadata.update(
        {
            "policy_key": "expense.conflict",
            "document_version": "1.0",
            "content_hash": "same-checksum",
        }
    )
    pipeline = _authoritative_pipeline(database, [left])

    with pytest.raises(ValueError, match="authority is unavailable"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            query_date="2026-08-25",
            access_scope={"allow": ["*"]},
        )


@pytest.mark.parametrize("corruption", ["rule_key", "metadata_hash", "missing_linked_note"])
def test_case_semantic_identity_corruption_fails_closed(tmp_path, corruption: str) -> None:
    database, canonical = _confirmed_duplicate_database(tmp_path)
    case = database.fetch_one("SELECT case_id, evidence_json FROM governance_cases")
    assert case is not None
    if corruption == "rule_key":
        database.execute(
            "UPDATE governance_cases SET rule_key = ? WHERE case_id = ?",
            ("a" * 64, case["case_id"]),
        )
    elif corruption == "metadata_hash":
        evidence = json.loads(case["evidence_json"])
        evidence["relevant_metadata_hash"] = "b" * 64
        database.execute(
            "UPDATE governance_cases SET evidence_json = ? WHERE case_id = ?",
            (json.dumps(evidence, sort_keys=True, separators=(",", ":")), case["case_id"]),
        )
    else:
        with sqlite3.connect(database.path) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DELETE FROM notes WHERE note_id = ?", ("z-alias",))
    pipeline = _authoritative_pipeline(database, [canonical])

    with pytest.raises(ValueError, match="authority is unavailable"):
        pipeline.retrieve(
            "policy",
            "hybrid",
            query_date="2026-08-25",
            access_scope={"allow": ["*"]},
        )
