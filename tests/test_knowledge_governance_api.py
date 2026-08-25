from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.auth import get_required_principal
from api.exception_handlers import (
    authorization_error_handler,
    product_error_handler,
    validation_error_handler,
    value_error_handler,
)
from api.routes import knowledge_governance
from api.routes.health import governance_health
from application.governance_case_service import GovernanceCaseService
from application.governance_policy import GovernancePolicy
from application.governance_reconciliation_service import GovernanceReconciliationService
from domain.errors import AuthorizationError, ProductError
from infrastructure.database import ProductDatabase

TODAY = date(2026, 8, 25)


def _insert_note(database: ProductDatabase, note_id: str, *, department: str = "finance", version: str = "1.0") -> None:
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
            note_id, f"private/{note_id}.md", "connector-main", f"Secret title {note_id}",
            f"hash-{note_id}", '{"secret":"never-return"}', "local_only", "indexed",
            "corp", department, json.dumps({"allow": [f"department:{department}"]}), 0,
            "travel-expense", "Finance", version, "2026-01-01", "2026-12-31",
            "active", "[]", "2026-08-25T00:00:00Z", "2026-08-25T00:00:00Z",
        ),
    )


@pytest.fixture
def api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = ProductDatabase(tmp_path / "api.sqlite3")
    database.initialize()
    _insert_note(database, "travel-old", version="1.0")
    _insert_note(database, "travel-new", version="2.0")
    reconciliation = GovernanceReconciliationService(database, GovernancePolicy())
    reconciliation.reconcile(as_of=TODAY)
    case_id = str(database.fetch_one("SELECT case_id FROM governance_cases")["case_id"])
    service = GovernanceCaseService(database, reconciliation, today=lambda: TODAY)
    container = SimpleNamespace(database=database, governance_cases=service)
    monkeypatch.setattr(knowledge_governance, "get_container", lambda: container)

    app = FastAPI()
    @app.middleware("http")
    async def request_identity(request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID", "request-generated")
        return await call_next(request)

    app.add_exception_handler(ProductError, product_error_handler)
    app.add_exception_handler(AuthorizationError, authorization_error_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    from fastapi.exceptions import RequestValidationError
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(knowledge_governance.router, prefix="/api/v1")
    principal = {
        "name": "reviewer-1",
        "roles": ["governance_reviewer"],
        "authenticated": True,
        "allow": ["department:finance"],
        "deny": [],
    }
    app.dependency_overrides[get_required_principal] = lambda: principal
    with TestClient(app) as client:
        yield client, database, case_id, principal


def test_resolve_rejects_actor_override(api_fixture) -> None:
    client, _, case_id, _ = api_fixture
    response = client.post(
        f"/api/v1/knowledge-governance/cases/{case_id}/resolve",
        json={"expected_status": "proposed", "decision": "reject", "resolved_by": "spoofed"},
    )
    assert response.status_code == 422


def test_reader_cannot_resolve_and_denial_is_audited(api_fixture) -> None:
    client, database, case_id, principal = api_fixture
    principal["roles"] = ["read"]
    principal["name"] = "reader-1"
    response = client.post(
        f"/api/v1/knowledge-governance/cases/{case_id}/resolve",
        json={"expected_status": "proposed", "decision": "reject"},
    )
    assert response.status_code == 403
    audit = database.fetch_one("SELECT * FROM access_audit ORDER BY created_at DESC LIMIT 1")
    assert audit["actor"] == "reader-1"
    assert audit["decision"] == "deny"


def test_hidden_case_returns_same_404_as_missing(api_fixture) -> None:
    client, database, case_id, principal = api_fixture
    database.execute("UPDATE notes SET department='hr', acl_json=? WHERE note_id='travel-new'", ('{"allow":["department:hr"]}',))
    hidden = client.get(f"/api/v1/knowledge-governance/cases/{case_id}")
    missing = client.get("/api/v1/knowledge-governance/cases/missing-case")
    assert hidden.status_code == missing.status_code == 404
    principal["roles"] = ["read"]
    hidden_write = client.post(
        f"/api/v1/knowledge-governance/cases/{case_id}/resolve",
        json={"expected_status": "proposed", "decision": "reject"},
    )
    assert hidden_write.status_code == 404


def test_resolve_uses_principal_actor_request_id_and_cas(api_fixture) -> None:
    client, database, case_id, _ = api_fixture
    response = client.post(
        f"/api/v1/knowledge-governance/cases/{case_id}/resolve",
        headers={"X-Request-ID": "request-safe-1"},
        json={"expected_status": "proposed", "decision": "reject"},
    )
    assert response.status_code == 200
    event = database.fetch_one(
        "SELECT actor, request_id FROM governance_events WHERE case_id=? AND source='human_review'",
        (case_id,),
    )
    assert event == {"actor": "reviewer-1", "request_id": "request-safe-1"}
    stale = client.post(
        f"/api/v1/knowledge-governance/cases/{case_id}/resolve",
        json={"expected_status": "proposed", "decision": "confirm"},
    )
    assert stale.status_code == 409


def test_invalid_canonical_is_422(api_fixture) -> None:
    client, _, case_id, _ = api_fixture
    response = client.post(
        f"/api/v1/knowledge-governance/cases/{case_id}/resolve",
        json={
            "expected_status": "proposed",
            "decision": "confirm",
            "canonical_note_id": "foreign-note",
        },
    )
    assert response.status_code == 422


def test_read_capabilities_follow_principal_role_and_events_are_safe(api_fixture) -> None:
    client, _, _, principal = api_fixture
    principal["roles"] = ["read"]
    cases = client.get("/api/v1/knowledge-governance/cases?status=proposed").json()["items"]
    assert cases[0]["capabilities"] == {"can_resolve": False, "can_revoke": False}
    serialized = json.dumps(client.get("/api/v1/knowledge-governance/events").json()).lower()
    for forbidden in ("body", "title", "vault_path", "acl_json", "token", "secret"):
        assert forbidden not in serialized


def test_case_list_rejects_unknown_query_fields(api_fixture) -> None:
    client, _, _, _ = api_fixture
    assert client.get("/api/v1/knowledge-governance/cases?actor=spoofed").status_code == 422


def test_health_reports_aggregate_governance_readiness_without_ids(api_fixture) -> None:
    _, database, case_id, _ = api_fixture
    body = governance_health(SimpleNamespace(database=database, mindgraph_index_root=None))
    assert set(body) == {
        "schema_ready",
        "last_reconciled_at",
        "last_reconciliation_status",
        "pending_case_count",
        "active_index_governed",
    }
    assert body["schema_ready"] is True
    assert body["pending_case_count"] == 1
    assert case_id not in json.dumps(body)


def test_health_allows_schema_eight_but_reports_governance_unavailable(api_fixture) -> None:
    client, database, _, _ = api_fixture
    database.execute("UPDATE schema_meta SET version=8")
    body = governance_health(SimpleNamespace(database=database, mindgraph_index_root=None))
    assert body == {
        "schema_ready": False,
        "last_reconciled_at": None,
        "last_reconciliation_status": "unavailable",
        "pending_case_count": None,
        "active_index_governed": False,
    }
    response = client.get("/api/v1/knowledge-governance/cases")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "governance_unavailable"


def test_governance_api_has_no_batch_mutation(api_fixture) -> None:
    client, _, _, _ = api_fixture
    response = client.post(
        "/api/v1/knowledge-governance/cases/resolve-batch",
        json={"ids": ["case-1"], "decision": "confirm"},
    )
    assert response.status_code in {404, 405, 422}
