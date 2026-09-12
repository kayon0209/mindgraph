"""语料摄取可见性回归：非递归 glob 跳过的子树必须报出来。

`load_markdown_chunks` 用 `docs_dir.glob("*.md")` —— 非递归。子目录里的
Markdown 会被静默跳过：本机 `knowledge/` 下只有 4 个顶层文件进了检索索引，
`policies/`(7) / `workflows/`(3) / `cases/`(2) / `external/public/`(9) 共 21 个
文件既没进索引，也没有任何提示。这份测试锁住「不改摄取范围、但必须可见」。
"""
from __future__ import annotations

import logging

from document_loader import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, load_markdown_chunks


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _corpus(root):
    _write(root / "费用报销管理制度.md", "# 总则\n\n差旅费报销时限为十个工作日。\n")
    _write(root / "policies" / "travel-domestic-v3.md", "# 国内差旅\n\n经济舱标准。\n")
    _write(root / "policies" / "invoice-compliance-v1.md", "# 发票\n\n电子发票须打印。\n")
    _write(root / "external" / "public" / "gitlab-travel-expense.md", "# GitLab\n\nSubmit within 30 days.\n")


def test_subtrees_are_reported_when_skipped(tmp_path):
    _corpus(tmp_path)
    handler = _Capture()
    logger = logging.getLogger("mindgraph.document_loader")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        records = load_markdown_chunks(
            tmp_path, origin="official",
            chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        )
    finally:
        logger.removeHandler(handler)

    # 行为未变：仍然只摄取顶层文件（扩不扩范围是产品决策，这里不擅自改）
    assert {item["metadata"]["doc_name"] for item in records} == {"费用报销管理制度.md"}

    events = [r for r in handler.records if r.getMessage() == "markdown_subtrees_not_scanned"]
    assert len(events) == 1
    event = events[0]
    assert event.scanned_top_level_files == 1
    assert event.unscanned_subtrees == {"policies": 2, "external/public": 1}


def test_no_warning_when_everything_is_top_level(tmp_path):
    _write(tmp_path / "费用报销管理制度.md", "# 总则\n\n差旅费报销时限为十个工作日。\n")
    handler = _Capture()
    logger = logging.getLogger("mindgraph.document_loader")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        load_markdown_chunks(tmp_path, origin="official")
    finally:
        logger.removeHandler(handler)

    assert [r for r in handler.records if r.getMessage() == "markdown_subtrees_not_scanned"] == []


def test_recursive_pattern_does_not_warn(tmp_path):
    """调用方若显式传 **/*.md，就不该再报「有子树没扫」。"""
    _corpus(tmp_path)
    handler = _Capture()
    logger = logging.getLogger("mindgraph.document_loader")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        records = load_markdown_chunks(tmp_path, origin="official", glob_pattern="**/*.md")
    finally:
        logger.removeHandler(handler)

    assert len({item["metadata"]["doc_name"] for item in records}) == 4
    assert [r for r in handler.records if r.getMessage() == "markdown_subtrees_not_scanned"] == []


# ── 已声明语料范围（settings.INDEX_INCLUDED_SUBTREES，2026-09-10 拍板） ──────────
# 语料只收 vault 根目录是**已接受的口径**。若不把"范围外跳过"降级，
# 每次重建都会刷一条 WARNING —— 长期为真的告警等于没有告警。


def _capture(level):
    handler = _Capture()
    logger = logging.getLogger("mindgraph.document_loader")
    logger.addHandler(handler)
    logger.setLevel(level)
    return handler, logger


def test_declared_root_only_scope_downgrades_notice_to_info(tmp_path):
    """声明 `仅根目录` 时，子目录跳过属预期 → INFO，不再 WARNING。"""
    _corpus(tmp_path)
    handler, logger = _capture(logging.INFO)
    try:
        load_markdown_chunks(tmp_path, origin="official", included_subtrees=())
    finally:
        logger.removeHandler(handler)

    assert [r for r in handler.records if r.getMessage() == "markdown_subtrees_not_scanned"] == []
    infos = [r for r in handler.records if r.getMessage() == "markdown_subtrees_out_of_declared_scope"]
    assert len(infos) == 1
    assert infos[0].levelno == logging.INFO
    # 不断言子树键的字面量（Windows 下分隔符是反斜杠），只断言归属与规模
    assert infos[0].unscanned_subtrees.get("policies") == 2
    assert sum(infos[0].unscanned_subtrees.values()) == 3


def test_in_scope_subtree_still_warns_under_declared_scope(tmp_path):
    """一旦把某子树纳入声明范围，它的缺失就必须继续是 WARNING。"""
    _corpus(tmp_path)
    handler, logger = _capture(logging.INFO)
    try:
        load_markdown_chunks(tmp_path, origin="official", included_subtrees=("policies",))
    finally:
        logger.removeHandler(handler)

    warnings = [r for r in handler.records if r.getMessage() == "markdown_subtrees_not_scanned"]
    assert len(warnings) == 1
    assert warnings[0].unscanned_subtrees == {"policies": 2}
    infos = [r for r in handler.records if r.getMessage() == "markdown_subtrees_out_of_declared_scope"]
    assert len(infos) == 1
    assert sum(infos[0].unscanned_subtrees.values()) == 1
    assert "policies" not in infos[0].unscanned_subtrees
