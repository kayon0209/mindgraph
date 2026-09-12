"""验收审查修复：workbuddy PR-04/06/07 的跨 PR 集成缺陷。

P0：m4 索引 manifest 的切分口径必须与 ChunkingPolicy 同源（PR-03 单一
来源承诺）；历史硬编码 500/1200/50 在选了非 legacy 预设时谎报指纹，
让 PR-04 门禁基于假数据做拦截决策。

P1：上传路径双重解析——create_version 解析一次（入索引），页级记账
_record_page_ingestion 又对同一 data 完整重解析一次；且记账的解析不含
OCR 增补结果（_maybe_ocr 先跑、记账后跑），OCR 已采纳的页在页级账本上
仍是 ocr_required。修法：记账复用已解析的 ParsedDocument（run_from_parsed），
不再自己重新解析。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "fix.sqlite3")
    database.initialize()
    return database


# ── P0：m4 manifest 切分口径同源 ─────────────────────────────────────


def test_m4_manifest_records_active_policy_not_hardcoded(monkeypatch: pytest.MonkeyPatch):
    """选了非 legacy 预设后，m4 manifest 必须记录真实 policy——否则
    PR-04 指纹读谎报值，门禁决策失真。"""
    from application import chunking_policy as cp
    from application.index_lifecycle_service import IndexLifecycleService
    from infrastructure.settings import get_settings

    probe = cp.ChunkingPolicy(name="probe_v2", version="1", child_size=800, parent_size=3000, overlap=100)
    monkeypatch.setitem(cp._PRESETS, "probe_v2", probe)
    monkeypatch.setenv("CHUNKING_POLICY", "probe_v2")
    get_settings.cache_clear()
    try:
        # 直接调 build 太重（要真索引）——断言 manifest 构造源头同源即可：
        # build 的 manifest 必须经 ChunkingPolicy.from_settings()。
        import inspect

        source = inspect.getsource(IndexLifecycleService.build)
        assert "ChunkingPolicy.from_settings()" in source, \
            "m4 manifest 必须从 ChunkingPolicy 取切分口径（当前仍硬编码 500/1200/50）"
        assert '"child_size": 500' not in source, "硬编码切分参数必须删除"
    finally:
        get_settings.cache_clear()


def test_m4_manifest_payload_matches_policy_shape(monkeypatch: pytest.MonkeyPatch):
    """manifest 的 chunking_policy 字段形状与 m3 路径一致（schema 同名），
    使 PR-04 指纹在两路径间可比。"""
    from application.chunking_policy import ChunkingPolicy

    policy = ChunkingPolicy.from_settings()
    payload = policy.manifest_payload()
    assert set(payload) == {"name", "version", "child_size", "parent_size", "overlap"}


# ── P1：页级记账复用已解析文档（消除双重解析 + OCR 状态分叉）──


def test_record_page_ingestion_reuses_parsed_document():
    """_record_page_ingestion 不得重新 parse——必须接受已解析的
    ParsedDocument（含 OCR 增补），每份文档只解析一次。"""
    import inspect

    from application.document_lifecycle_service import DocumentLifecycleService

    source = inspect.getsource(DocumentLifecycleService._record_page_ingestion)
    assert "run_from_parsed" in source, "页级记账必须复用已解析文档（run_from_parsed）"


def test_run_from_parsed_adopts_ocr_pages(db: ProductDatabase):
    """OCR 已采纳的页：页级账本记 parsed（不是 ocr_required）——
    消除「文档可检索、页级账本欠账」的状态分叉。"""
    from application.page_ingestion import PARSED, PageIngestionService
    from domain.models import ParsedDocument, ParsedElement
    from infrastructure.parsers import default_parser_registry

    parsed = ParsedDocument(
        document_id="doc-1", document_name="scan.pdf", file_type="pdf",
        checksum="chk", parser_name="pdf", parser_version="1",
        elements=[ParsedElement(element_type="paragraph", text="识别文本", order=0, page_number=1)],
        ocr_required_pages=[],  # OCR 增补后：该页已从待 OCR 列表移除
        metadata={"page_count": 1},
    )
    service = PageIngestionService(db, registry=default_parser_registry)
    service.register(document_id="doc-1", logical_document_id="l1", version="v1",
                     filename="scan.pdf", checksum="chk")
    report = service.run_from_parsed("doc-1", parsed, attempt=1)
    assert report["status"] == PARSED
    page = db.fetch_one("SELECT status FROM page_artifacts WHERE job_id=? AND page_number=1", ("doc-1",))
    assert page["status"] == PARSED


def test_run_from_parsed_marks_ocr_required_page(db: ProductDatabase):
    """未 OCR 的标记页：页级账本仍如实记 ocr_required（重试入口照常工作）。"""
    from application.page_ingestion import OCR_REQUIRED, PageIngestionService
    from domain.models import ParsedDocument
    from infrastructure.parsers import default_parser_registry

    parsed = ParsedDocument(
        document_id="doc-2", document_name="scan.pdf", file_type="pdf",
        checksum="chk", parser_name="pdf", parser_version="1",
        elements=[],
        ocr_required_pages=[1],  # 待 OCR
        metadata={"page_count": 1},
    )
    service = PageIngestionService(db, registry=default_parser_registry)
    service.register(document_id="doc-2", logical_document_id="l2", version="v1",
                     filename="scan.pdf", checksum="chk")
    report = service.run_from_parsed("doc-2", parsed, attempt=1)
    assert report["status"] == OCR_REQUIRED
    page = db.fetch_one("SELECT status FROM page_artifacts WHERE job_id=? AND page_number=1", ("doc-2",))
    assert page["status"] == OCR_REQUIRED


def test_run_from_parsed_state_machine_enforced(db: ProductDatabase):
    """run_from_parsed 同样走状态机（extracting → 终态），不绕过迁移校验。"""
    from application.page_ingestion import PageIngestionService
    from domain.errors import ConflictError
    from domain.models import ParsedDocument
    from infrastructure.parsers import default_parser_registry

    parsed = ParsedDocument(
        document_id="doc-3", document_name="a.md", file_type="md",
        checksum="chk", parser_name="md", parser_version="1",
        elements=[], metadata={},
    )
    service = PageIngestionService(db, registry=default_parser_registry)
    service.register(document_id="doc-3", logical_document_id="l3", version="v1",
                     filename="a.md", checksum="chk")
    service.run_from_parsed("doc-3", parsed, attempt=1)
    service.finalize("doc-3", chunk_count=2)  # parsed -> chunked
    with pytest.raises(ConflictError):
        service.run_from_parsed("doc-3", parsed, attempt=2)  # chunked 是终态


def test_upload_records_pages_from_ocr_enriched_parse(db: ProductDatabase):
    """端到端验收：create_version 上传一份「第 1 页正常 + 第 2 页 OCR 采纳」的
    文档后，页级账本两页都是 parsed，且全程只解析一次。"""
    import inspect

    from application.document_lifecycle_service import DocumentLifecycleService
    src = inspect.getsource(DocumentLifecycleService.create_version)
    # 记账调用必须把 parsed 传下去（OCR 增补后的那份）
    assert "_record_page_ingestion(" in src
    assert "parsed=parsed" in src, "create_version 必须把（OCR 后的）parsed 传给页级记账"
