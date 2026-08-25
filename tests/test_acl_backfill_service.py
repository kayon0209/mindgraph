from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from application.acl_backfill_service import AclBackfillService
from infrastructure import database as database_module
from infrastructure.database import ProductDatabase


def _load_backfill_cli_module():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "backfill_note_acl.py"
    spec = importlib.util.spec_from_file_location("backfill_note_acl", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_acl_backfill_cli_defaults_to_dry_run(monkeypatch, capsys):
    cli = _load_backfill_cli_module()
    service = SimpleNamespace(plan=lambda: {"note_count": 1, "unresolved_count": 1})
    monkeypatch.setattr(cli, "_service", lambda *, read_only: service)

    cli.main([])

    assert json.loads(capsys.readouterr().out) == {
        "mode": "dry_run",
        "note_count": 1,
        "unresolved_count": 1,
    }


def test_acl_backfill_cli_accepts_explicit_dry_run(monkeypatch, capsys):
    cli = _load_backfill_cli_module()
    monkeypatch.setattr(
        cli,
        "_service",
        lambda *, read_only: SimpleNamespace(
            plan=lambda: {"note_count": 0, "unresolved_count": 0}
        ),
    )

    cli.main(["--dry-run"])

    assert json.loads(capsys.readouterr().out)["mode"] == "dry_run"


def test_acl_backfill_cli_apply_calls_service_once(monkeypatch, capsys):
    cli = _load_backfill_cli_module()
    calls: list[str] = []

    def apply():
        calls.append("apply")
        return {"run_id": "run-1", "changed": 2, "unresolved_count": 1}

    monkeypatch.setattr(cli, "_service", lambda *, read_only: SimpleNamespace(apply=apply))

    cli.main(["--apply"])

    assert calls == ["apply"]
    assert json.loads(capsys.readouterr().out) == {
        "mode": "apply",
        "run_id": "run-1",
        "changed": 2,
        "unresolved_count": 1,
    }


def test_acl_backfill_cli_rollback_requires_exact_run_id():
    cli = _load_backfill_cli_module()

    with pytest.raises(SystemExit):
        cli.main(["--rollback"])
    with pytest.raises(SystemExit):
        cli.main(["--rollback", ""])


def test_acl_backfill_cli_rollback_calls_service_once(monkeypatch, capsys):
    cli = _load_backfill_cli_module()
    calls: list[str] = []

    def rollback(run_id: str):
        calls.append(run_id)
        return {"run_id": run_id, "restored": 2}

    monkeypatch.setattr(cli, "_service", lambda *, read_only: SimpleNamespace(rollback=rollback))

    cli.main(["--rollback", "run-1"])

    assert calls == ["run-1"]
    assert json.loads(capsys.readouterr().out) == {
        "mode": "rollback",
        "run_id": "run-1",
        "restored": 2,
    }


def test_acl_backfill_cli_output_omits_private_path_and_body(monkeypatch, capsys):
    cli = _load_backfill_cli_module()
    private_path = "private/hr/redundancy.md"
    private_body = "dismissal terms for a named employee"
    monkeypatch.setattr(
        cli,
        "_service",
        lambda *, read_only: SimpleNamespace(
            plan=lambda: {
                "note_count": 1,
                "unresolved_count": 1,
                "items": [{"vault_path": private_path, "body": private_body, "acl": {"allow": []}}],
            }
        ),
    )

    cli.main([])

    output = capsys.readouterr().out
    assert private_path not in output
    assert private_body not in output
    assert "allow" not in output


def test_acl_backfill_cli_uses_runtime_database_path(monkeypatch, tmp_path: Path):
    cli = _load_backfill_cli_module()
    runtime_database_path = tmp_path / "runtime" / "product.sqlite3"
    opened_paths: list[Path] = []

    class FakeDatabase:
        def __init__(self, path: Path) -> None:
            opened_paths.append(Path(path))

    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(DATABASE_PATH=str(runtime_database_path)),
        raising=False,
    )
    monkeypatch.setattr(cli, "ProductDatabase", FakeDatabase)
    monkeypatch.setattr(
        cli,
        "AclBackfillService",
        lambda database, root: SimpleNamespace(database=database, root=root),
    )
    monkeypatch.setattr(cli, "_validate_database", lambda path: None)

    service = cli._service(read_only=False)

    assert opened_paths == [runtime_database_path]
    assert service.root == cli.PROJECT_ROOT / "knowledge"


def test_acl_backfill_cli_failure_is_redacted_json(monkeypatch, capsys):
    cli = _load_backfill_cli_module()
    private_path = "private/hr/redundancy.md"
    private_acl = "department:hr"
    private_note_id = "employee-termination-policy"

    def rollback(run_id: str):
        raise ValueError(
            f"rollback conflict for {private_note_id} at {private_path} with {private_acl}"
        )

    monkeypatch.setattr(cli, "_service", lambda *, read_only: SimpleNamespace(rollback=rollback))

    exit_code = cli.main(["--rollback", "run-1"])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "operation_failed", "mode": "rollback"}
    assert private_path not in captured.err
    assert private_acl not in captured.err
    assert private_note_id not in captured.err
    assert exit_code == 1


def test_acl_backfill_cli_dry_run_does_not_migrate_schema(monkeypatch, tmp_path: Path, capsys):
    cli = _load_backfill_cli_module()
    database_path = tmp_path / "schema7.db"
    _seed_schema7_database(database_path, tmp_path / "connector")
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(DATABASE_PATH=str(database_path)),
    )

    exit_code = cli.main([])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().err) == {
        "error": "operation_failed",
        "mode": "dry_run",
    }
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 7
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(notes)").fetchall()
        }
    assert "source_id" not in columns


def test_acl_backfill_cli_round_trip_uses_runtime_database(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    cli = _load_backfill_cli_module()
    database_path = tmp_path / "product.sqlite3"
    db = ProductDatabase(database_path)
    db.initialize()
    _insert_note(db, vault_path="missing-policy.md")
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(DATABASE_PATH=str(database_path)),
    )

    assert cli.main([]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run == {
        "changed_count": 1,
        "mode": "dry_run",
        "note_count": 1,
        "unchanged_count": 0,
        "unresolved_count": 1,
    }
    assert db.fetch_one("SELECT acl_public FROM notes WHERE note_id='n1'") == {
        "acl_public": 1
    }
    assert db.fetch_one("SELECT COUNT(*) AS count FROM acl_backfill_runs") == {"count": 0}

    assert cli.main(["--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["mode"] == "apply"
    assert applied["changed"] == 1
    assert applied["unresolved_count"] == 1
    assert set(applied) == {"changed", "mode", "run_id", "unchanged", "unresolved_count"}
    assert db.fetch_one("SELECT acl_public FROM notes WHERE note_id='n1'") == {
        "acl_public": 0
    }

    assert cli.main(["--rollback", applied["run_id"]]) == 0
    rolled_back = json.loads(capsys.readouterr().out)
    assert rolled_back == {
        "mode": "rollback",
        "restored": 1,
        "run_id": applied["run_id"],
    }
    assert db.fetch_one("SELECT acl_public FROM notes WHERE note_id='n1'") == {
        "acl_public": 1
    }


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
    stored_acl = acl if acl is not None else {"allow": ["public:legacy"]}
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
            json.dumps(stored_acl),
            acl_public,
        ),
    )


def _seed_schema7_database(path: Path, connector_root: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            INSERT INTO schema_meta(version) VALUES (7);
            CREATE TABLE notes (
                note_id TEXT PRIMARY KEY,
                vault_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                frontmatter_json TEXT NOT NULL DEFAULT '{}',
                ai_access_level TEXT NOT NULL DEFAULT 'local_only',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                index_status TEXT NOT NULL DEFAULT 'pending',
                index_version TEXT,
                workspace TEXT,
                department TEXT,
                acl_json TEXT NOT NULL DEFAULT '{}',
                acl_public INTEGER NOT NULL DEFAULT 0,
                policy_key TEXT,
                owner TEXT,
                document_version TEXT,
                effective_from TEXT,
                effective_to TEXT,
                policy_status TEXT NOT NULL DEFAULT 'unspecified',
                metadata_issues_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_indexed_at TEXT
            );
            CREATE TABLE connector_syncs (
                connector_id TEXT PRIMARY KEY,
                connector_type TEXT NOT NULL,
                source_path TEXT NOT NULL,
                workspace TEXT,
                department TEXT,
                status TEXT NOT NULL,
                file_count INTEGER NOT NULL DEFAULT 0,
                added INTEGER NOT NULL DEFAULT 0,
                updated INTEGER NOT NULL DEFAULT 0,
                pruned INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            INSERT INTO notes (
                note_id, vault_path, title, content_hash, created_at, updated_at
            ) VALUES
                ('n1', 'dir-abc/finance/policy.md', 'Connector policy', 'hash-1', 'now', 'now'),
                ('n2', 'builtin.md', 'Built-in policy', 'hash-2', 'now', 'now');
            """
        )
        connection.execute(
            "INSERT INTO connector_syncs "
            "(connector_id, connector_type, source_path, status, started_at, finished_at) "
            "VALUES ('dir-abc', 'markdown_directory', ?, 'completed', 'start', 'finish')",
            (str(connector_root),),
        )


def test_initialize_migrates_real_schema7_and_backfills_source_ids(tmp_path: Path):
    database_path = tmp_path / "app.db"
    _seed_schema7_database(database_path, tmp_path / "connector")
    db = ProductDatabase(database_path)

    db.initialize()

    assert db.fetch_one("SELECT source_id FROM notes WHERE note_id='n1'")["source_id"] == "dir-abc"
    assert db.fetch_one("SELECT source_id FROM notes WHERE note_id='n2'")["source_id"] == "builtin"
    assert db.fetch_one("SELECT version FROM schema_meta")["version"] == 8


def test_initialize_backfills_source_ids_when_schema_meta_is_empty(tmp_path: Path):
    database_path = tmp_path / "app.db"
    _seed_schema7_database(database_path, tmp_path / "connector")
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM schema_meta")
    db = ProductDatabase(database_path)

    db.initialize()

    assert db.fetch_one("SELECT source_id FROM notes WHERE note_id='n1'")["source_id"] == "dir-abc"
    assert db.fetch_one("SELECT version FROM schema_meta") == {"version": 8}


def test_initialize_does_not_rewrite_source_id_after_schema8(tmp_path: Path):
    database_path = tmp_path / "app.db"
    _seed_schema7_database(database_path, tmp_path / "connector")
    db = ProductDatabase(database_path)
    db.initialize()
    db.execute("UPDATE connector_syncs SET status='failed' WHERE connector_id='dir-abc'")

    db.initialize()

    assert db.fetch_one("SELECT source_id FROM notes WHERE note_id='n1'")["source_id"] == "dir-abc"


def test_future_schema9_upgrade_does_not_rerun_source_ownership_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database_path = tmp_path / "app.db"
    _seed_schema7_database(database_path, tmp_path / "connector")
    db = ProductDatabase(database_path)
    db.initialize()
    db.execute("UPDATE connector_syncs SET status='failed' WHERE connector_id='dir-abc'")
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 9)

    db.initialize()

    assert db.fetch_one("SELECT source_id FROM notes WHERE note_id='n1'")["source_id"] == "dir-abc"
    assert db.fetch_one("SELECT version FROM schema_meta") == {"version": 9}


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


def test_changed_apply_is_unchanged_on_second_run(tmp_path: Path):
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db)
    service = AclBackfillService(db, tmp_path / "knowledge")

    first = service.apply()
    second = service.apply()

    assert first["changed"] == 1
    assert second["changed"] == 0
    assert db.fetch_one(
        "SELECT action FROM acl_backfill_items WHERE run_id=? AND note_id='n1'",
        (second["run_id"],),
    )["action"] == "unchanged"


@pytest.mark.parametrize("stored_acl", ["not-json", "[]"])
def test_invalid_or_array_acl_json_is_normalized_and_audited_as_updated(
    tmp_path: Path,
    stored_acl: str,
):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("# policy", encoding="utf-8")
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(
        db,
        vault_path="policy.md",
        workspace=None,
        department=None,
        frontmatter={"acl": {}},
        acl={},
        acl_public=0,
    )
    db.execute("UPDATE notes SET acl_json=? WHERE note_id='n1'", (stored_acl,))
    service = AclBackfillService(db, root)

    planned = service.plan()["items"][0]
    applied = service.apply()

    assert planned["action"] == "updated"
    assert db.fetch_one("SELECT acl_json FROM notes WHERE note_id='n1'")["acl_json"] == "{}"
    assert db.fetch_one(
        "SELECT action, old_acl_json FROM acl_backfill_items WHERE run_id=? AND note_id='n1'",
        (applied["run_id"],),
    ) == {"action": "updated", "old_acl_json": stored_acl}


def test_semantically_equal_object_acl_json_is_unchanged(tmp_path: Path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("# policy", encoding="utf-8")
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(
        db,
        vault_path="policy.md",
        workspace=None,
        department=None,
        frontmatter={"acl": {"deny": [], "allow": []}},
        acl={},
        acl_public=0,
    )
    db.execute(
        "UPDATE notes SET acl_json=' { \"allow\" : [], \"deny\" : [] } ' WHERE note_id='n1'"
    )

    planned = AclBackfillService(db, root).plan()["items"][0]

    assert planned["action"] == "unchanged"


def test_rollback_rejects_stale_run_without_overwriting_newer_acl(tmp_path: Path):
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "policy.md").write_text("# policy", encoding="utf-8")
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(
        db,
        vault_path="policy.md",
        workspace=None,
        department=None,
        frontmatter={"acl": {"allow": ["department:finance"]}},
        acl={},
        acl_public=0,
    )
    service = AclBackfillService(db, root)
    run1 = service.apply()
    db.execute(
        "UPDATE notes SET frontmatter_json=? WHERE note_id='n1'",
        (json.dumps({"acl": {"allow": ["department:legal"]}}),),
    )
    run2 = service.apply()

    with pytest.raises(ValueError, match="rollback conflict"):
        service.rollback(run1["run_id"])

    assert json.loads(db.fetch_one("SELECT acl_json FROM notes WHERE note_id='n1'")["acl_json"]) == {
        "allow": ["department:legal"]
    }
    assert db.fetch_one(
        "SELECT status FROM acl_backfill_runs WHERE run_id=?", (run1["run_id"],)
    )["status"] == "completed"
    assert db.fetch_one(
        "SELECT status FROM acl_backfill_runs WHERE run_id=?", (run2["run_id"],)
    )["status"] == "completed"


@pytest.mark.parametrize("vault_path", ["../outside/policy.md", "/outside/policy.md"])
def test_builtin_path_escape_is_unresolved(tmp_path: Path, vault_path: str):
    root = tmp_path / "knowledge"
    root.mkdir()
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db, vault_path=vault_path)

    planned = AclBackfillService(db, root).plan()["items"][0]

    assert planned["reason"] == "source_unavailable"
    assert planned["acl"] == {"allow": [], "backfill_reason": "source_unavailable"}
    assert planned["acl_public"] == 0


def test_builtin_windows_absolute_path_is_unresolved(tmp_path: Path):
    root = tmp_path / "knowledge"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db, vault_path=str(outside.resolve()))

    planned = AclBackfillService(db, root).plan()["items"][0]

    assert planned["reason"] == "source_unavailable"
    assert planned["acl_public"] == 0


def test_builtin_symlink_escape_is_unresolved(tmp_path: Path):
    root = tmp_path / "knowledge"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    link = root / "escape.md"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db, vault_path="escape.md")

    planned = AclBackfillService(db, root).plan()["items"][0]

    assert planned["reason"] == "source_unavailable"
    assert planned["acl_public"] == 0


def test_completed_connector_root_resolves_and_unavailable_root_does_not(tmp_path: Path):
    connector_root = tmp_path / "connector"
    (connector_root / "finance").mkdir(parents=True)
    (connector_root / "finance" / "policy.md").write_text("# policy", encoding="utf-8")
    (connector_root / "finance" / "acl.json").write_text(
        json.dumps({"workspace": "corp", "allow": ["workspace:corp"]}),
        encoding="utf-8",
    )
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    for connector_id, source_path in (
        ("dir-live", connector_root),
        ("dir-missing", tmp_path / "missing"),
    ):
        db.execute(
            "INSERT INTO connector_syncs "
            "(connector_id, connector_type, source_path, status, started_at, finished_at) "
            "VALUES (?, 'markdown_directory', ?, 'completed', 'start', 'finish')",
            (connector_id, str(source_path)),
        )
    _insert_note(
        db,
        note_id="live",
        vault_path="dir-live/finance/policy.md",
        source_id="dir-live",
    )
    _insert_note(
        db,
        note_id="missing",
        vault_path="dir-missing/finance/policy.md",
        source_id="dir-missing",
    )

    plan = AclBackfillService(db, tmp_path / "unused-builtin").plan()
    decisions = {item["note_id"]: item for item in plan["items"]}

    assert decisions["live"]["reason"] == "controlled_default"
    assert decisions["live"]["workspace"] == "corp"
    assert decisions["missing"]["reason"] == "source_unavailable"
    assert decisions["missing"]["acl_public"] == 0


def test_apply_trigger_failure_rolls_back_notes_and_audit_run(tmp_path: Path):
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db, note_id="n1", vault_path="one.md")
    _insert_note(db, note_id="n2", vault_path="two.md")
    db.execute(
        "CREATE TRIGGER fail_second_audit BEFORE INSERT ON acl_backfill_items "
        "WHEN NEW.note_id='n2' BEGIN SELECT RAISE(ABORT, 'audit failure'); END"
    )
    service = AclBackfillService(db, tmp_path / "knowledge")

    with pytest.raises(sqlite3.IntegrityError, match="audit failure"):
        service.apply()

    notes = db.fetch_all("SELECT note_id, acl_public FROM notes ORDER BY note_id")
    assert notes == [{"note_id": "n1", "acl_public": 1}, {"note_id": "n2", "acl_public": 1}]
    assert db.fetch_one("SELECT COUNT(*) AS count FROM acl_backfill_runs")["count"] == 0
    assert db.fetch_one("SELECT COUNT(*) AS count FROM acl_backfill_items")["count"] == 0


def test_rollback_trigger_failure_rolls_back_all_restores(tmp_path: Path):
    db = ProductDatabase(tmp_path / "app.db")
    db.initialize()
    _insert_note(db, note_id="n1", vault_path="one.md")
    _insert_note(db, note_id="n2", vault_path="two.md")
    service = AclBackfillService(db, tmp_path / "knowledge")
    applied = service.apply()
    db.execute(
        "CREATE TRIGGER fail_second_restore BEFORE UPDATE ON notes "
        "WHEN OLD.note_id='n2' BEGIN SELECT RAISE(ABORT, 'restore failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="restore failure"):
        service.rollback(applied["run_id"])

    notes = db.fetch_all("SELECT note_id, acl_public FROM notes ORDER BY note_id")
    assert notes == [{"note_id": "n1", "acl_public": 0}, {"note_id": "n2", "acl_public": 0}]
    assert db.fetch_one(
        "SELECT status FROM acl_backfill_runs WHERE run_id=?", (applied["run_id"],)
    )["status"] == "completed"


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
