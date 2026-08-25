from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

import pytest

from application.governance_policy import GovernancePolicy
from application.governance_reconciliation_service import (
    GovernancePersistenceError,
    GovernanceReconciliationService,
)
from domain.governance import GovernanceDisposition, GovernanceMode
from infrastructure.database import ProductDatabase


@dataclass(frozen=True, slots=True)
class StoredNote:
    note_id: str


@pytest.fixture
def schema9_database(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "governance.sqlite3")
    database.initialize()
    return database


def _insert_note(
    database: ProductDatabase,
    note_id: str,
    *,
    policy_key: str = "travel-expense",
    version: str = "1.0",
    effective_from: str = "2026-01-01",
    effective_to: str | None = None,
    content_hash: str = "hash-v1",
    acl_json: str = '{"allow":["workspace:corp"]}',
) -> StoredNote:
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
            "finance",
            acl_json,
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
    return StoredNote(note_id)


@pytest.fixture
def governed_note(schema9_database: ProductDatabase) -> StoredNote:
    return _insert_note(schema9_database, "policy-v1")


@pytest.fixture
def expiring_note(schema9_database: ProductDatabase) -> StoredNote:
    return _insert_note(schema9_database, "expiring-policy", effective_to="2026-08-25")


@pytest.fixture
def overlapping_notes(schema9_database: ProductDatabase) -> tuple[StoredNote, StoredNote]:
    return (
        _insert_note(
            schema9_database,
            "travel-old",
            version="z-old-label",
            effective_from="2026-01-01",
            effective_to="2026-12-31",
            content_hash="hash-old",
        ),
        _insert_note(
            schema9_database,
            "travel-new",
            version="a-new-label",
            effective_from="2026-08-01",
            content_hash="hash-new",
        ),
    )


@pytest.fixture
def equivalent_notes(schema9_database: ProductDatabase) -> tuple[StoredNote, StoredNote]:
    return (
        _insert_note(schema9_database, "duplicate-b", content_hash="same-checksum"),
        _insert_note(schema9_database, "duplicate-a", content_hash="same-checksum"),
    )


@pytest.fixture
def acl_divergent_notes(schema9_database: ProductDatabase) -> tuple[StoredNote, StoredNote]:
    return (
        _insert_note(schema9_database, "acl-a", content_hash="same-checksum"),
        _insert_note(
            schema9_database,
            "acl-b",
            content_hash="same-checksum",
            acl_json='{"allow":["workspace:other"]}',
        ),
    )


def _event_count_for_note(database: ProductDatabase, note_id: str) -> int:
    row = database.fetch_one(
        "SELECT COUNT(*) AS count FROM governance_events WHERE note_id = ?", (note_id,)
    )
    assert row is not None
    return int(row["count"])


def _projection_for(database: ProductDatabase, note_id: str) -> dict[str, object]:
    row = database.fetch_one("SELECT * FROM governance_note_state WHERE note_id = ?", (note_id,))
    assert row is not None
    return row


def _only_case(database: ProductDatabase) -> dict[str, object]:
    rows = database.fetch_all("SELECT * FROM governance_cases")
    assert len(rows) == 1
    return rows[0]


def test_reconcile_is_idempotent(
    schema9_database: ProductDatabase, governed_note: StoredNote
) -> None:
    """Catches identical reconciliation rewriting projection state or audit history."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())

    first = service.reconcile(as_of=date(2026, 8, 25))
    second = service.reconcile(as_of=date(2026, 8, 25))

    assert first.changed == 1
    assert first.events_appended == 1
    assert second.changed == 0
    assert second.events_appended == 0
    assert _event_count_for_note(schema9_database, governed_note.note_id) == 1


def test_date_rollover_changes_projection_once(
    schema9_database: ProductDatabase, expiring_note: StoredNote
) -> None:
    """Catches an effective-period boundary being missed or repeatedly audited."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))

    changed = service.reconcile(as_of=date(2026, 8, 26))
    repeated = service.reconcile(as_of=date(2026, 8, 26))

    assert changed.changed == 1
    assert changed.pending == 1
    assert changed.events_appended == 1
    assert repeated.changed == 0
    assert repeated.events_appended == 0
    assert _projection_for(schema9_database, expiring_note.note_id)["disposition"] == "excluded"


def test_new_calendar_day_without_state_change_refreshes_only_evaluated_on(
    schema9_database: ProductDatabase, governed_note: StoredNote
) -> None:
    """Catches raw evaluation dates polluting the governance state fingerprint."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    before_events = _event_count_for_note(schema9_database, governed_note.note_id)

    result = service.reconcile(as_of=date(2026, 8, 26))

    assert result.changed == 0
    assert result.pending == 0
    assert result.events_appended == 0
    assert _event_count_for_note(schema9_database, governed_note.note_id) == before_events
    assert _projection_for(schema9_database, governed_note.note_id)["evaluated_on"] == "2026-08-26"


def test_equivalent_text_normalization_does_not_change_fingerprint(
    schema9_database: ProductDatabase, governed_note: StoredNote
) -> None:
    """Catches raw formatting differences masquerading as a governance decision change."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    before_events = _event_count_for_note(schema9_database, governed_note.note_id)
    schema9_database.execute(
        "UPDATE notes SET owner = '  Finance  ' WHERE note_id = ?",
        (governed_note.note_id,),
    )

    result = service.reconcile(as_of=date(2026, 8, 25))

    assert result.changed == 0
    assert result.events_appended == 0
    assert _event_count_for_note(schema9_database, governed_note.note_id) == before_events


def test_overlapping_versions_create_one_stable_proposed_case(
    schema9_database: ProductDatabase, overlapping_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches interval conflicts being version-sorted or duplicated on every run."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())

    first = service.reconcile(as_of=date(2026, 8, 25))
    case_before = _only_case(schema9_database)
    second = service.reconcile(as_of=date(2026, 8, 25))

    assert first.cases_created == 1
    assert case_before["status"] == "proposed"
    assert second.cases_created == 0
    assert _only_case(schema9_database)["rule_key"] == case_before["rule_key"]


def test_equivalent_checksum_notes_auto_confirm_alias(
    schema9_database: ProductDatabase, equivalent_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches byte-identical, security-equivalent notes remaining ambiguous."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())

    result = service.reconcile(as_of=date(2026, 8, 25))

    case = _only_case(schema9_database)
    assert result.cases_created == 1
    assert case["status"] == "confirmed"
    assert case["canonical_note_id"] == min(note.note_id for note in equivalent_notes)
    alias_id = max(note.note_id for note in equivalent_notes)
    assert _projection_for(schema9_database, alias_id)["disposition"] == "duplicate_alias"


def test_normalized_equivalent_checksum_notes_still_auto_confirm(
    schema9_database: ProductDatabase, equivalent_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches raw metadata formatting splitting a semantically exact duplicate."""
    schema9_database.execute(
        "UPDATE notes SET owner = '  Finance  ' WHERE note_id = ?",
        (equivalent_notes[0].note_id,),
    )

    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )

    assert _only_case(schema9_database)["status"] == "confirmed"


def test_same_checksum_with_different_acl_is_only_proposed(
    schema9_database: ProductDatabase, acl_divergent_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches checksum equality bypassing source security equivalence."""
    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )

    case = _only_case(schema9_database)
    assert case["status"] == "proposed"
    assert case["canonical_note_id"] is None


def test_event_payload_does_not_contain_sensitive_fields(
    schema9_database: ProductDatabase, governed_note: StoredNote
) -> None:
    """Catches note content, location, ACL, or credentials leaking into append-only audit data."""
    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )

    serialized = json.dumps(schema9_database.fetch_all("SELECT * FROM governance_events"))
    for forbidden in ("body", "title", "vault_path", "acl_json", "secret", "token"):
        assert forbidden not in serialized.lower()


def test_untrusted_metadata_issue_cannot_smuggle_a_secret_into_events(
    schema9_database: ProductDatabase,
) -> None:
    """Catches syntactically valid quality metadata being used as an audit-data exfiltration path."""
    note = _insert_note(schema9_database, "malicious-quality-marker")
    schema9_database.execute(
        "UPDATE notes SET metadata_issues_json = ? WHERE note_id = ?",
        ('["secret_token_do_not_store"]', note.note_id),
    )

    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )

    serialized = json.dumps(schema9_database.fetch_all("SELECT * FROM governance_events"))
    assert "secret" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert json.loads(
        str(_projection_for(schema9_database, note.note_id)["reason_codes_json"])
    ) == ["malformed_metadata_issues_json"]


def test_acl_divergent_duplicate_cannot_be_promoted_to_confirmed_alias(
    schema9_database: ProductDatabase, acl_divergent_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches a persisted status edit bypassing mandatory duplicate security equivalence."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    case = _only_case(schema9_database)
    canonical_id = min(note.note_id for note in acl_divergent_notes)
    alias_id = max(note.note_id for note in acl_divergent_notes)
    schema9_database.execute(
        "UPDATE governance_cases SET status = 'confirmed', canonical_note_id = ? WHERE case_id = ?",
        (canonical_id, case["case_id"]),
    )
    schema9_database.execute(
        "UPDATE governance_case_notes SET participant_role = 'canonical' WHERE case_id = ? AND note_id = ?",
        (case["case_id"], canonical_id),
    )
    schema9_database.execute(
        "UPDATE governance_case_notes SET participant_role = 'alias' WHERE case_id = ? AND note_id = ?",
        (case["case_id"], alias_id),
    )

    service.reconcile(as_of=date(2026, 8, 25))

    assert _projection_for(schema9_database, alias_id)["disposition"] == "conflict_blocked"


def test_confirmed_overlap_blocks_every_case_participant(
    schema9_database: ProductDatabase, overlapping_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches confirmed conflict authority being ignored by note projection."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    case = _only_case(schema9_database)
    schema9_database.execute(
        "UPDATE governance_cases SET status = 'confirmed' WHERE case_id = ?",
        (case["case_id"],),
    )

    result = service.reconcile(as_of=date(2026, 8, 25))

    assert result.changed == 2
    assert {
        _projection_for(schema9_database, note.note_id)["disposition"]
        for note in overlapping_notes
    } == {"conflict_blocked"}
    decisions = service.confirmed_decisions([note.note_id for note in overlapping_notes])
    assert all(
        decision.disposition is GovernanceDisposition.CONFLICT_BLOCKED
        for values in decisions.values()
        for decision in values
    )


def test_case_participants_use_deterministic_roles(
    schema9_database: ProductDatabase, equivalent_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches canonical and alias identity being inferred from unstable row order."""
    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )

    participants = schema9_database.fetch_all(
        "SELECT note_id, participant_role FROM governance_case_notes ORDER BY note_id"
    )
    assert participants == [
        {"note_id": "duplicate-a", "participant_role": "canonical"},
        {"note_id": "duplicate-b", "participant_role": "alias"},
    ]


def test_rejected_unchanged_rule_key_stays_rejected(
    schema9_database: ProductDatabase, overlapping_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches deterministic discovery reopening an unchanged rejected business case."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    case = _only_case(schema9_database)
    schema9_database.execute(
        "UPDATE governance_cases SET status = 'rejected' WHERE case_id = ?",
        (case["case_id"],),
    )

    result = service.reconcile(as_of=date(2026, 8, 25))

    assert result.cases_created == 0
    assert _only_case(schema9_database)["status"] == "rejected"


def test_changed_interval_evidence_gets_a_new_rule_key(
    schema9_database: ProductDatabase, overlapping_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches materially different interval evidence being suppressed by stale case identity."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    old_case = _only_case(schema9_database)
    schema9_database.execute(
        "UPDATE governance_cases SET status = 'rejected' WHERE case_id = ?",
        (old_case["case_id"],),
    )
    schema9_database.execute(
        "UPDATE notes SET effective_from = '2026-09-01' WHERE note_id = 'travel-new'"
    )

    result = service.reconcile(as_of=date(2026, 8, 25))

    cases = schema9_database.fetch_all(
        "SELECT rule_key, status FROM governance_cases ORDER BY created_at, rule_key"
    )
    rule_keys = {str(case["rule_key"]) for case in cases}
    assert result.cases_created == 1
    assert len(cases) == 2
    assert len(rule_keys) == 2
    assert str(old_case["rule_key"]) in rule_keys
    assert {str(case["status"]) for case in cases} == {"proposed", "rejected"}


def test_malformed_metadata_fails_closed(schema9_database: ProductDatabase) -> None:
    """Catches damaged persisted quality metadata being treated as complete governance."""
    note = _insert_note(schema9_database, "damaged-metadata")
    schema9_database.execute(
        "UPDATE notes SET metadata_issues_json = ? WHERE note_id = ?",
        ('{"not":"a-list"}', note.note_id),
    )

    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(
        as_of=date(2026, 8, 25)
    )

    projection = _projection_for(schema9_database, note.note_id)
    assert projection["disposition"] == "unresolved"
    assert json.loads(str(projection["reason_codes_json"])) == [
        "malformed_metadata_issues_json"
    ]


def test_evaluate_notes_uses_confirmed_decisions(
    schema9_database: ProductDatabase, equivalent_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches the public pure-evaluation boundary dropping persisted confirmed authority."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    alias_id = max(note.note_id for note in equivalent_notes)
    row = schema9_database.fetch_one("SELECT * FROM notes WHERE note_id = ?", (alias_id,))
    assert row is not None

    result = service.evaluate_notes(
        [row], as_of=date(2026, 8, 25), mode=GovernanceMode.CURRENT
    )

    assert result[alias_id].disposition is GovernanceDisposition.DUPLICATE_ALIAS


def test_reconcile_in_transaction_keeps_caller_transaction_open(
    schema9_database: ProductDatabase, governed_note: StoredNote
) -> None:
    """Catches a helper nesting or prematurely committing its caller-owned transaction."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    with schema9_database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        result = service.reconcile_in_transaction(
            connection,
            note_ids=[governed_note.note_id],
            as_of=date(2026, 8, 25),
        )

        assert result.changed == 1
        assert connection.in_transaction
        connection.rollback()
    assert schema9_database.fetch_all("SELECT * FROM governance_note_state") == []


def test_event_failure_rolls_back_projection_case_event_and_index_status(
    schema9_database: ProductDatabase, overlapping_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches partial governance state surviving a failed append-only audit insert."""
    schema9_database.execute(
        """
        CREATE TRIGGER reject_state_change_event
        BEFORE INSERT ON governance_events
        WHEN NEW.action = 'state_changed'
        BEGIN
          SELECT RAISE(ABORT, 'test event rejection');
        END
        """
    )
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())

    with pytest.raises(Exception, match="test event rejection"):
        service.reconcile(as_of=date(2026, 8, 25))

    assert schema9_database.fetch_all("SELECT * FROM governance_note_state") == []
    assert schema9_database.fetch_all("SELECT * FROM governance_cases") == []
    assert schema9_database.fetch_all("SELECT * FROM governance_case_notes") == []
    assert schema9_database.fetch_all("SELECT * FROM governance_events") == []
    assert {
        row["index_status"]
        for row in schema9_database.fetch_all("SELECT index_status FROM notes")
    } == {"indexed"}


def test_corrupt_projection_aborts_without_partial_writes(
    schema9_database: ProductDatabase, governed_note: StoredNote
) -> None:
    """Catches damaged persisted projection authority being silently overwritten as trusted state."""
    schema9_database.execute(
        """
        INSERT INTO governance_note_state (
            note_id, evaluated_on, lifecycle_state, disposition, reason_codes_json,
            decision_fingerprint, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            governed_note.note_id,
            "2026-08-24",
            "current",
            "eligible",
            "not-json",
            "0" * 64,
            "2026-08-24T00:00:00Z",
        ),
    )

    with pytest.raises(GovernancePersistenceError):
        GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(
            as_of=date(2026, 8, 25)
        )

    assert schema9_database.fetch_all("SELECT * FROM governance_events") == []
    assert _projection_for(schema9_database, governed_note.note_id)["reason_codes_json"] == "not-json"


def test_corrupt_confirmed_rule_key_fails_closed(
    schema9_database: ProductDatabase, equivalent_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches damaged confirmed-case identity silently dropping an authoritative restriction."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    case = _only_case(schema9_database)
    schema9_database.execute(
        "UPDATE governance_cases SET rule_key = ? WHERE case_id = ?",
        ("0" * 64, case["case_id"]),
    )
    alias_id = max(note.note_id for note in equivalent_notes)

    with pytest.raises(GovernancePersistenceError, match="case identity"):
        service.reconcile(as_of=date(2026, 8, 25))

    assert _projection_for(schema9_database, alias_id)["disposition"] == "duplicate_alias"
    assert len(schema9_database.fetch_all("SELECT * FROM governance_cases")) == 1


def test_corrupt_proposed_case_participants_fail_closed(
    schema9_database: ProductDatabase, overlapping_notes: tuple[StoredNote, StoredNote]
) -> None:
    """Catches damaged case linkage silently suppressing deterministic rediscovery."""
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    case = _only_case(schema9_database)
    with schema9_database.connect() as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "DELETE FROM governance_case_notes WHERE case_id = ? AND note_id = ?",
            (case["case_id"], overlapping_notes[0].note_id),
        )

    with pytest.raises(GovernancePersistenceError, match="case state"):
        service.reconcile(as_of=date(2026, 8, 25))

    participants = schema9_database.fetch_all(
        "SELECT note_id FROM governance_case_notes WHERE case_id = ?",
        (case["case_id"],),
    )
    assert participants == [{"note_id": overlapping_notes[1].note_id}]
