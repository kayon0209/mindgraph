from pathlib import Path
import subprocess
import sys

from application.mindgraph_index_service import MindGraphIndexService
from application.vault_sync_service import VaultSyncService
from infrastructure.database import ProductDatabase


class FakeEmbeddingProvider:
    model_name = "fake"
    model_revision = "test"
    dimension = 2


def test_excluded_vault_note_is_not_added_to_index(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "public.md").write_text("# 公开制度\n可以进入索引。", encoding="utf-8")
    (vault / "private.md").write_text(
        "---\nai_access_level: excluded\n---\n# 私密制度\n不得进入索引。",
        encoding="utf-8",
    )

    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    VaultSyncService(database, vault, write_ids=True).scan_vault()

    service = MindGraphIndexService(
        database,
        vault,
        tmp_path / "indexes",
        provider=FakeEmbeddingProvider(),
    )
    chunks = service._all_chunks()

    assert {chunk.metadata["title"] for chunk in chunks} == {"公开制度"}


def test_offline_validator_uses_governed_production_factory() -> None:
    root = Path(__file__).resolve().parent.parent

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "validate_mindgraph_offline.py"),
            "--vault",
            str(root / "demo-vault"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "[PASS]" in completed.stdout
