"""Vault 扫描与稳定 ID 注入服务（M1-D2）。

职责：
- 递归遍历 Obsidian Vault 的 ``.md`` / ``.markdown`` 文件；
- 解析 Frontmatter，首次扫描仅注入 ``mindgraph_id``（定点插入，绝不改正文）；
- 计算基于正文（去除 Frontmatter）的 ``content_hash``，用于增量去重；
- 抽取标题与 ``ai_access_level``（默认 local_only）；
- 同步 workspace / department / ACL 元数据，供检索与台账列表做权限裁剪；
- upsert 到 ``notes`` 表；内容未变则保留原 ``index_status``，内容变更则置 ``pending``；
- 处理重复 ID（如复制文件带原 ID）与缺失文件剪枝。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, NamedTuple
import uuid

from infrastructure.database import ProductDatabase
from infrastructure.markdown_frontmatter import inject_mindgraph_id, parse_frontmatter

SUPPORTED_SUFFIXES = {".md", ".markdown"}
ACCESS_LEVELS = {"excluded", "local_only", "redacted_cloud", "cloud_allowed"}
POLICY_STATUSES = {"draft", "active", "expired", "superseded", "archived"}

# 同步时默认跳过的目录：虚拟环境 / 依赖 / 缓存 / VCS / 编辑器配置 / 回收站。
# 避免 Playwright、node_modules 等依赖文档被当成知识塞进索引。
DEFAULT_IGNORE_DIRS = frozenset({
    ".venv", "venv", "env",
    "node_modules",
    "__pycache__",
    ".git", ".hg", ".svn",
    ".obsidian",          # Obsidian 配置（非笔记内容）
    ".trash", ".idea", ".vscode", ".ruff_cache", ".mypy_cache",
})


class ScannedNote(NamedTuple):
    note_id: str
    vault_path: str
    title: str
    content_hash: str
    ai_access_level: str
    frontmatter: dict
    id_injected: bool
    duplicate_resolved: bool


class VaultScanResult(NamedTuple):
    scanned: list[ScannedNote]
    skipped: list[str]
    errors: list[str]
    pruned: int


class PolicyMetadata(NamedTuple):
    owner: str | None
    policy_key: str | None
    document_version: str | None
    effective_from: str | None
    effective_to: str | None
    policy_status: str
    issues: list[str]


class AccessMetadata(NamedTuple):
    workspace: str | None
    department: str | None
    acl_json: str
    acl_public: bool


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj):
    """json.dumps 兜底：YAML 会把日期解析成 date/datetime，set 解析成 set，
    直接 dumps 会抛 Not JSON serializable。统一转字符串，保证 notes.frontmatter_json 可写。"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def _extract_title(fm: dict, body: str, path: Path) -> str:
    title = fm.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in body.splitlines():
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            return m.group(1).strip()
    return path.stem


def _metadata_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _policy_metadata(fm: dict[str, Any]) -> PolicyMetadata:
    owner = _metadata_text(fm.get("owner"))
    policy_key = _metadata_text(fm.get("policy_key"))
    version = _metadata_text(fm.get("version"))
    effective_from = _metadata_text(fm.get("effective_from"))
    effective_to = _metadata_text(fm.get("effective_to"))
    raw_status = (_metadata_text(fm.get("status")) or "").lower()
    issues: list[str] = []

    if not owner:
        issues.append("missing_owner")
    if not policy_key:
        issues.append("missing_policy_key")
    if not version:
        issues.append("missing_version")
    if not effective_from:
        issues.append("missing_effective_from")
    if not raw_status:
        issues.append("missing_policy_status")
        status = "unspecified"
    elif raw_status not in POLICY_STATUSES:
        issues.append("invalid_policy_status")
        status = "unspecified"
    else:
        status = raw_status
    if effective_from and effective_to and effective_to < effective_from:
        issues.append("invalid_effective_range")

    return PolicyMetadata(owner, policy_key, version, effective_from, effective_to, status, issues)


def _access_metadata(fm: dict[str, Any], path: Path) -> AccessMetadata:
    workspace = _metadata_text(fm.get("workspace") or fm.get("tenant") or path.parent.name)
    department = _metadata_text(fm.get("department") or fm.get("dept"))
    raw_acl = fm.get("acl")
    if isinstance(raw_acl, dict):
        acl = dict(raw_acl)
    elif isinstance(raw_acl, list):
        acl = {"allow": raw_acl}
    elif isinstance(raw_acl, str) and raw_acl.strip():
        try:
            parsed = json.loads(raw_acl)
        except json.JSONDecodeError:
            parsed = {}
        acl = parsed if isinstance(parsed, dict) else {}
    else:
        acl = {}

    acl.setdefault("workspace", workspace)
    if department:
        acl.setdefault("department", department)
    if "allow" not in acl:
        allow = []
        if workspace:
            allow.append(f"workspace:{workspace}")
        if department:
            allow.append(f"department:{department}")
        acl["allow"] = allow

    public_flag = fm.get("acl_public", acl.get("public", acl.get("is_public", False)))
    acl_public = bool(public_flag)
    if acl_public:
        acl["public"] = True
    acl_json = json.dumps(acl, ensure_ascii=False, default=_json_default)
    return AccessMetadata(workspace, department, acl_json, acl_public)


class VaultSyncService:
    def __init__(
        self,
        db: ProductDatabase,
        vault_path: Path,
        write_ids: bool = True,
        ignore_dirs: set[str] | None = None,
        path_prefix: str | None = None,
        id_namespace: str | None = None,
    ) -> None:
        self.db = db
        self.vault_path = Path(vault_path)
        self.write_ids = write_ids
        self.path_prefix = path_prefix.strip("/") if path_prefix else None
        self.id_namespace = id_namespace
        # None → 使用默认忽略集合；空集合 → 不忽略任何目录
        self.ignore_dirs = DEFAULT_IGNORE_DIRS if ignore_dirs is None else frozenset(ignore_dirs)

    def _is_ignored(self, path: Path) -> bool:
        if not self.ignore_dirs:
            return False
        rel = path.relative_to(self.vault_path)
        for part in rel.parts[:-1]:
            if part in self.ignore_dirs:
                return True
        return False

    def scan_vault(self, *, prune_missing: bool = True) -> VaultScanResult:
        if not self.vault_path.exists() or not self.vault_path.is_dir():
            raise ValueError(f"Vault path is not a directory: {self.vault_path}")
        scanned: list[ScannedNote] = []
        skipped: list[str] = []
        errors: list[str] = []
        seen_ids: dict[str, Path] = {}
        now = _utc_iso()

        for path in sorted(self.vault_path.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                skipped.append(str(path))
                continue
            if self._is_ignored(path):
                skipped.append(str(path))
                continue
            try:
                note = self._process_file(path, now, seen_ids)
                if note is not None:
                    scanned.append(note)
            except Exception as exc:  # 单文件失败不影响整体
                errors.append(f"{path.relative_to(self.vault_path)}: {exc}")

        # A partial scan is not a reliable deletion snapshot.  Connector
        # callers also disable prune until notes have source ownership.
        pruned = 0
        if prune_missing and not errors:
            pruned = self._prune_missing({n.vault_path for n in scanned})
        return VaultScanResult(scanned, skipped, errors, pruned)

    def _process_file(self, path: Path, now: str, seen_ids: dict[str, Path]) -> ScannedNote | None:
        source_rel = path.relative_to(self.vault_path).as_posix()
        rel = f"{self.path_prefix}/{source_rel}" if self.path_prefix else source_rel
        raw = path.read_text(encoding="utf-8")
        fm, body, _ = parse_frontmatter(raw)
        id_injected = False
        duplicate_resolved = False

        if not fm.get("mindgraph_id"):
            if self.id_namespace:
                mid = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.id_namespace}:{source_rel}").hex
            else:
                mid = uuid.uuid4().hex
            id_injected = True
        else:
            mid = str(fm["mindgraph_id"])

        if mid in seen_ids:
            mid = uuid.uuid4().hex
            id_injected = True
            duplicate_resolved = True

        if id_injected and self.write_ids:
            new_text = inject_mindgraph_id(raw, mid)
            path.write_text(new_text, encoding="utf-8")
            raw = new_text
            fm, body, _ = parse_frontmatter(raw)

        seen_ids[mid] = path
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        title = _extract_title(fm, body, path)
        access = str(fm.get("ai_access_level", "local_only")).lower()
        if access not in ACCESS_LEVELS:
            access = "local_only"
        policy = _policy_metadata(fm)
        access_meta = _access_metadata(fm, path)
        self._upsert_note(mid, rel, title, content_hash, fm, access, policy, access_meta, now)
        return ScannedNote(mid, rel, title, content_hash, access, fm, id_injected, duplicate_resolved)

    _UPSERT = """
        INSERT INTO notes
            (note_id, vault_path, title, content_hash, frontmatter_json, ai_access_level,
             workspace, department, acl_json, acl_public, chunk_count, index_status, index_version,
             owner, document_version, policy_key, effective_from, effective_to, policy_status,
             metadata_issues_json, created_at, updated_at, last_indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(note_id) DO UPDATE SET
            vault_path=excluded.vault_path,
            title=excluded.title,
            content_hash=excluded.content_hash,
            frontmatter_json=excluded.frontmatter_json,
            ai_access_level=excluded.ai_access_level,
            workspace=excluded.workspace,
            department=excluded.department,
            acl_json=excluded.acl_json,
            acl_public=excluded.acl_public,
            owner=excluded.owner,
            document_version=excluded.document_version,
            policy_key=excluded.policy_key,
            effective_from=excluded.effective_from,
            effective_to=excluded.effective_to,
            policy_status=excluded.policy_status,
            metadata_issues_json=excluded.metadata_issues_json,
            updated_at=excluded.updated_at,
            index_status=CASE
                WHEN excluded.content_hash <> notes.content_hash
                  OR excluded.owner IS NOT notes.owner
                  OR excluded.document_version IS NOT notes.document_version
                  OR excluded.policy_key IS NOT notes.policy_key
                  OR excluded.effective_from IS NOT notes.effective_from
                  OR excluded.effective_to IS NOT notes.effective_to
                  OR excluded.policy_status IS NOT notes.policy_status
                  OR excluded.workspace IS NOT notes.workspace
                  OR excluded.department IS NOT notes.department
                  OR excluded.acl_json IS NOT notes.acl_json
                  OR excluded.acl_public IS NOT notes.acl_public
                THEN 'pending'
                ELSE notes.index_status
            END
    """

    def _upsert_note(
        self,
        note_id: str,
        vault_path: str,
        title: str,
        content_hash: str,
        fm: dict,
        access: str,
        policy: PolicyMetadata,
        access_meta: AccessMetadata,
        now: str,
    ) -> None:
        self.db.execute(
            self._UPSERT,
            (
                note_id,
                vault_path,
                title,
                content_hash,
                json.dumps(fm, ensure_ascii=False, default=_json_default),
                access,
                access_meta.workspace,
                access_meta.department,
                access_meta.acl_json,
                1 if access_meta.acl_public else 0,
                policy.owner,
                policy.document_version,
                policy.policy_key,
                policy.effective_from,
                policy.effective_to,
                policy.policy_status,
                json.dumps(policy.issues, ensure_ascii=False),
                now,
                now,
            ),
        )

    def _prune_missing(self, current_paths: set[str]) -> int:
        rows = self.db.fetch_all("SELECT note_id, vault_path FROM notes")
        to_delete = [row["note_id"] for row in rows if row["vault_path"] not in current_paths]
        if not to_delete:
            return 0
        placeholders = ",".join("?" * len(to_delete))
        self.db.execute(
            f"DELETE FROM note_relations "
            f"WHERE source_note_id IN ({placeholders}) OR target_note_id IN ({placeholders})",
            tuple(to_delete) * 2,
        )
        self.db.execute(f"DELETE FROM notes WHERE note_id IN ({placeholders})", tuple(to_delete))
        return len(to_delete)
