"""本地目录 / Markdown 目录增量同步连接器测试（Phase 5-2）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from application.directory_connector_service import DirectoryConnectorService
from application.vault_sync_service import VaultSyncService
from infrastructure.database import ProductDatabase


def _make_source(tmp_path: Path) -> Path:
    source = tmp_path / "corp"
    (source / "finance").mkdir(parents=True)
    (source / "hr").mkdir(parents=True)
    (source / "finance" / "expense.md").write_text(
        """---
owner: 财务部
policy_key: expense.general
version: "1.0"
status: active
effective_from: 2026-01-01
workspace: corp
department: finance
---
# 费用制度
报销应在 30 日内提交。
""",
        encoding="utf-8",
    )
    (source / "hr" / "leave.md").write_text(
        """---
owner: 人力资源部
policy_from: hr.leave
version: "1.0"
status: active
effective_from: 2026-01-01
---
# 请假制度
年假应在年初申报。
""",
        encoding="utf-8",
    )
    # 根 ACL 声明
    (source / "acl.json").write_text(
        json.dumps({"workspaces": {"corp": {"acl": {"allow": ["workspace:corp"]}}}}),
        encoding="utf-8",
    )
    return source


def test_directory_connector_sync_writes_workspace_department_and_acl(tmp_path: Path):
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_source(tmp_path)

    svc = DirectoryConnectorService(database, source, index_service=None)
    result = svc.sync(source, workspace="corp", trigger_index=False)

    assert result["status"] == "completed"
    assert result["file_count"] == 2
    # connector_syncs 审计行
    sync_row = database.fetch_one("SELECT * FROM connector_syncs WHERE connector_id=?", (result["connector_id"],))
    assert sync_row["status"] == "completed"
    assert sync_row["file_count"] == 2

    # finance 笔记带 workspace/department
    finance = database.fetch_one("SELECT workspace, department, acl_json FROM notes WHERE vault_path LIKE '%expense.md'")
    assert finance["workspace"] == "corp"
    assert finance["department"] == "finance"
    assert "workspace:corp" in finance["acl_json"]

    # hr 笔记通过目录结构回填 department=hr
    hr = database.fetch_one("SELECT workspace, department FROM notes WHERE vault_path LIKE '%leave.md'")
    assert hr["workspace"] == "corp"
    assert hr["department"] == "hr"


def test_directory_connector_incremental_sync_detects_changes(tmp_path: Path):
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_source(tmp_path)
    svc = DirectoryConnectorService(database, source, index_service=None)

    first = svc.sync(source, workspace="corp")
    assert first["added"] >= 1

    # 修改文件内容 → 应触发 pending
    expense = source / "finance" / "expense.md"
    expense.write_text(expense.read_text(encoding="utf-8") + "\n\n新增条款。", encoding="utf-8")

    second = svc.sync(source, workspace="corp")
    assert second["status"] == "completed"
    note = database.fetch_one("SELECT index_status FROM notes WHERE vault_path LIKE '%expense.md'")
    assert note["index_status"] == "pending"


def test_directory_connector_prunes_deleted_owned_note(tmp_path: Path):
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_source(tmp_path)
    svc = DirectoryConnectorService(database, source, index_service=None)

    svc.sync(source, workspace="corp")
    (source / "hr" / "leave.md").unlink()

    second = svc.sync(source, workspace="corp")
    assert second["pruned"] == 1
    remaining = database.fetch_one("SELECT COUNT(*) AS c FROM notes WHERE vault_path LIKE '%leave.md'")
    assert remaining["c"] == 0


def test_directory_connector_status_returns_history(tmp_path: Path):
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_source(tmp_path)
    svc = DirectoryConnectorService(database, source, index_service=None)

    svc.sync(source, workspace="corp")
    history = svc.status()
    assert len(history) == 1
    assert history[0]["status"] == "completed"


def _make_single_note_source(root: Path, source_name: str, note_id: str) -> Path:
    source = root / source_name
    source.mkdir()
    (source / "policy.md").write_text(
        f"---\nmindgraph_id: {note_id}\n---\n# {source_name}\nPolicy text.\n",
        encoding="utf-8",
    )
    return source


def test_syncing_second_connector_does_not_delete_first_source(tmp_path: Path):
    """Restoring generic prune for connectors would delete another source."""
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source_a = _make_single_note_source(tmp_path, "source-a", "note-a")
    source_b = _make_single_note_source(tmp_path, "source-b", "note-b")
    svc = DirectoryConnectorService(
        database,
        tmp_path,
        index_service=None,
        allowed_roots=(tmp_path,),
    )

    svc.sync(source_a, connector_id="connector-a")
    svc.sync(source_b, connector_id="connector-b")

    rows = database.fetch_all("SELECT note_id FROM notes ORDER BY note_id")
    assert [row["note_id"] for row in rows] == ["note-a", "note-b"]


def test_connector_sync_does_not_modify_source_markdown(tmp_path: Path):
    """External connector identity belongs in MindGraph, not source files."""
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = tmp_path / "external"
    source.mkdir()
    note_path = source / "policy.md"
    original = b"# External policy\nDo not rewrite this file.\n"
    note_path.write_bytes(original)
    svc = DirectoryConnectorService(
        database,
        source,
        index_service=None,
        allowed_roots=(source,),
    )

    svc.sync(source)

    assert note_path.read_bytes() == original


def test_connector_rejects_source_outside_allowed_roots(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = _make_single_note_source(tmp_path, "outside", "outside-note")
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    svc = DirectoryConnectorService(
        database,
        allowed,
        index_service=None,
        allowed_roots=(allowed,),
    )

    with pytest.raises(ValueError, match="outside configured connector roots"):
        svc.sync(outside)


def test_connector_rejects_symlink_escape(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = _make_single_note_source(tmp_path, "outside", "outside-note")
    link = allowed / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    svc = DirectoryConnectorService(
        database,
        allowed,
        index_service=None,
        allowed_roots=(allowed,),
    )

    with pytest.raises(ValueError, match="outside configured connector roots"):
        svc.sync(link)


def test_vault_sync_missing_root_does_not_prune_existing_notes(tmp_path: Path):
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_single_note_source(tmp_path, "existing", "existing-note")
    VaultSyncService(database, source, write_ids=False).scan_vault()

    with pytest.raises(ValueError, match="Vault path is not a directory"):
        VaultSyncService(database, tmp_path / "missing", write_ids=False).scan_vault()

    assert database.fetch_one("SELECT COUNT(*) AS c FROM notes")["c"] == 1


def test_vault_sync_scan_error_does_not_prune_failed_note(tmp_path: Path):
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_single_note_source(tmp_path, "existing", "existing-note")
    note_path = source / "policy.md"
    VaultSyncService(database, source, write_ids=False).scan_vault()
    note_path.write_bytes(b"\xff\xfe\x00")

    result = VaultSyncService(database, source, write_ids=False).scan_vault()

    assert result.errors
    assert result.pruned == 0
    assert database.fetch_one("SELECT COUNT(*) AS c FROM notes")["c"] == 1
