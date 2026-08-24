from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from application.directory_connector_service import DirectoryConnectorService
from application.mindgraph_index_service import MindGraphIndexService
from application.vault_sync_service import VaultSyncService
from infrastructure.database import ProductDatabase


def _make_note_source(root: Path, name: str, notes: dict[str, str]) -> Path:
    source = root / name
    source.mkdir()
    for filename, note_id in notes.items():
        (source / filename).write_text(
            f"---\nmindgraph_id: {note_id}\n---\n# {name}\n{name} policy text.\n",
            encoding="utf-8",
        )
    return source


def _note_ids(database: ProductDatabase, source_id: str) -> set[str]:
    return {
        row["note_id"]
        for row in database.fetch_all(
            "SELECT note_id FROM notes WHERE source_id=?", (source_id,)
        )
    }


def _insert_note(
    database: ProductDatabase,
    note_id: str,
    vault_path: str,
    source_id: str,
) -> None:
    database.execute(
        "INSERT INTO notes "
        "(note_id, vault_path, source_id, title, content_hash, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'hash', '2026-08-25T00:00:00Z', '2026-08-25T00:00:00Z')",
        (note_id, vault_path, source_id, note_id),
    )


def test_connector_prune_deletes_only_notes_owned_by_same_connector(
    tmp_path: Path,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    builtin = _make_note_source(tmp_path, "builtin", {"policy.md": "local1"})
    source_a = _make_note_source(tmp_path, "source-a", {"policy.md": "a1"})
    source_b = _make_note_source(tmp_path, "source-b", {"policy.md": "b1"})
    VaultSyncService(database, builtin, write_ids=False).scan_vault()
    connector = DirectoryConnectorService(
        database,
        tmp_path,
        index_service=None,
        allowed_roots=(tmp_path,),
    )
    connector.sync(source_a, connector_id="connector-a")
    connector.sync(source_b, connector_id="connector-b")

    (source_a / "policy.md").unlink()
    result = connector.sync(source_a, connector_id="connector-a")

    assert result["pruned"] == 1
    assert _note_ids(database, "connector-a") == set()
    assert _note_ids(database, "connector-b") == {"b1"}
    assert _note_ids(database, "builtin") == {"local1"}


def test_connector_partial_read_failure_prunes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_note_source(
        tmp_path,
        "source",
        {"removed.md": "removed", "unreadable.md": "unreadable"},
    )
    connector = DirectoryConnectorService(
        database,
        source,
        index_service=None,
        allowed_roots=(source,),
    )
    connector.sync(source, connector_id="connector-a")
    database.execute(
        "INSERT INTO note_relations "
        "(relation_id, source_note_id, target_note_id, relation_type, proposed_at) "
        "VALUES ('rel-1', 'removed', 'unreadable', 'supports', '2026-08-25T00:00:00Z')"
    )
    (source / "removed.md").unlink()
    original_process_file = VaultSyncService._process_file

    def fail_for_one_file(self, path, now, seen_ids):
        if path.name == "unreadable.md":
            raise OSError("forced read failure")
        return original_process_file(self, path, now, seen_ids)

    monkeypatch.setattr(VaultSyncService, "_process_file", fail_for_one_file)

    result = connector.sync(source, connector_id="connector-a")

    assert result["errors"]
    assert result["pruned"] == 0
    assert _note_ids(database, "connector-a") == {"removed", "unreadable"}
    assert database.fetch_one(
        "SELECT 1 FROM note_relations WHERE relation_id='rel-1'"
    )


def test_connector_cannot_overwrite_note_owned_by_another_source(
    tmp_path: Path,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source_a = _make_note_source(tmp_path, "source-a", {"policy.md": "shared"})
    source_b = _make_note_source(tmp_path, "source-b", {"policy.md": "shared"})
    connector = DirectoryConnectorService(
        database,
        tmp_path,
        index_service=None,
        allowed_roots=(tmp_path,),
    )
    connector.sync(source_a, connector_id="connector-a")
    before = database.fetch_one("SELECT * FROM notes WHERE note_id='shared'")

    with pytest.raises(ValueError, match="owned by source 'connector-a'"):
        connector.sync(source_b, connector_id="connector-b")

    assert database.fetch_one("SELECT * FROM notes WHERE note_id='shared'") == before


def test_prune_relation_and_note_deletes_are_atomic(tmp_path: Path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = tmp_path / "source"
    source.mkdir()
    _insert_note(database, "owned", "connector-a/owned.md", "connector-a")
    _insert_note(database, "related", "connector-a/related.md", "connector-a")
    database.execute(
        "INSERT INTO note_relations "
        "(relation_id, source_note_id, target_note_id, relation_type, proposed_at) "
        "VALUES ('rel-1', 'owned', 'related', 'supports', '2026-08-25T00:00:00Z')"
    )
    database.execute(
        "CREATE TRIGGER fail_owned_note_delete BEFORE DELETE ON notes "
        "WHEN OLD.note_id='owned' BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    sync = VaultSyncService(
        database,
        source,
        write_ids=False,
        source_id="connector-a",
    )

    with pytest.raises(sqlite3.IntegrityError):
        sync._prune_missing(set())

    assert database.fetch_one("SELECT 1 FROM notes WHERE note_id='owned'")
    assert database.fetch_one(
        "SELECT 1 FROM note_relations WHERE relation_id='rel-1'"
    )


def test_connector_owned_note_uses_its_source_root_and_exposes_source_metadata(
    tmp_path: Path,
) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    builtin = tmp_path / "builtin"
    shadow = builtin / "connector-a"
    shadow.mkdir(parents=True)
    (shadow / "policy.md").write_text(
        "# Shadow policy\nThis built-in shadow must not be indexed.\n",
        encoding="utf-8",
    )
    external = _make_note_source(tmp_path, "external", {"policy.md": "external-1"})
    connector = DirectoryConnectorService(
        database,
        builtin,
        index_service=None,
        allowed_roots=(external,),
    )
    connector.sync(external, connector_id="connector-a")
    note = database.fetch_one("SELECT * FROM notes WHERE note_id='external-1'")
    index_service = MindGraphIndexService(
        database,
        builtin,
        tmp_path / "indexes",
        provider=object(),
    )

    chunks = index_service._load_note_chunks(note)

    assert chunks
    assert "external policy text" in chunks[0].text
    assert "built-in shadow" not in chunks[0].text
    assert chunks[0].metadata["source_id"] == "connector-a"


def test_pruned_connector_sync_forces_requested_index_build(tmp_path: Path) -> None:
    class RecordingIndex:
        def __init__(self) -> None:
            self.force_values: list[bool] = []

        def build(self, *, force: bool = False) -> dict[str, str]:
            self.force_values.append(force)
            return {"index_version": "test-version"}

    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    source = _make_note_source(tmp_path, "source", {"policy.md": "owned"})
    index = RecordingIndex()
    connector = DirectoryConnectorService(
        database,
        source,
        index_service=index,
        allowed_roots=(source,),
    )
    connector.sync(source, connector_id="connector-a")
    (source / "policy.md").unlink()

    result = connector.sync(
        source,
        connector_id="connector-a",
        trigger_index=True,
    )

    assert result["pruned"] == 1
    assert index.force_values == [True]
