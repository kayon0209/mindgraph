"""EvidenceBundle v1 契约测试（M0 冻结基线）。

冻结对象：字段集合、result_state / next_action 取值面、状态→动作的
确定性映射、schema_version。只做加法；任何删除/重命名在此显式失败。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from domain.evidence import (
    RESULT_STATE_NEXT_ACTION,
    EvidenceBundle,
    EvidenceItem,
    EvidenceNextAction,
    EvidenceResultState,
    EvidenceRouteInfo,
)


def test_result_state_values_are_frozen():
    assert {item.value for item in EvidenceResultState} == {
        "evidence_found",
        "insufficient_evidence",
        "permission_denied",
        "conflicting_evidence",
        "out_of_scope",
        "retrieval_unavailable",
        "waiting_for_input",
    }


def test_next_action_values_are_frozen():
    assert {item.value for item in EvidenceNextAction} == {
        "generate",
        "ask_clarification",
        "request_access",
        "human_review",
        "retry",
        "stop",
    }


def test_every_result_state_has_deterministic_next_action():
    """映射全覆盖：新增终态忘补映射会在 KeyError 显式失败。"""
    for state in EvidenceResultState:
        assert state in RESULT_STATE_NEXT_ACTION
        assert RESULT_STATE_NEXT_ACTION[state] in EvidenceNextAction
    assert len(RESULT_STATE_NEXT_ACTION) == len(EvidenceResultState)


def test_bundle_roundtrip_preserves_machine_readable_fields():
    bundle = EvidenceBundle(
        trace_id="req-1",
        query="差旅报销 v1 和 v2 哪个适用？",
        as_of=date(2026, 9, 3),
        result_state=EvidenceResultState.conflicting_evidence,
        route=EvidenceRouteInfo(
            name="structured_fallback",
            reason_codes=["version_constraint"],
            selected_strategy="hybrid",
        ),
        evidence=[EvidenceItem(citation_id="citation-1", document_id="policy.md", policy_key="travel.meal")],
        warnings=["citation_fidelity:missing_marks=2"],
        index_version="idx-1",
        generated_at=datetime.now(UTC),
    )
    payload = bundle.model_dump(mode="json")

    assert payload["schema_version"] == "1.0"
    assert payload["result_state"] == "conflicting_evidence"
    assert payload["route"]["reason_codes"] == ["version_constraint"]
    assert bundle.resolved_next_action() is EvidenceNextAction.human_review

    # 反序列化（agent 侧消费路径）无损
    restored = EvidenceBundle.model_validate(payload)
    assert restored == bundle
    assert restored.resolved_next_action() is EvidenceNextAction.human_review


def test_bundle_does_not_expose_acl_surface():
    """权限侧信道红线：信封里不得出现 ACL 规则/作用域字段。

    负向断言 + 字段集冻结：未来加字段必须显式更新本测试，评审时才能
    看见“新字段是否携带权限信息”。
    """
    bundle = EvidenceBundle(trace_id="req-2", query="q", result_state=EvidenceResultState.evidence_found)
    assert set(bundle.model_fields) == {
        "schema_version",
        "trace_id",
        "query",
        "as_of",
        "result_state",
        "route",
        "evidence",
        "conflicts",
        "warnings",
        "index_version",
        "retryable",
        "next_action",
        "generated_at",
    }
    assert EvidenceItem.model_fields.keys() >= {"citation_id", "document_id"}
    assert "acl" not in EvidenceItem.model_fields
    assert "scope" not in EvidenceItem.model_fields


def test_minimal_bundle_serializes_without_optionals():
    payload = EvidenceBundle(trace_id="r", query="q", result_state=EvidenceResultState.out_of_scope).model_dump(mode="json")
    assert payload["evidence"] == []
    assert payload["conflicts"] == []
    assert payload["next_action"] is None
    assert EvidenceBundle.model_validate(payload).resolved_next_action() is EvidenceNextAction.stop


def test_next_action_roundtrip_requires_known_value():
    with pytest.raises(ValueError):
        EvidenceBundle.model_validate(
            {"trace_id": "r", "query": "q", "result_state": "evidence_found", "next_action": "do_everything"}
        )
