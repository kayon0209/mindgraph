import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from infrastructure.database import ProductDatabase
from application.vault_sync_service import VaultSyncService
import config

ROOT = config.ROOT
db = ProductDatabase(ROOT / "data" / "product" / "product.sqlite3")
db.initialize()
print("DB notes before:", db.fetch_one("SELECT COUNT(*) as c FROM notes")["c"])

vault = ROOT / "knowledge"
print("vault", vault, vault.exists())
print("vault files", [str(p.relative_to(ROOT)) for p in vault.rglob("*") if p.is_file()][:20])

svc = VaultSyncService(db, vault, write_ids=True)
result = svc.scan_vault(prune_missing=False)
print("scanned", len(result.scanned))
for n in result.scanned[:20]:
    print(" ", n.vault_path, n.note_id[:8], repr(n.title[:50]))
print("skipped", result.skipped[:5])
print("errors", result.errors[:5])
print("pruned", result.pruned)
print("DB notes after:", db.fetch_one("SELECT COUNT(*) as c FROM notes")["c"])
q = "SELECT vault_path, title, index_status FROM notes WHERE vault_path LIKE '%external%'"
for row in db.fetch_all(q):
    print("EXT note", dict(row))
