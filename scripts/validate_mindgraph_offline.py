"""离线全链路验证（Markdown Vault 副本 + Fake 嵌入/Fake LLM）。

目的：在不依赖 HuggingFace 网络与真实 LLM 的前提下，默认用仓库内可公开的
``demo-vault`` 驱动 M1-D1~D4 的真实生产类：
  D2 VaultSyncService.scan_vault   -> 注入 mindgraph_id + 写 notes 表
  D3 MindGraphIndexService.build   -> 增量索引（Fake 嵌入，其余逻辑全真）
  D4 MindGraphRetrievalPipeline     -> hybrid + 一跳图谱扩展（图谱开关消融）
      ChatService.answer           -> 可信问答（Fake LLM）

全程只读写临时目录，绝不触碰原 Vault。
"""
from __future__ import annotations

import argparse
import atexit
from datetime import date
import hashlib
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = ROOT / "demo-vault"
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402


# --------------------------------------------------------------------------- #
# 1) Fake 嵌入：bag-of-chars 向量（共享字符 -> 高余弦），使检索语义可信
# --------------------------------------------------------------------------- #
class FakeEmbeddingProvider:
    model_name = "fake-bge-zh-v1.5"
    model_revision = "demo"
    dimension = 512

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)

    def _vec(self, text: str):
        v = np.zeros(self.dimension, dtype=np.float32)
        for ch in text:
            if not ch.strip():
                continue
            h = int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16)
            v[h % self.dimension] += 1.0
        norm = np.linalg.norm(v)
        if norm > 0:
            v /= norm
        return v.tolist()


# --------------------------------------------------------------------------- #
# 2) Fake LLM：从检索证据里抽笔记标题，拼出"像样"的回答
# --------------------------------------------------------------------------- #
class FakeChatProvider:
    available = True
    model_name = "fake-llm-demo"
    provider_name = "fake"

    def complete(self, messages):
        ctx = messages[-1]["content"]
        names = dict.fromkeys(re.findall(r"\[citation-\d+\] (.+?) /", ctx))
        answer = "（Demo·离线生成，非真实 LLM）依据以下笔记作答：" + (
            "；".join(names) if names else "未检索到相关笔记"
        )
        usage = {"input_tokens": 24, "output_tokens": 60, "total_tokens": 84, "usage_source": "unavailable"}
        return answer, usage

    def stream(self, messages):
        text, usage = self.complete(messages)
        yield {"delta": text, "usage": usage}


class FakeProviderRegistry:
    def __init__(self):
        self._p = FakeChatProvider()

    def get(self, name=None, model=None):
        return self._p


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="离线验证 MindGraph 同步、索引、关系扩展与问答链路")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT, help="Markdown Vault 路径")
    parser.add_argument("--keep-workdir", action="store_true", help="保留临时工作区用于排查")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.vault.resolve()
    if not source.is_dir():
        print(f"[FAIL] Vault 不存在或不是目录：{source}")
        return 2

    t0 = time.perf_counter()
    work = Path(tempfile.mkdtemp(prefix="mg_realvault_"))
    if not args.keep_workdir:
        atexit.register(shutil.rmtree, work, ignore_errors=True)
    vault_copy = work / "vault"
    db_path = work / "demo.sqlite3"
    index_root = work / "indexes"
    print(f"[setup] 临时工作区: {work}")

    # 复制 Vault（排除虚拟环境/缓存，绝不碰原库）
    IGNORE = {".venv", "node_modules", "__pycache__", ".git", ".obsidian", ".trash"}
    print(f"[setup] 复制 Vault {source} -> {vault_copy}（忽略 {sorted(IGNORE)}）")
    shutil.copytree(source, vault_copy, ignore=shutil.ignore_patterns(*IGNORE), dirs_exist_ok=True)

    # 导入生产类
    from application.chat_service import ChatService  # noqa: E402
    from application.governance_policy import GovernancePolicy
    from application.governance_reconciliation_service import GovernanceReconciliationService
    from application.mindgraph_graph_store import MindGraphGraphStore  # noqa: E402
    from application.mindgraph_index_service import MindGraphIndexService  # noqa: E402
    from application.vault_sync_service import VaultSyncService  # noqa: E402
    from domain.models import ChatRequest  # noqa: E402
    from infrastructure.database import ProductDatabase  # noqa: E402
    import infrastructure.retrieval_factory as rf  # noqa: E402
    from infrastructure.retrieval_factory import create_mindgraph_retrieval_pipeline  # noqa: E402

    # 关键 patch：让 load_current_index 用 Fake 嵌入而非真实 BGE（无网络/无模型）
    rf.BGEEmbeddingProvider = FakeEmbeddingProvider

    db = ProductDatabase(db_path)
    db.initialize()

    # ---------------- D2：扫描 + 注入稳定 ID ---------------- #
    reconciler = GovernanceReconciliationService(db, GovernancePolicy())
    sync = VaultSyncService(db, vault_copy, write_ids=True)
    scan = sync.scan_vault()
    print(f"[D2] 扫描笔记 {len(scan.scanned)} 篇，注入ID {sum(1 for n in scan.scanned if n.id_injected)} 篇，跳过 {len(scan.skipped)}，错误 {len(scan.errors)}")
    if scan.errors:
        print("      扫描错误样本:", scan.errors[:3])
        return 1

    # ---------------- D3：增量索引（Fake 嵌入） ---------------- #
    provider = FakeEmbeddingProvider()
    build_date = date.today()
    reconciler.reconcile(as_of=build_date)
    idx = MindGraphIndexService(
        db,
        vault_copy,
        index_root,
        provider=provider,
        governance_reconciler=reconciler,
    )
    manifest = idx.build(
        force=True,
        build_date=build_date,
        governance_reconciled_as_of=build_date,
    )
    print(f"[D3] 索引构建: version={manifest['index_version']} chunks={manifest['chunk_count']} notes={manifest['note_count']} reused={manifest.get('reused_embeddings')} new={manifest.get('new_embeddings')}")

    graph_store = MindGraphGraphStore(db)

    def pipeline_factory(top_k):
        return create_mindgraph_retrieval_pipeline(
            index_root,
            graph_store,
            db,
            final_top_k=top_k,
            graph_enabled=True,
        )

    chat = ChatService(
        db, pipeline_factory, FakeProviderRegistry(), privacy_log_questions=False,
        system_prompt="你是企业制度与决策依据助手 MindGraph。只能依据给定证据回答；不得编造。先给结论，再给简要依据，并使用 [citation-N] 标注引用来源。",
    )

    notes = db.fetch_all("SELECT note_id, title FROM notes")
    by_title = {n["note_id"]: (n["title"] or "") for n in notes}

    def char_set(s):
        return set(s)

    # 选一对 (A,B)：A 标题较长且具辨识度，B 与 A 标题字符重叠最小 -> 图谱扩展可观测
    rich = [n for n in notes if len(by_title[n["note_id"]]) >= 4]
    rich.sort(key=lambda n: -len(by_title[n["note_id"]]))
    a_set = {n["note_id"]: char_set(by_title[n["note_id"]]) for n in rich}

    chosen_a = chosen_b = None
    on_res = off_res = None
    for i, a in enumerate(rich[:40]):
        aid = a["note_id"]
        # B：与 A 标题 jaccard 最小
        best_b, best_j = None, 1.0
        for b in rich:
            bid = b["note_id"]
            if bid == aid:
                continue
            sa, sb = a_set[aid], a_set[bid]
            if not sa or not sb:
                continue
            j = len(sa & sb) / len(sa | sb)
            if j < best_j:
                best_j, best_b = j, b
        if best_b is None:
            continue
        bid = best_b["note_id"]

        # 清旧关系，插入 confirmed A->B（Human-in-the-loop：确认后才进检索）
        db.execute("DELETE FROM note_relations")
        db.execute(
            "INSERT INTO note_relations "
            "(relation_id, source_note_id, target_note_id, relation_type, status, direction, "
            "evidence_chunk_id, confidence, model_version, prompt_version, proposed_at, resolved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"rel-{aid[:6]}-{bid[:6]}", aid, bid, "related_to", "confirmed", "outgoing",
             None, 0.92, "demo", "demo1", "2026-07-20T00:00:00Z", "2026-07-20T00:00:00Z"),
        )

        q = f"请简要总结《{by_title[aid]}》这篇笔记主要讲了什么？"
        on_req = ChatRequest(question=q, retrieval_strategy="hybrid", final_top_k=5, graph_enabled=True)
        off_req = ChatRequest(question=q, retrieval_strategy="hybrid", final_top_k=5, graph_enabled=False)
        on_res = chat.answer(on_req)
        off_res = chat.answer(off_req)

        on_names = [c.document_name for c in on_res.citations]
        if by_title[aid] in on_names and any(c.document_name == by_title[bid] for c in on_res.citations):
            chosen_a, chosen_b = aid, bid
            break

    if chosen_a is None:
        print("[FAIL] 未能选出可观测图谱扩展的 (A,B) 对")
        return 1

    on_names = [c.document_name for c in on_res.citations]
    off_names = [c.document_name for c in off_res.citations]
    on_links = on_res.retrieval_trace.graph_links if on_res.retrieval_trace else []
    off_links = off_res.retrieval_trace.graph_links if off_res.retrieval_trace else []

    print("\n================ 结果 ================")
    print(f"探针笔记 A: 《{by_title[chosen_a]}》  (note_id={chosen_a[:12]}…)")
    print(f"关联笔记 B: 《{by_title[chosen_b]}》  (note_id={chosen_b[:12]}…)")
    print(f"\n[图谱 开启] 回答: {on_res.answer}")
    print(f"\n[图谱 开启] 引用 {len(on_names)} 篇: {on_names}")
    print(f"[图谱 开启] graph_links: {on_links}")
    print(f"[图谱 关闭] 引用 {len(off_names)} 篇: {off_names}")
    print(f"[图谱 关闭] graph_links: {off_links}")

    # 断言
    ok = True
    if by_title[chosen_a] not in on_names:
        print("[ASSERT FAIL] A 未进入 hybrid 命中（检索异常）"); ok = False
    if by_title[chosen_b] not in on_names:
        print("[ASSERT FAIL] 图谱扩展未拉回 B（D4 失败）"); ok = False
    if not on_links or on_links[0]["target_note_id"] != chosen_b:
        print("[ASSERT FAIL] graph_links 未记录 A->B 关系"); ok = False
    if off_links:
        print("[ASSERT FAIL] 图谱关闭时不应有 graph_links"); ok = False
    if by_title[chosen_b] in off_names:
        print("[ASSERT FAIL] 消融：关闭图谱仍含 B"); ok = False

    if ok:
        print(f"\n[PASS] 全链路通过（{time.perf_counter()-t0:.1f}s）：Vault {len(scan.scanned)} 篇笔记 -> "
              f"扫描注入ID -> 增量索引 {manifest['chunk_count']} chunks -> "
              f"图谱开启拉回关联笔记 B，关闭则回归纯 hybrid。")
    else:
        print("\n[FAIL] 断言未通过")

    if args.keep_workdir:
        print(f"[cleanup] 已按参数保留临时工作区 {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
        print(f"[cleanup] 已删除临时工作区 {work}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
