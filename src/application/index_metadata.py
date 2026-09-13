"""索引元数据的单一事实源（2026-09-10 P0 修复）。

## 背景：同一个索引目录，三个 builder，三套 chunk schema

``data/retrieval_indexes/`` 下实际存在三种版本的索引目录，由三个不同的服务写入，
而 ``CURRENT`` 指向哪一版取决于"最后一次构建"，检索管线只认 ``CURRENT``：

===========  ==============================================  ==========================================
版本前缀      写入者                                            chunk 元数据
===========  ==============================================  ==========================================
``m3-``      ``KnowledgeService.rebuild()``（文件扫描语料）      doc_name / section_path / chunk_index /
                                                             source / origin / relative_path
``m4-``      ``IndexLifecycleService.build()``（document_versions） child_chunk_id / logical_document_id /
                                                             authority_level / knowledge_category …
``mg-``      ``MindGraphIndexService.build()``（``notes`` 表）   全量：workspace / department / acl_json /
                                                             source_id / source_path …
===========  ==============================================  ==========================================

## 本机真实事故（2026-09-09）

一次 ``POST /knowledge/index/rebuild`` 走 ``m3-``（文件扫描）路径重建，把此前 25 篇、
元数据齐全的索引**静默**换成了 4 篇顶层中文语料 / 69 chunks 的薄元数据索引：

- ``notes`` 表仍显示 25 篇 ``index_status='ready'``（那是 08-27 那版 ``mg-`` 留下的状态）；
- 按数据源过滤（``source_ids``）失去依据——chunk 里根本没有 source 字段；
- 按权限过滤（``access_scope``）在非通配场景下**静默返回 0 条证据**；
- 知识库规模与召回分母同时失真，而全链路没有任何告警。

## 本模块做四件事

1. :func:`enrich_records_with_notes` —— 任意 builder 产出的 chunk 都能从 ``notes`` 表补齐
   ACL / 命名空间字段（与 ``MindGraphIndexService._load_note_chunks`` 使用同一套键名，
   避免"同一份索引两套 schema"再次分裂）；
2. :func:`evaluate_index_shrinkage` —— 准入守卫：候选索引缺文档时拒绝激活（除非显式 force）；
3. :func:`audit_index_consistency` —— 把「``notes`` 声明 vs 活跃索引实际」的偏差做成可读报告；
4. :func:`classify_divergence` —— 按**已声明的语料范围**把偏差拆成"已接受的范围外缺失"
   与"真分叉"，让告警只在真有问题时响（详见 ``DEFAULT_INCLUDED_SUBTREES`` 的注释）。

**只注入元数据，不碰正文、分块与既有键**，因此检索召回指标不受影响。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("mindgraph.index_metadata")

# 从 notes 表注入 chunk metadata 的字段。
# 这套键名与 MindGraphIndexService._load_note_chunks 保持一致——两份实现必须能互相替代，
# 否则"哪个 builder 最后跑"会再次决定元数据有没有。
#
# **刻意只收 ACL 与命名空间字段**。以下三类字段虽然 notes 表里也有，但**不注入**，
# 因为它们在 `dense.py` / `sparse.py` / `pipeline.py` 里直接参与**检索准入**判定，
# 属于另一条事实源（frontmatter / vault_sync_service），顺手带进来会改变召回：
#   - ``policy_status`` / ``document_status`` —— `status != "active"` 会被直接丢弃；
#   - ``effective_from`` / ``effective_to``（含 ``effective_date`` / ``expiration_date``）
#     —— 未生效或已过期会被丢弃；
#   - ``owner`` / ``policy_key`` / ``document_version`` —— 治理字段，与过滤无关，
#     留给文档生命周期那条链路，避免两个地方都能写同一份语义。
# 实测教训（2026-09-10）：注入 ``policy_status='unspecified'`` 后，本机 4 篇中文制度
# 整批不可检索，召回从 5 条掉到 0 条——这类静默失效正是本次修复要消灭的东西。
NAMESPACE_FIELDS: tuple[str, ...] = (
    "mindgraph_id",
    "vault_path",
    "title",
    "workspace",
    "department",
    "acl_json",
    "acl_public",
    "source_id",
    "source_path",
)

# 明确「不注入」的准入字段：测试用它锁住边界，避免以后有人"顺手补全"再次踩雷。
RETRIEVAL_GATING_FIELDS: tuple[str, ...] = (
    "document_status",
    "policy_status",
    "effective_date",
    "effective_from",
    "expiration_date",
    "effective_to",
)

# 取「文档键」时按优先级尝试的字段。m3- 只有 relative_path，mg- 有 vault_path，
# m4- 两者都没有（退回标题），所以这里必须容错。
_DOCUMENT_KEY_FIELDS: tuple[str, ...] = (
    "vault_path",
    "relative_path",
    "source_path",
    "doc_name",
    "document_title",
)

_UNMATCHED_SAMPLE_LIMIT = 5

# 各 builder 的路径基准并不一致：``m3-`` 的 ``relative_path`` 相对**项目根**
# （``knowledge\差旅费报销管理办法.md``），而 ``notes`` 表存的是相对 **vault 根**
# （``差旅费报销管理办法.md``）。归一到 vault 根坐标系，两边才能比对。
VAULT_ROOT_SEGMENT = "knowledge"


def _norm(value: Any) -> str:
    """归一化路径：统一分隔符、去掉 ``./`` 前缀、首尾斜杠与 vault 根前缀。"""
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    prefix = f"{VAULT_ROOT_SEGMENT}/"
    return text[len(prefix):] if text.startswith(prefix) else text


def normalize_label(value: Any) -> str:
    """公开的标签归一化入口（``_norm`` 的正式名字）。

    索引侧与数据集侧必须用同一套归一化，否则「索引里有的标签」与「数据集里要的
    标签」会各算各的，重叠数变成口径差的产物。对外的消费方见
    ``application/evaluation_service.py``。
    """
    return _norm(value)


def document_key(metadata: dict[str, Any] | None) -> str | None:
    """从 chunk metadata 里取一个跨 builder 可比的文档键。"""
    if not metadata:
        return None
    for key in _DOCUMENT_KEY_FIELDS:
        value = metadata.get(key)
        if value not in (None, ""):
            return _norm(value)
    return None


def _basename(key: str) -> str:
    return key.rsplit("/", 1)[-1].lower()


# ── 索引语料范围（产品决策，2026-09-10 拍板：文件层面的差异不追） ──────────────
#
# 活跃索引**声明只覆盖 vault 根目录**的 markdown：子目录（``policies/``、
# ``workflows/``、``cases/``、``external/public/``）显式声明为"不在索引范围"。
# 于是审计能把两种偏差分开：
#
# - **范围外缺失**（子目录文档没进索引）→ 已声明口径，只记 INFO，不报错；
# - **范围内缺失 / 索引里有 notes 不认识的东西** → 真分叉，报 ERROR。
#
# 这样既不会让一个"已知且已接受"的缺口变成永远消不掉的假警报（警报一旦
# 长期为真就没人看了，09-09 那类事故反而更容易藏），又保住了对真分叉的告警。
#
# ⚠️ 改这个值 = 改语料口径：索引内容会变，已公布的检索指标（如 R@5 0.587，
# 基于 23 条可评测样本）就不再对应当前语料，必须重跑消融并重新公布。
DEFAULT_INCLUDED_SUBTREES: tuple[str, ...] = ()


def parse_included_subtrees(raw: Any) -> tuple[str, ...]:
    """把配置里的子树声明解析成元组：``""`` → 仅根目录；``"a,b"`` → 含 ``a/``、``b/``。

    兼容已经解析好的序列（测试与调用方可以直传 tuple）。
    """
    if raw in (None, "", (), []):
        return DEFAULT_INCLUDED_SUBTREES
    if isinstance(raw, str):
        parts = raw.split(",")
    else:
        parts = list(raw)
    cleaned = {_norm(part) for part in parts if _norm(part)}
    return tuple(sorted(cleaned))


def document_subtree(key: str) -> str:
    """取文档键所在的一级子树；根目录文件返回 ``""``。"""
    normalized = _norm(key)
    if "/" not in normalized:
        return ""
    return normalized.split("/", 1)[0]


def is_document_in_scope(key: str, included_subtrees: tuple[str, ...] | None = None) -> bool:
    """文档是否落在声明的索引语料范围内（根目录文件永远算在范围内）。"""
    subtree = document_subtree(key)
    if not subtree:
        return True
    return subtree in (included_subtrees or DEFAULT_INCLUDED_SUBTREES)


def classify_divergence(
    missing_from_index: Any,
    undeclared_in_index: Any,
    included_subtrees: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """把「声明 vs 实际」的偏差拆成"已声明范围外"与"真分叉"两类。

    - ``in_scope_missing``：范围内该进索引却没进 → 真问题；
    - ``out_of_scope_subtrees``：范围外缺失的子树与篇数 → 已声明口径，仅供参考；
    - ``undeclared_in_index``：索引里有、``notes`` 不认识 → 永远是问题
      （说明索引来自另一套事实源）。
    """
    included = included_subtrees if included_subtrees is not None else DEFAULT_INCLUDED_SUBTREES
    in_scope_missing: list[str] = []
    out_of_scope_subtrees: dict[str, int] = {}
    for key in missing_from_index or []:
        if is_document_in_scope(key, included):
            in_scope_missing.append(key)
        else:
            subtree = document_subtree(key) or "<root>"
            out_of_scope_subtrees[subtree] = out_of_scope_subtrees.get(subtree, 0) + 1

    undeclared = sorted(undeclared_in_index or [])
    return {
        "included_subtrees": list(included),
        "in_scope_missing": sorted(in_scope_missing),
        "in_scope_missing_count": len(in_scope_missing),
        "out_of_scope_missing_count": sum(out_of_scope_subtrees.values()),
        "out_of_scope_subtrees": dict(sorted(out_of_scope_subtrees.items())),
        "undeclared_count": len(undeclared),
        "scope_consistent": not in_scope_missing and not undeclared,
    }


@dataclass
class NoteIndex:
    """``notes`` 表的内存索引：按相对路径 / 唯一文件名定位。"""

    rows: list[dict[str, Any]] = field(default_factory=list)
    by_key: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_name: dict[str, dict[str, Any]] = field(default_factory=dict)
    available: bool = True
    reason: str | None = None

    @property
    def eligible_rows(self) -> list[dict[str, Any]]:
        """AI 可检索的笔记（``ai_access_level != 'excluded'``）。"""
        return [row for row in self.rows if str(row.get("ai_access_level") or "") != "excluded"]

    def lookup(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        return self.by_key.get(key) or self.by_name.get(_basename(key))


def load_note_index(db_path: str | Path | None) -> NoteIndex:
    """只读加载 ``notes`` 表；不可用时返回 ``available=False`` 而不是抛异常。

    索引构建不应该因为元数据源不可读就整体失败——降级为"文件系统元数据"并告警，
    但绝不再假装元数据齐全。
    """
    if db_path is None:
        return NoteIndex(available=False, reason="no_database_path")
    path = Path(db_path)
    if not path.is_file():
        logger.warning("index_metadata_source_missing", extra={"db_path": str(path)})
        return NoteIndex(available=False, reason="database_file_missing")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in connection.execute("SELECT * FROM notes")]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        logger.warning("index_metadata_source_unreadable", extra={"db_path": str(path), "error": str(exc)[:200]})
        return NoteIndex(available=False, reason=f"sqlite_error:{type(exc).__name__}")

    index = NoteIndex(rows=rows)
    name_hits: dict[str, int] = {}
    for row in rows:
        for key_field in ("vault_path", "source_path"):
            value = row.get(key_field)
            if value not in (None, ""):
                index.by_key.setdefault(_norm(value), row)
        primary = row.get("vault_path") or row.get("source_path")
        if primary not in (None, ""):
            name = _basename(_norm(primary))
            name_hits[name] = name_hits.get(name, 0) + 1
    # 文件名可能重名（不同工作区同名制度），只保留唯一命中的，避免张冠李戴。
    for row in rows:
        primary = row.get("vault_path") or row.get("source_path")
        if primary in (None, ""):
            continue
        name = _basename(_norm(primary))
        if name_hits.get(name) == 1:
            index.by_name[name] = row
    return index


@dataclass
class EnrichmentReport:
    total: int = 0
    matched: int = 0
    unmatched: int = 0
    unmatched_samples: list[str] = field(default_factory=list)
    source_available: bool = True
    source_reason: str | None = None

    @property
    def coverage(self) -> float:
        return round(self.matched / self.total, 4) if self.total else 1.0


def _metadata_of(record: Any) -> dict[str, Any] | None:
    """取记录里的 metadata 字典，兼容两种形态：

    - ``dict``（``load_markdown_chunks`` 的记录：``{"text": ..., "metadata": {...}}``）
    - 带 ``.metadata`` 的对象（``retrieval.types.Chunk`` 数据类，``load_corpus`` 的产物）
    """
    if isinstance(record, dict):
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            record["metadata"] = metadata
        return metadata
    metadata = getattr(record, "metadata", None)
    return metadata if isinstance(metadata, dict) else None


def enrich_records_with_notes(
    records: Iterable[Any],
    note_index: NoteIndex,
) -> EnrichmentReport:
    """把 ``notes`` 表的 ACL / 命名空间字段注入到 chunk 记录（原地修改）。

    匹配失败（该文件不在 ``notes`` 表里）不会丢数据，只会：
    - 标记 ``metadata_source = "filesystem_only"``；
    - 计入报告，由调用方打告警——「元数据缺失」必须可见。
    """
    report = EnrichmentReport(source_available=note_index.available, source_reason=note_index.reason)
    for record in records:
        report.total += 1
        metadata = _metadata_of(record)
        key = document_key(metadata)
        note = note_index.lookup(key)
        if metadata is None or note is None:
            if metadata is not None:
                metadata["metadata_source"] = "filesystem_only"
            report.unmatched += 1
            if len(report.unmatched_samples) < _UNMATCHED_SAMPLE_LIMIT:
                report.unmatched_samples.append(key or "<no-path>")
            continue
        for field_name in NAMESPACE_FIELDS:
            value = note.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            metadata[field_name] = bool(value) if field_name == "acl_public" else value
        metadata["metadata_source"] = "notes"
        report.matched += 1
    return report


def index_document_keys(index_root: str | Path) -> tuple[set[str], int, str | None]:
    """读取活跃索引（``CURRENT``）覆盖的文档键集合、chunk 数与版本号。"""
    root = Path(index_root)
    try:
        version = (root / "CURRENT").read_text(encoding="utf-8").strip() or None
    except OSError:
        return set(), 0, None
    if not version:
        return set(), 0, None
    try:
        chunks = json.loads((root / version / "chunks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "active_index_chunks_unreadable",
            extra={"index_version": version, "error": str(exc)[:200]},
        )
        return set(), 0, version
    keys: set[str] = set()
    for chunk in chunks if isinstance(chunks, list) else []:
        key = document_key(chunk.get("metadata") if isinstance(chunk, dict) else None)
        if key:
            keys.add(key)
    return keys, len(chunks) if isinstance(chunks, list) else 0, version


def evaluate_index_shrinkage(
    *,
    previous_keys: Iterable[str],
    candidate_keys: Iterable[str],
    excused_names: Iterable[str] = (),
) -> dict[str, Any]:
    """准入守卫：候选索引**丢失**了活跃索引里的文档时给出可解释结论。

    规则（有意做成 fail-closed）：
    - ``missing = 活跃索引文档 - 候选文档``；
    - 显式删除清单（``excused_names``）里能对上文件名的，属于预期缩水；
    - 剩下无法解释的缺失 → ``blocked=True``，需调用方显式 ``force`` 才继续。

    为什么不是"只比总数"：2026-09-09 那次事故正是总数从 25 掉到 4，
    只报数量变化看不出"丢的是哪些篇"，报出文件名才能立刻定位。
    """
    previous = {_norm(key) for key in previous_keys if key}
    candidate = {_norm(key) for key in candidate_keys if key}
    excused = {_basename(_norm(name)) for name in excused_names if name}
    missing = sorted(previous - candidate)
    unexplained = [key for key in missing if _basename(key) not in excused]
    return {
        "blocked": bool(unexplained),
        "previous_documents": len(previous),
        "candidate_documents": len(candidate),
        "missing_count": len(missing),
        "excused_count": len(missing) - len(unexplained),
        "unexplained_missing": unexplained[:20],
        "unexplained_missing_count": len(unexplained),
    }


def audit_index_consistency(
    *,
    index_root: str | Path,
    db_path: str | Path | None,
    included_subtrees: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """比对「``notes`` 声明可检索的文档」与「活跃索引实际覆盖的文档」。

    这是把 2026-09-09 那类静默分叉变成一行可见结论的最小工具：
    审计脚本与 API 启动日志都用它。

    ``included_subtrees`` 是**已声明的语料范围**（见 :data:`DEFAULT_INCLUDED_SUBTREES`）：
    落在范围外的缺失只算"已接受的口径"，不算分叉。``consistent`` 仍表示"完全无偏差"，
    调用方想判断"有没有需要处理的问题"应看 ``scope_consistent``。
    """
    note_index = load_note_index(db_path)
    declared = {
        _norm(row.get("vault_path") or row.get("source_path"))
        for row in note_index.eligible_rows
        if (row.get("vault_path") or row.get("source_path"))
    }
    index_keys, chunk_count, version = index_document_keys(index_root)
    missing_from_index = sorted(declared - index_keys)
    undeclared_in_index = sorted(index_keys - declared)

    classification = classify_divergence(missing_from_index, undeclared_in_index, included_subtrees)

    coverage = {"chunks": chunk_count, "chunks_with_workspace": 0, "chunks_with_acl": 0}
    if version:
        try:
            chunks = json.loads((Path(index_root) / version / "chunks.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            chunks = []
        for chunk in chunks if isinstance(chunks, list) else []:
            metadata = chunk.get("metadata") if isinstance(chunk, dict) else {}
            if not isinstance(metadata, dict):
                continue
            if metadata.get("workspace"):
                coverage["chunks_with_workspace"] += 1
            if metadata.get("acl_json"):
                coverage["chunks_with_acl"] += 1

    consistent = not missing_from_index and not undeclared_in_index
    return {
        "consistent": consistent,
        "scope_consistent": classification["scope_consistent"],
        "index_version": version,
        "declared_documents": len(declared),
        "indexed_documents": len(index_keys),
        "missing_from_index": missing_from_index[:20],
        "missing_from_index_count": len(missing_from_index),
        "undeclared_in_index": undeclared_in_index[:20],
        "undeclared_in_index_count": len(undeclared_in_index),
        "metadata_source_available": note_index.available,
        "metadata_source_reason": note_index.reason,
        **classification,
        **coverage,
    }


# ── 索引根登记表：把「哪个根服务哪套数据集」变成可检测的代码契约 ─────────────
#
# 本机实测（2026-09-11）：仓库里有两个索引根，**都叫 CURRENT**，但服务两套互不
# 相干的评测栈，且各自完全自洽：
#
# =======================  ============================================  ================
# 根目录                     服务的系统 / 数据集                            标签命名空间
# =======================  ============================================  ================
# ``mindgraph_indexes``    线上问答管线 + golden v2（90 题）               ``vault_path``
# ``retrieval_indexes``    历史 M1/M2 评测栈 + expense_qa_v1（34 题）      ``chunk_id``
# =======================  ============================================  ================
#
# 实测重叠：mindgraph 根对 golden 的 13 个 vault_path **全中**、对 expense_qa_v1 的
# 中文 chunk_id **0 中**；retrieval 根对 expense 的 17 个 chunk_id **全中**、对
# golden 的 vault_path **0 中**。因此**不存在「哪个根是错的」**——但存在两类真风险：
#
# 1. **「统一根」不是无痛操作**。把 ``EvaluationService`` 的根换成
#    ``mindgraph_indexes`` 会立刻抛 ``ValueError: No index version is compatible``
#    （它的数据集标签是 ``差旅费报销管理办法.md::6`` 这类中文 chunk_id，
#    ``mg-`` 根用的是 32 位 hex ID），``/api/v1/evaluations`` 直接坏掉。
# 2. **接错栈是静默的**。新增代码随手写一个 ``ROOT / "data" / ...`` 常量就能接上
#    另一套栈，表现是「跑得通，但测的不是线上系统」——数字看起来完全正常。
#
# 登记表负责消灭第 2 类：任何索引根都必须在此声明它的数据集与消费方，
# ``tests/test_index_root_registry.py`` 断言「登记的根存在」+「根与它声明的数据集
# 标签重叠 > 0」+「磁盘上的根没有漏登记」。于是接错栈会变成测试失败。
#
# ⚠️ 本表只声明事实，不改变任何读取行为：两个消费方（evaluation_service /
# freeze_baseline）各自继续读自己的根，因为它们的**数据集粒度不同**。
# 「让 /api/v1/evaluations 也测线上系统」是产品决策，另立任务，不在本表范围。
INDEX_ROOT_REGISTRY_VERSION = "index_root_registry_v1"

# label_key → 数据集里承载 gold 标签的字段名。
# v1（expense_qa_v1）标注到 chunk 级，v2（golden v2）标注到文档级——这不是疏漏，
# 而是两代评测对「期望证据」的粒度不同，故显式区分而不是取并集假装统一。
_GOLD_LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "chunk_id": ("gold_chunk_ids", "acceptable_chunk_ids"),
    "vault_path": ("gold_vault_paths",),
}


@dataclass(frozen=True)
class IndexRootSpec:
    """一个索引根的服务契约：它服务哪套数据集、谁写入、谁读取。"""

    name: str
    purpose: str
    dataset: str
    label_key: str
    builders: tuple[str, ...]
    consumers: tuple[str, ...]

    @property
    def root(self) -> str:
        """相对项目根的路径，用于写进报告与产物。"""
        return f"data/{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root": self.root,
            "purpose": self.purpose,
            "dataset": self.dataset,
            "label_key": self.label_key,
            "builders": list(self.builders),
            "consumers": list(self.consumers),
        }


INDEX_ROOT_REGISTRY: tuple[IndexRootSpec, ...] = (
    IndexRootSpec(
        name="mindgraph_indexes",
        purpose="线上 MindGraph 问答检索（container.mindgraph_pipeline）与 golden v2 基线",
        dataset="mindgraph_golden_v2.jsonl",
        label_key="vault_path",
        builders=("mg-（application/mindgraph_index_service.py）",),
        consumers=(
            "api/dependencies.py → ServiceContainer._init_mindgraph",
            "scripts/freeze_baseline.py（live 检索）",
        ),
    ),
    IndexRootSpec(
        name="retrieval_indexes",
        purpose="历史 M1/M2 评测栈：/api/v1/evaluations 与 evaluation.retrieval_eval 在用",
        dataset="expense_qa_v1.jsonl",
        label_key="chunk_id",
        builders=(
            "m3-（application/knowledge_service.py）",
            "m4-（application/index_lifecycle_service.py）",
        ),
        consumers=(
            "application/evaluation_service.py",
            "evaluation/retrieval_eval.py",
            "infrastructure/retrieval_factory.py",
        ),
    ),
)


def index_root_spec(name: str) -> IndexRootSpec:
    """按目录名取登记项；未登记即报错，强制新增根先登记。"""
    for spec in INDEX_ROOT_REGISTRY:
        if spec.name == name:
            return spec
    known = ", ".join(spec.name for spec in INDEX_ROOT_REGISTRY)
    raise KeyError(f"unregistered index root {name!r}; registered roots: {known}")


def dataset_gold_labels(dataset_path: str | Path, label_key: str) -> set[str]:
    """读数据集里的 gold 标签集合（按 ``label_key`` 决定读哪些字段）。

    文件不可读或无有效标签时返回空集——调用方（门禁）会把它判为「绑定不成立」，
    不在这里抛异常，免得审计路径因为数据文件缺失而整体崩掉。
    """
    fields = _GOLD_LABEL_FIELDS.get(label_key)
    if not fields:
        raise KeyError(f"unknown label_key {label_key!r}; known: {sorted(_GOLD_LABEL_FIELDS)}")
    path = Path(dataset_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    labels: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        for field_name in fields:
            for value in row.get(field_name) or []:
                if value:
                    labels.add(_norm(value))
    return labels


def version_label_set(
    version_dir: str | Path,
    label_key: str,
) -> tuple[set[str], int]:
    """读**指定索引版本目录**的标签集合，返回 ``(标签集, chunk 数)``。

    与 :func:`index_label_set` 唯一的区别是**不读 ``CURRENT``**：调用方已经知道要看哪个
    版本。用途是「在多个版本里挑一个与该数据集重叠最大的版本」——
    ``application/evaluation_service.py`` 的 ``_compatible_index_version`` 靠它工作。

    两者共用同一套归一化（:func:`_norm` / :func:`document_key`），这是有意的：
    若「挑版本」与「验绑定」用两套口径，就会出现「选中了某版本、但绑定检查说不成立」。

    ``label_key`` 未登记即报错（fail-closed）：历史上这里是一个 ``if/else``，
    拼错的 key 会静默落到 ``document_key`` 分支，产出「看起来正常」的错标签集。
    """
    if label_key not in _GOLD_LABEL_FIELDS:
        raise KeyError(f"unknown label_key {label_key!r}; known: {sorted(_GOLD_LABEL_FIELDS)}")
    directory = Path(version_dir)
    try:
        chunks = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), 0
    if not isinstance(chunks, list):
        return set(), 0
    labels: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        if label_key == "chunk_id":
            value = chunk.get("chunk_id")
            if value:
                labels.add(_norm(value))
        else:
            key = document_key(chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else None)
            if key:
                labels.add(key)
    return labels, len(chunks)


def index_label_set(
    index_root: str | Path,
    label_key: str,
) -> tuple[set[str], str | None, int]:
    """取活跃索引的标签集合，返回 ``(标签集, 索引版本, chunk 数)``。

    与 :func:`dataset_gold_labels` 用同一套归一化（:func:`_norm` / :func:`document_key`），
    两边可比才算「这个根服务的正是这套数据集」。
    """
    root = Path(index_root)
    try:
        version = (root / "CURRENT").read_text(encoding="utf-8").strip() or None
    except OSError:
        return set(), None, 0
    if not version:
        return set(), None, 0
    labels, chunk_count = version_label_set(root / version, label_key)
    return labels, version, chunk_count


def index_root_binding_report(project_root: str | Path) -> dict[str, Any]:
    """把登记表与磁盘实际状态对账，产出可读结论（门禁与审计共用）。

    每项含 ``overlap``（根标签 ∩ 数据集 gold 标签）。``binding_ok`` 要求
    ``overlap > 0``——这是「该根确实服务该数据集」的机器判据，也是本模块对
    2026-09-11「双根差 8.4×」那次排查的最终收口：不是选一个根，而是把
    「谁服务谁」写成可验证的事实。
    """
    project_root = Path(project_root)
    dataset_dir = project_root / "evaluation" / "datasets"
    entries: list[dict[str, Any]] = []
    for spec in INDEX_ROOT_REGISTRY:
        root_path = project_root / spec.root
        labels, version, chunk_count = index_label_set(root_path, spec.label_key)
        gold = dataset_gold_labels(dataset_dir / spec.dataset, spec.label_key)
        overlap = labels & gold
        entries.append({
            **spec.to_dict(),
            "exists": root_path.is_dir(),
            "index_version": version,
            "chunk_count": chunk_count,
            "label_count": len(labels),
            "dataset_label_count": len(gold),
            "overlap": len(overlap),
            "overlap_samples": sorted(overlap)[:_UNMATCHED_SAMPLE_LIMIT],
            "binding_ok": bool(overlap),
        })
    return {
        "registry_version": INDEX_ROOT_REGISTRY_VERSION,
        "roots": entries,
        "binding_ok": all(entry["binding_ok"] for entry in entries),
    }
