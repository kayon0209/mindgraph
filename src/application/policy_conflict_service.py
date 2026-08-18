from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from infrastructure.database import ProductDatabase


HISTORICAL_STATUSES = ("active", "archived", "expired", "superseded")


class PolicyConflictService:
    """Detect ambiguous policy versions without guessing version precedence."""

    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    def find_for_policy_keys(
        self,
        policy_keys: set[str],
        *,
        as_of: str | None,
        include_historical: bool,
    ) -> list[dict[str, Any]]:
        keys = sorted(key.strip() for key in policy_keys if key and key.strip())
        if not keys:
            return []
        target_date = date.fromisoformat(as_of).isoformat() if as_of else date.today().isoformat()
        key_placeholders = ",".join("?" for _ in keys)
        statuses = HISTORICAL_STATUSES if include_historical else ("active",)
        status_placeholders = ",".join("?" for _ in statuses)
        rows = self.database.fetch_all(
            f"""SELECT note_id, policy_key, title, vault_path, owner, document_version,
                       effective_from, effective_to, policy_status
                FROM notes
                WHERE policy_key IN ({key_placeholders})
                  AND policy_status IN ({status_placeholders})
                  AND (effective_from IS NULL OR effective_from <= ?)
                  AND (effective_to IS NULL OR effective_to >= ?)
                ORDER BY policy_key, effective_from, document_version, note_id""",
            (*keys, *statuses, target_date, target_date),
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["policy_key"]].append(
                {
                    "note_id": row["note_id"],
                    "title": row["title"],
                    "vault_path": row["vault_path"],
                    "version": row["document_version"],
                    "effective_from": row["effective_from"],
                    "effective_to": row["effective_to"],
                    "policy_status": row["policy_status"],
                    "owner": row["owner"],
                }
            )
        return [
            {"policy_key": policy_key, "as_of": target_date, "versions": versions}
            for policy_key, versions in sorted(grouped.items())
            if len(versions) > 1
        ]
