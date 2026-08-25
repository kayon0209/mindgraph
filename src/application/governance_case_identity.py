"""Pure, deterministic governance case identity discovery."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import hashlib
from typing import Any

from application.governance_policy import (
    GovernancePolicy,
    canonical_json,
    governance_metadata_dict,
)
from domain.governance import GovernanceNote


@dataclass(frozen=True, slots=True)
class GovernanceCaseCandidate:
    case_type: str
    policy_key: str
    participant_note_ids: tuple[str, ...]
    status: str
    canonical_note_id: str | None
    reason_code: str
    rule_key: str
    relevant_metadata_hash: str


def discover_governance_case_candidates(
    policy: GovernancePolicy,
    notes: Sequence[GovernanceNote],
) -> tuple[GovernanceCaseCandidate, ...]:
    """Compute current case identities without persistence or side effects."""
    groups: dict[str, list[tuple[GovernanceNote, tuple[date, date | None]]]] = (
        defaultdict(list)
    )
    for note in notes:
        interval = policy.governance_interval(note)
        if interval is not None and isinstance(note.policy_key, str) and note.policy_key:
            groups[note.policy_key].append((note, interval))

    candidates: list[GovernanceCaseCandidate] = []
    for policy_key in sorted(groups):
        governed = sorted(groups[policy_key], key=lambda item: item[0].note_id)
        candidates.extend(_duplicate_candidates(policy, policy_key, governed))
        candidates.extend(_conflict_candidates(policy, policy_key, governed))
    return tuple(sorted(candidates, key=lambda item: (item.case_type, item.rule_key)))


def _duplicate_candidates(
    policy: GovernancePolicy,
    policy_key: str,
    governed: Sequence[tuple[GovernanceNote, tuple[date, date | None]]],
) -> tuple[GovernanceCaseCandidate, ...]:
    by_checksum: dict[str, list[GovernanceNote]] = defaultdict(list)
    for note, _ in governed:
        if note.content_hash:
            by_checksum[note.content_hash].append(note)

    result: list[GovernanceCaseCandidate] = []
    for checksum in sorted(by_checksum):
        notes = sorted(by_checksum[checksum], key=lambda note: note.note_id)
        if len(notes) < 2:
            continue
        canonical = notes[0]
        equivalent = all(
            policy.exact_duplicate_equivalent(canonical, candidate)
            for candidate in notes[1:]
        )
        relevant = {
            "checksum": checksum,
            "notes": {
                note.note_id: governance_metadata_dict(note)
                for note in notes
            },
        }
        result.append(
            _case_candidate(
                "exact_duplicate",
                policy_key,
                tuple(note.note_id for note in notes),
                relevant,
                status="confirmed" if equivalent else "proposed",
                canonical_note_id=canonical.note_id if equivalent else None,
                reason_code=(
                    "exact_duplicate_equivalent"
                    if equivalent
                    else "checksum_match_requires_review"
                ),
            )
        )
    return tuple(result)


def _conflict_candidates(
    policy: GovernancePolicy,
    policy_key: str,
    governed: Sequence[tuple[GovernanceNote, tuple[date, date | None]]],
) -> tuple[GovernanceCaseCandidate, ...]:
    by_id = {note.note_id: (note, interval) for note, interval in governed}
    edges: dict[str, set[str]] = defaultdict(set)
    overlap_edges: set[tuple[str, str]] = set()
    ids = sorted(by_id)
    for index, left_id in enumerate(ids):
        left, left_interval = by_id[left_id]
        for right_id in ids[index + 1 :]:
            right, right_interval = by_id[right_id]
            if policy.exact_duplicate_equivalent(left, right):
                continue
            left_metadata = governance_metadata_dict(left)
            right_metadata = governance_metadata_dict(right)
            overlaps = _intervals_overlap(left_interval, right_interval)
            same_version_different_checksum = (
                left_metadata["document_version"] == right_metadata["document_version"]
                and left_metadata["content_hash"] != right_metadata["content_hash"]
            )
            if not overlaps and not same_version_different_checksum:
                continue
            edges[left_id].add(right_id)
            edges[right_id].add(left_id)
            if overlaps:
                overlap_edges.add((left_id, right_id))

    result: list[GovernanceCaseCandidate] = []
    unseen = set(edges)
    while unseen:
        seed = min(unseen)
        stack = [seed]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(edges[current] - component, reverse=True))
        unseen.difference_update(component)
        participant_ids = tuple(sorted(component))
        has_overlap = any(
            left_id in component and right_id in component
            for left_id, right_id in overlap_edges
        )
        relevant = {
            "intervals": {
                note_id: {
                    "effective_from": by_id[note_id][1][0].isoformat(),
                    "effective_to": (
                        by_id[note_id][1][1].isoformat()
                        if by_id[note_id][1][1] is not None
                        else None
                    ),
                }
                for note_id in participant_ids
            },
            "document_versions": {
                note_id: governance_metadata_dict(by_id[note_id][0])[
                    "document_version"
                ]
                for note_id in participant_ids
            },
            "checksums": {
                note_id: governance_metadata_dict(by_id[note_id][0])["content_hash"]
                for note_id in participant_ids
            },
        }
        result.append(
            _case_candidate(
                "version_conflict",
                policy_key,
                participant_ids,
                relevant,
                status="proposed",
                canonical_note_id=None,
                reason_code=(
                    "overlapping_effective_intervals"
                    if has_overlap
                    else "same_version_different_checksum"
                ),
            )
        )
    return tuple(result)


def _case_candidate(
    case_type: str,
    policy_key: str,
    participant_note_ids: tuple[str, ...],
    relevant_metadata: Mapping[str, Any],
    *,
    status: str,
    canonical_note_id: str | None,
    reason_code: str,
) -> GovernanceCaseCandidate:
    relevant_metadata_json = canonical_json(relevant_metadata)
    relevant_metadata_hash = hashlib.sha256(
        relevant_metadata_json.encode("utf-8")
    ).hexdigest()
    rule_payload = {
        "case_type": case_type,
        "participant_note_ids": sorted(participant_note_ids),
        "relevant_metadata": relevant_metadata,
    }
    rule_key = hashlib.sha256(canonical_json(rule_payload).encode("utf-8")).hexdigest()
    return GovernanceCaseCandidate(
        case_type,
        policy_key,
        tuple(sorted(participant_note_ids)),
        status,
        canonical_note_id,
        reason_code,
        rule_key,
        relevant_metadata_hash,
    )


def _intervals_overlap(
    left: tuple[date, date | None],
    right: tuple[date, date | None],
) -> bool:
    left_start, left_end = left
    right_start, right_end = right
    return (left_end is None or right_start <= left_end) and (
        right_end is None or left_start <= right_end
    )
