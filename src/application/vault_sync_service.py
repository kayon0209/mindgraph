"""Vault 扫描与稳定 ID 注入服务（M1-D2）。

职责：
- 递归遍历 Obsidian Vault 的 ``.md`` / ``.markdown`` 文件；
- 解析 Frontmatter，首次扫描仅注入 ``mindgraph_id``（定点插入，绝不改正文）；
- 计算基于正文（去除 Frontmatter）的 ``content_hash``，用于增量去重；
- 抽取标题与 ``ai_access_level``（默认 local_only）；
- upsert 到 ``notes`` 表；内容未变则保留原 ``index_status``，内容变更则置 ``pending``；
- 处理重复 ID（如复制文件带原 ID）与缺失文件剪枝。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

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


class VaultSyncService:
    def __init__(
        self,
        db: ProductDatabase,
        vault_path: Path,
        write_ids: bool = True,
        ignore_dirs: set[str] | None = None,
    ) -> None:
        self.db = db
        self.vault_path = Path(vault_path)
        self.write_ids = write_ids
        # None → 使用默认忽略集合；空集合 → 不忽略任何目录
        self.ignore_dirs = DEFAULT_IGNORE_DIRS if ignore_dirs is None else frozenset(ignore_dirs)

    def _is_ignored(self, path: Path) -> bool:
        if not self.ignore_dirs:
            return False
        rel = path.relative_to(self.vault_path)
        # 检查除文件名外的每一级父目录
        for part in rel.parts[:-1]:
            if part in self.ignore_dirs:
                return True
        return False

    def scan_vault(self) -> VaultScanResult:
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

        pruned = self._prune_missing({n.vault_path for n in scanned})
        return VaultScanResult(scanned, skipped, errors, pruned)

    def _process_file(self, path: Path, now: str, seen_ids: dict[str, Path]) -> ScannedNote | None:
        # 统一存 POSIX 风格路径（vault_path 跨平台一致，避免 Windows 反斜杠不匹配）
        rel = path.relative_to(self.vault_path).as_posix()
        raw = path.read_text(encoding="utf-8")
        fm, body, _ = parse_frontmatter(raw)
        id_injected = False
        duplicate_resolved = False

        if not fm.get("mindgraph_id"):
            mid = uuid.uuid4().hex
            id_injected = True
        else:
            mid = str(fm["mindgraph_id"])

        # 复制文件会保留原 ID → 重新分配，保证 note_id 全局唯一
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
        self._upsert_note(mid, rel, title, content_hash, fm, access, policy, now)
        return ScannedNote(mid, rel, title, content_hash, access, fm, id_injected, duplicate_resolved)

    _UPSERT = """
        INSERT INTO notes
            (note_id, vault_path, title, content_hash, frontmatter_json, ai_access_level,
             chunk_count, index_status, index_version, owner, document_version,
             policy_key, effective_from, effective_to, policy_status, metadata_issues_json,
             created_at, updated_at, last_indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, 'pending', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(note_id) DO UPDATE SET
            vault_path=excluded.vault_path,
            title=excluded.title,
            content_hash=excluded.content_hash,
            frontmatter_json=excluded.frontmatter_json,
            ai_access_level=excluded.ai_access_level,
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
                THEN 'pending'
                ELSE notes.index_status
            END
    """

    def _upsert_note(
        self, note_id: str, vault_path: str, title: str,
        content_hash: str, fm: dict, access: str, policy: PolicyMetadata, now: str,
    ) -> None:
        self.db.execute(
            self._UPSERT,
            (note_id, vault_path, title, content_hash, json.dumps(fm, ensure_ascii=False, default=_json_default),
             access, policy.owner, policy.document_version, policy.policy_key, policy.effective_from,
             policy.effective_to, policy.policy_status, json.dumps(policy.issues, ensure_ascii=False),
             now, now),
        )

    def _prune_missing(self, current_paths: set[str]) -> int:
        rows = self.db.fetch_all("SELECT note_id, vault_path FROM notes")
        to_delete = [row["note_id"] for row in rows if row["vault_path"] not in current_paths]
        if not to_delete:
            return 0
        # 批量删除：先清子表（notes 被 note_relations 外键引用），否则触发
        # sqlite3.IntegrityError: FOREIGN KEY constraint failed
        placeholders = ",".join("?" * len(to_delete))
        self.db.execute(
            f"DELETE FROM note_relations "
            f"WHERE source_note_id IN ({placeholders}) OR target_note_id IN ({placeholders})",
            tuple(to_delete) * 2,
        )
        self.db.execute(f"DELETE FROM notes WHERE note_id IN ({placeholders})", tuple(to_delete))
        return len(to_delete)
