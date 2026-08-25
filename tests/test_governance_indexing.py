from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from application.directory_connector_service import DirectoryConnectorService
from application.governance_policy import GovernancePolicy
from application.governance_reconciliation_service import GovernanceReconciliationService
from application.mindgraph_index_service import MindGraphIndexService
from application.mindgraph_sync_watcher import MindGraphSyncWatcher
from application.vault_sync_service import VaultScanResult, VaultSyncService
from domain.errors import GovernanceUnavailableError
from infrastructure.database import ProductDatabase


class FakeEmbeddingProvider:
    model_name = "fake"
    model_revision = "test"
    dimension = 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(index + 1)] for index, _ in enumerate(texts)]


def _note(note_id: str, *, status: str = "active", effective_to: str | None = None) -> str:
    until = f"effective_to: {effective_to}\n" if effective_to else ""
    return (
        "---\n"
        f"mindgraph_id: {note_id}\n"
        "owner: Finance\n"
        "policy_key: expense.general\n"
        "version: '1.0'\n"
        f"status: {status}\n"
        "effective_from: 2026-01-01\n"
        f"{until}"
        "workspace: corp\n"
        "department: finance\n"
        "---\n"
        "# Expense policy\n"
        "Eligible policy text.\n"
    )


@pytest.fixture
def governance_index_service(tmp_path: Path) -> MindGraphIndexService:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "current.md").write_text(_note("current-note"), encoding="utf-8")
    (vault / "draft.md").write_text(_note("draft-note", status="draft"), encoding="utf-8")
    (vault / "expired.md").write_text(
        _note("expired-note", effective_to="2026-08-24"), encoding="utf-8"
    )
    (vault / "superseded.md").write_text(
        _note("superseded-note", status="superseded"), encoding="utf-8"
    )
    (vault / "unresolved.md").write_text(
        "---\nmindgraph_id: unresolved-note\nstatus: active\n---\n# Incomplete\nNo owner.\n",
        encoding="utf-8",
    )
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    return MindGraphIndexService(
        database,
        vault,
        tmp_path / "indexes",
        provider=FakeEmbeddingProvider(),
        governance_reconciler=GovernanceReconciliationService(database, GovernancePolicy()),
    )


def _active_chunks(service: MindGraphIndexService, manifest: dict[str, object]) -> list[dict[str, object]]:
    version = str(manifest["index_version"])
    return json.loads((service.index_root / version / "chunks.json").read_text(encoding="utf-8"))


def test_index_excludes_draft_expired_superseded_and_unresolved_notes(
    governance_index_service: MindGraphIndexService,
) -> None:
    manifest = governance_index_service.build(force=True)
    chunks = _active_chunks(governance_index_service, manifest)

    assert {chunk["document_id"] for chunk in chunks} == {"current-note"}
    assert manifest["eligible_note_count"] == 1
    assert manifest["excluded_reason_counts"] == {
        "declared_draft": 1,
        "declared_superseded": 1,
        "effective_period_ended": 1,
        "missing_effective_from": 1,
        "missing_owner": 1,
        "missing_policy_key": 1,
        "missing_version": 1,
    }


def test_equivalent_aliases_index_once_and_retain_safe_citation_ids(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "canonical.md").write_text(_note("canonical-note"), encoding="utf-8")
    (vault / "alias.md").write_text(_note("z-alias-note"), encoding="utf-8")
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    service = MindGraphIndexService(
        database,
        vault,
        tmp_path / "indexes",
        provider=FakeEmbeddingProvider(),
        governance_reconciler=GovernanceReconciliationService(database, GovernancePolicy()),
    )

    manifest = service.build(force=True)
    chunks = _active_chunks(service, manifest)
    canonical = [chunk for chunk in chunks if chunk["document_id"] == "canonical-note"]

    assert canonical
    assert canonical[0]["metadata"]["equivalent_note_ids"] == ["canonical-note", "z-alias-note"]
    assert {chunk["document_id"] for chunk in chunks} == {"canonical-note"}


def test_reconciliation_failure_blocks_new_index_activation(
    governance_index_service: MindGraphIndexService,
) -> None:
    previous = governance_index_service.build(force=True)["index_version"]

    class FailingReconciler:
        policy = GovernancePolicy()

        def reconcile(self, *, as_of: date):
            raise RuntimeError("governance unavailable")

    governance_index_service.governance_reconciler = FailingReconciler()

    with pytest.raises(GovernanceUnavailableError):
        governance_index_service.build(force=True)
    assert governance_index_service.current_version() == previous


def test_failed_connector_scan_neither_prunes_reconciles_nor_builds(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()

    class FailedScan:
        def scan_vault(self, *, prune_missing: bool = True) -> VaultScanResult:
            assert prune_missing
            return VaultScanResult([], [], ["policy.md: unreadable"], 0)

    class FailIfCalled:
        def __getattr__(self, _name: str):
            raise AssertionError("failed scan must not reach governance or indexing")

    connector = DirectoryConnectorService(
        database,
        source,
        index_service=FailIfCalled(),
        governance_reconciler=FailIfCalled(),
        allowed_roots=(source,),
    )
    connector._sync_service = lambda _source, _connector_id: FailedScan()  # type: ignore[method-assign]

    result = connector.sync(source, trigger_index=True)

    assert result["pruned"] == 0
    assert result["status"] == "failed"


def test_watcher_reconciles_before_build() -> None:
    marker: dict[str, bool] = {"reconciled": False}

    class Scan:
        def scan_vault(self) -> VaultScanResult:
            return VaultScanResult([], [], [], 0)

    class Reconciler:
        def reconcile(self, *, as_of: date):
            marker["reconciled"] = True

    class Index:
        def has_pending(self) -> bool:
            return True

        def build(self, *, force: bool = False, governance_reconciled: bool = False) -> dict[str, object]:
            assert force is False
            assert governance_reconciled is True
            assert marker["reconciled"] is True
            return {"status": "validated", "index_version": "v1", "reused_embeddings": 0, "new_embeddings": 1}

    watcher = MindGraphSyncWatcher(
        Scan(), Index(), governance_reconciler=Reconciler(), debounce=0
    )

    result = watcher.run_once()

    assert result["build"]["status"] == "validated"
