"""Deterministic governance evaluation with no database or clock dependency."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Any

from domain.governance import (
    ConfirmedGovernanceDecision,
    GovernanceDisposition,
    GovernanceEvaluation,
    GovernanceMode,
    GovernanceNote,
    LifecycleState,
    NormalizedPolicyMetadata,
)

POLICY_STATUSES = frozenset({"draft", "active", "expired", "superseded", "archived", "unspecified"})
TERMINAL_STATUSES = frozenset({"expired", "superseded", "archived"})
DECISION_PRIORITIES = {
    GovernanceDisposition.CONFLICT_BLOCKED: 0,
    GovernanceDisposition.DUPLICATE_ALIAS: 1,
    GovernanceDisposition.ELIGIBLE: 2,
}


def _metadata_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def normalize_policy_metadata(fm: Mapping[str, Any]) -> NormalizedPolicyMetadata:
    """Normalize frontmatter and retain every quality issue for later governance."""
    owner = _metadata_text(fm.get("owner"))
    policy_key = _metadata_text(fm.get("policy_key"))
    version = _metadata_text(fm.get("version"))
    effective_from = _metadata_text(fm.get("effective_from"))
    effective_to = _metadata_text(fm.get("effective_to"))
    raw_status = (_metadata_text(fm.get("status")) or "").lower()
    issues: list[str] = []

    if not owner:
        issues.append("missing_owner")
    if not policy_key:
        issues.append("missing_policy_key")
    if not version:
        issues.append("missing_version")
    if not effective_from:
        issues.append("missing_effective_from")
    if not raw_status:
        issues.append("missing_policy_status")
        status = "unspecified"
    elif raw_status not in POLICY_STATUSES:
        issues.append("invalid_policy_status")
        status = "unspecified"
    else:
        status = raw_status

    parsed_from = _parse_date(effective_from)
    parsed_to = _parse_date(effective_to)
    if (effective_from and parsed_from is None) or (effective_to and parsed_to is None):
        issues.append("invalid_effective_date")
    elif parsed_from and parsed_to and parsed_to < parsed_from:
        issues.append("invalid_effective_range")

    return NormalizedPolicyMetadata(
        owner, policy_key, version, effective_from, effective_to, status, tuple(issues)
    )


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class _ValidatedInterval:
    effective_from: date | None = None
    effective_to: date | None = None
    error_reasons: tuple[str, ...] = ()


class GovernancePolicy:
    REQUIRED_FIELDS = ("owner", "policy_key", "document_version", "effective_from")

    def evaluate(
        self,
        note: GovernanceNote,
        *,
        as_of: date,
        mode: GovernanceMode,
        confirmed_decisions: Sequence[ConfirmedGovernanceDecision] = (),
    ) -> GovernanceEvaluation:
        decisions = tuple(item for item in confirmed_decisions if item.note_id == note.note_id)
        if any(not self._valid_confirmed_decision(note.note_id, item) for item in decisions):
            return self._unresolved(note.note_id, "invalid_confirmed_decision")
        decision = min(decisions, key=lambda item: DECISION_PRIORITIES[item.disposition], default=None)
        if decision and decision.disposition is GovernanceDisposition.CONFLICT_BLOCKED:
            return self._from_confirmed_decision(note.note_id, decision)

        interval = self._validated_interval(note)
        if interval.error_reasons:
            return self._unresolved(note.note_id, interval.error_reasons)
        if decision and decision.disposition is GovernanceDisposition.DUPLICATE_ALIAS:
            return self._from_confirmed_decision(note.note_id, decision)
        return self._evaluate_interval(note, interval, as_of=as_of, mode=mode)

    def exact_duplicate_equivalent(self, left: GovernanceNote, right: GovernanceNote) -> bool:
        left_acl = _canonical_acl(left.acl_json)
        right_acl = _canonical_acl(right.acl_json)
        if (
            not self._valid_duplicate_note(left, left_acl)
            or not self._valid_duplicate_note(right, right_acl)
        ):
            return False
        return self._duplicate_fingerprint(left, left_acl) == self._duplicate_fingerprint(right, right_acl)

    @classmethod
    def _validated_interval(cls, note: GovernanceNote) -> _ValidatedInterval:
        metadata_issues = note.metadata_issues
        if not isinstance(metadata_issues, tuple) or not all(
            isinstance(issue, str) and issue for issue in metadata_issues
        ):
            return _ValidatedInterval(error_reasons=("malformed_metadata_issues_json",))
        if metadata_issues:
            return _ValidatedInterval(error_reasons=metadata_issues)

        for field in cls.REQUIRED_FIELDS:
            value = getattr(note, field)
            if not isinstance(value, str) or not value.strip():
                return _ValidatedInterval(error_reasons=(f"missing_{field}",))

        if _policy_status(note.policy_status) is None:
            return _ValidatedInterval(error_reasons=("invalid_policy_status",))

        effective_from = _parse_date(note.effective_from)
        effective_to = _parse_date(note.effective_to)
        if effective_from is None or (note.effective_to is not None and effective_to is None):
            return _ValidatedInterval(error_reasons=("invalid_effective_date",))
        if effective_to and effective_to < effective_from:
            return _ValidatedInterval(error_reasons=("invalid_effective_range",))
        return _ValidatedInterval(effective_from, effective_to)

    @staticmethod
    def _valid_confirmed_decision(note_id: str, decision: ConfirmedGovernanceDecision) -> bool:
        if (
            not isinstance(decision.disposition, GovernanceDisposition)
            or decision.disposition not in DECISION_PRIORITIES
            or not isinstance(decision.reason_code, str)
            or not decision.reason_code.strip()
        ):
            return False
        if decision.disposition is GovernanceDisposition.DUPLICATE_ALIAS:
            return (
                isinstance(decision.canonical_note_id, str)
                and bool(decision.canonical_note_id.strip())
                and decision.canonical_note_id != note_id
            )
        return decision.canonical_note_id is None

    @classmethod
    def _valid_duplicate_note(cls, note: GovernanceNote, canonical_acl: str | None) -> bool:
        if cls._validated_interval(note).error_reasons or canonical_acl is None:
            return False
        return (
            isinstance(note.source_id, str)
            and bool(note.source_id.strip())
            and isinstance(note.content_hash, str)
            and bool(note.content_hash.strip())
            and isinstance(note.acl_public, bool)
            and all(value is None or isinstance(value, str) for value in (note.workspace, note.department))
        )

    @staticmethod
    def _unresolved(note_id: str, reasons: str | tuple[str, ...]) -> GovernanceEvaluation:
        reason_codes = (reasons,) if isinstance(reasons, str) else reasons
        return GovernanceEvaluation(
            note_id,
            LifecycleState.UNRESOLVED,
            GovernanceDisposition.UNRESOLVED,
            False,
            reason_codes,
        )

    @staticmethod
    def _from_confirmed_decision(
        note_id: str, decision: ConfirmedGovernanceDecision
    ) -> GovernanceEvaluation:
        return GovernanceEvaluation(
            note_id,
            LifecycleState.UNRESOLVED,
            decision.disposition,
            decision.disposition is GovernanceDisposition.ELIGIBLE,
            (decision.reason_code,),
            decision.canonical_note_id,
        )

    @staticmethod
    def _evaluate_interval(
        note: GovernanceNote,
        interval: _ValidatedInterval,
        *,
        as_of: date,
        mode: GovernanceMode,
    ) -> GovernanceEvaluation:
        assert interval.effective_from is not None
        if as_of < interval.effective_from:
            return GovernanceEvaluation(
                note.note_id,
                LifecycleState.NOT_YET_EFFECTIVE,
                GovernanceDisposition.EXCLUDED,
                False,
                ("not_yet_effective",),
            )
        if interval.effective_to and as_of > interval.effective_to:
            return GovernanceEvaluation(
                note.note_id,
                LifecycleState.EXPIRED,
                GovernanceDisposition.EXCLUDED,
                False,
                ("effective_period_ended",),
            )

        status = _policy_status(note.policy_status)
        assert status is not None
        if status != "active" and mode is GovernanceMode.CURRENT:
            return GovernanceEvaluation(
                note.note_id,
                LifecycleState.CURRENT,
                GovernanceDisposition.EXCLUDED,
                False,
                (f"declared_{status}",),
            )
        if mode is GovernanceMode.HISTORICAL and status in TERMINAL_STATUSES and interval.effective_to is None:
            return GovernancePolicy._unresolved(note.note_id, "historical_status_without_effective_to")
        if status in {"draft", "unspecified"}:
            return GovernanceEvaluation(
                note.note_id,
                LifecycleState.HISTORICAL if mode is GovernanceMode.HISTORICAL else LifecycleState.CURRENT,
                GovernanceDisposition.EXCLUDED,
                False,
                (f"declared_{status}",),
            )
        if mode is GovernanceMode.HISTORICAL:
            return GovernanceEvaluation(
                note.note_id,
                LifecycleState.HISTORICAL,
                GovernanceDisposition.ELIGIBLE,
                True,
                ("eligible_historical_version",),
            )
        return GovernanceEvaluation(
            note.note_id,
            LifecycleState.CURRENT,
            GovernanceDisposition.ELIGIBLE,
            True,
            ("eligible_current_version",),
        )

    @staticmethod
    def _duplicate_fingerprint(note: GovernanceNote, canonical_acl: str) -> tuple[object, ...]:
        return (
            note.source_id,
            note.content_hash,
            note.owner,
            note.policy_key,
            note.document_version,
            note.effective_from,
            note.effective_to,
            note.policy_status,
            note.metadata_issues,
            note.workspace,
            note.department,
            canonical_acl,
            note.acl_public,
        )


def _policy_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    status = value.lower().strip()
    return status if status in POLICY_STATUSES else None


def _canonical_acl(acl_json: object) -> str | None:
    """Give semantically equal ACLs one stable security fingerprint."""
    try:
        parsed = json.loads(acl_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    for key in ("allow", "deny"):
        if key not in parsed:
            continue
        value = parsed[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return None
        parsed[key] = sorted(set(value))
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
