"""EvidenceQueryService 测试（M1）：共享证据段的契约与零行为变化。

覆盖：
- Bundle 四类 result_state（evidence_found / insufficient_evidence /
  permission_denied / conflicting_evidence）的确定性推导；
- ChatService 与 EvidenceQueryService 对同一请求产出一致 citations/冲突判定
  （共享同一检索段，双实现漂移在此显式失败）；
- MCP 截断语义（excerpt_limit=400）与 Chat 语义（500）并存；
- 信封不含 ACL/scope 字段（权限侧信道红线）。
"""

from __future__ import annotations

from pathlib import Path

from application.chat_service import ChatService
from application.evidence_query_service import EvidenceQueryService
from domain.evidence import EvidenceNextAction, EvidenceResultState
from domain.models import ChatRequest, ResultState
from infrastructure.database import ProductDatabase
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


class FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"
    available = True

    def complete(self, _messages):
        return ("ok", {"total_tokens": 1})

    def stream(self, _messages):
        yield {"delta": "ok"}


def _trace(chunks: list[tuple[str, dict]], warnings: list[str] | None = None, index_version: str = "idx-1") -> RetrievalTrace:
    candidates = [
        RetrievalCandidate(chunk=Chunk(
            chunk_id=f"{doc}::{i}",
            text=meta.get("_text", f"chunk {i} of {meta.get('document_title', doc)}"),
            document_id=doc,
            chunk_index=i,
            section_path=meta.get("section_path", "默认节"),
            metadata={k: v for k, v in meta.items() if not k.startswith("_")},
        ), final_rank=i + 1, dense_score=0.9)
        for i, (doc, meta) in enumerate(chunks)
    ]
    return RetrievalTrace(
        query="q",
        requested_strategy="hybrid",
        actual_strategy="hybrid",
        candidate_counts={"final": len(candidates)},
        final_selected_chunks=candidates,
        latency_ms={"total_retrieval_ms": 1.0},
        index_version=index_version,
        applied_filters={},
        warnings=warnings or ["query_understanding:none:no_query_understanding_required"],
    )


class StubPipeline:
    """按政策键返回固定证据，并可注入冲突/ACL 裁剪场景。"""

    def __init__(self, scenario: str = "single") -> None:
        self.scenario = scenario
        base_meta = {
            "document_title": "差旅费报销管理办法",
            "vault_path": "policies/travel.md",
            "document_version": "v2",
            "effective_from": "2026-01-01",
            "policy_key": "travel.meal",
            "policy_status": "active",
            "owner": "财务部",
        }
        if scenario == "single":
            self.trace = _trace([("policy.md", {**base_meta, "policy_status": "active"})])
        elif scenario == "conflict":
            self.trace = _trace([
                ("a.md", {**base_meta, "document_version": "v1"}),
                ("b.md", {**base_meta, "document_version": "v2"}),
            ])
        else:  # acl_denied / empty
            self.trace = _trace([], warnings=[
                "query_understanding:none:no_query_understanding_required",
                "access_denied_chunks_filtered",
            ] if scenario == "acl_denied" else ["query_understanding:none:no_query_understanding_required"])

    def retrieve(self, *_args, **_kwargs):
        return self.trace


def _seed_conflict_notes(database: ProductDatabase) -> None:
    for doc_id, version in (("a.md", "v1"), ("b.md", "v2")):
        database.execute(
            """INSERT INTO notes (note_id, vault_path, title, content_hash, document_version, effective_from,
               effective_to, policy_status, policy_key, owner, acl_public, workspace, department, acl_json,
               chunk_count, index_status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, f"policies/{doc_id}", "差旅费报销管理办法", f"hash-{doc_id}", version, "2026-01-01", None,
             "active", "travel.meal", "财务部", 1, None, None, "{}", 1, "active",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )


def _build(tmp_path: Path, scenario: str = "single", seed_db: bool = False):
    database = ProductDatabase(tmp_path / f"evq-{scenario}.sqlite3")
    database.initialize()
    if seed_db:
        _seed_conflict_notes(database)
    service = ChatService(database, lambda top_k: StubPipeline(scenario), FakeProvider(), privacy_log_questions=False)
    return service, EvidenceQueryService(service), database


def _request(question: str = "差旅报销标准是什么？", **overrides) -> ChatRequest:
    return ChatRequest(question=question, retrieval_strategy="hybrid", **overrides)


def test_bundle_evidence_found(tmp_path: Path):
    _service, evidence, _db = _build(tmp_path, "single")
    result = evidence.query(_request())
    assert result.bundle.result_state is EvidenceResultState.evidence_found
    assert result.bundle.resolved_next_action() is EvidenceNextAction.generate
    assert result.bundle.evidence[0].policy_key == "travel.meal"
    assert result.bundle.evidence[0].excerpt is not None
    assert result.bundle.retryable is False
    # trace/citations 并行供 Chat 通道内部消费
    assert result.citations[0].citation_id == "citation-1"


def test_bundle_conflicting_evidence(tmp_path: Path):
    _service, evidence, _db = _build(tmp_path, "conflict", seed_db=True)
    result = evidence.query(_request("v1 和 v2 哪个适用？", query_date="2026-06-01"))
    assert result.bundle.result_state is EvidenceResultState.conflicting_evidence
    assert result.bundle.resolved_next_action() is EvidenceNextAction.human_review
    assert len(result.bundle.conflicts) == 1
    assert result.bundle.conflicts[0].policy_key == "travel.meal"
    assert len(result.bundle.conflicts[0].versions) == 2


def test_bundle_permission_denied_when_acl_filters_everything(tmp_path: Path):
    _service, evidence, _db = _build(tmp_path, "acl_denied")
    result = evidence.query(_request())
    assert result.bundle.result_state is EvidenceResultState.permission_denied
    assert result.bundle.resolved_next_action() is EvidenceNextAction.request_access


def test_bundle_insufficient_evidence(tmp_path: Path):
    _service, evidence, _db = _build(tmp_path, "empty")
    result = evidence.query(_request())
    assert result.bundle.result_state is EvidenceResultState.insufficient_evidence
    assert result.bundle.resolved_next_action() is EvidenceNextAction.ask_clarification
    assert result.bundle.evidence == []


def test_bundle_excludes_acl_surface(tmp_path: Path):
    _service, evidence, _db = _build(tmp_path, "single")
    payload = evidence.query(_request()).bundle.model_dump(mode="json")
    serialized = str(payload)
    assert "acl_json" not in serialized
    assert "access_scope" not in serialized


def test_chat_service_and_evidence_service_agree(tmp_path: Path):
    """零行为变化护栏：同一请求下 ChatService.answer 与 EvidenceQueryService
    的检索/冲突判定一致（state 与 citations 数量）。"""
    service, evidence, _db = _build(tmp_path, "conflict", seed_db=True)
    request = _request("差旅报销 v1 v2 冲突怎么办？", query_date="2026-06-01")

    answer = service.answer(request)
    result = evidence.query(request)

    assert answer.result_state is ResultState.conflicting_evidence
    assert result.bundle.result_state is EvidenceResultState.conflicting_evidence
    assert len(answer.citations) == len(result.bundle.evidence)


def test_mcp_excerpt_limit_is_applied(tmp_path: Path):
    _service, evidence, _db = _build(tmp_path, "single")
    result = evidence.query(_request(), excerpt_limit=400)
    assert all(len(item.excerpt) <= 400 for item in result.bundle.evidence if item.excerpt)
