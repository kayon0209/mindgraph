from __future__ import annotations

import json
from pathlib import Path

import pytest

from application.acl_backfill_service import AclBackfillService
from infrastructure.database import ProductDatabase


def _insert_note(
    db: ProductDatabase,
    *,
    note_id: str = "n1",
    vault_path: str = "missing.md",
    source_id: str = "builtin",
    frontmatter: dict | None = None,
    workspace: str | None = "legacy-workspace",
    department: str | None = "legacy-department",
    acl: dict | None = None,
    acl_public: int = 1,
) -> None:
    db.execute(
        "INSERT INTO notes "
        "(note_id, vault_path, source_id, title, content_hash, frontmatter_json, "
        "workspace, department, acl_json, acl_public, created_at, updated_at) "
        "VALUES (?, ?, ?, 'Policy', 'hash', ?, ?, ?, ?, ?, 'now', 'now')",
        (
            note_id,
            vault_path,
            source_id,
            json.dumps(frontmatter or {}),
            workspace,
            department,
            json.dumps(acl or {"allow": ["public:legacy"]}),
            acl_public,
        ),
    )


def test_initialize_backfills_connector_prefixed_note_source_id(tmp_path: Path):
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    db.execute(
        "INSERT INTO connector_syncs "
        "(connector_id, connector_type, source_path, status, started_at, finished_at) "
        "VALUES ('dir-abc', 'markdown_directory', ?, 'completed', 'start', 'finish')",
        (str(tmp_path / "connector"),),
    )
    _insert_note(db, vault_path="dir-abc/finance/policy.md")
    db.execute("UPDATE schema_meta SET version=7")

    db.initialize()

    assert db.fetch_one("SELECT source_id FROM notes WHERE note_id='n1'")["source_id"] == "dir-abc"


def test_backfill_unresolved_note_is_private_and_rollback_restores_original_acl(tmp_path: Path):
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db)
    service = AclBackfillService(db, tmp_path / "knowledge")

    plan = service.plan()

    assert plan["unresolved_count"] == 1
    assert "content" not in plan["items"][0]
    applied = service.apply()
    private_note = db.fetch_one("SELECT acl_json, acl_public FROM notes WHERE note_id='n1'")
    assert private_note["acl_public"] == 0
    assert json.loads(private_note["acl_json"])["backfill_reason"] == "source_unavailable"
    audit_columns = {
        row["name"] for row in db.fetch_all("PRAGMA table_info(acl_backfill_items)")
    }
    assert "content" not in audit_columns
    assert service.rollback(applied["run_id"])["restored"] == 1
    restored = db.fetch_one(
        "SELECT workspace, department, acl_json, acl_public FROM notes WHERE note_id='n1'"
    )
    assert restored == {
        "workspace": "legacy-workspace",
        "department": "legacy-department",
        "acl_json": json.dumps({"allow": ["public:legacy"]}),
        "acl_public": 1,
    }


def test_explicit_frontmatter_acl_and_public_marker_take_precedence(tmp_path: Path):
    root = tmp_path / "knowledge"
    (root / "finance").mkdir(parents=True)
    (root / "finance" / "policy.md").write_text("# policy", encoding="utf-8")
    (root / "finance" / "acl.json").write_text(
        json.dumps({"allow": ["department:finance"], "public": False}),
        encoding="utf-8",
    )
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(
        db,
        vault_path="finance/policy.md",
        frontmatter={
            "workspace": "corp",
            "department": "legal",
            "acl": {"allow": ["department:legal"]},
            "acl_public": True,
        },
    )

    planned = AclBackfillService(db, root).plan()["items"][0]

    assert planned["reason"] == "explicit_frontmatter"
    assert planned["workspace"] == "corp"
    assert planned["department"] == "legal"
    assert planned["acl"]["allow"] == ["department:legal"]
    assert "department:finance" not in planned["acl"]["allow"]
    assert planned["acl"]["public"] is True
    assert planned["acl_public"] == 1


def test_controlled_directory_acl_is_used_for_resolved_note(tmp_path: Path):
    root = tmp_path / "knowledge"
    (root / "finance").mkdir(parents=True)
    (root / "finance" / "policy.md").write_text("# policy", encoding="utf-8")
    (root / "finance" / "acl.json").write_text(
        json.dumps(
            {
                "workspace": "corp",
                "department": "finance",
                "allow": ["workspace:corp", "department:finance"],
            }
        ),
        encoding="utf-8",
    )
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db, vault_path="finance/policy.md", frontmatter={})

    applied = AclBackfillService(db, root).apply()

    assert applied["changed"] == 1
    note = db.fetch_one(
        "SELECT workspace, department, acl_json, acl_public FROM notes WHERE note_id='n1'"
    )
    assert note["workspace"] == "corp"
    assert note["department"] == "finance"
    assert json.loads(note["acl_json"])["allow"] == [
        "workspace:corp",
        "department:finance",
    ]
    assert note["acl_public"] == 0


def test_apply_creates_new_run_and_audits_unchanged_notes(tmp_path: Path):
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(
        db,
        workspace=None,
        department=None,
        acl={"allow": [], "backfill_reason": "source_unavailable"},
        acl_public=0,
    )
    service = AclBackfillService(db, tmp_path / "knowledge")

    first = service.apply()
    second = service.apply()

    assert first["run_id"] != second["run_id"]
    assert first["changed"] == 0
    assert second["changed"] == 0
    item = db.fetch_one(
        "SELECT action, old_acl_json, old_acl_public FROM acl_backfill_items WHERE run_id=?",
        (second["run_id"],),
    )
    assert item == {
        "action": "unchanged",
        "old_acl_json": json.dumps(
            {"allow": [], "backfill_reason": "source_unavailable"}
        ),
        "old_acl_public": 0,
    }


def test_rollback_rejects_unknown_or_incomplete_run(tmp_path: Path):
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    service = AclBackfillService(db, tmp_path / "knowledge")

    with pytest.raises(ValueError, match="unknown or incomplete"):
        service.rollback("missing")

    db.execute(
        "INSERT INTO acl_backfill_runs "
        "(run_id, status, started_at, item_count, changed_count, unresolved_count) "
        "VALUES ('running-run', 'running', 'now', 0, 0, 0)"
    )
    with pytest.raises(ValueError, match="unknown or incomplete"):
        service.rollback("running-run")
