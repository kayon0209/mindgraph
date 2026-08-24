from __future__ import annotations

import json
from pathlib import Path

import faiss
import pytest

from api.dependencies import ServiceContainer
from application.directory_connector_service import DirectoryConnectorService
from application.mindgraph_index_service import MindGraphIndexService
from application.vault_sync_service import VaultSyncService
from infrastructure.database import ProductDatabase


class FakeEmbeddingProvider:
    model_name = "fake"
    model_revision = "test"
    dimension = 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(index + 1)] for index, _text in enumerate(texts)]


def _setup(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "policy.md"
    note.write_text(
        "---\nmindgraph_id: note-1\n---\n# 制度\n原始内容。",
        encoding="utf-8",
    )
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    return database, vault, note


def test_deleting_last_note_activates_an_empty_index(tmp_path: Path) -> None:
    database, vault, note = _setup(tmp_path)
    indexes = tmp_path / "indexes"
    service = MindGraphIndexService(
        database, vault, indexes, provider=FakeEmbeddingProvider()
    )
    first = service.build()

    note.unlink()
    result = VaultSyncService(database, vault, write_ids=False).scan_vault()
    assert result.pruned == 1
    empty = service.build(force=True)

    assert empty["index_version"] != first["index_version"]
    assert empty["chunk_count"] == 0
    assert service.current_version() == empty["index_version"]
    assert faiss.read_index(str(indexes / empty["index_version"] / "dense.faiss")).ntotal == 0
    assert json.loads((indexes / empty["index_version"] / "chunks.json").read_text(encoding="utf-8")) == []


def test_database_failure_does_not_activate_new_current(tmp_path: Path, monkeypatch) -> None:
    database, vault, note = _setup(tmp_path)
    service = MindGraphIndexService(
        database, vault, tmp_path / "indexes", provider=FakeEmbeddingProvider()
    )
    previous = service.build()["index_version"]
    note.write_text(
        "---\nmindgraph_id: note-1\n---\n# 制度\n已修改内容。",
        encoding="utf-8",
    )
    VaultSyncService(database, vault, write_ids=False).scan_vault()

    original_execute_many = database.execute_many

    def fail_ready_update(sql, params_list):
        if "index_status='ready'" in sql:
            raise RuntimeError("database unavailable")
        return original_execute_many(sql, params_list)

    monkeypatch.setattr(database, "execute_many", fail_ready_update)

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.build()

    assert service.current_version() == previous


def test_activation_failure_restores_note_index_state(tmp_path: Path, monkeypatch) -> None:
    database, vault, note = _setup(tmp_path)
    service = MindGraphIndexService(
        database, vault, tmp_path / "indexes", provider=FakeEmbeddingProvider()
    )
    previous = service.build()["index_version"]
    note.write_text(
        "---\nmindgraph_id: note-1\n---\n# 制度\n等待重新索引。",
        encoding="utf-8",
    )
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    before = database.fetch_one(
        "SELECT index_status,index_version,last_indexed_at FROM notes WHERE note_id='note-1'"
    )
    monkeypatch.setattr(
        service, "_activate", lambda _version: (_ for _ in ()).throw(OSError("disk error"))
    )

    with pytest.raises(OSError, match="disk error"):
        service.build()

    after = database.fetch_one(
        "SELECT index_status,index_version,last_indexed_at FROM notes WHERE note_id='note-1'"
    )
    assert service.current_version() == previous
    assert after == before


def test_successful_activation_invalidates_cached_pipelines(tmp_path: Path) -> None:
    database, vault, _note = _setup(tmp_path)
    callbacks: list[str] = []
    service = MindGraphIndexService(
        database,
        vault,
        tmp_path / "indexes",
        provider=FakeEmbeddingProvider(),
        on_activated=lambda: callbacks.append("activated"),
    )

    service.build()

    assert callbacks == ["activated"]


def test_connector_note_is_loaded_from_its_configured_source(tmp_path: Path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    built_in_vault = tmp_path / "knowledge"
    built_in_vault.mkdir()
    external_source = tmp_path / "external-source"
    external_source.mkdir()
    (external_source / "policy.md").write_text(
        "# External policy\nThe connector source must be indexed.\n",
        encoding="utf-8",
    )
    index_service = MindGraphIndexService(
        database,
        built_in_vault,
        tmp_path / "indexes",
        provider=FakeEmbeddingProvider(),
    )
    connector = DirectoryConnectorService(
        database,
        built_in_vault,
        index_service=index_service,
        allowed_roots=(external_source,),
    )

    result = connector.sync(external_source, trigger_index=True)

    assert result["index_version"]
    manifest = json.loads(
        (tmp_path / "indexes" / result["index_version"] / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["chunk_count"] == 1


def test_container_invalidation_clears_both_pipeline_caches() -> None:
    container = ServiceContainer.__new__(ServiceContainer)
    container._pipelines = {5: object()}
    container._mindgraph_pipelines = {(5, True): object()}

    container.invalidate_pipelines()

    assert container._pipelines == {}
    assert container._mindgraph_pipelines == {}
