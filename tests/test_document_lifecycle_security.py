"""document_lifecycle 路径安全回归（2026-08-27 复审发现）。

logical_document_id / version 来自表单字段并直接拼进文件系统路径；
必须拒绝 ".." 与路径分隔符，防止穿越 storage_root 任意写文件。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from application.document_lifecycle_service import DocumentLifecycleService
from domain.errors import ValidationError
from infrastructure.database import ProductDatabase


@pytest.fixture()
def service(tmp_path: Path) -> DocumentLifecycleService:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    return DocumentLifecycleService(database, tmp_path / "storage")


def test_create_version_rejects_traversal_in_logical_id(service: DocumentLifecycleService) -> None:
    with pytest.raises(ValidationError):
        service.create_version("a.md", b"# hello", "../../evil", "v1", "other", "official_policy")


def test_create_version_rejects_traversal_in_version(service: DocumentLifecycleService) -> None:
    with pytest.raises(ValidationError):
        service.create_version("a.md", b"# hello", "doc-ok", "../v2", "other", "official_policy")


def test_create_version_rejects_backslash_and_separators(service: DocumentLifecycleService) -> None:
    for bad in ("a\\b", "a/b", "..", "....", "x y"):
        with pytest.raises(ValidationError):
            service.create_version("a.md", b"# hello", bad, "v1", "other", "official_policy")


def test_create_version_writes_inside_storage_root(service: DocumentLifecycleService, tmp_path: Path) -> None:
    record = service.create_version("a.md", b"# hello", "doc-ok", "v1", "other", "official_policy")
    record_dir = tmp_path / "storage" / "doc-ok" / "v1"
    assert record_dir.is_dir()
    assert (record_dir / "source.md").read_bytes() == b"# hello"
    assert record.document_id
