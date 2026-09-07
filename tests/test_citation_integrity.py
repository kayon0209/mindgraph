"""M0-A：引用标注完整性校验器专项测试（评审方案测试清单 test_citation_integrity.py）。

覆盖 CitationIntegrityValidator 的完整判定面：
- 格式：合法 [citation-N]、畸形（[citation-]、[citation-abc]、[citation-1-2]）；
- 集合完整性：越界标注、重复标注、未使用引用；
- 判定语义：applicable/passed 与「无标注且无引用不可判定」边界；
- 引用集合双身份来源：citation_id 与 final_rank 取并集；
- citation_marker_warning 的审计条目格式化。

与 evidence_fidelity 的区别：本校验器是更严格的完整视图（含格式/重复/未用
引用），当前仅由答案评测消费，不接入线上 Chat 生成路径。
"""

from __future__ import annotations

from application.citation_integrity import (
    CITATION_MARK_PATTERN,
    CitationIntegrityValidator,
    citation_marker_warning,
)


def test_valid_marks_within_set_pass():
    report = CitationIntegrityValidator(citation_ids=["citation-1", "citation-2"]).validate("依据 [citation-2] 与 [citation-1] 执行。")
    assert report.passed is True
    assert report.applicable is True
    assert report.valid_markers == ["[citation-2]", "[citation-1]"]
    assert report.unknown_markers == []
    assert report.malformed_markers == []
    assert report.duplicate_markers == []
    assert report.unused_citations == []


def test_rank_only_citation_set_is_accepted():
    validator = CitationIntegrityValidator(citation_ranks=[1, 3])
    report = validator.validate("见 [citation-3] 和 [citation-1]。")
    assert report.passed is True
    assert validator.known_ranks == {1, 3}
    assert validator.known_citation_ids == {"citation-1", "citation-3"}


def test_citation_id_and_rank_sources_are_unioned():
    validator = CitationIntegrityValidator(citation_ids=["citation-1", "citation-2"], citation_ranks=[2, 3])
    assert validator.known_ranks == {1, 2, 3}
    assert validator.known_citation_ids == {"citation-1", "citation-2", "citation-3"}


def test_out_of_range_marker_is_unknown_and_fails():
    report = CitationIntegrityValidator(citation_ranks=[1]).validate("[citation-1] 与 [citation-9]")
    assert report.passed is False
    assert report.unknown_markers == ["[citation-9]"]
    assert report.valid_markers == ["[citation-1]"]


def test_repeated_marker_is_duplicate_and_fails():
    report = CitationIntegrityValidator(citation_ranks=[1]).validate("见 [citation-1]，再次见 [citation-1]。")
    assert report.passed is False
    assert report.duplicate_markers == ["[citation-1]"]


def test_unused_citation_fails_even_when_all_marks_resolve():
    report = CitationIntegrityValidator(citation_ranks=[1, 2]).validate("只用 [citation-1]。")
    assert report.passed is False
    assert report.unused_citations == ["citation-2"]


def test_malformed_like_markers_are_collected():
    report = CitationIntegrityValidator(citation_ranks=[1]).validate(
        "畸形标注：[citation-]、[citation-abc]、[citation-1-2]，正常标注 [citation-1]。"
    )
    assert report.passed is False
    assert set(report.malformed_markers) == {"[citation-]", "[citation-abc]", "[citation-1-2]"}


def test_marks_order_preserved_and_valid_deduplicated():
    report = CitationIntegrityValidator(citation_ranks=[1, 2]).validate("[citation-2]，[citation-1]，[citation-2]")
    assert report.markers == ["[citation-2]", "[citation-1]", "[citation-2]"]
    assert report.valid_markers == ["[citation-2]", "[citation-1]"]


def test_no_markers_and_no_citations_is_not_applicable_and_passes():
    report = CitationIntegrityValidator().validate("纯文本，无引用。")
    assert report.applicable is False
    assert report.passed is True


def test_citations_but_no_marks_is_strictly_flagged_unused():
    """严格完整性视图：引用集合存在但正文从未标注 → 全部引用被判未使用并失败。

    与 evidence_fidelity 的一向检查（标注缺失才告警）互补：完整视图要求
    「引用集合里的每一项都至少被一个合法标注引用」（模块 docstring 第 4 条）。
    """
    report = CitationIntegrityValidator(citation_ranks=[1, 2]).validate("引用未标注，但集合存在。")
    assert report.applicable is True
    assert report.passed is False
    assert report.unused_citations == ["citation-1", "citation-2"]


def test_non_citation_id_is_ignored_in_set_identity():
    validator = CitationIntegrityValidator(citation_ids=["doc-x", "citation-2", 7, None])
    assert validator.known_citation_ids == {"citation-2"}
    assert validator.known_ranks == {2}


def test_rank_type_guards_reject_bools_and_non_positive():
    validator = CitationIntegrityValidator(citation_ranks=[True, 0, -3, 5])
    assert validator.known_ranks == {5}
    assert validator.known_citation_ids == {"citation-5"}


def test_citation_pattern_only_matches_canonical_form():
    text = "[citation-2] [citation-] [citation-abc] [citation-1-2] [1] [citation]"
    assert CITATION_MARK_PATTERN.findall(text) == ["2"]


def test_warning_format_for_mixed_failures():
    validator = CitationIntegrityValidator(citation_ranks=[1])
    report = validator.validate("[citation-1] [citation-7] [citation-1] [citation-]")
    warning = citation_marker_warning(report)
    assert warning is not None
    assert warning.startswith("citation_marker_integrity:")
    assert "malformed=[citation-]" in warning
    assert "unknown=[citation-7]" in warning
    assert "duplicate=[citation-1]" in warning


def test_warning_omits_absent_failure_kinds():
    report = CitationIntegrityValidator(citation_ranks=[1, 2]).validate("[citation-2] 缺失标注引用 1")
    warning = citation_marker_warning(report)
    assert warning == "citation_marker_integrity:unused=citation-1"


def test_warning_none_when_passed():
    report = CitationIntegrityValidator(citation_ranks=[1]).validate("[citation-1]")
    assert citation_marker_warning(report) is None
