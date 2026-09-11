"""确定性引用标注完整性校验（M0-A 任务三）。

只验证「标注格式」与「引用集合完整性」，不声称验证语义支持：

- 格式：答案正文中 ``[citation-N]`` 标注必须符合规范形态；形似标注但无法解析
  （如 ``[citation-]``、``[citation-abc]``、``[citation-1-2]``）记为畸形；
- 引用集合完整性：所有合法标注必须命中实际引用集合（按 ``citation_id`` /
  ``final_rank`` 映射的引用序号），同一标注不得重复使用，实际返回的引用也不应
  从未在正文中被标注引用。

输出字段（详见 :class:`CitationIntegrityReport`）：``markers``、
``valid_markers``、``unknown_markers``、``malformed_markers``、
``duplicate_markers``、``unused_citations``、``passed``、``applicable``。

与 :mod:`application.evidence_fidelity` 的区别：后者只检查「标注 → 引用集合」
单向缺失（warning-first，已接入 Chat 生成路径）；本模块是评测用的更严格完整性
视图（含格式、重复、未用引用），**本轮不接入线上 Chat 生成路径**，仅由 answer
evaluation 消费。

``passed`` 判定（全部满足才为 True）：
1. 无畸形标注；
2. 无越界标注（合法标注全部命中引用集合）；
3. 无重复标注；
4. 无未使用引用（引用集合里的每一项都至少被一个合法标注引用）——
   由构造参数 ``require_all_citations_used`` 控制，默认 True；当引用集合的语义是
   「提供给模型的候选证据」而非「应被全部引用的清单」时，调用方应传 False。
无标注且无引用时 ``applicable=False``（不可判定），此时 ``passed`` 恒为 True。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import re

# 规范标注：整段必须是 [citation-N]，N 为正整数
CITATION_MARK_PATTERN = re.compile(r"\[citation-(\d+)\]")
# 形似标注的 token：左括号 [citation- 到最近的 ]，用于识别畸形标注
_MALFORMED_LIKE_PATTERN = re.compile(r"\[citation-[^\]]*\]")


def _citation_id_for_rank(rank: int) -> str:
    return f"citation-{rank}"


def _rank_from_citation_id(citation_id: str) -> int | None:
    if not citation_id.startswith("citation-"):
        return None
    suffix = citation_id[len("citation-") :]
    return int(suffix) if suffix.isdigit() else None


@dataclass(frozen=True)
class CitationIntegrityReport:
    """一次引用标注完整性检查的结果。

    - ``markers``: 答案中出现过的全部合法 ``[citation-N]`` 标注（按出现顺序，含重复）；
    - ``valid_markers``: 命中引用集合的合法标注（去重后按出现顺序）；
    - ``unknown_markers``: 合法但序号不在引用集合内的标注（越界）；
    - ``malformed_markers``: 形似标注但格式非法的 token（按出现顺序）；
    - ``duplicate_markers``: 在答案中重复出现的标注（去重）；
    - ``unused_citations``: 引用集合中从未被任何合法标注引用的引用标识；
    - ``passed``: 四项判定全部通过（见模块 docstring）；
    - ``applicable``: 是否有可判定对象（有标注或引用集合非空）。
    """

    markers: list[str] = field(default_factory=list)
    valid_markers: list[str] = field(default_factory=list)
    unknown_markers: list[str] = field(default_factory=list)
    malformed_markers: list[str] = field(default_factory=list)
    duplicate_markers: list[str] = field(default_factory=list)
    unused_citations: list[str] = field(default_factory=list)
    passed: bool = True
    applicable: bool = False


class CitationIntegrityValidator:
    """校验答案正文中的引用标注格式与引用集合完整性。

    引用集合的两种等价身份来源（可同时提供，取并集）：
    - ``citation_ids``: 形如 ``citation-N`` 的引用标识（优先语义）；
    - ``citation_ranks``: 整数 ``final_rank``（无显式 citation_id 时的回退）。
    """

    def __init__(
        self,
        citation_ids: Iterable[str] | None = None,
        citation_ranks: Iterable[int] | None = None,
        require_all_citations_used: bool = True,
    ) -> None:
        # P0 契约澄清：``require_all_citations_used`` 控制第 4 条判定。
        # 运行时契约里 ``citations`` 是「提供给模型的候选证据」，系统提示词只要求
        # 「使用 [citation-N] 标注引用来源」，并未要求每条候选都被引用；此时
        # 「未使用引用」不是缺陷，评测应以 False 关闭该门（未使用数量仍会照常
        # 记录在 ``unused_citations`` 里供审计）。默认 True 保持本模块原有的严格
        # 语义不变。
        self._require_all_citations_used = require_all_citations_used
        self._citation_ids: set[str] = set()
        self._ranks: set[int] = set()
        for citation_id in citation_ids or ():
            if not isinstance(citation_id, str):
                continue
            # 只接受规范 citation-N 形式：非规范 id 无法被任何合法标注命中，
            # 若保留在集合里会制造「永远未使用」的恒真失败。
            rank = _rank_from_citation_id(citation_id)
            if rank is None:
                continue
            self._citation_ids.add(citation_id)
            self._ranks.add(rank)
        for rank in citation_ranks or ():
            if isinstance(rank, int) and not isinstance(rank, bool) and rank > 0:
                self._ranks.add(rank)
                self._citation_ids.add(_citation_id_for_rank(rank))

    @property
    def known_ranks(self) -> set[int]:
        return set(self._ranks)

    @property
    def known_citation_ids(self) -> set[str]:
        return set(self._citation_ids)

    def validate(self, answer: str) -> CitationIntegrityReport:
        text = answer or ""
        markers = [match.group(0) for match in CITATION_MARK_PATTERN.finditer(text)]
        marker_ranks = [int(match.group(1)) for match in CITATION_MARK_PATTERN.finditer(text)]

        malformed = [token for token in _MALFORMED_LIKE_PATTERN.findall(text) if not CITATION_MARK_PATTERN.fullmatch(token)]
        # 去重但保持出现顺序
        seen_malformed: set[str] = set()
        malformed_markers: list[str] = []
        for token in malformed:
            if token not in seen_malformed:
                malformed_markers.append(token)
                seen_malformed.add(token)

        duplicate_marks = sorted({marker for marker in markers if markers.count(marker) > 1})

        unknown_marks: list[str] = []
        valid_marks: list[str] = []
        seen_valid: set[str] = set()
        seen_unknown: set[str] = set()
        for marker, rank in zip(markers, marker_ranks, strict=True):
            if rank in self._ranks:
                if marker not in seen_valid:
                    valid_marks.append(marker)
                    seen_valid.add(marker)
            elif marker not in seen_unknown:
                unknown_marks.append(marker)
                seen_unknown.add(marker)

        referenced_ranks = {int(match.group(1)) for match in CITATION_MARK_PATTERN.finditer(text)}
        unused = sorted(
            self._citation_ids - {_citation_id_for_rank(rank) for rank in referenced_ranks},
            key=lambda item: (_rank_from_citation_id(item) or 0, item),
        )

        applicable = bool(markers) or bool(self._ranks)
        passed = not malformed_markers and not unknown_marks and not duplicate_marks
        if self._require_all_citations_used:
            passed = passed and not unused
        if not applicable:
            passed = True
        return CitationIntegrityReport(
            markers=list(markers),
            valid_markers=valid_marks,
            unknown_markers=unknown_marks,
            malformed_markers=malformed_markers,
            duplicate_markers=duplicate_marks,
            unused_citations=unused,
            passed=bool(passed),
            applicable=applicable,
        )


def citation_marker_warning(report: CitationIntegrityReport) -> str | None:
    """把校验结果格式化为可审计的告警条目；通过时返回 None。"""
    if report.passed:
        return None
    parts: list[str] = []
    if report.malformed_markers:
        parts.append("malformed=" + ",".join(report.malformed_markers))
    if report.unknown_markers:
        parts.append("unknown=" + ",".join(report.unknown_markers))
    if report.duplicate_markers:
        parts.append("duplicate=" + ",".join(report.duplicate_markers))
    if report.unused_citations:
        parts.append("unused=" + ",".join(report.unused_citations))
    return f"citation_marker_integrity:{';'.join(parts)}" if parts else None
