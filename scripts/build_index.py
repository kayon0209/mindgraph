import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from infrastructure.database import ProductDatabase
from application.mindgraph_index_service import MindGraphIndexService
import config

db = ProductDatabase(Path(config.ROOT) / "data" / "product" / "product.sqlite3")
db.initialize()
pending = db.fetch_one("SELECT COUNT(*) as c FROM notes WHERE index_status IN ('pending','failed')")["c"]
print(f"pending notes: {pending}")
for row in db.fetch_all("SELECT vault_path, index_status FROM notes WHERE index_status IN ('pending','failed') LIMIT 20"):
    print(" pending", dict(row))

svc = MindGraphIndexService(db, Path(config.ROOT) / "knowledge", Path(config.ROOT) / "data" / "retrieval_indexes")
result = svc.build(force=False)
print("build result:", result)
if result.get("status") != "noop":
    print("index version:", result.get("index_version") or result.get("version"))
    cur = Path(config.ROOT) / "data" / "retrieval_indexes" / "CURRENT"
    if cur.exists():
        print("CURRENT:", cur.read_text(encoding="utf-8").strip())

# verify external chunks present
import json
cur_ver = (Path(config.ROOT) / "data" / "retrieval_indexes" / "CURRENT").read_text(encoding="utf-8").strip()
chunks_path = Path(config.ROOT) / "data" / "retrieval_indexes" / cur_ver / "chunks.json"
if chunks_path.exists():
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    ext = [c for c in chunks if "external" in c.get("metadata",{}).get("vault_path","")]
    print(f"external chunks in index: {len(ext)} / total {len(chunks)}")
    for c in ext[:5]:
        print(" ", c["chunk_id"], c["metadata"]["vault_path"], c["text"][:120].replace("\n"," ")[:120])
else:
    print("no chunks.json at", chunks_path)
