"""Read-only, request-scoped governance authority for retrieval."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from contextlib import closing
from datetime import date
import json
import sqlite3
from typing import Any

from application.governance_case_identity import (
    discover_governance_case_candidates,
)
from application.governance_policy import GovernancePolicy
from application.governance_reconciliation_service import (
    GOVERNANCE_REASON_CODES,
    GovernancePersistenceError,
)
from domain.governance import (
    ConfirmedGovernanceDecision,
    GovernanceAuthoritySnapshot,
    GovernanceDisposition,
    GovernanceMode,
    GovernanceNote,
)
from infrastructure.database import ProductDatabase


class GovernanceRetrievalAuthority:
    """Load authoritative notes and persisted decisions without case discovery."""

    def __init__(self, database: ProductDatabase) -> None:
        self.database = database
        self.policy = GovernancePolicy()

    def load(
        self,
        note_ids: Collection[str],
        *,
        as_of: date,
        mode: GovernanceMode,
    ) -> GovernanceAuthoritySnapshot:
        selected = self._validated_ids(note_ids)
        if not selected:
            return GovernanceAuthoritySnapshot({}, {}, {})
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN")
            try:
                notes = self._load_notes(connection, selected)
                self._validate_projections(connection, selected, as_of=as_of, mode=mode)
                decisions, blocked = self._load_cases(connection, selected)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return GovernanceAuthoritySnapshot(notes, decisions, blocked)

    @staticmethod
    def _validated_ids(note_ids: Collection[str]) -> tuple[str, ...]:
        selected: set[str] = set()
        for note_id in note_ids:
            if not isinstance(note_id, str) or not note_id.strip():
                raise GovernancePersistenceError("retrieval authority note IDs are malformed")
            selected.add(note_id)
        return tuple(sorted(selected))

    def _load_notes(
        self,
        connection: sqlite3.Connection,
        note_ids: tuple[str, ...],
    ) -> dict[str, GovernanceNote]:
        placeholders = ",".join("?" for _ in note_ids)
        rows = connection.execute(
            f"""
            SELECT note_id, source_id, owner, policy_key, document_version,
                   effective_from, effective_to, policy_status, metadata_issues_json,
                   workspace, department, acl_json, acl_public, content_hash
            FROM notes
            WHERE note_id IN ({placeholders})
            ORDER BY note_id
            """,
            note_ids,
        ).fetchall()
        notes = {str(row["note_id"]): self._note_from_row(dict(row)) for row in rows}
        if set(notes) != set(note_ids):
            raise GovernancePersistenceError("retrieval authority is missing a requested note")
        return notes

    @staticmethod
    def _note_from_row(row: Mapping[str, Any]) -> GovernanceNote:
        try:
            issues = json.loads(row["metadata_issues_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise GovernancePersistenceError(
                "retrieval authority metadata issues are malformed"
            ) from exc
        if not isinstance(issues, list) or not all(
            isinstance(issue, str) and issue for issue in issues
        ):
            raise GovernancePersistenceError(
                "retrieval authority metadata issues are malformed"
            )
        note_id = row.get("note_id")
        source_id = row.get("source_id")
        content_hash = row.get("content_hash")
        acl_json = row.get("acl_json")
        acl_public = row.get("acl_public")
        if (
            not isinstance(note_id, str)
            or not note_id
            or not isinstance(source_id, str)
            or not source_id
            or not isinstance(content_hash, str)
            or not content_hash
            or not isinstance(acl_json, str)
            or acl_public not in (0, 1, False, True)
        ):
            raise GovernancePersistenceError("retrieval authority note is malformed")
        return GovernanceNote(
            note_id=note_id,
            source_id=source_id,
            owner=_optional_text(row.get("owner")),
            policy_key=_optional_text(row.get("policy_key")),
            document_version=_optional_text(row.get("document_version")),
            effective_from=_optional_text(row.get("effective_from")),
            effective_to=_optional_text(row.get("effective_to")),
            policy_status=row.get("policy_status"),
            metadata_issues=tuple(issues),
            workspace=_optional_text(row.get("workspace")),
            department=_optional_text(row.get("department")),
            acl_json=acl_json,
            acl_public=bool(acl_public),
            content_hash=content_hash,
        )

    @staticmethod
    def _validate_projections(
        connection: sqlite3.Connection,
        note_ids: tuple[str, ...],
        *,
        as_of: date,
        mode: GovernanceMode,
    ) -> None:
        placeholders = ",".join("?" for _ in note_ids)
        rows = connection.execute(
            f"""
            SELECT note_id, evaluated_on, lifecycle_state, disposition,
                   reason_codes_json, decision_fingerprint
            FROM governance_note_state
            WHERE note_id IN ({placeholders})
            """,
            note_ids,
        ).fetchall()
        by_id = {str(row["note_id"]): row for row in rows}
        if set(by_id) != set(note_ids):
            raise GovernancePersistenceError("retrieval governance projection is incomplete")
        for row in by_id.values():
            try:
                reasons = json.loads(row["reason_codes_json"])
                date.fromisoformat(str(row["evaluated_on"]))
            except (TypeError, ValueError) as exc:
                raise GovernancePersistenceError(
                    "retrieval governance projection is malformed"
                ) from exc
            if (
                not isinstance(reasons, list)
                or not all(isinstance(reason, str) and reason for reason in reasons)
                or not all(reason in GOVERNANCE_REASON_CODES for reason in reasons)
                or row["lifecycle_state"] not in {
                    "not_yet_effective",
                    "current",
                    "expired",
                    "historical",
                    "unresolved",
                }
                or row["disposition"] not in {
                    "eligible",
                    "excluded",
                    "unresolved",
                    "conflict_blocked",
                    "duplicate_alias",
                }
                or not _is_sha256(row["decision_fingerprint"])
            ):
                raise GovernancePersistenceError(
                    "retrieval governance projection is malformed"
                )
            if mode not in {GovernanceMode.CURRENT, GovernanceMode.HISTORICAL}:
                raise GovernancePersistenceError("retrieval governance mode is malformed")
            if mode is GovernanceMode.CURRENT and row["evaluated_on"] != as_of.isoformat():
                raise GovernancePersistenceError("retrieval governance projection is stale")

    def _load_cases(
        self,
        connection: sqlite3.Connection,
        note_ids: tuple[str, ...],
    ) -> tuple[
        dict[str, tuple[ConfirmedGovernanceDecision, ...]],
        dict[str, tuple[str, ...]],
    ]:
        placeholders = ",".join("?" for _ in note_ids)
        case_rows = connection.execute(
            f"""
            SELECT DISTINCT cases.case_id, cases.case_type, cases.status,
                            cases.canonical_note_id, cases.reason_code,
                            cases.rule_key, cases.evidence_json, cases.policy_key
            FROM governance_cases AS cases
            JOIN governance_case_notes AS linked ON linked.case_id = cases.case_id
            WHERE linked.note_id IN ({placeholders})
              AND cases.status IN ('proposed', 'confirmed')
            ORDER BY cases.case_id
            """,
            note_ids,
        ).fetchall()
        decisions: dict[str, list[ConfirmedGovernanceDecision]] = defaultdict(list)
        blocked: dict[str, set[str]] = defaultdict(set)
        selected_set = set(note_ids)
        for case_row in case_rows:
            case_id = str(case_row["case_id"])
            links = connection.execute(
                """
                SELECT note_id, participant_role
                FROM governance_case_notes
                WHERE case_id = ?
                ORDER BY note_id
                """,
                (case_id,),
            ).fetchall()
            roles = {str(link["note_id"]): str(link["participant_role"]) for link in links}
            self._validate_case_linkage(dict(case_row), roles)
            linked_notes = self._load_notes(connection, tuple(sorted(roles)))
            expected_reason = self._validate_case_semantics(
                dict(case_row),
                roles,
                linked_notes,
            )
            applicable = selected_set.intersection(roles)
            reason = expected_reason
            case_type = str(case_row["case_type"])
            status = str(case_row["status"])
            if case_type == "version_conflict":
                for note_id in applicable:
                    blocked[note_id].add(reason)
                    if status == "confirmed":
                        decisions[note_id].append(
                            ConfirmedGovernanceDecision(
                                note_id,
                                GovernanceDisposition.CONFLICT_BLOCKED,
                                reason,
                                decision_id=case_id,
                            )
                        )
            elif status == "proposed":
                for note_id in applicable:
                    blocked[note_id].add(reason)
            elif status == "confirmed":
                canonical = str(case_row["canonical_note_id"])
                for note_id in applicable:
                    if note_id != canonical:
                        decisions[note_id].append(
                            ConfirmedGovernanceDecision(
                                note_id,
                                GovernanceDisposition.DUPLICATE_ALIAS,
                                reason,
                                canonical,
                                case_id,
                            )
                        )
        return (
            {
                note_id: tuple(sorted(values, key=lambda item: item.decision_id or ""))
                for note_id, values in decisions.items()
            },
            {note_id: tuple(sorted(reasons)) for note_id, reasons in blocked.items()},
        )

    @staticmethod
    def _validate_case_linkage(row: Mapping[str, Any], roles: Mapping[str, str]) -> None:
        case_type = row.get("case_type")
        status = row.get("status")
        reason = row.get("reason_code")
        case_id = row.get("case_id")
        rule_key = row.get("rule_key")
        try:
            evidence = json.loads(row.get("evidence_json"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise GovernancePersistenceError("retrieval governance case is malformed") from exc
        participant_ids = evidence.get("participant_note_ids") if isinstance(evidence, dict) else None
        structurally_valid = (
            isinstance(case_id, str)
            and _is_sha256(rule_key)
            and case_id == f"case-{rule_key}"
            and case_type in {"version_conflict", "exact_duplicate"}
            and status in {"proposed", "confirmed"}
            and isinstance(reason, str)
            and reason in GOVERNANCE_REASON_CODES
            and isinstance(participant_ids, list)
            and participant_ids == sorted(set(participant_ids))
            and set(participant_ids) == set(roles)
            and _is_sha256(
                evidence.get("relevant_metadata_hash") if isinstance(evidence, dict) else None
            )
        )
        if case_type == "version_conflict":
            structurally_valid = structurally_valid and all(
                role == "candidate" for role in roles.values()
            )
        elif status == "confirmed":
            canonical = row.get("canonical_note_id")
            structurally_valid = structurally_valid and (
                isinstance(canonical, str)
                and roles.get(canonical) == "canonical"
                and all(
                    role == ("canonical" if note_id == canonical else "alias")
                    for note_id, role in roles.items()
                )
            )
        else:
            structurally_valid = structurally_valid and (
                row.get("canonical_note_id") is None
                and all(role == "candidate" for role in roles.values())
            )
        if not structurally_valid:
            raise GovernancePersistenceError("retrieval governance case is malformed")

    def _validate_case_semantics(
        self,
        row: Mapping[str, Any],
        roles: Mapping[str, str],
        notes: Mapping[str, GovernanceNote],
    ) -> str:
        case_type = str(row["case_type"])
        status = str(row["status"])
        participant_ids = tuple(sorted(notes))
        if set(notes) != set(roles) or len(participant_ids) < 2:
            raise GovernancePersistenceError("retrieval governance case is stale")
        candidates = tuple(
            candidate
            for candidate in discover_governance_case_candidates(
                self.policy,
                tuple(notes[note_id] for note_id in participant_ids),
            )
            if candidate.case_type == case_type
            and candidate.participant_note_ids == participant_ids
        )
        if len(candidates) != 1:
            raise GovernancePersistenceError("retrieval governance case is stale")
        candidate = candidates[0]
        if case_type == "exact_duplicate":
            canonical = row.get("canonical_note_id")
            if status == "confirmed":
                valid_semantics = (
                    candidate.status == "confirmed"
                    and canonical == candidate.canonical_note_id
                    and roles.get(candidate.canonical_note_id) == "canonical"
                    and all(
                        role == (
                            "canonical"
                            if note_id == candidate.canonical_note_id
                            else "alias"
                        )
                        for note_id, role in roles.items()
                    )
                )
            else:
                valid_semantics = (
                    candidate.status == "proposed"
                    and canonical is None
                    and all(role == "candidate" for role in roles.values())
                )
        else:
            valid_semantics = (
                row.get("canonical_note_id") is None
                and all(role == "candidate" for role in roles.values())
            )
        evidence = json.loads(str(row["evidence_json"]))
        if (
            not valid_semantics
            or row.get("policy_key") != candidate.policy_key
            or row.get("reason_code") != candidate.reason_code
            or row.get("rule_key") != candidate.rule_key
            or row.get("case_id") != f"case-{candidate.rule_key}"
            or evidence.get("relevant_metadata_hash")
            != candidate.relevant_metadata_hash
        ):
            raise GovernancePersistenceError("retrieval governance case is stale")
        return candidate.reason_code


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GovernancePersistenceError("retrieval authority note is malformed")
    text = value.strip()
    return text or None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )
