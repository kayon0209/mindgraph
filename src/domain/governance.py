"""Pure, immutable governance contracts for knowledge policy evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GovernanceMode(StrEnum):
    CURRENT = "current"
    HISTORICAL = "historical"


class LifecycleState(StrEnum):
    NOT_YET_EFFECTIVE = "not_yet_effective"
    CURRENT = "current"
    EXPIRED = "expired"
    HISTORICAL = "historical"
    UNRESOLVED = "unresolved"


class GovernanceDisposition(StrEnum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
    UNRESOLVED = "unresolved"
    CONFLICT_BLOCKED = "conflict_blocked"
    DUPLICATE_ALIAS = "duplicate_alias"


@dataclass(frozen=True, slots=True)
class GovernanceNote:
    note_id: str
    source_id: str
    owner: str | None
    policy_key: str | None
    document_version: str | None
    effective_from: str | None
    effective_to: str | None
    policy_status: str
    metadata_issues: tuple[str, ...]
    workspace: str | None
    department: str | None
    acl_json: str
    acl_public: bool
    content_hash: str


@dataclass(frozen=True, slots=True)
class NormalizedPolicyMetadata:
    owner: str | None
    policy_key: str | None
    document_version: str | None
    effective_from: str | None
    effective_to: str | None
    policy_status: str
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConfirmedGovernanceDecision:
    note_id: str
    disposition: GovernanceDisposition
    reason_code: str
    canonical_note_id: str | None = None


@dataclass(frozen=True, slots=True)
class GovernanceEvaluation:
    note_id: str
    lifecycle_state: LifecycleState
    disposition: GovernanceDisposition
    eligible: bool
    reason_codes: tuple[str, ...]
    canonical_note_id: str | None = None
