from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from application.access_control import GovernanceAuthorizationError
from application.governance_case_service import GovernanceCaseService
from application.governance_policy import GovernancePolicy
from application.governance_reconciliation_service import (
    GovernancePersistenceError,
    GovernanceReconciliationService,
)
from domain.errors import GovernanceConflictError
from infrastructure.database import ProductDatabase

TODAY = date(2026, 8, 25)


def finance_scope() -> dict[str, object]:
    return {"allow": ["department:finance"], "deny": []}


def admin_scope() -> dict[str, object]:
    return {"allow": ["*"], "deny": []}


def _insert_note(
    database: ProductDatabase,
    note_id: str,
    *,
    policy_key: str = "travel-expense",
    version: str = "1.0",
    content_hash: str = "hash-v1",
    department: str = "finance",
    effective_from: str = "2026-01-01",
    effective_to: str | None = None,
    acl_json: str | None = None,
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
            f"private/{note_id}.md",
            "connector-main",
            f"Sensitive title {note_id}",
            content_hash,
            '{"secret":"must-not-leak"}',
            "local_only",
            "indexed",
            "corp",
            department,
            acl_json or json.dumps({"allow": [f"department:{department}"]}),
            0,
            policy_key,
            "Finance",
            version,
            effective_from,
            effective_to,
            "active",
            "[]",
            "2026-08-25T00:00:00Z",
            "2026-08-25T00:00:00Z",
        ),
    )


@pytest.fixture
def database(tmp_path: Path) -> ProductDatabase:
    result = ProductDatabase(tmp_path / "governance.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def reconciliation(database: ProductDatabase) -> GovernanceReconciliationService:
    return GovernanceReconciliationService(database, GovernancePolicy())


@pytest.fixture
def case_service(
    database: ProductDatabase,
    reconciliation: GovernanceReconciliationService,
) -> GovernanceCaseService:
    return GovernanceCaseService(database, reconciliation, today=lambda: TODAY)


def _create_conflict_case(
    database: ProductDatabase,
    reconciliation: GovernanceReconciliationService,
    *,
    second_department: str = "finance",
) -> str:
    _insert_note(
        database,
        "travel-old",
        version="1.0",
        content_hash="hash-old",
        effective_to="2026-12-31",
    )
    _insert_note(
        database,
        "travel-new",
        version="2.0",
        content_hash="hash-new",
        department=second_department,
        effective_from="2026-08-01",
    )
    reconciliation.reconcile(as_of=TODAY)
    row = database.fetch_one(
        "SELECT case_id FROM governance_cases WHERE case_type = 'version_conflict'"
    )
    assert row is not None
    return str(row["case_id"])


@pytest.fixture
def proposed_case(
    database: ProductDatabase,
    reconciliation: GovernanceReconciliationService,
) -> str:
    return _create_conflict_case(database, reconciliation)


def _case_status(database: ProductDatabase, case_id: str) -> str:
    row = database.fetch_one(
        "SELECT status FROM governance_cases WHERE case_id = ?", (case_id,)
    )
    assert row is not None
    return str(row["status"])


def _case_event_actions(database: ProductDatabase, case_id: str) -> list[str]:
    return [
        str(row["action"])
        for row in database.fetch_all(
            "SELECT action FROM governance_events WHERE case_id = ? ORDER BY rowid",
            (case_id,),
        )
    ]


def test_governance_write_requires_any_allowed_role(
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches read-only and all-of role checks authorizing or blocking a decision."""
    with pytest.raises(GovernanceAuthorizationError):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="confirm",
            canonical_note_id=None,
            actor="reader",
            roles=("read",),
            access_scope=finance_scope(),
            request_id="req-denied",
        )

    result = case_service.resolve(
        proposed_case,
        expected_status="proposed",
        decision="reject",
        canonical_note_id=None,
        actor="reviewer",
        roles=("read", "governance_reviewer"),
        access_scope=finance_scope(),
        request_id="req-allowed",
    )

    assert result.status == "rejected"


def test_hidden_case_is_indistinguishable_from_missing(
    database: ProductDatabase,
    reconciliation: GovernanceReconciliationService,
    case_service: GovernanceCaseService,
) -> None:
    """Catches one visible participant disclosing a case containing a hidden participant."""
    hidden_case = _create_conflict_case(
        database,
        reconciliation,
        second_department="hr",
    )

    assert case_service.get_case(hidden_case, access_scope=finance_scope()) is None
    assert case_service.get_case("missing", access_scope=finance_scope()) is None
    assert case_service.list_cases(access_scope=finance_scope()) == []
    assert case_service.list_events(
        access_scope=finance_scope(), case_id=hidden_case
    ) == []
    assert case_service.list_events(
        access_scope=finance_scope(), case_id="missing"
    ) == []


def test_hidden_write_checks_acl_before_governance_role(
    database: ProductDatabase,
    reconciliation: GovernanceReconciliationService,
    case_service: GovernanceCaseService,
) -> None:
    """Catches role errors revealing that an out-of-scope case exists."""
    hidden_case = _create_conflict_case(
        database,
        reconciliation,
        second_department="hr",
    )

    for case_id in (hidden_case, "missing"):
        with pytest.raises(GovernanceConflictError, match="unavailable"):
            case_service.resolve(
                case_id,
                expected_status="proposed",
                decision="reject",
                canonical_note_id=None,
                actor="reader",
                roles=("read",),
                access_scope=finance_scope(),
                request_id="req-hidden",
            )


def test_resolve_uses_compare_and_swap(
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches stale clients overwriting an already completed human decision."""
    case_service.resolve(
        proposed_case,
        expected_status="proposed",
        decision="reject",
        canonical_note_id=None,
        actor="reviewer",
        roles=("governance_reviewer",),
        access_scope=finance_scope(),
        request_id="req-1",
    )

    with pytest.raises(GovernanceConflictError):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="confirm",
            canonical_note_id=None,
            actor="reviewer",
            roles=("governance_reviewer",),
            access_scope=finance_scope(),
            request_id="req-2",
        )


def test_event_failure_rolls_back_decision(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches a case decision committing without its immutable audit event."""
    before_events = len(_case_event_actions(database, proposed_case))
    database.execute(
        """
        CREATE TRIGGER reject_human_rejection
        BEFORE INSERT ON governance_events
        WHEN NEW.action = 'rejected'
        BEGIN
          SELECT RAISE(ABORT, 'test event rejection');
        END
        """
    )

    with pytest.raises(GovernancePersistenceError):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="reject",
            canonical_note_id=None,
            actor="reviewer",
            roles=("governance_reviewer",),
            access_scope=finance_scope(),
            request_id="req-event-failure",
        )

    assert _case_status(database, proposed_case) == "proposed"
    assert len(_case_event_actions(database, proposed_case)) == before_events


def test_reconciliation_failure_rolls_back_decision_and_audit(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches post-decision projection failure escaping the caller-owned transaction."""
    before_events = _case_event_actions(database, proposed_case)
    database.execute(
        """
        CREATE TRIGGER reject_projection_event
        BEFORE INSERT ON governance_events
        WHEN NEW.action = 'state_changed'
        BEGIN
          SELECT RAISE(ABORT, 'test projection rejection');
        END
        """
    )

    with pytest.raises(GovernancePersistenceError):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="confirm",
            canonical_note_id=None,
            actor="admin-user",
            roles=("admin",),
            access_scope=admin_scope(),
            request_id="req-reconcile-failure",
        )

    assert _case_status(database, proposed_case) == "proposed"
    assert _case_event_actions(database, proposed_case) == before_events


def test_revoke_appends_history_and_marks_participants_pending(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches revocation rewriting history or leaving governed indexes stale."""
    case_service.resolve(
        proposed_case,
        expected_status="proposed",
        decision="confirm",
        canonical_note_id=None,
        actor="reviewer",
        roles=("governance_reviewer",),
        access_scope=finance_scope(),
        request_id="req-confirm",
    )
    database.execute("UPDATE notes SET index_status = 'indexed'")

    result = case_service.revoke(
        proposed_case,
        expected_status="confirmed",
        actor="admin-user",
        roles=("admin",),
        access_scope=admin_scope(),
        request_id="req-revoke",
    )

    assert result.status == "revoked"
    assert _case_event_actions(database, proposed_case)[-2:] == ["confirmed", "revoked"]
    assert {
        row["index_status"]
        for row in database.fetch_all(
            """
            SELECT notes.index_status
            FROM notes
            JOIN governance_case_notes ON governance_case_notes.note_id = notes.note_id
            WHERE governance_case_notes.case_id = ?
            """,
            (proposed_case,),
        )
    } == {"pending"}


def test_confirmed_duplicate_assigns_canonical_and_alias_roles(
    database: ProductDatabase,
    reconciliation: GovernanceReconciliationService,
    case_service: GovernanceCaseService,
) -> None:
    """Catches a confirmed canonical decision remaining unusable candidate linkage."""
    _insert_note(database, "duplicate-a", content_hash="same-hash")
    _insert_note(
        database,
        "duplicate-b",
        content_hash="same-hash",
        acl_json='{"allow":["department:finance"],"review":"required"}',
    )
    reconciliation.reconcile(as_of=TODAY)
    row = database.fetch_one(
        """
        SELECT case_id FROM governance_cases
        WHERE case_type = 'exact_duplicate' AND status = 'proposed'
        """
    )
    assert row is not None
    case_id = str(row["case_id"])

    result = case_service.resolve(
        case_id,
        expected_status="proposed",
        decision="confirm",
        canonical_note_id="duplicate-a",
        actor="reviewer",
        roles=("governance_reviewer",),
        access_scope=finance_scope(),
        request_id="req-duplicate",
    )

    assert result.canonical_note_id == "duplicate-a"
    assert {
        participant.note_id: participant.participant_role
        for participant in result.participants
    } == {"duplicate-a": "canonical", "duplicate-b": "alias"}


def test_nonparticipant_canonical_is_rejected_without_mutation(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches a foreign note becoming canonical through an unchecked identifier."""
    with pytest.raises(ValueError, match="participant"):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="confirm",
            canonical_note_id="foreign-note",
            actor="reviewer",
            roles=("governance_reviewer",),
            access_scope=finance_scope(),
            request_id="req-foreign",
        )

    assert _case_status(database, proposed_case) == "proposed"


def test_reads_return_safe_immutable_views_and_state_capabilities(
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches governance reads exposing note bodies, paths, ACLs, or mutable state maps."""
    result = case_service.get_case(proposed_case, access_scope=finance_scope())

    assert result is not None
    assert result.case_id == proposed_case
    assert result.evidence_ids == ("travel-new", "travel-old")
    assert result.capabilities.can_resolve is True
    assert result.capabilities.can_revoke is False
    serialized = repr(result)
    for sentinel in ("Sensitive title", "private/", "must-not-leak", "acl_json"):
        assert sentinel not in serialized


def test_event_views_whitelist_scalar_governance_state(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches arbitrary persisted event JSON being reflected through the read model."""
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, case_id, policy_key, actor, action, previous_state_json,
            new_state_json, reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-safe-view",
            proposed_case,
            "travel-expense",
            "reviewer",
            "proposed",
            '{"status":"proposed","secret":"sentinel-secret"}',
            '{"status":"proposed","score":0.75,"body":"sentinel-body"}',
            "overlapping_effective_intervals",
            '["travel-new","travel-old"]',
            "human_review",
            "req-safe-view",
            "2026-08-25T00:00:00Z",
        ),
    )

    events = case_service.list_events(
        access_scope=finance_scope(), case_id=proposed_case
    )
    event = next(item for item in events if item.event_id == "event-safe-view")

    assert dict(event.previous_state) == {"status": "proposed"}
    assert dict(event.new_state) == {"status": "proposed", "score": 0.75}
    serialized = repr(events)
    assert "sentinel-secret" not in serialized
    assert "sentinel-body" not in serialized


def test_event_with_hidden_evidence_note_is_not_visible(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches a visible case event disclosing an evidence note outside the caller ACL."""
    _insert_note(
        database,
        "hr-secret-note",
        policy_key="hr-private-policy",
        department="hr",
    )
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, case_id, policy_key, actor, action, previous_state_json,
            new_state_json, reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-hidden-evidence",
            proposed_case,
            "travel-expense",
            "reviewer",
            "proposed",
            '{}',
            '{"status":"proposed"}',
            "overlapping_effective_intervals",
            '["hr-secret-note"]',
            "human_review",
            "req-hidden-evidence",
            "2026-08-25T00:00:00Z",
        ),
    )

    events = case_service.list_events(
        access_scope=finance_scope(), case_id=proposed_case
    )

    assert "event-hidden-evidence" not in {event.event_id for event in events}


def test_hidden_case_event_does_not_parse_malformed_evidence(
    database: ProductDatabase,
    reconciliation: GovernanceReconciliationService,
    case_service: GovernanceCaseService,
) -> None:
    """Catches malformed payload errors revealing a case hidden by participant ACL."""
    hidden_case = _create_conflict_case(
        database,
        reconciliation,
        second_department="hr",
    )
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, case_id, policy_key, actor, action, previous_state_json,
            new_state_json, reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-hidden-malformed-evidence",
            hidden_case,
            "travel-expense",
            "reviewer",
            "proposed",
            '{}',
            '{"status":"proposed"}',
            "overlapping_effective_intervals",
            "not-json",
            "human_review",
            "req-hidden-malformed-evidence",
            "2026-08-25T00:00:00Z",
        ),
    )

    assert case_service.list_events(
        access_scope=finance_scope(), case_id=hidden_case
    ) == []


def test_event_with_duplicate_evidence_is_not_visible(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches non-canonical duplicate evidence being reflected in audit views."""
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, case_id, policy_key, actor, action, previous_state_json,
            new_state_json, reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-duplicate-evidence",
            proposed_case,
            "travel-expense",
            "reviewer",
            "proposed",
            '{}',
            '{"status":"proposed"}',
            "overlapping_effective_intervals",
            '["travel-new","travel-new"]',
            "human_review",
            "req-duplicate-evidence",
            "2026-08-25T00:00:00Z",
        ),
    )

    events = case_service.list_events(
        access_scope=finance_scope(), case_id=proposed_case
    )

    assert "event-duplicate-evidence" not in {event.event_id for event in events}


def test_event_with_missing_evidence_note_is_not_visible(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches unknown evidence identifiers bypassing note-backed ACL checks."""
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, case_id, policy_key, actor, action, previous_state_json,
            new_state_json, reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-missing-evidence",
            proposed_case,
            "travel-expense",
            "reviewer",
            "proposed",
            '{}',
            '{"status":"proposed"}',
            "overlapping_effective_intervals",
            '["missing-note"]',
            "human_review",
            "req-missing-evidence",
            "2026-08-25T00:00:00Z",
        ),
    )

    events = case_service.list_events(
        access_scope=finance_scope(), case_id=proposed_case
    )

    assert "event-missing-evidence" not in {event.event_id for event in events}


def test_event_without_acl_anchor_is_not_visible(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
) -> None:
    """Catches targetless audit rows bypassing every ACL check."""
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, actor, action, previous_state_json, new_state_json,
            reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-without-anchor",
            "private-actor",
            "state_changed",
            '{}',
            '{"status":"proposed"}',
            "governance_state_changed",
            '[]',
            "lifecycle_rule",
            "req-without-anchor",
            "2026-08-25T00:00:00Z",
        ),
    )

    events = case_service.list_events(access_scope=finance_scope())

    assert "event-without-anchor" not in {event.event_id for event in events}


def test_targetless_event_with_malformed_evidence_is_not_visible(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
) -> None:
    """Catches corrupt targetless rows leaking their existence through parse errors."""
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, actor, action, previous_state_json, new_state_json,
            reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-targetless-malformed-evidence",
            "private-actor",
            "state_changed",
            '{}',
            '{"status":"proposed"}',
            "governance_state_changed",
            "not-json",
            "lifecycle_rule",
            "req-targetless-malformed-evidence",
            "2026-08-25T00:00:00Z",
        ),
    )

    events = case_service.list_events(access_scope=finance_scope())

    assert "event-targetless-malformed-evidence" not in {
        event.event_id for event in events
    }


def test_visible_event_with_malformed_evidence_fails_closed(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches visible corrupt evidence being silently accepted or partially returned."""
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, case_id, policy_key, actor, action, previous_state_json,
            new_state_json, reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-visible-malformed-evidence",
            proposed_case,
            "travel-expense",
            "reviewer",
            "proposed",
            '{}',
            '{"status":"proposed"}',
            "overlapping_effective_intervals",
            "not-json",
            "human_review",
            "req-visible-malformed-evidence",
            "2026-08-25T00:00:00Z",
        ),
    )

    with pytest.raises(GovernancePersistenceError, match="evidence ids"):
        case_service.list_events(
            access_scope=finance_scope(), case_id=proposed_case
        )


def test_event_state_rejects_invalid_value_for_safe_key(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches sensitive text smuggled through an allowlisted event-state key."""
    database.execute(
        """
        INSERT INTO governance_events (
            event_id, case_id, policy_key, actor, action, previous_state_json,
            new_state_json, reason_code, evidence_ids_json, source, request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "event-invalid-safe-value",
            proposed_case,
            "travel-expense",
            "reviewer",
            "proposed",
            '{"status":"credential-like-secret"}',
            '{"status":"proposed"}',
            "overlapping_effective_intervals",
            '["travel-new","travel-old"]',
            "human_review",
            "req-invalid-safe-value",
            "2026-08-25T00:00:00Z",
        ),
    )

    with pytest.raises(GovernancePersistenceError, match="event state"):
        case_service.list_events(
            access_scope=finance_scope(), case_id=proposed_case
        )


def test_begin_lock_failure_is_wrapped_in_persistence_error(
    database: ProductDatabase,
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches BEGIN IMMEDIATE lock errors escaping the stable domain boundary."""
    lock_connection = database.connect()
    lock_connection.execute("PRAGMA busy_timeout=1")
    lock_connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(
            GovernancePersistenceError,
            match="governance decision could not be persisted",
        ):
            case_service.resolve(
                proposed_case,
                expected_status="proposed",
                decision="reject",
                canonical_note_id=None,
                actor="reviewer",
                roles=("governance_reviewer",),
                access_scope=finance_scope(),
                request_id="req-locked",
            )
    finally:
        lock_connection.rollback()
        lock_connection.close()


def test_list_filters_status_and_enforces_limit(
    case_service: GovernanceCaseService,
    proposed_case: str,
) -> None:
    """Catches list filters being ignored and unbounded read requests reaching SQLite."""
    assert [
        item.case_id
        for item in case_service.list_cases(
            access_scope=finance_scope(), status="proposed", limit=1
        )
    ] == [proposed_case]
    assert case_service.list_cases(
        access_scope=finance_scope(), status="confirmed"
    ) == []
    with pytest.raises(ValueError, match="limit"):
        case_service.list_cases(access_scope=finance_scope(), limit=0)
