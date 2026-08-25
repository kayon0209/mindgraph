from datetime import date
from itertools import permutations

import pytest

from application.governance_policy import GovernancePolicy
from domain.errors import GovernanceConflictError, GovernanceUnavailableError
from domain.governance import (
    ConfirmedGovernanceDecision,
    GovernanceDisposition,
    GovernanceMode,
    GovernanceNote,
    LifecycleState,
)


def note(**overrides: object) -> GovernanceNote:
    values: dict[str, object] = {
        "note_id": "policy-v1",
        "source_id": "builtin",
        "owner": "Finance",
        "policy_key": "expense-policy",
        "document_version": "1.0",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "policy_status": "active",
        "metadata_issues": (),
        "workspace": "corp",
        "department": "finance",
        "acl_json": '{"allow":["workspace:corp"]}',
        "acl_public": False,
        "content_hash": "sha256:a",
    }
    values.update(overrides)
    return GovernanceNote(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("as_of", "state", "disposition", "reason"),
    [
        (date(2025, 12, 31), LifecycleState.NOT_YET_EFFECTIVE, GovernanceDisposition.EXCLUDED, "not_yet_effective"),
        (date(2026, 1, 1), LifecycleState.CURRENT, GovernanceDisposition.ELIGIBLE, "eligible_current_version"),
        (date(2026, 12, 31), LifecycleState.CURRENT, GovernanceDisposition.ELIGIBLE, "eligible_current_version"),
        (date(2027, 1, 1), LifecycleState.EXPIRED, GovernanceDisposition.EXCLUDED, "effective_period_ended"),
    ],
)
def test_current_mode_date_boundaries(as_of, state, disposition, reason) -> None:
    """Catches inclusive policy boundaries being shifted by one day."""
    result = GovernancePolicy().evaluate(
        note(effective_to="2026-12-31"), as_of=as_of, mode=GovernanceMode.CURRENT
    )

    assert result.lifecycle_state is state
    assert result.disposition is disposition
    assert reason in result.reason_codes


def test_invalid_date_is_unresolved_not_an_exception() -> None:
    """Catches malformed source dates being treated as a usable policy."""
    result = GovernancePolicy().evaluate(
        note(effective_from="2026-13-01"),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("invalid_effective_date",)


def test_invalid_status_is_unresolved_not_a_current_candidate() -> None:
    """Catches an unsupported source status being treated as a meaningful exclusion."""
    result = GovernancePolicy().evaluate(
        note(policy_status="unreviewed"), as_of=date(2026, 8, 25), mode=GovernanceMode.CURRENT
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("invalid_policy_status",)


def test_governance_errors_expose_distinct_retry_and_conflict_contracts() -> None:
    """Catches governance failures being surfaced with generic HTTP error semantics."""
    assert (GovernanceUnavailableError.code, GovernanceUnavailableError.status_code) == (
        "governance_unavailable",
        503,
    )
    assert (GovernanceConflictError.code, GovernanceConflictError.status_code) == (
        "governance_conflict",
        409,
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("draft", "declared_draft"),
        ("archived", "declared_archived"),
        ("superseded", "declared_superseded"),
        ("expired", "declared_expired"),
        ("unspecified", "declared_unspecified"),
    ],
)
def test_current_mode_excludes_non_active_policy_statuses(status: str, reason: str) -> None:
    """Catches a terminal or unpublished status leaking into current retrieval."""
    result = GovernancePolicy().evaluate(
        note(policy_status=status), as_of=date(2026, 8, 25), mode=GovernanceMode.CURRENT
    )

    assert result.disposition is GovernanceDisposition.EXCLUDED
    assert result.eligible is False
    assert result.reason_codes == (reason,)


@pytest.mark.parametrize("field", ["owner", "policy_key", "document_version", "effective_from"])
def test_missing_required_metadata_is_unresolved(field: str) -> None:
    """Catches incomplete governance records being promoted to retrieval candidates."""
    result = GovernancePolicy().evaluate(
        note(**{field: None}), as_of=date(2026, 8, 25), mode=GovernanceMode.CURRENT
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == (f"missing_{field}",)


def test_reversed_effective_range_is_unresolved() -> None:
    """Catches range validation comparing dates lexicographically or not at all."""
    result = GovernancePolicy().evaluate(
        note(effective_from="2026-12-31", effective_to="2026-01-01"),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("invalid_effective_range",)


def test_confirmed_duplicate_alias_is_never_eligible() -> None:
    """Catches a confirmed canonicalization being ignored by policy evaluation."""
    decision = ConfirmedGovernanceDecision(
        note_id="policy-v1",
        disposition=GovernanceDisposition.DUPLICATE_ALIAS,
        reason_code="confirmed_duplicate_alias",
        canonical_note_id="policy-canonical",
    )
    result = GovernancePolicy().evaluate(
        note(),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
        confirmed_decisions=(decision,),
    )

    assert result.disposition is GovernanceDisposition.DUPLICATE_ALIAS
    assert result.eligible is False
    assert result.canonical_note_id == "policy-canonical"


def test_confirmed_conflict_block_has_order_independent_priority() -> None:
    """Catches input ordering letting an alias or approval bypass a confirmed block."""
    decisions = (
        ConfirmedGovernanceDecision(
            "policy-v1", GovernanceDisposition.ELIGIBLE, "confirmed_eligible"
        ),
        ConfirmedGovernanceDecision(
            "policy-v1",
            GovernanceDisposition.DUPLICATE_ALIAS,
            "confirmed_duplicate_alias",
            "canonical-policy",
        ),
        ConfirmedGovernanceDecision(
            "policy-v1", GovernanceDisposition.CONFLICT_BLOCKED, "confirmed_conflict"
        ),
    )

    for ordered in permutations(decisions):
        result = GovernancePolicy().evaluate(
            note(owner=None),
            as_of=date(2026, 8, 25),
            mode=GovernanceMode.CURRENT,
            confirmed_decisions=ordered,
        )
        assert result.disposition is GovernanceDisposition.CONFLICT_BLOCKED
        assert result.eligible is False
        assert result.reason_codes == ("confirmed_conflict",)


@pytest.mark.parametrize(
    ("decisions", "disposition", "reason", "canonical_note_id"),
    [
        (
            (
                ConfirmedGovernanceDecision(
                    "policy-v1",
                    GovernanceDisposition.CONFLICT_BLOCKED,
                    "confirmed_conflict",
                ),
                ConfirmedGovernanceDecision(
                    "policy-v1",
                    GovernanceDisposition.CONFLICT_BLOCKED,
                    "confirmed_conflict",
                ),
                ConfirmedGovernanceDecision(
                    "policy-v1",
                    GovernanceDisposition.DUPLICATE_ALIAS,
                    "confirmed_duplicate_alias",
                    "canonical-policy",
                ),
            ),
            GovernanceDisposition.CONFLICT_BLOCKED,
            "confirmed_conflict",
            None,
        ),
        (
            (
                ConfirmedGovernanceDecision(
                    "policy-v1",
                    GovernanceDisposition.DUPLICATE_ALIAS,
                    "confirmed_duplicate_alias",
                    "canonical-policy",
                ),
                ConfirmedGovernanceDecision(
                    "policy-v1",
                    GovernanceDisposition.DUPLICATE_ALIAS,
                    "confirmed_duplicate_alias",
                    "canonical-policy",
                ),
                ConfirmedGovernanceDecision(
                    "policy-v1", GovernanceDisposition.ELIGIBLE, "confirmed_eligible"
                ),
            ),
            GovernanceDisposition.DUPLICATE_ALIAS,
            "confirmed_duplicate_alias",
            "canonical-policy",
        ),
    ],
)
def test_identical_highest_priority_confirmed_decisions_fold_deterministically(
    decisions, disposition, reason, canonical_note_id
) -> None:
    """Catches repeated confirmed records changing the selected result by tuple order."""
    for ordered in permutations(decisions):
        result = GovernancePolicy().evaluate(
            note(),
            as_of=date(2026, 8, 25),
            mode=GovernanceMode.CURRENT,
            confirmed_decisions=ordered,
        )
        assert result.disposition is disposition
        assert result.reason_codes == (reason,)
        assert result.canonical_note_id == canonical_note_id


@pytest.mark.parametrize(
    "decisions",
    [
        (
            ConfirmedGovernanceDecision(
                "policy-v1",
                GovernanceDisposition.CONFLICT_BLOCKED,
                "confirmed_conflict_a",
            ),
            ConfirmedGovernanceDecision(
                "policy-v1",
                GovernanceDisposition.CONFLICT_BLOCKED,
                "confirmed_conflict_b",
            ),
            ConfirmedGovernanceDecision(
                "policy-v1",
                GovernanceDisposition.DUPLICATE_ALIAS,
                "confirmed_duplicate_alias",
                "canonical-policy",
            ),
        ),
        (
            ConfirmedGovernanceDecision(
                "policy-v1",
                GovernanceDisposition.DUPLICATE_ALIAS,
                "confirmed_duplicate_alias_a",
                "canonical-policy-a",
            ),
            ConfirmedGovernanceDecision(
                "policy-v1",
                GovernanceDisposition.DUPLICATE_ALIAS,
                "confirmed_duplicate_alias_b",
                "canonical-policy-b",
            ),
            ConfirmedGovernanceDecision(
                "policy-v1", GovernanceDisposition.ELIGIBLE, "confirmed_eligible"
            ),
        ),
        (
            ConfirmedGovernanceDecision(
                "policy-v1", GovernanceDisposition.ELIGIBLE, "confirmed_eligible_a"
            ),
            ConfirmedGovernanceDecision(
                "policy-v1", GovernanceDisposition.ELIGIBLE, "confirmed_eligible_b"
            ),
        ),
    ],
)
def test_conflicting_highest_priority_confirmed_decisions_fail_closed_in_every_order(decisions) -> None:
    """Catches arbitrary selection between legally shaped but contradictory governance records."""
    for ordered in permutations(decisions):
        result = GovernancePolicy().evaluate(
            note(),
            as_of=date(2026, 8, 25),
            mode=GovernanceMode.CURRENT,
            confirmed_decisions=ordered,
        )
        assert result.disposition is GovernanceDisposition.UNRESOLVED
        assert result.eligible is False
        assert result.reason_codes == ("conflicting_confirmed_decisions",)
        assert result.canonical_note_id is None


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (note(owner=None), "missing_owner"),
        (note(effective_from="2026-13-01"), "invalid_effective_date"),
        (note(policy_status="draft"), "declared_draft"),
    ],
)
def test_confirmed_positive_decision_does_not_bypass_source_governance(candidate, reason) -> None:
    """Catches an approval decision making malformed, expired, or draft source evidence eligible."""
    result = GovernancePolicy().evaluate(
        candidate,
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
        confirmed_decisions=(
            ConfirmedGovernanceDecision(
                "policy-v1", GovernanceDisposition.ELIGIBLE, "confirmed_eligible"
            ),
        ),
    )

    assert result.eligible is False
    assert result.reason_codes == (reason,)


@pytest.mark.parametrize(
    "decision",
    [
        ConfirmedGovernanceDecision(
            "policy-v1", GovernanceDisposition.DUPLICATE_ALIAS, "alias_without_canonical"
        ),
        ConfirmedGovernanceDecision(
            "policy-v1", GovernanceDisposition.CONFLICT_BLOCKED, "block_with_canonical", "other"
        ),
        ConfirmedGovernanceDecision(
            "policy-v1", GovernanceDisposition.ELIGIBLE, "approval_with_canonical", "other"
        ),
        ConfirmedGovernanceDecision("policy-v1", GovernanceDisposition.ELIGIBLE, ""),
    ],
)
def test_malformed_confirmed_decision_fails_closed(decision) -> None:
    """Catches malformed confirmed governance records being used as authority."""
    result = GovernancePolicy().evaluate(
        note(),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
        confirmed_decisions=(decision,),
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("invalid_confirmed_decision",)


def test_historical_mode_accepts_proven_superseded_interval() -> None:
    """Catches historical mode discarding a version proven effective on the query date."""
    result = GovernancePolicy().evaluate(
        note(policy_status="superseded", effective_to="2026-06-30"),
        as_of=date(2026, 4, 1),
        mode=GovernanceMode.HISTORICAL,
    )

    assert result.lifecycle_state is LifecycleState.HISTORICAL
    assert result.eligible is True
    assert result.reason_codes == ("eligible_historical_version",)


def test_explicit_unspecified_status_is_excluded_in_historical_mode() -> None:
    """Catches an explicit non-operational lifecycle status becoming historical evidence."""
    result = GovernancePolicy().evaluate(
        note(policy_status="unspecified"),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.HISTORICAL,
    )

    assert result.disposition is GovernanceDisposition.EXCLUDED
    assert result.reason_codes == ("declared_unspecified",)


@pytest.mark.parametrize("status", [None, 17, ""])
def test_non_string_or_empty_status_fails_closed(status: object) -> None:
    """Catches unchecked status values raising AttributeError during governance evaluation."""
    result = GovernancePolicy().evaluate(
        note(policy_status=status), as_of=date(2026, 8, 25), mode=GovernanceMode.CURRENT
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("invalid_policy_status",)


@pytest.mark.parametrize("status", ["superseded", "expired", "archived"])
def test_historical_terminal_status_requires_a_provable_interval(status: str) -> None:
    """Catches a historical terminal record without an end date being asserted as valid evidence."""
    result = GovernancePolicy().evaluate(
        note(policy_status=status), as_of=date(2026, 8, 25), mode=GovernanceMode.HISTORICAL
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("historical_status_without_effective_to",)


def test_note_metadata_issues_are_returned_as_unresolved_reason_codes() -> None:
    """Catches malformed persisted metadata quality markers being silently ignored."""
    result = GovernancePolicy().evaluate(
        note(metadata_issues=("invalid_effective_date", "malformed_metadata_issues_json")),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
    )

    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("invalid_effective_date", "malformed_metadata_issues_json")


def test_exact_duplicate_requires_security_and_source_equivalence() -> None:
    """Catches deduplication collapsing records from different sources or security scopes."""
    policy = GovernancePolicy()
    assert policy.exact_duplicate_equivalent(note(note_id="a"), note(note_id="b"))
    assert policy.exact_duplicate_equivalent(
        note(note_id="a"),
        note(note_id="b", acl_json='{ "allow": ["workspace:corp", "workspace:corp"] }'),
    )
    assert not policy.exact_duplicate_equivalent(
        note(note_id="a"), note(note_id="b", acl_json='{ "allow": ["workspace:other"] }')
    )
    assert not policy.exact_duplicate_equivalent(note(note_id="a"), note(note_id="b", source_id="connector-b"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"acl_json": "not-json"},
        {"acl_json": '["workspace:corp"]'},
        {"metadata_issues": ("missing_owner",)},
        {"policy_status": "unreviewed"},
    ],
)
def test_exact_duplicate_fails_closed_for_unresolved_or_malformed_governance(overrides) -> None:
    """Catches unsafe metadata being collapsed into a trusted duplicate alias."""
    policy = GovernancePolicy()
    assert not policy.exact_duplicate_equivalent(note(note_id="a", **overrides), note(note_id="b", **overrides))
