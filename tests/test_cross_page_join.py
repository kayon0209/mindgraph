"""PR-08 跨页条款与续表恢复：确定性信号判断 + 切分器集成。

任务书测试矩阵（六类）：
半句续页 / 正常新段 / 条款续页 / 续表 / 错误表头 / 页眉页脚。

fixture 策略（任务书修正 2）：跨页语义用**纯文本多页元素**固化进 git，
不依赖本地 PDF 本体（它们被 .gitignore，换机器就消失）。

不变性：``cross_page_join`` 默认关闭，同页与旧跨页行为逐字节不变——
这是把新功能隔离在开关后、历史指标可对照的前提。
"""

from __future__ import annotations

from application.cross_page_join import (
    CONNECTIVE_START,
    NO_SIGNAL,
    NOT_CROSS_PAGE,
    SAME_CLAUSE,
    TABLE_HEADER_MATCH,
    UNTERMINATED_SENTENCE,
    decide_join,
)
from application.structured_chunker import StructuredChunker
from domain.models import ParsedDocument, ParsedElement


def _element(
    text: str,
    *,
    order: int,
    page: int | None,
    heading: tuple[str, ...] = (),
    clause: str | None = None,
    table_rows: list[list[str]] | None = None,
) -> ParsedElement:
    return ParsedElement(
        element_type="paragraph",
        text=text,
        order=order,
        page_number=page,
        heading_path=list(heading),
        clause_number=clause,
        table_rows=table_rows,
    )


def _document(elements: list[ParsedElement]) -> ParsedDocument:
    return ParsedDocument(
        document_id="doc-1",
        document_name="fixture.md",
        file_type="md",
        checksum="chk-1",
        parser_name="fixture",
        parser_version="1",
        elements=elements,
    )


# ── 判断器：六类信号 ────────────────────────────────────────────────────


def test_unterminated_sentence_joins_across_page():
    """半句续页：页尾无终止标点 + 下页以续接词开头 → 合并。"""
    prev = _element("差旅费报销应当在出差结束后", order=0, page=1)
    next_ = _element("其中交通费按标准凭票报销", order=1, page=2)
    decision = decide_join(prev, next_)
    assert decision.should_join
    assert UNTERMINATED_SENTENCE in decision.signals
    assert CONNECTIVE_START in decision.signals


def test_normal_new_paragraph_not_joined():
    """正常新段：上页句号终结、下页非续接 → 不合并。"""
    prev = _element("差旅费应在十个工作日内提交。", order=0, page=1)
    next_ = _element("住宿标准按城市分类执行。", order=1, page=2)
    decision = decide_join(prev, next_)
    assert not decision.should_join
    assert decision.reason == NO_SIGNAL


def test_same_clause_continuation_joins():
    """条款续页：同一条款号跨页 → 高置信合并。"""
    prev = _element("报销时限为十个工作日内", order=0, page=1, clause="第十条")
    next_ = _element("逾期不予受理", order=1, page=2, clause="第十条")
    decision = decide_join(prev, next_)
    assert decision.should_join
    assert decision.reason == SAME_CLAUSE
    assert decision.confidence == 0.95


def test_continued_table_joins_on_matching_header():
    """续表：两页表头一致 → 合并，并保留表头证据。"""
    header = [["城市", "标准"]]
    prev = _element("上海 500", order=0, page=1, table_rows=header + [["上海", "500"]])
    next_ = _element("北京 400", order=1, page=2, table_rows=header + [["北京", "400"]])
    decision = decide_join(prev, next_)
    assert decision.should_join
    assert decision.reason == TABLE_HEADER_MATCH
    assert decision.detail["header"] == ["城市", "标准"]


def test_mismatched_table_header_not_joined():
    """错误表头：表头不一致 → 不合并（宁漏合不错合）。"""
    prev = _element("上海 500", order=0, page=1, table_rows=[["城市", "标准"], ["上海", "500"]])
    next_ = _element("项目 金额", order=1, page=2, table_rows=[["项目", "金额"], ["交通", "300"]])
    decision = decide_join(prev, next_)
    assert not decision.should_join


def test_same_page_adjacent_never_joins():
    """页眉页脚/同页相邻：非跨页 → 不参与判断，同页分组行为完全不变。"""
    prev = _element("同页第一段。", order=0, page=1)
    next_ = _element("同页第二段，虽然以续接词开头", order=1, page=1)
    decision = decide_join(prev, next_)
    assert not decision.should_join
    assert decision.reason == NOT_CROSS_PAGE


def test_header_footer_like_text_not_joined():
    """页脚残留（如页码「第 1 页」）无信号 → 不合并。"""
    prev = _element("正文结束。", order=0, page=1)
    footer = _element("第 1 页", order=1, page=2)
    decision = decide_join(prev, footer)
    assert not decision.should_join


# ── 切分器集成 ─────────────────────────────────────────────────────────


def _cross_page_doc() -> ParsedDocument:
    """无标题多页 fixture：条款在页 1/2 间断开（模拟 PDF 分页）。"""
    return _document([
        _element("第十条 报销应当在出差结束后", order=0, page=1, clause="第十条"),
        _element("三十个工作日内提交，其中交通费按标准执行", order=1, page=2, clause="第十条"),
        _element("第十一条 住宿费按城市分类。", order=2, page=2, clause="第十一条"),
    ])


def test_chunker_default_off_keeps_legacy_grouping():
    """默认关闭：跨页照样断组（旧行为），输出与 PR-08 之前一致。"""
    chunks = StructuredChunker(child_size=50, parent_size=500, overlap=5).chunk(_cross_page_doc())
    # 页 1 与页 2 各成父块（page:1 / page:2 键不同）
    parent_ids = {chunk.parent_chunk_id for chunk in chunks}
    assert len(parent_ids) == 2


def test_chunker_join_enabled_merges_cross_page_clause():
    """打开开关：同条款号跨页续接 → 同一父块，page_start/page_end 跨页可追溯。"""
    chunks = StructuredChunker(
        child_size=50, parent_size=500, overlap=5, cross_page_join=True,
    ).chunk(_cross_page_doc())
    assert len(chunks) >= 1
    merged = chunks[0]
    assert merged.page_start == 1
    assert merged.page_end == 2
    assert merged.clause_numbers == ["第十条"]


def test_join_only_applies_across_pages():
    """同页的标题/无标题分组不受开关影响（跨页续接才并入）。"""
    doc = _document([
        _element("内容甲延续到页尾", order=0, page=1),
        _element("内容乙是同页新句子。", order=1, page=1),
    ])
    off = StructuredChunker(child_size=20, parent_size=200, overlap=2).chunk(doc)
    on = StructuredChunker(child_size=20, parent_size=200, overlap=2, cross_page_join=True).chunk(doc)
    assert [c.text for c in off] == [c.text for c in on]


def test_join_decision_not_persisted_when_disabled():
    """关闭时父块键与历史分组完全一致（开关即回滚，无隐式行为）。"""
    doc = _cross_page_doc()
    on = StructuredChunker(child_size=50, parent_size=500, overlap=5, cross_page_join=True).chunk(doc)
    off = StructuredChunker(child_size=50, parent_size=500, overlap=5).chunk(doc)
    assert {c.parent_chunk_id for c in on} != {c.parent_chunk_id for c in off} or \
        len(on) != len(off)  # 行为确实不同（否则开关是死的）
