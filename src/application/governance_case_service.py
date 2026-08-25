"""ACL-protected human governance decisions and immutable audit reads."""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from datetime import UTC, date, datetime
import json
import math
import re
import sqlite3
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from application.access_control import (
    GovernanceAuthorizationError,
    note_acl_matches,
    require_governance_write_role,
)
from application.governance_policy import canonical_json
from application.governance_reconciliation_service import (
    GOVERNANCE_REASON_CODES,
    GovernancePersistenceError,
    GovernanceReconciliationService,
)
from domain.errors import GovernanceConflictError
from domain.governance import (
    GovernanceCapabilities,
    GovernanceCaseView,
    GovernanceEventView,
    GovernanceParticipantView,
)
from infrastructure.database import ProductDatabase

_CASE_STATUSES = frozenset({"proposed", "confirmed", "rejected", "revoked"})
_EVENT_ACTIONS = frozenset({"state_changed", "proposed", "confirmed", "rejected", "revoked"})
_EVENT_SOURCES = frozenset({"ingestion_rule", "lifecycle_rule", "human_review"})
_SAFE_EVENT_STATE_KEYS = frozenset(
    {
        "canonical_note_id",
        "case_type",
        "decision_fingerprint",
        "disposition",
        "eligible",
        "lifecycle_state",
        "reason_code",
        "relevant_metadata_hash",
        "score",
        "status",
    }
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")


class GovernanceCaseService:
    """Expose safe governance views and one atomic human-decision boundary."""

    def __init__(
        self,
        database: ProductDatabase,
        reconciliation: GovernanceReconciliationService,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.database = database
        self.reconciliation = reconciliation
        self._today = today

    def list_cases(
        self,
        *,
        access_scope: dict[str, Any] | None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[GovernanceCaseView]:
        _validate_limit(limit)
        if status is not None and status not in _CASE_STATUSES:
            raise ValueError("status is invalid")
        sql = "SELECT * FROM governance_cases"
        params: tuple[Any, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY updated_at DESC, case_id"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            result: list[GovernanceCaseView] = []
            for row in rows:
                view = self._visible_case_view(connection, row, access_scope)
                if view is not None:
                    result.append(view)
                    if len(result) == limit:
                        break
            return result

    def get_case(
        self,
        case_id: str,
        *,
        access_scope: dict[str, Any] | None,
    ) -> GovernanceCaseView | None:
        if not isinstance(case_id, str) or not case_id:
            return None
        with self.database.connect() as connection:
            return self._get_visible_case(connection, case_id, access_scope)

    def list_events(
        self,
        *,
        access_scope: dict[str, Any] | None,
        case_id: str | None = None,
        limit: int = 200,
    ) -> list[GovernanceEventView]:
        _validate_limit(limit)
        sql = "SELECT * FROM governance_events"
        params: tuple[Any, ...] = ()
        if case_id is not None:
            sql += " WHERE case_id = ?"
            params = (case_id,)
        sql += " ORDER BY created_at DESC, event_id DESC"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            visible_case_cache: dict[str, GovernanceCaseView | None] = {}
            result: list[GovernanceEventView] = []
            for row in rows:
                if not self._event_is_visible(
                    connection,
                    row,
                    access_scope,
                    visible_case_cache,
                ):
                    continue
                result.append(_event_view(row))
                if len(result) == limit:
                    break
            return result

    def resolve(
        self,
        case_id: str,
        *,
        expected_status: str,
        decision: str,
        canonical_note_id: str | None,
        actor: str,
        roles: Collection[str],
        access_scope: dict[str, Any] | None,
        request_id: str,
    ) -> GovernanceCaseView:
        return self._decide(
            case_id,
            expected_status=expected_status,
            decision=decision,
            canonical_note_id=canonical_note_id,
            actor=actor,
            roles=roles,
            access_scope=access_scope,
            request_id=request_id,
        )

    def revoke(
        self,
        case_id: str,
        *,
        expected_status: str,
        actor: str,
        roles: Collection[str],
        access_scope: dict[str, Any] | None,
        request_id: str,
    ) -> GovernanceCaseView:
        return self._decide(
            case_id,
            expected_status=expected_status,
            decision="revoke",
            canonical_note_id=None,
            actor=actor,
            roles=roles,
            access_scope=access_scope,
            request_id=request_id,
        )

    def _decide(
        self,
        case_id: str,
        *,
        expected_status: str,
        decision: str,
        canonical_note_id: str | None,
        actor: str,
        roles: Collection[str],
        access_scope: dict[str, Any] | None,
        request_id: str,
    ) -> GovernanceCaseView:
        _validate_actor_and_request(actor, request_id)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._get_visible_case(connection, case_id, access_scope)
                if current is None:
                    raise GovernanceConflictError("governance case is unavailable")

                # Visibility is deliberately resolved before governance authority.
                require_governance_write_role(roles)
                new_status = _validated_transition(expected_status, decision)
                participant_ids = tuple(item.note_id for item in current.participants)
                if canonical_note_id is not None and canonical_note_id not in participant_ids:
                    raise ValueError("canonical_note_id must be a case participant")
                self._validate_canonical_choice(
                    current,
                    decision=decision,
                    canonical_note_id=canonical_note_id,
                )

                now = _utc_now()
                cursor = connection.execute(
                    """
                    UPDATE governance_cases
                    SET status = ?, canonical_note_id = ?, resolved_at = ?,
                        resolved_by = ?, request_id = ?, updated_at = ?
                    WHERE case_id = ? AND status = ?
                    """,
                    (
                        new_status,
                        current.canonical_note_id if decision == "revoke" else canonical_note_id,
                        now,
                        actor,
                        request_id,
                        now,
                        case_id,
                        expected_status,
                    ),
                )
                if cursor.rowcount != 1:
                    raise GovernanceConflictError("governance case status changed")

                if decision == "confirm" and current.case_type == "exact_duplicate":
                    connection.execute(
                        """
                        UPDATE governance_case_notes
                        SET participant_role = CASE WHEN note_id = ? THEN 'canonical' ELSE 'alias' END
                        WHERE case_id = ?
                        """,
                        (canonical_note_id, case_id),
                    )

                connection.execute(
                    """
                    INSERT INTO governance_events (
                        event_id, case_id, note_id, policy_key, actor, action,
                        previous_state_json, new_state_json, reason_code,
                        evidence_ids_json, source, request_id, created_at
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'human_review', ?, ?)
                    """,
                    (
                        str(uuid4()),
                        case_id,
                        current.policy_key,
                        actor,
                        new_status,
                        canonical_json(
                            {
                                "status": current.status,
                                "canonical_note_id": current.canonical_note_id,
                            }
                        ),
                        canonical_json(
                            {
                                "status": new_status,
                                "canonical_note_id": (
                                    current.canonical_note_id
                                    if decision == "revoke"
                                    else canonical_note_id
                                ),
                            }
                        ),
                        current.reason_code,
                        canonical_json(list(participant_ids)),
                        request_id,
                        now,
                    ),
                )

                placeholders = ",".join("?" for _ in participant_ids)
                connection.execute(
                    f"UPDATE notes SET index_status = 'pending' "
                    f"WHERE note_id IN ({placeholders})",
                    participant_ids,
                )
                self.reconciliation.reconcile_in_transaction(
                    connection,
                    note_ids=participant_ids,
                    as_of=self._today(),
                )
                result = self._get_visible_case(connection, case_id, access_scope)
                if result is None:
                    raise GovernancePersistenceError("persisted governance case became unavailable")
                connection.commit()
                return result
            except (GovernanceAuthorizationError, GovernanceConflictError, ValueError):
                connection.rollback()
                raise
            except GovernancePersistenceError:
                connection.rollback()
                raise
            except sqlite3.Error as exc:
                connection.rollback()
                raise GovernancePersistenceError("governance decision could not be persisted") from exc
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _validate_canonical_choice(
        current: GovernanceCaseView,
        *,
        decision: str,
        canonical_note_id: str | None,
    ) -> None:
        if decision != "confirm" and canonical_note_id is not None:
            raise ValueError("canonical_note_id is valid only for confirmation")
        if decision == "confirm" and current.case_type == "exact_duplicate":
            if canonical_note_id is None:
                raise ValueError("exact_duplicate confirmation requires a canonical participant")
        elif canonical_note_id is not None:
            raise ValueError("this case type does not accept a canonical participant")

    def _get_visible_case(
        self,
        connection: sqlite3.Connection,
        case_id: str,
        access_scope: dict[str, Any] | None,
    ) -> GovernanceCaseView | None:
        row = connection.execute(
            "SELECT * FROM governance_cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return self._visible_case_view(connection, row, access_scope) if row is not None else None

    def _visible_case_view(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        access_scope: dict[str, Any] | None,
    ) -> GovernanceCaseView | None:
        participant_rows = connection.execute(
            """
            SELECT links.note_id, links.participant_role,
                   notes.document_version, notes.effective_from, notes.effective_to,
                   notes.workspace, notes.department, notes.acl_json, notes.acl_public
            FROM governance_case_notes AS links
            JOIN notes ON notes.note_id = links.note_id
            WHERE links.case_id = ?
            ORDER BY links.note_id
            """,
            (row["case_id"],),
        ).fetchall()
        if not participant_rows or any(
            not note_acl_matches(dict(participant), access_scope)
            for participant in participant_rows
        ):
            return None
        status = str(row["status"])
        reason_code = str(row["reason_code"])
        if status not in _CASE_STATUSES or reason_code not in GOVERNANCE_REASON_CODES:
            raise GovernancePersistenceError("persisted governance case view is malformed")
        participants = tuple(
            GovernanceParticipantView(
                note_id=str(participant["note_id"]),
                participant_role=str(participant["participant_role"]),
                document_version=_optional_str(participant["document_version"]),
                effective_from=_optional_str(participant["effective_from"]),
                effective_to=_optional_str(participant["effective_to"]),
            )
            for participant in participant_rows
        )
        return GovernanceCaseView(
            case_id=str(row["case_id"]),
            case_type=str(row["case_type"]),
            policy_key=_optional_str(row["policy_key"]),
            status=status,
            canonical_note_id=_optional_str(row["canonical_note_id"]),
            reason_code=reason_code,
            evidence_ids=tuple(participant.note_id for participant in participants),
            participants=participants,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            resolved_at=_optional_str(row["resolved_at"]),
            capabilities=GovernanceCapabilities(
                can_resolve=status == "proposed",
                can_revoke=status in {"confirmed", "rejected"},
            ),
        )

    def _event_is_visible(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        access_scope: dict[str, Any] | None,
        case_cache: dict[str, GovernanceCaseView | None],
    ) -> bool:
        case_id = _optional_str(row["case_id"])
        if case_id is not None:
            if case_id not in case_cache:
                case_cache[case_id] = self._get_visible_case(
                    connection, case_id, access_scope
                )
            if case_cache[case_id] is None:
                return False
        note_id = _optional_str(row["note_id"])
        if note_id is not None:
            note = connection.execute(
                """
                SELECT workspace, department, acl_json, acl_public
                FROM notes WHERE note_id = ?
                """,
                (note_id,),
            ).fetchone()
            if note is None or not note_acl_matches(dict(note), access_scope):
                return False
        return True


def _validated_transition(expected_status: str, decision: str) -> str:
    if expected_status not in _CASE_STATUSES:
        raise ValueError("expected_status is invalid")
    if decision in {"confirm", "reject"}:
        if expected_status != "proposed":
            raise ValueError("resolve requires expected_status proposed")
        return "confirmed" if decision == "confirm" else "rejected"
    if decision == "revoke":
        if expected_status not in {"confirmed", "rejected"}:
            raise ValueError("revoke requires a resolved expected_status")
        return "revoked"
    raise ValueError("decision is invalid")


def _event_view(row: sqlite3.Row) -> GovernanceEventView:
    action = str(row["action"])
    source = str(row["source"])
    reason_code = str(row["reason_code"])
    if (
        action not in _EVENT_ACTIONS
        or source not in _EVENT_SOURCES
        or reason_code not in GOVERNANCE_REASON_CODES
    ):
        raise GovernancePersistenceError("persisted governance event view is malformed")
    return GovernanceEventView(
        event_id=str(row["event_id"]),
        case_id=_optional_str(row["case_id"]),
        note_id=_optional_str(row["note_id"]),
        policy_key=_optional_str(row["policy_key"]),
        actor=str(row["actor"]),
        action=action,
        previous_state=_safe_event_state(row["previous_state_json"]),
        new_state=_safe_event_state(row["new_state_json"]),
        reason_code=reason_code,
        evidence_ids=_safe_evidence_ids(row["evidence_ids_json"]),
        source=source,
        request_id=_optional_str(row["request_id"]),
        created_at=str(row["created_at"]),
    )


def _safe_event_state(value: Any) -> Mapping[str, str | int | float | bool | None]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GovernancePersistenceError("persisted governance event state is malformed") from exc
    if not isinstance(parsed, dict):
        raise GovernancePersistenceError("persisted governance event state is malformed")
    safe: dict[str, str | int | float | bool | None] = {}
    for key, item in parsed.items():
        if key not in _SAFE_EVENT_STATE_KEYS:
            continue
        if item is None or isinstance(item, (str, bool, int)) or (isinstance(item, float) and math.isfinite(item)):
            safe[key] = item
    return MappingProxyType(safe)


def _safe_evidence_ids(value: Any) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GovernancePersistenceError("persisted governance evidence ids are malformed") from exc
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) and _SAFE_ID.fullmatch(item) for item in parsed
    ):
        raise GovernancePersistenceError("persisted governance evidence ids are malformed")
    return tuple(parsed)


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")


def _validate_actor_and_request(actor: str, request_id: str) -> None:
    if not isinstance(actor, str) or not _SAFE_ID.fullmatch(actor):
        raise ValueError("actor must be a safe principal identifier")
    if not isinstance(request_id, str) or not _SAFE_ID.fullmatch(request_id):
        raise ValueError("request_id must be a safe request identifier")


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
