from __future__ import annotations

import json
from pathlib import Path

import pytest

from application.source_ownership_service import SourceOwnershipService
from domain.source_ownership import SourceOwnershipError
from infrastructure.database import ProductDatabase

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _database(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "ownership.sqlite3")
    database.initialize()
    return database


def _insert_note(
    database: ProductDatabase,
    *,
    note_id: str,
    source_id: str | None,
    source_path: str | None,
    acl_json: str = "{}",
) -> None:
    database.execute(
        "INSERT INTO notes (note_id, vault_path, title, content_hash, frontmatter_json, ai_access_level, "
        "source_id, source_path, acl_json, acl_public, created_at, updated_at) "
        "VALUES (?, ?, 'Title', 'hash', '{\"secret\":\"body\"}', 'local_only', ?, ?, ?, 0, 't', 't')",
        (note_id, f"{note_id}.md", source_id, source_path, acl_json),
    )


def test_register_directory_source_rejects_overlapping_root(tmp_path: Path):
    database = _database(tmp_path)
    root = tmp_path / "sources"
    child = root / "finance"
    child.mkdir(parents=True)
    service = SourceOwnershipService(database)

    service.register_directory_source("connector-root", root)

    with pytest.raises(SourceOwnershipError, match="source_root_overlap"):
        service.register_directory_source("connector-child", child)


def test_dry_run_audit_persists_redacted_findings_without_mutating_notes(tmp_path: Path):
    database = _database(tmp_path)
    root = tmp_path / "source"
    root.mkdir()
    service = SourceOwnershipService(database)
    service.register_directory_source("connector-a", root)
    _insert_note(database, note_id="invalid", source_id="connector-a", source_path="connector-a/a.md", acl_json="{not-json")
    _insert_note(database, note_id="outside", source_id="connector-a", source_path="other/a.md")
    _insert_note(database, note_id="missing", source_id=None, source_path=None)
    _insert_note(database, note_id="unknown", source_id="gone", source_path="gone/a.md")

    result = service.dry_run_audit()

    assert result.status == "needs_review"
    assert result.finding_count == 4
    assert database.fetch_one("SELECT acl_json FROM notes WHERE note_id='invalid'")["acl_json"] == "{not-json"
    codes = {
        row["reason_code"]
        for row in database.fetch_all(
            "SELECT reason_code FROM source_ownership_findings WHERE audit_run_id=?",
            (result.audit_run_id,),
        )
    }
    assert codes == {"invalid_acl_json", "source_path_outside_root", "missing_source_id", "unknown_source_id"}
    audit_metadata = database.fetch_all(
        "SELECT metadata_json FROM source_ownership_findings WHERE audit_run_id=?",
        (result.audit_run_id,),
    )
    assert all("secret" not in row["metadata_json"] for row in audit_metadata)
    assert all("acl_json" not in row["metadata_json"] for row in audit_metadata)


def test_sync_authorization_requires_a_clean_current_audit(tmp_path: Path):
    database = _database(tmp_path)
    root = tmp_path / "source"
    root.mkdir()
    service = SourceOwnershipService(database)
    registered = service.register_directory_source("connector-a", root)

    with pytest.raises(SourceOwnershipError, match="audit_required"):
        service.require_sync_authorized("connector-a", root)

    result = service.dry_run_audit(registered.source_id)

    assert result.status == "clean"
    assert service.require_sync_authorized("connector-a", root).source_id == "connector-a"
    assert json.loads(database.fetch_one("SELECT summary_json FROM source_ownership_audit_runs WHERE audit_run_id=?", (result.audit_run_id,))["summary_json"]) == {"finding_count": 0}


def test_source_lookup_rejects_an_unknown_persisted_source_status(tmp_path: Path):
    database = _database(tmp_path)
    root = tmp_path / "source"
    root.mkdir()
    service = SourceOwnershipService(database)
    service.register_directory_source("connector-a", root)
    database.execute("PRAGMA ignore_check_constraints=ON")
    database.execute("UPDATE knowledge_sources SET status='unexpected' WHERE connector_id='connector-a'")

    with pytest.raises(SourceOwnershipError, match="source_status_invalid"):
        service.source_for_connector("connector-a")


def test_operator_docs_state_source_ownership_safety_contract():
    for relative_path in ("README.md", "README.zh-CN.md", "docs/DEPLOYMENT.md"):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "schema v16" in text
        assert "dry-run" in text
        assert "clean audit" in text
        assert "directory-root task" in text


def test_readmes_state_agent_capabilities_and_safety_gates():
    hero_path = PROJECT_ROOT / "assets" / "hero-governed-source-flow-v2.png"
    assert hero_path.is_file()

    for relative_path in ("README.md", "README.zh-CN.md"):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "assets/hero-governed-source-flow-v2.png" in text
        assert "AGENT_TASKS_ENABLED" in text
        assert "ASSIST_MCP_ENABLED" in text
        assert "AGENT_WRITE_TOOLS_ENABLED" in text
        assert "mindgraph_save_artifact" in text
        assert "eight read-only" in text
        assert "directory-root task" in text
