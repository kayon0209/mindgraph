import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import config
from infrastructure.database import ProductDatabase
from application.vault_sync_service import VaultSyncService

db = ProductDatabase(ROOT / "data" / "product" / "product.sqlite3")
db.initialize()
print("before notes:", db.fetch_one("SELECT COUNT(*) as c FROM notes")["c"])
vault = ROOT / "demo-vault"
print("sync vault:", vault, "exists", vault.exists(), "files", len(list(vault.rglob('*.md'))))
svc = VaultSyncService(db, vault, write_ids=True)
res = svc.scan_vault(prune_missing=False)
print(f"scanned {len(res.scanned)} skipped {len(res.skipped)} errors {len(res.errors)} pruned {res.pruned}")
for n in res.scanned:
    print(" ", n.vault_path, n.title[:40], n.index_status if hasattr(n,'index_status') else '?')
print("after notes:", db.fetch_one("SELECT COUNT(*) as c FROM notes")["c"])
for row in db.fetch_all("SELECT vault_path, index_status FROM notes WHERE vault_path LIKE 'policies/%' LIMIT 10"):
    print(" policy", dict(row))
