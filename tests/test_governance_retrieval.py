from __future__ import annotations

from datetime import date

import pytest

from application.chat_service import ChatService
from application.governance_policy import GovernancePolicy
from domain.governance import (
    ConfirmedGovernanceDecision,
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
    return RetrievalPipeline(
        GuardedRetriever(chunks, blocked, "dense_score"),
        GuardedRetriever(chunks, blocked, "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
        confirmed_decision_loader=lambda _ids: decisions or {},
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
        confirmed_decision_loader=lambda _ids: {},
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

    def unavailable(_ids):
        raise RuntimeError("storage unavailable")

    pipeline = RetrievalPipeline(
        GuardedRetriever([chunk], set(), "dense_score"),
        GuardedRetriever([chunk], set(), "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
        confirmed_decision_loader=unavailable,
    )

    with pytest.raises(ValueError, match="decisions are unavailable"):
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
        confirmed_decision_loader=lambda _ids: {},
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

    def load(note_ids):
        loaded.append(tuple(sorted(note_ids)))
        return {}

    pipeline = RetrievalPipeline(
        GuardedRetriever([visible, hidden], {hidden.chunk_id}, "dense_score"),
        GuardedRetriever([visible, hidden], {hidden.chunk_id}, "sparse_score"),
        ReciprocalRankFusion(),
        governance_policy=GovernancePolicy(),
        confirmed_decision_loader=load,
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
