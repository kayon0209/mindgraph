from __future__ import annotations

import json
from typing import Any


def public_access_scope() -> dict[str, Any]:
    """Return the explicit scope used by unauthenticated demo users.

    ``None`` is reserved for the deliberate ``AUTH_MODE=off`` bypass.  An
    anonymous caller must carry a real scope so private records are denied by
    default while records marked public remain visible.
    """
    return {
        "user": "anonymous",
        "roles": [],
        "allow": [],
        "deny": [],
        "workspace": None,
        "department": None,
        "public_only": True,
    }


def _normalized_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        items = [str(value).strip()]
    return [item for item in items if item]


def _parse_acl_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def access_tags(workspace: Any = None, department: Any = None) -> set[str]:
    tags: set[str] = set()
    for value, prefix in ((workspace, "workspace"), (department, "department")):
        text = str(value).strip() if value is not None else ""
        if text:
            tags.add(f"{prefix}:{text}")
    return tags


def build_access_scope(principal: dict[str, Any] | None) -> dict[str, Any] | None:
    if not principal or not principal.get("authenticated"):
        return None

    roles = [str(role).strip() for role in principal.get("roles", []) if str(role).strip()]
    allow = set(_normalized_items(principal.get("allow")))
    deny = set(_normalized_items(principal.get("deny")))

    for key, prefix in (
        ("allow_workspaces", "workspace"),
        ("workspaces", "workspace"),
        ("allow_departments", "department"),
        ("departments", "department"),
    ):
        for item in _normalized_items(principal.get(key)):
            allow.add(item if ":" in item else f"{prefix}:{item}")

    for key, prefix in (
        ("deny_workspaces", "workspace"),
        ("blocked_workspaces", "workspace"),
        ("deny_departments", "department"),
        ("blocked_departments", "department"),
    ):
        for item in _normalized_items(principal.get(key)):
            deny.add(item if ":" in item else f"{prefix}:{item}")

    workspace = str(principal.get("workspace") or "").strip()
    department = str(principal.get("department") or "").strip()
    if workspace:
        allow.add(f"workspace:{workspace}")
    if department:
        allow.add(f"department:{department}")

    acl = principal.get("acl")
    if isinstance(acl, dict):
        for item in _normalized_items(acl.get("allow")):
            allow.add(item)
        for item in _normalized_items(acl.get("deny")):
            deny.add(item)
        for item in _normalized_items(acl.get("workspaces")):
            allow.add(item if ":" in item else f"workspace:{item}")
        for item in _normalized_items(acl.get("departments")):
            allow.add(item if ":" in item else f"department:{item}")
        for item in _normalized_items(acl.get("deny_workspaces")):
            deny.add(item if ":" in item else f"workspace:{item}")
        for item in _normalized_items(acl.get("deny_departments")):
            deny.add(item if ":" in item else f"department:{item}")

    if any(role in {"admin", "owner", "superuser"} for role in roles):
        allow.add("*")

    scope = {
        "user": principal.get("name") or principal.get("username") or principal.get("sub") or "anonymous",
        "roles": roles,
        "allow": sorted(allow),
        "deny": sorted(deny),
        "workspace": workspace or None,
        "department": department or None,
    }
    if not scope["allow"] and not scope["deny"]:
        return scope
    return scope


def note_acl_matches(note: dict[str, Any], access_scope: dict[str, Any] | None) -> bool:
    if access_scope is None:
        return True
    if "*" in access_scope.get("allow", []):
        return True

    workspace = str(note.get("workspace") or "").strip()
    department = str(note.get("department") or "").strip()
    acl = _parse_acl_json(note.get("acl_json"))
    if note.get("acl_public") or acl.get("public") or acl.get("is_public"):
        return True

    note_tags = access_tags(workspace, department)
    note_tags.update(_normalized_items(acl.get("allow")))
    note_tags.update(_normalized_items(acl.get("allow_tags")))
    note_tags.update(_normalized_items(acl.get("allow_workspaces")))
    note_tags.update(_normalized_items(acl.get("allow_departments")))
    note_deny = set(_normalized_items(acl.get("deny")))
    note_deny.update(_normalized_items(acl.get("deny_tags")))
    note_deny.update(_normalized_items(acl.get("deny_workspaces")))
    note_deny.update(_normalized_items(acl.get("deny_departments")))

    if note_deny and note_deny.intersection(access_scope.get("allow", [])):
        return False

    allowed = set(access_scope.get("allow", []))
    denied = set(access_scope.get("deny", []))
    if denied.intersection(note_tags):
        return False
    if not allowed:
        return False
    # 命中判定：allow 与笔记标签的交集（deny 已在上面全部拦截）。
    return bool(allowed.intersection(note_tags))


def chunk_acl_matches(metadata: dict[str, Any], access_scope: dict[str, Any] | None) -> bool:
    if access_scope is None:
        return True
    if "*" in access_scope.get("allow", []):
        return True

    workspace = metadata.get("workspace") or metadata.get("vault_workspace")
    department = metadata.get("department")
    acl = _parse_acl_json(metadata.get("acl_json"))
    if metadata.get("acl_public") or acl.get("public") or acl.get("is_public"):
        return True

    note_tags = access_tags(workspace, department)
    note_tags.update(_normalized_items(acl.get("allow")))
    note_tags.update(_normalized_items(acl.get("allow_tags")))
    note_tags.update(_normalized_items(acl.get("allow_workspaces")))
    note_tags.update(_normalized_items(acl.get("allow_departments")))
    note_deny = set(_normalized_items(acl.get("deny")))
    note_deny.update(_normalized_items(acl.get("deny_tags")))
    note_deny.update(_normalized_items(acl.get("deny_workspaces")))
    note_deny.update(_normalized_items(acl.get("deny_departments")))

    if note_deny.intersection(access_scope.get("allow", [])):
        return False

    allowed = set(access_scope.get("allow", []))
    denied = set(access_scope.get("deny", []))
    if denied.intersection(note_tags):
        return False
    if not allowed:
        return False
    # 命中判定：allow 与笔记标签的交集（deny 已在上面全部拦截）。
    return bool(allowed.intersection(note_tags))


def record_access_audit(
    database,
    *,
    actor: str,
    action: str,
    resource: str,
    decision: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

    from infrastructure.database import dumps

    audit_id = f"audit-{uuid4().hex}"
    database.execute(
        "INSERT INTO access_audit "
        "(audit_id, request_id, actor, action, resource, decision, reason, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            audit_id,
            request_id,
            actor,
            action,
            resource,
            decision,
            reason,
            dumps(metadata or {}),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
