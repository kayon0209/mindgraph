from pathlib import Path

from application.chat_service import ChatService
from application.policy_conflict_service import PolicyConflictService
from domain.models import ChatRequest
from infrastructure.database import ProductDatabase
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


def _insert_note(
    database: ProductDatabase,
    note_id: str,
    *,
    policy_key: str | None,
    version: str,
    status: str,
    effective_from: str | None,
    effective_to: str | None,
) -> None:
    database.execute(
        """INSERT INTO notes (
            note_id, vault_path, title, content_hash, policy_key, document_version,
            effective_from, effective_to, policy_status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            note_id,
            f"policies/{note_id}.md",
            f"制度 {version}",
            f"hash-{note_id}",
            policy_key,
            version,
            effective_from,
            effective_to,
            status,
            "2026-01-01",
            "2026-01-01",
        ),
    )


def test_current_query_reports_all_overlapping_active_versions(tmp_path: Path) -> None:
    """Catches the system silently choosing one of two current active versions."""
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    _insert_note(
        database,
        "expense-v2",
        policy_key="expense.general",
        version="2.0",
        status="active",
        effective_from="2026-07-01",
        effective_to=None,
    )
    _insert_note(
        database,
        "expense-v3",
        policy_key="expense.general",
        version="3.0",
        status="active",
        effective_from="2026-08-01",
        effective_to=None,
    )
    _insert_note(
        database,
        "travel-v1",
        policy_key="travel.domestic",
        version="1.0",
        status="active",
        effective_from="2026-01-01",
        effective_to=None,
    )

    conflicts = PolicyConflictService(database).find_for_policy_keys(
        {"expense.general", "travel.domestic"},
        as_of="2026-08-18",
        include_historical=False,
    )

    assert conflicts == [
        {
            "policy_key": "expense.general",
            "as_of": "2026-08-18",
            "versions": [
                {
                    "note_id": "expense-v2",
                    "title": "制度 2.0",
                    "vault_path": "policies/expense-v2.md",
                    "version": "2.0",
                    "effective_from": "2026-07-01",
                    "effective_to": None,
                    "policy_status": "active",
                    "owner": None,
                },
                {
                    "note_id": "expense-v3",
                    "title": "制度 3.0",
                    "vault_path": "policies/expense-v3.md",
                    "version": "3.0",
                    "effective_from": "2026-08-01",
                    "effective_to": None,
                    "policy_status": "active",
                    "owner": None,
                },
            ],
        }
    ]


def test_conflict_detection_respects_date_history_and_missing_policy_keys(tmp_path: Path) -> None:
    """Catches future, expired, or ungrouped notes creating false current conflicts."""
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    _insert_note(
        database,
        "v1",
        policy_key="expense.general",
        version="1.0",
        status="archived",
        effective_from="2025-01-01",
        effective_to="2026-06-30",
    )
    _insert_note(
        database,
        "v2",
        policy_key="expense.general",
        version="2.0",
        status="active",
        effective_from="2026-07-01",
        effective_to=None,
    )
    _insert_note(
        database,
        "future",
        policy_key="expense.general",
        version="3.0",
        status="active",
        effective_from="2027-01-01",
        effective_to=None,
    )
    _insert_note(
        database,
        "ungrouped",
        policy_key=None,
        version="9.0",
        status="active",
        effective_from=None,
        effective_to=None,
    )
    service = PolicyConflictService(database)

    assert service.find_for_policy_keys({"expense.general"}, as_of="2026-08-18", include_historical=False) == []
    assert service.find_for_policy_keys({"expense.general"}, as_of="2025-06-01", include_historical=True) == []
    assert service.find_for_policy_keys(set(), as_of="2026-08-18", include_historical=False) == []


def test_explicit_empty_scope_does_not_expose_private_conflict_participants(
    tmp_path: Path,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    for note_id, version in (("private-v1", "1.0"), ("private-v2", "2.0")):
        _insert_note(
            database,
            note_id,
            policy_key="expense.private",
            version=version,
            status="active",
            effective_from="2026-01-01",
            effective_to=None,
        )

    conflicts = PolicyConflictService(database).find_for_policy_keys(
        {"expense.private"},
        as_of="2026-08-25",
        include_historical=False,
        access_scope={},
    )

    assert conflicts == []


class _Provider:
    available = True
    model_name = "test-model"
    provider_name = "test-provider"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, _messages):
        self.calls += 1
        return "不应生成", {}

    def stream(self, _messages):
        self.calls += 1
        yield {"delta": "不应生成"}


class _Pipeline:
    def __init__(self, trace: RetrievalTrace) -> None:
        self.trace = trace

    def retrieve(self, *_args, **_kwargs):
        return self.trace


def _conflicting_chat_service(database: ProductDatabase) -> tuple[ChatService, _Provider]:
    for note_id, version, starts in (("expense-v2", "2.0", "2026-07-01"), ("expense-v3", "3.0", "2026-08-01")):
        _insert_note(
            database,
            note_id,
            policy_key="expense.general",
            version=version,
            status="active",
            effective_from=starts,
            effective_to=None,
        )
    chunk = Chunk(
        chunk_id="expense-v3::0",
        text="员工应在三十日内提交报销。",
        document_id="expense-v3",
        chunk_index=0,
        section_path="报销时限",
        metadata={
            "title": "制度 3.0",
            "vault_path": "policies/expense-v3.md",
            "policy_key": "expense.general",
            "document_version": "3.0",
            "effective_from": "2026-08-01",
            "effective_to": None,
            "policy_status": "active",
        },
    )
    trace = RetrievalTrace(
        query="当前期限？",
        requested_strategy="hybrid",
        actual_strategy="hybrid",
        final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1)],
    )
    provider = _Provider()
    service = ChatService(database, lambda _top_k: _Pipeline(trace), provider)
    return service, provider


def test_chat_refuses_conflicting_policy_versions_before_generation(tmp_path: Path) -> None:
    """Catches the LLM generating an arbitrary answer from one of two active versions."""
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    service, provider = _conflicting_chat_service(database)

    result = service.answer(ChatRequest(question="当前报销期限？", query_date="2026-08-18"))

    assert result.result_state.value == "conflicting_evidence"
    assert "多个有效版本" in result.answer
    assert provider.calls == 0
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.policy_conflicts[0]["policy_key"] == "expense.general"
    assert {item["version"] for item in result.retrieval_trace.policy_conflicts[0]["versions"]} == {"2.0", "3.0"}
    assert database.fetch_one("SELECT result_state FROM query_logs WHERE request_id=?", (result.request_id,)) == {
        "result_state": "conflicting_evidence"
    }


def test_stream_emits_policy_conflict_without_generation_event(tmp_path: Path) -> None:
    """Catches SSE clients showing a fake generation phase for deterministic conflict refusal."""
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    service, provider = _conflicting_chat_service(database)

    events = list(service.stream(ChatRequest(question="当前报销期限？", query_date="2026-08-18")))
    names = [item["event"] for item in events]

    assert "policy_conflict_detected" in names
    assert "generation_started" not in names
    assert names[-1] == "completed"
    completed = events[-1]["data"]
    assert completed["result_state"] == "conflicting_evidence"
    assert completed["retrieval_trace"]["policy_conflicts"][0]["policy_key"] == "expense.general"
    assert provider.calls == 0
