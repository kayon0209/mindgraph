"""Authenticated, ACL-safe knowledge-governance operations."""
from __future__ import annotations

from dataclasses import asdict, replace
import re
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from api.auth import get_required_principal, require_any_role
from api.dependencies import get_container
from application.access_control import (
    GOVERNANCE_WRITE_ROLES,
    build_access_scope,
    record_access_audit,
)
from application.governance_reconciliation_service import GovernancePersistenceError
from domain.errors import (
    AuthorizationError,
    GovernanceUnavailableError,
    NotFoundError,
)
from domain.governance import GovernanceCaseView

router = APIRouter(prefix="/knowledge-governance", tags=["knowledge-governance"])
_SAFE_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")


class ResolveGovernanceCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_status: Literal["proposed"]
    decision: Literal["confirm", "reject"]
    canonical_note_id: str | None = None


class RevokeGovernanceCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_status: Literal["confirmed", "rejected"]


def _service():
    container = get_container()
    try:
        version = container.database.fetch_one("SELECT version FROM schema_meta LIMIT 1")
        tables = {
            str(row["name"])
            for row in container.database.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        schema_ready = bool(
            version
            and int(version["version"]) >= 9
            and {
                "governance_cases",
                "governance_case_notes",
                "governance_note_state",
                "governance_events",
            }.issubset(tables)
        )
    except (sqlite3.Error, TypeError, ValueError) as exc:
        raise GovernanceUnavailableError("knowledge governance is unavailable") from exc
    if not schema_ready:
        raise GovernanceUnavailableError("knowledge governance schema is unavailable")
    service = getattr(container, "governance_cases", None)
    if service is None:
        raise GovernanceUnavailableError("knowledge governance service is unavailable")
    return service


def _scope(principal: dict) -> dict:
    scope = build_access_scope(principal)
    if scope is None:
        raise GovernanceUnavailableError("authenticated access scope is unavailable")
    return scope


def _actor(principal: dict) -> str:
    actor = principal.get("sub") or principal.get("name") or principal.get("username")
    if not isinstance(actor, str) or _SAFE_PRINCIPAL.fullmatch(actor) is None:
        raise GovernanceUnavailableError("authenticated principal identity is unavailable")
    return actor


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str) or not request_id:
        raise GovernanceUnavailableError("request identity is unavailable")
    return request_id


def _can_write(principal: dict) -> bool:
    return bool(GOVERNANCE_WRITE_ROLES.intersection(principal.get("roles", [])))


def _case_payload(case: GovernanceCaseView, principal: dict) -> dict:
    if _can_write(principal):
        return asdict(case)
    return asdict(
        replace(
            case,
            capabilities=replace(
                case.capabilities,
                can_resolve=False,
                can_revoke=False,
            ),
        )
    )


def _visible_case(case_id: str, principal: dict) -> GovernanceCaseView:
    try:
        result = _service().get_case(case_id, access_scope=_scope(principal))
    except (GovernancePersistenceError, sqlite3.Error) as exc:
        raise GovernanceUnavailableError("knowledge governance is unavailable") from exc
    if result is None:
        raise NotFoundError("governance case not found")
    return result


def _authorize_write(request: Request, principal: dict, case_id: str) -> None:
    try:
        require_any_role(*sorted(GOVERNANCE_WRITE_ROLES))(principal)
        return
    except AuthorizationError:
        pass
    try:
        record_access_audit(
            get_container().database,
            actor=_actor(principal),
            action="governance_decision",
            resource=f"knowledge-governance/cases/{case_id}",
            decision="deny",
            reason="missing_governance_write_role",
            request_id=_request_id(request),
        )
    except sqlite3.Error as exc:
        raise GovernanceUnavailableError("governance access audit is unavailable") from exc
    raise AuthorizationError("Missing required governance role")


@router.get("/cases")
def list_cases(
    request: Request,
    principal: dict = Depends(get_required_principal),
    status: Literal["proposed", "confirmed", "rejected", "revoked"] | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    if set(request.query_params).difference({"status", "limit"}):
        raise ValueError("unknown governance case query field")
    try:
        items = _service().list_cases(
            access_scope=_scope(principal), status=status, limit=limit
        )
    except (GovernancePersistenceError, sqlite3.Error) as exc:
        raise GovernanceUnavailableError("knowledge governance is unavailable") from exc
    return {"total": len(items), "items": [_case_payload(item, principal) for item in items]}


@router.get("/cases/{case_id}")
def get_case(case_id: str, principal: dict = Depends(get_required_principal)):
    return _case_payload(_visible_case(case_id, principal), principal)


@router.post("/cases/{case_id}/resolve")
def resolve_case(
    case_id: str,
    body: ResolveGovernanceCaseRequest,
    request: Request,
    principal: dict = Depends(get_required_principal),
):
    _visible_case(case_id, principal)
    _authorize_write(request, principal, case_id)
    try:
        result = _service().resolve(
            case_id,
            expected_status=body.expected_status,
            decision=body.decision,
            canonical_note_id=body.canonical_note_id,
            actor=_actor(principal),
            roles=principal.get("roles", []),
            access_scope=_scope(principal),
            request_id=_request_id(request),
        )
    except (GovernancePersistenceError, sqlite3.Error) as exc:
        raise GovernanceUnavailableError("knowledge governance is unavailable") from exc
    return _case_payload(result, principal)


@router.post("/cases/{case_id}/revoke")
def revoke_case(
    case_id: str,
    body: RevokeGovernanceCaseRequest,
    request: Request,
    principal: dict = Depends(get_required_principal),
):
    _visible_case(case_id, principal)
    _authorize_write(request, principal, case_id)
    try:
        result = _service().revoke(
            case_id,
            expected_status=body.expected_status,
            actor=_actor(principal),
            roles=principal.get("roles", []),
            access_scope=_scope(principal),
            request_id=_request_id(request),
        )
    except (GovernancePersistenceError, sqlite3.Error) as exc:
        raise GovernanceUnavailableError("knowledge governance is unavailable") from exc
    return _case_payload(result, principal)


@router.get("/events")
def list_events(
    principal: dict = Depends(get_required_principal),
    case_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    try:
        events = _service().list_events(
            access_scope=_scope(principal), case_id=case_id, limit=limit
        )
    except (GovernancePersistenceError, sqlite3.Error) as exc:
        raise GovernanceUnavailableError("knowledge governance is unavailable") from exc
    items = [
        {
            "event_id": event.event_id,
            "case_id": event.case_id,
            "note_id": event.note_id,
            "policy_key": event.policy_key,
            "actor": event.actor,
            "action": event.action,
            "previous_state": dict(event.previous_state),
            "new_state": dict(event.new_state),
            "reason_code": event.reason_code,
            "evidence_ids": list(event.evidence_ids),
            "source": event.source,
            "request_id": event.request_id,
            "created_at": event.created_at,
        }
        for event in events
    ]
    return {"total": len(items), "items": items}


__all__ = ["require_any_role", "router"]
