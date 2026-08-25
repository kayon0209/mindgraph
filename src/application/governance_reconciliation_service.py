"""Transactional projection and deterministic case discovery for governed notes."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import sqlite3
from typing import Any
from uuid import uuid4

from application.governance_policy import (
    GovernancePolicy,
    canonical_json,
    governance_metadata_dict,
)
from domain.governance import (
    ConfirmedGovernanceDecision,
    GovernanceDisposition,
    GovernanceEvaluation,
    GovernanceMode,
    GovernanceNote,
    LifecycleState,
    ReconciliationResult,
)
from infrastructure.database import ProductDatabase

GOVERNANCE_REASON_CODES = frozenset(
    {
        "checksum_match_requires_review",
        "conflicting_confirmed_decisions",
        "corrupt_confirmed_governance_case",
        "declared_archived",
        "declared_draft",
        "declared_expired",
        "declared_superseded",
        "declared_unspecified",
        "effective_period_ended",
        "eligible_current_version",
        "eligible_historical_version",
        "exact_duplicate_equivalent",
        "governance_state_changed",
        "historical_status_without_effective_to",
        "invalid_acl_public",
        "invalid_confirmed_decision",
        "invalid_effective_date",
        "invalid_effective_range",
        "invalid_policy_status",
        "malformed_acl_json",
        "malformed_metadata_issues_json",
        "missing_content_hash",
        "missing_document_version",
        "missing_effective_from",
        "missing_owner",
        "missing_policy_key",
        "missing_policy_status",
        "missing_source_id",
        "missing_version",
        "not_yet_effective",
        "overlapping_effective_intervals",
    }
)


class GovernancePersistenceError(RuntimeError):
    """Raised when persisted governance authority cannot be trusted."""


@dataclass(frozen=True, slots=True)
class _CaseCandidate:
    case_type: str
    policy_key: str
    participant_note_ids: tuple[str, ...]
    status: str
    canonical_note_id: str | None
    reason_code: str
    rule_key: str
    relevant_metadata_hash: str


@dataclass(frozen=True, slots=True)
class _StoredState:
    evaluated_on: str
    lifecycle_state: str
    disposition: str
    reason_codes: tuple[str, ...]
    decision_fingerprint: str

    @property
    def eligible(self) -> bool:
        return self.disposition == GovernanceDisposition.ELIGIBLE.value

    def event_dict(self) -> dict[str, Any]:
        return {
            "lifecycle_state": self.lifecycle_state,
            "disposition": self.disposition,
            "reason_codes": list(self.reason_codes),
        }


class GovernanceReconciliationService:
    def __init__(self, database: ProductDatabase, policy: GovernancePolicy) -> None:
        self.database = database
        self.policy = policy

    def reconcile(
        self,
        *,
        note_ids: Collection[str] | None = None,
        as_of: date | None = None,
    ) -> ReconciliationResult:
        evaluated_on = as_of if as_of is not None else date.today()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                selected = (
                    self._all_note_ids(connection)
                    if note_ids is None
                    else self._validated_note_ids(note_ids)
                )
                result = self.reconcile_in_transaction(
                    connection,
                    note_ids=selected,
                    as_of=evaluated_on,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def reconcile_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        note_ids: Collection[str],
        as_of: date,
    ) -> ReconciliationResult:
        if not connection.in_transaction:
            raise ValueError("reconcile_in_transaction requires an active transaction")
        if not isinstance(as_of, date):
            raise TypeError("as_of must be an explicit date")

        requested_ids = self._validated_note_ids(note_ids)
        if not requested_ids:
            return ReconciliationResult(0, 0, 0, 0, 0, as_of)

        all_notes = self._load_notes(connection)
        notes_by_id = {note.note_id: note for note in all_notes}
        target_ids = {note_id for note_id in requested_ids if note_id in notes_by_id}
        if not target_ids:
            return ReconciliationResult(0, 0, 0, 0, 0, as_of)

        candidates = self._discover_cases(all_notes)
        relevant_candidates = tuple(
            candidate
            for candidate in candidates
            if target_ids.intersection(candidate.participant_note_ids)
        )
        affected_ids = set(target_ids)
        for candidate in relevant_candidates:
            affected_ids.update(candidate.participant_note_ids)
        affected_ids.update(self._confirmed_case_participants(connection, affected_ids))

        now = _utc_now()
        cases_created = 0
        events_appended = 0
        for candidate in relevant_candidates:
            created = self._insert_case(connection, candidate, now=now)
            if created:
                cases_created += 1
                self._append_case_event(connection, candidate, now=now)
                events_appended += 1

        decisions = self._confirmed_decisions_in_connection(
            connection,
            affected_ids,
            notes_by_id=notes_by_id,
            current_candidates={(item.case_type, item.rule_key): item for item in candidates},
        )
        notes = tuple(notes_by_id[note_id] for note_id in sorted(affected_ids) if note_id in notes_by_id)
        evaluations = {
            note.note_id: self.policy.evaluate(
                note,
                as_of=as_of,
                mode=GovernanceMode.CURRENT,
                confirmed_decisions=decisions.get(note.note_id, ()),
            )
            for note in notes
        }

        changed = 0
        pending = 0
        for note in notes:
            evaluation = evaluations[note.note_id]
            did_change, did_mark_pending, did_append_event = self._project_note(
                connection,
                note,
                evaluation,
                decisions.get(note.note_id, ()),
                as_of=as_of,
                now=now,
            )
            changed += int(did_change)
            pending += int(did_mark_pending)
            events_appended += int(did_append_event)

        return ReconciliationResult(
            len(notes),
            changed,
            pending,
            cases_created,
            events_appended,
            as_of,
        )

    def evaluate_notes(
        self,
        notes: Sequence[Mapping[str, Any]],
        *,
        as_of: date,
        mode: GovernanceMode,
    ) -> dict[str, GovernanceEvaluation]:
        converted = tuple(self._note_from_mapping(note) for note in notes)
        decisions = self.confirmed_decisions(tuple(note.note_id for note in converted))
        return {
            note.note_id: self.policy.evaluate(
                note,
                as_of=as_of,
                mode=mode,
                confirmed_decisions=decisions.get(note.note_id, ()),
            )
            for note in converted
        }

    def confirmed_decisions(
        self, note_ids: Collection[str]
    ) -> dict[str, tuple[ConfirmedGovernanceDecision, ...]]:
        selected = self._validated_note_ids(note_ids)
        if not selected:
            return {}
        with self.database.connect() as connection:
            notes = self._load_notes(connection)
            candidates = self._discover_cases(notes)
            return self._confirmed_decisions_in_connection(
                connection,
                set(selected),
                notes_by_id={note.note_id: note for note in notes},
                current_candidates={(item.case_type, item.rule_key): item for item in candidates},
            )

    @staticmethod
    def _validated_note_ids(note_ids: Collection[str]) -> tuple[str, ...]:
        normalized: set[str] = set()
        for note_id in note_ids:
            if not isinstance(note_id, str) or not note_id.strip():
                raise ValueError("note_ids must contain non-empty strings")
            normalized.add(note_id)
        return tuple(sorted(normalized))

    @staticmethod
    def _all_note_ids(connection: sqlite3.Connection) -> tuple[str, ...]:
        return tuple(str(row[0]) for row in connection.execute("SELECT note_id FROM notes ORDER BY note_id"))

    def _load_notes(self, connection: sqlite3.Connection) -> tuple[GovernanceNote, ...]:
        rows = connection.execute(
            """
            SELECT note_id, source_id, owner, policy_key, document_version,
                   effective_from, effective_to, policy_status, metadata_issues_json,
                   workspace, department, acl_json, acl_public, content_hash
            FROM notes
            ORDER BY note_id
            """
        ).fetchall()
        return tuple(self._note_from_mapping(dict(row)) for row in rows)

    @staticmethod
    def _note_from_mapping(row: Mapping[str, Any]) -> GovernanceNote:
        note_id = row.get("note_id")
        if not isinstance(note_id, str) or not note_id.strip():
            raise GovernancePersistenceError("persisted note_id is missing or malformed")

        issues = _metadata_issues(row.get("metadata_issues_json", row.get("metadata_issues")))
        source_id = row.get("source_id")
        content_hash = row.get("content_hash")
        acl_json = row.get("acl_json")
        acl_public = row.get("acl_public")
        if not isinstance(source_id, str) or not source_id.strip():
            issues = _add_issue(issues, "missing_source_id")
            source_id = ""
        if not isinstance(content_hash, str) or not content_hash.strip():
            issues = _add_issue(issues, "missing_content_hash")
            content_hash = ""
        if not _valid_acl_json(acl_json):
            issues = _add_issue(issues, "malformed_acl_json")
            acl_json = "{}" if not isinstance(acl_json, str) else acl_json
        if acl_public not in (False, True, 0, 1):
            issues = _add_issue(issues, "invalid_acl_public")
            acl_public = False

        return GovernanceNote(
            note_id=note_id,
            source_id=source_id,
            owner=_optional_text(row.get("owner")),
            policy_key=_optional_text(row.get("policy_key")),
            document_version=_optional_text(row.get("document_version", row.get("version"))),
            effective_from=_optional_text(row.get("effective_from")),
            effective_to=_optional_text(row.get("effective_to")),
            policy_status=row.get("policy_status", row.get("status")),
            metadata_issues=issues,
            workspace=_optional_text(row.get("workspace")),
            department=_optional_text(row.get("department")),
            acl_json=acl_json,
            acl_public=bool(acl_public),
            content_hash=content_hash,
        )

    def _discover_cases(self, notes: Sequence[GovernanceNote]) -> tuple[_CaseCandidate, ...]:
        groups: dict[str, list[tuple[GovernanceNote, tuple[date, date | None]]]] = defaultdict(list)
        for note in notes:
            interval = self.policy.governance_interval(note)
            if interval is not None and isinstance(note.policy_key, str) and note.policy_key:
                groups[note.policy_key].append((note, interval))

        candidates: list[_CaseCandidate] = []
        for policy_key in sorted(groups):
            governed = sorted(groups[policy_key], key=lambda item: item[0].note_id)
            candidates.extend(self._duplicate_candidates(policy_key, governed))
            candidates.extend(self._conflict_candidates(policy_key, governed))
        return tuple(sorted(candidates, key=lambda item: (item.case_type, item.rule_key)))

    def _duplicate_candidates(
        self,
        policy_key: str,
        governed: Sequence[tuple[GovernanceNote, tuple[date, date | None]]],
    ) -> tuple[_CaseCandidate, ...]:
        by_checksum: dict[str, list[GovernanceNote]] = defaultdict(list)
        for note, _ in governed:
            if note.content_hash:
                by_checksum[note.content_hash].append(note)

        result: list[_CaseCandidate] = []
        for checksum in sorted(by_checksum):
            notes = sorted(by_checksum[checksum], key=lambda note: note.note_id)
            if len(notes) < 2:
                continue
            canonical = notes[0]
            equivalent = all(
                self.policy.exact_duplicate_equivalent(canonical, candidate)
                for candidate in notes[1:]
            )
            relevant = {
                "checksum": checksum,
                "notes": {
                    note.note_id: governance_metadata_dict(note)
                    for note in notes
                },
            }
            result.append(
                self._case_candidate(
                    "exact_duplicate",
                    policy_key,
                    tuple(note.note_id for note in notes),
                    relevant,
                    status="confirmed" if equivalent else "proposed",
                    canonical_note_id=canonical.note_id if equivalent else None,
                    reason_code=(
                        "exact_duplicate_equivalent"
                        if equivalent
                        else "checksum_match_requires_review"
                    ),
                )
            )
        return tuple(result)

    def _conflict_candidates(
        self,
        policy_key: str,
        governed: Sequence[tuple[GovernanceNote, tuple[date, date | None]]],
    ) -> tuple[_CaseCandidate, ...]:
        by_id = {note.note_id: (note, interval) for note, interval in governed}
        edges: dict[str, set[str]] = defaultdict(set)
        ids = sorted(by_id)
        for index, left_id in enumerate(ids):
            left, left_interval = by_id[left_id]
            for right_id in ids[index + 1 :]:
                right, right_interval = by_id[right_id]
                if self.policy.exact_duplicate_equivalent(left, right):
                    continue
                left_version = governance_metadata_dict(left)["document_version"]
                right_version = governance_metadata_dict(right)["document_version"]
                if left_version == right_version or _intervals_overlap(
                    left_interval, right_interval
                ):
                    edges[left_id].add(right_id)
                    edges[right_id].add(left_id)

        result: list[_CaseCandidate] = []
        unseen = set(edges)
        while unseen:
            seed = min(unseen)
            stack = [seed]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(sorted(edges[current] - component, reverse=True))
            unseen.difference_update(component)
            participant_ids = tuple(sorted(component))
            relevant = {
                "intervals": {
                    note_id: {
                        "effective_from": by_id[note_id][1][0].isoformat(),
                        "effective_to": (
                            by_id[note_id][1][1].isoformat()
                            if by_id[note_id][1][1] is not None
                            else None
                        ),
                    }
                    for note_id in participant_ids
                },
                "document_versions": {
                    note_id: governance_metadata_dict(by_id[note_id][0])[
                        "document_version"
                    ]
                    for note_id in participant_ids
                },
            }
            result.append(
                self._case_candidate(
                    "version_conflict",
                    policy_key,
                    participant_ids,
                    relevant,
                    status="proposed",
                    canonical_note_id=None,
                    reason_code="overlapping_effective_intervals",
                )
            )
        return tuple(result)

    @staticmethod
    def _case_candidate(
        case_type: str,
        policy_key: str,
        participant_note_ids: tuple[str, ...],
        relevant_metadata: Mapping[str, Any],
        *,
        status: str,
        canonical_note_id: str | None,
        reason_code: str,
    ) -> _CaseCandidate:
        relevant_metadata_json = canonical_json(relevant_metadata)
        relevant_metadata_hash = hashlib.sha256(
            relevant_metadata_json.encode("utf-8")
        ).hexdigest()
        rule_payload = {
            "case_type": case_type,
            "participant_note_ids": sorted(participant_note_ids),
            "relevant_metadata": relevant_metadata,
        }
        rule_key = hashlib.sha256(canonical_json(rule_payload).encode("utf-8")).hexdigest()
        return _CaseCandidate(
            case_type,
            policy_key,
            tuple(sorted(participant_note_ids)),
            status,
            canonical_note_id,
            reason_code,
            rule_key,
            relevant_metadata_hash,
        )

    @staticmethod
    def _insert_case(
        connection: sqlite3.Connection,
        candidate: _CaseCandidate,
        *,
        now: str,
    ) -> bool:
        case_id = f"case-{candidate.rule_key}"
        existing_identity = connection.execute(
            "SELECT case_type, rule_key FROM governance_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if existing_identity is not None and (
            existing_identity["case_type"] != candidate.case_type
            or existing_identity["rule_key"] != candidate.rule_key
        ):
            raise GovernancePersistenceError("persisted governance case identity is malformed")
        evidence = canonical_json(
            {
                "participant_note_ids": list(candidate.participant_note_ids),
                "relevant_metadata_hash": candidate.relevant_metadata_hash,
            }
        )
        cursor = connection.execute(
            """
            INSERT INTO governance_cases (
                case_id, case_type, policy_key, status, canonical_note_id,
                reason_code, rule_key, evidence_json, created_at, updated_at,
                resolved_at, resolved_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_type, rule_key) DO NOTHING
            """,
            (
                case_id,
                candidate.case_type,
                candidate.policy_key,
                candidate.status,
                candidate.canonical_note_id,
                candidate.reason_code,
                candidate.rule_key,
                evidence,
                now,
                now,
                now if candidate.status == "confirmed" else None,
                "governance-policy" if candidate.status == "confirmed" else None,
            ),
        )
        if cursor.rowcount != 1:
            existing = connection.execute(
                """
                SELECT case_id, reason_code, evidence_json
                FROM governance_cases
                WHERE case_type = ? AND rule_key = ?
                """,
                (candidate.case_type, candidate.rule_key),
            ).fetchone()
            linked_ids = (
                tuple(
                    sorted(
                        str(row[0])
                        for row in connection.execute(
                            "SELECT note_id FROM governance_case_notes WHERE case_id = ?",
                            (existing["case_id"],),
                        )
                    )
                )
                if existing is not None
                else ()
            )
            if (
                existing is None
                or existing["case_id"] != case_id
                or not _safe_reason_code(existing["reason_code"])
                or _evidence_participant_ids(existing["evidence_json"])
                != candidate.participant_note_ids
                or _evidence_metadata_hash(existing["evidence_json"])
                != candidate.relevant_metadata_hash
                or linked_ids != candidate.participant_note_ids
            ):
                raise GovernancePersistenceError("persisted governance case state is malformed")
            return False

        for note_id in candidate.participant_note_ids:
            role = "candidate"
            if candidate.canonical_note_id == note_id:
                role = "canonical"
            elif candidate.canonical_note_id is not None:
                role = "alias"
            connection.execute(
                """
                INSERT INTO governance_case_notes (case_id, note_id, participant_role)
                VALUES (?, ?, ?)
                """,
                (case_id, note_id, role),
            )
        return True

    @staticmethod
    def _append_case_event(
        connection: sqlite3.Connection,
        candidate: _CaseCandidate,
        *,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO governance_events (
                event_id, case_id, note_id, policy_key, actor, action,
                previous_state_json, new_state_json, reason_code,
                evidence_ids_json, source, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                f"case-{candidate.rule_key}",
                candidate.policy_key,
                "governance-policy",
                candidate.status,
                "{}",
                canonical_json(
                    {
                        "case_type": candidate.case_type,
                        "status": candidate.status,
                        "canonical_note_id": candidate.canonical_note_id,
                    }
                ),
                candidate.reason_code,
                canonical_json(list(candidate.participant_note_ids)),
                "ingestion_rule",
                now,
            ),
        )

    @staticmethod
    def _confirmed_case_participants(
        connection: sqlite3.Connection, note_ids: set[str]
    ) -> set[str]:
        if not note_ids:
            return set()
        placeholders = ",".join("?" for _ in note_ids)
        rows = connection.execute(
            f"""
            SELECT DISTINCT linked.note_id
            FROM governance_cases AS cases
            JOIN governance_case_notes AS selected ON selected.case_id = cases.case_id
            JOIN governance_case_notes AS linked ON linked.case_id = cases.case_id
            WHERE cases.status = 'confirmed' AND selected.note_id IN ({placeholders})
            """,
            tuple(sorted(note_ids)),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _confirmed_decisions_in_connection(
        self,
        connection: sqlite3.Connection,
        note_ids: set[str],
        *,
        notes_by_id: Mapping[str, GovernanceNote],
        current_candidates: Mapping[tuple[str, str], _CaseCandidate],
    ) -> dict[str, tuple[ConfirmedGovernanceDecision, ...]]:
        decisions: dict[str, list[ConfirmedGovernanceDecision]] = {
            note_id: [] for note_id in sorted(note_ids)
        }
        rows = connection.execute(
            """
            SELECT case_id, case_type, canonical_note_id, reason_code, rule_key, evidence_json
            FROM governance_cases
            WHERE status = 'confirmed'
            ORDER BY case_id
            """
        ).fetchall()
        for row in rows:
            case_id = str(row["case_id"])
            linked_rows = connection.execute(
                """
                SELECT note_id, participant_role
                FROM governance_case_notes
                WHERE case_id = ?
                ORDER BY note_id
                """,
                (case_id,),
            ).fetchall()
            linked = {str(item["note_id"]): str(item["participant_role"]) for item in linked_rows}
            evidence_ids = self._validate_confirmed_authority(
                dict(row),
                linked,
                notes_by_id=notes_by_id,
            )
            candidate = current_candidates.get((str(row["case_type"]), str(row["rule_key"])))
            if candidate is None:
                evidence_hash = _evidence_metadata_hash(row["evidence_json"])
                same_current_identity = any(
                    current.case_type == str(row["case_type"])
                    and current.participant_note_ids == evidence_ids
                    and current.relevant_metadata_hash == evidence_hash
                    for current in current_candidates.values()
                )
                if same_current_identity:
                    raise GovernancePersistenceError(
                        "confirmed governance authority is malformed"
                    )
                continue
            applicable_ids = set(evidence_ids).intersection(note_ids)
            if not applicable_ids:
                continue
            if (
                evidence_ids != candidate.participant_note_ids
                or _evidence_metadata_hash(row["evidence_json"])
                != candidate.relevant_metadata_hash
            ):
                raise GovernancePersistenceError(
                    "confirmed governance authority is malformed"
                )
            valid = str(row["case_type"]) != "exact_duplicate" or (
                candidate.status == "confirmed"
                and row["canonical_note_id"] == candidate.canonical_note_id
                )
            if not valid:
                for note_id in applicable_ids:
                    decisions[note_id].append(
                        ConfirmedGovernanceDecision(
                            note_id,
                            GovernanceDisposition.CONFLICT_BLOCKED,
                            "corrupt_confirmed_governance_case",
                            decision_id=case_id,
                        )
                    )
                continue

            case_type = str(row["case_type"])
            if case_type == "version_conflict":
                for note_id in applicable_ids:
                    decisions[note_id].append(
                        ConfirmedGovernanceDecision(
                            note_id,
                            GovernanceDisposition.CONFLICT_BLOCKED,
                            str(row["reason_code"]),
                            decision_id=case_id,
                        )
                    )
            elif case_type == "exact_duplicate":
                canonical_note_id = row["canonical_note_id"]
                if (
                    not isinstance(canonical_note_id, str)
                    or canonical_note_id not in linked
                    or linked[canonical_note_id] != "canonical"
                ):
                    for note_id in applicable_ids:
                        decisions[note_id].append(
                            ConfirmedGovernanceDecision(
                                note_id,
                                GovernanceDisposition.CONFLICT_BLOCKED,
                                "corrupt_confirmed_governance_case",
                                decision_id=case_id,
                            )
                        )
                    continue
                for note_id in applicable_ids:
                    if note_id == canonical_note_id:
                        continue
                    if linked.get(note_id) != "alias":
                        decisions[note_id].append(
                            ConfirmedGovernanceDecision(
                                note_id,
                                GovernanceDisposition.CONFLICT_BLOCKED,
                                "corrupt_confirmed_governance_case",
                                decision_id=case_id,
                            )
                        )
                        continue
                    decisions[note_id].append(
                        ConfirmedGovernanceDecision(
                            note_id,
                            GovernanceDisposition.DUPLICATE_ALIAS,
                            str(row["reason_code"]),
                            canonical_note_id,
                            case_id,
                        )
                    )
        return {
            note_id: tuple(
                sorted(
                    values,
                    key=lambda item: (
                        item.decision_id or "",
                        item.disposition.value,
                        item.reason_code,
                        item.canonical_note_id or "",
                    ),
                )
            )
            for note_id, values in decisions.items()
        }

    @staticmethod
    def _validate_confirmed_authority(
        row: Mapping[str, Any],
        linked: Mapping[str, str],
        *,
        notes_by_id: Mapping[str, GovernanceNote],
    ) -> tuple[str, ...]:
        case_id = row.get("case_id")
        case_type = row.get("case_type")
        rule_key = row.get("rule_key")
        canonical_note_id = row.get("canonical_note_id")
        evidence_ids = _evidence_participant_ids(row.get("evidence_json"))
        structurally_valid = (
            isinstance(case_id, str)
            and _is_sha256(rule_key)
            and case_id == f"case-{rule_key}"
            and case_type in {"exact_duplicate", "version_conflict"}
            and _safe_reason_code(row.get("reason_code"))
            and evidence_ids is not None
            and _evidence_metadata_hash(row.get("evidence_json")) is not None
            and set(linked) == set(evidence_ids or ())
            and all(note_id in notes_by_id for note_id in evidence_ids or ())
        )
        if case_type == "exact_duplicate":
            structurally_valid = structurally_valid and (
                isinstance(canonical_note_id, str)
                and canonical_note_id in linked
                and linked.get(canonical_note_id) == "canonical"
                and all(
                    role == ("canonical" if note_id == canonical_note_id else "alias")
                    for note_id, role in linked.items()
                )
            )
        elif case_type == "version_conflict":
            structurally_valid = structurally_valid and canonical_note_id is None
        if not structurally_valid or evidence_ids is None:
            raise GovernancePersistenceError("confirmed governance authority is malformed")
        return evidence_ids

    def _project_note(
        self,
        connection: sqlite3.Connection,
        note: GovernanceNote,
        evaluation: GovernanceEvaluation,
        decisions: Sequence[ConfirmedGovernanceDecision],
        *,
        as_of: date,
        now: str,
    ) -> tuple[bool, bool, bool]:
        row = connection.execute(
            "SELECT * FROM governance_note_state WHERE note_id = ?", (note.note_id,)
        ).fetchone()
        previous = _stored_state(dict(row)) if row is not None else None
        fingerprint = _decision_fingerprint(note, evaluation, decisions)
        if previous is not None and previous.decision_fingerprint == fingerprint:
            if (
                previous.lifecycle_state != evaluation.lifecycle_state.value
                or previous.disposition != evaluation.disposition.value
                or previous.reason_codes != evaluation.reason_codes
            ):
                raise GovernancePersistenceError(
                    "persisted governance projection semantics contradict its fingerprint"
                )
            if previous.evaluated_on != as_of.isoformat():
                connection.execute(
                    "UPDATE governance_note_state SET evaluated_on = ? WHERE note_id = ?",
                    (as_of.isoformat(), note.note_id),
                )
            return False, False, False

        connection.execute(
            """
            INSERT INTO governance_note_state (
                note_id, evaluated_on, lifecycle_state, disposition,
                reason_codes_json, decision_fingerprint, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                evaluated_on = excluded.evaluated_on,
                lifecycle_state = excluded.lifecycle_state,
                disposition = excluded.disposition,
                reason_codes_json = excluded.reason_codes_json,
                decision_fingerprint = excluded.decision_fingerprint,
                updated_at = excluded.updated_at
            """,
            (
                note.note_id,
                as_of.isoformat(),
                evaluation.lifecycle_state.value,
                evaluation.disposition.value,
                canonical_json(list(evaluation.reason_codes)),
                fingerprint,
                now,
            ),
        )
        membership_changed = previous is None or previous.eligible != evaluation.eligible
        if membership_changed:
            connection.execute(
                "UPDATE notes SET index_status = 'pending', updated_at = ? WHERE note_id = ?",
                (now, note.note_id),
            )
        connection.execute(
            """
            INSERT INTO governance_events (
                event_id, note_id, policy_key, actor, action, previous_state_json,
                new_state_json, reason_code, evidence_ids_json, source, created_at
            ) VALUES (?, ?, ?, ?, 'state_changed', ?, ?, ?, ?, 'lifecycle_rule', ?)
            """,
            (
                str(uuid4()),
                note.note_id,
                note.policy_key,
                "governance-policy",
                canonical_json(previous.event_dict() if previous is not None else {}),
                canonical_json(_evaluation_event_dict(evaluation)),
                evaluation.reason_codes[0] if evaluation.reason_codes else "governance_state_changed",
                "[]",
                now,
            ),
        )
        return True, membership_changed, True


def _decision_fingerprint(
    note: GovernanceNote,
    evaluation: GovernanceEvaluation,
    decisions: Sequence[ConfirmedGovernanceDecision],
) -> str:
    payload = {
        "note_id": note.note_id,
        "metadata": governance_metadata_dict(note),
        "decisions": sorted(
            (
                {
                    "decision_id": decision.decision_id,
                    "disposition": decision.disposition.value,
                    "reason_code": decision.reason_code,
                    "canonical_note_id": decision.canonical_note_id,
                }
                for decision in decisions
            ),
            key=lambda item: canonical_json(item),
        ),
        "evaluation": evaluation.to_dict(),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _metadata_issues(value: Any) -> tuple[str, ...]:
    if isinstance(value, tuple) and all(_safe_reason_code(item) for item in value):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return ("malformed_metadata_issues_json",)
    if not isinstance(parsed, list) or not all(_safe_reason_code(item) for item in parsed):
        return ("malformed_metadata_issues_json",)
    return tuple(parsed)


def _safe_reason_code(value: Any) -> bool:
    return isinstance(value, str) and value in GOVERNANCE_REASON_CODES


def _add_issue(issues: tuple[str, ...], issue: str) -> tuple[str, ...]:
    return issues if issue in issues else (*issues, issue)


def _valid_acl_json(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    for key in ("allow", "deny"):
        members = parsed.get(key)
        if members is not None and (
            not isinstance(members, list)
            or not all(isinstance(member, str) for member in members)
        ):
            return False
    return True


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _intervals_overlap(
    left: tuple[date, date | None], right: tuple[date, date | None]
) -> bool:
    left_start, left_end = left
    right_start, right_end = right
    return (left_end is None or right_start <= left_end) and (
        right_end is None or left_start <= right_end
    )


def _evidence_payload(value: Any) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _evidence_participant_ids(value: Any) -> tuple[str, ...] | None:
    payload = _evidence_payload(value)
    if payload is None:
        return None
    note_ids = payload.get("participant_note_ids")
    if (
        not isinstance(note_ids, list)
        or not note_ids
        or not all(isinstance(note_id, str) and note_id for note_id in note_ids)
    ):
        return None
    return tuple(sorted(set(note_ids)))


def _evidence_metadata_hash(value: Any) -> str | None:
    payload = _evidence_payload(value)
    fingerprint = payload.get("relevant_metadata_hash") if payload is not None else None
    return fingerprint if _is_sha256(fingerprint) else None


def _stored_state(row: Mapping[str, Any]) -> _StoredState:
    evaluated_on = row.get("evaluated_on")
    lifecycle_state = row.get("lifecycle_state")
    disposition = row.get("disposition")
    fingerprint = row.get("decision_fingerprint")
    try:
        date.fromisoformat(evaluated_on)
        LifecycleState(lifecycle_state)
        GovernanceDisposition(disposition)
    except (TypeError, ValueError) as exc:
        raise GovernancePersistenceError("persisted governance projection is malformed") from exc
    reason_codes = _metadata_issues(row.get("reason_codes_json"))
    if "malformed_metadata_issues_json" in reason_codes or not _is_sha256(fingerprint):
        raise GovernancePersistenceError("persisted governance projection is malformed")
    return _StoredState(
        evaluated_on,
        lifecycle_state,
        disposition,
        reason_codes,
        fingerprint,
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _evaluation_event_dict(evaluation: GovernanceEvaluation) -> dict[str, Any]:
    return {
        "lifecycle_state": evaluation.lifecycle_state.value,
        "disposition": evaluation.disposition.value,
        "reason_codes": list(evaluation.reason_codes),
        "canonical_note_id": evaluation.canonical_note_id,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
