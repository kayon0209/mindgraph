"""UG-008 source ownership value objects and stable failure codes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceStatus = Literal["active", "disabled"]
AuditStatus = Literal["clean", "needs_review"]


class SourceOwnershipError(ValueError):
    """A fail-closed source ownership validation error."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RegisteredSource:
    source_id: str
    connector_id: str
    connector_type: str
    root_locator: str
    status: SourceStatus


@dataclass(frozen=True, slots=True)
class OwnershipFinding:
    reason_code: str
    note_id: str | None
    source_id: str | None
    source_path: str | None


@dataclass(frozen=True, slots=True)
class OwnershipAuditResult:
    audit_run_id: str
    status: AuditStatus
    source_id: str | None
    finding_count: int
